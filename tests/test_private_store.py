"""Synthetic storage failures and authorization; never open a real private path."""

import os
import sqlite3
import stat
from dataclasses import replace
from pathlib import Path

import pytest

import portfolio_dashboard.auth as auth
import portfolio_dashboard.private_store as storage
from portfolio_dashboard.config import (
    AppConfig,
    CacheTTLs,
    LoginConfig,
    Mode,
    Provider,
    SourceConfig,
    load_config,
)
from portfolio_dashboard.private_store import (
    APPLICATION_ID,
    PrivateDatabase,
    PrivateStorageError,
)
from portfolio_dashboard.watchlist import WatchlistEntry, WatchlistRepository

NOW = 2_000_000_000
LOGIN = LoginConfig(
    enabled=True,
    owner_issuer="https://accounts.google.com",
    owner_subject="synthetic-owner",
    client_id="synthetic-client",
)


def config(directory, mode=Mode.LOCAL):
    return AppConfig(
        mode,
        SourceConfig(),
        Provider.SYNTHETIC,
        CacheTTLs(),
        LOGIN,
        None if directory is None else str(directory),
    )


def claims():
    return {
        "is_logged_in": True,
        "iss": LOGIN.owner_issuer,
        "sub": LOGIN.owner_subject,
        "aud": LOGIN.client_id,
        "iat": NOW - 60,
        "exp": NOW + 60,
    }


def repo_for(directory):
    return WatchlistRepository(
        PrivateDatabase(lambda: config(directory), server_address=lambda: "127.0.0.1")
    )


def operations(repo):
    record = WatchlistEntry("synthetic-id", "Synthetische Beobachtung")
    return [
        lambda: repo.list(),
        lambda: repo.get(record.entry_id),
        lambda: repo.add(record),
        lambda: repo.update(record),
        lambda: repo.remove(record.entry_id, expected_revision=1),
    ]


@pytest.mark.parametrize(
    "bad_claims",
    [{}, {"sub": "synthetic-other"}, {"exp": NOW}, {"is_logged_in": False}],
)
def test_every_private_operation_denies_before_filesystem(
    tmp_path, monkeypatch, bad_claims
):
    monkeypatch.setattr(auth.time, "time", lambda: NOW)
    supplied = claims() | bad_claims if bad_claims else {}
    database = PrivateDatabase(
        lambda: config(tmp_path / "private", Mode.PRODUCTION), user=lambda: supplied
    )
    repo = WatchlistRepository(database)

    def forbidden(*args):
        raise AssertionError("unauthorized filesystem access")

    monkeypatch.setattr(storage, "_database_path", forbidden)
    for operation in operations(repo):
        with pytest.raises(PrivateStorageError) as error:
            operation()
        assert error.value.code == "access"
    assert not (tmp_path / "private").exists()


