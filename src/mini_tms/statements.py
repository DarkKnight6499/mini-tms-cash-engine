"""Synthetic statement serializers for the supported parser profiles."""

from itertools import groupby
from typing import Iterable
from xml.etree import ElementTree as ET

from .models import Statement, money
from .parsers import CAMT_NS


def _child(parent: ET.Element, tag: str, value: str | None = None,
           **attributes: str) -> ET.Element:
    element = ET.SubElement(parent, tag, attributes)
    element.text = value
    return element


def to_camt053(statements: Iterable[Statement]) -> str:
    """Serialize booked entries in camt.053.001.02."""
    root = ET.Element("Document", xmlns=CAMT_NS)
    message = _child(root, "BkToCstmrStmt")
    header = _child(message, "GrpHdr")
    _child(header, "MsgId", "SYNTHETIC-CAMT")
    _child(header, "CreDtTm", "2025-04-01T00:00:00Z")
    for s in statements:
        stmt = _child(message, "Stmt")
        _child(stmt, "Id", s.statement_id)
        _child(stmt, "CreDtTm", s.day.isoformat() + "T23:59:00Z")
        account = _child(stmt, "Acct")
        _child(_child(_child(account, "Id"), "Othr"), "Id", s.account.account)
        _child(account, "Ccy", s.account.currency)
        for code, value in (("OPBD", s.opening_cents), ("CLBD", s.closing_cents)):
            b = _child(stmt, "Bal")
            _child(_child(_child(b, "Tp"), "CdOrPrtry"), "Cd", code)
            _child(b, "Amt", money(abs(value)), Ccy=s.account.currency)
            _child(b, "CdtDbtInd", "CRDT" if value >= 0 else "DBIT")
            _child(_child(b, "Dt"), "Dt", s.day.isoformat())
        for t in s.transactions:
            n = _child(stmt, "Ntry")
            _child(n, "NtryRef", t.reference)
            _child(n, "Amt", money(abs(t.amount_cents)), Ccy=s.account.currency)
            _child(n, "CdtDbtInd", "CRDT" if t.amount_cents >= 0 else "DBIT")
            _child(n, "Sts", "BOOK")
            _child(_child(n, "BookgDt"), "Dt", t.booking_date.isoformat())
            _child(_child(n, "ValDt"), "Dt", t.value_date.isoformat())
            _child(_child(_child(n, "BkTxCd"), "Prtry"), "Cd", t.type_code)
            refs = _child(_child(_child(n, "NtryDtls"), "TxDtls"), "Refs")
            _child(refs, "AcctSvcrRef", t.reference)
            _child(n, "AddtlNtryInf", t.description)
    ET.indent(root)
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def to_bai2(statements: Iterable[Statement]) -> str:
    """Serialize BAI2 totals including physical continuation record counts."""
    items = sorted(statements, key=lambda s: (s.day, s.account.bank, s.account.currency, s.account.account))
    lines = ["01,SYNTHETIC,TREASURY,250401,0000,1,,,2/"]
    file_total = group_count = 0
    for (day, bank, currency), group in groupby(items, key=lambda s: (s.day, s.account.bank, s.account.currency)):
        group_start = len(lines)
        lines.append(f"02,TREASURY,{bank},1,{day:%y%m%d},2359,{currency},2/")
        group_total = account_count = 0
        for s in group:
            account_start = len(lines)
            lines.append(f"03,{s.account.account},{currency},010,{s.opening_cents},,/")
            lines.append(f"88,015,{s.closing_cents},,/")
            total = s.opening_cents + s.closing_cents
            for t in s.transactions:
                code = "195" if t.amount_cents >= 0 else "495"
                lines.append(f"16,{code},{abs(t.amount_cents)},0,{t.reference},{t.reference},Synthetic ")
                lines.append("88," + t.description)
                total += abs(t.amount_cents)
            lines.append(f"49,{total},{len(lines) - account_start + 1}/")
            group_total += total
            account_count += 1
        lines.append(f"98,{group_total},{account_count},{len(lines) - group_start + 1}/")
        file_total += group_total
        group_count += 1
    lines.append(f"99,{file_total},{group_count},{len(lines) + 1}/")
    return "\n".join(lines) + "\n"


def to_mt940(statements: Iterable[Statement]) -> str:
    """Serialize final daily statement bodies without FIN transport headers."""
    lines = []
    for number, s in enumerate(statements, 1):
        def balance(value: int) -> str:
            return f"{'C' if value >= 0 else 'D'}{s.day:%y%m%d}{s.account.currency}{money(abs(value)).replace('.', ',')}"
        lines.extend([f":20:SYN{number:09d}", f":25:{s.account.account}",
                      f":28C:{number}/1", ":60F:" + balance(s.opening_cents)])
        for t in s.transactions:
            amount = money(abs(t.amount_cents)).replace(".", ",")
            lines.append(f":61:{t.value_date:%y%m%d}{t.booking_date:%m%d}{'C' if t.amount_cents >= 0 else 'D'}{amount}NTRF{t.reference}")
            lines.append(":86:" + t.description)
        lines.append(":62F:" + balance(s.closing_cents))
    return "\n".join(lines) + "\n"
