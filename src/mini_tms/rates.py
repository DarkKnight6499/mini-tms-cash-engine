"""Validated FX quotes, expressed as USD per unit of native currency."""

from math import isfinite

import pandas as pd


class FXTable:
    """Exact-date cross conversion without silently carrying forward rates."""

    def __init__(self, frame: pd.DataFrame) -> None:
        self._quotes: dict[tuple[str, str], float] = {}
        for row in frame.itertuples():
            key = (str(row.date), row.currency)
            if key in self._quotes:
                raise ValueError("Duplicate FX quote")
            rate = float(row.usd_per_unit)
            if not isfinite(rate) or rate <= 0 or (row.currency == "USD" and rate != 1):
                raise ValueError("Invalid FX quote")
            self._quotes[key] = rate

    def rate(self, day: str, currency: str, reporting_currency: str = "USD") -> float:
        """Return reporting currency units per native currency unit."""
        try:
            return self._quotes[(day, currency)] / self._quotes[(day, reporting_currency)]
        except KeyError as exc:
            raise ValueError(f"Missing FX quote on {day}: {currency}/{reporting_currency}") from exc
