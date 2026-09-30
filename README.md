# Beancount Importers

Importers for [Beancount](https://github.com/beancount/beancount) v3 (via
[beangulp](https://github.com/beancount/beangulp)) that process CSV exports
from two French banks:

* [Crédit Mutuel](https://creditmutuel.fr) — checking account statements
* [Fortuneo](https://fortuneo.fr) — checking account and stock account (Bourse) statements

This is _not_ a tool that automatically fetches CSV exports from the bank:
you need to download the CSV files yourself.

## Installation

Requires Python 3.10+ and a [beancount](https://pypi.org/project/beancount/) v3 ledger.
Install from GitHub into your ledger project:

```sh
uv add git+ssh://git@github.com/vrischmann/beancount-importers.git
# or
pip install git+https://github.com/vrischmann/beancount-importers.git
```

## Usage

Configure the importers in your ledger's import script and let
[beangulp](https://beancount.github.io/beangulp/) drive them:

```python
import beangulp

from beancount_ccm import Importer as CcmImporter
from beancount_fortuneo import CheckingAccountImporter, StockAccountImporter

importers = [
    CcmImporter("Actifs:CreditMutuel:Courant"),
    CheckingAccountImporter("Actifs:Fortuneo:Courant"),
    StockAccountImporter(
        "Actifs:Fortuneo:PEA:Cash",              # cash account settled by trades
        "Dépenses:FraisBancaires:Courtage",      # broker fees
        assets_root="Actifs:Fortuneo:PEA:UC",    # open directives under this root map tickers to accounts
        income_root="Revenu:UC",                 # income accounts are derived from asset accounts by swapping the roots
    ),
]

ingest = beangulp.Ingest(importers)

if __name__ == "__main__":
    ingest()
```

Run it with beangulp's CLI:

```sh
# Check that a downloaded file is recognized, without importing it.
uv run import.py identify ~/Downloads

# Extract transactions into your ledger.
uv run import.py extract -e ledger.beancount ~/Downloads
```

The `-e` ledger is mandatory for the Fortuneo stock importer: securities
are resolved against the `commodity` and `open` directives of your ledger
(see [below](#resolving-a-security-from-the-broker-label)).

### What each importer produces

**`beancount_ccm.Importer`** — Crédit Mutuel checking account
(`<account number>.csv`, ISO-8859-15, semicolon-separated). One
transaction per row, with the narration taken from the `Libellé` column,
plus a balance assertion dated the day after the last row, checked
against the final `Solde` of the statement.

**`beancount_fortuneo.CheckingAccountImporter`** — Fortuneo checking
account (the `HistoriqueOperations_*.zip` archive). One transaction per
row, narration from the `libellé` column.

**`beancount_fortuneo.StockAccountImporter`** — Fortuneo stock account
(the `HistoriqueOperationsBourse_*.zip` archive, or the plain CSV). It
understands these `Opération` types:

| Opération                                  | Entry produced |
| ------------------------------------------ | -------------- |
| `Achat Comptant`                           | Shares credited at cost, broker fees expensed, cash account debited with the net amount |
| `Vente comptant`                           | Shares debited at the sale price, broker fees expensed, cash account credited with the net amount |
| `Encaissement coupons intérêt/dividende`   | Income account credited, cash account debited (no share posting) |
| `OST de création avec ou sans droits ...`  | Bonus shares booked at a zero cost basis, single posting |
| Anything else (e.g. `OST de création de coupons`, `ANNUL. OST`) | Skipped as bookkeeping noise |

## Resolving a security from the broker label

The Fortuneo Bourse export identifies a security by nothing but its `libellé`
free text: there is no ticker column and no ISIN column. So a `commodity`
directive declares the label it is known as, and the importer joins on it:

```beancount
2021-02-11 commodity WALLIX
  fortuneo-label: "WALLIX"        ; what the bank prints
  name: "Wallix"                  ; what you want to see in Fava

2021-02-11 open Actifs:Fortuneo:PEA:UC:Actions:WALLIX  WALLIX
```

A commodity is matchable by any of three wordings: the `fortuneo-label` it
declares, its `name`, and the leaf of the account opened for it under the
assets root — `Actions:WALLIX` makes `WALLIX` a wording too.

The join runs in tiers, and stops at the first one that decides:

| tier | match | decides on its own |
| ---- | ----- | ------------------ |
| 1 | the label, character for character | yes |
| 2 | equal once case, accents and punctuation are folded (`airbus` = `AIRBUS`) | yes |
| 3 | one label's words inside the other's, wrapper noise removed (`STELLANTIS NV` ⊃ `Stellantis`, `ACME CORP DS` ⊃ `ACME CORP`) | yes, above `CONTAINMENT_MINIMUM` |
| 4 | text similarity | only with `fuzzy=True` |

Every automatic decision beyond tier 1 is logged as a warning carrying the
`fortuneo-label` line that would make it official, so a working import still
ends with the ledger made explicit.

What is *not* accepted is a guess between two candidates: the best label must
stand `AMBIGUITY_MARGIN` above the runner-up **ticker**. That guard exists for a
reason. Shares filed under the wrong fund leave the cash posting correct and the
transaction balanced, so nothing downstream necessarily notices. With `fuzzy=True`
on a real ledger, the label `Amundi MSCI World Swap - UCITS ETF - EUR (D) DIS`
lands on a neighbouring world-index fund 40 points clear of the runner-up —
confidently, and wrongly. Keep the flag off unless you review every resolution.

When nothing can decide, the import aborts and quotes the candidates, ranked:

```
no commodity directive in the ledger declares the label 'Amundi CAC 40 UCITS ETF - EUR DIS'. Add a fortuneo-label metadata entry carrying it, e.g.
  commodity CAC
    fortuneo-label: "Amundi CAC 40 UCITS ETF - EUR DIS"

Closest candidates in the ledger:
  CAC      0.65 similarity               'LYXOR ETF CAC 40'
  DCAM     0.56 similarity               'AMUNDI PEA MONDE (MSCI World) UCITS ETF'
```

A label no text can reach is one where the ledger stores the trading mnemonic
instead of a name — `HO` for Thales, `RNO` for Renault, `ALO` for Alstom.
No matcher and no model closes that gap; `name:` holding the real company name
does, and makes Fava nicer besides.

## Development

The project uses [uv](https://docs.astral.sh/uv/) and [just](https://github.com/casey/just):

```sh
just test          # run the test suite (uv run pytest)
```

The test fixtures are fully synthetic reproductions of the bank exports, in
`synthetic/ccm-courant/` and `synthetic/fortuneo-bourse/`. Each directory has
its own README describing the exact file format and the expected output; the
fixtures are regenerated with `uv run generate_sample.py` from within them.

## License

[MIT](LICENSE)
