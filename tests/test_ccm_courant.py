"""Tests for the Crédit Mutuel checking account importer.

The fixture is the synthetic CCM statement in synthetic/ccm-courant/
(see its README.md). The importer is a thin format importer: one
transaction per row on the account, plus a balance assertion dated the
day after the last row. The payee/counterparty prediction of the real
pipeline is Smart Importer territory and lives in the ledger project,
so the tests here cover the raw extraction only:

- identify() on extension and header;
- one transaction per data row, with dates, narrations and signed
  amounts intact (French decimal comma, Débit/Crédit columns);
- the Latin-9 encoding round-trips;
- the trailing balance assertion, cross-checked against the final
  Solde of the CSV.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from beancount.core import data
from beancount.core.number import D

from beancount_ccm import Importer

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "synthetic" / "ccm-courant"
CSV_PATH = str(FIXTURE_DIR / "00000000001.csv")

IMPORTER = Importer("Actifs:CCM:Courant")

ALL_ROW_DATES = {date(2025, 11, d) for d in (3, 4, 5, 6, 7, 8, 9, 10, 12, 15, 18, 22, 24, 25, 26, 30)}


def test_identify_csv():
    assert IMPORTER.identify(CSV_PATH)


def test_identify_rejects_other_extensions():
    assert not IMPORTER.identify(str(FIXTURE_DIR / "00000000001.zip"))
    assert not IMPORTER.identify(str(FIXTURE_DIR / "00000000001.txt"))


def test_identify_rejects_wrong_header(tmp_path):
    path = tmp_path / "00000000001.csv"
    path.write_text("foo;bar\n", encoding="iso-8859-15")

    assert not IMPORTER.identify(str(path))


def test_extract_one_transaction_per_row():
    """24 data rows, one transaction each, in booking-date order."""

    entries = IMPORTER.extract(CSV_PATH)
    txns = [e for e in entries if isinstance(e, data.Transaction)]

    assert len(txns) == 24
    assert len(entries) == 25  # plus the balance assertion
    assert {t.date for t in txns} == ALL_ROW_DATES
    assert [t.date for t in txns] == sorted(t.date for t in txns)


def test_extract_single_posting_without_payee():
    for entry in IMPORTER.extract(CSV_PATH):
        if not isinstance(entry, data.Transaction):
            continue

        assert entry.payee is not None and not entry.payee  # no payee ("" today)
        assert entry.narration

        (posting, ) = entry.postings
        assert posting.account == "Actifs:CCM:Courant"


def test_extract_amount_signs():
    """Debits come from the Débit column, credits from Crédit, with the
    French decimal comma parsed (first and last credit/debit rows)."""

    entries = IMPORTER.extract(CSV_PATH)
    by_narration = {t.narration: t for t in entries if isinstance(t, data.Transaction)}

    (debit, ) = by_narration["PRLV SEPA ACME TELECOM VOTRE ABONNEMENT MOBILE: 06XXXX"].postings
    assert debit.units == data.Amount(D("-26.99"), "EUR")

    (credit, ) = by_narration["VIR GAMMA CONSULTING PP000000000004"].postings
    assert credit.units == data.Amount(D("2950.00"), "EUR")


def test_extract_latin9_encoding():
    """The degree sign of the Etalis label survives the Latin-9 round-trip."""

    entries = IMPORTER.extract(CSV_PATH)
    narrations = {t.narration for t in entries if isinstance(t, data.Transaction)}

    assert "ECH PRET CAP 01000 000000 10 UTILISATION N°001" in narrations


def test_extract_balance_assertion():
    """The last entry is a balance assertion dated the day after the last
    row, with the final Solde of the statement."""

    (last, ) = IMPORTER.extract(CSV_PATH)[-1:]
    assert isinstance(last, data.Balance)

    assert last.account == "Actifs:CCM:Courant"
    assert last.date == date(2025, 12, 1)
    assert last.amount == data.Amount(D("6591.46"), "EUR")


def test_balance_matches_final_solde():
    """The importer's assertion matches the final `Solde` of the CSV."""

    with open(CSV_PATH, encoding="iso-8859-15") as f:
        last_line = f.read().splitlines()[-1]
    solde = Decimal(last_line.split(";")[5].replace(",", "."))

    (actual, ) = IMPORTER.extract(CSV_PATH)[-1:]
    assert isinstance(actual, data.Balance)
    assert actual.amount == data.Amount(solde, "EUR")
