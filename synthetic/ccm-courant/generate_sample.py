#!/usr/bin/env python3
"""Generate a synthetic CCM checking-account export.

The raw CSV is byte-compatible with the real export:
- ISO-8859-15 encoding (the encoding the importer opens with), CRLF
  line terminators, ';' delimited, 6 columns, French decimal comma
- header fields are taken verbatim from the installed
  beancount_ccm.Importer.FIELDS so the importer's identify() header
  check is guaranteed to pass

All payees, merchants, amounts, dates and the account/loan/card numbers
are synthetic. Payee and counterparty prediction is Smart Importer
territory in the ledger project and deliberately out of scope here:
the fixture covers the raw format and the raw importer output only.

Writes two files next to this script:
- 00000000001.csv          the raw export
- current_output.beancount output of the current beancount_ccm Importer

Run with: uv run generate_sample.py
"""

import os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Tuple

from beancount.core.number import D
from beancount.parser import printer

from beancount_ccm import Importer as CcmImporter

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_NAME = "00000000001.csv"
CSV_PATH = os.path.join(HERE, CSV_NAME)
OUTPUT_PATH = os.path.join(HERE, "current_output.beancount")

COURANT = "Actifs:CCM:Courant"

# Opening balance of the statement (the Solde before the first row).
OPENING = D("8377.58")

# Synthetic libellés (the shapes mirror the real CCM labels).
LBL_MOBILE = "PRLV SEPA ACME TELECOM VOTRE ABONNEMENT MOBILE: 06XXXX"
LBL_FIBRE = "PRLV SEPA ACME TELECOM VOTRE ABONNEMENT FIBRE (FACTURE"
LBL_EAU = "PRLV SEPA BETA EAU CLIENTS PARTICULI DUPONT JEAN"
LBL_ELECTRICITE = "PRLV SEPA GAMMA ELECTRICITE CLIENTS PARTICULI DUPONT JEAN"
LBL_SERVICES = "PRLV SEPA OMEGA SERVICES FDSDUP0001234567"
LBL_SOFINCO = "PRLV SEPA CA CONSUMER FINANCE"
LBL_SOFINCO_GAR = "PRLV SEPA CA CONSUMER FINANCE PRELEVEMENT SEPA"
LBL_ECHEANCE_LOAN = "ECH PRET CAP+IN 01000 000000 09"
LBL_ECHEANCE_ETALIS = "ECH PRET CAP 01000 000000 10 UTILISATION N°001"
LBL_AUTOMOBILE = "AUTOMOBILE AA00000000"
LBL_ASSURANCE_HAB = "ASSURANCE HABITATION BF00000000"
LBL_FRAIS_CCM = "F COTIS EUROCOMPTE CONFORT"
LBL_REMBOURSE = "VIR DE MLE JEANNE MARTIN MLE JEANNE MARTIN"


@dataclass(frozen=True)
class Row:
    """One export row."""

    date: date  # "Date" column: the booking date (the txn date)
    value_date: date  # "Date de valeur" column (may differ from date)
    net: D  # signed: debit negative, credit positive
    label: str  # "Libellé" column


def fr(v: D) -> str:
    """French amount: -26.99 -> '-26,99'."""
    return f"{v:.2f}".replace(".", ",")


# The rows of the statement, in booking-date order.


def mobile(d: date) -> Row:
    return Row(d, d, D("-26.99"), LBL_MOBILE)


def fibre(d: date) -> Row:
    return Row(d, d, D("-57.99"), LBL_FIBRE)


def eau(d: date) -> Row:
    return Row(d, d, D("-12.41"), LBL_EAU)


def electricite(d: date) -> Row:
    return Row(d, d, D("-59.20"), LBL_ELECTRICITE)


def nettoyage(d: date, n: int) -> Row:
    return Row(d, d, D("-51.30"), f"PRLV SEPA DELTA NETTOYAGE GR PRELEV DELTA 00000000{n}")


def services(d: date) -> Row:
    return Row(d, d, D("-16.99"), LBL_SERVICES)


def sofinco_garantie(d: date) -> Row:
    return Row(d, d, D("-21.00"), LBL_SOFINCO_GAR)


def sofinco(d: date) -> Row:
    return Row(d, d, D("-128.26"), LBL_SOFINCO)


def echeance_loan(d: date) -> Row:
    # Booked one day after its value date, like the real export.
    return Row(d, d - timedelta(days=1), D("-543.21"), LBL_ECHEANCE_LOAN)


def echeance_etalis(d: date) -> Row:
    # Value date one day after the booking date.
    return Row(d, d + timedelta(days=1), D("-51.31"), LBL_ECHEANCE_ETALIS)


def frais_ccm(d: date) -> Row:
    # Booked on the 6th, value date on the 1st of the month.
    return Row(d, d.replace(day=1), D("-10.30"), LBL_FRAIS_CCM)


def automobile(d: date) -> Row:
    return Row(d, d, D("-139.45"), LBL_AUTOMOBILE)


