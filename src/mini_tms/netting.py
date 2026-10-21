"""Multilateral cycle valuation, functional-currency settlement and payment batches."""

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from . import config as cfg
from .config import C
from .models import cents
from .payments import Batch, Payment
from .rates import FXTable


@dataclass
class NettingResult:
    invoices: pd.DataFrame
    bilateral_payables: pd.DataFrame
    bilateral_net: pd.DataFrame
    positions: pd.DataFrame
    instructions: pd.DataFrame
    fx_trades: pd.DataFrame
    metrics: dict


def synthetic_invoices(cycle_date: date, count: int = cfg.INVOICE_COUNT,
                       seed: int = cfg.NETTING_SEED) -> pd.DataFrame:
    """Create one illustrative monthly cycle using the existing entity universe."""
    if count <= 0:
        raise ValueError("Positive invoice count required")
    rng = np.random.default_rng(seed)
    entities = tuple(cfg.BASE_CURRENCIES)
    currencies = tuple(sorted(set(cfg.BASE_CURRENCIES.values())))
    rows = []
    for number in range(count):
        payer = str(rng.choice(entities))
        receiver = str(rng.choice([e for e in entities if e != payer]))
        rows.append({C.INVOICE_ID: f"IC{number:04d}", C.PAYER: payer, C.RECEIVER: receiver,
                     C.CURRENCY: str(rng.choice(currencies)),
                     C.AMOUNT: float(rng.integers(*cfg.INVOICE_RANGE)),
                     C.DUE_DATE: (cycle_date + timedelta(days=int(rng.integers(cfg.CYCLE_DAYS)))).isoformat()})
    return pd.DataFrame(rows)


