"""Tests for the Fortuneo stock account importer.

The fixture is the synthetic Fortuneo Bourse export in
synthetic/fortuneo-bourse/ (see its README.md for the file format and the
ground truth).

The importer resolves securities against the ledger passed to extract(),
so the tests load expected.beancount — it doubles as a miniature ledger:
its commodity directives carry the fortuneo-label metadata and its open
directives under the assets root map tickers to accounts.

- Parsing tests pin down the extraction of every row type (buys, sells,
  dividends, share creations) and the skipping of the noise cluster.
- The acceptance test combines the scaffolding of expected.beancount
  (options, commodities, opens, seed transaction, balance assertions)
  with the importer's raw output and requires it to pass bean-check.
"""

from datetime import date
from pathlib import Path
import re
import zipfile

import pytest

from beancount import loader
from beancount.core import data
from beancount.core.number import D
from beancount.parser import printer

from beancount_fortuneo import StockAccountImporter, UnknownOperationError, UnknownSecurityError

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "synthetic" / "fortuneo-bourse"
BASENAME = "HistoriqueOperationsBourse_000000000000_du_01_07_2025_au_30_06_2026"

ZIP_PATH = str(FIXTURE_DIR / f"{BASENAME}.zip")
CSV_PATH = str(FIXTURE_DIR / f"{BASENAME}.csv")

IMPORTER = StockAccountImporter(
    "Actifs:Fortuneo:PEA:Cash",
    "Dépenses:FraisBancaires:Courtage",
    "Actifs:Fortuneo:PEA",
    "Revenu",
)

ALL_ROW_DATES = {
    date(2025, 7, 2),
    date(2025, 8, 4),
    date(2025, 9, 18),
    date(2025, 11, 30),
    date(2026, 1, 12),
    date(2026, 3, 5),
    date(2026, 4, 28),
    date(2026, 6, 15),
}


@pytest.fixture
def existing():
    """The fixture ledger (expected.beancount) as the existing entries."""

    entries, errors, _ = loader.load_file(str(FIXTURE_DIR / "expected.beancount"))
    assert not errors

    return entries


def test_identify_zip():
    assert IMPORTER.identify(ZIP_PATH)


def test_identify_csv():
    assert IMPORTER.identify(CSV_PATH)


def test_identify_rejects_wrong_header(tmp_path):
    path = tmp_path / "HistoriqueOperationsBourse_x.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("HistoriqueOperationsBourse_x.csv", "foo;bar\n")

    assert not IMPORTER.identify(str(path))


def test_extract_requires_ledger():
    with pytest.raises(ValueError, match="-e"):
        IMPORTER.extract(ZIP_PATH)


def test_extract_parses_all_rows(existing):
    entries = IMPORTER.extract(ZIP_PATH, existing)

    assert entries is not None
    assert len(entries) == 8
    assert {e.date for e in entries} == ALL_ROW_DATES


def test_extract_skips_noise_cluster(existing):
    """The Détachement/ANNUL. rows of 26/04/2026 must not produce entries."""

    entries = IMPORTER.extract(ZIP_PATH, existing)
    narrations = {e.narration for e in entries}

    assert "ACME CORP - OST de création de coupons - Détachement coupon optionnel" not in narrations
    assert "ACME CORP - ANNUL. OST de création de coupons - Détachement coupon optionnel" not in narrations
    assert not [e for e in entries if e.date == date(2026, 4, 26)]


def test_extract_no_payee(existing):
    """The importer sets no payee on any transaction."""

    for entry in IMPORTER.extract(ZIP_PATH, existing):
        assert entry.payee is None


def test_extract_buy_row(existing):
    """Field-level parsing, on the one-decimal BETA buy (04/08/2025)."""

    entries = IMPORTER.extract(ZIP_PATH, existing)
    (txn, ) = [e for e in entries if e.narration == "BETA INDUSTRIES - Achat Comptant"]
    assert txn.date == date(2025, 8, 4)

    stock, fees, cash = txn.postings
    assert stock.account == "Actifs:Fortuneo:PEA:UC:Actions:BETA"
    assert stock.units == data.Amount(D("10.00"), "BETA")
    assert stock.cost.number == D("88.90")
    assert stock.cost.currency == "EUR"
    assert stock.cost.date == date(2025, 8, 4)

    assert fees.account == "Dépenses:FraisBancaires:Courtage"
    assert fees.units == data.Amount(D("1.50"), "EUR")

    assert cash.account == "Actifs:Fortuneo:PEA:Cash"
    assert cash.units == data.Amount(D("-890.50"), "EUR")


