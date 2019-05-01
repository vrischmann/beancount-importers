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

FIELDS = [
    "Date opération",
    "Date valeur",
    "libellé",
    "Débit",
    "Crédit",
    "",
]

av_re = re.compile("vrst (fortuneo vie|symphonis-vie)")


class InvalidFormatError(Exception):
    pass


class InvalidZipArchive(Exception):
    pass


@contextmanager
def archive_file(f):
    if f.name.endswith('.csv'):
        fd = open(f.name, encoding='iso-8859-1')
        try:
            yield fd
        finally:
            fd.close()

        return

    with zipfile.ZipFile(f.name, 'r') as zf:
        for name in zf.namelist():
            if name.startswith('HistoriqueOperations') and name.endswith(
                    '.csv'):
                with zf.open(name) as f:
                    yield io.TextIOWrapper(f, encoding='iso-8859-1')


class Importer(importer.ImporterProtocol):
    def __init__(self, checking_account, av_account, **kwargs):
        self.checking_account = checking_account
        self.av_account = av_account
        self.invert_posting = kwargs.get("invert_posting", False)

    def identify(self, f):
        def check_fields(f):
            rd = csv.reader(f, delimiter=';')
            for row in rd:
                if set(row) != set(FIELDS):
                    print("invalid header")
                    return False

                break

            return True

        with archive_file(f) as f:
            return check_fields(f)

    def _make_posting(self, account, amount=None):
        return data.Posting(account, amount, None, None, None, None)

    def extract(self, f):
        entries = []

        with archive_file(f) as fd:
            rd = csv.reader(fd, delimiter=';')

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

                if len(row) != 5 and len(row) != 6:
                    continue

                # Extract data

                row_date = datetime.strptime(row[0], "%d/%m/%Y")
                label = row[2]

                txn_amount = row[3]
                if txn_amount == '':
                    txn_amount = row[4]
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
                if av_re.match(label):
                    second_account = self.av_account

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
