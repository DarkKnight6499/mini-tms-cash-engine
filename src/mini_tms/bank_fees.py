"""Illustrative account analysis statements, ECA economics and billing exceptions."""

import numpy as np
import pandas as pd

from . import config as cfg
from .config import C


def account_analysis(ledger, float_balance, ecr, reserve, days, fees, fund_yield=cfg.MONEY_FUND_YIELD,
                     carry_in=0.0, carry=cfg.CARRY_ECA):
    """Calculate simple ACT/365 credits and opportunity cost without interest compounding."""
    if (not np.isfinite([ledger, float_balance, ecr, reserve, days, fees, fund_yield, carry_in]).all()
            or min(ledger, float_balance, ecr, fees, fund_yield, carry_in) < 0
            or float_balance > ledger or not 0 <= reserve < 1 or days <= 0):
        raise ValueError("Valid nonnegative statement inputs required")
    collected = ledger - float_balance
    factor = ecr * (1 - reserve) * days / cfg.YEAR_DAYS
    eca = collected * factor
    available = eca + (carry_in if carry else 0.0)
    return {C.COLLECTED: collected, C.ECA: eca, C.NET_CHARGE: max(fees - available, 0.0),
            C.CARRY_IN: carry_in if carry else 0.0, C.CARRY_OUT: max(available - fees, 0.0) if carry else 0.0,
            C.REQUIRED_BALANCE: fees / factor if factor else (float("inf") if fees else 0.0),
            C.DECISION: "Hold balances" if ecr * (1 - reserve) >= fund_yield else "Pay fees and invest",
            C.FUND_YIELD: fund_yield}


def synthetic_statements(seed=cfg.FEE_SEED):
    """Return service lines, monthly balances and bank-specific contracted prices."""
    rng = np.random.default_rng(seed)
    lines, balances, contract = [], [], []
    months = pd.date_range(cfg.FEE_START, periods=cfg.FEE_MONTHS, freq="MS")
    for bank, multiplier in cfg.BANK_MULTIPLIERS.items():
        for service, (base_volume, price) in cfg.SERVICES.items():
            contract.append({C.BANK: bank, C.SERVICE: service, C.CONTRACT_PRICE: price * multiplier})
        for month_number, month in enumerate(months):
            stamp = month.strftime("%Y-%m")
            balances.append({C.BANK: bank, C.MONTH: stamp,
                             C.LEDGER: float(rng.integers(*cfg.FEE_LEDGER_RANGE)),
                             C.FLOAT: float(rng.integers(*cfg.FEE_FLOAT_RANGE)), C.ECR: cfg.ECR,
                             C.RESERVE: cfg.RESERVE_RATIO, C.DAYS: month.days_in_month})
            for service, (base_volume, price) in cfg.SERVICES.items():
                volume = max(1, round(base_volume * (1 + rng.normal(0, cfg.FEE_VOLUME_NOISE))))
                unit = price * multiplier
                activity = volume
                if bank == "BANKA" and month_number == cfg.FEE_MONTHS - 1:
                    if service == "outgoing_wire":
                        unit *= 1 + cfg.FEE_PRICE_UPLIFT
                    if service == "ach_originated":
                        volume *= cfg.FEE_SPIKE_MULTIPLE
                        activity = volume
                    if service == "positive_pay_item":
                        activity, volume = 0, cfg.FEE_ZERO_ACTIVITY_VOLUME
                row = {C.LINE_ID: f"{bank}-{stamp}-{service}", C.BANK: bank, C.MONTH: stamp,
                       C.SERVICE: service, C.VOLUME: volume, C.ACTIVITY: activity, C.UNIT_PRICE: unit}
                lines.append(row)
                if bank == "BANKB" and month_number == cfg.FEE_MONTHS - 1 and service == "incoming_wire":
                    lines.append({**row, C.LINE_ID: row[C.LINE_ID] + "-DUP"})
    return pd.DataFrame(lines), pd.DataFrame(balances), pd.DataFrame(contract)


