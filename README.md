# mini-tms-cash-engine

An offline Python library for a small treasury workflow: bank statement reconciliation, cash positioning, liquidity forecasting, FX exposure and controlled payment-file export. This is a prototype workflow, not a replacement for a commercial treasury system.

The offline examples use synthetic financial data. Seeded generation creates 4 legal entities, 3 currencies, 8 accounts at 3 synthetic banks, and 90 days of transactions. USD, EUR and GBP accounts include concentration, disbursement, collection and local roles. An optional, separate SEC downloader supplies observed working-capital data. Tests and offline workflows need no network access or bank connection.

## What it does

- Generates clean and defective statements in camt.053.001.02, BAI2 and MT940, then parses every file into a common ledger.
- Reconciles balances and BAI2 control totals, including continuation record counts. Reports duplicate transactions, missing days and balance discontinuities, and quarantines affected statements.
- Calculates daily cash position by account, entity, bank and currency, with FX conversion, target surplus or shortfall, and stale balance flags.
- Simulates same-currency target sweeps and stylized multicurrency notional interest compensation.
- Builds a 13-week direct forecast from AR, AP, payroll, tax, debt service and intercompany flows. Checks entity liquidity buffers and reports variance, MAPE, bias and the largest errors.
- Nets foreign-currency exposure by entity and tenor, recommends opposing forward notionals within policy bands, and shows residual exposure.
- Enforces maker-checker approval before pain.001.001.03 export. Larger batches require another distinct checker. Frozen payloads and a hash-chain audit log bind approvals and generated XML.
- Adds multilateral netting, 90-day physical pooling, account analysis review, working-capital analysis and share repurchase support. Exports a formula-driven Excel workbook.

## Run

Use Python 3.11+ with NumPy and pandas. Corporate tools, their tests and the workbook also use matplotlib and openpyxl. These packages must already be available in an offline environment. The optional installation extra is `.[corporate]`.

```text
python -m unittest discover -s tests -v
python examples/run_workflow.py
python examples/run_corporate_tools.py
python excel/build_workbook.py
python excel/reconcile_workbook.py
```

For library imports from another project, install the package in the chosen Python environment:

```text
python -m pip install -e .
```

The complete example also runs the corporate tools. Reports under `examples/output/` are overwritten. Corporate outputs live in `examples/output/corporate_tools/`; the workbook is `excel/corporate_treasury_tools.xlsx`. The separate corporate runner prints its own `results.json`. LibreOffice is optional: when present, headless recalculation checks every formula against Python and saves cached values in the delivered workbook. Without it, the reconciliation test skips and formulas calculate when opened in a spreadsheet application.

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

Fixed data seed: `20250317`. Final local run: **212 tests passed, 1 skipped**, with **0 failures and 0 errors**. The absent SEC snapshot causes the skip. The complete example and LibreOffice reconciliation succeeded. Machine-readable evidence is in [validation.json](examples/output/validation.json), [baseline results](examples/output/results.json) and [corporate results](examples/output/corporate_tools/results.json). The CI matrix targets Python 3.11 and 3.12; CI was not executed locally.

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

## Multilateral netting

`netting.py` creates 150 seeded intercompany invoices for the April 2025 cycle. It reuses the existing entities, functional currencies, participant accounts and exact-date FX table. All invoices settle on April 30 at that cycle's rates. If $P_{ij}$ is USD payable from entity $i$ to $j$, bilateral net is $P_{ij}-P_{ji}$ and entity net position is $N_i=\sum_j P_{ji}-\sum_j P_{ij}$. Positive positions receive cash. Net positions sum to zero.

Net payers fund the center and the center pays receivers in their functional currencies. The center uses separate illustrative clearing accounts, owned by E1, to avoid payments from an account to itself. Every settlement passes the existing maker-checker and pain.001 structural validation path. The example supplies distinct synthetic approvers; the library only creates unapproved batches. FX trades show center buys or sells for foreign-currency net amounts. Native wires round to cents; their USD rounding residual is reported separately.

Multilateral settlement value is $\sum_i\max(-N_i,0)$, counting payer funding once. Payment count includes both incoming and outgoing center wires. Value reduction is $1-V_{multi}/V_{gross}$. Gross FX conversions count each invoice's payer and receiver functional-currency mismatch; converted USD volume sums both legs. Avoided conversions and volume compare this gross basis with center FX on net amounts. Estimated savings equal wires avoided times the **illustrative USD 20 wire fee**, plus converted USD volume avoided times the **illustrative 8 bps spread**. Neither savings input is a bank quote.

