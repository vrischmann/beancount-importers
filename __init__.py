import csv
import re

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


class Importer(importer.ImporterProtocol):
    def __init__(self, checking_account, av_account):
        self.checking_account = checking_account
        self.av_account = av_account

    def identify(self, f):
        with open(f.name, encoding='iso-8859-1') as f:
            rd = csv.reader(f, delimiter=';')
            for row in rd:
                if set(row) != set(FIELDS):
                    print("invalid header")
                    return False

                break

        return True

    def extract(self, f):
        entries = []

        with open(f.name, encoding='iso-8859-1') as fd:
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

                date = datetime.strptime(row[0], "%d/%m/%Y")
                label = row[2]

                txn_amount = row[3]
                if txn_amount == '':
                    txn_amount = row[4]
                txn_amount = txn_amount.replace(',', '.')

                # Prepare the transaction

                meta = data.new_metadata(f.name, line_index)

                txn = data.Transaction(
                    meta=meta,
                    date=date,
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
                        self.checking_account,
                        amount.Amount(D(txn_amount), 'EUR'),
                        None,
                        None,
                        None,
                        None,
                    ))

                # We can infer the other posting account here.
                if av_re.match(label):
                    account = self.av_account

                    txn.postings.append(
                        data.Posting(
                            self.av_account,
                            None,
                            None,
                            None,
                            None,
                            None,
                        ))

                entries.append(txn)

                line_index += 1

        return entries
