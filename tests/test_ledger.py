"""FIFO journal (ADR 0012) with synthetic transactions only."""

from datetime import date
from decimal import Decimal

from portfolio_dashboard.ledger import Transaction, TransactionKind, build_ledger

A, B = "XX0000000001", "XX0000000002"
K = TransactionKind


def txn(txn_id, day, isin, kind, qty, amount=None, reference=None, broker_ref=None, row=2):
    return Transaction(txn_id, date(2026, 1, day), isin, kind, Decimal(str(qty)), row,
                       None if amount is None else Decimal(str(amount)), reference, broker_ref)


def test_fifo_sale_takes_oldest_lots_and_realises_gain():
    ledger = build_ledger([
        txn("T1", 1, A, K.BUY, 10, 100),
        txn("T2", 2, A, K.SAVINGS_PLAN, 10, 200),
        txn("T3", 3, A, K.SALE, 15, 300),
    ])
    holding = ledger.holdings[A]
    assert holding.quantity == 5
    assert holding.remaining_cost_eur == 100  # half of the second lot
    assert holding.realized_eur == 300 - (100 + 100)
    assert not ledger.invalid and not ledger.warnings


def test_order_is_day_then_txn_id_not_row_order():
    ledger = build_ledger([
        txn("T2", 1, A, K.SALE, 5, 60),
        txn("T1", 1, A, K.BUY, 5, 50),
    ])
    assert ledger.holdings[A].quantity == 0
    assert ledger.holdings[A].realized_eur == 10


def test_transfer_in_with_reference_takes_over_cost_without_gain_or_loss():
    ledger = build_ledger([
        txn("T1", 1, A, K.BUY, 100, 1000),
        txn("T2", 2, A, K.TRANSFER_OUT, 100),
        txn("T3", 2, B, K.TRANSFER_IN, 15, reference="T2"),
        txn("T4", 3, B, K.SALE, 5, 400),
    ])
    assert ledger.holdings[A].quantity == 0 and ledger.holdings[A].realized_eur == 0
    b = ledger.holdings[B]
    assert b.quantity == 10
    assert b.remaining_cost_eur.quantize(Decimal("0.01")) == Decimal("666.67")
    assert b.realized_eur.quantize(Decimal("0.01")) == Decimal("66.67")


def test_unreferenced_transfer_out_is_a_realised_loss():
    ledger = build_ledger([txn("T1", 1, A, K.BUY, 10, 50), txn("T2", 2, A, K.TRANSFER_OUT, 10)])
    assert ledger.holdings[A].quantity == 0
    assert ledger.holdings[A].realized_eur == -50


def test_free_allotment_has_zero_cost():
    ledger = build_ledger([txn("T1", 1, A, K.TRANSFER_IN, 500, 0), txn("T2", 2, A, K.SALE, 500, 3)])
    assert ledger.holdings[A].realized_eur == 3


def test_overselling_makes_only_that_isin_unavailable():
    ledger = build_ledger([
        txn("T1", 1, A, K.BUY, 1, 10),
        txn("T2", 2, A, K.SALE, 2, 30, row=7),
        txn("T3", 1, B, K.BUY, 3, 30),
    ])
    assert ledger.invalid == {A}
    assert A not in ledger.holdings and ledger.holdings[B].quantity == 3
    (warning,) = ledger.warnings
    assert warning.code == "negative_holding" and warning.source_row == 7


def test_missing_amounts_and_bad_references_are_rejected():
    ledger = build_ledger([
        txn("T1", 1, A, K.BUY, 1),
        txn("T2", 1, B, K.TRANSFER_IN, 1, reference="T9"),
    ])
    assert ledger.invalid == {A, B}
    assert {w.code for w in ledger.warnings} == {"missing_amount", "invalid_reference"}


def test_reference_may_be_used_only_once():
    ledger = build_ledger([
        txn("T1", 1, A, K.BUY, 2, 20),
        txn("T2", 2, A, K.TRANSFER_OUT, 2),
        txn("T3", 2, B, K.TRANSFER_IN, 1, reference="T2"),
        txn("T4", 3, B, K.TRANSFER_IN, 1, reference="T2"),
    ])
    assert B in ledger.invalid


def test_duplicate_ids_and_broker_refs_invalidate_the_affected_isins():
    ledger = build_ledger([
        txn("T1", 1, A, K.BUY, 1, 10),
        txn("T1", 2, B, K.BUY, 1, 10),
        txn("T3", 3, A, K.BUY, 1, 10, broker_ref="R1"),
    ])
    assert ledger.invalid == {A, B}
    assert all(w.code == "duplicate_txn_id" for w in ledger.warnings)
    ledger = build_ledger([
        txn("T1", 1, A, K.BUY, 1, 10, broker_ref="R1"),
        txn("T2", 2, A, K.BUY, 1, 10, broker_ref="R1"),
    ])
    assert ledger.invalid == {A}
    assert all("R1" not in w.message for w in ledger.warnings)
