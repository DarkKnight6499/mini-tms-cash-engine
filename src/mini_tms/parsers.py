"""Strict parsers for documented daily statement subsets, not bank universality."""

from datetime import date, datetime
import re
from typing import Iterable, Mapping
from xml.etree import ElementTree as ET

from .models import Account, ParseResult, Statement, Transaction, cents, normalize

CAMT_NS = "urn:iso:std:iso:20022:tech:xsd:camt.053.001.02"


def _account(identifier: str, currency: str, accounts: Mapping[str, Account]) -> Account:
    if identifier not in accounts:
        raise ValueError(f"Unknown account {identifier}")
    account = accounts[identifier]
    if account.currency != currency:
        raise ValueError(f"Currency mismatch for {identifier}")
    return account


def _xml(text: str, namespace: str) -> ET.Element:
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("XML declarations of entities are unsupported")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError("Malformed XML") from exc
    if root.tag != f"{{{namespace}}}Document":
        raise ValueError("Unexpected XML namespace or root")
    return root


def _required(node: ET.Element, path: str, ns: str = CAMT_NS) -> str:
    element = node.find("/".join(f"{{{ns}}}{p}" for p in path.split("/")))
    if element is None or not element.text or not element.text.strip():
        raise ValueError(f"Missing XML element {path}")
    return element.text.strip()


def _date_choice(node: ET.Element, tag: str) -> date:
    for choice in ("Dt", "DtTm"):
        value = node.findtext(f"{{{CAMT_NS}}}{tag}/{{{CAMT_NS}}}{choice}")
        if value:
            if choice == "DtTm":
                return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
            return date.fromisoformat(value)
    raise ValueError(f"Missing {tag} date")


def _signed(value: str, direction: str) -> int:
    amount = cents(value)
    if amount < 0 or direction not in ("CRDT", "DBIT"):
        raise ValueError("Invalid amount or credit/debit indicator")
    return amount if direction == "CRDT" else -amount


def parse_camt053(text: str, accounts: Mapping[str, Account],
                  expected_dates: Iterable[date] | None = None) -> ParseResult:
    """Parse booked, single-detail entries with OPBD and CLBD balances."""
    root = _xml(text, CAMT_NS)
    ns = {"c": CAMT_NS}
    message = root.find("c:BkToCstmrStmt", ns)
    if message is None:
        raise ValueError("Missing BkToCstmrStmt")
    statements = []
    for s in message.findall("c:Stmt", ns):
        identifier = s.findtext("c:Acct/c:Id/c:Othr/c:Id", namespaces=ns)
        identifier = identifier or s.findtext("c:Acct/c:Id/c:IBAN", namespaces=ns)
        currency = _required(s, "Acct/Ccy")
        a = _account(identifier or "", currency, accounts)
        balances = {}
        for b in s.findall("c:Bal", ns):
            code = _required(b, "Tp/CdOrPrtry/Cd")
            if code in ("OPBD", "CLBD"):
                if code in balances:
                    raise ValueError("Duplicate booked balance")
                amount_node = b.find("c:Amt", ns)
                if amount_node is None or amount_node.attrib.get("Ccy") != currency:
                    raise ValueError("Balance currency mismatch")
                balances[code] = (_signed(_required(b, "Amt"), _required(b, "CdtDbtInd")),
                                  _date_choice(b, "Dt"))
        if "OPBD" not in balances or "CLBD" not in balances:
            raise ValueError("Opening and closing booked balances required")
        txs = []
        for n in s.findall("c:Ntry", ns):
            if _required(n, "Sts") != "BOOK":
                raise ValueError("Only booked entries supported")
            amount_node = n.find("c:Amt", ns)
            if amount_node is None or amount_node.attrib.get("Ccy") != currency:
                raise ValueError("Entry currency mismatch")
            if len(n.findall("c:NtryDtls/c:TxDtls", ns)) > 1:
                raise ValueError("Batched entry details unsupported")
            ref = n.findtext("c:NtryDtls/c:TxDtls/c:Refs/c:AcctSvcrRef", namespaces=ns)
            ref = ref or n.findtext("c:NtryRef", default="", namespaces=ns)
            code = n.findtext("c:BkTxCd/c:Prtry/c:Cd", namespaces=ns)
            code = code or n.findtext("c:BkTxCd/c:Domn/c:Cd", namespaces=ns)
            if not code:
                raise ValueError("Missing bank transaction code")
            txs.append(Transaction(_date_choice(n, "ValDt"), _date_choice(n, "BookgDt"),
                                   _signed(_required(n, "Amt"), _required(n, "CdtDbtInd")),
                                   code, ref,
                                   n.findtext("c:AddtlNtryInf", default="", namespaces=ns)))
        statements.append(Statement(_required(s, "Id"), a, balances["CLBD"][1],
                                    balances["OPBD"][0], balances["CLBD"][0], tuple(txs)))
    if not statements:
        raise ValueError("No statements")
    return normalize(statements, "camt.053", expected_dates=expected_dates, expected_accounts=accounts)


