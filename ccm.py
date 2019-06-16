import csv
import re
import zipfile
import io
from contextlib import contextmanager
from datetime import datetime
from datetime import timedelta
from beancount.core.number import D
from beancount.core import amount
from beancount.core import flags
from beancount.core import data
from beancount.ingest import importer
from .helpers import identify, make_posting, parse_amount

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

    def identify(self, f):
        if f.mimetype() != "text/csv":
            return False

        with open_file(f) as f:
            return identify(f, "ccm", FIELDS)

    def extract(self, f, existing_entries=None):
        entries = []

        row = None
        row_date = None

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
                txn_amount = _parse_amount(txn_amount)

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

                first_posting = make_posting(self.checking_account, txn_amount)
                txn.postings.append(first_posting)

                # Done

                entries.append(txn)

                line_index += 1

        if line_index > 0:
            balance_check = data.Balance(
                meta=data.new_metadata(f.name, line_index + 1),
                date=row_date.date() + timedelta(days=1),
                account=self.checking_account,
                amount=_parse_amount(row[5]),
                diff_amount=None,
                tolerance=None,
            )
            entries.append(balance_check)

        return entries