def test_expiry_revocation_and_current_config_are_rechecked_each_operation(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(auth.time, "time", lambda: NOW)
    current = config(tmp_path / "private", Mode.PRODUCTION)
    user = claims()
    database = PrivateDatabase(lambda: current, user=lambda: user)
    repo = WatchlistRepository(database)
    record = WatchlistEntry("synthetic-id", "Synthetische Beobachtung")
    repo.add(record)
    assert repo.list() == (record,)
    user["exp"] = NOW
    for operation in operations(repo):
        with pytest.raises(PrivateStorageError, match="Besitzerzugriff"):
            operation()
    user.update(claims())
    current = replace(current, login=replace(LOGIN, owner_subject="synthetic-revoked"))
    for operation in operations(repo):
        with pytest.raises(PrivateStorageError, match="Besitzerzugriff"):
            operation()
    current = replace(current, login=LOGIN)
    assert repo.get(record.entry_id) == record


@pytest.mark.parametrize("address", [None, "", "0.0.0.0", "192.0.2.4"])
def test_local_boundary_precedes_all_storage_operations(tmp_path, address, monkeypatch):
    database = PrivateDatabase(
        lambda: config(tmp_path / "private"), server_address=lambda: address
    )

    def forbidden(*args):
        raise AssertionError("non-loopback filesystem access")

    monkeypatch.setattr(storage, "_database_path", forbidden)
    for operation in operations(WatchlistRepository(database)):
        with pytest.raises(PrivateStorageError) as error:
            operation()
        assert error.value.code == "local"


@pytest.mark.parametrize("address", ["127.0.0.1", "localhost", "::1"])
def test_local_loopback_is_rechecked_without_consulting_login(tmp_path, address):
    current_address = address

    def forbidden():
        raise AssertionError("local consulted login")

    database = PrivateDatabase(
        lambda: config(tmp_path / "private"),
        user=forbidden,
        server_address=lambda: current_address,
    )
    repo = WatchlistRepository(database)
    assert repo.list() == ()
    current_address = "0.0.0.0"
    with pytest.raises(PrivateStorageError) as error:
        repo.list()
    assert error.value.code == "local"


def test_runtime_defaults_fetch_current_streamlit_identity_and_address(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(auth.time, "time", lambda: NOW)
    current = config(tmp_path / "private", Mode.PRODUCTION)
    # The default config callback is the real runtime_config function. Patch its
    # dependencies rather than replacing an already bound default argument.
    monkeypatch.setenv("PORTFOLIO_MODE", "production")
    monkeypatch.setattr(storage, "load_config", lambda **kwargs: current)
    monkeypatch.setattr(storage.st, "secrets", {"auth": {}})
    monkeypatch.setattr(storage.st, "user", claims())
    repo = WatchlistRepository(PrivateDatabase())
    assert repo.list() == ()
    monkeypatch.setattr(storage.st, "user", {})
    with pytest.raises(PrivateStorageError, match="Besitzerzugriff"):
        repo.list()


@pytest.fixture
def inline_production_runtime(tmp_path, monkeypatch):
    # Exercise the real runtime_config/load_config chain, never host secrets.
    for name in tuple(os.environ):
        if name.startswith("PORTFOLIO_"):
            monkeypatch.delenv(name)
    settings = {
        "PORTFOLIO_MODE": "production",
        "PORTFOLIO_OWNER_ISSUER": LOGIN.owner_issuer,
        "PORTFOLIO_OWNER_SUBJECT": LOGIN.owner_subject,
        "PORTFOLIO_SHEET_ID": "synthetic-source-id",
        "PORTFOLIO_SHEET_RANGE": "synthetic-source-range",
        "PORTFOLIO_PRIVATE_DATA_DIR": str(tmp_path / "private"),
    }
    for name, value in settings.items():
        monkeypatch.setenv(name, value)

    class SyntheticSecrets(dict):
        def get(self, key, default=None):
            # The DB gate needs auth, never Sheet credentials or listings.
            assert key == "auth"
            return super().get(key, default)

    secrets = SyntheticSecrets({
        "auth": {
            "client_id": LOGIN.client_id,
            "client_secret": "synthetic-client-secret",
            "cookie_secret": "synthetic-cookie-secret",
            "redirect_uri": "https://dashboard.example.invalid/oauth2callback",
            "server_metadata_url": "https://accounts.google.com/.well-known/openid-configuration",
        },
        "sheets_service_account": {"type": "service_account", "synthetic": True},
        "listing": [],
    })
    monkeypatch.setattr(storage.st, "secrets", secrets)
    monkeypatch.setattr(storage.st, "user", claims())
    monkeypatch.setattr(auth.time, "time", lambda: NOW)
    return tmp_path / "private"


def test_runtime_inline_production_owner_persists_without_credential_file(
    inline_production_runtime,
):
    current = storage.runtime_config()
    assert current.source.credentials_file is None
    assert current.private_data_dir == str(inline_production_runtime)
    database = PrivateDatabase()
    repo = WatchlistRepository(database)
    record = WatchlistEntry("synthetic-inline-id", "Synthetische Beobachtung")
    repo.add(record)
    database.close()
    reopened = PrivateDatabase()
    try:
        repo = WatchlistRepository(reopened)
        assert repo.get(record.entry_id) == record
        updated = repo.update(replace(record, label="Synthetische Änderung"))
        assert repo.list() == (updated,)
        repo.remove(updated.entry_id, expected_revision=updated.revision)
        assert repo.list() == ()
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "bad_claims",
    [{}, {"sub": "synthetic-other"}, {"exp": NOW}, {"is_logged_in": False}],
)
@pytest.mark.parametrize("existing", [False, True])
def test_runtime_inline_production_rechecks_owner_before_every_private_access(
    inline_production_runtime, monkeypatch, bad_claims, existing
):
    repo = WatchlistRepository(PrivateDatabase())
    if existing:
        repo.add(WatchlistEntry("synthetic-id", "Synthetische Beobachtung"))
    supplied = claims() | bad_claims if bad_claims else {}
    monkeypatch.setattr(storage.st, "user", supplied)

    def forbidden(*args):
        raise AssertionError("unauthorized filesystem access")

    monkeypatch.setattr(storage, "_database_path", forbidden)
    for operation in operations(repo):
        with pytest.raises(PrivateStorageError) as error:
            operation()
        assert error.value.code == "access"
    assert inline_production_runtime.exists() is existing


def test_runtime_inline_production_rechecks_owner_configuration(
    inline_production_runtime, monkeypatch,
):
    repo = WatchlistRepository(PrivateDatabase())
    assert repo.list() == ()
    monkeypatch.setenv("PORTFOLIO_OWNER_SUBJECT", "synthetic-revoked")

    def forbidden(*args):
        raise AssertionError("revoked owner filesystem access")

    monkeypatch.setattr(storage, "_database_path", forbidden)
    for operation in operations(repo):
        with pytest.raises(PrivateStorageError) as error:
            operation()
        assert error.value.code == "access"


def test_demo_ignores_private_path_and_never_consults_identity(tmp_path):
    private = tmp_path / "synthetic-real-placeholder"
    private.mkdir(mode=0o700)
    marker = private / "private.sqlite3"
    marker.write_bytes(b"synthetic-do-not-open")

    def forbidden():
        raise AssertionError("demo consulted auth or bind address")

    demo = replace(config(private, Mode.DEMO), private_data_dir=str(private))
    first = PrivateDatabase(lambda: demo, user=forbidden, server_address=forbidden)
    first_repo = WatchlistRepository(first)
    first_repo.add(WatchlistEntry("synthetic-demo", "Synthetische Demo"))
    temporary = Path(first._temporary.name)
    second = PrivateDatabase(lambda: demo, user=forbidden, server_address=forbidden)
    assert WatchlistRepository(second).list() == ()
    assert marker.read_bytes() == b"synthetic-do-not-open"
    assert first_repo.list()[0].label == "Synthetische Demo"
    first.close()
    second.close()
    assert not temporary.exists()
    with pytest.raises(PrivateStorageError):
        first_repo.list()


def test_demo_configuration_discards_private_path_even_if_invalid():
    value = load_config(
        {"PORTFOLIO_MODE": "demo", "PORTFOLIO_PRIVATE_DATA_DIR": "<PRIVATE>"}
    )
    assert value.private_data_dir is None


@pytest.mark.parametrize(
    "directory",
    [
        None,
        "relative-private-path",
        "/app/private",
        str(Path(__file__).parents[1] / "private"),
    ],
)
def test_missing_or_in_checkout_or_image_path_does_not_succeed(directory):
    with pytest.raises(PrivateStorageError) as error:
        repo_for(directory).list()
    assert error.value.code == "path"
    if directory:
        assert directory not in str(error.value)


def test_unwritable_or_missing_parent_path_has_fixed_message(
    tmp_path, monkeypatch, caplog
):
    directory = tmp_path / "synthetic-private-path"

    def denied(*args, **kwargs):
        raise PermissionError("synthetic-secret-filesystem-error")

    monkeypatch.setattr(storage.os, "open", denied)
    with pytest.raises(PrivateStorageError) as error:
        repo_for(directory).list()
    assert error.value.code == "path"
    combined = str(error.value) + caplog.text
    assert (
        str(directory) not in combined
        and "synthetic-secret-filesystem-error" not in combined
    )
    assert "kein Speichern bestätigt" in str(error.value)


def test_private_directory_database_and_journal_have_restrictive_permissions(tmp_path):
    directory = tmp_path / "private"
    database = PrivateDatabase(
        lambda: config(directory), server_address=lambda: "127.0.0.1"
    )
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO watchlist VALUES ('synthetic-id', '{}', NULL, 1)"
        )
        if os.name == "posix":
            assert stat.S_IMODE(directory.stat().st_mode) == 0o700
            assert stat.S_IMODE((directory / "private.sqlite3").stat().st_mode) == 0o600
            journal = directory / "private.sqlite3-journal"
            assert journal.exists() and stat.S_IMODE(journal.stat().st_mode) == 0o600


