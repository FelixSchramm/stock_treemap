"""Real wheel installation with invented checkout paths and synthetic records."""

import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def wheel(tmp_path_factory):
    # Build only public application sources; artifacts never enter the checkout.
    directory = tmp_path_factory.mktemp("private-store-wheel")
    source = directory / "source"
    source.mkdir()
    for name in ("pyproject.toml", "README.md"):
        shutil.copy2(ROOT / name, source / name)
    package = source / "src" / "portfolio_dashboard"
    package.mkdir(parents=True)
    for module in (ROOT / "src" / "portfolio_dashboard").glob("*.py"):
        shutil.copy2(module, package / module.name)
    # uv sync already cached the declared backend while installing this project.
    # Offline mode ensures this regression never downloads build dependencies.
    subprocess.run(
        [
            "uv", "build", "--wheel", "--offline", "--python", sys.executable,
            "--out-dir", str(directory / "wheels"), str(source),
        ],
        check=True, capture_output=True, text=True,
    )
    return next((directory / "wheels").glob("*.whl"))


@pytest.mark.parametrize("layout", ["git", "worktree", "unpacked"])
@pytest.mark.parametrize("working_directory", ["checkout", "elsewhere"])
def test_noneditable_install_rejects_checkout_and_allows_external_storage(
    tmp_path, wheel, layout, working_directory
):
    checkout = tmp_path / "synthetic-checkout"
    checkout.mkdir(mode=0o700)
    if layout == "git":
        subprocess.run(["git", "init", "--quiet", str(checkout)], check=True)
    elif layout == "worktree":
        # Git worktrees use a .git file rather than a directory. Its contents
        # need not be read to conservatively exclude the surrounding checkout.
        (checkout / ".git").write_text("gitdir: /synthetic/git/worktrees/test\n")
    else:
        shutil.copy2(ROOT / "app.py", checkout / "app.py")
        shutil.copy2(ROOT / "pyproject.toml", checkout / "pyproject.toml")
    environment = checkout / ".venv"
    subprocess.run(
        ["uv", "venv", "--offline", "--python", sys.executable, str(environment)],
        check=True, capture_output=True, text=True,
    )
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    subprocess.run(
        ["uv", "pip", "install", "--offline", "--no-deps", "--python", str(python), str(wheel)],
        check=True, capture_output=True, text=True,
    )
    runtime = checkout / "runtime"
    runtime.mkdir()
    (checkout / "exports").mkdir()
    code = """
import socket
import sys
from pathlib import Path

# Reuse installed third-party dependencies, after the wheel's own site-packages.
# -I excludes PYTHONPATH and cwd; no editable project is installed here.
sys.path.append(sys.argv[1])
import portfolio_dashboard.private_store as storage
from portfolio_dashboard.config import AppConfig, CacheTTLs, LoginConfig, Mode, Provider, SourceConfig

class ProbeStore:
    # Neutral exerciser: the scenario table through the public transaction contract.
    def __init__(self, database):
        self._database = database

    def list(self):
        with self._database.transaction() as connection:
            return tuple(connection.execute('SELECT instrument_id, payload FROM scenario_versions'))

    def add(self, record):
        with self._database.transaction() as connection:
            connection.execute('INSERT INTO scenario_versions VALUES (?, 1, ?)', record)

def no_network(*args, **kwargs):
    raise AssertionError('unexpected network access')
socket.socket = socket.create_connection = no_network
checkout, external = map(Path, sys.argv[2:])
assert Path(storage.__file__).is_relative_to(Path(sys.prefix) / 'lib') or (
    Path(storage.__file__).is_relative_to(Path(sys.prefix) / 'Lib')
)
assert Path(sys.prefix) == checkout / '.venv'

def database(directory, mode=Mode.LOCAL):
    config = AppConfig(mode, SourceConfig(), Provider.SYNTHETIC, CacheTTLs(), LoginConfig(), str(directory))
    return storage.PrivateDatabase(lambda: config, server_address=lambda: '127.0.0.1')

for directory in (checkout, checkout / 'exports' / 'nested', checkout / 'synthetic-runtime',
                  Path(sys.prefix) / 'private', Path('/app/private')):
    try:
        ProbeStore(database(directory)).list()
    except storage.PrivateStorageError as error:
        assert error.code == 'path'
        assert str(directory) not in str(error)
    else:
        raise AssertionError('forbidden database path was accepted')
assert not tuple(checkout.rglob('private.sqlite3'))

record = ('synthetic-id', 'Synthetische Beobachtung')
first = database(external)
ProbeStore(first).add(record)
first.close()
assert ProbeStore(database(external)).list() == (record,)
assert (external / 'private.sqlite3').is_file()

demo = database(checkout / 'exports', Mode.DEMO)
ProbeStore(demo).add(record)
assert ProbeStore(demo).list() == (record,)
demo.close()
assert not tuple(checkout.rglob('private.sqlite3'))
print('wheel-storage-ok')
"""
    result = subprocess.run(
        [
            str(python), "-I", "-c", code, sysconfig.get_path("purelib"),
            str(checkout), str(tmp_path / "external-private"),
        ],
        cwd=runtime if working_directory == "checkout" else tmp_path,
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "wheel-storage-ok"
