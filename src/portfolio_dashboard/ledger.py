"""FIFO journal of the owner's transactions per ISIN (ADR 0012); pure, no I/O.

Quantity, remaining cost and realised gain/loss are derived from the journal,
never maintained by hand. An inconsistent journal makes only the affected ISIN
unavailable; other holdings stay usable. Warnings name rows, never cell values.
"""

from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum

from portfolio_dashboard.models import DataWarning


class TransactionKind(Enum):
    BUY = "Kauf"
    SAVINGS_PLAN = "Sparplan"
    TRANSFER_IN = "Einbuchung"
    SALE = "Verkauf"
    TRANSFER_OUT = "Ausbuchung"


INFLOWS = frozenset(
    {TransactionKind.BUY, TransactionKind.SAVINGS_PLAN, TransactionKind.TRANSFER_IN}
)


@dataclass(frozen=True, slots=True)
class Transaction:
    """One journal row. ``amount_eur`` is the cost of an inflow including fees or
    the proceeds of a sale before taxes; a transfer-in with ``reference`` takes
    over the FIFO cost of the referenced transfer-out instead."""

    txn_id: str
    day: date
    isin: str
    kind: TransactionKind
    quantity: Decimal
    source_row: int
    amount_eur: Decimal | None = None
    reference: str | None = None
    broker_ref: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, TransactionKind):
            raise TypeError("kind must be TransactionKind")
        if not isinstance(self.quantity, Decimal) or not self.quantity > 0:
            raise ValueError("quantity must be a positive Decimal")
        if self.amount_eur is not None and (
            not isinstance(self.amount_eur, Decimal) or self.amount_eur < 0
        ):
            raise ValueError("amount_eur must be a non-negative Decimal or None")


@dataclass(frozen=True, slots=True)
class Holding:
    isin: str
    quantity: Decimal
    remaining_cost_eur: Decimal
    realized_eur: Decimal


@dataclass(frozen=True, slots=True)
class Ledger:
    holdings: Mapping[str, Holding]
    invalid: frozenset[str]
    warnings: tuple[DataWarning, ...] = ()


def _warning(code: str, message: str, row: int) -> DataWarning:
    return DataWarning(code, message, "transaktionen", row)


def build_ledger(transactions: Sequence[Transaction]) -> Ledger:
    """Apply the journal in (day, txn_id) order with FIFO lots per ISIN."""
    warnings: list[DataWarning] = []
    invalid: set[str] = set()
    ids: dict[str, list[Transaction]] = defaultdict(list)
    refs: dict[str, list[Transaction]] = defaultdict(list)
    for txn in transactions:
        ids[txn.txn_id].append(txn)
        if txn.broker_ref is not None:
            refs[txn.broker_ref].append(txn)
    for groups, code, message in (
        (ids, "duplicate_txn_id", "txn_id mehrfach vergeben; Bestand dieser ISIN nicht verfügbar."),
        (refs, "duplicate_broker_ref", "broker_ref mehrfach vergeben; Bestand dieser ISIN nicht verfügbar."),
    ):
        for group in groups.values():
            if len(group) > 1:
                for txn in group:
                    invalid.add(txn.isin)
                    warnings.append(_warning(code, message, txn.source_row))

    lots: dict[str, deque[list[Decimal]]] = defaultdict(deque)
    realized: dict[str, Decimal] = defaultdict(Decimal)
    transfers_out: dict[str, tuple[str, Decimal]] = {}
    used_refs: set[str] = set()

    def fail(txn: Transaction, code: str, message: str) -> None:
        invalid.add(txn.isin)
        warnings.append(_warning(code, message, txn.source_row))

    for txn in sorted(transactions, key=lambda t: (t.day, t.txn_id)):
        if txn.isin in invalid:
            continue
        if txn.kind in INFLOWS:
            if txn.kind is TransactionKind.TRANSFER_IN and txn.reference is not None:
                source = transfers_out.get(txn.reference)
                if source is None or txn.reference in used_refs:
                    fail(txn, "invalid_reference",
                         "bezug_txn_id verweist auf keine frühere, freie Ausbuchung.")
                    continue
                used_refs.add(txn.reference)
                cost = source[1]
            elif txn.amount_eur is None:
                fail(txn, "missing_amount", "betrag_eur fehlt; Bestand dieser ISIN nicht verfügbar.")
                continue
            else:
                cost = txn.amount_eur
            lots[txn.isin].append([txn.quantity, cost])
            continue

        if txn.kind is TransactionKind.SALE and txn.amount_eur is None:
            fail(txn, "missing_amount", "betrag_eur fehlt; Bestand dieser ISIN nicht verfügbar.")
            continue
        held = sum((lot[0] for lot in lots[txn.isin]), Decimal(0))
        if txn.quantity > held:
            fail(txn, "negative_holding",
                 "Abgang übersteigt den Bestand; Bestand dieser ISIN nicht verfügbar.")
            continue
        remaining, cost = txn.quantity, Decimal(0)
        while remaining > 0:
            lot = lots[txn.isin][0]
            take = min(remaining, lot[0])
            part = lot[1] if take == lot[0] else lot[1] * take / lot[0]
            cost += part
            lot[0] -= take
            lot[1] -= part
            remaining -= take
            if lot[0] == 0:
                lots[txn.isin].popleft()
        if txn.kind is TransactionKind.SALE:
            realized[txn.isin] += txn.amount_eur - cost
        else:
            transfers_out[txn.txn_id] = (txn.isin, cost)

    # A transfer-out nobody took over (e.g. a knock-out) is a realised loss.
    for txn_id, (isin, cost) in transfers_out.items():
        if txn_id not in used_refs:
            realized[isin] -= cost

    holdings = {}
    for isin in dict.fromkeys(t.isin for t in transactions):
        if isin in invalid:
            continue
        holdings[isin] = Holding(
            isin,
            sum((lot[0] for lot in lots[isin]), Decimal(0)),
            sum((lot[1] for lot in lots[isin]), Decimal(0)),
            realized[isin],
        )
    return Ledger(holdings, frozenset(invalid), tuple(warnings))