def _short_date(value: str) -> date:
    if not re.fullmatch(r"\d{6}", value):
        raise ValueError("Expected YYMMDD date")
    return date(2000 + int(value[:2]), int(value[2:4]), int(value[4:6]))


def parse_bai2(text: str, accounts: Mapping[str, Account],
               expected_dates: Iterable[date] | None = None) -> ParseResult:
    """Parse BAI2 envelopes, continuation records and integer-cent controls."""
    lines = [line.rstrip("\r") for line in text.splitlines() if line.strip()]
    logical: list[tuple[str, int]] = []
    for line in lines:
        if not re.match(r"\d{2},", line):
            raise ValueError("Malformed BAI2 record")
        if line.startswith("88,"):
            if not logical:
                raise ValueError("Orphan continuation")
            prior, count = logical[-1]
            parts = prior.split(",", 6)
            if prior.startswith("16,") and len(parts) == 7 and parts[6] != "/":
                prior += line[3:]
            else:
                if not prior.endswith("/"):
                    raise ValueError("Nontext continuation requires a slash delimiter")
                prior = prior[:-1] + "," + line[3:]
            logical[-1] = (prior, count + 1)
        else:
            logical.append((line, 1))
    statements: list[Statement] = []
    findings: list[dict] = []
    state = "start"
    file_total = group_total = account_total = 0
    file_records = group_records = account_records = 0
    group_count = account_count = 0
    a: Account | None = None
    day = date.min
    sid = ""
    txs: list[Transaction] = []
    opening = closing = 0

    def check(actual: int, declared: str, code: str) -> None:
        if actual != int(declared):
            findings.append(dict(format="BAI2", statement_id=sid if code.startswith("ACCOUNT") else "*",
                                 account=a.account if a and code.startswith("ACCOUNT") else "*",
                                 date=day.isoformat(), code=code,
                                 detail=f"Calculated {actual}, declared {declared}"))

    for record, count in logical:
        code = record[:2]
        if code != "16" and not record.endswith("/"):
            raise ValueError("Missing BAI2 record terminator")
        if code == "16":
            f = record.split(",", 6)
            if len(f) == 7 and f[6] == "/":
                f[6] = ""
        else:
            f = record[:-1].split(",")
        lengths = {"01": 9, "02": 8, "49": 3, "98": 4, "99": 4}
        if code in lengths and len(f) != lengths[code]:
            raise ValueError(f"Invalid BAI2 field count for {code}")
        file_records += count
        if code == "01" and state == "start":
            if len(f) != 9 or f[-1] != "2":
                raise ValueError("BAI2 version 2 header required")
            state = "file"
        elif code == "02" and state == "file":
            if len(f) != 8 or f[3] != "1":
                raise ValueError("Only BAI2 update groups supported")
            if f[7] not in ("2", "4"):
                raise ValueError("Only final daily BAI2 data supported")
            bank = f[2]
            day = _short_date(f[4])
            group_currency = f[6]
            group_count += 1
            group_total = account_count = 0
            group_records = count
            state = "group"
        elif code == "03" and state == "group":
            if len(f) < 3:
                raise ValueError("Incomplete BAI2 account record")
            a = _account(f[1], f[2] or group_currency, accounts)
            if a.bank != bank:
                raise ValueError("BAI2 originator bank mismatch")
            sid = f"{a.account}-{day.isoformat()}"
            data = f[3:]
            if len(data) % 4:
                raise ValueError("Only defaulted funds distributions supported")
            status: dict[str, int] = {}
            account_total = 0
            for i in range(0, len(data), 4):
                typ, amount, item_count, funds = data[i:i + 4]
                if not re.fullmatch(r"[0-9]{3}", typ):
                    raise ValueError("Invalid BAI2 type code")
                if not 1 <= int(typ) <= 99 and int(amount or 0) < 0:
                    raise ValueError("BAI2 summary amounts must be unsigned")
                if int(typ) < 100 and (item_count or funds):
                    raise ValueError("BAI2 status fields must default item count and funds")
                if item_count and not re.fullmatch(r"[0-9]+", item_count):
                    raise ValueError("Invalid BAI2 item count")
                if funds not in ("", "0"):
                    raise ValueError("Only defaulted or immediate funds type supported")
                if typ in status:
                    raise ValueError("Repeated BAI2 status code")
                status[typ] = int(amount or 0)
                account_total += status[typ]
            if "010" not in status or "015" not in status:
                raise ValueError("Opening and closing ledger status required")
            opening, closing = status["010"], status["015"]
            account_count += 1
            account_records = count
            group_records += count
            txs = []
            state = "account"
        elif code == "16" and state == "account":
            if len(f) != 7 or f[3] not in ("", "0"):
                raise ValueError("Unsupported transaction funds distribution")
            typ, amount = int(f[1]), int(f[2])
            if amount < 0:
                raise ValueError("BAI2 detail amounts must be unsigned")
            if 101 <= typ <= 399 or 920 <= typ <= 959:
                signed = amount
            elif 401 <= typ <= 699 or 960 <= typ <= 999:
                signed = -amount
            else:
                raise ValueError("Unsupported BAI2 monetary detail code")
            account_total += amount
            txs.append(Transaction(day, day, signed, f[1], f[4] or f[5], f[6]))
            account_records += count
            group_records += count
        elif code == "49" and state == "account":
            account_records += count
            group_records += count
            check(account_total, f[1], "ACCOUNT_CONTROL_TOTAL")
            check(account_records, f[2], "ACCOUNT_RECORD_COUNT")
            group_total += int(f[1])
            statements.append(Statement(sid, a, day, opening, closing, tuple(txs)))
            state = "group"
        elif code == "98" and state == "group":
            group_records += count
            check(group_total, f[1], "GROUP_CONTROL_TOTAL")
            check(account_count, f[2], "GROUP_ACCOUNT_COUNT")
            check(group_records, f[3], "GROUP_RECORD_COUNT")
            file_total += int(f[1])
            state = "file"
        elif code == "99" and state == "file":
            check(file_total, f[1], "FILE_CONTROL_TOTAL")
            check(group_count, f[2], "FILE_GROUP_COUNT")
            check(file_records, f[3], "FILE_RECORD_COUNT")
            state = "done"
        else:
            raise ValueError(f"Unexpected BAI2 record {code} in {state}")
    if state != "done" or not statements:
        raise ValueError("Incomplete BAI2 file")
    result = normalize(statements, "BAI2", findings, expected_dates, accounts)
    # An invalid envelope makes every enclosed balance untrusted.
    if any(i["account"] == "*" for i in findings):
        result.statements["valid"] = False
        result.clean_ledger = result.ledger.iloc[0:0].copy()
    return result


