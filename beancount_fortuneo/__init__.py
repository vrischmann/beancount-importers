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
import difflib
import enum
import io
import logging
import os.path
import re
import unicodedata
import zipfile


logger = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class LabelMatch:
    """A candidate label for a broker label, with the strength of the match."""

    ticker: str
    label: str
    score: float
    how: str


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


# --- Resolution of broker labels onto commodities ------------------------------
#
# The Bourse export has no ticker column and no ISIN column: the libellé free
# text is the only thing identifying the security. A commodity directive
# therefore declares the label it is known as, and the importer joins on it.

#: Words carrying no security identity: wrappers, share classes and legal
#: forms. Dropping them is what lets a rights-issue row printed as
#: "ACME CORP DS" resolve to the "ACME CORP" commodity. Kept short on purpose:
#: the longer the list, the more distinct funds look alike, and the ambiguity
#: guard below then refuses to guess at all.
LABEL_NOISE_TOKENS = frozenset({
    "UCITS", "ETF", "PEA", "ACC", "ACCUM", "ACCUMULATION", "ACCUR", "CAP",
    "DIST", "DISTRIBUTION", "DIS", "EUR", "USD",
    "SA", "SE", "SCA", "SCM", "PLC", "NV", "AG", "SPA", "ASA", "ADR",
    "II", "III", "IV", "DR", "THE", "DE", "LA", "LE", "DU", "DS",
})

CONTAINMENT_MINIMUM = 0.60

#: How far the best ticker must stand above the runner-up ticker. Two share
#: classes of the same fund produce high scores for both; the margin is what
#: keeps the importer from choosing one.
AMBIGUITY_MARGIN = 0.15

#: Below this, a candidate is not worth reporting in an error message.
SUGGESTION_FLOOR = 0.25


def _normalize_label(label: str) -> str:
    """Fold case, accents and punctuation so the broker's wording and the
    ledger's metadata can be compared as plain text."""

    folded = unicodedata.normalize("NFD", label.upper())
    folded = "".join(c for c in folded if unicodedata.category(c) != "Mn")

    return re.sub(r"[^A-Z0-9]+", " ", folded).strip()


def _label_tokens(normalized: str) -> set:
    """The words of a normalized label, single letters dropped."""

    return {token for token in normalized.split() if len(token) > 1}


def _identity_tokens(tokens: set) -> set:
    """The tokens of a label with the wrapper noise removed, never empty."""

    kept = tokens - LABEL_NOISE_TOKENS

    return kept or tokens


def _score_label(query: str, candidate: str) -> tuple:
    """Score how well candidate names the security of query, as (score, how)."""

    normalized_query = _normalize_label(query)
    normalized_candidate = _normalize_label(candidate)

    if normalized_query == normalized_candidate:
        return 1.0, "exact after normalisation"

    query_tokens = _identity_tokens(_label_tokens(normalized_query))
    candidate_tokens = _identity_tokens(_label_tokens(normalized_candidate))
    common = query_tokens & candidate_tokens
    union = query_tokens | candidate_tokens

    overlap = len(common) / len(union) if union else 0.0
    ratio = difflib.SequenceMatcher(None, normalized_query, normalized_candidate).ratio()

    if common and (query_tokens <= candidate_tokens or candidate_tokens <= query_tokens):
        return max(overlap, ratio * 0.95), "token containment"

    # Partial overlap: the long fund names that differ mostly in wrapper words.
    return max(ratio, overlap + (0.15 if len(common) >= 2 else 0.0)), "similarity"


