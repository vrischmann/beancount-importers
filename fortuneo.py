import csv
import re
import zipfile
import io
import os.path
from contextlib import contextmanager
from datetime import datetime
from beancount.core.number import D
from beancount.core import amount
from beancount.core import flags
from beancount.core import data
from beancount.core import position
from beancount.ingest import importer
from .helpers import identify, make_posting, parse_amount

FIELDS = [
    "Date opération",
    "Date valeur",
    "libellé",
    "Débit",
    "Crédit",
    "",
]
STOCK_FIELDS = [
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

av_re = re.compile("vrst (fortuneo vie|symphonis-vie)")
normal_operations_re = re.compile("HistoriqueOperations_.+")
stock_operations_re = re.compile("HistoriqueOperationsBourse_.+")


class InvalidFormatError(Exception):
    pass


class InvalidZipArchive(Exception):
    pass


@contextmanager
def archive_file(f):
    if f.mimetype() == "text/csv":
        fd = open(f.name, encoding='iso-8859-1')
        try:
            yield fd
        finally:
            fd.close()

        return

    with zipfile.ZipFile(f.name, 'r') as zf:
        for name in zf.namelist():
            if name.startswith('HistoriqueOperations') and name.endswith('.csv'):
                with zf.open(name) as f:
                    yield io.TextIOWrapper(f, encoding='iso-8859-1')


class Importer(importer.ImporterProtocol):
    def __init__(self, checking_account, av_account, stock_account, **kwargs):
        csv.register_dialect("fortuneo", "excel", delimiter=";")

        self.checking_account = checking_account
        self.av_account = av_account
        self.stock_account = stock_account

        if "broker_fees_account" in kwargs:
            self.broker_fees_account = kwargs["broker_fees_account"]
        else:
            self.broker_fees_account = "Expenses:Fortuneo:BrokerFees"

    def identify(self, f):
        mimetype = f.mimetype()
        if mimetype not in ["text/csv", "application/zip"]:
            return False

        filename = os.path.basename(f.name)

        if normal_operations_re.match(filename):
            with archive_file(f) as f:
                return identify(f, "fortuneo", FIELDS)

        if stock_operations_re.match(filename):
            with archive_file(f) as f:
                return identify(f, "fortuneo", STOCK_FIELDS)

        return False

    def extract(self, f, existing_entries=None):
        filename = os.path.basename(f.name)

        if normal_operations_re.match(filename):
            with archive_file(f) as f:
                return self._extract_normal_operations(f.name, f)

        if stock_operations_re.match(filename):
            with archive_file(f) as f:
                return self._extract_stock_operations(f.name, f)

    def _extract_normal_operations(self, filename, rd):
        rd = csv.reader(rd, dialect="fortuneo")

        entries = []
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

            txn.postings.append(make_posting(self.checking_account, txn_amount))

            # Done

            entries.append(txn)

            line_index += 1

        return entries

    def _extract_stock_operations(self, filename, rd):
        rd = csv.reader(rd, dialect="fortuneo")

        entries = []
        header = True
        line_index = 0

        for row in rd:
            # Check header
            if header:
                if set(row) != set(STOCK_FIELDS):
                    raise InvalidFormatError()
                header = False

                line_index += 1

                continue

            if len(row) != len(STOCK_FIELDS):
                continue

            # Extract data

            row_date = datetime.strptime(row[3], "%d/%m/%Y")
            label = row[0].strip() + " - " + row[1]
            currency = row[9]

            stock_amount = data.Amount(D(row[4]), 'STK')
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

            txn.postings.append(data.Posting(account="Assets:Stock:STK", units=stock_amount, cost=stock_cost, price=None, flag=None, meta=None))
            txn.postings.append(make_posting(self.broker_fees_account, -parse_amount(row[7])))
            txn.postings.append(make_posting(self.stock_account, parse_amount(row[8])))

            # Done

            entries.append(txn)

            line_index += 1

        return entries
