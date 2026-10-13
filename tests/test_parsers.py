"""Known-result parser fixtures, independent control arithmetic and failure modes."""

from dataclasses import replace
from datetime import date, timedelta
import unittest

from helpers import ACCOUNT, ACCOUNTS, DAY, FIXTURES, tiny_statement

import pandas as pd

from mini_tms.models import Account, Statement, Transaction, cents, normalize
from mini_tms.parsers import parse_bai2, parse_camt053, parse_mt940
from mini_tms.statements import to_bai2, to_camt053, to_mt940
from mini_tms.synthetic import DAYS, START, generate


class MinimalParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.camt = (FIXTURES / "minimal_camt.xml").read_text()
        self.bai = (FIXTURES / "minimal.bai").read_text()
        self.mt = (FIXTURES / "minimal.sta").read_text()

    def assert_minimal(self, result) -> None:
        self.assertEqual(result.ledger.amount.tolist(), [50.0, -25.0])
        self.assertEqual(result.ledger.balance.tolist(), [150.0, 125.0])
        self.assertEqual(result.statements.closing.tolist(), [125.0])
        self.assertTrue(result.exceptions.empty)
        self.assertEqual(result.ledger.account.tolist(), ["001", "001"])

    def test_camt_known_amounts(self):
        self.assert_minimal(parse_camt053(self.camt, ACCOUNTS))

    def test_bai_known_amounts(self):
        self.assert_minimal(parse_bai2(self.bai, ACCOUNTS))

    def test_mt_known_amounts(self):
        self.assert_minimal(parse_mt940(self.mt, ACCOUNTS))

    def test_bai_text_continuation(self):
        self.assertEqual(parse_bai2(self.bai, ACCOUNTS).ledger.description.iloc[0], "Customer receipt")

    def test_mt_text_continuation(self):
        self.assertEqual(parse_mt940(self.mt, ACCOUNTS).ledger.description.iloc[1], "Vendor payment")

    def test_camt_namespace_required(self):
        with self.assertRaises(ValueError):
            parse_camt053(self.camt.replace("camt.053.001.02", "camt.053.001.08"), ACCOUNTS)

    def test_camt_negative_unsigned_amount_rejected(self):
        with self.assertRaises(ValueError):
            parse_camt053(self.camt.replace(">50.00<", ">-50.00<"), ACCOUNTS)

    def test_camt_invalid_indicator_rejected(self):
        with self.assertRaises(ValueError):
            parse_camt053(self.camt.replace("DBIT", "OTHER"), ACCOUNTS)

    def test_camt_nonbooked_rejected(self):
        with self.assertRaises(ValueError):
            parse_camt053(self.camt.replace("BOOK", "PDNG"), ACCOUNTS)

    def test_camt_missing_balance_rejected(self):
        with self.assertRaises(ValueError):
            parse_camt053(self.camt.replace("OPBD", "ITBD"), ACCOUNTS)

    def test_camt_currency_mismatch(self):
        with self.assertRaises(ValueError):
            parse_camt053(self.camt.replace('Ccy="EUR">50', 'Ccy="USD">50'), ACCOUNTS)

    def test_camt_iban_account_choice(self):
        text = self.camt.replace("<Othr><Id>001</Id></Othr>", "<IBAN>001</IBAN>")
        self.assert_minimal(parse_camt053(text, ACCOUNTS))

    def test_camt_datetime_choice(self):
        text = self.camt.replace("<ValDt><Dt>2025-01-01</Dt></ValDt>", "<ValDt><DtTm>2025-01-01T12:00:00Z</DtTm></ValDt>")
        self.assertEqual(parse_camt053(text, ACCOUNTS).ledger.value_date.iloc[0], "2025-01-01")

    def test_xml_entities_rejected(self):
        with self.assertRaises(ValueError):
            parse_camt053('<!DOCTYPE x [<!ENTITY a "value">]>' + self.camt, ACCOUNTS)

    def test_unknown_account_all_parsers(self):
        for parser, text in ((parse_camt053, self.camt), (parse_bai2, self.bai), (parse_mt940, self.mt)):
            with self.subTest(parser=parser.__name__), self.assertRaises(ValueError):
                parser(text, {})

    def test_bai_account_control(self):
        result = parse_bai2(self.bai.replace("49,30000", "49,29999"), ACCOUNTS)
        self.assertIn("ACCOUNT_CONTROL_TOTAL", result.exceptions.code.tolist())
        self.assertTrue(result.clean_ledger.empty)
        self.assertFalse(result.statements.valid.any())

    def test_bai_account_record_count(self):
        result = parse_bai2(self.bai.replace("49,30000,5", "49,30000,4"), ACCOUNTS)
        self.assertIn("ACCOUNT_RECORD_COUNT", result.exceptions.code.tolist())

    def test_bai_declared_trailers_reconcile_to_parent_controls(self):
        account = parse_bai2(self.bai.replace("49,30000", "49,29999"), ACCOUNTS)
        self.assertIn("GROUP_CONTROL_TOTAL", account.exceptions.code.tolist())
        group = parse_bai2(self.bai.replace("98,30000", "98,29999"), ACCOUNTS)
        self.assertIn("FILE_CONTROL_TOTAL", group.exceptions.code.tolist())
        self.assertFalse(account.statements.valid.any())
        self.assertFalse(group.statements.valid.any())

    def test_bai_group_controls_quarantine(self):
        result = parse_bai2(self.bai.replace("98,30000,1,7", "98,29999,2,6"), ACCOUNTS)
        self.assertTrue({"GROUP_CONTROL_TOTAL", "GROUP_ACCOUNT_COUNT", "GROUP_RECORD_COUNT"}.issubset(set(result.exceptions.code)))
        self.assertFalse(result.statements.valid.any())

    def test_bai_file_controls_quarantine(self):
        result = parse_bai2(self.bai.replace("99,30000,1,9", "99,29999,2,8"), ACCOUNTS)
        self.assertTrue({"FILE_CONTROL_TOTAL", "FILE_GROUP_COUNT", "FILE_RECORD_COUNT"}.issubset(set(result.exceptions.code)))
        self.assertTrue(result.clean_ledger.empty)

    def test_bai_unsigned_detail(self):
        with self.assertRaises(ValueError):
            parse_bai2(self.bai.replace("195,5000", "195,-5000"), ACCOUNTS)

    def test_bai_unknown_detail(self):
        with self.assertRaises(ValueError):
            parse_bai2(self.bai.replace("16,195", "16,890"), ACCOUNTS)

    def test_bai_missing_trailer(self):
        with self.assertRaises(ValueError):
            parse_bai2(self.bai[:self.bai.index("99,")], ACCOUNTS)

    def test_bai_interim_data_rejected(self):
        with self.assertRaises(ValueError):
            parse_bai2(self.bai.replace("2359,EUR,2/", "2359,EUR,1/"), ACCOUNTS)

    def test_bai_deletion_group_rejected(self):
        with self.assertRaises(ValueError):
            parse_bai2(self.bai.replace("BANK,1,250101", "BANK,2,250101"), ACCOUNTS)

    def test_bai_orphan_continuation(self):
        with self.assertRaises(ValueError):
            parse_bai2("88,orphan\n" + self.bai, ACCOUNTS)

    def test_bai_summary_continuation(self):
        text = self.bai.replace("010,10000,,,015,12500,,/", "010,10000,,/\n88,015,12500,,/")
        text = text.replace("49,30000,5", "49,30000,6").replace("98,30000,1,7", "98,30000,1,8").replace("99,30000,1,9", "99,30000,1,10")
        self.assert_minimal(parse_bai2(text, ACCOUNTS))

    def test_bai_nontext_continuation_requires_slash(self):
        text = self.bai.replace("010,10000,,,015,12500,,/", "010,10000,,,\n88,015,12500,,/")
        with self.assertRaisesRegex(ValueError, "slash delimiter"):
            parse_bai2(text, ACCOUNTS)

    def test_bai_detail_fields_continue_after_slash(self):
        text = self.bai.replace("16,495,2500,0,R2,R2,Payment", "16,495,2500,0/\n88,R2,R2,Payment")
        text = text.replace("49,30000,5", "49,30000,6").replace("98,30000,1,7", "98,30000,1,8").replace("99,30000,1,9", "99,30000,1,10")
        self.assert_minimal(parse_bai2(text, ACCOUNTS))
        self.assertEqual(parse_bai2(text, ACCOUNTS).ledger.reference.tolist(), ["R1", "R2"])

    def test_bai_slashes_inside_text_preserved(self):
        text = self.bai.replace("Customer\n88, receipt", "Customer/\n88, receipt/")
        self.assertEqual(parse_bai2(text, ACCOUNTS).ledger.description.iloc[0], "Customer/ receipt/")

    def test_bai_status_funds_rejected(self):
        with self.assertRaises(ValueError):
            parse_bai2(self.bai.replace("010,10000,,,", "010,10000,,0,"), ACCOUNTS)

    def test_bai_negative_summary_rejected(self):
        text = self.bai.replace("015,12500,,/", "015,12500,,,100,-5000,,/")
        with self.assertRaises(ValueError):
            parse_bai2(text, ACCOUNTS)

    def test_bai_malformed_trailers_raise_value_error(self):
        for record in ("49,30000,5/", "98,30000,1,7/", "99,30000,1,9/"):
            with self.subTest(record=record), self.assertRaises(ValueError):
                parse_bai2(self.bai.replace(record, record[:2] + ",/"), ACCOUNTS)

    def test_mt_reversal_credit_is_debit(self):
        text = self.mt.replace("0101D25", "0101RC25")
        self.assert_minimal(parse_mt940(text, ACCOUNTS))

    def test_mt_reversal_debit_is_credit(self):
        self.assert_minimal(parse_mt940(self.mt.replace("0101C50", "0101RD50"), ACCOUNTS))

    def test_mt_optional_funds_code(self):
        self.assert_minimal(parse_mt940(self.mt.replace("0101C50", "0101CR50"), ACCOUNTS))

    def test_mt_no_booking_date(self):
        self.assert_minimal(parse_mt940(self.mt.replace("2501010101", "250101"), ACCOUNTS))

    def test_mt_year_boundary(self):
        text = self.mt.replace("2501010101C50", "2501011231C50")
        self.assertEqual(parse_mt940(text, ACCOUNTS).ledger.booking_date.iloc[0], "2024-12-31")

    def test_mt_missing_header(self):
        with self.assertRaises(ValueError):
            parse_mt940(self.mt.replace(":28C:1/1\n", ""), ACCOUNTS)

    def test_mt_missing_closing(self):
        with self.assertRaises(ValueError):
            parse_mt940(self.mt[:self.mt.index(":62F:")], ACCOUNTS)

    def test_money_precision_and_nonfinite(self):
        self.assertEqual(cents("12.30"), 1230)
        for value in ("0.001", "nan", "inf", "bad"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                cents(value)


class RoundTripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = generate()
        cls.days = [START + timedelta(days=i) for i in range(DAYS)]

    def test_all_formats_reconcile_synthetic_totals(self):
        reference = normalize(self.data.statements, "source")
        columns = ["account", "value_date", "booking_date", "amount", "reference", "balance"]
        expected = reference.ledger[columns].sort_values(["account", "reference"]).reset_index(drop=True)
        for serializer, parser in ((to_camt053, parse_camt053), (to_bai2, parse_bai2), (to_mt940, parse_mt940)):
            with self.subTest(parser=parser.__name__):
                result = parser(serializer(self.data.statements), self.data.accounts, self.days)
                actual = result.ledger[columns].sort_values(["account", "reference"]).reset_index(drop=True)
                pd.testing.assert_frame_equal(actual, expected)
                self.assertTrue(result.exceptions.empty)
                self.assertEqual(len(result.statements), len(self.data.accounts) * DAYS)
                self.assertEqual(len(result.ledger), 2 * len(self.data.accounts) * DAYS)

    def test_defects_detected_in_each_format(self):
        for serializer, parser in ((to_camt053, parse_camt053), (to_bai2, parse_bai2), (to_mt940, parse_mt940)):
            with self.subTest(parser=parser.__name__):
                result = parser(serializer(self.data.defective_statements), self.data.accounts, self.days)
                self.assertTrue({"DUPLICATE_TRANSACTION", "OUT_OF_BALANCE", "MISSING_DAY", "BALANCE_DISCONTINUITY"}.issubset(set(result.exceptions.code)))
                self.assertEqual((~result.statements.valid).sum(), 3)
                self.assertEqual((result.exceptions.code == "MISSING_DAY").sum(), 1)

    def test_zero_activity_statement_survives_all_formats(self):
        statement = replace(tiny_statement(), transactions=(), closing_cents=10000)
        for serializer, parser in ((to_camt053, parse_camt053), (to_bai2, parse_bai2), (to_mt940, parse_mt940)):
            with self.subTest(parser=parser.__name__):
                result = parser(serializer([statement]), ACCOUNTS)
                self.assertTrue(result.ledger.empty)
                self.assertEqual(result.statements.closing.iloc[0], 100)
                self.assertTrue(result.exceptions.empty)

    def test_negative_balances_round_trip(self):
        statement = replace(tiny_statement(), opening_cents=-10000, closing_cents=-7500)
        for serializer, parser in ((to_camt053, parse_camt053), (to_bai2, parse_bai2), (to_mt940, parse_mt940)):
            self.assertEqual(parser(serializer([statement]), ACCOUNTS).statements.closing.iloc[0], -75)

    def test_duplicate_statement_quarantines_both_copies(self):
        result = normalize([tiny_statement(), tiny_statement()], "source")
        self.assertIn("DUPLICATE_STATEMENT", result.exceptions.code.tolist())
        self.assertTrue(result.clean_ledger.empty)

    def test_duplicate_day_with_different_identifiers_quarantines_both(self):
        first = tiny_statement()
        result = normalize([first, replace(first, statement_id="S2")], "source")
        self.assertFalse(result.statements.valid.any())
        self.assertTrue(result.clean_ledger.empty)

    def test_missing_whole_account_and_generator_dates(self):
        accounts = dict(ACCOUNTS, absent=Account("E", "BANK", "absent", "EUR"))
        result = normalize([tiny_statement()], "source", expected_dates=(d for d in [DAY]), expected_accounts=accounts)
        self.assertEqual(result.exceptions.account.tolist(), ["absent"])

    def test_missing_reference_is_quarantined(self):
        s = tiny_statement()
        s = replace(s, transactions=(replace(s.transactions[0], reference=""), s.transactions[1]))
        self.assertTrue(normalize([s], "source").clean_ledger.empty)

    def test_synthetic_seed_reproducibility(self):
        second = generate()
        self.assertEqual(self.data.statements, second.statements)
        pd.testing.assert_frame_equal(self.data.fx, second.fx)
        pd.testing.assert_frame_equal(self.data.ar, second.ar)

    def test_synthetic_seed_changes_history(self):
        self.assertNotEqual(self.data.statements, generate(7).statements)
