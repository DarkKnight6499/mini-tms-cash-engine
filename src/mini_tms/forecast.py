"""Direct expected cash flows and explicit forecast error conventions."""

from datetime import date
from math import isfinite
from typing import Mapping

import numpy as np
import pandas as pd

from .rates import FXTable
from . import config as cfg
from .config import C

# Collection probabilities and timing distributions are stylized policy assumptions.
COLLECTION_POLICY = {
    "current": (.95, ((0, .4), (1, .4), (2, .2))),
    "overdue_30": (.85, ((0, .2), (1, .4), (3, .4))),
    "overdue_60": (.65, ((1, .2), (3, .4), (5, .4))),
    "overdue_90": (.35, ((2, .2), (5, .4), (8, .4))),
}
LINES = ("receipts", "vendors", "payroll", "tax", "debt_service", "intercompany", "buyback", "netting")
FLOW_COLUMNS = ["entity", "currency", "week", "line", "amount", "reference"]


def treasury_flows(netting_instructions: pd.DataFrame, buyback_plan: pd.DataFrame,
                   start: date, horizon: int = cfg.HORIZON) -> pd.DataFrame:
    """Add participant and center settlement legs plus planned repurchase spending."""
    if type(horizon) is not int or horizon <= 0:
        raise ValueError("Positive integer forecast horizon required")
    rows = []

    def add(entity, currency, day, amount, line, reference):
        week = (date.fromisoformat(day) - start).days // 7
        if not isfinite(amount):
            raise ValueError("Finite treasury cash flow required")
        if 0 <= week < horizon:
            rows.append({C.ENTITY: entity, C.CURRENCY: currency, C.WEEK: week, C.LINE: line,
                         C.AMOUNT: amount, C.REFERENCE: reference})

    for r in netting_instructions.to_dict("records"):
        add(r[C.ENTITY], r[C.CURRENCY], r[C.DATE], r[C.SIGNED], "netting", r[C.PAYMENT_ID])
        add(cfg.CENTER_ENTITY, r[C.CURRENCY], r[C.DATE], -r[C.SIGNED], "netting", r[C.PAYMENT_ID])
    for r in buyback_plan.to_dict("records"):
        add(cfg.BUYBACK_ENTITY, cfg.NETTING_CURRENCY, r[C.DATE], -r[C.SPEND], "buyback", "PLANNED-BUYBACK")
    return pd.DataFrame(rows, columns=FLOW_COLUMNS)


