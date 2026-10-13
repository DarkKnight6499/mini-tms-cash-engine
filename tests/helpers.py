"""Shared import bootstrap and hand-calculable test data."""

from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd

from mini_tms.models import Account, Statement, Transaction
from mini_tms.rates import FXTable

FIXTURES = Path(__file__).parent / "fixtures"
DAY = date(2025, 1, 1)
ACCOUNT = Account("E", "BANK", "001", "EUR")
ACCOUNTS = {"001": ACCOUNT}


def tiny_statement() -> Statement:
    return Statement("S1", ACCOUNT, DAY, 10000, 12500,
                     (Transaction(DAY, DAY, 5000, "195", "R1", "Receipt"),
                      Transaction(DAY, DAY, -2500, "495", "R2", "Payment")))


def tiny_fx() -> FXTable:
    return FXTable(pd.DataFrame([dict(date=DAY.isoformat(), currency=c, usd_per_unit=r)
                                 for c, r in (("USD", 1.0), ("EUR", 1.2), ("GBP", 1.5))]))
