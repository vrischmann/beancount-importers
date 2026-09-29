"""Tests for the resolution of Fortuneo labels onto ledger commodities.

The Bourse export identifies a security only by its libellé free text, so a
commodity directive declares the label it is known as and the importer joins
on it. These tests pin down what happens when the broker's wording and the
ledger's metadata disagree: what may be accepted automatically, and what must
abort with the candidates quoted instead of guessing.

Everything here is invented. The tickers and labels are shaped like the real
failure patterns without being anyone's portfolio:

- ACME    exact label, and the DS suffix a rights-issue row carries
- BETA    a name: only, no fortuneo-label
- EDVF    an accented company name whose account leaf is the trading mnemonic
- GLOB    \
- GLOBW   / two look-alike funds, one with a stale name: the trap case
- MNEM    an account named after a mnemonic the broker never prints
- TOTALE  a label the broker prints without its space: near-certain text, still tier 4
- DATED   a name: that beancount parses as a date rather than text
"""

from datetime import date
import logging
from pathlib import Path
import zipfile

import pytest

from beancount import loader
from beancount.core import data
from beancount.core.number import D

from beancount_fortuneo import (
    StockAccountImporter,
    UnknownSecurityError,
    _identity_tokens,
    _label_tokens,
    _normalize_label,
    _score_label,
)

LEDGER = """
option "name_assets" "Actifs"
option "name_liabilities" "Passifs"
option "name_expenses" "Dépenses"
option "name_income" "Revenu"
option "name_equity" "Capital"
option "operating_currency" "EUR"

2025-01-01 open Actifs:Fortuneo:PEA:Cash                    EUR
2025-01-01 open Actifs:Fortuneo:PEA:UC:Actions:ACME         ACME
2025-01-01 open Actifs:Fortuneo:PEA:UC:Actions:BETA         BETA
2025-01-01 open Actifs:Fortuneo:PEA:UC:Trackers:EDVF        EDVF
2025-01-01 open Actifs:Fortuneo:PEA:UC:Trackers:GLOB        GLOB
2025-01-01 open Actifs:Fortuneo:PEA:UC:Trackers:GLOBW       GLOBW
2025-01-01 open Actifs:Fortuneo:PEA:UC:Actions:MNEM         MNEM
2025-01-01 open Dépenses:FraisBancaires:Courtage
2025-01-01 open Revenu:UC:Actions:ACME

2025-01-01 commodity ACME
  fortuneo-label: "ACME CORP"
2025-01-01 commodity BETA
  name: "BETA INDUSTRIES"
2025-01-01 commodity EDVF
  name: "Énergie Véhicules France"
2025-01-01 commodity GLOB
  name: "AMUNDI PEA MONDE (MSCI World) UCITS ETF"
2025-01-01 commodity GLOBW
  name: "LYXOR MSCI WOR PEA"
2025-01-01 commodity MNEM
  name: "MNEM"
"""


AMBIGUOUS_LEDGER = LEDGER.replace(
    '2025-01-01 commodity GLOB\n  name: "AMUNDI PEA MONDE (MSCI World) UCITS ETF"',
    '2025-01-01 commodity GLOB\n  name: "AMUNDI PEA MONDE LARGE UCITS ETF"',
).replace(
    '2025-01-01 commodity GLOBW\n  name: "LYXOR MSCI WOR PEA"',
    '2025-01-01 commodity GLOBW\n  name: "AMUNDI PEA MONDE SMALL UCITS ETF"',
)

SPACING_LEDGER = LEDGER + """
2025-01-01 open Actifs:Fortuneo:PEA:UC:Actions:TOTALE       TOTALE

2025-01-01 commodity TOTALE
  fortuneo-label: "TOTAL ENERGIES"
"""

DATED_NAME_LEDGER = LEDGER + """
2025-01-01 open Actifs:Fortuneo:PEA:UC:Actions:DATED        DATED

2025-01-01 commodity DATED
  fortuneo-label: "DATED CORP"
  name: 2025-01-01
"""


def importer(fuzzy=False):
    return StockAccountImporter(
        "Actifs:Fortuneo:PEA:Cash",
        "Dépenses:FraisBancaires:Courtage",
        "Actifs:Fortuneo:PEA:UC",
        "Revenu:UC",
        fuzzy=fuzzy,
    )


@pytest.fixture
def entries():
    entries, errors, _ = loader.load_string(LEDGER)
    assert not errors

    return entries


