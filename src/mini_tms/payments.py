"""Output-only payment files with segregation of duties and an append-only audit API."""

from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import re
from typing import Callable, Iterable
from xml.etree import ElementTree as ET

from .models import cents, money
from .parsers import _xml
from .statements import _child

PAIN_NS = "urn:iso:std:iso:20022:tech:xsd:pain.001.001.03"
GENESIS = "0" * 64


def _timestamp(value: str) -> None:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})?", value):
        raise ValueError("Invalid ISO timestamp")
    datetime.fromisoformat(value.replace("Z", "+00:00"))


def _digest(value: dict) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return sha256(encoded).hexdigest()


def _text(value: str, maximum: int, label: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError(f"Invalid {label}")


@dataclass(frozen=True)
class Payment:
    payment_id: str
    creditor_name: str
    creditor_account: str
    currency: str
    amount_cents: int
    remittance: str = "Synthetic payment"

    def __post_init__(self) -> None:
        _text(self.payment_id, 35, "payment identifier")
        _text(self.creditor_name, 140, "creditor name")
        _text(self.creditor_account, 34, "creditor account")
        _text(self.remittance, 140, "remittance")
        if not re.fullmatch(r"[A-Z]{3}", self.currency):
            raise ValueError("Invalid currency")
        if type(self.amount_cents) is not int or not 0 < self.amount_cents <= 99999999999999:
            raise ValueError("Payment requires positive integer cents within profile limit")


@dataclass(frozen=True)
class Batch:
    batch_id: str
    maker: str
    debtor_name: str
    debtor_account: str
    execution_date: date
    payments: tuple[Payment, ...]

    def __post_init__(self) -> None:
        _text(self.batch_id, 35, "batch identifier")
        _text(self.maker, 140, "maker")
        _text(self.debtor_name, 140, "debtor name")
        _text(self.debtor_account, 34, "debtor account")
        if type(self.execution_date) is not date or not isinstance(self.payments, tuple) or not self.payments:
            raise ValueError("Batch requires a date and immutable nonempty payment tuple")
        if not all(isinstance(p, Payment) for p in self.payments):
            raise ValueError("Invalid payment object")
        if len({p.payment_id for p in self.payments}) != len(self.payments):
            raise ValueError("Duplicate payment identifier")
        if len({p.currency for p in self.payments}) != 1:
            raise ValueError("This prototype requires a single-currency batch")
        if self.total_cents > 999999999999999999:
            raise ValueError("Batch control sum exceeds profile limit")

    @property
    def total_cents(self) -> int:
        return sum(p.amount_cents for p in self.payments)

    @property
    def payload_hash(self) -> str:
        payload = asdict(self)
        payload["execution_date"] = self.execution_date.isoformat()
        return _digest(payload)


@dataclass(frozen=True)
class AuditEvent:
    sequence: int
    timestamp: str
    action: str
    actor: str
    batch_id: str
    payload_hash: str
    previous_hash: str
    event_hash: str


def verify_audit(events: Iterable[AuditEvent], expected_head: str | None = None,
                 expected_count: int | None = None) -> bool:
    """Verify event edits and reordering; optional checkpoints detect truncation."""
    items = tuple(events)
    previous = GENESIS
    for sequence, event in enumerate(items):
        payload = asdict(event)
        claimed = payload.pop("event_hash")
        if event.sequence != sequence or event.previous_hash != previous or _digest(payload) != claimed:
            return False
        previous = claimed
    return ((expected_head is None or expected_head == previous)
            and (expected_count is None or expected_count == len(items)))


class AuditLog:
    """Expose frozen snapshots and append operations, with no edit/delete API."""

    def __init__(self, clock: Callable[[], str] | None = None) -> None:
        self._events: tuple[AuditEvent, ...] = ()
        self._clock = clock or (lambda: datetime.now(timezone.utc).isoformat())

    @property
    def events(self) -> tuple[AuditEvent, ...]:
        return self._events

    @property
    def head(self) -> str:
        return self._events[-1].event_hash if self._events else GENESIS

    def append(self, action: str, actor: str, batch_id: str, payload_hash: str) -> None:
        if not verify_audit(self._events):
            raise ValueError("Corrupted audit chain")
        payload = dict(sequence=len(self._events), timestamp=self._clock(), action=action,
                       actor=actor, batch_id=batch_id, payload_hash=payload_hash, previous_hash=self.head)
        event = AuditEvent(**payload, event_hash=_digest(payload))
        self._events += (event,)


@dataclass(frozen=True)
class _State:
    batch: Batch
    required_approvers: int
    payload_hash: str
    approvals: tuple[str, ...] = ()
    exported: bool = False


class PaymentWorkflow:
    """Single-process controls with caller-provided role identities, not authentication."""

    def __init__(self, makers: Iterable[str], checkers: Iterable[str],
                 second_approval_threshold_cents: int = 5000000,
                 clock: Callable[[], str] | None = None) -> None:
        if type(second_approval_threshold_cents) is not int or second_approval_threshold_cents <= 0:
            raise ValueError("Positive integer threshold required")
        self._makers = frozenset(makers)
        self._checkers = frozenset(checkers)
        self._threshold = second_approval_threshold_cents
        self._states: dict[str, _State] = {}
        self.audit = AuditLog(clock)

    def create(self, batch: Batch) -> None:
        if batch.maker not in self._makers:
            raise PermissionError("User has no maker role")
        if batch.batch_id in self._states:
            raise ValueError("Batch identifier already used")
        required = 2 if batch.total_cents >= self._threshold else 1
        self.audit.append("CREATE", batch.maker, batch.batch_id, batch.payload_hash)
        self._states[batch.batch_id] = _State(batch, required, batch.payload_hash)

    def approve(self, batch_id: str, checker: str) -> None:
        state = self._states[batch_id]
        if state.batch.payload_hash != state.payload_hash:
            raise ValueError("Batch changed after creation")
        if checker == state.batch.maker:
            raise PermissionError("Maker cannot approve own batch")
        if checker not in self._checkers:
            raise PermissionError("User has no checker role")
        if checker in state.approvals:
            raise ValueError("Approver already counted")
        if state.exported or len(state.approvals) >= state.required_approvers:
            raise ValueError("Batch already approved or exported")
        self.audit.append("APPROVE", checker, batch_id, state.batch.payload_hash)
        self._states[batch_id] = replace(state, approvals=state.approvals + (checker,))

    def status(self, batch_id: str) -> str:
        state = self._states[batch_id]
        return "EXPORTED" if state.exported else "APPROVED" if len(state.approvals) >= state.required_approvers else "PENDING"

    def export(self, batch_id: str, actor: str, created_at: str | None = None) -> str:
        """Generate XML once, validate against the approved payload, then audit its hash."""
        state = self._states[batch_id]
        if actor not in self._makers | self._checkers:
            raise PermissionError("User has no export role")
        if self.status(batch_id) != "APPROVED":
            raise PermissionError("Batch must be approved and not already exported")
        if state.batch.payload_hash != state.payload_hash:
            raise ValueError("Batch changed after creation")
        if not verify_audit(self.audit.events):
            raise ValueError("Corrupted audit chain")
        timestamp = created_at or datetime.now(timezone.utc).isoformat()
        xml = _build_pain001(state.batch, timestamp)
        validate_pain001(xml, state.batch)
        self.audit.append("EXPORT", actor, batch_id, sha256(xml.encode()).hexdigest())
        self._states[batch_id] = replace(state, exported=True)
        return xml


def _build_pain001(batch: Batch, created_at: str) -> str:
    """Serialize the narrow credit-transfer profile used by the workflow."""
    _timestamp(created_at)
    root = ET.Element("Document", xmlns=PAIN_NS)
    message = _child(root, "CstmrCdtTrfInitn")
    header = _child(message, "GrpHdr")
    _child(header, "MsgId", batch.batch_id)
    _child(header, "CreDtTm", created_at)
    _child(header, "NbOfTxs", str(len(batch.payments)))
    _child(header, "CtrlSum", money(batch.total_cents))
    _child(_child(header, "InitgPty"), "Nm", batch.debtor_name)
    info = _child(message, "PmtInf")
    _child(info, "PmtInfId", batch.batch_id)
    _child(info, "PmtMtd", "TRF")
    _child(info, "NbOfTxs", str(len(batch.payments)))
    _child(info, "CtrlSum", money(batch.total_cents))
    _child(info, "ReqdExctnDt", batch.execution_date.isoformat())
    _child(_child(info, "Dbtr"), "Nm", batch.debtor_name)
    _child(_child(_child(_child(info, "DbtrAcct"), "Id"), "Othr"), "Id", batch.debtor_account)
    _child(_child(_child(info, "DbtrAgt"), "FinInstnId"), "BIC", "SYNBUS33XXX")
    for p in batch.payments:
        tx = _child(info, "CdtTrfTxInf")
        _child(_child(tx, "PmtId"), "EndToEndId", p.payment_id)
        _child(_child(tx, "Amt"), "InstdAmt", money(p.amount_cents), Ccy=p.currency)
        _child(_child(tx, "Cdtr"), "Nm", p.creditor_name)
        _child(_child(_child(_child(tx, "CdtrAcct"), "Id"), "Othr"), "Id", p.creditor_account)
        _child(_child(tx, "RmtInf"), "Ustrd", p.remittance)
    ET.indent(root)
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def validate_pain001(text: str, batch: Batch | None = None) -> dict[str, int | str]:
    """Validate our complete narrow structure and controls, not the published XSD."""
    root = _xml(text, PAIN_NS)
    if root.attrib:
        raise ValueError("Unexpected document attribute")
    for node in root.iter():
        if (len(node) and node.text and node.text.strip()) or (node.tail and node.tail.strip()):
            raise ValueError("Unexpected mixed payment content")
    ns = {"p": PAIN_NS}
    def children(node: ET.Element, names: list[str]) -> None:
        actual = [e.tag for e in node]
        if actual != [f"{{{PAIN_NS}}}{name}" for name in names]:
            raise ValueError("Invalid payment element sequence or cardinality")
        if any(e.attrib for e in node if e.tag != f"{{{PAIN_NS}}}InstdAmt"):
            raise ValueError("Unexpected payment attribute")
    children(root, ["CstmrCdtTrfInitn"])
    msg = root[0]
    children(msg, ["GrpHdr", "PmtInf"])
    header, info = msg
    children(header, ["MsgId", "CreDtTm", "NbOfTxs", "CtrlSum", "InitgPty"])
    txs = info.findall("p:CdtTrfTxInf", ns)
    if not txs:
        raise ValueError("No payment transactions")
    children(info, ["PmtInfId", "PmtMtd", "NbOfTxs", "CtrlSum", "ReqdExctnDt",
                    "Dbtr", "DbtrAcct", "DbtrAgt"] + ["CdtTrfTxInf"] * len(txs))
    def leaf(node: ET.Element, tag: str, maximum: int) -> str:
        element = node.find(f"{{{PAIN_NS}}}{tag}")
        if element is None or len(element):
            raise ValueError("Unexpected nested payment element")
        value = element.text or ""
        _text(value, maximum, tag)
        return value
    def name(node: ET.Element) -> str:
        children(node, ["Nm"])
        return leaf(node, "Nm", 140)
    def account(node: ET.Element) -> str:
        children(node, ["Id"])
        children(node[0], ["Othr"])
        children(node[0][0], ["Id"])
        return leaf(node[0][0], "Id", 34)
    msg_id = leaf(header, "MsgId", 35)
    payment_info_id = leaf(info, "PmtInfId", 35)
    _timestamp(leaf(header, "CreDtTm", 40))
    execution = leaf(info, "ReqdExctnDt", 10)
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", execution):
        raise ValueError("Invalid ISO execution date")
    execution_date = date.fromisoformat(execution)
    if leaf(info, "PmtMtd", 3) != "TRF":
        raise ValueError("Only credit transfers supported")
    initiating_name = name(header[-1])
    debtor_name = name(info[5])
    debtor_account = account(info[6])
    children(info[7], ["FinInstnId"])
    children(info[7][0], ["BIC"])
    if not re.fullmatch(r"[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?", leaf(info[7][0], "BIC", 11)):
        raise ValueError("Invalid BIC structure")
    parsed = []
    for tx in txs:
        children(tx, ["PmtId", "Amt", "Cdtr", "CdtrAcct", "RmtInf"])
        children(tx[0], ["EndToEndId"])
        children(tx[1], ["InstdAmt"])
        amt = tx[1][0]
        if set(amt.attrib) != {"Ccy"} or len(amt):
            raise ValueError("Invalid instructed amount")
        children(tx[4], ["Ustrd"])
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", amt.text or ""):
            raise ValueError("Invalid payment amount syntax")
        parsed.append(Payment(leaf(tx[0], "EndToEndId", 35), name(tx[2]), account(tx[3]),
                              amt.attrib["Ccy"], cents(amt.text or ""), leaf(tx[4], "Ustrd", 140)))
    checked = Batch(msg_id, batch.maker if batch else "STRUCTURAL", debtor_name, debtor_account,
                    execution_date, tuple(parsed))
    if initiating_name != debtor_name or payment_info_id != msg_id:
        raise ValueError("Inconsistent payment header identity")
    for node in (header, info):
        count = leaf(node, "NbOfTxs", 15)
        if not re.fullmatch(r"[0-9]{1,15}", count) or int(count) != len(parsed):
            raise ValueError("Transaction count mismatch")
        control_sum = leaf(node, "CtrlSum", 22)
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", control_sum):
            raise ValueError("Invalid control sum syntax")
        if cents(control_sum) != checked.total_cents:
            raise ValueError("Control sum mismatch")
    if batch and checked != batch:
        raise ValueError("XML differs from approved batch")
    return dict(transaction_count=len(parsed), control_sum_cents=checked.total_cents,
                currency=parsed[0].currency, validation="structural_only")
