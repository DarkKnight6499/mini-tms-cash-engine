"""Run the complete synthetic workflow and write portable reports locally."""

from dataclasses import asdict
from datetime import timedelta
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd

from mini_tms.forecast import (COLLECTION_POLICY, convert_flows, direct_flows, synthetic_actuals,
                               variance_report, weekly_balances)
from mini_tms.fx import HEDGE_POLICY, STYLIZED_FORWARD_RATES, hedge_recommendations
from mini_tms.parsers import parse_bai2, parse_camt053, parse_mt940
from mini_tms.payments import Batch, Payment, PaymentWorkflow, validate_pain001, verify_audit
from mini_tms.position import STYLIZED_RATES, aggregate_position, daily_position, notional_pool, sweep
from mini_tms.rates import FXTable
from mini_tms.reporting import dashboard, markdown_table
from mini_tms.synthetic import BASE_CURRENCIES, DAYS, HORIZON, SEED, START, generate, write_statements


def main() -> None:
    """Produce a reproducible end-to-end portfolio demonstration."""
    output = ROOT / "examples" / "output"
    output.mkdir(parents=True, exist_ok=True)
    data = generate()
    paths = write_statements(data, output / "statements")
    days = [START + timedelta(days=n) for n in range(DAYS)]
    parsers = {"camt": parse_camt053, "bai2": parse_bai2, "mt940": parse_mt940}
    clean, defective, reconciliation = {}, {}, []
    for name, parser in parsers.items():
        clean[name] = parser(paths[f"clean_{name}"].read_text(encoding="utf-8"), data.accounts, days)
        defective[name] = parser(paths[f"defective_{name}"].read_text(encoding="utf-8"), data.accounts, days)
        for currency, group in clean[name].ledger.groupby("currency"):
            expected_cents = sum(t.amount_cents for s in data.statements
                                 if s.account.currency == currency for t in s.transactions)
            parsed_cents = sum(round(amount * 100) for amount in group.amount)
            reconciliation.append(dict(format=name, currency=currency, transactions=len(group),
                                       total_movement=parsed_cents / 100, expected_movement=expected_cents / 100,
                                       difference_cents=parsed_cents - expected_cents,
                                       max_statement_residual_cents=max(
                                           abs(round((s.closing - s.opening - s.movements) * 100))
                                           for s in clean[name].statements.itertuples()),
                                       exceptions=len(clean[name].exceptions)))
    source = defective["camt"]
    fx = FXTable(data.fx)
    position = daily_position(source.statements, data.accounts, days, fx)
    latest = position[position.date == days[-1].isoformat()].copy()
    swept, transfers = sweep(latest, data.accounts)
    pooling = notional_pool(latest, fx, days=HORIZON * 7)
    flows = direct_flows(data.ar, data.ap, data.scheduled, data.forecast_start)
    valuation_day = days[-1].isoformat()
    converted = convert_flows(flows, fx, valuation_day)
    opening = latest.groupby("entity").balance_reporting.sum().to_dict()
    buffers = {"E1": 200000.0, "E2": 50000.0, "E3": 40000.0, "E4": 30000.0}
    forecast = weekly_balances(converted, opening, buffers)
    forecast["week_start"] = [(data.forecast_start + timedelta(days=int(w) * 7)).isoformat() for w in forecast.week]
    forecast["week_end"] = [(data.forecast_start + timedelta(days=int(w) * 7 + 6)).isoformat() for w in forecast.week]
    actuals = synthetic_actuals(converted)
    variance, metrics, explanations = variance_report(converted, actuals)
    actual_flows = actuals.rename(columns={"actual": "amount_reporting"})
    actual_balances = weekly_balances(actual_flows, opening, buffers)
    balance_variance = forecast[["entity", "week", "closing"]].merge(
        actual_balances[["entity", "week", "closing"]], on=["entity", "week"], suffixes=("_forecast", "_actual"))
    balance_variance["variance"] = balance_variance.closing_actual - balance_variance.closing_forecast
    hedges = hedge_recommendations(flows, BASE_CURRENCIES, fx, valuation_day)
    workflow = PaymentWorkflow(["maker"], ["checker_a", "checker_b"],
                               clock=lambda: "2025-04-01T09:00:00Z")
    batch = Batch("SYNTHETIC-BATCH", "maker", "Synthetic Entity E1", "A100", data.forecast_start,
                  (Payment("P001", "Synthetic Vendor A", "SYNTH001", "USD", 2500000),
                   Payment("P002", "Synthetic Vendor B", "SYNTH002", "USD", 3000000),
                   Payment("P003", "Synthetic Vendor C", "SYNTH003", "USD", 2000000)))
    workflow.create(batch)
    workflow.approve(batch.batch_id, "checker_a")
    workflow.approve(batch.batch_id, "checker_b")
    xml = workflow.export(batch.batch_id, "maker", "2025-04-01T09:00:00Z")
    payment_controls = validate_pain001(xml, batch)
    (output / "synthetic_pain001.xml").write_text(xml, encoding="utf-8")
    (output / "audit.json").write_text(json.dumps([asdict(e) for e in workflow.audit.events], indent=2), encoding="utf-8")
    tables = {"accounts": pd.DataFrame([asdict(a) for a in data.accounts.values()]),
              "fx_quotes": data.fx, "ar_ladder": data.ar, "ap_ladder": data.ap,
              "scheduled_flows": data.scheduled, "ledger_raw": source.ledger,
              "ledger_quarantined_clean": source.clean_ledger, "statement_balances": source.statements,
              "exceptions": pd.concat([r.exceptions for r in defective.values()], ignore_index=True),
              "format_reconciliation": pd.DataFrame(reconciliation), "position_daily": position,
              "position_latest": latest, "position_after_sweep": swept, "sweep_transfers": transfers,
              "pooling": pd.DataFrame([pooling]), "forecast_flows": converted,
              "forecast_weekly": forecast, "actuals_synthetic": actuals,
              "actual_balances_synthetic": actual_balances, "balance_variance": balance_variance,
              "variance_detail": variance, "variance_metrics": metrics,
              "variance_explanations": explanations, "fx_hedges": hedges}
    for dimension in ("account", "entity", "bank", "currency"):
        tables[f"position_by_{dimension}"] = aggregate_position(position, dimension)
    for name, frame in tables.items():
        frame.to_csv(output / f"{name}.csv", index=False, float_format="%.6f")
    final_week = forecast[forecast.week == HORIZON - 1]
    liquidity = forecast.groupby("entity", as_index=False).agg(
        minimum_closing_usd=("closing", "min"), buffer_usd=("buffer", "first"),
        minimum_headroom_usd=("headroom", "min"), breach_weeks=("breach", "sum"))
    liquidity = liquidity.merge(final_week[["entity", "closing"]].rename(columns={"closing": "final_closing_usd"}),
                                on="entity", validate="one_to_one")
    liquidity.to_csv(output / "liquidity_summary.csv", index=False, float_format="%.6f")
    assumptions = dict(label="synthetic and stylized, not regulatory parameters", reporting_currency="USD",
                       collection_policy=COLLECTION_POLICY, pooling_rates=STYLIZED_RATES,
                       forward_rates=STYLIZED_FORWARD_RATES, hedge_policy=HEDGE_POLICY, buffers_usd=buffers,
                       second_approval_threshold_cents=5000000, actuals_seed=8675309)
    (output / "assumptions.json").write_text(json.dumps(assumptions, indent=2), encoding="utf-8")
    max_reconciliation = max(row["max_statement_residual_cents"] for row in reconciliation) / 100
    results = dict(seed=SEED, entities=len(BASE_CURRENCIES), currencies=latest.currency.nunique(),
                   banks=latest.bank.nunique(), accounts=len(data.accounts), history_days=DAYS, forecast_weeks=HORIZON,
                   clean_statements=len(clean["camt"].statements), clean_transactions=len(clean["camt"].ledger),
                   clean_exceptions=len(clean["camt"].exceptions), max_reconciliation_residual=max_reconciliation,
                   max_format_difference_cents=max(abs(row["difference_cents"]) for row in reconciliation),
                   defective_statements=len(source.statements), defective_exceptions=len(source.exceptions),
                   quarantined_statements=int((~source.statements.valid).sum()),
                   missing_days=int((source.exceptions.code == "MISSING_DAY").sum()),
                   latest_cash_usd=float(latest.balance_reporting.sum()),
                   sweep_cash_difference_usd=float(swept.balance_reporting.sum() - latest.balance_reporting.sum()),
                   sweep_transfer_count=len(transfers), pooling_benefit_usd=pooling["benefit_usd"],
                   pooling_days=pooling["days"], unpooled_interest_usd=pooling["unpooled_interest_usd"],
                   pooled_interest_usd=pooling["pooled_interest_usd"],
                   compensated_cash_usd=pooling["compensated_usd"],
                   final_forecast_cash_usd=float(final_week.closing.sum()), liquidity_breach_entity_weeks=int(forecast.breach.sum()),
                   forecast_entity_weeks=len(forecast),
                   receipts_mape_pct=float(metrics.loc[metrics.line == "receipts", "mape_pct"].iloc[0]),
                   vendor_mape_pct=float(metrics.loc[metrics.line == "vendors", "mape_pct"].iloc[0]),
                   forecast_total_variance_usd=float(variance.variance.sum()),
                   forecast_mean_bias_usd=float(variance.variance.mean()),
                   absolute_fx_exposure_before_usd=float(hedges.exposure_before_usd.abs().sum()),
                   absolute_fx_exposure_after_usd=float(hedges.exposure_after_usd.abs().sum()),
                   payment_transactions=payment_controls["transaction_count"], payment_control_sum=batch.total_cents / 100,
                   audit_events=len(workflow.audit.events), audit_valid=verify_audit(workflow.audit.events, workflow.audit.head, len(workflow.audit.events)),
                   payment_validation=payment_controls["validation"])
    (output / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    summary = pd.DataFrame([dict(metric=k, value=v) for k, v in results.items()])
    summary.to_csv(output / "summary.csv", index=False)
    report = "# Synthetic treasury workflow report\n\nAll data and policy assumptions are synthetic.\n\n"
    for title, frame in (("Validation results", summary),
                         ("Latest account position", latest[["account", "entity", "currency", "balance_native", "balance_reporting", "stale"]]),
                         ("Interest compensation", tables["pooling"]),
                         ("Final forecast week, USD", final_week[["entity", "closing", "buffer", "breach"]]),
                         ("Liquidity summary, USD", liquidity),
                         ("Variance metrics, USD", metrics),
                         ("Largest variance explanations", explanations[["explanation"]]),
                         ("Exceptions from defective camt feed", source.exceptions)):
        report += f"## {title}\n\n" + markdown_table(frame) + "\n\n"
    (output / "report.md").write_text(report, encoding="utf-8")
    dashboard(output / "dashboard.html", {"Latest cash, USD": f"{results['latest_cash_usd']:,.2f}",
                                          "Forecast final cash, USD": f"{results['final_forecast_cash_usd']:,.2f}",
                                          "Interest benefit, USD": f"{pooling['benefit_usd']:,.2f}",
                                          "Exceptions, camt": len(source.exceptions)},
              latest[["account", "entity", "currency", "balance_native", "balance_reporting", "stale"]],
              forecast[["entity", "week_start", "week_end", "opening", "net_flow", "closing", "buffer", "breach"]], source.exceptions)
    print(json.dumps(results, indent=2))
    from run_corporate_tools import main as run_corporate_tools
    run_corporate_tools()


if __name__ == "__main__":
    main()
