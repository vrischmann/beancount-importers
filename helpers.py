import io
import csv


def identify(rd: io.TextIOBase, dialect: str, fields: [str]):
    rd = csv.reader(rd, dialect=dialect)
    for row in rd:
        if set(row) != set(fields):
            return False

        break

    return True
