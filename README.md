# mini-tms-cash-engine

An offline Python library for a small treasury workflow: bank statement reconciliation, cash positioning, liquidity forecasting, FX exposure and controlled payment-file export. This is a prototype workflow, not a replacement for a commercial treasury system.

All financial data are synthetic. Seeded generation creates 4 legal entities, 3 currencies, 8 accounts at 3 synthetic banks, and 90 days of transactions. USD, EUR and GBP accounts include concentration, disbursement, collection and local roles. No network access or bank connection is used at runtime.

## What it does

- Generates clean and defective statements in camt.053.001.02, BAI2 and MT940, then parses every file into a common ledger.
- Reconciles balances and BAI2 control totals, including continuation record counts. Reports duplicate transactions, missing days and balance discontinuities, and quarantines affected statements.
- Calculates daily cash position by account, entity, bank and currency, with FX conversion, target surplus or shortfall, and stale balance flags.
- Simulates same-currency target sweeps and stylized multicurrency notional interest compensation.
- Builds a 13-week direct forecast from AR, AP, payroll, tax, debt service and intercompany flows. Checks entity liquidity buffers and reports variance, MAPE, bias and the largest errors.
- Nets foreign-currency exposure by entity and tenor, recommends opposing forward notionals within policy bands, and shows residual exposure.
- Enforces maker-checker approval before pain.001.001.03 export. Larger batches require another distinct checker. Frozen payloads and a hash-chain audit log bind approvals and generated XML.

## Run

Use Python with NumPy and pandas. They are the only external runtime dependencies. The example and tests run directly from the checkout when those dependencies are already available.

```text
python -m unittest discover -s tests -v
python examples/run_workflow.py
```

For library imports from another project, install the package in the chosen Python environment:

```text
python -m pip install -e .
```

The example overwrites generated reports under `examples/output/` and prints the metrics stored in `results.json`. It needs no downloads, credentials, spreadsheet application or optional charting packages.

## Methodology

Statement reconciliation uses integer cents before normalized tables expose currency-unit amounts. The common ledger retains entity, bank, account, currency, booking date, value date, signed amount, bank type code, reference and running book balance. Separate statement balances preserve days without activity. Raw rows remain available alongside accepted rows and quality exceptions.

Position uses the latest accepted booked closing balance. Unknown balances remain unknown, and carried balances are labelled stale. Seeded FX quotes express USD per native unit; cross rates divide native and reporting quotes. Sweeps conserve native cash separately for each currency and identify intercompany transfers. Zero targets implement zero balancing. Sweeps may create concentration overdrafts and are separate from the baseline forecast.

Pooling converts deposits and overdrafts into USD equivalents. It offsets low-rate deposits against high-rate borrowing only when the avoided borrowing rate exceeds the displaced deposit rate. The benefit uses simple accrual with the latest snapshot, quotes and stylized rates held constant for 91 days. Account balances do not change. This is a scenario result, not realized interest.

AR expected receipts multiply invoice amounts by aging-bucket collection probabilities and timing weights. AP and scheduled obligations follow due dates. Allocations beyond the horizon remain outside it. Weekly closing equals opening plus all forecast lines; the next opening equals that closing. Buffers flag a shortfall without automatic financing. Forecast spot rates remain frozen at inception.

Variance equals actual less forecast, so positive variance improves cash. MAPE uses absolute actual amounts as denominators and excludes zero actuals; coverage is reported. Bias is mean signed error by line. Actuals are a separately seeded synthetic scenario, not an observed backtest. Automated explanations describe patterns and label causal explanations as hypotheses.

FX recommendations exclude functional-currency flows and oppose net exposure using stylized tenor bands. Forward points follow simple-interest covered interest parity. Residual exposure equals original exposure plus signed hedge notional. Gross exposure totals sum absolute net exposures across entity, currency and tenor buckets, rather than a net group position.

Payment controls apply to the total native amount of a single-currency batch. The maker cannot approve that batch. Export requires sufficient distinct checkers, verifies the approved payload, and records the XML hash. An external audit head or event count detects tail truncation. The in-memory append-only API alone cannot make storage tamper-proof.

