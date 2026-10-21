"""Illustrative execution support and scoped Rule 10b-18 condition screening."""

from datetime import date, datetime, timedelta
from math import floor

import numpy as np
import pandas as pd

from . import config as cfg
from .config import C


def _week(day):
    return day - timedelta(days=day.weekday())


def adtv(history, purchase_day: date):
    """Use trading days in four full prior calendar weeks, excluding weekly blocks."""
    frame = history.copy()
    if frame[C.DATE].duplicated().any():
        raise ValueError("One trading-volume record per date required")
    dates = pd.to_datetime(frame[C.DATE]).dt.date
    end = _week(purchase_day)
    start = end - timedelta(weeks=cfg.ADTV_WEEKS)
    frame = frame.loc[(dates >= start) & (dates < end)].copy()
    selected_dates = pd.to_datetime(frame[C.DATE]).dt.date
    expected_weeks = {start + timedelta(weeks=n) for n in range(cfg.ADTV_WEEKS)}
    if frame.empty or {_week(d) for d in selected_dates} != expected_weeks:
        raise ValueError("Prior four-calendar-week trading history required")
    excluded = frame[C.VOLUME_EXCLUDED] if C.VOLUME_EXCLUDED in frame else 0.0
    volumes = frame[C.VOLUME] - excluded
    if (not np.isfinite(volumes).all() or (volumes < 0).any() or np.any(excluded < 0)
            or any(d.weekday() >= 5 for d in selected_dates)):
        raise ValueError("Finite trading volume at least excluded block volume required")
    return float(volumes.mean())


def volume_cap(history, purchase_day: date):
    """Return the standard daily share cap, rounded down to whole shares."""
    return floor(adtv(history, purchase_day) * cfg.VOLUME_FRACTION)


def authorization_tracker(blotter, authorization=cfg.AUTHORIZATION):
    """Report executed spend and assumed retirement without changing authorization."""
    if not np.isfinite(authorization) or authorization < 0:
        raise ValueError("Nonnegative authorization required")
    values = blotter[[C.SHARES, C.PRICE]]
    if not np.isfinite(values).all().all() or (values <= 0).any().any():
        raise ValueError("Positive executed shares and prices required")
    spend = float((blotter[C.SHARES] * blotter[C.PRICE]).sum())
    shares = float(blotter[C.SHARES].sum())
    return {"authorization": authorization, "cumulative_spend": spend,
            "remaining_capacity": authorization - spend, "shares_retired": shares,
            "average_price": spend / shares if shares else 0.0, "authorization_breached": spend > authorization}


def execution_plan(trading_days, history, spend=cfg.PLANNED_BUYBACK_SPEND,
                    price=cfg.BUYBACK_PRICE, remaining=cfg.AUTHORIZATION, broker=cfg.BUYBACK_BROKER):
    """Allocate whole shares within both remaining capacity and the standard volume cap."""
    if not np.isfinite([spend, price, remaining]).all() or min(spend, remaining) < 0 or price <= 0:
        raise ValueError("Valid plan budget, capacity and price required")
    dates = sorted(set(trading_days))
    budget = min(spend, remaining)
    rows = []
    for day in dates:
        if day.weekday() >= 5:
            raise ValueError("Trading dates must be supplied explicitly")
        average = adtv(history, day)
        cap = volume_cap(history, day)
        shares = min(floor(budget / price), cap)
        if shares:
            rows.append({C.DATE: day.isoformat(), C.BROKER: broker, C.TIME: cfg.NORMAL_TIMES[0],
                         C.SHARES: shares, C.PRICE: price, C.SPEND: shares * price,
                         C.ADTV: average, C.CAP: cap})
            budget -= shares * price
    return pd.DataFrame(rows, columns=[C.DATE, C.BROKER, C.TIME, C.SHARES, C.PRICE, C.SPEND, C.ADTV, C.CAP])