def resolve(label, entries, fuzzy=False):
    return importer(fuzzy=fuzzy)._security_lookup(entries)(label)


# --- The helpers the scoring is built from -------------------------------------

def test_normalize_folds_case_accents_and_punctuation():
    assert _normalize_label("élan, cat.") == "ELAN CAT"
    assert _normalize_label("ACME-CORP (PA)") == "ACME CORP PA"
    assert _normalize_label("  Compagnie de la Voie ") == "COMPAGNIE DE LA VOIE"


def test_token_dropping_keeps_a_label_meaningful():
    tokens = _label_tokens(_normalize_label("AMUNDI PEA MONDE UCITS ETF"))
    assert _identity_tokens(tokens) == {"AMUNDI", "MONDE"}

    # A label made of nothing but wrapper words must not reduce to nothing.
    assert _identity_tokens({"UCITS", "ETF"}) == {"UCITS", "ETF"}


def test_score_is_one_only_for_the_same_text():
    assert _score_label("ACME CORP", "acme corp") == (1.0, "exact after normalisation")
    assert _score_label("BETA", "BETA INDUSTRIES")[1] == "token containment"
    assert _score_label("TOTALLY OTHER", "BETA INDUSTRIES")[0] < 0.5


# --- What may be resolved automatically ----------------------------------------

def test_exact_label_resolves(entries):
    security = resolve("ACME CORP", entries)

    assert security.ticker == "ACME"
    assert security.account == "Actifs:Fortuneo:PEA:UC:Actions:ACME"
    assert security.income_account == "Revenu:UC:Actions:ACME"


def test_case_and_accents_do_not_matter(entries):
    """The two labels that differ only in how the text is written must resolve."""

    assert resolve("acme corp", entries).ticker == "ACME"
    assert resolve("ENERGIE VEHICULES FRANCE", entries).ticker == "EDVF"


def test_name_metadata_alone_is_a_label(entries):
    """A commodity carrying only name: still declares its broker label."""

    assert resolve("BETA INDUSTRIES", entries).ticker == "BETA"


def test_account_leaf_is_a_label_too(entries):
    """The broker prints the trading mnemonic where the ledger stores a
    company name: the leaf of the account name bridges that."""

    assert resolve("EDVF", entries).ticker == "EDVF"


def test_rights_issue_suffix_resolves(entries):
    """A rights issue is printed with a trailing DS. It is the same security,
    and beancount allows only one label per commodity, so the suffix has to be
    seen through."""

    assert resolve("ACME CORP DS", entries).ticker == "ACME"
    assert resolve("beta industries", entries).ticker == "BETA"


def test_operation_text_is_not_part_of_a_label(entries):
    """The operation lives in its own column, so the importer never sees it as
    part of a label — and a label that only shares two words with a commodity
    stays untrusted."""

    with pytest.raises(UnknownSecurityError, match="BETA INDUSTRIES - Souscription"):
        resolve("BETA INDUSTRIES - Souscription avec droit", entries)


def test_automatic_match_is_reported(caplog, entries):
    """Anything decided without an exact declaration says so in the log, with
    the metadata line that would make it official."""

    with caplog.at_level(logging.WARNING):
        resolve("ACME CORP DS", entries)

    message = caplog.text
    assert "ACME CORP DS" in message
    assert "ACME" in message
    assert 'fortuneo-label: "ACME CORP DS"' in message


# --- What must abort -----------------------------------------------------------

def test_look_alike_funds_are_not_told_apart(entries):
    """The dangerous case: the true commodity carries a stale name, and a
    neighbour fund matches better. Guessing here files shares in the wrong
    account while the cash posting stays correct, so nothing else complains."""

    label = "Amundi MSCI World Swap - UCITS ETF - EUR (D) DIS"

    with pytest.raises(UnknownSecurityError) as excinfo:
        resolve(label, entries)

    # The stale-but-similar neighbour is what a matcher would have picked.
    assert "GLOB" in str(excinfo.value)
    assert "GLOBW" in str(excinfo.value)


def test_share_classes_of_one_fund_stay_ambiguous(entries):
    """Two share classes whose names differ by one word match a shared label
    equally well; the importer refuses to choose."""

    entries, errors, _ = loader.load_string(AMBIGUOUS_LEDGER)
    assert not errors

    with pytest.raises(UnknownSecurityError, match="AMUNDI PEA MONDE UCITS ETF") as excinfo:
        resolve("AMUNDI PEA MONDE UCITS ETF", entries)

    assert "GLOB" in str(excinfo.value)
    assert "GLOBW" in str(excinfo.value)