Format field semantics were checked against the original [ISO message definition report, readable copy](https://zv.psa.at/schemata/Payments_Maintenance_2009.pdf), [BAI technical manual](https://www.bai.org/docs/default-source/libraries/site-general-downloads/cash_management_2005.pdf), and [Danske Bank MT940 description](https://danskeci.com/-/media/pdf/danskeci-com/swift-mt/reconciliation/mt940_structured.pdf). Exact sections, supported profiles and retrieval limitations appear in [methodology](docs/methodology.md).

## Validation and actual run results

Fixed data seed: `20250317`. Final local run: **150 tests passed**, with **0 failures and 0 errors**, on Python **3.12.14**, NumPy **2.3.5** and pandas **3.0.1**. The complete example succeeded. Machine-readable evidence is in [validation.json](examples/output/validation.json) and [results.json](examples/output/results.json). The CI matrix is configured for Python 3.11 and 3.12; CI was not executed locally.

Tests cover hand-written known-result fixtures, independent BAI2 control arithmetic and continuation delimiters, full seeded round trips, defect detection, stale balance handling, sweep conservation and idempotence, analytic pooling benchmarks, forecast arithmetic and collection monotonicity, forward parity, hedge identities, approval rejection, audit tamper detection and payment controls.

| Validation measure | Actual output |
| :- | :- |
| Clean statements per format | 720 |
| Clean transactions per format | 1,440 |
| Clean exceptions per format | 0 |
| Maximum statement residual, native units | 0.00 |
| Maximum source-versus-parser difference, cents | 0 |
| Defective-feed findings per format | 5 |
| Quarantined statements per defective format | 3 |
| Missing statement days per defective format | 1 |

The defective feed repeats a transaction, changes a closing balance and omits a day. Findings include two out-of-balance statements, a duplicate reference, a subsequent balance discontinuity and a missing day. Later valid closing statements can reestablish a current snapshot, but the missing transaction period remains incomplete.

All statement formats reproduce these clean native-currency totals:

| Currency | Transactions | Native net movement |
| :- | :- | :- |
| EUR | 360 | -189,357.64 |
| GBP | 360 | -180,530.59 |
| USD | 720 | -472,913.94 |

### Position, pooling, FX and payments

| Scenario measure | Actual output |
| :- | :- |
| Latest group cash, USD | 343,791.13 |
| Sweep transfers | 4 |
| Sweep change in reporting cash, USD | 5.82076609135e-11 |
| Standalone interest, USD | -4,027.50 |
| Compensated interest, USD | 3,223.92 |
| Pooling benefit, USD | 7,251.42 |
| Gross absolute FX exposure before hedging, USD | 380,964.33 |
| Gross absolute FX exposure after hedging, USD | 177,944.72 |
| Payment transactions | 3 |
| Payment control sum, USD | 75,000.00 |
| Audit events | 4 |
| Audit chain verified | true |

The sweep reporting residual is floating-point conversion noise; native cash conservation is tested per currency. Payment sums and both XML transaction counts reconcile with the approved batch. Payment validation is **structural only**, with approved-payload reconciliation.

### Forecast and liquidity

Final group forecast cash is **USD 60,741.80**. There are **39 liquidity breaches across 52 entity-weeks**. Positive group cash does not imply adequate entity liquidity. The baseline retains overdrafts and does not automatically redistribute cash.

| Entity | Minimum weekly close, USD | Buffer, USD | Final close, USD | Breach weeks |
| :- | :- | :- | :- | :- |
| E1 | 963,887.71 | 200,000.00 | 963,887.71 | 0 |
| E2 | -331,007.39 | 50,000.00 | -273,454.54 | 13 |
| E3 | -381,218.98 | 40,000.00 | -381,218.98 | 13 |
| E4 | -248,472.39 | 30,000.00 | -248,472.39 | 13 |

Forecast error against synthetic actuals:

| Line | MAPE, % | Mean bias, USD | Total variance, USD | MAPE observations |
| :- | :- | :- | :- | :- |
| Debt service | 3.42 | -32.86 | -525.78 | 16 |
| Intercompany | 0.00 | 0.00 | 0.00 | 6 |
| Payroll | 5.66 | 218.51 | 11,362.46 | 52 |
| Receipts | 14.95 | -4,012.83 | -208,667.40 | 52 |
| Tax | 5.55 | 69.57 | 834.90 | 12 |
| Vendors | 7.74 | -2,501.52 | -125,076.20 | 50 |

Total actual-minus-forecast flow variance is **USD -322,072.02**. These errors describe the constructed scenario; they do not demonstrate predictive performance on observed treasury data.

## Outputs and library layout

[Markdown report](examples/output/report.md) and [self-contained HTML dashboard](examples/output/dashboard.html) summarize position, forecast and exceptions. The dashboard uses inline CSS with no scripts, external libraries or remote assets. Tables are also exported as CSV; charts are omitted.

| Location | Contents |
| :- | :- |
| `src/mini_tms/models.py` | Immutable account, transaction and statement records; cents arithmetic; reconciliation |
| `src/mini_tms/parsers.py`, `statements.py` | Statement ingestion and synthetic serialization |
| `src/mini_tms/synthetic.py`, `rates.py` | Seeded data and exact-date FX quotes |
| `src/mini_tms/position.py` | Position aggregation, sweeps and notional compensation |
| `src/mini_tms/forecast.py`, `fx.py` | Direct forecast, variance metrics and FX recommendations |
| `src/mini_tms/payments.py` | Maker-checker workflow, audit chain, payment XML and structural validator |
| `src/mini_tms/reporting.py` | Portable Markdown and HTML rendering |
| `tests/` | Unit tests and hand-written statement fixtures |
| `docs/methodology.md` | Formula conventions, source sections and limitations |
| `examples/run_workflow.py` | Runnable end-to-end workflow |
| `examples/output/` | Generated inputs and CSV, Markdown, HTML, JSON and XML outputs |

The output directory includes raw and accepted ledgers, statement balances, positions by each dimension, sweep transfers, pooling, forecast flows and balances, synthetic actuals, variances, liquidity and FX exposure. `assumptions.json` exports collection probabilities, buffers, rates, hedge bands and approval thresholds. `audit.json` records payment events. `format_reconciliation.csv` provides source-versus-parser totals and residuals.

The statement feeds are alternate encodings of the same history. Do not concatenate them into one ledger.

## Limitations

All policy bands, collection probabilities, liquidity buffers, approval thresholds and rates are **stylized assumptions**, not regulatory parameters or market estimates. No regulatory thresholds are asserted.

The parsers support documented subsets, not every bank dialect. BAI2 uses two decimal minor units and final daily snapshots; stateful update replacement, deletion and correction processing are omitted. MT940 accepts bare final statement bodies rather than FIN transport or interim pages. References are assumed unique per account within an ingestion batch. Missing-day checks use a supplied calendar-day schedule. Bank holiday calendars, full transaction-code catalogues and complete camt schema validation are omitted.

The published pain.001 XSD could not be retrieved through the available web transport or offline shell. No copy is bundled and no XSD conformance is claimed. The documented validator checks namespace, element order, cardinality, scalar syntax, dates, identities, currencies, counts, sums and the approved payload. Direct SWIFT documentation was inaccessible; MT940 semantics were checked against the bank's original specification. Full SWIFT conformance remains unverified.

The model omits transaction costs, bank limits, legal pooling eligibility, set-off and cross-guarantee analysis, taxes on pooling, FX basis and credit risk, stochastic forecasting and invoice-level hedge maturity matching. Maker-checker roles are supplied identities, not authentication. Payment state and audit storage are in memory, without concurrent processing, persistence, digital signatures or bank acknowledgements. Exported payment files are synthetic output only.

MIT license. See [LICENSE](LICENSE).
