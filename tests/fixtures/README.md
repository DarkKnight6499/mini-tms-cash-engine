# Fixture provenance

`minimal_camt.xml`, `minimal.bai` and `minimal.sta` are independently hand-written synthetic fixtures. Each begins with EUR 100.00, records a receipt of EUR 50.00 and a payment of EUR 25.00, and closes at EUR 125.00. The BAI control total is 30000 minor units: opening 10000 plus closing 12500 plus unsigned details 5000 and 2500. Its physical account, group and file record counts include the narrative continuation.

No published pain.001.001.03 XSD was successfully retrieved. The payment tests use the documented structural validator and approved-payload reconciliation. See [retrieval attempts and primary sources](../../docs/methodology.md).

`working_capital.csv` contains 15 synthetic annual records across five fiscal years. HAS, WHR and KSS are labels only; no values represent issuer filings. Opening and ending AR, inventory and AP, annual revenue, COGS and period dates match the normalized SEC downloader schema. The provenance column labels every record synthetic.