def check_safe_harbor(blotter, history, adtv_value=cfg.ADTV_VALUE, public_float=cfg.PUBLIC_FLOAT,
                       principal_close=cfg.MARKET_CLOSE, execution_close=cfg.MARKET_CLOSE,
                       prior_block_dates=()):
    """Screen solicited primary-session consolidated-market purchases of one security."""
    if (not np.isfinite([adtv_value, public_float]).all() or min(adtv_value, public_float) < 0
            or blotter[C.TRADE_ID].duplicated().any()):
        raise ValueError("Unique trades and finite nonnegative security values required")
    numeric = blotter[[C.SHARES, C.PRICE, C.BID, C.LAST]]
    if not np.isfinite(numeric).all().all() or (numeric <= 0).any().any():
        raise ValueError("Positive finite shares, prices and independent quotes required")
    if (blotter[C.SHARES] % 1 != 0).any():
        raise ValueError("Whole shares required")
    blackout = cfg.LARGE_BLACKOUT_MINUTES if (adtv_value >= cfg.LARGE_ADTV_VALUE and
                public_float >= cfg.LARGE_PUBLIC_FLOAT) else cfg.OTHER_BLACKOUT_MINUTES
    used_weeks = {_week(day) for day in prior_block_dates}
    violations = []

    def flag(r, rule, detail):
        violations.append({C.DATE: r[C.DATE], C.TRADE_ID: r[C.TRADE_ID], C.RULE: rule, C.DETAIL: detail})

    for stamp, daily in blotter.sort_values([C.DATE, C.TIME, C.TRADE_ID]).groupby(C.DATE, sort=True):
        day = date.fromisoformat(stamp)
        records = daily.to_dict("records")
        if day.weekday() >= 5:
            raise ValueError("Primary-session trading date required")
        if daily[C.BROKER].nunique() != 1:
            for r in records:
                flag(r, "(b)(1)", "More than one broker or dealer in the primary session")
        block_requested = daily[C.BLOCK].any()
        qualified = C.BLOCK_QUALIFIED in daily and daily[C.BLOCK_QUALIFIED].all()
        block_ok = block_requested and qualified and len(daily) == 1 and _week(day) not in used_weeks
        if block_ok:
            used_weeks.add(_week(day))
        if block_requested and not block_ok:
            for r in records:
                flag(r, "(b)(4)", "Weekly block requires verified eligibility, one purchase, and unused weekly exception")
        if not block_ok and daily[C.SHARES].sum() > volume_cap(history, day):
            for r in records:
                flag(r, "(b)(4)", "Daily purchases exceed 25% of prior four-calendar-week ADTV")
        for r in records:
            instant = datetime.combine(day, datetime.strptime(r[C.TIME], "%H:%M").time())
            opening = datetime.combine(day, datetime.strptime(cfg.MARKET_OPEN, "%H:%M").time())
            closes = [datetime.combine(day, datetime.strptime(t, "%H:%M").time())
                      for t in (principal_close, execution_close)]
            if instant < opening or instant >= min(closes):
                flag(r, "scope", "Outside supported primary session; after-hours conditions require separate review")
            if r[C.OPENING]:
                flag(r, "(b)(2)(i)", "Opening regular-way purchase")
            if any(close - timedelta(minutes=blackout) <= instant < close for close in closes):
                flag(r, "(b)(2)(ii)" if blackout == cfg.LARGE_BLACKOUT_MINUTES else "(b)(2)(iii)",
                     "Purchase within the applicable closing restriction")
            if r[C.PRICE] > max(r[C.BID], r[C.LAST]):
                flag(r, "(b)(3)", "Price above both independent bid and last independent transaction")
    return pd.DataFrame(violations, columns=[C.DATE, C.TRADE_ID, C.RULE, C.DETAIL])


def eps_accretion(net_income, shares, spend, average_price, cash_yield=cfg.CASH_YIELD, tax=cfg.TAX_RATE):
    """Assume immediate retirement and a full year of forgone after-tax cash interest."""
    if (not np.isfinite([net_income, shares, spend, average_price, cash_yield, tax]).all()
            or min(shares, average_price) <= 0 or min(spend, cash_yield) < 0 or not 0 <= tax <= 1):
        raise ValueError("Valid EPS inputs required")
    retired = spend / average_price
    if retired >= shares:
        raise ValueError("Repurchase must leave positive shares outstanding")
    pro_income = net_income - spend * cash_yield * (1 - tax)
    return {"shares_retired": retired, "pro_forma_shares": shares - retired,
            "pro_forma_net_income": pro_income, "base_eps": net_income / shares,
            "pro_forma_eps": pro_income / (shares - retired)}


def synthetic_blotter(start: date, seed=cfg.BUYBACK_SEED):
    """Seed an illustrative blotter with broker, opening, timing, price and volume exceptions."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(start, periods=cfg.BUYBACK_DAYS).date
    history_days = pd.bdate_range(_week(start) - timedelta(weeks=cfg.ADTV_WEEKS),
                                 days[-1] - timedelta(days=1)).date
    history = pd.DataFrame([{C.DATE: d.isoformat(), C.VOLUME: cfg.BUYBACK_HISTORY_VOLUME,
                             C.VOLUME_EXCLUDED: 0} for d in history_days])
    rows = []
    for number, day in enumerate(days):
        for slot, time in enumerate(cfg.NORMAL_TIMES):
            rows.append({C.TRADE_ID: f"BB-{number}-{slot}", C.DATE: day.isoformat(), C.TIME: time,
                         C.BROKER: cfg.BUYBACK_BROKER, C.SHARES: int(rng.integers(*cfg.SYNTHETIC_TRADE_RANGE)),
                         C.PRICE: cfg.BUYBACK_PRICE, C.BID: cfg.BUYBACK_PRICE, C.LAST: cfg.BUYBACK_PRICE,
                         C.OPENING: False, C.BLOCK: False, C.BLOCK_QUALIFIED: False})
    rows[0].update({C.OPENING: True, C.TIME: cfg.MARKET_OPEN, C.SHARES: cfg.DELIBERATE_EXCESS_SHARES})
    rows[1][C.BROKER] = cfg.SECOND_BROKER
    rows[2][C.TIME] = cfg.LATE_TIME
    rows[3][C.PRICE] += cfg.PRICE_VIOLATION_UPLIFT
    return pd.DataFrame(rows), history
