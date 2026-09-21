"""Load a small, explicitly illustrative recording without touching user storage."""

import json
from importlib.resources import files
from typing import Literal

from typesafe_sdk import SystemOneResponse

from jev.core.models import Run, StrictModel


class DemoRecording(StrictModel):
    schema_version: Literal[1] = 1
    title: str
    provenance: str
    disclaimer: str
    run: Run


def load_demo() -> DemoRecording:
    recording = DemoRecording.model_validate_json(
        files("jev.resources").joinpath("demo.json").read_text(encoding="utf-8")
    )
    # Detect an incompatible or damaged bundled example without contacting any provider.
    SystemOneResponse.model_validate_json(json.dumps(recording.run.response))
    return recording