def test_symlink_and_hardlink_database_are_rejected_without_reading_target(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    target = tmp_path / "synthetic-target"
    target.write_bytes(b"synthetic-private-target")
    database = directory / "private.sqlite3"
    database.symlink_to(target)
    with pytest.raises(PrivateStorageError) as error:
        repo_for(directory).list()
    assert error.value.code == "permissions"
    database.unlink()
    os.link(target, database)
    with pytest.raises(PrivateStorageError) as error:
        repo_for(directory).list()
    assert error.value.code == "permissions"
    assert target.read_bytes() == b"synthetic-private-target"


def test_directory_and_sidecar_symlinks_cannot_redirect_storage(tmp_path):
    target = tmp_path / "synthetic-target"
    target.mkdir(mode=0o700)
    alias = tmp_path / "synthetic-alias"
    alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(PrivateStorageError) as error:
        repo_for(alias).list()
    assert error.value.code == "permissions"
    repo = repo_for(target)
    repo.list()
    sidecar = target / "private.sqlite3-journal"
    sidecar.symlink_to(tmp_path / "nonexistent-target")
    with pytest.raises(PrivateStorageError) as error:
        repo.list()
    assert error.value.code == "permissions"


@pytest.mark.skipif(os.name != "posix", reason="POSIX modes")
@pytest.mark.parametrize("what", ["directory", "database"])
def test_unsafe_existing_permissions_are_refused(tmp_path, what):
    directory = tmp_path / "private"
    repo = repo_for(directory)
    repo.list()
    path = directory if what == "directory" else directory / "private.sqlite3"
    path.chmod(0o755 if what == "directory" else 0o644)
    with pytest.raises(PrivateStorageError) as error:
        repo.list()
    assert error.value.code == "permissions"


@pytest.mark.parametrize("contents", [b"synthetic-corrupt-private-data", b""])
def test_corrupt_or_truncated_database_is_never_recreated(tmp_path, contents):
    directory = tmp_path / "private"
    repo = repo_for(directory)
    repo.add(WatchlistEntry("synthetic-id", "Synthetisch"))
    path = directory / "private.sqlite3"
    path.write_bytes(contents)
    with pytest.raises(PrivateStorageError) as error:
        repo.list()
    assert error.value.code in {"corrupt", "incompatible"}
    assert path.read_bytes() == contents
    assert "synthetic-corrupt-private-data" not in str(error.value)


@pytest.mark.parametrize("kind", ["future", "foreign", "unversioned", "schema-drift"])
def test_incompatible_database_remains_untouched(tmp_path, kind):
    directory = tmp_path / "private"
    repo = repo_for(directory)
    repo.list()
    path = directory / "private.sqlite3"
    with sqlite3.connect(path) as connection:
        if kind == "future":
            connection.execute("PRAGMA user_version = 99")
        elif kind == "foreign":
            connection.execute("PRAGMA application_id = 123")
        elif kind == "unversioned":
            connection.execute("PRAGMA user_version = 0")
        else:
            connection.execute(
                "ALTER TABLE watchlist ADD COLUMN synthetic_unknown TEXT"
            )
    before = path.read_bytes()
    with pytest.raises(PrivateStorageError) as error:
        repo.list()
    assert error.value.code == "incompatible"
    assert path.read_bytes() == before


def test_schema_migration_failure_is_atomic_and_not_silently_recreated(
    tmp_path, monkeypatch
):
    directory = tmp_path / "private"
    database = PrivateDatabase(
        lambda: config(directory), server_address=lambda: "127.0.0.1"
    )
    monkeypatch.setattr(
        storage, "MIGRATIONS", {1: (storage.WATCHLIST_SCHEMA, "synthetic invalid SQL")}
    )
    with pytest.raises(PrivateStorageError):
        WatchlistRepository(database).list()
    with sqlite3.connect(directory / "private.sqlite3") as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert connection.execute("SELECT name FROM sqlite_schema").fetchall() == []
    monkeypatch.setattr(storage, "MIGRATIONS", {1: (storage.WATCHLIST_SCHEMA,)})
    with pytest.raises(PrivateStorageError) as error:
        WatchlistRepository(database).list()
    assert error.value.code == "incompatible"


def test_rejected_first_domain_change_leaves_usable_empty_schema(tmp_path):
    directory = tmp_path / "private"
    database = PrivateDatabase(
        lambda: config(directory), server_address=lambda: "127.0.0.1"
    )
    with pytest.raises(ValueError, match="synthetic rollback"):
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO watchlist VALUES ('synthetic-id', '{}', NULL, 1)"
            )
            raise ValueError("synthetic rollback")
    assert WatchlistRepository(database).list() == ()


