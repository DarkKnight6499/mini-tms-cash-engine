"""Run the offline corporate treasury extensions and write reproducible outputs."""

from dataclasses import asdict
from datetime import date, timedelta
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "excel"))

import pandas as pd

from build_workbook import build_workbook
from reconcile_workbook import reconcile
from mini_tms import config as cfg
from mini_tms.bank_fees import review_fees, synthetic_statements
from mini_tms.buyback import (authorization_tracker, check_safe_harbor, eps_accretion,
                              execution_plan, synthetic_blotter)
from mini_tms.config import C
from mini_tms.forecast import convert_flows, direct_flows, treasury_flows, weekly_balances
from mini_tms.models import normalize
from mini_tms.netting import run_netting, settlement_batches, synthetic_invoices
from mini_tms.payments import PaymentWorkflow, validate_pain001, verify_audit
from mini_tms.pooling import compare_pooling
from mini_tms.position import STYLIZED_RATES, daily_position
from mini_tms.rates import FXTable
from mini_tms.synthetic import generate
from mini_tms.working_capital import (chart_ccc, collections_priority, discount_decisions,
                                      working_capital_table)

# CONFIG: all financial scenario inputs are illustrative and centralized in config.py.
OUTPUT_DIRECTORY = cfg.OUTPUT