def test_near_identical_text_still_needs_the_opt_in():
    """A label printed without its space scores 0.96 against the declared one —
    near-certain to a human eye, but still tier 4: without fuzzy the import
    aborts with the candidates quoted rather than decide on plain text."""

    entries, errors, _ = loader.load_string(SPACING_LEDGER)
    assert not errors

    with pytest.raises(UnknownSecurityError) as excinfo:
        resolve("TOTALENERGIES", entries)

    assert "TOTALE" in str(excinfo.value)
    assert "0.96" in str(excinfo.value)

    assert resolve("TOTALENERGIES", entries, fuzzy=True).ticker == "TOTALE"


def test_unknown_security_names_the_label_and_the_fix(entries):
    with pytest.raises(UnknownSecurityError) as excinfo:
        resolve("TITAN MINERALS PLC", entries)

    message = str(excinfo.value)
    assert "'TITAN MINERALS PLC'" in message
    assert "fortuneo-label" in message


def test_hopeless_label_names_no_ticker(entries):
    """Pointing at a commodity that barely resembles the label would send the
    operator to edit the wrong directive."""

    with pytest.raises(UnknownSecurityError) as excinfo:
        resolve("TITAN MINERALS PLC", entries)

    assert "commodity <TICKER>" in str(excinfo.value)
    assert "TITAN MINERALS" in str(excinfo.value)


def test_non_string_metadata_is_ignored_rather_than_fatal():
    """Beancount parses a bare date in metadata as a date, not as text. A
    wording that is not a string cannot be matched and must not take the whole
    lookup down with it."""

    entries, errors, _ = loader.load_string(DATED_NAME_LEDGER)
    assert not errors

    with pytest.raises(UnknownSecurityError, match="TITAN MINERALS PLC"):
        resolve("TITAN MINERALS PLC", entries)


# --- The opt-in loosening ------------------------------------------------------

def test_fuzzy_flag_accepts_the_similarity_tier(entries):
    """With fuzzy=True the weak tier decides as well — and on the look-alike
    funds it decides wrong, which is why it stays off by default."""

    label = "Amundi MSCI World Swap - UCITS ETF - EUR (D) DIS"

    assert resolve(label, entries, fuzzy=True).ticker == "GLOB"


def test_fuzzy_flag_still_refuses_to_pick_between_ties(entries):
    entries, errors, _ = loader.load_string(AMBIGUOUS_LEDGER)
    assert not errors

    with pytest.raises(UnknownSecurityError):
        resolve("AMUNDI PEA MONDE UCITS ETF", entries, fuzzy=True)


# --- End to end through extract() ----------------------------------------------

def _write_export(path: Path, rows):
    """Write a synthetic export zip with the given data rows."""

    content = ";".join(StockAccountImporter.FIELDS) + "\n"
    content += "".join(";".join((*row, "")) + "\n" for row in rows)

    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("HistoriqueOperationsBourse_x.csv", content.encode("iso-8859-1"))


def test_extract_resolves_a_suffixed_label_end_to_end(tmp_path, entries):
    """A rights-issue row whose label no commodity declares resolves onto the
    share account instead of aborting the import."""

    path = tmp_path / "HistoriqueOperationsBourse_ds.zip"
    _write_export(path, [("ACME CORP DS", "OST de création avec ou sans droits - Souscription avec droit",
                          "Euronext Paris", "01/07/2025", "5.0", "0.0", "0.0", "0.0", "0.0", "EUR")])

    (txn, ) = importer().extract(str(path), entries)

    assert txn.date == date(2025, 7, 1)
    (stock, ) = txn.postings
    assert stock.account == "Actifs:Fortuneo:PEA:UC:Actions:ACME"
    assert stock.units == data.Amount(D("5.00"), "ACME")
    assert stock.cost.number == D("0.00")


def test_extract_aborts_on_an_unrelated_label(tmp_path, entries):
    path = tmp_path / "HistoriqueOperationsBourse_unknown.zip"
    _write_export(path, [("TITAN MINERALS PLC", "Achat Comptant", "Euronext Paris",
                          "01/07/2025", "1.0", "1.0", "-1.0", "0.0", "-1.0", "EUR")])

    with pytest.raises(UnknownSecurityError, match="TITAN MINERALS PLC"):
        importer().extract(str(path), entries)
