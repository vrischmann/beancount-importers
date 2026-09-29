# Synthetic CCM checking-account export — importer fixture

This directory contains a fully synthetic reproduction of a Crédit Mutuel
(CCM) checking-account statement export — the `<account number>.csv`
files downloaded from the bank — for the `beancount_ccm` importer.

No real data is included: payees, merchants, amounts, dates and the
account/loan/card numbers are invented or zeroed. The importer is a thin
format importer; payee and counterparty prediction (Smart Importer) and
the booking conventions built on top of it live in the ledger project
and are deliberately out of scope here.

## Files

| File | Purpose |
| ---- | ------- |
| `00000000001.csv` | The raw export, in the exact real format (see below). |
| `current_output.beancount` | Output of the current `beancount_ccm.Importer` on the CSV. |
| `generate_sample.py` | Regenerates both files. Run with `uv run generate_sample.py`. |

## The raw file format

Faithful to the real export (the generator reads the header fields from
`beancount_ccm.Importer.FIELDS` to guarantee `identify()` passes):

- **Encoding**: ISO-8859-15 (Latin-9) — NOT UTF-8. Accents appear in the
  header (`Débit`, `Crédit`, `Libellé`) and the degree sign in the Etalis
  labels (`N°001`). The importer opens the file with this encoding.
- **Line terminators**: CRLF, with a trailing CRLF (as in the real
  export).
- **Delimiter**: `;`, 6 columns:
  `Date;Date de valeur;Débit;Crédit;Libellé;Solde`.
- **Dates**: `DD/MM/YYYY`, twice. `Date` is the booking date — the one
  the importer uses for the transaction date. `Date de valeur` is the
  value date and may differ: the loan row is booked one day *after* its
  value date, the Etalis row one day *before*, and the monthly bank fee
  carries the 1st of the month as value date.
- **Amounts**: French decimal comma, two decimals. A debit is a negative
  amount in the `Débit` column with an empty `Crédit` column
  (`-26,99;;`); a credit the other way around (`;;2950,00;`).
- **`Solde`**: the running account balance after the row, always two
  decimals. The whole column is arithmetically consistent with the
  opening balance of the statement — the final value is what the
  importer turns into its `balance` assertion.
- **Rows sorted by `Date`.**
- **File name**: real exports are named after the account number; zeroed
  here (`00000000001.csv`). `identify()` only looks at the extension and
  the header.

## What the importer does

`beancount_ccm.Importer` is a thin format importer (see
`current_output.beancount`): one transaction per row, no payee,
narration = `Libellé`, a single explicit posting on `Actifs:CCM:Courant`,
plus a `balance` assertion on that account dated the day after the last
row, with the final `Solde`.

## Libellé shapes and date quirks

The libellés mimic the real label shapes: `PRLV SEPA ...` direct debits,
`PAIEMENT CB/PSC ...` card payments, `VIR`/`VIR INST`/`VIR SEPA`/`VIR DE`
transfers, `ECH PRET ...` loan échéances and `F COTIS ...` bank fees.
A few rows deliberately differ between booking date and value date (see
the format above) so the importer's use of the booking date is
exercised.

## Checking

`uv run pytest` (at the repo root) covers the importer against this
fixture: `identify()` on extension and header, one transaction per data
row with dates, narrations and signed amounts intact, the Latin-9
encoding round-trip, and the trailing balance assertion, which must
match the final `Solde` of the CSV.

## Wiring (context)

- Importer: `beancount_ccm.Importer("Actifs:CCM:Courant")`.
