from beancount.core import data
from beancount.core.number import D
from typing import List, Iterable
import csv


def identify(rd: Iterable[str], dialect: str, fields: List[str]) -> bool:
    rd = csv.reader(rd, dialect=dialect)
    for row in rd:
        if set(row) != set(fields):
            return False

        break

    return True


def make_posting(account, amount=None):
    return data.Posting(account, amount, None, None, None, None)


def parse_amount(s):
    s = s.replace(",", ".")
    return data.Amount(D(s), "EUR")