def direct_flows(ar: pd.DataFrame, ap: pd.DataFrame, scheduled: pd.DataFrame,
                 start: date, horizon: int = 13,
                 policy: Mapping | None = None) -> pd.DataFrame:
    """Allocate AR probabilities, AP due dates and dated obligations into weeks."""
    if horizon <= 0:
        raise ValueError("Positive horizon required")
    policy = COLLECTION_POLICY if policy is None else policy
    for probability, timings in policy.values():
        if not isfinite(probability) or not 0 <= probability <= 1 or not timings:
            raise ValueError("Invalid collection probability")
        if any(not isinstance(offset, int) or offset < 0 or not isfinite(weight) or weight < 0
               for offset, weight in timings) or abs(sum(w for _, w in timings) - 1) > 1e-12:
            raise ValueError("Invalid timing distribution")
    rows = []

    def add(entity: str, currency: str, week: int, line: str, amount: float, reference: str) -> None:
        if not isfinite(amount):
            raise ValueError("Nonfinite cash flow")
        if 0 <= week < horizon:
            rows.append(dict(entity=entity, currency=currency, week=week, line=line,
                             amount=amount, reference=reference))

    for row in ar.itertuples():
        if row.amount < 0 or not isfinite(row.amount):
            raise ValueError("AR balances must be finite and nonnegative")
        if row.aging_bucket not in policy:
            raise ValueError("Unknown AR aging bucket")
        probability, timings = policy[row.aging_bucket]
        due_week = max(0, (date.fromisoformat(row.due_date) - start).days // 7)
        for offset, weight in timings:
            add(row.entity, row.currency, due_week + offset, "receipts",
                row.amount * probability * weight, row.invoice)
    for row in ap.itertuples():
        if row.amount < 0 or not isfinite(row.amount):
            raise ValueError("AP balances must be finite and nonnegative")
        week = max(0, (date.fromisoformat(row.due_date) - start).days // 7)
        add(row.entity, row.currency, week, "vendors", -row.amount, row.invoice)
    for row in scheduled.itertuples():
        if row.line not in LINES:
            raise ValueError("Unknown forecast line")
        week = (date.fromisoformat(row.date) - start).days // 7
        add(row.entity, row.currency, week, row.line, row.amount, "SCHEDULED")
    return pd.DataFrame(rows, columns=FLOW_COLUMNS)


def convert_flows(flows: pd.DataFrame, fx: FXTable, valuation_day: str,
                  reporting_currency: str = "USD") -> pd.DataFrame:
    """Freeze spot at forecast inception to separate cash errors from FX errors."""
    out = flows.copy()
    out["amount_reporting"] = [row.amount * fx.rate(valuation_day, row.currency, reporting_currency)
                               for row in flows.itertuples()]
    out["reporting_currency"] = reporting_currency
    return out


def weekly_balances(flows: pd.DataFrame, opening: Mapping[str, float],
                    buffers: Mapping[str, float], horizon: int = 13) -> pd.DataFrame:
    """Reconcile entity opening, net flows and closing, with a liquidity flag."""
    if horizon <= 0 or set(opening) != set(buffers):
        raise ValueError("Positive horizon and a buffer per entity required")
    if not set(flows.entity).issubset(opening):
        raise ValueError("Missing entity opening balance")
    if len(flows) and (not flows.amount_reporting.map(isfinite).all()
                       or not flows.week.map(lambda w: isinstance(w, (int, np.integer)) and 0 <= w < horizon).all()
                       or not flows.line.isin(LINES).all()):
        raise ValueError("Invalid weekly forecast flow")
    grouped = flows.groupby(["entity", "week", "line"]).amount_reporting.sum().to_dict()
    rows = []
    for entity, balance in opening.items():
        if not isfinite(balance) or not isfinite(buffers[entity]) or buffers[entity] < 0:
            raise ValueError("Invalid balance or buffer")
        for week in range(horizon):
            row = dict(entity=entity, week=week, opening=balance)
            row.update({line: float(grouped.get((entity, week, line), 0)) for line in LINES})
            net = sum(row[line] for line in LINES)
            balance += net
            row.update(net_flow=net, closing=balance, buffer=buffers[entity],
                       headroom=balance - buffers[entity], breach=balance < buffers[entity])
            rows.append(row)
    return pd.DataFrame(rows)


def synthetic_actuals(flows: pd.DataFrame, seed: int = 8675309) -> pd.DataFrame:
    """Create synthetic realized flows with receipt delays and vendor overruns."""
    rng = np.random.default_rng(seed)
    out = flows.groupby(["entity", "week", "line"], as_index=False).amount_reporting.sum()
    perturbation = rng.normal(0, .06, len(out))
    # Matched intercompany legs must not create group cash through independent shocks.
    perturbation[out.line.to_numpy() == "intercompany"] = 0
    multiplier = np.where(out.line == "receipts", .90, np.where(out.line == "vendors", 1.08, 1.0))
    out["actual"] = out.amount_reporting * (multiplier + perturbation)
    # A delayed receipt is observed later, preserving its total over the horizon.
    for entity in sorted(out.entity.unique()):
        source = out.index[(out.entity == entity) & (out.line == "receipts") & (out.week == 0)]
        target = out.index[(out.entity == entity) & (out.line == "receipts") & (out.week == 1)]
        if len(source) and len(target):
            delayed = out.loc[source[0], "actual"] * .25
            out.loc[source[0], "actual"] -= delayed
            out.loc[target[0], "actual"] += delayed
    return out[["entity", "week", "line", "actual"]]


def variance_report(flows: pd.DataFrame, actuals: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Use actual minus forecast; MAPE excludes zero actuals and reports coverage."""
    keys = ["entity", "week", "line"]
    if actuals.duplicated(keys).any() or not actuals.actual.map(isfinite).all():
        raise ValueError("Actuals require unique keys and finite amounts")
    forecast = flows.groupby(keys, as_index=False).amount_reporting.sum().rename(columns={"amount_reporting": "forecast"})
    details = forecast.merge(actuals, on=keys, how="outer", validate="one_to_one")
    details["missing_actual"] = details.actual.isna()
    details["forecast"] = details.forecast.fillna(0.0)
    details["variance"] = details.actual - details.forecast
    details["absolute_error"] = details.variance.abs()
    details["ape"] = np.where(details.actual.abs() > 1e-12,
                              details.absolute_error / details.actual.abs().replace(0, np.nan), np.nan)
    metrics = []
    for line, group in details.groupby("line"):
        denominator = group.actual.abs().sum()
        metrics.append(dict(line=line, mape_pct=group.ape.mean() * 100,
                            bias=group.variance.mean(), total_variance=group.variance.sum(min_count=1),
                            wape_pct=group.absolute_error.sum() / denominator * 100 if denominator else np.nan,
                            observations=group.actual.notna().sum(), missing_actuals=group.missing_actual.sum(),
                            mape_observations=group.ape.notna().sum()))
    largest = details.dropna(subset=["absolute_error"]).sort_values("absolute_error", ascending=False).head(5).copy()
    def explain(row: pd.Series) -> str:
        if row.line == "receipts":
            cause = "lower or delayed collections" if row.variance < 0 else "higher or accelerated collections"
        else:
            cause = "larger outflows" if row.variance < 0 else "smaller outflows or higher inflows"
        return (f"{row.entity}, week {row.week + 1}, {row.line}: actual minus forecast "
                f"{row.variance:,.2f}; pattern consistent with {cause}. Cause is a hypothesis, not confirmed.")
    largest["explanation"] = largest.apply(explain, axis=1) if len(largest) else pd.Series(dtype=str)
    return details, pd.DataFrame(metrics), largest
