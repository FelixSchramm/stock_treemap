"""Manually started, read-only yfinance coverage probe for M8-02 (#71).

Never run in CI and never imported by the app. It needs the owner's private
universe file (ignored by git) and an explicit ``--confirm-live`` flag, fetches
each dataset once per instrument with pauses, and writes the per-instrument
detail report only below a ``private`` directory. Standard output carries
aggregate counts and case numbers only: no symbols, names or provider values.

    uv run --locked python scripts/fundamentals_coverage_probe.py \\
        docs/private/fundamentals_universe.json --confirm-live \\
        --output docs/private/fundamentals_coverage_<date>.md \\
        --snapshot-dir docs/private/fundamentals_snapshots

Snapshots are raw provider responses: keep them in ignored private storage only.
"""

import argparse
import json
import logging
import pickle
import time
import warnings
from datetime import date, datetime, timezone
from importlib import metadata
from pathlib import Path

from portfolio_dashboard.fundamentals_coverage import (
    ProviderSnapshot,
    aggregate,
    evaluate_instrument,
    key_values,
    load_universe,
    render_aggregate_markdown,
    render_instrument_markdown,
)

DATASETS = (
    "annual_income",
    "annual_balance",
    "annual_cashflow",
    "quarterly_income",
    "quarterly_balance",
    "quarterly_cashflow",
)
ATTRIBUTES = {
    "annual_income": "income_stmt",
    "annual_balance": "balance_sheet",
    "annual_cashflow": "cashflow",
    "quarterly_income": "quarterly_income_stmt",
    "quarterly_balance": "quarterly_balance_sheet",
    "quarterly_cashflow": "quarterly_cashflow",
}


def fetch_snapshot(symbol: str, pause: float) -> ProviderSnapshot:
    """One read per dataset; failures are recorded by exception class name only."""
    import yfinance

    ticker = yfinance.Ticker(symbol)
    failed: list[str] = []
    frames = {}
    for name in DATASETS:
        try:
            frames[name] = getattr(ticker, ATTRIBUTES[name])
        except Exception as error:
            frames[name] = None
            failed.append(f"{name}:{type(error).__name__}")
        time.sleep(pause)
    try:
        info = dict(ticker.info)
    except Exception as error:
        info = None
        failed.append(f"info:{type(error).__name__}")
    time.sleep(pause)
    try:
        shares = ticker.get_shares_full()
    except Exception as error:
        shares = None
        failed.append(f"shares_full:{type(error).__name__}")
    time.sleep(pause)
    try:
        table = ticker.get_earnings_dates(limit=25)
        earnings = (
            None
            if table is None or table.empty
            else sorted({stamp.date() for stamp in table.index})
        )
    except Exception as error:
        earnings = None
        failed.append(f"earnings_dates:{type(error).__name__}")
    # A frame that is None because the call failed is reported as such.
    failed_datasets = tuple(f.split(":")[0] for f in failed)
    return ProviderSnapshot(
        info=info,
        shares_full=shares,
        earnings_dates=earnings,
        failed_datasets=failed_datasets,
        **frames,
    )


def load_or_fetch(target, directory: Path | None, pause: float) -> ProviderSnapshot:
    """Reuse a raw private snapshot when present, so a rerun costs no provider read."""
    path = None if directory is None else directory / f"{target.case_id}.pickle"
    if path is not None and path.exists():
        with path.open("rb") as handle:
            return pickle.load(handle)  # Own file in an ignored private directory.
    snapshot = fetch_snapshot(target.symbol, pause)
    if path is not None:
        directory.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            pickle.dump(snapshot, handle)
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("universe", type=Path, help="private universe JSON")
    parser.add_argument("--confirm-live", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pause", type=float, default=1.5, help="seconds between reads")
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        help="private directory for raw snapshots; existing ones are reused, not refetched",
    )
    args = parser.parse_args()

    if "private" not in args.output.resolve().parts:
        parser.error("--output must be inside a directory named 'private'")
    if args.snapshot_dir and "private" not in args.snapshot_dir.resolve().parts:
        parser.error("--snapshot-dir must be inside a directory named 'private'")
    targets = load_universe(json.loads(args.universe.read_text(encoding="utf-8")))

    logging.getLogger("yfinance").disabled = True  # Its messages can name symbols.
    warnings.simplefilter("ignore")
    today = date.today()
    coverages, values = [], {}
    for target in targets:
        snapshot = load_or_fetch(target, args.snapshot_dir, args.pause)
        coverages.append(evaluate_instrument(target, snapshot, today))
        values[target.case_id] = key_values(snapshot)
        print(f"{target.case_id} done", flush=True)
        time.sleep(args.pause)

    summary = aggregate(coverages)
    version = metadata.version("yfinance")
    lines = [
        "# Fundamentaldaten-Abdeckung (PRIVAT, nicht committen)",
        "",
        f"- Lauf: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- yfinance: {version}; Referenzdatum der Altersprüfung: {today.isoformat()}",
        f"- Fälle: {summary['cases']}",
        "",
        "## Aggregat",
        "",
        render_aggregate_markdown(summary),
        "",
        "## Je Instrument",
        "",
    ]
    for coverage in coverages:
        lines += [render_instrument_markdown(coverage), ""]
        kv = values[coverage.target.case_id]
        lines += [
            "Kernwerte letztes Geschäftsjahr (Providereinheiten, ungerundet): "
            + json.dumps(kv, ensure_ascii=False),
            "",
        ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(render_aggregate_markdown(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
