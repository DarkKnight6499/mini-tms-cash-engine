"""Cash conversion, discount economics, AR collection and annual XBRL normalization."""

from datetime import date, timedelta

import numpy as np
import pandas as pd

from . import config as cfg
from .config import C


def cash_conversion(ar, inventory, ap, revenue, cogs, days=cfg.YEAR_DAYS,
                    opening=None, use_average=cfg.USE_AVERAGE_BALANCES):
    """Use average opening/closing balances unless ending balances are requested."""
    values = [ar, inventory, ap, revenue, cogs, days]
    if not np.isfinite(values).all() or min(ar, inventory, ap) < 0 or min(revenue, cogs, days) <= 0:
        raise ValueError("Finite balances and positive annual flows and days required")
    balances = {C.AR: ar, C.INVENTORY: inventory, C.AP: ap}
    if use_average:
        if opening is None or any(k not in opening for k in balances):
            raise ValueError("Opening balances required for average method")
        if not np.isfinite(list(opening.values())).all() or min(opening.values()) < 0:
            raise ValueError("Finite nonnegative opening balances required")
        balances = {k: (v + opening[k]) / 2 for k, v in balances.items()}
    dso = balances[C.AR] / revenue * days
    dio = balances[C.INVENTORY] / cogs * days
    dpo = balances[C.AP] / cogs * days
    return {C.DSO: dso, C.DIO: dio, C.DPO: dpo, C.CCC: dso + dio - dpo,
            C.CASH_DSO: revenue / days, C.CASH_DIO: cogs / days, C.CASH_DPO: cogs / days,
            C.BALANCE_BASIS: "average" if use_average else "ending"}


def discount_apr(discount=cfg.DISCOUNT_RATE, discount_days=cfg.DISCOUNT_DAYS, net_days=cfg.NET_DAYS):
    """Simple annualized cost of forgoing a supplier discount."""
    if not np.isfinite([discount, discount_days, net_days]).all() or not 0 <= discount < 1 or not 0 <= discount_days < net_days:
        raise ValueError("Discount below one and net days after discount days required")
    return discount / (1 - discount) * cfg.YEAR_DAYS / (net_days - discount_days)


def discount_decisions(discount=cfg.DISCOUNT_RATE, discount_days=cfg.DISCOUNT_DAYS,
                       net_days=cfg.NET_DAYS, payment_day=cfg.DYNAMIC_PAYMENT_DAY,
                       borrowing_rate=cfg.BORROWING_RATE, scf_rate=cfg.SCF_RATE,
                       extension_days=cfg.SCF_EXTENSION_DAYS):
    """Compare discount funding and buyer-funded SCF with short-term borrowing."""
    apr = discount_apr(discount, discount_days, net_days)
    if (not np.isfinite([payment_day, borrowing_rate, scf_rate, extension_days]).all() or
            not discount_days <= payment_day < net_days or min(borrowing_rate, scf_rate) < 0 or extension_days <= 0):
        raise ValueError("Valid dynamic payment date and nonnegative funding rates required")
    dynamic = discount * (net_days - payment_day) / (net_days - discount_days)
    dynamic_apr = discount_apr(dynamic, payment_day, net_days)
    maximum_discount = borrowing_rate * (net_days - payment_day) / cfg.YEAR_DAYS
    maximum_discount /= 1 + maximum_discount
    return {C.DISCOUNT: discount, C.DISCOUNT_DAYS: discount_days, C.NET_DAYS: net_days, C.APR: apr,
            C.BORROWING: borrowing_rate, "take_discount": apr > borrowing_rate,
            "dynamic_discount": dynamic, "dynamic_apr": dynamic_apr,
            "dynamic_break_even_discount": maximum_discount,
            "dynamic_discount_is_fundable": dynamic_apr >= borrowing_rate,
            "scf_rate": scf_rate, "scf_break_even_rate": borrowing_rate,
            "scf_cost_per_invoice_dollar": scf_rate * extension_days / cfg.YEAR_DAYS,
            "scf_net_benefit_per_invoice_dollar": (borrowing_rate - scf_rate) * extension_days / cfg.YEAR_DAYS,
            "scf_is_economic": scf_rate < borrowing_rate}


def collections_priority(ar, as_of: date, fx):
    """Re-age the existing AR ladder, ordering overdue days then USD amount."""
    out = ar.copy()
    if not np.isfinite(out[C.AMOUNT]).all() or (out[C.AMOUNT] < 0).any():
        raise ValueError("Nonnegative finite AR required")
    out[C.AGE] = [(as_of - date.fromisoformat(v)).days for v in out[C.DUE_DATE]]
    out[C.AGING] = pd.cut(out[C.AGE], bins=[-np.inf, *cfg.AGING_LIMITS, np.inf],
                          labels=cfg.AGING_LABELS).astype(str)
    out[C.USD] = [r[C.AMOUNT] * fx.rate(as_of.isoformat(), r[C.CURRENCY]) for r in out.to_dict("records")]
    out = out.sort_values([C.AGE, C.USD, C.INVOICE], ascending=[False, False, True]).reset_index(drop=True)
    out[C.PRIORITY] = np.arange(1, len(out) + 1)
    aging = out.groupby([C.ENTITY, C.AGING], as_index=False)[C.USD].sum()
    return aging, out


