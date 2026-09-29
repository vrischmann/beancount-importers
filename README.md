# Beancount Importers

This repository contains importers for [Beancount](https://github.com/beancount/beancount) which are capable of processing CSV exports from two french banks:

* [Fortuneo](https://fortuneo.fr)
* [Crédit Mutuel](https://creditmutuel.fr)

This is _not_ a tool that automically fetches CSV exports from the bank: you need to download the CSV files yourself.

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

