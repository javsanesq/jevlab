"""Nonsecret configuration, confined to one app-data directory."""

import os
import tomllib
from pathlib import Path

import tomli_w
from pydantic import ValidationError

from jev.core.errors import JevError
from jev.core.files import atomic_write, read_text
from jev.core.models import Settings
from jev.core.templates import validation_message


def data_directory() -> Path:
    """JEV_HOME allows isolated tests and separate workspaces without changing HOME."""
    return Path(os.environ.get("JEV_HOME", "~/.jev")).expanduser().resolve()


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
