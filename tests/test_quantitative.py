"""Analytic cash, pooling, forecast and FX benchmarks and invariants."""

from dataclasses import replace
from datetime import timedelta
import unittest

from helpers import DAY, tiny_fx

import numpy as np
import pandas as pd

from mini_tms.forecast import (COLLECTION_POLICY, FLOW_COLUMNS, LINES, convert_flows,
                               direct_flows, synthetic_actuals, variance_report, weekly_balances)
from mini_tms.fx import forward_rate, hedge_recommendations, tenor_bucket
from mini_tms.models import Account, Statement, normalize
from mini_tms.position import aggregate_position, daily_position, notional_pool, sweep
from mini_tms.rates import FXTable
from mini_tms.synthetic import generate


class PositionTests(unittest.TestCase):
    def setUp(self):
        items = [Account("E1", "B", "P", "USD", "concentration", target=100),
                 Account("E2", "B", "C", "USD", "disbursement", "P"),
                 Account("E2", "B", "D", "USD", "collection", "P", 50),
                 Account("E3", "B", "E", "EUR"), Account("E4", "B", "G", "GBP"),
                 Account("E1", "B", "Z", "USD")]
        self.accounts = {a.account: a for a in items}
        balances = (1000, -100, 200, -50, 80, 0)
        self.statements = normalize([Statement(a.account, a, DAY, b * 100, b * 100)
                                     for a, b in zip(items, balances)], "test").statements
        self.fx = tiny_fx()
        self.position = daily_position(self.statements, self.accounts, [DAY], self.fx)

    def test_reporting_conversion_known_total(self):
        self.assertAlmostEqual(self.position.balance_reporting.sum(), 1160)

    def test_target_surplus_identity(self):
        np.testing.assert_allclose(self.position.surplus_native,
                                   self.position.balance_native - self.position.target_native)

    def test_aggregations_reconcile_reporting_total(self):
        for dimension in ("account", "entity", "bank", "currency"):
            self.assertAlmostEqual(aggregate_position(self.position, dimension).balance_reporting.sum(), 1160)

    def test_aggregation_keeps_currencies_separate(self):
        self.assertEqual(aggregate_position(self.position, "bank").currency.nunique(), 3)

    def test_sweep_conserves_each_native_currency(self):
        swept, transfers = sweep(self.position, self.accounts)
        pd.testing.assert_series_equal(swept.groupby("currency").balance_native.sum(),
                                       self.position.groupby("currency").balance_native.sum())
        self.assertAlmostEqual(swept.balance_reporting.sum(), 1160)
        self.assertEqual(len(transfers), 2)

    def test_sweep_hits_targets_and_parent(self):
        swept, _ = sweep(self.position, self.accounts)
        balance = swept.set_index("account").balance_native
        self.assertEqual(balance["P"], 1050)
        self.assertEqual(balance["C"], 0)
        self.assertEqual(balance["D"], 50)

    def test_sweep_intercompany_flags(self):
        _, transfers = sweep(self.position, self.accounts)
        self.assertTrue(transfers.intercompany.all())

    def test_sweep_idempotence(self):
        first, _ = sweep(self.position, self.accounts)
        second, transfers = sweep(first, self.accounts)
        pd.testing.assert_frame_equal(first, second)
        self.assertTrue(transfers.empty)

    def test_sweep_cross_currency_rejected(self):
        accounts = dict(self.accounts)
        accounts["C"] = replace(accounts["C"], parent="E")
        with self.assertRaises(ValueError):
            sweep(self.position, accounts)

    def test_sweep_stale_rejected(self):
        position = self.position.copy()
        position.loc[0, "stale"] = True
        with self.assertRaises(ValueError):
            sweep(position, self.accounts)

    def test_sweep_registry_mismatch_rejected(self):
        for field, value in (("currency", "EUR"), ("entity", "UNKNOWN"), ("target_native", 10)):
            position = self.position.copy()
            position.loc[position.account == "C", field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                sweep(position, self.accounts)

    def test_sweep_nonfinite_target_rejected(self):
        accounts = dict(self.accounts)
        accounts["C"] = replace(accounts["C"], target=float("nan"))
        with self.assertRaises(ValueError):
            sweep(self.position, accounts)

    def test_missing_initial_balance_marked_unknown(self):
        statements = self.statements[self.statements.account != "P"]
        position = daily_position(statements, self.accounts, [DAY], self.fx)
        p = position[position.account == "P"].iloc[0]
        self.assertTrue(np.isnan(p.balance_native))
        self.assertTrue(p.stale)
        self.assertTrue(aggregate_position(position, "bank").query("currency == 'USD'").balance_reporting.isna().all())

    def test_invalid_statement_not_used(self):
        statements = self.statements.copy()
        statements.loc[statements.account == "P", "valid"] = False
        position = daily_position(statements, self.accounts, [DAY], self.fx)
        self.assertTrue(position.iloc[0].stale)

    def test_missing_day_carries_balance_with_stale_flag(self):
        later = DAY + timedelta(days=1)
        frame = pd.DataFrame([dict(date=d.isoformat(), currency=c, usd_per_unit=r)
                              for d in (DAY, later) for c, r in (("USD", 1), ("EUR", 1.2), ("GBP", 1.5))])
        position = daily_position(self.statements, self.accounts, [later], FXTable(frame))
        self.assertTrue(position.stale.all())
        self.assertAlmostEqual(position.balance_reporting.sum(), 1160)

    def test_pooling_analytic_spread_benchmark(self):
        position = self.position[self.position.account.isin(["P", "E"])].copy()
        position.loc[position.account == "P", "balance_native"] = 100
        rates = {"USD": (.03, .07), "EUR": (.02, .06)}
        result = notional_pool(position, self.fx, 365, rates)
        self.assertAlmostEqual(result["compensated_usd"], 60)
        self.assertAlmostEqual(result["benefit_usd"], 1.8)
        self.assertAlmostEqual(result["unpooled_interest_usd"], -.6)
        self.assertAlmostEqual(result["pooled_interest_usd"], 1.2)

    def test_pooling_additive_in_days(self):
        one = notional_pool(self.position, self.fx, 1)
        ten = notional_pool(self.position, self.fx, 10)
        self.assertAlmostEqual(ten["benefit_usd"], one["benefit_usd"] * 10)

    def test_pooling_designed_benefit_nonnegative(self):
        result = notional_pool(self.position, self.fx)
        self.assertGreater(result["benefit_usd"], 0)
        self.assertGreaterEqual(result["pooled_interest_usd"], result["unpooled_interest_usd"])

    def test_pooling_no_liabilities_no_benefit(self):
        p = self.position[self.position.balance_native >= 0]
        self.assertEqual(notional_pool(p, self.fx)["benefit_usd"], 0)

    def test_pooling_no_assets_no_benefit(self):
        p = self.position[self.position.balance_native < 0]
        self.assertEqual(notional_pool(p, self.fx)["benefit_usd"], 0)

    def test_pooling_does_not_mutate_balances(self):
        original = self.position.copy(deep=True)
        notional_pool(self.position, self.fx)
        pd.testing.assert_frame_equal(self.position, original)

    def test_pooling_duplicate_balances_rejected(self):
        duplicate = pd.concat([self.position, self.position.iloc[:1]], ignore_index=True)
        with self.assertRaises(ValueError):
            notional_pool(duplicate, self.fx)

    def test_pooling_missing_rates_rejected(self):
        with self.assertRaises(ValueError):
            notional_pool(self.position, self.fx, rates={})

    def test_pooling_no_economic_spread_leaves_cash_unchanged(self):
        rates = {"USD": (.10, .11), "EUR": (.02, .06)}
        position = self.position[self.position.account.isin(["P", "E"])]
        result = notional_pool(position, self.fx, rates=rates)
        self.assertEqual(result["benefit_usd"], 0)
        self.assertEqual(result["pooled_interest_usd"], result["unpooled_interest_usd"])


class FXTests(unittest.TestCase):
    def test_cross_rate_reciprocity(self):
        fx = tiny_fx()
        self.assertAlmostEqual(fx.rate(DAY.isoformat(), "EUR", "GBP") * fx.rate(DAY.isoformat(), "GBP", "EUR"), 1)

    def test_missing_rate_rejected(self):
        with self.assertRaises(ValueError):
            tiny_fx().rate("2025-01-02", "EUR")

    def test_invalid_rates_rejected(self):
        for value in (0, -1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                FXTable(pd.DataFrame([dict(date=DAY.isoformat(), currency="EUR", usd_per_unit=value)]))

    def test_duplicate_rates_rejected(self):
        row = dict(date=DAY.isoformat(), currency="EUR", usd_per_unit=1.2)
        with self.assertRaises(ValueError):
            FXTable(pd.DataFrame([row, row]))

    def test_usd_quote_must_equal_one(self):
        with self.assertRaises(ValueError):
            FXTable(pd.DataFrame([dict(date=DAY.isoformat(), currency="USD", usd_per_unit=1.1)]))

    def test_forward_analytic_parity(self):
        self.assertAlmostEqual(forward_rate(1.2, .05, .02, 365), 1.2 * 1.05 / 1.02)

    def test_forward_inverse_parity(self):
        f = forward_rate(1.2, .05, .02, 91)
        inverse = forward_rate(1 / 1.2, .02, .05, 91)
        self.assertAlmostEqual(f * inverse, 1)

    def test_zero_tenor_forward_equals_spot(self):
        self.assertEqual(forward_rate(1.2, .05, .02, 0), 1.2)

    def test_equal_rates_forward_equals_spot(self):
        self.assertEqual(forward_rate(1.2, .03, .03, 91), 1.2)

    def test_forward_monotone_domestic_rate(self):
        self.assertGreater(forward_rate(1.2, .06, .02, 91), forward_rate(1.2, .03, .02, 91))

    def test_forward_invalid_inputs(self):
        for args in ((0, .03, .02, 91), (1, .03, -5, 365), (1, .03, .02, -1), (float("nan"), 0, 0, 1)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                forward_rate(*args)

    def test_tenor_boundaries(self):
        self.assertEqual([tenor_bucket(w) for w in (0, 3, 4, 7, 8, 12)], ["near", "near", "medium", "medium", "far", "far"])
        for w in (-1, 13):
            with self.assertRaises(ValueError):
                tenor_bucket(w)

    def test_hedge_signed_exposure_identity_and_bands(self):
        flows = pd.DataFrame([dict(entity="E", currency="EUR", week=w, amount=a)
                              for w, a in ((0, 100), (1, -20), (5, -200), (10, 300))])
        result = hedge_recommendations(flows, {"E": "USD"}, tiny_fx(), DAY.isoformat())
        np.testing.assert_allclose(result.exposure_before + result.hedge_notional, result.exposure_after)
        self.assertTrue((result.exposure_after.abs() <= result.exposure_before.abs()).all())
        self.assertTrue(((result.hedge_ratio >= result.policy_min) & (result.hedge_ratio <= result.policy_max)).all())
        self.assertEqual(result.set_index("bucket").loc["near", "exposure_after"], 16)
        self.assertEqual(result.set_index("bucket").loc["medium", "action"], "buy")

    def test_functional_currency_not_hedged(self):
        flows = pd.DataFrame([dict(entity="E", currency="USD", week=0, amount=100)])
        self.assertTrue(hedge_recommendations(flows, {"E": "USD"}, tiny_fx(), DAY.isoformat()).empty)

    def test_invalid_hedge_band_rejected(self):
        flows = pd.DataFrame([dict(entity="E", currency="EUR", week=0, amount=100)])
        policy = {k: (.9, .7, .8) for k in ("near", "medium", "far")}
        with self.assertRaises(ValueError):
            hedge_recommendations(flows, {"E": "USD"}, tiny_fx(), DAY.isoformat(), policy)


class ForecastTests(unittest.TestCase):
    def setUp(self):
        self.ar = pd.DataFrame([dict(entity="E", currency="USD", invoice="R", amount=100,
                                    due_date=DAY.isoformat(), aging_bucket="current")])
        self.ap = pd.DataFrame([dict(entity="E", currency="USD", invoice="P", amount=20,
                                    due_date=DAY.isoformat())])
        self.scheduled = pd.DataFrame(columns=["entity", "currency", "date", "line", "amount"])

    def test_collection_analytic_expected_receipts(self):
        flows = direct_flows(self.ar, self.ap.iloc[0:0], self.scheduled, DAY)
        self.assertEqual(flows.amount.tolist(), [38, 38, 19])
        self.assertAlmostEqual(flows.amount.sum(), 95)

    def test_collection_monotone_probability(self):
        lower = direct_flows(self.ar, self.ap, self.scheduled, DAY, policy={"current": (.5, ((0, 1),))})
        higher = direct_flows(self.ar, self.ap, self.scheduled, DAY, policy={"current": (.9, ((0, 1),))})
        self.assertGreater(higher.amount.sum(), lower.amount.sum())

    def test_ap_overdue_paid_first_week(self):
        self.ap["due_date"] = (DAY - timedelta(days=20)).isoformat()
        flows = direct_flows(self.ar.iloc[0:0], self.ap, self.scheduled, DAY)
        self.assertEqual(flows.week.tolist(), [0])
        self.assertEqual(flows.amount.tolist(), [-20])

    def test_forecast_week_boundary(self):
        self.ap["due_date"] = (DAY + timedelta(days=7)).isoformat()
        flows = direct_flows(self.ar.iloc[0:0], self.ap, self.scheduled, DAY)
        self.assertEqual(flows.week.tolist(), [1])

    def test_horizon_excludes_tail_without_renormalization(self):
        flows = direct_flows(self.ar, self.ap.iloc[0:0], self.scheduled, DAY, horizon=1)
        self.assertEqual(flows.amount.sum(), 38)

    def test_future_ap_outside_horizon(self):
        self.ap["due_date"] = (DAY + timedelta(days=91)).isoformat()
        flows = direct_flows(self.ar.iloc[0:0], self.ap, self.scheduled, DAY)
        self.assertTrue(flows.empty)

    def test_invalid_collection_weights_rejected(self):
        for policy in ({"current": (.9, ((0, .5),))}, {"current": (1.1, ((0, 1),))}, {"current": (.9, ((-1, 1),))}):
            with self.assertRaises(ValueError):
                direct_flows(self.ar, self.ap, self.scheduled, DAY, policy=policy)

    def test_negative_invoice_rejected(self):
        for label in ("ar", "ap"):
            ar, ap = self.ar.copy(), self.ap.copy()
            (ar if label == "ar" else ap).loc[0, "amount"] = -1
            with self.assertRaises(ValueError):
                direct_flows(ar, ap, self.scheduled, DAY)

    def test_forecast_arithmetic_identity(self):
        flows = convert_flows(direct_flows(self.ar, self.ap, self.scheduled, DAY), tiny_fx(), DAY.isoformat())
        result = weekly_balances(flows, {"E": 100}, {"E": 50})
        np.testing.assert_allclose(result.opening + result.net_flow, result.closing)
        np.testing.assert_allclose(result[list(LINES)].sum(axis=1), result.net_flow)
        np.testing.assert_allclose(result.opening.iloc[1:], result.closing.iloc[:-1])
        self.assertAlmostEqual(result.closing.iloc[-1], 175)

    def test_buffer_equality_is_not_breach(self):
        flows = pd.DataFrame(columns=["entity", "week", "line", "amount_reporting"])
        result = weekly_balances(flows, {"E": 50}, {"E": 50})
        self.assertFalse(result.breach.any())
        self.assertEqual(result.headroom.sum(), 0)

    def test_unknown_entity_rejected(self):
        flows = pd.DataFrame([dict(entity="X", week=0, line="receipts", amount_reporting=1)])
        with self.assertRaises(ValueError):
            weekly_balances(flows, {"E": 100}, {"E": 50})

    def test_intercompany_conserves_group_cash(self):
        data = generate()
        self.assertEqual(data.scheduled.query("line == 'intercompany'").amount.sum(), 0)
        fx = FXTable(data.fx)
        flows = convert_flows(direct_flows(data.ar, data.ap, data.scheduled, data.forecast_start), fx,
                              data.forecast_start.isoformat())
        self.assertEqual(flows.query("line == 'intercompany'").amount_reporting.sum(), 0)

    def test_synthetic_actuals_reproducible(self):
        flows = convert_flows(direct_flows(self.ar, self.ap, self.scheduled, DAY), tiny_fx(), DAY.isoformat())
        pd.testing.assert_frame_equal(synthetic_actuals(flows), synthetic_actuals(flows))

    def test_synthetic_actual_intercompany_conserves_group_cash(self):
        data = generate()
        flows = convert_flows(direct_flows(data.ar, data.ap, data.scheduled, data.forecast_start),
                              FXTable(data.fx), data.forecast_start.isoformat())
        actuals = synthetic_actuals(flows)
        self.assertAlmostEqual(actuals.query("line == 'intercompany'").actual.sum(), 0)

    def test_mape_and_bias_known_benchmark(self):
        flows = pd.DataFrame([dict(entity="E", week=w, line="receipts", amount_reporting=f)
                              for w, f in ((0, 80), (1, 120))])
        actual = pd.DataFrame([dict(entity="E", week=w, line="receipts", actual=100) for w in (0, 1)])
        detail, metrics, _ = variance_report(flows, actual)
        self.assertEqual(detail.variance.tolist(), [20, -20])
        self.assertEqual(metrics.mape_pct.iloc[0], 20)
        self.assertEqual(metrics.bias.iloc[0], 0)

    def test_zero_actual_excluded_from_mape_not_bias(self):
        flows = pd.DataFrame([dict(entity="E", week=0, line="receipts", amount_reporting=20)])
        actual = pd.DataFrame([dict(entity="E", week=0, line="receipts", actual=0)])
        _, metrics, _ = variance_report(flows, actual)
        self.assertTrue(np.isnan(metrics.mape_pct.iloc[0]))
        self.assertEqual(metrics.mape_observations.iloc[0], 0)
        self.assertEqual(metrics.bias.iloc[0], -20)

    def test_missing_actual_not_invented_as_zero(self):
        flows = pd.DataFrame([dict(entity="E", week=0, line="receipts", amount_reporting=20)])
        actual = pd.DataFrame(columns=["entity", "week", "line", "actual"])
        detail, metrics, largest = variance_report(flows, actual)
        self.assertTrue(detail.actual.isna().all())
        self.assertEqual(metrics.missing_actuals.iloc[0], 1)
        self.assertTrue(largest.empty)

    def test_unforecast_actual_recorded(self):
        flows = pd.DataFrame(columns=["entity", "week", "line", "amount_reporting"])
        actual = pd.DataFrame([dict(entity="E", week=0, line="tax", actual=-40)])
        detail, _, _ = variance_report(flows, actual)
        self.assertEqual(detail.variance.iloc[0], -40)

    def test_duplicate_actual_key_rejected(self):
        flows = pd.DataFrame([dict(entity="E", week=0, line="receipts", amount_reporting=20)])
        actual = pd.DataFrame([dict(entity="E", week=0, line="receipts", actual=0)] * 2)
        with self.assertRaises(ValueError):
            variance_report(flows, actual)

    def test_largest_explanations_rank_errors_and_label_hypotheses(self):
        flows = pd.DataFrame([dict(entity="E", week=w, line="vendors", amount_reporting=-100) for w in range(7)])
        actual = pd.DataFrame([dict(entity="E", week=w, line="vendors", actual=-100 - 10 * w) for w in range(7)])
        _, _, largest = variance_report(flows, actual)
        self.assertEqual(largest.absolute_error.tolist(), [60, 50, 40, 30, 20])
        self.assertTrue(largest.explanation.str.contains("hypothesis").all())
