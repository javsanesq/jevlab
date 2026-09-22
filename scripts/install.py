"""Install the editable command, warning about an existing PATH entry first."""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path


class InstallError(Exception):
    """A failed preflight that must not discard installed optional dependencies."""


def installed_tools() -> dict[str, set[str]]:
    """Read both package receipts before changing either uv-managed installation."""
    try:
        result = subprocess.run(["uv", "tool", "dir"], check=False, capture_output=True, text=True)
    except OSError as exc:
        raise InstallError("Cannot run uv. Install uv and retry make install.") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise InstallError(
            "Cannot locate the uv tool directory. Run uv tool dir to diagnose, "
            "then retry make install. No installation was changed."
        )
    directory = Path(result.stdout.strip())
    return {
        name: receipt_extras(directory / name)
        for name in ("jev-workbench", "jevlab")
        if (directory / name).exists()
    }


def receipt_extras(tool: Path) -> set[str]:
    """Validate ownership and optional dependencies without exposing receipt contents."""
    receipt = tool / "uv-receipt.toml"
    try:
        with receipt.open("rb") as stream:
            data = tomllib.load(stream)
        section = data.get("tool")
        if not isinstance(section, dict):
            raise ValueError("Missing tool section")
        requirements = section.get("requirements")
        if not isinstance(requirements, list):
            raise ValueError("Missing requirements list")
        matches = [
            requirement
            for requirement in requirements
            if isinstance(requirement, dict)
            and isinstance(requirement.get("name"), str)
            and re.sub(r"[-_.]+", "-", requirement["name"]).lower() == tool.name
        ]
        if len(matches) != 1:
            raise ValueError("Ambiguous or missing package requirement")
        extras = matches[0].get("extras", [])
        if not isinstance(extras, list) or not all(isinstance(extra, str) for extra in extras):
            raise ValueError("Invalid extras list")
        return set(extras) & {"anthropic", "openai"}
    except (OSError, ValueError) as exc:
        # Avoid echoing receipt contents or exception details: requirements may have URLs.
        raise InstallError(
            f"Cannot read the existing {tool.name} uv receipt. No installation was changed. "
            "Repair the receipt reported under uv tool dir, or reinstall explicitly with "
            "uv tool install --reinstall --editable '.[anthropic,openai]' "
            "from the jevlab project directory. This retains both coach SDKs."
        ) from exc


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--coach", choices=["openai", "anthropic", "both"])
    args = parser.parse_args()
    existing = shutil.which("jevlab")
    # uv run injects this project's own environment into PATH; it isn't a collision.
    if existing and Path(existing).parent != root / ".venv" / "bin":
        print(
            f"Warning: jevlab already exists at {existing}. "
            "uv will check ownership before installing.",
            file=sys.stderr,
        )
    else:
        others = list(
            dict.fromkeys(
                str(Path(directory) / "jevlab")
                for directory in os.get_exec_path()
                if Path(directory) != root / ".venv" / "bin"
                and (Path(directory) / "jevlab").is_file()
            )
        )
        if others:
            print(f"Warning: existing jevlab command(s): {', '.join(others)}", file=sys.stderr)
    try:
        installed = installed_tools()
    except InstallError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    extras = set().union(*installed.values())
    if args.coach == "both":
        extras.update({"anthropic", "openai"})
    elif args.coach:
        extras.add(args.coach)
    # `uv tool install` accepts extras in the requirement, not a --extra flag.
    requirement = str(root) + (f"[{','.join(sorted(extras))}]" if extras else "")
    command = ["uv", "tool", "install", "--editable", requirement]
    try:
        result = subprocess.run(command, check=False)
    except OSError:
        print("Error: Cannot run uv. Install uv and retry make install.", file=sys.stderr)
        raise SystemExit(1) from None
    if result.returncode != 0:
        raise SystemExit(result.returncode)
    # uv owns this validated legacy receipt and removes only its managed entry points.
    # Never remove it before the replacement installation has completed successfully.
    if "jev-workbench" in installed:
        try:
            result = subprocess.run(["uv", "tool", "uninstall", "jev-workbench"], check=False)
        except OSError:
            removal_status = 1
        else:
            removal_status = result.returncode
        if removal_status != 0:
            print(
                "Error: jevlab is installed, but the old jev command could not be removed. "
                "Run uv tool uninstall jev-workbench to finish the command migration. "
                "Your templates, history and API keys were not changed.",
                file=sys.stderr,
            )
            raise SystemExit(removal_status)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