def assurance_hab(d: date) -> Row:
    return Row(d, d, D("-51.14"), LBL_ASSURANCE_HAB)


def salaire(d: date, ref: str) -> Row:
    return Row(d, d, D("2950.00"), f"VIR GAMMA CONSULTING {ref}")


def wero(d: date, ref: str) -> Row:
    return Row(d, d, D("-500.00"), f"VIR INST WERO MLE JEANNE MARTIN AIDE {ref}")


def rembourse(d: date) -> Row:
    return Row(d, d, D("520.00"), LBL_REMBOURSE)


def mutuelle(d: date, ref: str, net: D) -> Row:
    return Row(d, d, net, f"VIR INST DELTA MUTUELLE REMBOURSEMENT SANTE {ref}")


def impot(d: date, year: str, net: D) -> Row:
    return Row(d, d, net, f"PRLV SEPA DIRECTION GENERALE DE SOLDE IMPOT REVENUS {year} N DE F")


def transfert(d: date, ref: str) -> Row:
    return Row(d, d, D("-300.00"), f"VIR SEPA DUPONT JEAN {ref}")


def card(d: date, ddmm: str, mode: str, city: str, merchant: str, net: D, ref: str, card_no: str = "8101") -> Row:
    label = (f"PAIEMENT {mode} {ddmm} {city} {merchant} "
             f"{'PAYWEB' + card_no if mode == 'CB' else 'CARTE ' + card_no} "
             f"GIR0126000{ref}")
    return Row(d, d, net, label)


def rows() -> Tuple[Row, ...]:
    return (
        mobile(date(2025, 11, 3)),
        card(date(2025, 11, 3), "0111", "CB", "CORK", "ALPHA CLOUD", D("-0.99"), "111"),
        eau(date(2025, 11, 4)),
        nettoyage(date(2025, 11, 5), 3),
        wero(date(2025, 11, 5), "0000000000000000002"),
        salaire(date(2025, 11, 5), "PP000000000004"),
        echeance_loan(date(2025, 11, 6)),
        automobile(date(2025, 11, 6)),
        assurance_hab(date(2025, 11, 6)),
        frais_ccm(date(2025, 11, 6)),
        sofinco_garantie(date(2025, 11, 7)),
        sofinco(date(2025, 11, 7)),
        electricite(date(2025, 11, 8)),
        echeance_etalis(date(2025, 11, 9)),
        services(date(2025, 11, 10)),
        card(date(2025, 11, 12), "1011", "PSC", "TOURCOING", "DELTA FASTFOOD", D("-10.79"), "112"),
        mutuelle(date(2025, 11, 15), "0000000000000000002", D("9.00")),
        card(date(2025, 11, 15), "1311", "PSC", "PARIS", "ZETA TRANSIT", D("-22.95"), "113"),
        rembourse(date(2025, 11, 18)),
        fibre(date(2025, 11, 22)),
        card(date(2025, 11, 24), "2111", "PSC", "HEM", "BETA MARKET", D("-121.24"), "114"),
        impot(date(2025, 11, 25), "2024", D("-2920.00")),
        card(date(2025, 11, 26), "2411", "CB", "PARIS 10", "ALPHA RAILWAYS", D("-219.60"), "115"),
        transfert(date(2025, 11, 30), "CG3V25112L000003"),
    )


def write_csv(rs: Tuple[Row, ...]) -> None:
    importer = CcmImporter(COURANT)
    fields = list(importer.FIELDS)
    assert fields == ["Date", "Date de valeur", "Débit", "Crédit", "Libellé", "Solde"], fields

    lines = [";".join(fields)]
    balance = OPENING
    for row in rs:
        balance += row.net
        debit = fr(row.net) if row.net < 0 else ""
        credit = fr(row.net) if row.net > 0 else ""
        lines.append(";".join([
            f"{row.date:%d/%m/%Y}",
            f"{row.value_date:%d/%m/%Y}",
            debit,
            credit,
            row.label,
            fr(balance),
        ]))
    with open(CSV_PATH, "w", encoding="iso-8859-15", newline="") as f:
        f.write("\r\n".join(lines) + "\r\n")


def write_current_output() -> None:
    importer = CcmImporter(COURANT)
    entries = importer.extract(CSV_PATH)
    body = "\n\n".join(printer.format_entry(e).rstrip("\n") for e in entries).strip()
    header = f";; -*- mode: beancount -*-\n\n**** {CSV_PATH}\n\n"
    with open(OUTPUT_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(header + body + "\n")


def main() -> None:
    rs = rows()
    write_csv(rs)
    write_current_output()

    # The importer must identify the file it will be fed.
    assert CcmImporter(COURANT).identify(CSV_PATH)

    # The final Solde equals the opening balance plus the net of every
    # row; the importer's balance assertion must match it.
    final = OPENING + sum(r.net for r in rs)
    print(f"wrote {CSV_PATH} ({len(rs)} rows)")
    print(f"wrote {OUTPUT_PATH}")
    print(f"final Solde: {final} (asserted by the importer, last row + 1 day)")


if __name__ == "__main__":
    main()
