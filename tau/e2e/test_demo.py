"""Демо-стенд: `scripts/demo.sh` проходит от шаблона до связей."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_demo_script_runs_end_to_end() -> None:
    env = {
        **os.environ,
        "PYTHON": sys.executable,
        "PYTHONPATH": str(REPO),
        "KEEP": "0",
    }
    result = subprocess.run(
        ["bash", str(REPO / "scripts" / "demo.sh")],
        capture_output=True,
        text=True,
        cwd=REPO,
        env=env,
        timeout=180,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    printed = result.stdout
    assert "template video-archive" in printed
    assert "applied: 4" in printed, "источник должен записать четыре объекта"
    assert "related <- ep-a" in printed, "связи должны быть видны через links"
    assert "файлы источника не менялись" in printed
