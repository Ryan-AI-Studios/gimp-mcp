"""Pure + host unit tests for snapshot budget policy (track 0023)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastmcp.exceptions import ToolError

import gimp_mcp_security as sec
import gimp_mcp_snapshot as snap

# ---------------------------------------------------------------------------
# resolve_snapshot_max_box / clamp_edge
# ---------------------------------------------------------------------------


def test_default_box() -> None:
    budget = snap.resolve_snapshot_max_box()
    assert budget.max_width == 1024
    assert budget.max_height == 1024
    assert budget.region is None


def test_explicit_512_and_clamp_8000() -> None:
    b512 = snap.resolve_snapshot_max_box(512, 512)
    assert (b512.max_width, b512.max_height) == (512, 512)

    b_hard = snap.resolve_snapshot_max_box(8000, 8000)
    assert b_hard.max_width == snap.HARD_MAX_SNAPSHOT_EDGE
    assert b_hard.max_height == snap.HARD_MAX_SNAPSHOT_EDGE


def test_single_dim_square_after_clamp() -> None:
    only_w = snap.resolve_snapshot_max_box(max_width=640)
    assert (only_w.max_width, only_w.max_height) == (640, 640)

    only_h = snap.resolve_snapshot_max_box(max_height=480)
    assert (only_h.max_width, only_h.max_height) == (480, 480)

    # Single dim above hard max → square at hard max
    huge = snap.resolve_snapshot_max_box(max_width=9000)
    assert (huge.max_width, huge.max_height) == (
        snap.HARD_MAX_SNAPSHOT_EDGE,
        snap.HARD_MAX_SNAPSHOT_EDGE,
    )


def test_max_size_path() -> None:
    b = snap.resolve_snapshot_max_box(max_size=768)
    assert (b.max_width, b.max_height) == (768, 768)

    clamped = snap.resolve_snapshot_max_box(max_size=99999)
    assert clamped.max_width == snap.HARD_MAX_SNAPSHOT_EDGE

    with pytest.raises(ValueError, match="positive"):
        snap.resolve_snapshot_max_box(max_size=0)
    with pytest.raises(ValueError, match="positive"):
        snap.resolve_snapshot_max_box(max_size=-10)


def test_clamp_edge_rejects_non_positive_and_none() -> None:
    with pytest.raises(TypeError, match="non-None"):
        snap.clamp_edge(None)
    with pytest.raises(ValueError, match="positive"):
        snap.clamp_edge(0)
    with pytest.raises(ValueError, match="positive"):
        snap.clamp_edge(-5)
    assert snap.clamp_edge(100, hard_max=50) == 50
    assert snap.clamp_edge(40, hard_max=50) == 40


def test_env_override_default_and_hard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(snap.ENV_SNAPSHOT_MAX_EDGE, "512")
    monkeypatch.setenv(snap.ENV_SNAPSHOT_HARD_MAX_EDGE, "2048")
    assert snap.default_snapshot_max_edge() == 512
    assert snap.hard_max_snapshot_edge() == 2048
    b = snap.resolve_snapshot_max_box()
    assert (b.max_width, b.max_height) == (512, 512)
    # Explicit above env hard max clamps to 2048
    big = snap.resolve_snapshot_max_box(max_width=4096, max_height=4096)
    assert big.max_width == 2048

    # Invalid env → defaults
    monkeypatch.setenv(snap.ENV_SNAPSHOT_MAX_EDGE, "nope")
    monkeypatch.setenv(snap.ENV_SNAPSHOT_HARD_MAX_EDGE, "nope")
    assert snap.default_snapshot_max_edge() == snap.DEFAULT_SNAPSHOT_MAX_EDGE
    assert snap.hard_max_snapshot_edge() == snap.HARD_MAX_SNAPSHOT_EDGE

    # Dict environ without mutating process env
    env = {
        snap.ENV_SNAPSHOT_MAX_EDGE: "256",
        snap.ENV_SNAPSHOT_HARD_MAX_EDGE: "1024",
    }
    assert snap.default_snapshot_max_edge(env) == 256
    assert snap.resolve_snapshot_max_box(environ=env).max_width == 256


def test_default_edge_clamped_to_hard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(snap.ENV_SNAPSHOT_MAX_EDGE, "4096")
    monkeypatch.setenv(snap.ENV_SNAPSHOT_HARD_MAX_EDGE, "512")
    assert snap.default_snapshot_max_edge() == 512


# ---------------------------------------------------------------------------
# Region edges + M3 fill
# ---------------------------------------------------------------------------


def test_region_width_9000_reject() -> None:
    with pytest.raises(ValueError, match="MAX_REGION_EDGE"):
        snap.validate_region_edges({"width": 9000, "height": 100})
    with pytest.raises(ValueError, match="MAX_REGION_EDGE"):
        snap.validate_region_edges({"width": 100, "height": 9000})
    with pytest.raises(ValueError, match="MAX_REGION_EDGE"):
        snap.resolve_snapshot_max_box(
            region={"origin_x": 0, "origin_y": 0, "width": 9000, "height": 100}
        )


def test_region_only_max_width_filled() -> None:
    """M3: region with only max_width → fill max_height from full-image box."""
    budget = snap.resolve_snapshot_max_box(
        max_width=1024,
        max_height=768,
        region={
            "origin_x": 10,
            "origin_y": 20,
            "width": 200,
            "height": 100,
            "max_width": 512,
        },
    )
    assert budget.region is not None
    assert budget.region["max_width"] == 512
    assert budget.region["max_height"] == 768  # filled from full box
    assert budget.max_width == 1024
    assert budget.max_height == 768


def test_region_neither_max_inherits_box() -> None:
    budget = snap.resolve_snapshot_max_box(
        region={"x": 0, "y": 0, "width": 100, "height": 80},
    )
    assert budget.region is not None
    assert budget.region["max_width"] == 1024
    assert budget.region["max_height"] == 1024
    assert budget.region["origin_x"] == 0
    assert budget.region["width"] == 100


def test_region_both_max_clamped() -> None:
    budget = snap.resolve_snapshot_max_box(
        region={
            "origin_x": 0,
            "origin_y": 0,
            "width": 100,
            "height": 100,
            "max_width": 8000,
            "max_height": 100,
        },
    )
    assert budget.region is not None
    assert budget.region["max_width"] == snap.HARD_MAX_SNAPSHOT_EDGE
    assert budget.region["max_height"] == 100


# ---------------------------------------------------------------------------
# Timeout + probe fields
# ---------------------------------------------------------------------------


def test_command_timeout_parse_and_clamp(monkeypatch: pytest.MonkeyPatch) -> None:
    assert snap.command_timeout_s({}) == 60.0
    assert snap.command_timeout_s({snap.ENV_COMMAND_TIMEOUT_S: "120"}) == 120.0
    assert snap.command_timeout_s({snap.ENV_COMMAND_TIMEOUT_S: "1"}) == 5.0
    assert snap.command_timeout_s({snap.ENV_COMMAND_TIMEOUT_S: "9999"}) == 600.0
    assert snap.command_timeout_s({snap.ENV_COMMAND_TIMEOUT_S: "bogus"}) == 60.0
    assert snap.command_timeout_s({snap.ENV_COMMAND_TIMEOUT_S: "nan"}) == 60.0

    monkeypatch.setenv(snap.ENV_COMMAND_TIMEOUT_S, "45.5")
    assert snap.command_timeout_s() == 45.5


def test_snapshot_budget_probe_fields_shape() -> None:
    fields = snap.snapshot_budget_probe_fields()
    assert fields["default_max_edge"] == 1024
    assert fields["hard_max_edge"] == 4096
    assert fields["max_region_edge"] == 8192
    assert isinstance(fields["command_timeout_s"], float)
    assert fields["command_timeout_s"] == 60.0
    env_names = fields["env_names"]
    assert env_names["snapshot_max_edge"] == "GIMP_MCP_SNAPSHOT_MAX_EDGE"
    assert env_names["snapshot_hard_max_edge"] == "GIMP_MCP_SNAPSHOT_HARD_MAX_EDGE"
    assert env_names["command_timeout_s"] == "GIMP_MCP_COMMAND_TIMEOUT_S"
    assert "region-first" in fields["guidance"]
    assert "summary_only" in fields["guidance"]


def test_probe_fields_resolved_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(snap.ENV_SNAPSHOT_MAX_EDGE, "768")
    monkeypatch.setenv(snap.ENV_COMMAND_TIMEOUT_S, "90")
    fields = snap.snapshot_budget_probe_fields()
    assert fields["default_max_edge"] == 768
    assert fields["command_timeout_s"] == 90.0


# ---------------------------------------------------------------------------
# Structure greps (dead const / settimeout / shared path)
# ---------------------------------------------------------------------------


def test_structure_no_default_timeout_seconds_in_plugin() -> None:
    text = Path("gimp-mcp-plugin.py").read_text(encoding="utf-8")
    assert "DEFAULT_TIMEOUT_SECONDS" not in text


def test_structure_no_settimeout_10_on_connection() -> None:
    text = Path("gimp_mcp_server.py").read_text(encoding="utf-8")
    assert "settimeout(10)" not in text
    assert "command_timeout_s" in text
    # connect() path uses snap.command_timeout_s
    assert "self.sock.settimeout" in text


def test_structure_shared_impl_and_max_size_default() -> None:
    text = Path("gimp_mcp_server.py").read_text(encoding="utf-8")
    assert "def _render_visible_composite_impl" in text
    # get_state_snapshot default max_size = 1024
    assert "max_size: int = 1024" in text
    assert "resolve_snapshot_max_box" in text
    # Both public tools call shared impl
    assert text.count("_render_visible_composite_impl(") >= 2


def test_structure_plugin_uses_max_region_edge() -> None:
    text = Path("gimp-mcp-plugin.py").read_text(encoding="utf-8")
    assert "MAX_REGION_EDGE" in text or "_snap.MAX_REGION_EDGE" in text
    # Local unused MAX_REGION_SIZE constant removed
    assert "MAX_REGION_SIZE = 8192" not in text


# ---------------------------------------------------------------------------
# Host pre-TCP reject (M4) + default box before send
# ---------------------------------------------------------------------------


def test_oversized_region_rejects_before_send(monkeypatch: pytest.MonkeyPatch) -> None:
    """M4: region edge over cap → fail before get_gimp_connection / send_command."""
    import gimp_mcp_server as srv

    called: list[str] = []

    def _boom_conn(*_a: Any, **_k: Any) -> Any:
        called.append("get_gimp_connection")
        raise AssertionError("TCP must not be reached for oversized region")

    monkeypatch.setattr(srv, "get_gimp_connection", _boom_conn)
    monkeypatch.setattr(srv.sec, "write_audit_event", lambda *a, **k: None)

    with pytest.raises(ToolError) as ei:
        srv._render_visible_composite_impl(
            image_index=0,
            region={"origin_x": 0, "origin_y": 0, "width": 9000, "height": 100},
        )
    assert called == []
    parsed = sec.parse_tool_error_text(str(ei.value))
    assert parsed is not None
    assert parsed["error"]["code"] == sec.CODE_POLICY_DENIED
    assert "8192" in parsed["error"]["message"] or "MAX_REGION" in parsed["error"]["message"]


def test_missing_max_dims_send_complete_default_box(monkeypatch: pytest.MonkeyPatch) -> None:
    """Omit max_* → params always carry complete 1024x1024 box before send."""
    import gimp_mcp_server as srv

    sent: dict[str, Any] = {}

    class _Conn:
        def send_command(self, command_type: str, params: dict[str, Any] | None = None) -> dict:
            sent["type"] = command_type
            sent["params"] = dict(params or {})
            return {
                "status": "success",
                "results": {
                    "image_data": "iVBORw0KGgo=",
                    "format": "png",
                    "width": 64,
                    "height": 64,
                    "original_width": 1920,
                    "original_height": 1080,
                    "image_index": 0,
                },
            }

    monkeypatch.setattr(srv, "get_gimp_connection", lambda: _Conn())
    # Avoid filesystem write side effects
    monkeypatch.setattr(
        srv,
        "_snapshot_tool_result",
        lambda results, **kw: MagicMock(name="ToolResult"),
    )

    srv._render_visible_composite_impl(image_index=0)
    assert sent["type"] == "get_image_bitmap"
    assert sent["params"]["max_width"] == 1024
    assert sent["params"]["max_height"] == 1024


def test_get_image_bitmap_and_render_share_impl() -> None:
    """B1: both advanced alias and HL share _render_visible_composite_impl."""
    text = Path("gimp_mcp_server.py").read_text(encoding="utf-8")
    # Locate each public tool body and assert shared impl call
    for name in ("def render_visible_composite(", "def get_image_bitmap("):
        start = text.index(name)
        body = text[start : start + 2500]
        assert "_render_visible_composite_impl(" in body, f"{name} missing shared impl"


def test_surface_probe_includes_snapshot_budget() -> None:
    import gimp_mcp_server as srv

    fields = srv._surface_probe_fields()
    assert "snapshot_budget" in fields
    sb = fields["snapshot_budget"]
    assert isinstance(sb["command_timeout_s"], float)
    assert "env_names" in sb
    assert sb["default_max_edge"] == 1024


def test_timeout_error_maps_to_code_timeout() -> None:
    import gimp_mcp_server as srv

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(srv.sec, "write_audit_event", lambda *a, **k: None)
        with pytest.raises(ToolError) as ei:
            srv.raise_from_exception(
                TimeoutError("recv timed out"),
                request_id="req_" + "a" * 32,
                tool_name="render_visible_composite",
            )
    parsed = sec.parse_tool_error_text(str(ei.value))
    assert parsed is not None
    assert parsed["error"]["code"] == sec.CODE_TIMEOUT
    details = parsed["error"].get("details") or {}
    assert "timeout_s" in details
    assert isinstance(details["timeout_s"], (int, float))
    assert parsed["error"]["retryable"] is True
