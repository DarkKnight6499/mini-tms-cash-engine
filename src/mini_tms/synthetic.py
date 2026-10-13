"""Seeded synthetic data, deliberately separate from market or bank data."""

from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .models import Account, Statement, Transaction
from .statements import to_bai2, to_camt053, to_mt940

SEED = 20250317
START = date(2025, 1, 1)
DAYS = 90
HORIZON = 13
BASE_CURRENCIES = {"E1": "USD", "E2": "USD", "E3": "EUR", "E4": "GBP"}


@dataclass
class SyntheticData:
    accounts: dict[str, Account]
    statements: tuple[Statement, ...]
    defective_statements: tuple[Statement, ...]
    fx: pd.DataFrame
    ar: pd.DataFrame
    ap: pd.DataFrame
    scheduled: pd.DataFrame
    forecast_start: date


def generate(seed: int = SEED) -> SyntheticData:
    """Generate a reproducible history and future operating ladders."""
    rng = np.random.default_rng(seed)
    accounts_list = [
        Account("E1", "BANKA", "A100", "USD", "concentration", target=200000),
        Account("E1", "BANKB", "A200", "EUR", "concentration", target=100000),
        Account("E1", "BANKC", "A300", "GBP", "concentration", target=80000),
        Account("E2", "BANKA", "A101", "USD", "disbursement", "A100", 0),
        Account("E2", "BANKB", "A102", "USD", "collection", "A100", 0),
        Account("E3", "BANKB", "A201", "EUR", "disbursement", "A200", 0),
        Account("E4", "BANKC", "A301", "GBP", "collection", "A300", 0),
        Account("E3", "BANKC", "A103", "USD", "local", target=20000),
    ]
    accounts = {a.account: a for a in accounts_list}
    initial = [700000, 300000, 200000, -120000, 80000, -60000, 30000, 40000]
    statements = []
    for index, a in enumerate(accounts_list):
        balance = initial[index] * 100
        for n in range(DAYS):
            day = START + timedelta(days=n)
            receipts = int(rng.integers(500000, 1400000))
            payments = -int(rng.integers(600000, 1500000))
            txs = (Transaction(day, day, receipts, "RECEIPT", f"T{index}{n:03d}C", "Customer receipt"),
                   Transaction(day, day, payments, "PAYMENT", f"T{index}{n:03d}D", "Vendor payment"))
            closing = balance + receipts + payments
            statements.append(Statement(f"{a.account}-{day.isoformat()}", a, day, balance, closing, txs))
            balance = closing
    defective = list(statements)
    defective[10] = replace(defective[10], transactions=defective[10].transactions + (defective[10].transactions[0],))
    defective[110] = replace(defective[110], closing_cents=defective[110].closing_cents + 10000)
    del defective[210]
    fx_rows = []
    for day_number in range(DAYS + HORIZON * 7):
        day = START + timedelta(days=day_number)
        for currency, base in (("USD", 1.0), ("EUR", 1.08), ("GBP", 1.27)):
            rate = 1.0 if currency == "USD" else base * np.exp(rng.normal(0, .008))
            fx_rows.append(dict(date=day.isoformat(), currency=currency, usd_per_unit=float(rate)))
    forecast_start = START + timedelta(days=DAYS)
    ar, ap, scheduled = [], [], []
    for entity, base in BASE_CURRENCIES.items():
        for n in range(52):
            currency = base if n % 4 else ("EUR" if base == "USD" else "USD")
            bucket = ("current", "overdue_30", "overdue_60", "overdue_90")[n % 4]
            age_range = {"current": (0, 84), "overdue_30": (-30, 0),
                         "overdue_60": (-60, -30), "overdue_90": (-90, -60)}[bucket]
            due = forecast_start + timedelta(days=int(rng.integers(*age_range)))
            ar.append(dict(entity=entity, currency=currency, invoice=f"AR{entity}{n}",
                           amount=float(rng.integers(6000, 19000)), due_date=due.isoformat(), aging_bucket=bucket))
            due_ap = forecast_start + timedelta(days=int(rng.integers(-14, 91)))
            ap.append(dict(entity=entity, currency=currency, invoice=f"AP{entity}{n}",
                           amount=float(rng.integers(3000, 10000)), due_date=due_ap.isoformat()))
        for week in range(HORIZON):
            day = forecast_start + timedelta(days=week * 7 + 4)
            for line, amount in (("payroll", -12000.0), ("tax", -3500.0 if week % 4 == 3 else 0.0),
                                 ("debt_service", -5000.0 if week % 4 == 0 else 0.0)):
                if amount:
                    scheduled.append(dict(entity=entity, currency=base, date=day.isoformat(), line=line, amount=amount))
    for week in (1, 5, 9):
        day = (forecast_start + timedelta(days=week * 7)).isoformat()
        scheduled.extend([dict(entity="E1", currency="USD", date=day, line="intercompany", amount=-18000.0),
                          dict(entity="E2", currency="USD", date=day, line="intercompany", amount=18000.0)])
    return SyntheticData(accounts, tuple(statements), tuple(defective), pd.DataFrame(fx_rows),
                         pd.DataFrame(ar), pd.DataFrame(ap), pd.DataFrame(scheduled), forecast_start)


def write_statements(data: SyntheticData, directory: Path) -> dict[str, Path]:
    """Write clean and defective files in all supported formats."""
    paths = {}
    for label, items in (("clean", data.statements), ("defective", data.defective_statements)):
        folder = directory / label
        folder.mkdir(parents=True, exist_ok=True)
        for name, serializer, extension in (("camt", to_camt053, "xml"),
                                             ("bai2", to_bai2, "bai"),
                                             ("mt940", to_mt940, "sta")):
            path = folder / f"synthetic.{extension}"
            path.write_text(serializer(items), encoding="utf-8")
            paths[f"{label}_{name}"] = path
    return paths