def _mt_balance(value: str) -> tuple[int, date, str]:
    m = re.fullmatch(r"([CD])(\d{6})([A-Z]{3})(\d+,\d{0,2})", value)
    if not m:
        raise ValueError("Malformed MT940 balance")
    direction, day, currency, amount = m.groups()
    return cents(amount.replace(",", ".")) * (1 if direction == "C" else -1), _short_date(day), currency


def parse_mt940(text: str, accounts: Mapping[str, Account],
                expected_dates: Iterable[date] | None = None) -> ParseResult:
    """Parse bare MT940 bodies with final opening and closing balances."""
    tags: list[tuple[str, str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        m = re.match(r"^:(\d{2}[A-Z]?):(.*)$", line)
        if m:
            tags.append((m[1], m[2].strip()))
        elif tags and tags[-1][0] in ("86", "61"):
            tag, value = tags[-1]
            tags[-1] = (tag, value + " " + line.strip())
        else:
            raise ValueError("Unsupported MT940 wrapper or continuation")
    statements = []
    block: dict[str, str] = {}
    txs: list[Transaction] = []
    opened = False
    closed = False
    for tag, value in tags:
        if tag == "20":
            if block and not closed:
                raise ValueError("Missing MT940 closing balance")
            block = {tag: value}
            txs = []
            opened = closed = False
        elif tag in ("25", "28C", "60F"):
            if not block or tag in block or opened or closed:
                raise ValueError("MT940 header order or duplicate field")
            block[tag] = value
            if tag == "60F":
                if not all(t in block for t in ("20", "25", "28C")):
                    raise ValueError("Incomplete MT940 header")
                opening, opening_day, currency = _mt_balance(value)
                a = _account(block["25"].split("/")[-1], currency, accounts)
                opened = True
        elif tag == "61" and opened and not closed:
            m = re.fullmatch(r"(\d{6})(\d{4})?(RC|RD|C|D)[A-Z]?(\d+,\d{0,2})([NSF][A-Z0-9]{3})([^/]{1,16})(?://(.*))?", value)
            if not m:
                raise ValueError("Malformed MT940 statement line")
            vd, bd, direction, amount, typ, ref, extra = m.groups()
            value_day = _short_date(vd)
            booking_day = value_day
            if bd:
                candidates = []
                for year in (value_day.year - 1, value_day.year, value_day.year + 1):
                    try:
                        candidates.append(date(year, int(bd[:2]), int(bd[2:])))
                    except ValueError:
                        continue
                if not candidates:
                    raise ValueError("Invalid MT940 booking date")
                booking_day = min(candidates, key=lambda d: abs((d - value_day).days))
            signed = cents(amount.replace(",", ".")) * (1 if direction in ("C", "RD") else -1)
            txs.append(Transaction(value_day, booking_day, signed, typ, ref.strip()))
        elif tag == "86" and opened and not closed and txs:
            t = txs[-1]
            txs[-1] = Transaction(t.value_date, t.booking_date, t.amount_cents,
                                   t.type_code, t.reference, value)
        elif tag == "62F" and opened and not closed:
            closing, day, closing_currency = _mt_balance(value)
            if closing_currency != currency:
                raise ValueError("MT940 balance currency mismatch")
            sid = f"{a.account}-{day.isoformat()}"
            statements.append(Statement(sid, a, day, opening, closing, tuple(txs)))
            closed = True
        else:
            raise ValueError(f"Unexpected or unsupported MT940 tag {tag}")
    if not statements or not closed:
        raise ValueError("Incomplete MT940 file")
    return normalize(statements, "MT940", expected_dates=expected_dates, expected_accounts=accounts)
