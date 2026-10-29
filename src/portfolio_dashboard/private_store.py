"""Private SQLite foundation, guarded anew before every transaction.

No connection or authorization result is cached. Runtime callbacks read only
server configuration and Streamlit's validated identity; browser dictionaries
must never be supplied. Repositories may share transaction() and later append
explicit schema migrations. This module does not contact a provider or Sheet.
"""

import os
import sqlite3
import stat
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import streamlit as st

from portfolio_dashboard.auth import owner_is_authorized
from portfolio_dashboard.config import (
    LOOPBACK_ADDRESSES,
    AppConfig,
    ConfigurationError,
    Mode,
    load_config,
)

APPLICATION_ID = 0x50574442
SCHEMA_VERSION = 3
# Retired by #95: the Sheet tab is the only watchlist. Nothing reads or writes this
# table any more. It stays in the schema so existing databases keep validating and
# no stored row is ever dropped automatically; new databases get it empty.
WATCHLIST_SCHEMA = (
    "CREATE TABLE watchlist (entry_id TEXT PRIMARY KEY NOT NULL, "
    "payload TEXT NOT NULL, instrument_id TEXT UNIQUE, "
    "revision INTEGER NOT NULL CHECK (revision >= 1))"
)
# Each migration runs inside the same transaction as its version marker.
SCENARIO_SCHEMA = (
    "CREATE TABLE scenario_versions (instrument_id TEXT NOT NULL, "
    "revision INTEGER NOT NULL CHECK (revision >= 1), payload TEXT NOT NULL, "
    "PRIMARY KEY (instrument_id, revision))"
)
# M8-05: raw provider statements per verified security (JSON payload, one row).
FUNDAMENTALS_CACHE_SCHEMA = (
    "CREATE TABLE fundamentals_cache (fetch_key TEXT PRIMARY KEY NOT NULL, "
    "payload TEXT NOT NULL)"
)
MIGRATIONS = {
    1: (WATCHLIST_SCHEMA,),
    2: (SCENARIO_SCHEMA,),
    3: (FUNDAMENTALS_CACHE_SCHEMA,),
}
SCHEMAS = {
    1: [("table", "watchlist", WATCHLIST_SCHEMA)],
    2: [
        ("table", "scenario_versions", SCENARIO_SCHEMA),
        ("table", "watchlist", WATCHLIST_SCHEMA),
    ],
    3: [
        ("table", "fundamentals_cache", FUNDAMENTALS_CACHE_SCHEMA),
        ("table", "scenario_versions", SCENARIO_SCHEMA),
        ("table", "watchlist", WATCHLIST_SCHEMA),
    ],
}

ERRORS = {
    "access": "Kein Besitzerzugriff auf die private Ablage; Sitzung und Freigabe prüfen.",
    "local": "Private Ablage im lokalen Modus nur mit --server.address 127.0.0.1.",
    "path": (
        "Private Ablage nicht verfügbar. PORTFOLIO_PRIVATE_DATA_DIR als absolutes, "
        "beschreibbares eigenes Verzeichnis außerhalb von Checkout und Image konfigurieren; "
        "das Elternverzeichnis muss existieren. Es wurde kein Speichern bestätigt."
    ),
    "permissions": (
        "Private Ablage verweigert: eigenes Verzeichnis (0700) und reguläre Dateien "
        "(0600) ohne Symlinks erforderlich."
    ),
    "corrupt": "Private Datenbank beschädigt. Geschützte Sicherung wiederherstellen; keine automatische Neuerstellung.",
    "incompatible": "Private Datenbank hat ein unbekanntes oder inkompatibles Schema. Passende App-Version verwenden.",
    "busy": "Private Datenbank wird gerade verwendet. Änderung nicht gespeichert; erneut versuchen.",
    "data": "Private Datenbank enthält ungültige Daten. Geschützte Sicherung prüfen.",
}


