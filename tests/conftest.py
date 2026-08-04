"""Shared pytest fixtures for gimp-mcp offline tests."""

from __future__ import annotations

from pathlib import Path

import pytest

import gimp_mcp_security as sec


@pytest.fixture
def tmp_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Per-test temp workspace with ``GIMP_WORKSPACE_ROOT`` set (path jail)."""
    monkeypatch.setenv(sec.ENV_WORKSPACE, str(tmp_path))
    return Path(tmp_path)
