"""Daily positions, target sweeps and stylized interest compensation."""

from datetime import date
from math import isfinite
from typing import Iterable, Mapping

import pandas as pd

from .models import Account
from .rates import FXTable

# These rates are synthetic assumptions, not market quotes or regulatory parameters.
STYLIZED_RATES = {"USD": (.035, .075), "EUR": (.025, .060), "GBP": (.040, .080)}


def daily_position(statements: pd.DataFrame, accounts: Mapping[str, Account],
                   days: Iterable[date], fx: FXTable,
                   reporting_currency: str = "USD") -> pd.DataFrame:
    """Use the latest valid closing balance and label stale or absent balances."""
    rows = []
    valid = statements[statements.valid].copy()
    if valid.duplicated(["account", "date"]).any():
        raise ValueError("Ambiguous daily account balances")
    for day in days:
        day_string = day.isoformat()
        for a in accounts.values():
            history = valid[(valid.account == a.account) & (valid.date <= day_string)].sort_values("date")
            balance = float(history.iloc[-1].closing) if not history.empty else float("nan")
            source = str(history.iloc[-1].date) if not history.empty else ""
            rate = fx.rate(day_string, a.currency, reporting_currency)
            rows.append(dict(date=day_string, entity=a.entity, bank=a.bank, account=a.account,
                             currency=a.currency, reporting_currency=reporting_currency,
                             role=a.role, fx_rate=rate, balance_native=balance, balance_reporting=balance * rate,
                             target_native=a.target, surplus_native=balance - a.target,
                             target_reporting=a.target * rate, surplus_reporting=(balance - a.target) * rate,
                             source_date=source, stale=source != day_string))
    return pd.DataFrame(rows)


def aggregate_position(position: pd.DataFrame, dimension: str) -> pd.DataFrame:
    """Aggregate within currency, preserving unknown balances as unknown."""
    if dimension not in ("account", "entity", "bank", "currency"):
        raise ValueError("Unknown position dimension")
    keys = list(dict.fromkeys(["date", dimension, "currency", "reporting_currency"]))
    amounts = ["balance_native", "balance_reporting", "target_native", "surplus_native"]
    grouped = position.groupby(keys, as_index=False)
    out = grouped[amounts].agg(lambda s: s.sum() if s.notna().all() else float("nan"))
    flags = grouped.stale.max()
    return out.merge(flags, on=keys, validate="one_to_one")


def sweep(position: pd.DataFrame, accounts: Mapping[str, Account]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Simulate same-currency target transfers, allowing concentration overdrafts."""
    if position.date.nunique() != 1 or position.account.duplicated().any():
        raise ValueError("Sweep requires one balance per account on one day")
    if position.stale.any() or not position.balance_native.map(isfinite).all():
        raise ValueError("Sweep requires fresh, finite balances")
    for row in position.itertuples():
        if row.account not in accounts:
            raise ValueError("Unknown sweep account")
        registered = accounts[row.account]
        if (row.currency, row.entity, row.bank, row.target_native) != (
                registered.currency, registered.entity, registered.bank, registered.target):
            raise ValueError("Sweep position differs from account registry")
        if not isfinite(registered.target) or registered.target < 0:
            raise ValueError("Sweep targets must be finite and nonnegative")
    out = position.copy().set_index("account", drop=False)
    transfers = []
    for a in accounts.values():
        if a.parent is None or a.account not in out.index:
            continue
        if a.parent not in out.index or a.parent not in accounts:
            raise ValueError("Missing concentration account")
        parent = accounts[a.parent]
        if parent.currency != a.currency or parent.role != "concentration" or parent.parent is not None:
            raise ValueError("Sweep parent must be a same-currency concentration account")
        amount = float(out.loc[a.account, "balance_native"]) - a.target
        if amount == 0:
            continue
        out.loc[a.account, "balance_native"] -= amount
        out.loc[a.parent, "balance_native"] += amount
        transfers.append(dict(source=a.account if amount >= 0 else a.parent,
                              destination=a.parent if amount >= 0 else a.account,
                              currency=a.currency, amount=abs(amount),
                              intercompany=a.entity != parent.entity))
    for account in out.index:
        original = position[position.account == account].iloc[0]
        rate = original.fx_rate
        out.loc[account, "balance_reporting"] = out.loc[account, "balance_native"] * rate
        out.loc[account, "surplus_native"] = out.loc[account, "balance_native"] - original.target_native
        out.loc[account, "surplus_reporting"] = out.loc[account, "surplus_native"] * rate
    return out.reset_index(drop=True), pd.DataFrame(transfers, columns=["source", "destination", "currency", "amount", "intercompany"])


def notional_pool(position: pd.DataFrame, fx: FXTable, days: int = 1,
                  rates: Mapping[str, tuple[float, float]] | None = None) -> dict[str, float]:
    """Offset cheapest deposits against dearest borrowing in USD equivalents."""
    if type(days) is not int or days <= 0 or position.date.nunique() != 1 or position.account.duplicated().any():
        raise ValueError("Positive days and a single valuation day required")
    if position.stale.any() or not position.balance_native.map(isfinite).all():
        raise ValueError("Pooling requires fresh, finite balances")
    rates = STYLIZED_RATES if rates is None else rates
    positives, negatives = [], []
    unpooled = 0.0
    for row in position.itertuples():
        if row.currency not in rates:
            raise ValueError("Missing pooling rates")
        credit, debit = rates[row.currency]
        if not all(isfinite(r) and r >= 0 for r in (credit, debit)) or debit < credit:
            raise ValueError("Rates require nonnegative credit and debit at least credit")
        balance = row.balance_native * fx.rate(row.date, row.currency)
        unpooled += balance * (credit if balance >= 0 else debit) * days / 365
        (positives if balance >= 0 else negatives).append([abs(balance), credit if balance >= 0 else debit])
    positives.sort(key=lambda x: x[1])
    negatives.sort(key=lambda x: -x[1])
    benefit = compensated = 0.0
    i = j = 0
    while i < len(positives) and j < len(negatives):
        deposit, borrowing = positives[i], negatives[j]
        if borrowing[1] <= deposit[1]:
            break
        offset = min(deposit[0], borrowing[0])
        compensated += offset
        benefit += offset * (borrowing[1] - deposit[1]) * days / 365
        deposit[0] -= offset
        borrowing[0] -= offset
        if deposit[0] <= 1e-9:
            i += 1
        if borrowing[0] <= 1e-9:
            j += 1
    return dict(days=days, compensated_usd=compensated, unpooled_interest_usd=unpooled,
                pooled_interest_usd=unpooled + benefit, benefit_usd=benefit)
