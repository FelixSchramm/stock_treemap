"""Per-session Watchlist loader that is rebuilt when the calendar day changes (#85).

``venue_calendars`` is computed once when a ``WatchlistRefreshCoordinator`` is built,
so its session window does not advance in a long-lived process (docs/WATCHLIST_QUOTES.md).
A rebuild drops the coordinator's cache; the view then loads again behind a spinner.
Pure and offline: the factory decides what is built, the caller owns the clock.
"""

from collections.abc import Callable, MutableMapping
from datetime import date
from typing import Protocol

from portfolio_dashboard.watchlist_quotes import WatchlistBundle

LOADER_STATE_KEY = "own.watchlist_loader"


class WatchlistLoader(Protocol):
    """What the view needs: a coordinator, or the static demo loader."""

    def load(self, *, refresh: bool = False) -> WatchlistBundle: ...


def session_loader(
    state: MutableMapping,
    factory: Callable[[], WatchlistLoader | None],
    today: date,
) -> tuple[WatchlistLoader | None, bool]:
    """Return the session's loader, building it once per calendar day.

    :param state: Session state (``st.session_state``).
    :param factory: Builds the loader; ``None`` means no Watchlist is configured.
        A factory that raises leaves the state unchanged, so the next run retries.
    :param today: Current local date.
    :return: Tuple of (loader or None, whether it was (re)built in this call).
    """
    held = state.get(LOADER_STATE_KEY)
    if held is not None and held[0] == today:
        return held[1], False
    loader = factory()
    state[LOADER_STATE_KEY] = (today, loader)
    return loader, True
