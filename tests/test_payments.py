"""Segregation of duties, immutable snapshots, audit integrity and XML controls."""

from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
import unittest

from helpers import DAY

from mini_tms.payments import (AuditLog, Batch, Payment, PaymentWorkflow,
                               validate_pain001, verify_audit)


class PaymentTests(unittest.TestCase):
    def setUp(self):
        self.workflow = PaymentWorkflow(["maker"], ["maker", "alice", "bob"], 10000,
                                        clock=lambda: "2025-01-01T12:00:00Z")
        self.small = Batch("B1", "maker", "Synthetic Payer", "SYN001", DAY,
                           (Payment("P1", "Vendor <A>", "SYN002", "USD", 9900),))
        self.large = replace(self.small, batch_id="B2", payments=(replace(self.small.payments[0], amount_cents=10000),))

    def approved_xml(self, batch=None):
        batch = batch or self.small
        self.workflow.create(batch)
        self.workflow.approve(batch.batch_id, "alice")
        if batch.total_cents >= 10000:
            self.workflow.approve(batch.batch_id, "bob")
        return self.workflow.export(batch.batch_id, "maker", "2025-01-01T12:00:00Z")

    def test_maker_cannot_check_own_batch(self):
        self.workflow.create(self.small)
        with self.assertRaises(PermissionError):
            self.workflow.approve("B1", "maker")
        self.assertEqual(self.workflow.status("B1"), "PENDING")
        self.assertEqual(len(self.workflow.audit.events), 1)

    def test_xml_amount_exponent_rejected(self):
        xml = self.approved_xml()
        with self.assertRaises(ValueError):
            validate_pain001(xml.replace(">99.00<", ">9.9E1<"))

    def test_xml_compact_execution_date_rejected(self):
        xml = self.approved_xml()
        with self.assertRaises(ValueError):
            validate_pain001(xml.replace(">2025-01-01<", ">20250101<"))

    def test_xml_timestamp_requires_time(self):
        xml = self.approved_xml()
        with self.assertRaises(ValueError):
            validate_pain001(xml.replace("2025-01-01T12:00:00Z", "2025-01-01"))

    def test_xml_mixed_content_rejected(self):
        xml = self.approved_xml()
        with self.assertRaises(ValueError):
            validate_pain001(xml.replace("<PmtInf>", "<PmtInf>unexpected text"))

    def test_xml_text_whitespace_round_trip_preserves_approved_payload(self):
        payment = replace(self.small.payments[0], creditor_name=" Vendor A ")
        batch = replace(self.small, debtor_name=" Synthetic Payer ", payments=(payment,))
        self.assertEqual(validate_pain001(self.approved_xml(batch), batch)["control_sum_cents"], 9900)

    def test_failed_export_leaves_approval_and_audit_unchanged(self):
        self.workflow.create(self.small)
        self.workflow.approve("B1", "alice")
        events = self.workflow.audit.events
        with self.assertRaises(ValueError):
            self.workflow.export("B1", "maker", "invalid")
        self.assertEqual(self.workflow.status("B1"), "APPROVED")
        self.assertEqual(self.workflow.audit.events, events)

    def test_unauthorized_maker_rejected(self):
        with self.assertRaises(PermissionError):
            self.workflow.create(replace(self.small, maker="outsider"))

    def test_unauthorized_checker_rejected(self):
        self.workflow.create(self.small)
        with self.assertRaises(PermissionError):
            self.workflow.approve("B1", "outsider")

    def test_duplicate_batch_identifier_rejected(self):
        self.workflow.create(self.small)
        with self.assertRaises(ValueError):
            self.workflow.create(self.small)

    def test_small_batch_one_checker(self):
        self.workflow.create(self.small)
        self.workflow.approve("B1", "alice")
        self.assertEqual(self.workflow.status("B1"), "APPROVED")

    def test_threshold_inclusive_needs_second_checker(self):
        self.workflow.create(self.large)
        self.workflow.approve("B2", "alice")
        self.assertEqual(self.workflow.status("B2"), "PENDING")
        with self.assertRaises(PermissionError):
            self.workflow.export("B2", "maker")
        self.workflow.approve("B2", "bob")
        self.assertEqual(self.workflow.status("B2"), "APPROVED")

    def test_threshold_uses_total_batch_not_individual_payment(self):
        batch = replace(self.small, payments=(replace(self.small.payments[0], amount_cents=6000),
                                              Payment("P2", "Vendor B", "SYN003", "USD", 6000)))
        self.workflow.create(batch)
        self.workflow.approve("B1", "alice")
        self.assertEqual(self.workflow.status("B1"), "PENDING")

    def test_same_second_checker_not_counted_twice(self):
        self.workflow.create(self.large)
        self.workflow.approve("B2", "alice")
        with self.assertRaises(ValueError):
            self.workflow.approve("B2", "alice")

    def test_unapproved_export_rejected(self):
        self.workflow.create(self.small)
        with self.assertRaises(PermissionError):
            self.workflow.export("B1", "maker")

    def test_export_actor_requires_role(self):
        self.workflow.create(self.small)
        self.workflow.approve("B1", "alice")
        with self.assertRaises(PermissionError):
            self.workflow.export("B1", "outsider")

    def test_second_export_rejected(self):
        self.approved_xml()
        self.assertEqual(self.workflow.status("B1"), "EXPORTED")
        with self.assertRaises(PermissionError):
            self.workflow.export("B1", "maker")

    def test_audit_hash_chain_valid(self):
        self.approved_xml(self.large)
        self.assertTrue(verify_audit(self.workflow.audit.events, self.workflow.audit.head, 4))
        self.assertEqual([e.action for e in self.workflow.audit.events], ["CREATE", "APPROVE", "APPROVE", "EXPORT"])

    def test_audit_edit_detected(self):
        self.approved_xml()
        events = list(self.workflow.audit.events)
        events[1] = replace(events[1], actor="mallory")
        self.assertFalse(verify_audit(events))

    def test_audit_reordering_detected(self):
        self.approved_xml()
        events = list(self.workflow.audit.events)
        events[0], events[1] = events[1], events[0]
        self.assertFalse(verify_audit(events))

    def test_audit_middle_deletion_detected(self):
        self.approved_xml()
        events = self.workflow.audit.events
        self.assertFalse(verify_audit((events[0], events[2])))

    def test_audit_truncation_detected_with_checkpoint(self):
        self.approved_xml()
        events = self.workflow.audit.events
        self.assertFalse(verify_audit(events[:-1], self.workflow.audit.head, len(events)))

    def test_audit_snapshots_frozen(self):
        self.workflow.create(self.small)
        with self.assertRaises(FrozenInstanceError):
            self.workflow.audit.events[0].actor = "mallory"
        self.assertIsInstance(self.workflow.audit.events, tuple)

    def test_payment_and_batch_frozen(self):
        with self.assertRaises(FrozenInstanceError):
            self.small.payments[0].amount_cents = 1
        with self.assertRaises(FrozenInstanceError):
            self.small.maker = "alice"

    def test_changed_payload_after_approval_blocks_export(self):
        self.workflow.create(self.small)
        self.workflow.approve("B1", "alice")
        object.__setattr__(self.small, "debtor_account", "MUTATED")
        with self.assertRaisesRegex(ValueError, "Batch changed"):
            self.workflow.export("B1", "maker")

    def test_export_audit_binds_xml_hash(self):
        xml = self.approved_xml()
        self.assertEqual(self.workflow.audit.events[-1].payload_hash, sha256(xml.encode()).hexdigest())

    def test_xml_control_sum_and_count_equal_batch(self):
        batch = replace(self.small, payments=(self.small.payments[0], Payment("P2", "Vendor B", "SYN003", "USD", 123)))
        xml = self.approved_xml(batch)
        controls = validate_pain001(xml, batch)
        self.assertEqual(controls["transaction_count"], 2)
        self.assertEqual(controls["control_sum_cents"], 10023)
        self.assertEqual(controls["validation"], "structural_only")

    def test_xml_escapes_beneficiary_text(self):
        xml = self.approved_xml()
        self.assertIn("Vendor &lt;A&gt;", xml)
        validate_pain001(xml, self.small)

    def test_xml_control_sum_tampering_detected(self):
        xml = self.approved_xml().replace("<CtrlSum>99.00</CtrlSum>", "<CtrlSum>98.00</CtrlSum>", 1)
        with self.assertRaisesRegex(ValueError, "Control sum"):
            validate_pain001(xml)

    def test_xml_transaction_count_tampering_detected(self):
        xml = self.approved_xml().replace("<NbOfTxs>1</NbOfTxs>", "<NbOfTxs>2</NbOfTxs>", 1)
        with self.assertRaisesRegex(ValueError, "Transaction count"):
            validate_pain001(xml)

    def test_xml_matches_approved_recipient(self):
        xml = self.approved_xml().replace("SYN002", "SYN999")
        with self.assertRaisesRegex(ValueError, "approved batch"):
            validate_pain001(xml, self.small)

    def test_xml_unknown_element_rejected(self):
        xml = self.approved_xml().replace("</GrpHdr>", "<Unknown>1</Unknown></GrpHdr>")
        with self.assertRaises(ValueError):
            validate_pain001(xml)

    def test_xml_missing_required_structure(self):
        xml = self.approved_xml().replace("<PmtMtd>TRF</PmtMtd>", "")
        with self.assertRaises(ValueError):
            validate_pain001(xml)

    def test_xml_namespace_rejected(self):
        xml = self.approved_xml().replace("pain.001.001.03", "pain.001.001.09")
        with self.assertRaises(ValueError):
            validate_pain001(xml)

    def test_xml_nested_scalar_rejected(self):
        xml = self.approved_xml().replace("<MsgId>B1</MsgId>", "<MsgId>B1<Extra>1</Extra></MsgId>")
        with self.assertRaises(ValueError):
            validate_pain001(xml)

    def test_zero_negative_and_fractional_payment_cents_rejected(self):
        for amount in (0, -1, 1.5, True):
            with self.assertRaises(ValueError):
                replace(self.small.payments[0], amount_cents=amount)

    def test_duplicate_payment_identifiers_rejected(self):
        with self.assertRaises(ValueError):
            replace(self.small, payments=(self.small.payments[0], self.small.payments[0]))

    def test_empty_batch_rejected(self):
        with self.assertRaises(ValueError):
            replace(self.small, payments=())

    def test_mixed_currency_batch_rejected(self):
        with self.assertRaises(ValueError):
            replace(self.small, payments=(self.small.payments[0], Payment("P2", "Vendor B", "SYN003", "EUR", 100)))

    def test_mutable_payment_list_rejected(self):
        with self.assertRaises(ValueError):
            replace(self.small, payments=list(self.small.payments))
