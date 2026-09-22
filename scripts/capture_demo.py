"""Capture the actual recorded-demo UI as a small animated GIF, without live calls.

Optional maintainer command on macOS (requires FFmpeg on PATH):
    uv run python scripts/capture_demo.py

Textual renders the frames, macOS Quick Look converts SVG to PNG, and FFmpeg
encodes the animation. No image tools are added to JevLab's runtime dependencies.
Only docs/assets/demo.gif is retained; the temporary profile/frames are removed.
"""

import asyncio
import os
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from textual.containers import VerticalScroll
from textual.widgets import Button

from jevlab.core.models import Settings
from jevlab.core.service import Workbench
from jevlab.tui.app import JevApp
from jevlab.tui.guidance import DemoScreen

OUTPUT = Path(__file__).resolve().parents[1] / "docs" / "assets" / "demo.gif"
CAPTION = "jevlab / RECORDED DEMO / no live API calls"


def blocked(*args: object, **kwargs: object) -> None:
    raise RuntimeError("Demo capture must not access the network or Keychain.")


async def capture(temporary: Path) -> list[tuple[Path, float]]:
    wb = Workbench(temporary / "profile")
    wb.update_settings(Settings(credential_mode="environment", coach_provider="disabled"))
    app = JevApp(wb)
    app.sub_title = "Recorded demo · no live API calls"
    frames: list[tuple[Path, float]] = []

    def frame(name: str, seconds: float) -> None:
        svg = app.export_screenshot(title=CAPTION)
        if str(temporary) in svg:
            raise RuntimeError("A temporary path leaked into a demo frame.")
        path = temporary / f"{name}.svg"
        path.write_text(svg, encoding="utf-8")
        frames.append((path, seconds))

    async with app.run_test(size=(96, 32)) as pilot:
        app.screen.query_one("#demo", Button).focus()
        await pilot.pause()
        frame("home", 3)
        await pilot.click("#demo")
        await pilot.pause()
        if not isinstance(app.screen, DemoScreen):
            raise RuntimeError("The recorded demo did not open; no asset was produced.")
        scroll = app.screen.query_one(VerticalScroll)
        scroll.scroll_home(animate=False)
        await pilot.pause()
        frame("recorded-results", 6)
        scroll.scroll_end(animate=False)
        await pilot.pause()
        frame("review-policy", 6)
        if wb.storage.history():
            raise RuntimeError("The recorded demo unexpectedly created run history.")
    return frames


def main() -> None:
    renderer = shutil.which("qlmanage")
    encoder = shutil.which("ffmpeg")
    if not renderer or not encoder:
        raise SystemExit("Capture requires macOS Quick Look (qlmanage) and FFmpeg on PATH.")
    os.environ.pop("NO_COLOR", None)
    os.environ["TERM"] = "xterm-256color"
    os.environ["COLORTERM"] = "truecolor"
    for name in ("TYPESAFE_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        os.environ.pop(name, None)
    with (
        tempfile.TemporaryDirectory(prefix="jevlab-demo-") as directory,
        patch("socket.socket.connect", side_effect=blocked),
        patch("socket.socket.connect_ex", side_effect=blocked),
        patch("jevlab.core.credentials.native_store", side_effect=blocked),
    ):
        temporary = Path(directory)
        frames = asyncio.run(capture(temporary))
        # Quick Look centers SVGs in square thumbnails. Preserve the exported
        # terminal's aspect ratio by removing only that renderer-added padding.
        viewbox = ET.parse(frames[0][0]).getroot().attrib["viewBox"].split()
        width, height = float(viewbox[2]), float(viewbox[3])
        scale = 1200 / max(width, height)
        crop = f"crop={round(width * scale)}:{round(height * scale)}"
        manifest: list[str] = []
        for svg, duration in frames:
            subprocess.run(
                [renderer, "-t", "-s", "1200", "-o", directory, str(svg)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            manifest.extend([f"file '{svg.name}.png'", f"duration {duration}"])
        # The concat reader needs the last frame repeated to retain its duration.
        manifest.append(f"file '{frames[-1][0].name}.png'")
        (temporary / "frames.txt").write_text("\n".join(manifest) + "\n", encoding="utf-8")
        destination = temporary / "demo.gif"
        subprocess.run(
            [
                encoder,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                "frames.txt",
                "-filter_complex",
                f"[0:v]{crop},split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];"
                "[b][p]paletteuse=dither=none",
                "-fps_mode",
                "vfr",
                "-loop",
                "0",
                str(destination),
            ],
            cwd=temporary,
            check=True,
        )
        if destination.stat().st_size > 1_000_000:
            raise RuntimeError("The generated GIF exceeds 1 MB; reduce its size before publishing.")
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(destination, OUTPUT)
    print(
        f"docs/assets/demo.gif ({OUTPUT.stat().st_size:,} bytes); recorded examples, no API calls"
    )


if __name__ == "__main__":
    main()
