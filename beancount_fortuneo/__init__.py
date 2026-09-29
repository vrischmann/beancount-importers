from beancount_helpers import identify, make_posting, parse_amount
from beancount.core import data
from beancount.core import flags
from beancount.core import position
from beancount.core.number import D
from beangulp import Importer
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import IO, Callable
import csv
import enum
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


class UnknownOperationError(Exception):
    """Exception raised when a row's operation type is not supported."""

    pass


class UnknownSecurityError(Exception):
    """Exception raised when a row's security cannot be resolved from the ledger."""

    pass


@dataclass(frozen=True)
class Security:
    """A security as resolved from the ledger for a given broker label."""

    ticker: str
    account: str
    income_account: str


class OperationType(enum.Enum):
    """The kinds of rows the stock account importer knows about."""

    BUY = enum.auto()
    SELL = enum.auto()
    DIVIDEND = enum.auto()
    SHARE_CREATION = enum.auto()
    NOISE = enum.auto()


TWO_PLACES = Decimal("0.01")


def _is_under(account: str, root: str) -> bool:
    """Return whether account is root itself or lies under it."""

    return account == root or account.startswith(root + ":")


def _quantize(number: Decimal) -> Decimal:
    """Round to the two decimal places of the target ledger convention."""

    return number.quantize(TWO_PLACES)


@contextmanager
def archive_file(filepath: str) -> IO[str]:
    """
    Context manager for handling files from a Fortuneo export.
    Supports both standalone CSV files and CSV files within a zip archive.
    """

    if filepath.endswith(".csv"):
        fd = open(filepath, encoding="iso-8859-1")
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

    Securities are resolved from the ledger given to extract() (the
    beangulp `-e` ledger): commodity directives carry the broker label
    in their `fortuneo-label` (or `name`) metadata and open directives
    under `assets_root` map tickers to accounts. Income accounts are
    derived from asset accounts by replacing `assets_root` with
    `income_root`.
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

    def __init__(self, account_name: str, broker_fees_account: str, assets_root: str, income_root: str):
        csv.register_dialect("fortuneo", "excel", delimiter=";")

        self.account_name = account_name
        self.broker_fees_account = broker_fees_account
        self.assets_root = assets_root
        self.income_root = income_root

    def identify(self, filepath: str) -> bool:
        if not filepath.endswith((".zip", ".csv")):
            return False

        filename = os.path.basename(filepath)
        if self.FILENAME_RE.match(filename):
            with archive_file(filepath) as f:
                return identify(f, "fortuneo", self.FIELDS)

        return False

    def account(self, filepath: str) -> data.Account:
        return self.account_name

    def extract(self, filepath: str, existing_entries=None):
        if existing_entries is None:
            raise ValueError("extract() requires the ledger entries (beangulp's -e option) "
                             "to resolve securities from the commodity and open directives")

        filename = os.path.basename(filepath)

        if self.FILENAME_RE.match(filename):
            with archive_file(filepath) as f:
                return self._extract(f.name, f, self._security_lookup(existing_entries))

    def _security_lookup(self, existing_entries) -> Callable[[str], Security]:
        """Build the label -> Security resolution from the ledger."""

        label_to_ticker: dict[str, str] = {}
        ticker_to_account: dict[str, str] = {}

        for entry in existing_entries:
            if isinstance(entry, data.Commodity):
                label = entry.meta.get("fortuneo-label") or entry.meta.get("name")
                if label is None:
                    continue

                previous = label_to_ticker.get(label)
                if previous is not None and previous != entry.currency:
                    raise UnknownSecurityError(f"label {label!r} maps to multiple commodities ({previous}, {entry.currency})")

                label_to_ticker[label] = entry.currency

            elif isinstance(entry, data.Open) and entry.currencies:
                if not _is_under(entry.account, self.assets_root):
                    continue

                for ticker in entry.currencies:
                    previous = ticker_to_account.get(ticker)
                    if previous is not None and previous != entry.account:
                        raise UnknownSecurityError(f"ticker {ticker} is opened under multiple accounts "
                                                   f"({previous}, {entry.account})")

                    ticker_to_account[ticker] = entry.account

        def resolve(label: str) -> Security:
            ticker = label_to_ticker.get(label)
            if ticker is None:
                raise UnknownSecurityError(f"no commodity directive in the ledger declares the label {label!r} "
                                           "(add one with a fortuneo-label metadata entry)")

            account = ticker_to_account.get(ticker)
            if account is None:
                raise UnknownSecurityError(f"commodity {ticker} (label {label!r}) has no open account under {self.assets_root}")

            income_account = self.income_root + account[len(self.assets_root):]

            return Security(ticker=ticker, account=account, income_account=income_account)

        return resolve

    def _classify(self, label: str, operation: str) -> OperationType:
        if operation == "Achat Comptant":
            return OperationType.BUY
        if operation == "Vente comptant":
            return OperationType.SELL
        if operation == "Encaissement coupons intérêt/dividende":
            return OperationType.DIVIDEND
        if operation.startswith("OST de création avec ou sans droits"):
            return OperationType.SHARE_CREATION
        if operation.startswith("OST de création de coupons"):
            return OperationType.NOISE
        if operation.startswith("ANNUL. OST"):
            return OperationType.NOISE

        raise UnknownOperationError(f"unsupported operation {operation!r} (label {label!r})")

    def _extract(self, filename, rd, security_lookup):
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

            row_date = datetime.strptime(row[3], "%d/%m/%Y").date()
            label = row[0].strip()
            operation = row[1]
            kind = self._classify(label, operation)

            # Bookkeeping noise around dividend events is skipped entirely.

            if kind is OperationType.NOISE:
                line_index += 1

                continue

            security = security_lookup(label)
            currency = row[9]
            quantity = _quantize(D(row[4]))
            price = _quantize(D(row[5]))
            gross = _quantize(D(row[6]))
            commission = _quantize(D(row[7]))
            net = _quantize(D(row[8]))

            # Prepare the transaction

            meta = data.new_metadata(filename, line_index)
            narration = f"{label} - {operation}"
            postings = []

            # Create the postings.

            if kind is OperationType.BUY:
                postings.append(data.Posting(account=security.account, units=data.Amount(quantity, security.ticker), cost=position.Cost(number=price, currency=currency, date=row_date, label=None), price=None, flag=None, meta=None))
            elif kind is OperationType.SELL:
                postings.append(data.Posting(account=security.account, units=data.Amount(-quantity, security.ticker), cost=None, price=data.Amount(price, currency), flag=None, meta=None))
            elif kind is OperationType.DIVIDEND:
                postings.append(make_posting(security.income_account, data.Amount(-gross, currency)))
            elif kind is OperationType.SHARE_CREATION:
                if gross != 0 or net != 0 or commission != 0:
                    raise UnknownOperationError(f"share creation with non-zero amounts: {narration!r}")

                postings.append(data.Posting(account=security.account, units=data.Amount(quantity, security.ticker), cost=position.Cost(number=D("0.00"), currency=currency, date=row_date, label=None), price=None, flag=None, meta=None))

            if kind is not OperationType.SHARE_CREATION:
                if commission != 0:
                    postings.append(make_posting(self.broker_fees_account, data.Amount(-commission, currency)))

                postings.append(make_posting(self.account_name, data.Amount(net, currency)))

            # Done

            txn = data.Transaction(
                meta=meta,
                date=row_date,
                flag=flags.FLAG_OKAY,
                payee=None,
                narration=narration,
                tags=set(),
                links=set(),
                postings=postings,
            )

            entries.append(txn)

            line_index += 1

        return entries
