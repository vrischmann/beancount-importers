from beancount_helpers import identify, make_posting, parse_amount
from beancount.core import amount
from beancount.core import data
from beancount.core import flags
from beancount.core.number import D
from beangulp import Importer
from contextlib import contextmanager
from datetime import datetime
from datetime import timedelta
from typing import IO
import csv
import io
import re
import zipfile


@contextmanager
def open_file(filepath: str) -> IO[str]:
    """
    Context manager for opening and closing files.
    Uses the encoding "iso-8859-15" for Crédit Mutuel files.
    """

    fd = open(filepath, encoding="iso-8859-15")
    try:
        yield fd
    finally:
        fd.close()


class Importer(Importer):
    """
    Importer for Crédit Mutuel checking account statements.
    """

    FIELDS = [
        "Date",
        "Date de valeur",
        "Débit",
        "Crédit",
        "Libellé",
        "Solde",
    ]

    def __init__(self, account_name: str):
        csv.register_dialect("ccm", "excel", delimiter=";")

        self.account_name = account_name

    def identify(self, filepath: str) -> bool:
        # Bail if the extesion is not .csv
        if not filepath.endswith(".csv"):
            return False

        with open_file(filepath) as f:
            return identify(f, "ccm", self.FIELDS)

    def account(self, filepath: str) -> data.Account:
        return self.account_name

    def extract(self, filepath: str, existing_entries=None):
        entries = []

        row = None
        row_date = None

        with open_file(filepath) as fd:
            rd = csv.reader(fd, dialect="ccm")

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

                if len(row) != 6:
                    continue

                # Extract data

                row_date = datetime.strptime(row[0], "%d/%m/%Y")
                label = row[4]

                txn_amount = row[2]
                if txn_amount == "":
                    txn_amount = row[3]
                txn_amount = parse_amount(txn_amount)

                # Prepare the transaction

                meta = data.new_metadata(filepath, line_index)

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

                first_posting = make_posting(self.account_name, txn_amount)
                txn.postings.append(first_posting)

                # Done

                entries.append(txn)

                line_index += 1

        if line_index > 0:
            balance_check = data.Balance(
                meta=data.new_metadata(filepath, line_index + 1),
                date=row_date.date() + timedelta(days=1),
                account=self.account_name,
                amount=parse_amount(row[5]),
                diff_amount=None,
                tolerance=None,
            )
            entries.append(balance_check)

        return entries
