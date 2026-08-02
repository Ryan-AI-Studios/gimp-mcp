"""Offline smoke tests for quality gates (no GIMP process required)."""

from __future__ import annotations

from pathlib import Path


def test_repo_root_has_mcp_server_module() -> None:
    root = Path(__file__).resolve().parents[1]
    assert (root / "gimp_mcp_server.py").is_file()
    assert (root / "gimp-mcp-plugin.py").is_file()


def test_gimp_mcp_server_imports() -> None:
    import gimp_mcp_server

    assert hasattr(gimp_mcp_server, "main")
