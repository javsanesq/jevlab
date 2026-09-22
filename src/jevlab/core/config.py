"""Nonsecret configuration, confined to one app-data directory."""

import os
import tomllib
from pathlib import Path

import tomli_w
from pydantic import ValidationError

from jevlab.core.errors import JevError
from jevlab.core.files import atomic_write, read_text
from jevlab.core.models import Settings
from jevlab.core.templates import validation_message


def data_directory() -> Path:
    """Choose one complete profile; never merge or move a potentially open database."""
    override = os.environ.get("JEVLAB_HOME") or os.environ.get("JEV_HOME")
    if override:
        return Path(override).expanduser().resolve()
    current, legacy = Path.home() / ".jevlab", Path.home() / ".jev"
    return (legacy if legacy.exists() and not current.exists() else current).resolve()


def profile_notice(root: Path) -> str | None:
    """Return migration guidance as data; presentation belongs to the CLI and TUI."""
    if os.environ.get("JEVLAB_HOME"):
        return None
    if os.environ.get("JEV_HOME"):
        return "Using legacy JEV_HOME. Set JEVLAB_HOME instead; your profile has not moved."
    current, legacy = Path.home() / ".jevlab", Path.home() / ".jev"
    if root == legacy.resolve():
        return (
            "Using your existing ~/.jev profile. Templates, settings and history stay in place. "
            "New installations use ~/.jevlab; JEVLAB_HOME selects a different profile."
        )
    if root == current.resolve() and legacy.exists():
        return (
            "Both ~/.jevlab and ~/.jev exist. Using ~/.jevlab; the old profile is untouched. "
            "To open the old profile, run JEVLAB_HOME=~/.jev jevlab."
        )
    return None


def database_path(root: Path) -> Path:
    """Keep an existing history file under its original name, including WAL companions."""
    current, legacy = root / "jevlab.db", root / "jev.db"
    if current.exists() and legacy.exists():
        raise JevError(
            "ambiguous_history",
            "This profile contains both jevlab.db and jev.db; its history is ambiguous.",
            "Back up the entire profile, then put each database and its WAL/SHM companions "
            "in a separate profile. Choose one with JEVLAB_HOME; do not overwrite either file.",
        )
    return legacy if legacy.exists() else current


def load_settings(root: Path) -> Settings:
    path = root / "config.toml"
    if not path.exists():
        return Settings()
    try:
        return Settings.model_validate(tomllib.loads(read_text(path)))
    except (ValueError, ValidationError) as error:
        raise JevError(
            "invalid_config",
            f"config.toml: {validation_message(error)}",
            "Edit the named setting in config.toml; use jev-latest for the Jev model. "
            "Credentials belong in Keychain or environment variables.",
        ) from None
    except OSError:
        raise JevError(
            "invalid_config",
            "Cannot read config.toml.",
            "Check its TOML syntax and supported settings; credentials do not belong in this file.",
        ) from None


def save_settings(root: Path, settings: Settings) -> None:
    validated = Settings.model_validate(settings.model_dump())
    atomic_write(root / "config.toml", tomli_w.dumps(validated.model_dump()))


PRIVACY = {
    "no_training": "Published TypeSafe policy; no per-request toggle.",
    "zdr": "Enterprise arrangement; not activated or verified by this app.",
    "source": "https://docs.typesafe.ai/legal",
    "local_history": (
        "Inputs/responses are saved locally; age/size retention prunes eligible history. "
        "Active work is protected."
    ),
}