class PrivateStorageError(ValueError):
    """Only fixed messages/codes; never paths, SQLite errors or stored values."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(ERRORS[code])


def runtime_config() -> AppConfig:
    """Read current host settings, including auth only in production."""
    auth = None
    if os.environ.get("PORTFOLIO_MODE", "demo").strip() == Mode.PRODUCTION:
        try:
            auth = st.secrets.get("auth", {})
        except Exception:
            raise ConfigurationError(
                "Produktivkonfiguration fehlt oder ist ungültig: auth."
            ) from None
    return load_config(auth_settings=auth)


def _private_regular(path: Path) -> None:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or (hasattr(os, "getuid") and info.st_uid != os.getuid())
        or (os.name == "posix" and info.st_mode & 0o177)
    ):
        raise PrivateStorageError("permissions")


def _database_path(directory: str | Path) -> tuple[Path, bool]:
    path = Path(directory)
    # No implicit default, cwd-relative path, home expansion or symlink alias.
    if not path.is_absolute():
        raise PrivateStorageError("path")
    resolved = path.resolve()
    forbidden = (
        Path(__file__).resolve().parents[2],
        Path(sys.prefix).resolve(),
        Path("/app"),
        Path("/tmp"),
        Path("/"),
    )
    if any(
        resolved == base
        or (base not in {Path("/"), Path("/tmp")} and resolved.is_relative_to(base))
        for base in forbidden
    ):
        raise PrivateStorageError("path")
    # A wheel's __file__ identifies site-packages, not the application checkout.
    # Inspect target ancestors on the server, independent of cwd. Git metadata
    # has HEAD, worktrees use .git files, and unpacked apps have both app files.
    for parent in (resolved, *resolved.parents):
        git = parent / ".git"
        if (
            git.is_file()
            or (git / "HEAD").is_file()
            or ((parent / "app.py").is_file() and (parent / "pyproject.toml").is_file())
        ):
            raise PrivateStorageError("path")
    for part in (*reversed(path.parents), path):
        if part.is_symlink():
            raise PrivateStorageError("permissions")
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or (hasattr(os, "getuid") and info.st_uid != os.getuid())
        or (os.name == "posix" and info.st_mode & 0o077)
    ):
        raise PrivateStorageError("permissions")
    database = path / "private.sqlite3"
    created = False
    try:
        fd = os.open(
            database,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except FileExistsError:
        pass
    else:
        os.close(fd)
        created = True
    _private_regular(database)
    if not created:
        # The creator can be preempted between exclusive file creation and its
        # first SQLite connection. Do not acquire SQLite's writer lock before
        # that initializer; a truncated existing file still fails closed.
        deadline = time.monotonic() + 2
        while database.stat().st_size == 0:
            if time.monotonic() >= deadline:
                raise PrivateStorageError("incompatible")
            time.sleep(0.01)
    for suffix in ("-journal", "-wal", "-shm"):
        sidecar = Path(str(database) + suffix)
        try:
            _private_regular(sidecar)
        except FileNotFoundError:
            pass  # Another committed session can remove its transient journal.
    return database, created


def _schema(connection: sqlite3.Connection, *, created: bool) -> None:
    if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise PrivateStorageError("corrupt")
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    application = connection.execute("PRAGMA application_id").fetchone()[0]
    schema = connection.execute(
        "SELECT type, name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    if version == 0:
        if not created or application not in (0, APPLICATION_ID) or schema:
            raise PrivateStorageError("incompatible")
    elif application != APPLICATION_ID or not 1 <= version <= SCHEMA_VERSION:
        raise PrivateStorageError("incompatible")
    elif schema != SCHEMAS[version]:
        raise PrivateStorageError("incompatible")
    for target in range(version + 1, SCHEMA_VERSION + 1):
        for statement in MIGRATIONS[target]:
            connection.execute(statement)
        connection.execute(f"PRAGMA user_version = {target}")
    if version == 0:
        connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
    if connection.execute(
        "SELECT type, name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall() != SCHEMAS[SCHEMA_VERSION]:
        raise PrivateStorageError("incompatible")


class PrivateDatabase:
    """Host-only persistence handle; construction does no IO.

    Callbacks are trusted server wiring, supplied explicitly only in tests or
    another host. Config and identity are fetched again for *each* transaction.
    Demo ignores the configured path and owns an empty temporary database.
    """

    def __init__(
        self,
        config: Callable[[], AppConfig] = runtime_config,
        *,
        user: Callable[[], Mapping[str, object]] = lambda: st.user,
        server_address: Callable[[], object] = lambda: st.get_option("server.address"),
    ):
        self._config = config
        self._user = user
        self._server_address = server_address
        self._temporary: TemporaryDirectory | None = None
        self._closed = False

    def _authorize(self, config: AppConfig) -> None:
        if config.mode is Mode.PRODUCTION:
            if not owner_is_authorized(self._user(), config.login):
                raise PrivateStorageError("access")
        elif config.mode is Mode.LOCAL:
            if self._server_address() not in LOOPBACK_ADDRESSES:
                raise PrivateStorageError("local")
        else:
            raise PrivateStorageError("access")

    def require_real_access(self) -> None:
        """Raise unless the current run may touch real private data and providers.

        Production needs the validated owner session, local the loopback address.
        Demo and every other mode are refused: demo data stays synthetic and
        offline. Nothing is cached; callers repeat the check before each use and
        no filesystem access happens here.
        """
        self._authorize(self._config())

    def _directory(self) -> str | Path:
        config = self._config()
        if config.mode is Mode.DEMO:
            if self._temporary is None:
                self._temporary = TemporaryDirectory(prefix="portfolio-demo-")
            return Path(self._temporary.name).resolve()
        self._authorize(config)
        if not config.private_data_dir:
            raise PrivateStorageError("path")
        return config.private_data_dir

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Guard, validate/migrate, commit atomically; rollback on every failure.

        IMMEDIATE serializes both schema startup and writes from independent
        sessions/processes. Reads are short transactions too; no stale snapshot
        or persistent connection outlives this operation. Busy waits are bounded.
        """
        connection = None
        try:
            if self._closed:
                raise PrivateStorageError("path")
            database, created = _database_path(self._directory())
            connection = sqlite3.connect(database, timeout=2, isolation_level=None)
            connection.execute("PRAGMA trusted_schema = OFF")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA temp_store = MEMORY")
            connection.execute("PRAGMA secure_delete = ON")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("BEGIN IMMEDIATE")
            _schema(connection, created=created)
            # A successfully initialized schema survives a rejected first domain
            # change. The migration itself is committed atomically before CRUD.
            if created:
                connection.commit()
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except sqlite3.Error as error:
            code = getattr(error, "sqlite_errorcode", 0) & 0xFF
            reason = (
                "busy"
                if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
                else "corrupt"
                if code in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB)
                else "path"
            )
            raise PrivateStorageError(reason) from None
        except (OSError, RuntimeError):
            raise PrivateStorageError("path") from None
        finally:
            if connection is not None:
                connection.close()  # Closing an uncommitted transaction rolls it back.

    def close(self) -> None:
        """Discard only this handle's synthetic demo data; never delete private data."""
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None
        self._closed = True