def run_netting(invoices: pd.DataFrame, fx: FXTable, cycle_day: str,
                functional_currencies=None, wire_fee: float = cfg.WIRE_FEE,
                spread_bps: float = cfg.FX_SPREAD_BPS) -> NettingResult:
    """Net exact USD values; round only actual native-currency wire instructions."""
    functional = cfg.BASE_CURRENCIES if functional_currencies is None else functional_currencies
    if wire_fee < 0 or spread_bps < 0 or not np.isfinite([wire_fee, spread_bps]).all():
        raise ValueError("Finite nonnegative costs required")
    out = invoices.copy()
    if (out.empty or out[C.INVOICE_ID].duplicated().any() or
            not np.isfinite(out[C.AMOUNT]).all() or (out[C.AMOUNT] <= 0).any() or
            (out[C.PAYER] == out[C.RECEIVER]).any()):
        raise ValueError("Unique positive invoices between distinct entities required")
    if not set(out[C.PAYER]).union(out[C.RECEIVER]).issubset(functional):
        raise ValueError("Missing functional currency")
    pd.to_datetime(out[C.DUE_DATE], errors="raise")
    out[C.USD] = [r[C.AMOUNT] * fx.rate(cycle_day, r[C.CURRENCY]) for r in out.to_dict("records")]
    entities = sorted(functional)
    matrix = out.pivot_table(index=C.PAYER, columns=C.RECEIVER, values=C.USD, aggfunc="sum", fill_value=0)
    matrix = matrix.reindex(index=entities, columns=entities, fill_value=0).astype(float)
    net_matrix = matrix - matrix.T
    bilateral = [{C.PAYER: a, C.RECEIVER: b, C.USD: float(net_matrix.loc[a, b])}
                 for a in entities for b in entities if net_matrix.loc[a, b] > cfg.AMOUNT_TOLERANCE]
    positions = pd.DataFrame({C.ENTITY: entities,
                             C.NET: (matrix.sum(axis=0) - matrix.sum(axis=1)).reindex(entities).to_numpy()})
    instructions = []
    for row in positions.to_dict("records"):
        entity, net = row[C.ENTITY], row[C.NET]
        if abs(net) <= cfg.AMOUNT_TOLERANCE:
            continue
        currency = functional[entity]
        rate = fx.rate(cycle_day, currency)
        amount = round(abs(net) / rate, 2)
        signed = amount if net > 0 else -amount
        instructions.append({C.PAYMENT_ID: f"NET-{entity}", C.ENTITY: entity,
                             C.PAYER: cfg.CENTER if net > 0 else entity,
                             C.RECEIVER: entity if net > 0 else cfg.CENTER,
                             C.CURRENCY: currency, C.AMOUNT: amount, C.SIGNED: signed,
                             C.USD: abs(net), C.FX_RATE: rate, C.ROUNDING: net - signed * rate,
                             C.DATE: cycle_day})
    columns = [C.PAYMENT_ID, C.ENTITY, C.PAYER, C.RECEIVER, C.CURRENCY, C.AMOUNT,
               C.SIGNED, C.USD, C.FX_RATE, C.ROUNDING, C.DATE]
    settlement = pd.DataFrame(instructions, columns=columns)
    trades = settlement.loc[settlement[C.CURRENCY] != cfg.NETTING_CURRENCY].copy()
    trades[C.ACTION] = np.where(trades[C.SIGNED] > 0, "buy", "sell")
    gross_fx_count, gross_fx_volume = 0, 0.0
    for row in out.to_dict("records"):
        for party in (C.PAYER, C.RECEIVER):
            if functional[row[party]] != row[C.CURRENCY]:
                gross_fx_count += 1
                gross_fx_volume += row[C.USD]
    gross = float(out[C.USD].sum())
    multilateral = float(-positions.loc[positions[C.NET] < 0, C.NET].sum())
    avoided_volume = gross_fx_volume - float(trades[C.USD].sum())
    metrics = dict(gross_payment_count=len(out), gross_value_usd=gross,
                   bilateral_payment_count=len(bilateral),
                   bilateral_value_usd=sum(r[C.USD] for r in bilateral),
                   multilateral_payment_count=len(settlement), multilateral_value_usd=multilateral,
                   value_reduction_pct=100 * (1 - multilateral / gross),
                   gross_fx_conversions=gross_fx_count, net_fx_conversions=len(trades),
                   fx_conversions_avoided=gross_fx_count - len(trades),
                   gross_converted_volume_usd=gross_fx_volume,
                   net_converted_volume_usd=float(trades[C.USD].sum()),
                   converted_volume_avoided_usd=avoided_volume,
                   estimated_savings_usd=(len(out) - len(settlement)) * wire_fee + avoided_volume * spread_bps / 10000,
                   wire_fee_illustrative=wire_fee, fx_spread_bps_illustrative=spread_bps,
                   rounding_residual_usd=float(settlement[C.ROUNDING].sum()))
    return NettingResult(out, matrix, pd.DataFrame(bilateral, columns=[C.PAYER, C.RECEIVER, C.USD]),
                         positions, settlement, trades, metrics)


def settlement_batches(result: NettingResult, accounts, maker: str = cfg.MAKER) -> tuple[Batch, ...]:
    """Create unapproved batches for the existing maker-checker export path."""
    def account_for(entity, currency):
        if entity == cfg.CENTER:
            return cfg.CENTER_ACCOUNTS[currency]
        owner = entity
        candidates = [a for a in accounts.values() if a.entity == owner and a.currency == currency]
        if not candidates:
            raise ValueError("Missing settlement account")
        return sorted(candidates, key=lambda a: (a.role != "concentration", a.account))[0].account

    batches = []
    for r in result.instructions.to_dict("records"):
        if r[C.AMOUNT] <= 0:
            raise ValueError("Net amount rounds below one cent")
        payment = Payment(r[C.PAYMENT_ID], r[C.RECEIVER], account_for(r[C.RECEIVER], r[C.CURRENCY]),
                          r[C.CURRENCY], cents(r[C.AMOUNT]), "Synthetic netting settlement")
        batches.append(Batch(r[C.PAYMENT_ID], maker, r[C.PAYER], account_for(r[C.PAYER], r[C.CURRENCY]),
                             date.fromisoformat(r[C.DATE]), (payment,)))
    return tuple(batches)
