"""Daily physical concentration, intercompany accruals and notional comparison."""

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from . import config as cfg
from .config import C
from .position import STYLIZED_RATES, notional_pool, sweep
from .rates import FXTable


@dataclass
class PoolingResult:
    balances: pd.DataFrame
    transfers: pd.DataFrame
    loan_ledger: pd.DataFrame
    monthly_interest: pd.DataFrame
    comparison: pd.DataFrame


def concentrate(position, accounts, mode=cfg.POOL_MODE, target=cfg.POOL_TARGET, band=cfg.POOL_BAND):
    """Apply zero, target or threshold-to-target sweeps through existing controls."""
    if mode not in cfg.POOL_MODES or not np.isfinite([target, *band]).all() or not band[0] <= target <= band[1]:
        raise ValueError("Valid sweep mode and band containing target required")
    registry = {}
    adjusted = position.copy()
    native = adjusted.set_index(C.ACCOUNT)[C.BALANCE].to_dict()
    for key, account in accounts.items():
        active = account.parent is not None
        if active and mode == "threshold":
            active = native[key] < band[0] or native[key] > band[1]
        new_target = 0.0 if mode == "zero" else target
        registry[key] = replace(account, target=new_target if account.parent else account.target,
                                parent=account.parent if active else None)
        adjusted.loc[adjusted[C.ACCOUNT] == key, C.TARGET] = registry[key].target
    adjusted[C.TARGET_REPORTING] = adjusted[C.TARGET] * adjusted[C.FX_RATE]
    return sweep(adjusted, registry)


def _position(day, native, accounts, fx):
    rows = []
    for key, balance in native.items():
        a = accounts[key]
        rate = fx.rate(day, a.currency)
        rows.append({C.DATE: day, C.ENTITY: a.entity, C.BANK: a.bank, C.ACCOUNT: key,
                     C.CURRENCY: a.currency, C.REPORTING_CURRENCY: cfg.NETTING_CURRENCY,
                     C.ROLE: a.role, C.FX_RATE: rate, C.BALANCE: balance,
                     C.REPORTING_BALANCE: balance * rate, C.TARGET: a.target,
                     C.TARGET_REPORTING: a.target * rate, C.SURPLUS: balance - a.target,
                     C.SURPLUS_REPORTING: (balance - a.target) * rate, C.SOURCE_DATE: day, C.STALE: False})
    return pd.DataFrame(rows)


def compare_pooling(data, fx: FXTable, mode=cfg.POOL_MODE, pool_rate=cfg.POOL_RATE,
                    spread=cfg.LOAN_SPREAD) -> PoolingResult:
    """Replay the same operating movements for physical and unswept notional cases."""
    if not np.isfinite([pool_rate, spread]).all() or not 0 <= spread <= pool_rate:
        raise ValueError("Finite pool rate at least nonnegative spread required")
    accounts = dict(data.accounts)
    headers = {a.currency: a.account for a in accounts.values() if a.role == "concentration"}
    for key, a in list(accounts.items()):
        if a.parent is None and a.role != "concentration":
            accounts[key] = replace(a, parent=headers[a.currency])
    by_day = {}
    for statement in data.statements:
        by_day.setdefault(statement.day.isoformat(), []).append(statement)
    native, principal = {}, {key: 0.0 for key, a in accounts.items() if a.parent and a.entity != accounts[a.parent].entity}
    history, transfers, loans, allocation = [], [], [], []
    physical_interest = notional_interest = standalone_interest = overdraft_physical = overdraft_notional = 0.0
    advances = 0
    for day, statements in sorted(by_day.items()):
        baseline = {s.account.account: s.closing_cents / 100 for s in statements}
        for s in statements:
            key = s.account.account
            native[key] = native.get(key, s.opening_cents / 100) + (s.closing_cents - s.opening_cents) / 100
        before = _position(day, native, accounts, fx)
        after, moved = concentrate(before, accounts, mode)
        native = after.set_index(C.ACCOUNT)[C.BALANCE].to_dict()
        group_interest = 0.0
        for r in after.to_dict("records"):
            credit, debit = STYLIZED_RATES[r[C.CURRENCY]]
            group_interest += r[C.BALANCE] * (credit if r[C.BALANCE] >= 0 else debit) / cfg.YEAR_DAYS * r[C.FX_RATE]
        physical_interest += group_interest
        unpooled = _position(day, baseline, accounts, fx)
        notional = notional_pool(unpooled, fx)
        standalone_interest += notional["unpooled_interest_usd"]
        notional_interest += notional["pooled_interest_usd"]
        before_overdraft = float(-unpooled.loc[unpooled[C.REPORTING_BALANCE] < 0, C.REPORTING_BALANCE].sum())
        after_overdraft = float(-after.loc[after[C.REPORTING_BALANCE] < 0, C.REPORTING_BALANCE].sum())
        overdraft_physical += before_overdraft - after_overdraft
        overdraft_notional += notional["compensated_usd"]
        daily_allocated = 0.0
        for r in moved.to_dict("records"):
            r[C.DATE] = day
            transfers.append(r)
            child = r[C.SOURCE] if accounts[r[C.SOURCE]].parent else r[C.DESTINATION]
            if child in principal:
                principal[child] += r[C.AMOUNT] * (1 if r[C.SOURCE] == child else -1)
                advances += 1
        for key, balance in principal.items():
            a = accounts[key]
            rate = pool_rate - spread if balance >= 0 else pool_rate + spread
            interest = balance * rate / cfg.YEAR_DAYS
            usd_interest = interest * fx.rate(day, a.currency)
            row = {C.DATE: day, C.MONTH: day[:7], C.ENTITY: a.entity, C.ACCOUNT: key,
                   C.DESTINATION: a.parent, C.CURRENCY: a.currency, C.PRINCIPAL: balance,
                   C.LOAN_RATE: rate, C.INTEREST: interest, C.INTEREST_USD: usd_interest}
            loans.append(row)
            allocation.append({C.MONTH: day[:7], C.ENTITY: a.entity, C.INTEREST_USD: usd_interest})
            daily_allocated += usd_interest
        # Header retains external bank interest after participant compensation.
        allocation.append({C.MONTH: day[:7], C.ENTITY: cfg.CENTER_ENTITY,
                           C.INTEREST_USD: group_interest - daily_allocated})
        history.append(after)
    comparison = pd.DataFrame([
        {C.STRUCTURE: "physical", C.GROUP_INTEREST: physical_interest, C.OVERDRAFT: overdraft_physical,
         C.LOANS: len(principal), C.ADVANCES: advances,
         C.NOTES: "Daily native-currency transfers; participant loans and header residual interest allocation."},
        {C.STRUCTURE: "notional", C.GROUP_INTEREST: notional_interest, C.OVERDRAFT: overdraft_notional,
         C.LOANS: 0, C.ADVANCES: 0,
         C.NOTES: "Existing daily notional compensation; no transfers; cross-currency set-off assumed illustrative."},
        {C.STRUCTURE: "standalone", C.GROUP_INTEREST: standalone_interest, C.OVERDRAFT: 0.0,
         C.LOANS: 0, C.ADVANCES: 0, C.NOTES: "Same unswept daily operating balances without compensation."}])
    monthly = pd.DataFrame(allocation).groupby([C.MONTH, C.ENTITY], as_index=False)[C.INTEREST_USD].sum()
    return PoolingResult(pd.concat(history, ignore_index=True), pd.DataFrame(transfers),
                         pd.DataFrame(loans), monthly, comparison)
