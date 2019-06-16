import csv
import re
import zipfile
import io
from contextlib import contextmanager
from datetime import datetime
from beancount.core.number import D
from beancount.core import amount
from beancount.core import flags
from beancount.core import data
from beancount.ingest import importer
from .helpers import identify

FIELDS = [
    "Date",
    "Date de valeur",
    "Débit",
    "Crédit",
    "Libellé",
    "Solde",
]


@contextmanager
def open_file(f):
    fd = open(f.name, encoding='iso-8859-15')
    try:
        yield fd
    finally:
        fd.close()


class Importer(importer.ImporterProtocol):
    def __init__(self, checking_account, **kwargs):
        csv.register_dialect("ccm", "excel", delimiter=";")

        self.checking_account = checking_account
        self.invert_posting = kwargs.get("invert_posting", False)

    def identify(self, f):
        if f.mimetype() != "text/csv":
            return False

        with open_file(f) as f:
            return identify(f, "ccm", FIELDS)

    def _make_posting(self, account, amount=None):
        return data.Posting(account, amount, None, None, None, None)

    def extract(self, f, existing_entries=None):
        entries = []

        with open_file(f) as fd:
            rd = csv.reader(fd, dialect="ccm")

            header = True
            line_index = 0

            for row in rd:
                # Check header
                if header:
                    if set(row) != set(FIELDS):
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
                if txn_amount == '':
                    txn_amount = row[3]
                txn_amount = txn_amount.replace(',', '.')

                # Prepare the transaction

                meta = data.new_metadata(f.name, line_index)

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

                second_account = "Unknown"

                if self.invert_posting:
                    first_posting = self._make_posting(self.checking_account, None)
                    second_posting = self._make_posting(second_account, -amount.Amount(D(txn_amount), 'EUR'))

                    txn.postings.append(second_posting)
                    txn.postings.append(first_posting)
                else:
                    first_posting = self._make_posting(self.checking_account, amount.Amount(D(txn_amount), 'EUR'))
                    second_posting = self._make_posting(second_account)

                    txn.postings.append(first_posting)
                    txn.postings.append(second_posting)

                # Done

                entries.append(txn)

                line_index += 1

        return entries
