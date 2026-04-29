from __future__ import annotations

from . import run_release_scenarios


def test_release_scenarios() -> None:
    assert run_release_scenarios.main() == 0
