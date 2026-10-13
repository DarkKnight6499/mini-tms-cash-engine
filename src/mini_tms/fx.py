"""Net forecast FX exposure and stylized tenor hedge recommendations."""

from math import isfinite
from typing import Mapping

import pandas as pd

from .rates import FXTable

# (target, minimum, maximum) ratios are synthetic treasury policy bands.
HEDGE_POLICY = {"near": (.80, .70, .90), "medium": (.60, .50, .70), "far": (.40, .30, .50)}
STYLIZED_FORWARD_RATES = {"USD": .045, "EUR": .030, "GBP": .050}


def tenor_bucket(week: int) -> str:
    """Map zero-based weeks to near, medium and far policy buckets."""
    if not isinstance(week, int) or not 0 <= week < 13:
        raise ValueError("Week outside the forecast horizon")
    return "near" if week < 4 else "medium" if week < 8 else "far"


def forward_rate(spot: float, domestic_rate: float, foreign_rate: float, days: int) -> float:
    """Use simple-interest parity for domestic units per foreign unit."""
    if not all(isfinite(x) for x in (spot, domestic_rate, foreign_rate)) or spot <= 0 or days < 0:
        raise ValueError("Invalid forward inputs")
    term = days / 365
    if 1 + domestic_rate * term <= 0 or 1 + foreign_rate * term <= 0:
        raise ValueError("Invalid discount factors")
    return spot * (1 + domestic_rate * term) / (1 + foreign_rate * term)


def hedge_recommendations(flows: pd.DataFrame, base_currencies: Mapping[str, str],
                          fx: FXTable, valuation_day: str,
                          policy: Mapping | None = None,
                          rates: Mapping[str, float] | None = None) -> pd.DataFrame:
    """Recommend opposing forward notionals for net foreign cash flows."""
    policy = HEDGE_POLICY if policy is None else policy
    rates = STYLIZED_FORWARD_RATES if rates is None else rates
    for bucket in ("near", "medium", "far"):
        target, minimum, maximum = policy[bucket]
        if not all(isfinite(v) for v in (target, minimum, maximum)) or not 0 <= minimum <= target <= maximum <= 1:
            raise ValueError("Invalid hedge policy band")
    out = flows.copy()
    if not out.amount.map(isfinite).all():
        raise ValueError("Nonfinite FX exposure")
    out["bucket"] = [tenor_bucket(int(w)) if int(w) == w else tenor_bucket(-1) for w in out.week]
    rows = []
    for (entity, currency, bucket), group in out.groupby(["entity", "currency", "bucket"]):
        if entity not in base_currencies:
            raise ValueError("Missing entity functional currency")
        domestic = base_currencies[entity]
        if currency == domestic:
            continue
        exposure = float(group.amount.sum())
        target, minimum, maximum = policy[bucket]
        hedge = -exposure * target
        tenor_days = {"near": 28, "medium": 56, "far": 91}[bucket]
        spot = fx.rate(valuation_day, currency, domestic)
        forward = forward_rate(spot, rates[domestic], rates[currency], tenor_days)
        rows.append(dict(entity=entity, currency=currency, functional_currency=domestic,
                         bucket=bucket, tenor_days=tenor_days, exposure_before=exposure,
                         hedge_ratio=target, policy_min=minimum, policy_max=maximum,
                         hedge_notional=hedge, action="sell" if hedge < 0 else "buy" if hedge > 0 else "none",
                         exposure_after=exposure + hedge, spot=spot, forward=forward,
                         forward_points=forward - spot,
                         exposure_before_usd=exposure * fx.rate(valuation_day, currency),
                         exposure_after_usd=(exposure + hedge) * fx.rate(valuation_day, currency)))
    return pd.DataFrame(rows)