def review_fees(lines, balances, contract, tolerance=cfg.PRICE_TOLERANCE,
                 carry=cfg.CARRY_ECA, fund_yield=cfg.MONEY_FUND_YIELD):
    """Keep suspicious charges in billed totals and flag them for review."""
    keys = [C.BANK, C.MONTH]
    if tolerance < 0 or not np.isfinite(tolerance) or contract.duplicated([C.BANK, C.SERVICE]).any():
        raise ValueError("Nonnegative tolerance and unique contracts required")
    if balances.duplicated(keys).any() or lines[C.LINE_ID].duplicated().any():
        raise ValueError("Unique statements and line identifiers required")
    checked = lines.merge(contract, on=[C.BANK, C.SERVICE], how="left", validate="many_to_one")
    numeric = [C.VOLUME, C.ACTIVITY, C.UNIT_PRICE, C.CONTRACT_PRICE]
    if checked[numeric].isna().any().any() or not np.isfinite(checked[numeric]).all().all() or (checked[numeric] < 0).any().any():
        raise ValueError("Finite nonnegative volumes and contracted prices required")
    checked[C.FEES] = checked[C.VOLUME] * checked[C.UNIT_PRICE]
    history = checked.groupby([C.BANK, C.SERVICE, C.MONTH], as_index=False)[C.VOLUME].sum()
    z_scores = {}
    for (bank, service), group in history.groupby([C.BANK, C.SERVICE]):
        group = group.sort_values(C.MONTH)
        series = group.set_index(C.MONTH)[C.VOLUME]
        for month, volume in series.items():
            earlier = series.loc[series.index < month].tail(cfg.TRAILING_MONTHS)
            if len(earlier) == cfg.TRAILING_MONTHS:
                deviation = earlier.std(ddof=0)
                z = (volume - earlier.mean()) / deviation if deviation else (float("inf") if volume > earlier.mean() else 0.0)
                z_scores[(bank, service, month)] = z
    duplicated = checked.duplicated([C.BANK, C.MONTH, C.SERVICE], keep=False)
    exceptions = []
    for index, r in checked.iterrows():
        z = z_scores.get((r[C.BANK], r[C.SERVICE], r[C.MONTH]), float("nan"))
        codes = []
        if r[C.UNIT_PRICE] > r[C.CONTRACT_PRICE] * (1 + tolerance) + 1e-12:
            codes.append("PRICE_ABOVE_CONTRACT")
        if z > cfg.VOLUME_Z_LIMIT:
            codes.append("VOLUME_SPIKE")
        if duplicated.loc[index]:
            codes.append("DUPLICATE_SERVICE")
        if r[C.ACTIVITY] == 0 and r[C.FEES] > 0:
            codes.append("ZERO_ACTIVITY")
        for code in codes:
            exceptions.append({C.LINE_ID: r[C.LINE_ID], C.BANK: r[C.BANK], C.MONTH: r[C.MONTH],
                               C.SERVICE: r[C.SERVICE], C.CODE: code, C.Z_SCORE: z})
    totals = checked.groupby(keys, as_index=False)[C.FEES].sum()
    summary = balances.merge(totals, on=keys, how="outer", validate="one_to_one")
    if summary.isna().any().any():
        raise ValueError("Every statement requires balances and service lines")
    rows, carry_balances = [], {}
    for r in summary.sort_values(keys).to_dict("records"):
        values = account_analysis(r[C.LEDGER], r[C.FLOAT], r[C.ECR], r[C.RESERVE], r[C.DAYS], r[C.FEES],
                                  fund_yield, carry_balances.get(r[C.BANK], 0.0), carry)
        rows.append({**r, **values})
        carry_balances[r[C.BANK]] = values[C.CARRY_OUT]
    comparison = checked.groupby([C.BANK, C.SERVICE], as_index=False)[[C.FEES, C.VOLUME]].sum()
    comparison[C.UNIT_PRICE] = comparison[C.FEES] / comparison[C.VOLUME].replace(0, np.nan)
    return (pd.DataFrame(rows), pd.DataFrame(exceptions, columns=[C.LINE_ID, C.BANK, C.MONTH, C.SERVICE, C.CODE, C.Z_SCORE]),
            comparison, checked)
