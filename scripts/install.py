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


def installed_coach_extras() -> set[str]:
    """Read this tool's receipt, respecting uv's configured installation directory."""
    try:
        result = subprocess.run(["uv", "tool", "dir"], check=False, capture_output=True, text=True)
    except OSError as exc:
        raise InstallError("Cannot run uv. Install uv and retry make install.") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise InstallError(
            "Cannot locate the uv tool directory. Run uv tool dir to diagnose, "
            "then retry make install. No installation was changed."
        )
    tool = Path(result.stdout.strip()) / "jev-workbench"
    if not tool.exists():
        return set()
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
            and re.sub(r"[-_.]+", "-", requirement["name"]).lower() == "jev-workbench"
        ]
        if len(matches) != 1:
            raise ValueError("Ambiguous or missing jev requirement")
        extras = matches[0].get("extras", [])
        if not isinstance(extras, list) or not all(isinstance(extra, str) for extra in extras):
            raise ValueError("Invalid extras list")
        return set(extras) & {"anthropic", "openai"}
    except (OSError, ValueError) as exc:
        # Avoid echoing receipt contents or exception details: requirements may have URLs.
        raise InstallError(
            "Cannot read the existing jev uv receipt. No installation was changed. "
            "Repair the receipt reported under uv tool dir, or reinstall explicitly with "
            "uv tool install --reinstall --editable '.[anthropic,openai]' "
            "from the jev project directory. This retains both coach SDKs."
        ) from exc


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--coach", choices=["openai", "anthropic", "both"])
    args = parser.parse_args()
    existing = shutil.which("jev")
    # uv run injects this project's own environment into PATH; it isn't a collision.
    if existing and Path(existing).parent != root / ".venv" / "bin":
        print(
            f"Warning: jev already exists at {existing}. "
            "uv will check ownership before installing.",
            file=sys.stderr,
        )
    else:
        others = list(
            dict.fromkeys(
                str(Path(directory) / "jev")
                for directory in os.get_exec_path()
                if Path(directory) != root / ".venv" / "bin" and (Path(directory) / "jev").is_file()
            )
        )
        if others:
            print(f"Warning: existing jev command(s): {', '.join(others)}", file=sys.stderr)
    try:
        extras = installed_coach_extras()
    except InstallError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
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
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