class SecurityResolver:
    """Resolve a broker label onto a Security.

    An exact label match decides, as it always did. Failing that, the ledger's
    labels are scored as text and a match is accepted only when it is strong
    (exact once normalized, or a token containment) and no other ticker comes
    close. Everything weaker aborts with the ranked candidates quoted, so the
    operator adds the missing metadata instead of trusting a guess.

    With fuzzy=True the weaker similarity tier is accepted too, on the same
    uniqueness condition; each automatic decision is logged as a warning.
    """

    def __init__(self, aliases, ticker_to_account, assets_root, income_root, exact, fuzzy=False):
        self.aliases = aliases
        self.ticker_to_account = ticker_to_account
        self.assets_root = assets_root
        self.income_root = income_root
        self.exact = exact
        self.fuzzy = fuzzy

    def resolve(self, label: str) -> Security:
        """Return the Security of a broker label, or raise UnknownSecurityError."""

        ticker = self.exact.get(label)
        if ticker is not None:
            return self._security(ticker, label)

        best = self.suggest(label)
        if best:
            (top, ) = best[:1]
            runner_up = next((m.score for m in best[1:] if m.ticker != top.ticker), 0.0)

            strong = (
                top.how == "exact after normalisation"
                or (top.how == "token containment" and top.score >= CONTAINMENT_MINIMUM)
                or (self.fuzzy and top.how == "similarity")
            )
            if strong and top.score - runner_up >= AMBIGUITY_MARGIN:
                logger.warning(
                    "resolved the label %r as %s (%s, score %.2f); make it official with a "
                    'fortuneo-label: "%s" metadata entry on commodity %s',
                    label, top.ticker, top.how, top.score, label, top.ticker,
                )

                return self._security(top.ticker, label)

        raise self._failure(label, best)

    def suggest(self, label: str) -> list:
        """Rank the ledger labels closest to a broker label, best ticker first."""

        scored = []
        for ticker, labels in self.aliases.items():
            for candidate in labels:
                score, how = _score_label(label, candidate)
                if score >= SUGGESTION_FLOOR:
                    scored.append(LabelMatch(ticker=ticker, label=candidate, score=score, how=how))

        by_ticker = {}
        for match in scored:
            previous = by_ticker.get(match.ticker)
            if previous is None or match.score > previous.score:
                by_ticker[match.ticker] = match

        return sorted(by_ticker.values(), key=lambda match: -match.score)

    def _security(self, ticker, label) -> Security:
        account = self.ticker_to_account.get(ticker)
        if account is None:
            raise UnknownSecurityError(f"commodity {ticker} (label {label!r}) has no open account under {self.assets_root}")

        income_account = self.income_root + account[len(self.assets_root):]

        return Security(ticker=ticker, account=account, income_account=income_account)

    def _failure(self, label, suggestions) -> UnknownSecurityError:
        # Naming a ticker is only helpful when one really looks like the label;
        # a 0.3 match would point the operator at the wrong commodity.
        probable = suggestions[0] if suggestions and suggestions[0].score >= CONTAINMENT_MINIMUM else None

        message = (
            f"no commodity directive in the ledger declares the label {label!r}. "
            "Add a fortuneo-label metadata entry carrying it, e.g."
            f"\n  commodity {probable.ticker if probable else '<TICKER>'}"
            f'\n    fortuneo-label: "{label}"'
        )

        if not suggestions:
            return UnknownSecurityError(message + "\n\nNo commodity label in the ledger resembles it.")

        ranked = "".join(
            f"\n  {match.ticker:8} {match.score:.2f} {match.how:24} {match.label!r}"
            for match in suggestions[:5]
        )

        return UnknownSecurityError(message + "\n\nClosest candidates in the ledger:" + ranked)


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

    A label that no commodity declares exactly is not simply lost: see
    SecurityResolver for the fallback matching, and the `fuzzy` flag for
    how far the importer may go without asking.
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

    def __init__(self, account_name: str, broker_fees_account: str, assets_root: str, income_root: str, fuzzy: bool = False):
        csv.register_dialect("fortuneo", "excel", delimiter=";")

        self.account_name = account_name
        self.broker_fees_account = broker_fees_account
        self.assets_root = assets_root
        self.income_root = income_root
        self.fuzzy = fuzzy

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
        declared: dict[str, set] = {}

        for entry in existing_entries:
            if isinstance(entry, data.Commodity):
                label = entry.meta.get("fortuneo-label") or entry.meta.get("name")
                if label is None:
                    continue

                previous = label_to_ticker.get(label)
                if previous is not None and previous != entry.currency:
                    raise UnknownSecurityError(f"label {label!r} maps to multiple commodities ({previous}, {entry.currency})")

                label_to_ticker[label] = entry.currency

                declared.setdefault(entry.currency, set()).update(
                    value for value in (entry.meta.get("fortuneo-label"), entry.meta.get("name"))
                    if isinstance(value, str) and value
                )

            elif isinstance(entry, data.Open) and entry.currencies:
                if not _is_under(entry.account, self.assets_root):
                    continue

                for ticker in entry.currencies:
                    previous = ticker_to_account.get(ticker)
                    if previous is not None and previous != entry.account:
                        raise UnknownSecurityError(f"ticker {ticker} is opened under multiple accounts "
                                                   f"({previous}, {entry.account})")

                    ticker_to_account[ticker] = entry.account

        # The leaf of the account name names the security too: the broker prints
        # the trading mnemonic (EDF) where the metadata carries the company name
        # (Electricité de France), and the other way round.
        aliases = {
            ticker: declared.get(ticker, set()) | {account.split(":")[-1]}
            for ticker, account in ticker_to_account.items()
        }

        resolver = SecurityResolver(
            aliases=aliases,
            ticker_to_account=ticker_to_account,
            assets_root=self.assets_root,
            income_root=self.income_root,
            exact=label_to_ticker,
            fuzzy=self.fuzzy,
        )

        return resolver.resolve

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
