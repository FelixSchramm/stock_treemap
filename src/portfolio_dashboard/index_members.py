"""Fixed index members for the public start page: no Sheet, no secrets.

``index_members.csv`` is built by ``scripts/build_index_members.py`` from the
Wikipedia member lists (Nikkei 225 names from Yahoo); every Yahoo ticker was
checked once (symbol, quote type EQUITY). Only the ``TOP_N`` largest members by
market cap on ``SOURCE_DATE`` are kept, so one click loads a bounded number of
companies. Index compositions change: rerun the script after an index review,
check the diff and update ``SOURCE_DATE``.
"""

import csv
from functools import cache
from pathlib import Path

from portfolio_dashboard.indices import Constituent

MEMBERS_FILE = Path(__file__).with_name("index_members.csv")
SOURCE_DATE = "14.11.2026"
TOP_N = 50


@cache
def members() -> dict[str, tuple[Constituent, ...]]:
    """Index name -> members in file order.

    :return: Constituents per index name as used in ``market_overview.WORLD_INDICES``.
    """
    result: dict[str, list[Constituent]] = {}
    with MEMBERS_FILE.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            result.setdefault(row["index"], []).append(
                Constituent(row["unternehmen"], row["yahoo_ticker"])
            )
    return {name: tuple(rows) for name, rows in result.items()}


class FixedIndexReader:
    """``IndicesLoader`` reader backed by the fixed CSV instead of the Sheet."""

    def list_indices(self) -> tuple[str, ...]:
        """:return: Index names with fixed members."""
        return tuple(members())

    def read(self, name: str) -> tuple[Constituent, ...]:
        """:param name: Index name from ``list_indices``.
        :return: Members of that index.
        """
        return members()[name]
