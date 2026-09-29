# Synthetic Fortuneo "Bourse" export — importer test fixture

This directory contains a fully synthetic reproduction of a Fortuneo stock
account (PEA) export — the `HistoriqueOperationsBourse_*.csv` / `.zip`
files — as a test fixture for the `StockAccountImporter` of
`beancount_fortuneo`.

No real data is included: companies, tickers, amounts, and dates are
invented. The account names mirror the structure of the target beancount v3
ledger (French roots) so the expected output is directly representative.

## Files

| File | Purpose |
| ---- | ------- |
| `HistoriqueOperationsBourse_000000000000_du_01_07_2025_au_30_06_2026.csv` | The raw export, in the exact real format (see below). |
| `HistoriqueOperationsBourse_000000000000_du_01_07_2025_au_30_06_2026.zip` | Same CSV wrapped in a zip, the form the bank sends and the importer's `identify()` accepts. |
| `expected.beancount` | **Ground truth**: self-contained ledger that must pass `bean-check`. One section per data row, with the convention each row must follow. |
| `generate_sample.py` | Regenerates the CSV/zip. Run with `uv run generate_sample.py`. |

## The raw file format

Faithful to the real export (the generator reads the header fields from
`beancount_fortuneo.StockAccountImporter.FIELDS` to guarantee a match):

- **Encoding**: ISO-8859-1 (NOT UTF-8).
- **Delimiter**: `;`, 11 columns, the 11th is empty (every line ends with `;`).
- **Header**: `libellé;Opération;Place;Date;Qté;Prix d'éxé;Montant brut;Courtage/Prélèvement;Montant net;Devise;`
  (columns: 0 label, 1 operation, 2 venue, 3 date, 4 quantity, 5 execution
  price, 6 gross amount, 7 commission/levy, 8 net amount, 9 currency).
- **Date**: `DD/MM/YYYY`.
- **Zip**: the inner file name starts with `HistoriqueOperations` and ends
  with `.csv`; `identify()` only accepts `.zip` paths.
- **Amounts**: printed with "natural" decimals — `0.0`, `-2.0`, `-890.5`
  (one decimal when the value has one) as well as `12.34`, `-445.78`.
  Debits (buys, commissions) are negative, credits (sales, dividends)
  positive. `Montant net` = `Montant brut` + `Courtage/Prélèvement`.
- **Rows sorted by date, descending.**

## Operation types and expected treatment

Ticker mapping (label → ticker → account):

- `ACME CORP` → `ACME` → `Actifs:Fortuneo:PEA:UC:Actions:ACME`
- `BETA INDUSTRIES` → `BETA` → `Actifs:Fortuneo:PEA:UC:Actions:BETA`
- `DELTA SA` → `DELTA` → `Actifs:Fortuneo:PEA:UC:Actions:DELTA`
- `GAMMA WORLD UCITS ETF - Acc EUR ACC` → `GWETF` →
  `Actifs:Fortuneo:PEA:UC:Trackers:GWETF` (ETFs go under `Trackers:`)

| Opération | Expected beancount entry |
| --------- | ------------------------ |
| `Achat Comptant` | Shares credited at unit price in the account above; `Dépenses:FraisBancaires:Courtage` + commission; `Actifs:Fortuneo:PEA:Cash` − net amount. |
| `Vente comptant` | Shares **debited** at the sale price (no cost basis in the posting, e.g. `-50 ACME @ 14.00 EUR`); commission expensed; `Actifs:Fortuneo:PEA:Cash` + net amount. |
| `Encaissement coupons intérêt/dividende` | `Revenu:UC:Actions:<TICKER>` + `Actifs:Fortuneo:PEA:Cash` + gross amount. **No share posting** — the quantity column is the number of shares that received the dividend, not shares acquired. |
| `OST de création avec ou sans droits - Souscription avec droit` / `... - Attribution automatique` (all amounts `0.0`) | Bonus shares: single posting `<qty> <TICKER> {0 EUR, <date>}`, no other postings. |
| `OST de création de coupons - Détachement coupon optionnel` and `ANNUL. OST de création de coupons ...` | **Ignored.** See "The noise cluster" below. |

Narration convention: `<libellé> - <Opération>` (e.g.
`"ACME CORP - Achat Comptant"`). The importer sets no payee.

### The noise cluster (important)

The 2026-04-26 rows mirror a real-world quirk of this export: for one
dividend, the file emits an "OST de création de coupons - Détachement
coupon optionnel" row (the coupon "detachment", here in both EUR and USD)
and then an `ANNUL.` row cancelling the USD one — while the actual cash
only ever arrives through the `Encaissement coupons intérêt/dividende` row
(2026-04-28 here). Bookkeeping the OST rows (let alone the ANNUL row)
double-counts or invents money. The net real effect of the whole cluster
is the `Encaissement` row alone, which is why the importer skips the
`OST de création de coupons` / `ANNUL. OST` rows entirely.

## Ground truth

`expected.beancount` contains the `option` block, `commodity` directives
(carrying the `fortuneo-label` metadata the importer resolves broker
labels against), `open` lines, one expected transaction per importable
row (with a comment citing the source row), comments for the ignored
rows, and final `balance` assertions:

```
2026-06-16 balance Actifs:Fortuneo:PEA:Cash                     2476.03 EUR
2026-06-16 balance Actifs:Fortuneo:PEA:UC:Actions:ACME              50 ACME
2026-06-16 balance Actifs:Fortuneo:PEA:UC:Actions:BETA              13 BETA
2026-06-16 balance Actifs:Fortuneo:PEA:UC:Actions:DELTA           250 DELTA
2026-06-16 balance Actifs:Fortuneo:PEA:UC:Trackers:GWETF          200 GWETF
```

## Checking

`uv run pytest` (at the repo root) covers the importer against this
fixture: parsing tests per row type, plus the acceptance test
(`test_extract_matches_ground_truth` in `tests/test_fortuneo_stock.py`)
which builds a ledger from the scaffolding lines of `expected.beancount`
(the `option` block, the `commodity` and `open` lines, the seed
transaction, and the final `balance` lines) plus the importer's raw
output for the zip, and requires `bean-check` to pass. The balance
assertions force correct share positions and cash flows, so any wrong
posting, dropped row or wrong direction makes the check fail.

## Wiring (context)

- Importer:
  `beancount_fortuneo.StockAccountImporter(
      "Actifs:Fortuneo:PEA:Cash",
      "Dépenses:FraisBancaires:Courtage",
      "Actifs:Fortuneo:PEA",   # assets root to scan
      "Revenu",                # income root
  )`.
- Securities are resolved from the ledger passed to `extract()` (the
  `-e` ledger of the extraction CLI): `commodity` directives carry the
  broker label in `fortuneo-label` metadata, `open` directives under
  the assets root map tickers to accounts, and income accounts are the
  asset accounts with the assets root replaced by the income root.
