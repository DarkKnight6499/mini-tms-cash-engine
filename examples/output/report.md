# Synthetic treasury workflow report

All data and policy assumptions are synthetic.

## Validation results

| metric | value |
| :- | :- |
| seed | 20250317 |
| entities | 4 |
| currencies | 3 |
| banks | 3 |
| accounts | 8 |
| history_days | 90 |
| forecast_weeks | 13 |
| clean_statements | 720 |
| clean_transactions | 1440 |
| clean_exceptions | 0 |
| max_reconciliation_residual | 0.00 |
| max_format_difference_cents | 0 |
| defective_statements | 719 |
| defective_exceptions | 5 |
| quarantined_statements | 3 |
| missing_days | 1 |
| latest_cash_usd | 343,791.13 |
| sweep_cash_difference_usd | 0.00 |
| sweep_transfer_count | 4 |
| pooling_benefit_usd | 7,251.42 |
| pooling_days | 91 |
| unpooled_interest_usd | -4,027.50 |
| pooled_interest_usd | 3,223.92 |
| compensated_cash_usd | 723,472.02 |
| final_forecast_cash_usd | 60,741.80 |
| liquidity_breach_entity_weeks | 39 |
| forecast_entity_weeks | 52 |
| receipts_mape_pct | 14.95 |
| vendor_mape_pct | 7.74 |
| forecast_total_variance_usd | -322,072.02 |
| forecast_mean_bias_usd | -1,713.15 |
| absolute_fx_exposure_before_usd | 380,964.33 |
| absolute_fx_exposure_after_usd | 177,944.72 |
| payment_transactions | 3 |
| payment_control_sum | 75,000.00 |
| audit_events | 4 |
| audit_valid | True |
| payment_validation | structural_only |

## Latest account position

| account | entity | currency | balance_native | balance_reporting | stale |
| :- | :- | :- | :- | :- | :- |
| A100 | E1 | USD | 633,469.17 | 633,469.17 | False |
| A200 | E1 | EUR | 234,209.83 | 249,308.55 | False |
| A300 | E1 | GBP | 141,545.68 | 179,682.38 | False |
| A101 | E2 | USD | -290,682.19 | -290,682.19 | False |
| A102 | E2 | USD | 4,803.05 | 4,803.05 | False |
| A201 | E3 | EUR | -183,567.47 | -195,401.45 | False |
| A301 | E4 | GBP | -92,076.27 | -116,884.41 | False |
| A103 | E3 | USD | -120,503.97 | -120,503.97 | False |

## Interest compensation

| days | compensated_usd | unpooled_interest_usd | pooled_interest_usd | benefit_usd |
| :- | :- | :- | :- | :- |
| 91 | 723,472.02 | -4,027.50 | 3,223.92 | 7,251.42 |

## Final forecast week, USD

| entity | closing | buffer | breach |
| :- | :- | :- | :- |
| E1 | 963,887.71 | 200,000.00 | False |
| E2 | -273,454.54 | 50,000.00 | True |
| E3 | -381,218.98 | 40,000.00 | True |
| E4 | -248,472.39 | 30,000.00 | True |

## Liquidity summary, USD

| entity | minimum_closing_usd | buffer_usd | minimum_headroom_usd | breach_weeks | final_closing_usd |
| :- | :- | :- | :- | :- | :- |
| E1 | 963,887.71 | 200,000.00 | 763,887.71 | 0 | 963,887.71 |
| E2 | -331,007.39 | 50,000.00 | -381,007.39 | 13 | -273,454.54 |
| E3 | -381,218.98 | 40,000.00 | -421,218.98 | 13 | -381,218.98 |
| E4 | -248,472.39 | 30,000.00 | -278,472.39 | 13 | -248,472.39 |

## Variance metrics, USD

| line | mape_pct | bias | total_variance | wape_pct | observations | missing_actuals | mape_observations |
| :- | :- | :- | :- | :- | :- | :- | :- |
| debt_service | 3.42 | -32.86 | -525.78 | 3.63 | 16 | 0 | 16 |
| intercompany | 0.00 | 0.00 | 0.00 | 0.00 | 6 | 0 | 6 |
| payroll | 5.66 | 218.51 | 11,362.46 | 5.62 | 52 | 0 | 52 |
| receipts | 14.95 | -4,012.83 | -208,667.40 | 13.07 | 52 | 0 | 52 |
| tax | 5.55 | 69.57 | 834.90 | 5.55 | 12 | 0 | 12 |
| vendors | 7.74 | -2,501.52 | -125,076.20 | 8.11 | 50 | 0 | 50 |

## Largest variance explanations

| explanation |
| :- |
| E4, week 1, receipts: actual minus forecast -14,332.96; pattern consistent with lower or delayed collections. Cause is a hypothesis, not confirmed. |
| E3, week 9, receipts: actual minus forecast -14,209.08; pattern consistent with lower or delayed collections. Cause is a hypothesis, not confirmed. |
| E1, week 6, receipts: actual minus forecast -13,752.30; pattern consistent with lower or delayed collections. Cause is a hypothesis, not confirmed. |
| E1, week 1, receipts: actual minus forecast -12,372.18; pattern consistent with lower or delayed collections. Cause is a hypothesis, not confirmed. |
| E4, week 1, vendors: actual minus forecast -11,328.68; pattern consistent with larger outflows. Cause is a hypothesis, not confirmed. |

## Exceptions from defective camt feed

| format | statement_id | account | date | code | detail |
| :- | :- | :- | :- | :- | :- |
| camt.053 | A100-2025-01-11 | A100 | 2025-01-11 | OUT_OF_BALANCE | Residual -9597.91 USD |
| camt.053 | A100-2025-01-11 | A100 | 2025-01-11 | DUPLICATE_TRANSACTION | Repeated reference T0010C |
| camt.053 | A200-2025-01-21 | A200 | 2025-01-21 | OUT_OF_BALANCE | Residual 100.00 EUR |
| camt.053 | A200-2025-01-22 | A200 | 2025-01-22 | BALANCE_DISCONTINUITY | Prior closing differs from opening |
| camt.053 |  | A300 | 2025-01-31 | MISSING_DAY | Expected calendar-day statement absent |

