"""Offline diagnostics, with an explicit optional account connectivity check."""

import asyncio
import os
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

from jev.core.client import suppress_wire_logs, translate_error
from jev.core.config import PRIVACY
from jev.core.credentials import Credentials, require_credentials
from jev.core.errors import JevError
from jev.core.retention import database_bytes
from jev.core.service import Workbench


def inspect(workbench: Workbench) -> dict[str, object]:
    coach_sdks: dict[str, str | None] = {}
    for provider in ("openai", "anthropic"):
        try:
            coach_sdks[provider] = version(provider)
        except PackageNotFoundError:
            coach_sdks[provider] = None
    templates = []
    for name in workbench.templates.names():
        try:
            workbench.templates.load(name)
            templates.append({"name": name, "valid": True})
        except JevError as error:
            templates.append({"name": name, "valid": False, "error": error.as_dict()})
    sizes = {
        str(p.relative_to(workbench.root)): p.stat().st_size
        for p in workbench.root.rglob("*")
        if p.is_file()
    }
    binaries = list(
        dict.fromkeys(
            str((Path(directory) / "jev").resolve())
            for directory in os.get_exec_path()
            if (Path(directory) / "jev").is_file() and os.access(Path(directory) / "jev", os.X_OK)
        )
    )
    return {
        "data_directory": str(workbench.root),
        "database": workbench.storage.health(),
        "dependencies": {name: version(name) for name in ["typesafe-sdk", "textual", "keyring"]},
        "credentials": Credentials(workbench.settings.credential_mode).status(
            timeout_seconds=min(5.0, workbench.settings.deadline_seconds)
        ),
        "templates": templates,
        "disk_bytes": sum(sizes.values()),
        "files": sizes,
        "jev_binaries": binaries,
        "path_collision": len(binaries) > 1,
        "retention": {
            "days": workbench.settings.retention_days,
            "bytes": workbench.settings.retention_bytes,
            "enforced": True,
            "history_bytes": database_bytes(workbench.storage.path),
            "scope": "SQLite database and WAL; templates and external dataset/output files kept.",
            "active_history": "Pending operations are protected; the size cap is best effort then.",
            "maintenance_error": workbench.maintenance_error,
        },
        "privacy": PRIVACY,
        "network_checked": False,
        "coach": {
            "provider": workbench.settings.coach_provider,
            "model": workbench.settings.coach_model,
            "models": {
                provider: workbench.settings.coach_model_for(provider)
                for provider in ("anthropic", "openai")
            },
            "installed_sdks": coach_sdks,
            "network_checked": False,
        },
    }


async def online(workbench: Workbench) -> list[dict[str, str]]:
    suppress_wire_logs()
    settings = workbench.settings
    deadline = asyncio.get_running_loop().time() + settings.deadline_seconds
    key = await require_credentials(
        Credentials(settings.credential_mode), timeout_seconds=min(5.0, settings.deadline_seconds)
    )
    try:
        async with (
            asyncio.timeout_at(deadline),
            AsyncTypeSafeClient(
                api_key=key,
                base_url="https://api.typesafe.ai",
                timeout=settings.timeout_seconds,
                retry=RetryPolicy(max_retries=settings.max_retries),
            ) as client,
        ):
            result = await client.models.list()
        return [
            {
                "name": model.name,
                "description": model.description,
                "release_date": model.release_date,
            }
            for model in result.models
        ]
    except Exception as error:
        raise translate_error(error, secrets=(key,)) from None
