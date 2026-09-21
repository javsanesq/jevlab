import os
from pathlib import Path

import pytest

from jev.core.models import Settings
from jev.core.service import Workbench


@pytest.mark.live
async def test_live_jev(tmp_path: Path) -> None:
    if not os.environ.get("TYPESAFE_API_KEY"):
        pytest.skip("TYPESAFE_API_KEY is required")
    wb = Workbench(tmp_path / "live")
    wb.update_settings(Settings(credential_mode="environment"))
    template = wb.templates.load("support-triage")
    run = await wb.run(template, {"ticket": {"message": "Please refund my duplicate charge."}})
    assert run.status == "succeeded"
    assert run.response
    answers = run.response["answers"]
    assert isinstance(answers, dict) and set(answers) == set(template.questions)
