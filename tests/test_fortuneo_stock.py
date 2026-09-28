"""Tests for the Fortuneo stock account importer.

The fixture is the synthetic Fortuneo Bourse export in
synthetic/fortuneo-bourse/ (see its README.md for the file format and the
ground truth).

Two levels of testing:

- Parsing tests (green today): the importer must identify the export and
  extract one transaction per data row, with dates, narrations and
  amounts intact. These cover what the current importer already gets
  right and act as a regression net.
- The acceptance test (xfail for now): combining the scaffolding of
  expected.beancount (options, opens, seed transaction, balance
  assertions) with the importer's raw output must pass `bean-check`.
  It documents the known defects of the current importer (placeholder
  stock account, wrong sale direction, noise rows, dropped currency,
  ...). It is marked strict so that fixing the importer turns it into
  an XPASS failure, at which point the marker should be removed.
"""

from datetime import date
from pathlib import Path
import re
import zipfile

import pytest

from beancount import loader
from beancount.core.number import D
from beancount.parser import printer

from beancount_fortuneo import StockAccountImporter

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "synthetic" / "fortuneo-bourse"
BASENAME = "HistoriqueOperationsBourse_000000000000_du_01_07_2025_au_30_06_2026"

ZIP_PATH = str(FIXTURE_DIR / f"{BASENAME}.zip")

IMPORTER = StockAccountImporter("Actifs:Fortuneo:PEA:Cash", "Dépenses:FraisBancaires:Courtage")

ALL_ROW_DATES = {
    date(2025, 7, 2),
    date(2025, 8, 4),
    date(2025, 9, 18),
    date(2025, 11, 30),
    date(2026, 1, 12),
    date(2026, 3, 5),
    # The noise cluster of 2026-04-26 (two Détachement rows and one ANNUL.).
    date(2026, 4, 26),
    date(2026, 4, 28),
    date(2026, 6, 15),
}


def test_identify_zip():
    assert IMPORTER.identify(ZIP_PATH)


def test_identify_rejects_wrong_header(tmp_path):
    path = tmp_path / "HistoriqueOperationsBourse_x.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("HistoriqueOperationsBourse_x.csv", "foo;bar\n")

    assert not IMPORTER.identify(str(path))


def test_extract_parses_all_rows():
    entries = IMPORTER.extract(ZIP_PATH)

    assert entries is not None
    assert len(entries) == 11
    assert {e.date for e in entries} == ALL_ROW_DATES


def test_extract_narration_convention():
    """Narrations must follow the "<libellé> - <Opération>" convention."""

    entries = IMPORTER.extract(ZIP_PATH)
    narrations = {e.narration for e in entries}

    assert "BETA INDUSTRIES - Achat Comptant" in narrations
    assert ("ACME CORP - ANNUL. OST de création de coupons - Détachement coupon optionnel" in narrations)
    for entry in entries:
        assert " - " in entry.narration


def test_extract_buy_row_amounts():
    """Field-level parsing, on the one-decimal BETA buy (04/08/2025)."""

    entries = IMPORTER.extract(ZIP_PATH)
    (txn, ) = [e for e in entries if e.narration == "BETA INDUSTRIES - Achat Comptant"]
    assert txn.date == date(2025, 8, 4)

    by_account = {p.account: p for p in txn.postings}
    stock = by_account["Assets:Stock:STK"]

    assert stock.units.number == D("10")
    assert stock.units.currency == "STK"
    assert stock.cost.number == D("88.9")
    assert by_account["Dépenses:FraisBancaires:Courtage"].units.number == D("1.5")
    assert by_account["Actifs:Fortuneo:PEA:Cash"].units.number == D("-890.5")


def _scaffolding() -> str:
    """The non-transaction parts of expected.beancount: the option block,
    the open directives, the seed transaction and the final balances."""

    lines = (FIXTURE_DIR / "expected.beancount").read_text(encoding="utf-8").splitlines()

    out = []
    in_seed = False
    for line in lines:
        if line.startswith("option "):
            out.append(line)
        elif re.match(r"\d{4}-\d{2}-\d{2} open ", line):
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


@pytest.mark.xfail(
    reason=("The importer does not yet match the ground truth (see "
            "synthetic/fortuneo-bourse/README.md): placeholder stock account and "
            "commodity, wrong sale direction, dividend/OST rows touching the share "
            "position, noise rows imported, USD posted as EUR, one-decimal amounts."),
    strict=True,
)
def test_extract_matches_ground_truth(tmp_path):
    """Acceptance test: importer output + expected.beancount scaffolding
    must produce a ledger that passes bean-check (the balance assertions
    force correct share positions and cash flows)."""

    output = "\n\n".join(printer.format_entry(e) for e in IMPORTER.extract(ZIP_PATH))
    ledger = tmp_path / "ledger.beancount"
    ledger.write_text(_scaffolding() + "\n\n" + output + "\n", encoding="utf-8")

    _, errors, _ = loader.load_file(str(ledger))
    assert not errors