## Physical and notional pooling

`pooling.py` applies existing sweep controls to zero-balance, target-balance and threshold structures. Threshold sweeps trigger outside the illustrative band of -10,000 to 20,000 native units and return to the illustrative 10,000 target. Zero balancing returns every participant to zero. The unparented E3 USD local account joins the existing USD header in this comparison.

The 90-day comparison starts each case from identical opening cash and operating transactions. Physical concentration carries swept balances into the next day. Notional pooling uses the original unswept daily balances and calls `position.notional_pool` each day. Bank interest uses existing illustrative credit/debit rates; the prior 91-day frozen-snapshot scenario remains available separately.

Positive loan principal means the participant lends to its header. Daily participant interest is principal times $(r_{pool}-s)/365$ on lending or $(r_{pool}+s)/365$ on borrowing. Pool rate 4.0% and arm's-length spread 0.5% are **illustrative**, without a transfer-pricing assessment. Monthly participant allocations plus the header's residual equal external group interest. Internal interest is not additional group earnings. Reports distinguish loan facilities from daily advances. Overdraft avoided is USD balance-days, not interest or a point-in-time balance.

## Bank fee analysis

`bank_fees.py` generates six monthly statements for three synthetic banks, with eight service types, bank-specific contracts and deliberate billing defects. Fees remain in billed totals even when flagged. Exceptions cover prices above contract plus the illustrative 1% tolerance, volume above the prior three observed months by more than the illustrative z-score limit of 3, duplicate services and billed volume with zero recorded activity. Population standard deviation is used; an increase above a constant history has infinite z-score. Cross-bank unit costs equal total billed fees divided by billed volume.

Collected balance is ledger less float. $ECA=B_{collected}(1-r)ECR\times days/365$; net charge is $\max(fees-ECA-carry\_in,0)$. Excess credits expire unless `CARRY_ECA` is enabled. Balance needed to offset fees is $fees/[ECR(1-r)days/365]$. A zero ECR implies no finite offsetting balance when fees are positive. The pay-fees decision compares reserve-adjusted ECR with the illustrative money fund yield, assuming equal tax treatment and no investment costs.

