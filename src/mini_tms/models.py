"""Exact statement money and normalized reconciliation tables."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping

import pandas as pd

LEDGER_COLUMNS = ["entity", "bank", "account", "currency", "value_date",
                  "booking_date", "amount", "type_code", "reference", "balance",
                  "statement_id", "description"]
QUALITY_COLUMNS = ["format", "statement_id", "account", "date", "code", "detail"]
STATEMENT_COLUMNS = ["entity", "bank", "account", "currency", "date", "opening",
                     "closing", "movements", "statement_id", "valid"]


def cents(value: str | float | Decimal) -> int:
    """Convert money to integer minor units without silently rounding."""
    try:
        number = Decimal(str(value))
        scaled = number * 100
        if not number.is_finite() or scaled != scaled.to_integral_value():
            raise ValueError("Money must be finite with at most two decimal places")
        return int(scaled)
    except InvalidOperation as exc:
        raise ValueError("Invalid money") from exc


def money(value: int) -> str:
    """Format integer cents using a decimal point."""
    return f"{Decimal(value) / 100:.2f}"


@dataclass(frozen=True)
class Account:
    entity: str
    bank: str
    account: str
    currency: str
    role: str = "local"
    parent: str | None = None
    target: float = 0.0


@dataclass(frozen=True)
class Transaction:
    value_date: date
    booking_date: date
    amount_cents: int
    type_code: str
    reference: str
    description: str = ""


@dataclass(frozen=True)
class Statement:
    statement_id: str
    account: Account
    day: date
    opening_cents: int
    closing_cents: int
    transactions: tuple[Transaction, ...] = ()


@dataclass
class ParseResult:
    """Raw ledger, quarantined ledger, balances and explicit quality findings."""
    ledger: pd.DataFrame
    clean_ledger: pd.DataFrame
    statements: pd.DataFrame
    exceptions: pd.DataFrame


def normalize(statements: Iterable[Statement], format_name: str,
              findings: list[dict] | None = None,
              expected_dates: Iterable[date] | None = None,
              expected_accounts: Mapping[str, Account] | None = None) -> ParseResult:
    """Reconcile statements and quarantine any statement with a finding."""
    items = list(statements)
    issues = list(findings or [])
    rows: list[dict] = []
    balances: list[dict] = []
    seen_refs: set[tuple] = set()
    seen_statements: dict[tuple, Statement] = {}
    previous: dict[str, Statement] = {}

    def issue(s: Statement, code: str, detail: str) -> None:
        issues.append(dict(format=format_name, statement_id=s.statement_id,
                           account=s.account.account, date=s.day.isoformat(),
                           code=code, detail=detail))

    for s in sorted(items, key=lambda x: (x.account.account, x.day)):
        a = s.account
        total = sum(t.amount_cents for t in s.transactions)
        if s.opening_cents + total != s.closing_cents:
            issue(s, "OUT_OF_BALANCE", f"Residual {money(s.closing_cents - s.opening_cents - total)} {a.currency}")
        key = (a.account, s.day)
        if key in seen_statements:
            issue(s, "DUPLICATE_STATEMENT", "Repeated account and statement day")
            first = seen_statements[key]
            if first.statement_id != s.statement_id:
                issue(first, "DUPLICATE_STATEMENT", "Repeated account and statement day")
        else:
            seen_statements[key] = s
        p = previous.get(a.account)
        if p and p.day + timedelta(days=1) == s.day and p.closing_cents != s.opening_cents:
            issue(s, "BALANCE_DISCONTINUITY", "Prior closing differs from opening")
        previous[a.account] = s
        running = s.opening_cents
        for t in s.transactions:
            if not t.reference or t.reference == "NONREF":
                issue(s, "MISSING_REFERENCE", "Transaction has no reliable reference")
            ref_key = (a.account, t.reference)
            if ref_key in seen_refs:
                issue(s, "DUPLICATE_TRANSACTION", f"Repeated reference {t.reference}")
            seen_refs.add(ref_key)
            running += t.amount_cents
            rows.append(dict(entity=a.entity, bank=a.bank, account=a.account,
                             currency=a.currency, value_date=t.value_date.isoformat(),
                             booking_date=t.booking_date.isoformat(), amount=t.amount_cents / 100,
                             type_code=t.type_code, reference=t.reference, balance=running / 100,
                             statement_id=s.statement_id, description=t.description))
        balances.append(dict(entity=a.entity, bank=a.bank, account=a.account,
                             currency=a.currency, date=s.day.isoformat(),
                             opening=s.opening_cents / 100, closing=s.closing_cents / 100,
                             movements=total / 100, statement_id=s.statement_id, valid=True))
    if expected_dates is not None:
        expected_dates = tuple(expected_dates)
        accounts = expected_accounts or {s.account.account: s.account for s in items}
        for account in accounts:
            for day in expected_dates:
                if (account, day) not in seen_statements:
                    issues.append(dict(format=format_name, statement_id="", account=account,
                                       date=day.isoformat(), code="MISSING_DAY",
                                       detail="Expected calendar-day statement absent"))
    quality = pd.DataFrame(issues, columns=QUALITY_COLUMNS)
    bad = set(zip(quality.account, quality.statement_id))
    ledger = pd.DataFrame(rows, columns=LEDGER_COLUMNS)
    statement_table = pd.DataFrame(balances, columns=STATEMENT_COLUMNS)
    if not statement_table.empty:
        statement_table["valid"] = [
            (r.account, r.statement_id) not in bad for r in statement_table.itertuples()]
    mask = [(r.account, r.statement_id) not in bad for r in ledger.itertuples()]
    clean = ledger.loc[mask].reset_index(drop=True) if len(ledger) else ledger.copy()
    return ParseResult(ledger, clean, statement_table, quality)
