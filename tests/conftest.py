"""Shared fixtures: a synthetic demo run in a temporary repo root."""
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def demo_ctx(tmp_path_factory):
    """Build the full demo pipeline once in a temp root and return its context."""
    root = tmp_path_factory.mktemp("repo")
    (root / "config").mkdir()
    shutil.copy(REPO / "config" / "sites.yaml", root / "config" / "sites.yaml")
    import os
    os.environ["MM_ROOT"] = str(root)
    from metals_monitor import cli
    args = cli.common_parser().parse_args(["--demo", "--start", "2019-01-01", "--end", "2026-10-01"])
    ctx = cli.build_context(args)
    for name in cli.DEFAULT_SEQUENCE:
        cli.STEPS[name](ctx)
    return ctx


@pytest.fixture
def blank_args():
    return SimpleNamespace(anomaly_months=6)