def annual_companyfacts(payload, issuer, years=cfg.WC_YEARS):
    """Resolve annual USD facts by period, not comparative facts' filing fiscal year."""
    facts = payload.get("facts", {}).get("us-gaap", {})

    def candidates(metric, end=None, duration=False, start=None):
        for tag in cfg.WC_TAGS[metric]:
            rows = []
            for r in facts.get(tag, {}).get("units", {}).get("USD", []):
                if r.get("form") != "10-K" or (end is not None and r.get("end") != end):
                    continue
                if duration:
                    if not r.get("start") or r.get("fp") != "FY":
                        continue
                    days = (date.fromisoformat(r["end"]) - date.fromisoformat(r["start"])).days + 1
                    if not cfg.ANNUAL_DAY_RANGE[0] <= days <= cfg.ANNUAL_DAY_RANGE[1]:
                        continue
                    if start is not None and r["start"] != start:
                        continue
                elif r.get("start"):
                    continue
                if not np.isfinite(r.get("val", float("nan"))):
                    continue
                rows.append({**r, "tag": tag})
            if rows:
                return rows
        return []

    # Gather all fallback tags so older periods are not hidden by newer tag adoption.
    ends, annual_revenue = set(), []
    for tag in cfg.WC_TAGS[C.REVENUE]:
        for r in facts.get(tag, {}).get("units", {}).get("USD", []):
            if r.get("form") == "10-K" and r.get("fp") == "FY" and r.get("start"):
                length = (date.fromisoformat(r["end"]) - date.fromisoformat(r["start"])).days + 1
                if cfg.ANNUAL_DAY_RANGE[0] <= length <= cfg.ANNUAL_DAY_RANGE[1]:
                    ends.add(r["end"])
                    annual_revenue.append(r)
    if len(ends) < years:
        raise ValueError(f"{issuer}: fewer than {years} annual revenue periods")
    output = []
    for end in sorted(ends)[-years:]:
        revenue_rows = candidates(C.REVENUE, end, True)
        revenue = max(revenue_rows, key=lambda r: (r.get("filed", ""), r.get("accn", "")))
        first = min((r for r in annual_revenue if r["end"] == end), key=lambda r: r.get("filed", ""))
        start = revenue["start"]
        opening_end = (date.fromisoformat(start) - timedelta(days=1)).isoformat()
        row = {C.ISSUER: issuer, C.FISCAL_YEAR: first["fy"], C.PERIOD_START: start, C.PERIOD_END: end,
               C.DAYS: (date.fromisoformat(end) - date.fromisoformat(start)).days + 1,
               C.PROVENANCE: "SEC companyfacts annual 10-K USD"}
        for metric in cfg.WC_TAGS:
            is_duration = metric in (C.REVENUE, C.COGS)
            records = candidates(metric, end, is_duration, start if is_duration else None)
            if not records:
                raise ValueError(f"{issuer} {end}: missing aligned annual {metric}")
            selected = max(records, key=lambda r: (r.get("filed", ""), r.get("accn", "")))
            row[metric] = float(selected["val"])
            row[f"{metric}_tag"] = selected["tag"]
            row[f"{metric}_accession"] = selected.get("accn", "")
            if metric in (C.AR, C.INVENTORY, C.AP):
                opening_rows = candidates(metric, opening_end)
                if not opening_rows:
                    raise ValueError(f"{issuer} {end}: missing opening {metric} at {opening_end}")
                selected_open = max(opening_rows, key=lambda r: (r.get("filed", ""), r.get("accn", "")))
                row[f"{metric}_open"] = float(selected_open["val"])
                row[f"{metric}_open_tag"] = selected_open["tag"]
                row[f"{metric}_open_accession"] = selected_open.get("accn", "")
        output.append(row)
    return pd.DataFrame(output)


def working_capital_table(snapshot, use_average=cfg.USE_AVERAGE_BALANCES):
    """Calculate metrics from normalized snapshots or matching synthetic fixtures."""
    if snapshot.duplicated([C.ISSUER, C.PERIOD_END]).any():
        raise ValueError("Duplicate issuer fiscal period")
    rows = []
    for r in snapshot.to_dict("records"):
        opening = {C.AR: r.get(C.AR_OPEN), C.INVENTORY: r.get(C.INVENTORY_OPEN), C.AP: r.get(C.AP_OPEN)}
        metrics = cash_conversion(r[C.AR], r[C.INVENTORY], r[C.AP], r[C.REVENUE], r[C.COGS],
                                   r[C.DAYS], opening, use_average)
        rows.append({**r, **metrics})
    return pd.DataFrame(rows)


def chart_ccc(table, path):
    """Write a standalone chart with the snapshot's provenance visible."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axis = plt.subplots(figsize=(8, 4))
    for issuer, group in table.groupby(C.ISSUER):
        group = group.sort_values(C.PERIOD_END)
        axis.plot(group[C.FISCAL_YEAR], group[C.CCC], marker="o", label=issuer)
    synthetic = table[C.PROVENANCE].str.contains("synthetic", case=False).any()
    axis.set(title="Illustrative synthetic CCC" if synthetic else "SEC annual cash conversion cycle",
             xlabel="Fiscal year", ylabel="Days")
    axis.set_xticks(sorted(table[C.FISCAL_YEAR].unique()))
    axis.legend()
    fig.tight_layout()
    fig.savefig(path, metadata={"Author": cfg.AUTHOR})
    plt.close(fig)