Default reserve ratio is zero. The Federal Reserve confirms zero reserve ratios effective March 26, 2020 in its [Reserve Requirements page, opening paragraph and current table](https://www.federalreserve.gov/monetarypolicy/reservereq.htm), checked October 21, 2026. ECR 2.0% and money fund yield 4.5% are **illustrative**, not current market rates. The hand test gives ECA USD 15,616.44 and net charge USD 4,383.56 on USD 20,000 fees, USD 9.5 million collected and 30 days.

## Working capital and discounts

`working_capital.py` calculates $DSO=AR/revenue\times days$, $DIO=inventory/COGS\times days$, $DPO=AP/COGS\times days$ and $CCC=DSO+DIO-DPO$. Opening/closing averages are the default; ending balances are an explicit switch. Cash released by a one-day improvement is revenue/days for DSO and COGS/days for DIO or a one-day increase in DPO. Annual days follow each source period.

Discount APR is $d/(1-d)\times365/(net\ days-discount\ days)$, so 2/10 net 30 gives 37.2449%. The illustrative dynamic discount declines linearly from 2% on day 10 to zero on day 30. Funding is attractive when discount APR exceeds the illustrative 6% borrowing rate. For a buyer-funded supply-chain-finance extension, the break-even annual fee rate equals that borrowing rate; benefit per invoice dollar is $(borrowing\ rate-SCF\ rate)\times extension\ days/365$. The illustrative SCF rate is 5% over 30 days. This excludes supplier credit risk and program fees.

Existing forecast AR is re-aged into current, 1-30, 31-60, 61-90 and over-90 day buckets. Collections priority sorts overdue days, then USD amount, then invoice ID. This is an operational ordering rule, not a credit-risk estimate.

### Optional SEC provenance

Run `python examples/fetch_working_capital.py` separately on a network-enabled machine after replacing the illustrative contact in `SEC_USER_AGENT`. It resolves HAS, WHR and KSS through the SEC ticker file, then retrieves companyfacts at four requests per second maximum. The [SEC EDGAR API documentation, XBRL Data APIs and companyfacts sections](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) confirms the endpoint and warns about period alignment. Each raw JSON has a URL, retrieval timestamp and SHA-256 in `data/working_capital/manifest.json`; the CSV also has a hash.

Only USD annual 10-K facts qualify. Revenue falls back from `RevenueFromContractWithCustomerExcludingAssessedTax` to `Revenues`; COGS tries `CostOfGoodsAndServicesSold`, `CostOfRevenue`, then `CostOfGoodsSold`. Balance tags are `AccountsReceivableNetCurrent`, `InventoryNet` and `AccountsPayableCurrent`. Annual durations must span 350-380 inclusive days, with revenue and COGS sharing start/end dates. Opening balances match the day before period start. Missing aligned facts fail explicitly. Latest filed facts take precedence; fiscal-year labels come from the earliest annual filing for each period, avoiding comparative facts' later filing-year labels. Tag and accession provenance remain in the CSV. A new snapshot is published only when all three issuers normalize successfully; failed attempts retain raw responses and leave any previous snapshot intact.

**Actual fetch result:** the shell blocked the SEC ticker request with socket WinError 10013. No observed issuer snapshot was produced. Companyfacts requests therefore were not attempted. The checked-in 15-row fixture uses issuer symbols only as labels and states that every value is synthetic. The example selects an available SEC snapshot or this labelled fixture, writes its chosen source into `results.json`, and creates a CCC chart. Current results and chart use the fixture, not issuer financial figures. Tests never download data; the optional observed-snapshot test skips when absent.

## Share repurchases and forecast integration

`buyback.py` tracks authorization, spend, remaining capacity, assumed retired shares and average price. The execution plan respects budget, authorization and the standard daily volume cap. The checker screens solicited primary-session purchases in one consolidated-market security, aggregating issuer and affiliated purchases supplied in the blotter.

Verified October 21, 2026 against [17 CFR 240.10b-18 on eCFR, paragraphs (a)(1) and (b)(1)-(4)](https://www.ecfr.gov/current/title-17/chapter-II/part-240/section-240.10b-18): use one broker daily; exclude the opening regular-way purchase; exclude the final 10 minutes when ADTV value is at least USD 1 million and float at least USD 150 million, otherwise 30 minutes. Both principal and execution market closes apply. Price cannot exceed the higher independent bid or last independent trade. Standard volume is at most 25% of average daily volume in the four full prior calendar weeks. One qualified weekly block can replace that day's cap only when no other purchase occurs that day; exclude that block volume from later ADTV calculations.

Block eligibility is an explicit caller verification input, including paragraph (a)(5)'s conditions; a block flag alone is insufficient. Supply prior same-week block dates and complete prior trading-volume history. The checker does not determine legal eligibility, solicitation exceptions, non-consolidated quotes, after-hours conditions, market-wide suspension alternatives, holidays or reporting duties. After-hours input returns an out-of-scope finding. Findings identify missed safe-harbor conditions; they do not determine a statutory violation. Synthetic security values and execution inputs are **illustrative**.

Retired shares equal spend/price. Full-year pro forma EPS equals $[net\ income-spend\times cash\ yield\times(1-tax)]/[shares-spend/price]$. Immediate retirement, full-year interest loss and unchanged operating income are illustrative assumptions. `forecast.treasury_flows` adds planned buyback outflows and both legs of center settlements to the existing 13-week forecast. Center cash belongs to E1; settlement legs cancel at group level. Buybacks reduce group cash. These are planned flows, not additional forecast copies of the deliberately defective execution blotter.

## Corporate tools results from the executed example

All figures below are synthetic and illustrative. Values come from [corporate results](examples/output/corporate_tools/results.json). Rebuild the example to refresh outputs after changing configuration.

| Measure | Actual synthetic output |
| :- | -: |
| Gross invoices / gross USD value | 150 / 4,161,932.68 |
| Bilateral payments / USD value | 6 / 575,555.44 |
| Center settlement wires / payer funding, USD | 4 / 464,878.55 |
| Value reduction | 88.8302% |
| FX conversions avoided / converted USD volume avoided | 197 / 5,272,444.69 |
| Estimated savings, USD | 7,137.96 |
| Physical / notional / standalone 90-day interest, USD | 6,726.96 / 7,143.02 / 2,543.35 |
| Interest allocation residual, USD | -0.00000000000364 |
| Bank statements / exception findings | 18 / 15 |
| Billed fees / net service charges, USD | 128,403.60 / 48,195.87 |
| Synthetic fixture rows / mean CCC, days | 15 / 45.5042 |
| Discount APR | 37.2449% |
| Buyback condition findings | 9 |
| Planned buyback spend, USD | 500,000.00 |
| Base EPS / pro forma EPS, USD | 10.0000 / 10.0859 |
| Extended final forecast cash, USD | -438,872.64 |
| LibreOffice formula comparisons | 170 passed |

The extended forecast uses fresh clean source balances and the April 1 forecast FX rate. The original baseline uses its defective-feed accepted balances and March 31 FX rate. Consequently their final cash difference includes both the USD 500,000 buyback and that valuation-date difference.

## Excel workbook

`excel/build_workbook.py` reads Python CSV/JSON outputs and creates four plain sheets: Bank Fees, CCC, Discount and Buyback Cap. ECA, credits, charges, required balances, CCC ratios, discount APR and volume caps remain live formulas. Inputs are typed values; conclusions are static Python statements refreshed by rebuilding. Workbook author metadata is set explicitly. No colored fonts, fills or tabs are applied.

`excel/reconcile_workbook.py` uses an isolated LibreOffice profile under `.pytest_tmp/`, compares all formula cells with Python within USD or ratio absolute tolerance `1e-6`, and keeps recalculated caches in the workbook. Tests also double an ECR input and verify recalculated results. At zero ECR, positive fees display "No finite balance" and zero fees require zero collected balance. The reconciliation JSON records engine availability and comparison count. All financial assumptions live in `src/mini_tms/config.py`; regulatory constants are distinguished from illustrative inputs.

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
| `src/mini_tms/config.py` | Central inputs, named data fields and illustrative/regulatory distinctions |
| `src/mini_tms/netting.py`, `pooling.py` | Monthly netting, controlled settlements, physical sweeps and loan accruals |
| `src/mini_tms/bank_fees.py` | Contract review, ECA, credit carry and cross-bank costs |
| `src/mini_tms/working_capital.py`, `buyback.py` | CCC, collections, discount economics and repurchase support |
| `src/mini_tms/reporting.py` | Portable Markdown and HTML rendering |
| `tests/` | Unit tests and hand-written statement fixtures |
| `docs/methodology.md` | Formula conventions, source sections and limitations |
| `examples/run_workflow.py` | Runnable end-to-end workflow |
| `examples/run_corporate_tools.py`, `fetch_working_capital.py` | Offline corporate workflow and separate SEC downloader |
| `excel/` | Workbook builder, live formulas, Python expectations and LibreOffice reconciliation |
| `examples/output/corporate_tools/` | Generated CSV/JSON reports, CCC chart, approved settlement XML and audit |
| `data/working_capital/` | SEC retrieval manifest and optional raw/normalized observed snapshots |
| `examples/output/` | Generated inputs and CSV, Markdown, HTML, JSON and XML outputs |

The output directory includes raw and accepted ledgers, statement balances, positions by each dimension, sweep transfers, pooling, forecast flows and balances, synthetic actuals, variances, liquidity and FX exposure. `assumptions.json` exports collection probabilities, buffers, rates, hedge bands and approval thresholds. `audit.json` records payment events. `format_reconciliation.csv` provides source-versus-parser totals and residuals.

The statement feeds are alternate encodings of the same history. Do not concatenate them into one ledger.

## Limitations

All policy bands, collection probabilities, liquidity buffers, approval thresholds and financial scenario rates are **illustrative assumptions**, not market estimates. Verified regulatory parameters are limited to the cited reserve-ratio default and the scoped Rule 10b-18 conditions above.

The parsers support documented subsets, not every bank dialect. BAI2 uses two decimal minor units and final daily snapshots; stateful update replacement, deletion and correction processing are omitted. MT940 accepts bare final statement bodies rather than FIN transport or interim pages. References are assumed unique per account within an ingestion batch. Missing-day checks use a supplied calendar-day schedule. Bank holiday calendars, full transaction-code catalogues and complete camt schema validation are omitted.

The published pain.001 XSD could not be retrieved through the available web transport or offline shell. No copy is bundled and no XSD conformance is claimed. The documented validator checks namespace, element order, cardinality, scalar syntax, dates, identities, currencies, counts, sums and the approved payload. Direct SWIFT documentation was inaccessible; MT940 semantics were checked against the bank's original specification. Full SWIFT conformance remains unverified.

Netting estimates illustrative wire and FX costs; other scenarios omit transaction costs. The model omits bank limits, legal pooling eligibility, set-off and cross-guarantee analysis, taxes on pooling, FX basis and credit risk, stochastic forecasting and invoice-level hedge maturity matching. Netting assumes enforceable obligations, available FX liquidity and prefunded center operations; it does not model funding timing or principal risk. Maker-checker roles are supplied identities, not authentication. Payment state and audit storage are in memory, without concurrent processing, persistence, digital signatures or bank acknowledgements. Exported payment files are synthetic output only.

MIT license. See [LICENSE](LICENSE).
