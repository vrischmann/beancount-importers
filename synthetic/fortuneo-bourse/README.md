# Synthetic Fortuneo "Bourse" export — importer test fixture

This directory contains a fully synthetic reproduction of a Fortuneo stock
account (PEA) export — the `HistoriqueOperationsBourse_*.csv` / `.zip`
files — for improving the `StockAccountImporter` of `beancount_fortuneo`
(part of the `beancount-importers` package, a git fork).

No real data is included: companies, tickers, amounts, and dates are
invented. The account names mirror the structure of the target beancount v3
ledger (French roots) so the expected output is directly representative.

## Files

| File | Purpose |
| ---- | ------- |
| `HistoriqueOperationsBourse_000000000000_du_01_07_2025_au_30_06_2026.csv` | The raw export, in the exact real format (see below). |
| `HistoriqueOperationsBourse_000000000000_du_01_07_2025_au_30_06_2026.zip` | Same CSV wrapped in a zip, the form the bank sends and the importer's `identify()` accepts. |
| `expected.beancount` | **Ground truth**: self-contained ledger that must pass `bean-check`. One section per data row, with the convention each row must follow. |
| `current_output.beancount` | Output of the *current* importer on the zip, to document the gap. |
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
| `Achat Comptant` | Shares credited at unit price in the account above; `Dépenses:FraisBancaires:Courtage` + commission; `Actifs:Fortuneo:PEA:Cash` − net amount. Payee `"Fortuneo"`. |
| `Vente comptant` | Shares **debited** at the sale price (no cost basis in the posting, e.g. `-50 ACME @ 14.00 EUR`); commission expensed; `Actifs:Fortuneo:PEA:Cash` + net amount. Payee `"Fortuneo"`. |
| `Encaissement coupons intérêt/dividende` | `Revenu:UC:Actions:<TICKER>` + `Actifs:Fortuneo:PEA:Cash` + gross amount. **No share posting** — the quantity column is the number of shares that received the dividend, not shares acquired. Payee `"Fortuneo"`. |
| `OST de création avec ou sans droits - Souscription avec droit` / `... - Attribution automatique` (all amounts `0.0`) | Bonus shares: single posting `<qty> <TICKER> {0 EUR, <date>}`, no other postings. No payee. |
| `OST de création de coupons - Détachement coupon optionnel` and `ANNUL. OST de création de coupons ...` | **Ignored.** See "The noise cluster" below. |

Narration convention: `<libellé> - <Opération>` (e.g.
`"ACME CORP - Achat Comptant"`).

### The noise cluster (important)

The 2026-04-26 rows mirror a real-world quirk of this export: for one
dividend, the file emits an "OST de création de coupons - Détachement
coupon optionnel" row (the coupon "detachment", here in both EUR and USD)
and then an `ANNUL.` row cancelling the USD one — while the actual cash
only ever arrives through the `Encaissement coupons intérêt/dividende` row
(2026-04-28 here). Bookkeeping the OST rows (let alone the ANNUL row)
double-counts or invents money. The net real effect of the whole cluster
is the `Encaissement` row alone. An improved importer should skip the
`OST de création de coupons` / `ANNUL. OST` rows (or, if it keeps them,
ensure they net to zero and never touch `Actifs:Fortuneo:PEA:Cash`).

## Ground truth

`expected.beancount` contains the `open`/`option` scaffolding, one expected
transaction per importable row (with a comment citing the source row),
comments for the ignored rows, and final `balance` assertions:

```
2026-06-16 balance Actifs:Fortuneo:PEA:Cash                     2476.03 EUR
2026-06-16 balance Actifs:Fortuneo:PEA:UC:Actions:ACME              50 ACME
2026-06-16 balance Actifs:Fortuneo:PEA:UC:Actions:BETA              13 BETA
2026-06-16 balance Actifs:Fortuneo:PEA:UC:Actions:DELTA           250 DELTA
2026-06-16 balance Actifs:Fortuneo:PEA:UC:Trackers:GWETF          200 GWETF
```

Validate with:

```
uv run bean-check expected.beancount   # (from the ledger project, or with a
                                       #  local venv providing beancount >= 3)
```

A natural acceptance test for an improved importer: build a ledger from
the scaffolding lines of `expected.beancount` (the `option` block, the
`open` lines, the seed transaction, and the final `balance` lines) plus
the importer's raw output for the zip, and require `bean-check` to pass.
The balance assertions force correct share positions and cash flows, so
every defect above (wrong direction, noise rows, dropped currency, …)
makes the check fail.

## What the current importer gets wrong

See `current_output.beancount` (generated with the current
`StockAccountImporter`). Concrete defects:

1. Every row posts to the placeholder `Assets:Stock:STK` with the
   placeholder commodity `STK` — no label→ticker/account mapping.
2. `Vente comptant` **credits** shares (+50) instead of debiting them.
3. Dividend and OST rows credit shares at zero cost — dividends must not
   touch the share position at all.
4. Zero-value OST rows produce a 3-posting transaction of all-zero amounts
   instead of a single `<qty> <TICKER> {0 EUR}` posting.
5. `ANNUL. OST` rows are imported as regular transactions instead of being
   ignored/netted.
6. Currency is dropped: the USD row's `28.4` is posted to the EUR cash
   account as `28.4 EUR`.
7. One-decimal amounts are emitted verbatim (`-890.5 EUR`); the target
   ledger convention writes two decimals (`-890.50 EUR`).
8. `identify()` accepts only `.zip` files, although the reader itself
   (`archive_file`) also supports standalone `.csv`.
9. No `payee` ("Fortuneo") on the importable rows.

## Wiring (context)

- Importer: `beancount_fortuneo.StockAccountImporter(
    "Actifs:Fortuneo:PEA:Cash", "Dépenses:FraisBancaires:Courtage")`.
- Wired with Smart Importer hooks `PredictPayees()` and
  `PredictPostings()` in the ledger's `extract.py`.
- Extraction CLI: `just extract <input> <output>` (runs
  `uv run ./extract.py extract -e data/ledger.beancount -o <output>
  <input>`).