def test_committed_schema_is_versioned_and_domain_failure_rolls_back(tmp_path):
    directory = tmp_path / "private"
    database = PrivateDatabase(
        lambda: config(directory), server_address=lambda: "127.0.0.1"
    )
    repo = WatchlistRepository(database)
    assert repo.list() == ()
    with pytest.raises(ValueError):
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO watchlist VALUES ('synthetic-id', '{}', NULL, 1)"
            )
            raise ValueError("synthetic rollback")
    assert repo.list() == ()
    with sqlite3.connect(directory / "private.sqlite3") as connection:
        assert (
            connection.execute("PRAGMA user_version").fetchone()[0]
            == storage.SCHEMA_VERSION
        )
        assert (
            connection.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        )


def test_two_sessions_start_when_creator_is_paused_before_sqlite_connect(
    tmp_path, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    directory = tmp_path / "private"
    first_connect = Event()
    second_waiting = Event()
    original_connect = storage.sqlite3.connect
    original_sleep = storage.time.sleep

    def delayed_connect(*args, **kwargs):
        if not first_connect.is_set():
            first_connect.set()
            assert second_waiting.wait(timeout=5)
        return original_connect(*args, **kwargs)

    def observed_wait(seconds):
        second_waiting.set()
        original_sleep(seconds)

    monkeypatch.setattr(storage.sqlite3, "connect", delayed_connect)
    monkeypatch.setattr(storage.time, "sleep", observed_wait)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(repo_for(directory).list)
        assert first_connect.wait(timeout=5)
        second = pool.submit(repo_for(directory).list)
        assert first.result(timeout=10) == ()
        assert second.result(timeout=10) == ()
    assert second_waiting.is_set()