def main():
    output = OUTPUT_DIRECTORY
    output.mkdir(parents=True, exist_ok=True)
    data = generate()
    fx = FXTable(data.fx)
    cycle_day = (data.forecast_start + timedelta(days=cfg.CYCLE_DAYS - 1)).isoformat()
    netting = run_netting(synthetic_invoices(data.forecast_start), fx, cycle_day)
    workflow = PaymentWorkflow([cfg.MAKER], cfg.CHECKERS, cfg.SECOND_APPROVAL_CENTS,
                               clock=lambda: cfg.EXPORT_TIMESTAMP)
    approved_count = 0
    for batch in settlement_batches(netting, data.accounts):
        selected = netting.instructions[C.PAYMENT_ID] == batch.batch_id
        netting.instructions.loc[selected, C.DEBTOR_ACCOUNT] = batch.debtor_account
        netting.instructions.loc[selected, C.CREDITOR_ACCOUNT] = batch.payments[0].creditor_account
        workflow.create(batch)
        for checker in cfg.CHECKERS:
            if workflow.status(batch.batch_id) != "APPROVED":
                workflow.approve(batch.batch_id, checker)
        xml = workflow.export(batch.batch_id, cfg.MAKER, cfg.EXPORT_TIMESTAMP)
        approved_count += validate_pain001(xml, batch)["transaction_count"]
        (output / f"{batch.batch_id}.xml").write_text(xml, encoding="utf-8")
    pool = compare_pooling(data, fx)
    lines, balances, contracts = synthetic_statements()
    fees, exceptions, cross_bank, checked = review_fees(lines, balances, contracts)
    source_path = cfg.SNAPSHOT if cfg.SNAPSHOT.exists() else cfg.WC_FIXTURE
    source = pd.read_csv(source_path)
    wc = working_capital_table(source)
    chart_ccc(wc, output / "ccc.png")
    aging, priority = collections_priority(data.ar, data.forecast_start, fx)
    discounts = discount_decisions()
    blotter, history = synthetic_blotter(data.forecast_start)
    violations = check_safe_harbor(blotter, history)
    plan_days = sorted(date.fromisoformat(day) for day in blotter[C.DATE].unique())
    plan = execution_plan(plan_days, history)
    authorization = authorization_tracker(blotter)
    eps = eps_accretion(cfg.BUYBACK_NET_INCOME, cfg.BUYBACK_SHARES, float(plan[C.SPEND].sum()), cfg.BUYBACK_PRICE)
    extra = treasury_flows(netting.instructions, plan, data.forecast_start)
    baseline = direct_flows(data.ar, data.ap, data.scheduled, data.forecast_start)
    flows = pd.concat([baseline, extra], ignore_index=True)
    converted = convert_flows(flows, fx, data.forecast_start.isoformat())
    valuation = data.forecast_start - timedelta(days=1)
    position = daily_position(normalize(data.statements, "source").statements, data.accounts, [valuation], fx)
    opening = position.groupby(C.ENTITY)[C.REPORTING_BALANCE].sum().to_dict()
    forecast = weekly_balances(converted, opening, cfg.BUFFERS)
    tables = {"netting_invoices": netting.invoices, "netting_bilateral_net": netting.bilateral_net,
              "netting_positions": netting.positions, "netting_settlements": netting.instructions,
              "netting_fx_trades": netting.fx_trades, "pooling_daily": pool.balances,
              "pooling_transfers": pool.transfers, "pooling_loans": pool.loan_ledger,
              "pooling_monthly_interest": pool.monthly_interest, "pooling_comparison": pool.comparison,
              "bank_fee_lines": checked, "bank_fee_contracts": contracts, "bank_fee_summary": fees,
              "bank_fee_exceptions": exceptions, "bank_fee_comparison": cross_bank,
              "working_capital": wc, "ar_aging": aging, "collections_priority": priority,
              "buyback_blotter": blotter, "buyback_history": history, "buyback_violations": violations,
              "buyback_plan": plan, "treasury_forecast_flows": converted, "treasury_forecast": forecast}
    netting.bilateral_payables.to_csv(output / "netting_bilateral_payables.csv")
    for name, table in tables.items():
        table.to_csv(output / f"{name}.csv", index=False)
    for name, values in {"discounts": discounts, "authorization": authorization, "eps": eps,
                         "netting_metrics": netting.metrics,
                         "netting_audit": [asdict(e) for e in workflow.audit.events]}.items():
        (output / f"{name}.json").write_text(json.dumps(values, indent=2), encoding="utf-8")
    assumptions = {"label": "Illustrative financial scenarios except cited regulatory constants",
                   "seeds": [cfg.NETTING_SEED, cfg.FEE_SEED, cfg.BUYBACK_SEED],
                   "netting_currency": cfg.NETTING_CURRENCY, "cycle_settlement_date": cycle_day,
                   "center_owner_entity": cfg.CENTER_ENTITY, "illustrative_clearing_accounts": cfg.CENTER_ACCOUNTS,
                   "wire_fee": cfg.WIRE_FEE, "fx_spread_bps": cfg.FX_SPREAD_BPS,
                   "pool_rate": cfg.POOL_RATE, "arm_length_spread": cfg.LOAN_SPREAD,
                   "bank_interest_rates": STYLIZED_RATES,
                   "ecr": cfg.ECR, "carry_excess_eca": cfg.CARRY_ECA, "money_fund_yield": cfg.MONEY_FUND_YIELD,
                   "borrowing_rate": cfg.BORROWING_RATE, "cash_yield": cfg.CASH_YIELD, "tax_rate": cfg.TAX_RATE,
                   "regulatory_reserve_ratio": cfg.RESERVE_RATIO, "regulatory_volume_fraction": cfg.VOLUME_FRACTION,
                   "working_capital_source": str(source_path.relative_to(ROOT))}
    (output / "assumptions.json").write_text(json.dumps(assumptions, indent=2), encoding="utf-8")
    build_workbook()
    reconciliation = reconcile()
    (output / "workbook_reconciliation.json").write_text(json.dumps(reconciliation, indent=2), encoding="utf-8")
    interest = pool.comparison.set_index(C.STRUCTURE)[C.GROUP_INTEREST].to_dict()
    results = {**netting.metrics, "approved_settlement_payments": approved_count,
               "netting_audit_valid": verify_audit(workflow.audit.events, workflow.audit.head, len(workflow.audit.events)),
               "pooling_days": pool.balances[C.DATE].nunique(), "physical_interest_usd": interest["physical"],
               "notional_interest_usd": interest["notional"], "standalone_interest_usd": interest["standalone"],
               "interest_allocation_residual_usd": float(pool.monthly_interest[C.INTEREST_USD].sum() - interest["physical"]),
               "bank_statement_count": len(fees), "bank_fee_exception_count": len(exceptions),
               "bank_fees_usd": float(fees[C.FEES].sum()), "bank_net_charges_usd": float(fees[C.NET_CHARGE].sum()),
               "working_capital_rows": len(wc), "working_capital_source": str(source_path.relative_to(ROOT)),
               "working_capital_mean_ccc_days": float(wc[C.CCC].mean()), "discount_apr": discounts[C.APR],
               "buyback_violation_count": len(violations), "planned_buyback_spend_usd": float(plan[C.SPEND].sum()),
               "buyback_base_eps": eps["base_eps"], "buyback_pro_forma_eps": eps["pro_forma_eps"],
               "final_forecast_cash_usd": float(forecast.loc[forecast[C.WEEK] == cfg.HORIZON - 1, C.CLOSING].sum()),
               "workbook_reconciliation": reconciliation["status"]}
    (output / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))
    return results


if __name__ == "__main__":
    main()