def test_extract_sell_row(existing):
    """Sales debit shares at a price, with no cost basis (05/03/2026)."""

    entries = IMPORTER.extract(ZIP_PATH, existing)
    (txn, ) = [e for e in entries if e.narration == "ACME CORP - Vente comptant"]

    stock, fees, cash = txn.postings
    assert stock.account == "Actifs:Fortuneo:PEA:UC:Actions:ACME"
    assert stock.units == data.Amount(D("-50.00"), "ACME")
    assert stock.cost is None
    assert stock.price == data.Amount(D("14.00"), "EUR")

    assert fees.units == data.Amount(D("2.00"), "EUR")
    assert cash.units == data.Amount(D("698.00"), "EUR")


def test_extract_dividend_row(existing):
    """Dividends touch only income and cash, never the share position."""

    entries = IMPORTER.extract(ZIP_PATH, existing)
    (txn, ) = [e for e in entries if e.narration == "ACME CORP - Encaissement coupons intérêt/dividende"]

    income, cash = txn.postings
    assert income.account == "Revenu:UC:Actions:ACME"
    assert income.units == data.Amount(D("-25.00"), "EUR")
    assert cash.account == "Actifs:Fortuneo:PEA:Cash"
    assert cash.units == data.Amount(D("25.00"), "EUR")


@pytest.mark.parametrize(
    "narration,account,ticker,quantity",
    [
        ("BETA INDUSTRIES - OST de création avec ou sans droits - Attribution automatique", "Actifs:Fortuneo:PEA:UC:Actions:BETA", "BETA", "3"),
        ("DELTA SA - OST de création avec ou sans droits - Souscription avec droit", "Actifs:Fortuneo:PEA:UC:Actions:DELTA", "DELTA", "250"),
    ],
)
def test_extract_share_creation_row(existing, narration, account, ticker, quantity):
    """Share creations produce a single zero-cost posting."""

    entries = IMPORTER.extract(ZIP_PATH, existing)
    (txn, ) = [e for e in entries if e.narration == narration]

    (stock, ) = txn.postings
    assert stock.account == account
    assert stock.units == data.Amount(D(quantity), ticker)
    assert stock.cost.number == D("0.00")
    assert stock.price is None


def _write_export(path: Path, rows):
    """Write a synthetic export zip with the given data rows."""

    content = ";".join(StockAccountImporter.FIELDS) + "\n"
    content += "".join(";".join((*row, "")) + "\n" for row in rows)

    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("HistoriqueOperationsBourse_x.csv", content.encode("iso-8859-1"))


def test_extract_unknown_label_raises(tmp_path, existing):
    """Securities missing from the ledger abort the extraction."""

    path = tmp_path / "HistoriqueOperationsBourse_unknown.zip"
    _write_export(path, [("UNKNOWN CORP", "Achat Comptant", "Euronext Paris", "01/07/2025", "1.0", "1.0", "-1.0", "0.0", "-1.0", "EUR")])

    with pytest.raises(UnknownSecurityError, match="UNKNOWN CORP"):
        IMPORTER.extract(str(path), existing)


def test_extract_unknown_operation_raises(tmp_path, existing):
    """Operation types the importer does not know abort the extraction."""

    path = tmp_path / "HistoriqueOperationsBourse_op.zip"
    _write_export(path, [("ACME CORP", "Transfert Kind", "Euronext Paris", "01/07/2025", "1.0", "1.0", "-1.0", "0.0", "-1.0", "EUR")])

    with pytest.raises(UnknownOperationError, match="Transfert Kind"):
        IMPORTER.extract(str(path), existing)


def _scaffolding() -> str:
    """The non-transaction parts of expected.beancount: the option block,
    the commodity and open directives, the seed transaction and the final
    balances."""

    lines = (FIXTURE_DIR / "expected.beancount").read_text(encoding="utf-8").splitlines()

    out = []
    in_seed = False
    for line in lines:
        if line.startswith("option "):
            out.append(line)
        elif re.match(r"\d{4}-\d{2}-\d{2} (commodity|open) ", line):
            out.append(line)
        elif line.startswith('2025-07-01 * "VIR S/ PEA"'):
            in_seed = True
            out.append(line)
        elif in_seed:
            if not line.strip():
                in_seed = False
            else:
                out.append(line)
        elif re.match(r"\d{4}-\d{2}-\d{2} balance ", line):
            out.append(line)

    return "\n".join(out)


def test_extract_matches_ground_truth(tmp_path, existing):
    """Acceptance test: importer output + expected.beancount scaffolding
    must produce a ledger that passes bean-check (the balance assertions
    force correct share positions and cash flows)."""

    output = "\n\n".join(printer.format_entry(e) for e in IMPORTER.extract(ZIP_PATH, existing))
    ledger = tmp_path / "ledger.beancount"
    ledger.write_text(_scaffolding() + "\n\n" + output + "\n", encoding="utf-8")

    _, errors, _ = loader.load_file(str(ledger))
    assert not errors
