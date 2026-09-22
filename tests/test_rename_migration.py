"""The command rename preserves complete legacy profiles and credential ownership."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from conftest import MockEvaluator
from typer.testing import CliRunner

from jevlab.cli.app import app
from jevlab.core.config import data_directory, profile_notice, save_settings
from jevlab.core.credentials import ENV_KEYS, LEGACY_SERVICE, SERVICE, Credentials, Provider
from jevlab.core.errors import JevError
from jevlab.core.models import Settings
from jevlab.core.service import Workbench
from jevlab.core.storage import Storage
from jevlab.server.app import server_token, server_token_source


@pytest.fixture
def isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "synthetic-home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    for variable in ("JEVLAB_HOME", "JEV_HOME", "JEVLAB_SERVER_TOKEN", "JEV_SERVER_TOKEN"):
        monkeypatch.delenv(variable, raising=False)
    return home


def test_fresh_profile_uses_new_directory_and_database(isolated_home: Path) -> None:
    selected = data_directory()
    assert selected == isolated_home / ".jevlab"
    assert not selected.exists()  # Choosing a profile has no filesystem side effects.
    bench = Workbench(selected, maintain=False)
    assert bench.storage.path == selected / "jevlab.db"
    assert bench.profile_notice is None
    assert not (isolated_home / ".jev").exists()
    assert not (selected / "jev.db").exists()


def test_legacy_profile_is_selected_without_copying_or_moving(isolated_home: Path) -> None:
    legacy = isolated_home / ".jev"
    legacy.mkdir()
    marker = legacy / "keep-me.txt"
    marker.write_text("synthetic profile marker")
    before = marker.stat().st_ino
    assert data_directory() == legacy
    assert profile_notice(legacy) == (
        "Using your existing ~/.jev profile. Templates, settings and history stay in place. "
        "New installations use ~/.jevlab; JEVLAB_HOME selects a different profile."
    )
    assert marker.stat().st_ino == before
    assert not (isolated_home / ".jevlab").exists()


def test_two_profiles_choose_new_without_merging_and_explain_old_access(
    isolated_home: Path,
) -> None:
    for name in (".jev", ".jevlab"):
        root = isolated_home / name
        root.mkdir()
        (root / "marker").write_text(name)
    selected = data_directory()
    assert selected == isolated_home / ".jevlab"
    notice = profile_notice(selected)
    assert notice and "Both ~/.jevlab and ~/.jev exist" in notice
    assert "JEVLAB_HOME=~/.jev jevlab" in notice
    for name in (".jev", ".jevlab"):
        assert (isolated_home / name / "marker").read_text() == name


def test_profile_environment_precedence_and_legacy_fallback(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy_override = isolated_home / "legacy-override"
    new_override = isolated_home / "new-override"
    monkeypatch.setenv("JEV_HOME", str(legacy_override))
    assert data_directory() == legacy_override
    assert "Using legacy JEV_HOME" in (profile_notice(legacy_override) or "")
    monkeypatch.setenv("JEVLAB_HOME", str(new_override))
    assert data_directory() == new_override
    assert profile_notice(new_override) is None
    assert not legacy_override.exists() and not new_override.exists()


async def test_legacy_database_and_live_wal_remain_in_place(isolated_home: Path) -> None:
    legacy = isolated_home / ".jev"
    database = legacy / "jev.db"
    Storage(database)
    original = Workbench(legacy, maintain=False)
    settings = Settings(credential_mode="environment", retention_days=37, ui_mode="expert")
    original.update_settings(settings)
    template = original.templates.load("support-triage").model_copy(
        update={"name": "migration-example", "notes": "Keep this synthetic design."}
    )
    template_path = original.templates.save(template)
    template_before = template_path.read_bytes()
    config_before = (legacy / "config.toml").read_bytes()
    run = await original.run(
        template,
        {"ticket": {"message": "Synthetic duplicate billing example"}},
        evaluator=MockEvaluator(),
    )
    database_inode = database.stat().st_ino
    with closing(sqlite3.connect(database)) as keeper:
        assert keeper.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        keeper.execute("PRAGMA wal_autocheckpoint=0")
        main_file_before = database.read_bytes()
        # Commit a history update only to the WAL, after the run's routine retention.
        keeper.execute("UPDATE runs SET request_id=? WHERE id=?", ("offline-wal-request", run.id))
        keeper.commit()
        expected = run.model_copy(update={"request_id": "offline-wal-request"})
        wal = legacy / "jev.db-wal"
        assert wal.exists() and wal.stat().st_size > 0
        assert database.read_bytes() == main_file_before
        wal_inode = wal.stat().st_ino
        reopened = Workbench(data_directory(), maintain=False)
        assert reopened.storage.path == database
        assert reopened.storage.get(run.id) == expected
        assert reopened.templates.load(template.name) == template
        assert reopened.settings == settings
        assert database.stat().st_ino == database_inode
        assert wal.stat().st_ino == wal_inode
        assert template_path.read_bytes() == template_before
        assert (legacy / "config.toml").read_bytes() == config_before
        assert not (legacy / "jevlab.db").exists()
        assert not (isolated_home / ".jevlab").exists()


def test_two_database_names_fail_before_any_profile_mutation(isolated_home: Path) -> None:
    root = isolated_home / ".jevlab"
    root.mkdir()
    for filename in ("jev.db", "jev.db-wal", "jevlab.db", "jevlab.db-wal"):
        (root / filename).write_bytes(f"synthetic {filename}".encode())
    before = {path.name: (path.read_bytes(), path.stat().st_ino) for path in root.iterdir()}
    with pytest.raises(JevError) as raised:
        Workbench(root)
    assert raised.value.code == "ambiguous_history"
    assert "both jevlab.db and jev.db" in raised.value.message
    assert "do not overwrite either file" in raised.value.fix
    assert {path.name: (path.read_bytes(), path.stat().st_ino) for path in root.iterdir()} == before


@pytest.mark.parametrize("both_profiles", [False, True])
def test_profile_notice_uses_stderr_without_changing_json_envelope(
    isolated_home: Path, both_profiles: bool
) -> None:
    legacy = isolated_home / ".jev"
    legacy.mkdir()
    if both_profiles:
        (isolated_home / ".jevlab").mkdir()
    selected = data_directory()
    save_settings(selected, Settings(credential_mode="environment"))
    result = CliRunner().invoke(app, ["history", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert set(payload) == {"schema_version", "ok", "data"}
    assert payload["schema_version"] == 1 and payload["ok"] is True
    assert payload["data"]["runs"] == []
    assert "~/.jev" not in result.stdout
    assert " ".join(result.stderr.split()) == " ".join((profile_notice(selected) or "").split())


class SyntheticKeyStore:
    def __init__(self, entries: dict[tuple[str, str], str]) -> None:
        self.entries = entries.copy()
        self.reads: list[tuple[str, str]] = []
        self.writes: list[tuple[str, str, str]] = []

    def get_password(self, service: str, username: str) -> str | None:
        self.reads.append((service, username))
        return self.entries.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.writes.append((service, username, password))
        self.entries[service, username] = password


@pytest.mark.parametrize("provider", ["typesafe", "anthropic", "openai"])
@pytest.mark.parametrize(
    ("current", "legacy", "expected", "source"),
    [
        ("offline-new", "offline-legacy", "offline-new", "keychain"),
        (None, "offline-legacy", "offline-legacy", "keychain (legacy jev-workbench)"),
        ("   ", "offline-legacy", "offline-legacy", "keychain (legacy jev-workbench)"),
        (None, None, "offline-environment", "environment"),
    ],
)
def test_keychain_service_precedence_preserves_all_provider_keys(
    monkeypatch: pytest.MonkeyPatch,
    provider: Provider,
    current: str | None,
    legacy: str | None,
    expected: str,
    source: str,
) -> None:
    entries = {
        (service, provider): value
        for service, value in ((SERVICE, current), (LEGACY_SERVICE, legacy))
        if value is not None
    }
    store = SyntheticKeyStore(entries)
    monkeypatch.setenv(ENV_KEYS[provider], "offline-environment")
    assert Credentials(store=store).resolve(provider) == (expected, source)
    assert store.reads[0] == ("jevlab", provider)
    if source == "keychain":
        assert len(store.reads) == 1
    else:
        assert store.reads[1] == ("jev-workbench", provider)
    assert store.writes == []  # Reading a legacy key never copies it anywhere.


@pytest.mark.parametrize("provider", ["typesafe", "anthropic", "openai"])
def test_saving_key_writes_only_current_service_and_environment_mode_skips_both(
    monkeypatch: pytest.MonkeyPatch, provider: Provider
) -> None:
    store = SyntheticKeyStore({("jev-workbench", provider): "offline-legacy"})
    credentials = Credentials(store=store)
    credentials.save(provider, " offline-new ")
    assert store.writes == [("jevlab", provider, "offline-new")]
    assert store.entries["jev-workbench", provider] == "offline-legacy"
    assert credentials.resolve(provider) == ("offline-new", "keychain")
    store.reads.clear()
    monkeypatch.setenv(ENV_KEYS[provider], "offline-environment")
    assert Credentials("environment", store).resolve(provider) == (
        "offline-environment",
        "environment",
    )
    assert store.reads == []


def test_server_token_legacy_fallback_and_new_name_precedence(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = "offline-legacy-local-token-0123456789"
    current = "offline-current-local-token-0123456789"
    monkeypatch.setenv("JEV_SERVER_TOKEN", legacy)
    assert server_token_source() == "JEV_SERVER_TOKEN"
    assert server_token() == legacy
    monkeypatch.setenv("JEVLAB_SERVER_TOKEN", current)
    assert server_token_source() == "JEVLAB_SERVER_TOKEN"
    assert server_token() == current
    monkeypatch.setenv("JEVLAB_SERVER_TOKEN", "")
    with pytest.raises(JevError) as raised:
        server_token()
    assert raised.value.code == "server_token_missing"
    assert "JEVLAB_SERVER_TOKEN" in raised.value.message
    assert legacy not in json.dumps(raised.value.as_dict())


def test_server_token_missing_does_not_reuse_provider_key(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "offline-provider-key-01234567890123456789")
    with pytest.raises(JevError) as raised:
        server_token()
    assert raised.value.code == "server_token_missing"
    assert "JEVLAB_SERVER_TOKEN" in raised.value.message
