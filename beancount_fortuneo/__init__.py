from beancount_helpers import identify, make_posting, parse_amount
from beancount.core import amount
from beancount.core import data
from beancount.core import flags
from beancount.core import position
from beancount.core.number import D
from beangulp import Importer
from contextlib import contextmanager
from datetime import datetime
from typing import IO
import csv
import io
import os.path
import re
import zipfile


class InvalidFormatError(Exception):
    """Exception raised when the format of the file is not as expected."""

    pass


class InvalidZipArchive(Exception):
    """Exception raised when the zip archive is not valid."""

    pass


@contextmanager
def archive_file(filepath: str) -> IO[str]:
    """
    Context manager for handling files from a Fortuneo export.
    Supports both standalone CSV files and CSV files within a zip archive.
    """

    if filepath.endswith("csv"):
        fd = open(filename, encoding="iso-8859-1")
        try:
            yield fd
        finally:
            fd.close()

        return

    with zipfile.ZipFile(filepath, "r") as zf:
        for name in zf.namelist():
            if name.startswith("HistoriqueOperations") and name.endswith(".csv"):
                with zf.open(name) as f:
                    yield io.TextIOWrapper(f, encoding="iso-8859-1")


class CheckingAccountImporter(Importer):
    """
    Importer for Fortuneo checking account statements.
    """

    FIELDS = [
        "Date opération",
        "Date valeur",
        "libellé",
        "Débit",
        "Crédit",
        "",
    ]

    FILENAME_RE = re.compile("HistoriqueOperations_.+")

    def __init__(self, account_name: str):
        csv.register_dialect("fortuneo", "excel", delimiter=";")

        self.account_name = account_name

    def identify(self, filepath: str) -> bool:
        if not filepath.endswith(".zip"):
            return False

        filename = os.path.basename(filepath)
        if self.FILENAME_RE.match(filename):
            with archive_file(filepath) as f:
                return identify(f, "fortuneo", self.FIELDS)

        return False

    def account(self, filepath: str) -> data.Account:
        return self.account_name

    def extract(self, filepath: str, existing_entries=None):
        filename = os.path.basename(filepath)

        if self.FILENAME_RE.match(filename):
            with archive_file(filepath) as f:
                return self._extract(f.name, f)

    def _extract(self, filename, rd):
        rd = csv.reader(rd, dialect="fortuneo")

        entries = []
        header = True
        line_index = 0

        for row in rd:
            # Check header
            if header:
                if set(row) != set(self.FIELDS):
                    raise InvalidFormatError()
                header = False

                line_index += 1

                continue

            if len(row) != 5 and len(row) != 6:
                continue

            # Extract data

            row_date = datetime.strptime(row[0], "%d/%m/%Y")
            label = row[2]

            txn_amount = row[3]
            if txn_amount == "":
                txn_amount = row[4]
            txn_amount = parse_amount(txn_amount)

            # Prepare the transaction

            meta = data.new_metadata(filename, line_index)

            txn = data.Transaction(
                meta=meta,
                date=row_date.date(),
                flag=flags.FLAG_OKAY,
                payee="",
                narration=label,
                tags=set(),
                links=set(),
                postings=[],
            )

            # Create the postings.

            txn.postings.append(make_posting(self.account_name, txn_amount))

            # Done

            entries.append(txn)

            line_index += 1

        return entries


class StockAccountImporter(Importer):
    """
    Importer for Fortuneo stock account transactions.
    """

    FIELDS = [
        "libellé",
        "Opération",
        "Place",
        "Date",
        "Qté",
        "Prix d'éxé",
        "Montant brut",
        "Courtage/Prélèvement",
        "Montant net",
        "Devise",
        "",
    ]

    FILENAME_RE = re.compile("HistoriqueOperationsBourse_.+")

    def __init__(self, account_name: str, broker_fees_account: str):
        csv.register_dialect("fortuneo", "excel", delimiter=";")

        self.account_name = account_name
        self.broker_fees_account = broker_fees_account

    def identify(self, filepath: str) -> bool:
        if not filepath.endswith(".zip"):
            return False

        filename = os.path.basename(filepath)
        if self.FILENAME_RE.match(filename):
            with archive_file(filepath) as f:
                return identify(f, "fortuneo", self.FIELDS)

        return False

    def account(self, filepath: str) -> data.Account:
        return self.account_name

    def extract(self, filepath: str, existing_entries=None):
        filename = os.path.basename(filepath)

        if self.FILENAME_RE.match(filename):
            with archive_file(filepath) as f:
                return self._extract(f.name, f)

    def _extract(self, filename, rd):
        rd = csv.reader(rd, dialect="fortuneo")

        entries = []
        header = True
        line_index = 0

        for row in rd:
            # Check header
            if header:
                if set(row) != set(self.FIELDS):
                    raise InvalidFormatError()
                header = False

                line_index += 1

                continue

            if len(row) != len(self.FIELDS):
                continue

            # Extract data

            row_date = datetime.strptime(row[3], "%d/%m/%Y")
            label = row[0].strip() + " - " + row[1]
            currency = row[9]

            stock_amount = data.Amount(D(row[4]), "STK")
            stock_cost = position.Cost(
                number=D(row[5]),
                currency=currency,
                date=row_date.date(),
                label=None,
            )

            # Prepare the transaction

            meta = data.new_metadata(filename, line_index)

            txn = data.Transaction(
                meta=meta,
                date=row_date.date(),
                flag=flags.FLAG_OKAY,
                payee="",
                narration=label,
                tags=set(),
                links=set(),
                postings=[],
            )

            # Create the postings.

            txn.postings.append(
                data.Posting(
                    account="Assets:Stock:STK",
                    units=stock_amount,
                    cost=stock_cost,
                    price=None,
                    flag=None,
                    meta=None,
                )
            )
            txn.postings.append(
                make_posting(self.broker_fees_account, -parse_amount(row[7]))
            )
            txn.postings.append(make_posting(self.stock_account, parse_amount(row[8])))

            # Done

            entries.append(txn)

            line_index += 1

        return entries
