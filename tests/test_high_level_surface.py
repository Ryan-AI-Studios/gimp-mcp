"""Offline tests for track 0010 high-level MCP surface."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from gimp_mcp_surface import (
    ADVANCED_TAG,
    ENV_ADVANCED_TOOLS,
    HL_TAG,
    HL_TOOL_NAMES,
    advanced_tools_enabled,
    get_hl_catalog_names,
    include_tags_for_mode,
    is_hl_tool,
    soft_version_ok,
    surface_mode,
    validate_create_selection_params,
)

# ---------------------------------------------------------------------------
# Pure surface helpers
# ---------------------------------------------------------------------------


def test_hl_catalog_exact_25() -> None:
    names = get_hl_catalog_names()
    assert len(names) == 25
    assert set(names) == HL_TOOL_NAMES
    assert names == sorted(names)
    assert "compare_images" in HL_TOOL_NAMES
    assert "verify_artifact" in HL_TOOL_NAMES
    assert "list_recipes" in HL_TOOL_NAMES
    assert "apply_recipe" in HL_TOOL_NAMES
    assert "apply_nde_filter" in HL_TOOL_NAMES
    assert "edit_filter_config" in HL_TOOL_NAMES
    assert "remove_nde_filter" in HL_TOOL_NAMES
    assert "batch_run" not in HL_TOOL_NAMES
    assert "list_drawable_filters" not in HL_TOOL_NAMES
    assert "merge_nde_filters" not in HL_TOOL_NAMES


def test_is_hl_tool() -> None:
    assert is_hl_tool("session_probe")
    assert is_hl_tool("create_selection")
    assert is_hl_tool("apply_nde_filter")
    assert not is_hl_tool("get_image_bitmap")
    assert not is_hl_tool("blur")
    assert not is_hl_tool("list_drawable_filters")
    assert not is_hl_tool("merge_nde_filters")


def test_advanced_tools_enabled_truthy() -> None:
    assert advanced_tools_enabled({ENV_ADVANCED_TOOLS: "1"}) is True
    assert advanced_tools_enabled({ENV_ADVANCED_TOOLS: "true"}) is True
    assert advanced_tools_enabled({ENV_ADVANCED_TOOLS: "YES"}) is True
    assert advanced_tools_enabled({ENV_ADVANCED_TOOLS: "on"}) is True
    assert advanced_tools_enabled({ENV_ADVANCED_TOOLS: "0"}) is False
    assert advanced_tools_enabled({ENV_ADVANCED_TOOLS: "false"}) is False
    assert advanced_tools_enabled({}) is False


def test_surface_mode_and_include_tags() -> None:
    assert surface_mode({ENV_ADVANCED_TOOLS: ""}) == "high-level"
    assert surface_mode({ENV_ADVANCED_TOOLS: "1"}) == "advanced"
    assert surface_mode(advanced_mode=False) == "high-level"
    assert surface_mode(advanced_mode=True) == "advanced"
    # advanced_mode overrides env
    assert surface_mode({ENV_ADVANCED_TOOLS: "1"}, advanced_mode=False) == "high-level"

    assert include_tags_for_mode("high-level") == {HL_TAG}
    assert include_tags_for_mode("advanced") is None
    with pytest.raises(ValueError, match="unknown"):
        include_tags_for_mode("bogus")


def test_soft_version_ok() -> None:
    assert soft_version_ok("3.2.4", "3.2") is True
    assert soft_version_ok("3.1", "3.2") is False
    assert soft_version_ok("3.2.0", "3.2") is True
    assert soft_version_ok(None, "3.2") is None
    assert soft_version_ok("3.2", None) is None
    assert soft_version_ok("v3.2", "3.2") is None  # unparseable
    assert soft_version_ok("3.2-rc1", "3.2") is None


def test_validate_create_selection_rectangle() -> None:
    out = validate_create_selection_params(
        {"type": "Rectangle", "x": 1, "y": 2, "width": 10, "height": 20, "operation": "ADD"}
    )
    assert out["type"] == "rectangle"
    assert out["operation"] == "add"
    assert out["x"] == 1 and out["y"] == 2
    assert out["width"] == 10 and out["height"] == 20
    assert out["feather"] == 0.0


def test_validate_create_selection_feather() -> None:
    out = validate_create_selection_params(
        {
            "type": "ellipse",
            "x": 0,
            "y": 0,
            "width": 5,
            "height": 5,
            "feather": 2.5,
        }
    )
    assert out["feather"] == 2.5


def test_validate_create_selection_by_color_requires_color() -> None:
    with pytest.raises(ValueError, match="requires color"):
        validate_create_selection_params({"type": "by_color"})
    out = validate_create_selection_params(
        {"type": "by_color", "color": "#ff0000", "threshold": 10}
    )
    assert out["color"] == "#ff0000"
    assert out["threshold"] == 10


def test_validate_create_selection_all_none() -> None:
    assert validate_create_selection_params({"type": "all"})["type"] == "all"
    assert validate_create_selection_params({"type": "NONE"})["type"] == "none"


def test_validate_create_selection_bad_type_and_geometry() -> None:
    with pytest.raises(ValueError, match="unknown type"):
        validate_create_selection_params({"type": "lasso"})
    with pytest.raises(ValueError, match="requires x"):
        validate_create_selection_params({"type": "rectangle", "y": 0, "width": 1, "height": 1})
    with pytest.raises(ValueError, match="width and height"):
        validate_create_selection_params(
            {"type": "rectangle", "x": 0, "y": 0, "width": 0, "height": 1}
        )
    with pytest.raises(ValueError, match="unknown operation"):
        validate_create_selection_params({"type": "all", "operation": "xor"})


# ---------------------------------------------------------------------------
# Server factory + list_tools contracts (real FastMCP)
# ---------------------------------------------------------------------------


def test_create_mcp_server_factory_include_tags() -> None:
    import gimp_mcp_server as srv

    m_hl = srv.create_mcp_server(advanced_mode=False)
    assert m_hl.include_tags == {HL_TAG}
    m_adv = srv.create_mcp_server(advanced_mode=True)
    assert m_adv.include_tags is None


def test_module_mcp_is_real_fastmcp() -> None:
    from fastmcp import FastMCP as RealFastMCP

    import gimp_mcp_server as srv

    assert isinstance(srv.mcp, RealFastMCP)
    # H1: FuncMetadata monkeypatch and shim import must be gone
    assert not hasattr(srv, "_convert_result_passthrough")
    src = open(srv.__file__, encoding="utf-8").read()
    assert "mcp.server.fastmcp" not in src
    assert "FuncMetadata" not in src


def _list_tool_names(mcp: Any) -> list[str]:
    async def _run() -> list[str]:
        tools = await mcp._list_tools()
        return sorted(t.name for t in tools)

    return asyncio.run(_run())


def _list_prompt_names(mcp: Any) -> list[str]:
    async def _run() -> list[str]:
        prompts = await mcp._list_prompts()
        return sorted(p.name for p in prompts)

    return asyncio.run(_run())


def test_default_list_tools_exactly_hl_catalog() -> None:
    import gimp_mcp_server as srv

    # Mutate include_tags on live registration (factory-style filter test)
    prev = srv.mcp.include_tags
    try:
        srv.mcp.include_tags = {HL_TAG}
        names = _list_tool_names(srv.mcp)
        assert names == get_hl_catalog_names()
        assert "get_image_bitmap" not in names
        assert "check_server" not in names
        assert "session_probe" in names
        assert "render_visible_composite" in names
        assert "create_selection" in names
    finally:
        srv.mcp.include_tags = prev


def test_advanced_list_tools_includes_legacy() -> None:
    import gimp_mcp_server as srv

    prev = srv.mcp.include_tags
    try:
        srv.mcp.include_tags = None
        names = _list_tool_names(srv.mcp)
        assert len(names) >= 80
        assert "get_image_bitmap" in names
        assert "check_server" in names
        assert "blur" in names
        assert "session_probe" in names
        assert "create_selection" in names
        # Advanced NDE inventory / merge (not HL)
        assert "list_drawable_filters" in names
        assert "merge_nde_filters" in names
    finally:
        srv.mcp.include_tags = prev


def test_prompts_listed_in_hl_mode() -> None:
    import gimp_mcp_server as srv

    prev = srv.mcp.include_tags
    try:
        srv.mcp.include_tags = {HL_TAG}
        prompts = _list_prompt_names(srv.mcp)
        assert "gimp_best_practices" in prompts
        assert "gimp_iterative_workflow" in prompts
    finally:
        srv.mcp.include_tags = prev


def test_every_registered_tool_has_tags() -> None:
    import gimp_mcp_server as srv

    async def _check() -> None:
        # get_tools returns all registered regardless of include_tags
        tools = await srv.mcp.get_tools()
        assert len(tools) >= 80
        for name, tool in tools.items():
            tags = getattr(tool, "tags", None) or set()
            assert tags, f"tool {name} has empty tags"
            assert tags == {HL_TAG} or tags == {ADVANCED_TAG}, f"tool {name} tags={tags}"

    asyncio.run(_check())


def test_hl_tools_have_annotations() -> None:
    import gimp_mcp_server as srv

    async def _check() -> None:
        tools = await srv.mcp.get_tools()
        for name in HL_TOOL_NAMES:
            assert name in tools, f"missing HL tool {name}"
            tool = tools[name]
            ann = getattr(tool, "annotations", None)
            assert ann is not None, f"HL tool {name} missing annotations"
            # readOnly tools must not set destructiveHint
            ro = getattr(ann, "readOnlyHint", None)
            dest = getattr(ann, "destructiveHint", None)
            if ro is True:
                assert dest is None or dest is False

    asyncio.run(_check())


def _tool_fn(tool: Any) -> Any:
    """Real FastMCP wraps callables in FunctionTool; tests call the underlying fn."""
    return getattr(tool, "fn", tool)


def test_session_probe_disconnected_shape() -> None:
    import gimp_mcp_server as srv

    # Force disconnect path: reset connection and use unreachable port via mock
    # call the tool function directly with a dummy ctx
    class _Ctx:
        pass

    # session_probe when GIMP is down should still report surface mode
    out = _tool_fn(srv.session_probe)(_Ctx())  # type: ignore[arg-type]
    assert "connected" in out
    assert out["tool_surface"] in ("high-level", "advanced")
    assert "advanced_tools_enabled" in out
    assert out["hl_tool_names"] == get_hl_catalog_names()
    assert "capabilities" in out
    assert out["capabilities"].get("high_level_mcp_surface") is True
    if not out["connected"]:
        assert "error" in out
        assert "host" in out and "port" in out


def test_create_selection_validation_before_tcp(monkeypatch: pytest.MonkeyPatch) -> None:
    import gimp_mcp_server as srv

    class _Ctx:
        pass

    called: list[Any] = []

    def _boom(*_a: Any, **_k: Any) -> Any:
        called.append(1)
        raise AssertionError("TCP should not be reached")

    monkeypatch.setattr(srv, "get_gimp_connection", _boom)

    create_selection = _tool_fn(srv.create_selection)
    with pytest.raises(Exception, match=r"create_selection|type|color"):
        create_selection(_Ctx(), type="by_color")  # type: ignore[arg-type]
    assert called == []  # validation failed before connection

    with pytest.raises(Exception, match=r"unknown type|create_selection"):
        create_selection(_Ctx(), type="lasso")  # type: ignore[arg-type]


def test_ensure_and_checkpoint_accept_handle_signature() -> None:
    import inspect

    import gimp_mcp_server as srv

    for name in (
        "ensure_source_immutable",
        "checkpoint_create",
        "checkpoint_restore",
        "save_xcf",
        "export_image",
        "verify_alpha_channel",
        "close_image",
        "render_visible_composite",
    ):
        sig = inspect.signature(_tool_fn(getattr(srv, name)))
        assert "handle" in sig.parameters, f"{name} missing handle param"


def test_plugin_handle_resolve_for_hl_mutators() -> None:
    """Plugin must resolve handle for save/export/verify/close (P1-1)."""
    text = open("gimp-mcp-plugin.py", encoding="utf-8").read()
    for snippet in (
        "def _save_xcf",
        "def _export_image",
        "def _verify_alpha_channel",
        "def _close_image",
    ):
        assert snippet in text, f"missing {snippet}"
        start = text.index(snippet)
        body = text[start : start + 1200]
        assert "_resolve_image_from_params" in body, f"{snippet} missing handle resolve"


def test_checkpoint_restore_close_prior_handle_fail_closed() -> None:
    """Explicit prior handle must not be swallowed on HandleError (Codex P1-1)."""
    text = open("gimp-mcp-plugin.py", encoding="utf-8").read()
    start = text.index("def _checkpoint_restore")
    body = text[start : start + 4500]
    # Must return handle error response, not silent prior = None
    assert "return self._handle_error_response(e)" in body
    assert "except _handles.HandleError as e:" in body
    # The silent swallow pattern must not remain for prior_handle
    assert "except _handles.HandleError:\n                            prior = None" not in body


def test_get_image_bitmap_derives_image_index_from_handle() -> None:
    """Composite mapping must not hardcode image_index=0 after handle resolve."""
    text = open("gimp-mcp-plugin.py", encoding="utf-8").read()
    start = text.index("def _get_current_image_bitmap")
    body = text[start : start + 2500]
    assert "_resolve_image_from_params" in body
    assert "images_open" in body or "Gimp.get_images()" in body
    assert "enumerate" in body


def test_save_xcf_forwards_handle_only(monkeypatch: pytest.MonkeyPatch) -> None:
    import gimp_mcp_server as srv

    class _Ctx:
        pass

    captured: list[dict[str, Any]] = []

    class _Conn:
        def send_command(self, cmd: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
            captured.append({"cmd": cmd, "params": params or {}})
            return {
                "status": "success",
                "results": {
                    "file_path": "x.xcf",
                    "bytes": 1,
                    "sha256": "a" * 64,
                    "collision": "fail",
                    "collision_resolved": False,
                    "backup_path": None,
                    "atomic": True,
                    "reopen_verified": True,
                },
            }

    monkeypatch.setattr(srv, "get_gimp_connection", lambda: _Conn())
    monkeypatch.setattr(srv, "_jail_path_or_raise", lambda p, label="path": p)

    handle = {"image_id": 9, "generation": 1, "session_epoch": 1}
    _tool_fn(srv.save_xcf)(_Ctx(), file_path="out.xcf", handle=handle)  # type: ignore[arg-type]
    assert captured and captured[0]["cmd"] == "save_xcf"
    assert captured[0]["params"].get("handle") == handle
    assert "image_index" not in captured[0]["params"]
    assert captured[0]["params"].get("collision") == "fail"
    assert captured[0]["params"].get("verify_reopen") is True


def test_save_xcf_forwards_collision(monkeypatch: pytest.MonkeyPatch) -> None:
    import gimp_mcp_server as srv

    class _Ctx:
        pass

    captured: list[dict[str, Any]] = []

    class _Conn:
        def send_command(self, cmd: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
            captured.append({"cmd": cmd, "params": params or {}})
            return {
                "status": "success",
                "results": {
                    "file_path": "out-1.xcf",
                    "bytes": 2,
                    "sha256": "b" * 64,
                    "collision": "version",
                    "collision_resolved": True,
                    "backup_path": None,
                    "atomic": True,
                    "reopen_verified": False,
                },
            }

    monkeypatch.setattr(srv, "get_gimp_connection", lambda: _Conn())
    monkeypatch.setattr(srv, "_jail_path_or_raise", lambda p, label="path": p)
    _tool_fn(srv.save_xcf)(  # type: ignore[arg-type]
        _Ctx(),
        file_path="out.xcf",
        image_index=0,
        collision="version",
        verify_reopen=False,
    )
    assert captured[0]["params"]["collision"] == "version"
    assert captured[0]["params"]["verify_reopen"] is False


def test_save_xcf_invalid_collision_policy_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastmcp.exceptions import ToolError

    import gimp_mcp_server as srv

    class _Ctx:
        pass

    monkeypatch.setattr(srv, "_jail_path_or_raise", lambda p, label="path": p)
    with pytest.raises(ToolError) as ei:
        _tool_fn(srv.save_xcf)(  # type: ignore[arg-type]
            _Ctx(),
            file_path="out.xcf",
            image_index=0,
            collision="overwrite",
        )
    text = str(ei.value)
    assert "POLICY_DENIED" in text or "invalid collision" in text


def test_export_image_forwards_collision(monkeypatch: pytest.MonkeyPatch) -> None:
    import gimp_mcp_server as srv

    class _Ctx:
        pass

    captured: list[dict[str, Any]] = []

    class _Conn:
        def send_command(self, cmd: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
            captured.append({"cmd": cmd, "params": params or {}})
            return {
                "status": "success",
                "results": {
                    "file_path": "out.png",
                    "format": "png",
                    "file_size_bytes": 10,
                    "atomic": True,
                    "collision": "replace",
                    "sha256": "c" * 64,
                },
            }

    monkeypatch.setattr(srv, "get_gimp_connection", lambda: _Conn())
    monkeypatch.setattr(srv, "_jail_path_or_raise", lambda p, label="path": p)
    _tool_fn(srv.export_image)(  # type: ignore[arg-type]
        _Ctx(),
        file_path="out.png",
        format="png",
        image_index=0,
        collision="replace",
    )
    assert captured[0]["cmd"] == "export_image"
    assert captured[0]["params"]["collision"] == "replace"
    # export has no verify_reopen
    assert "verify_reopen" not in captured[0]["params"]


def test_capability_high_level_surface() -> None:
    from gimp_mcp_state import default_capabilities

    caps = default_capabilities()
    assert caps["high_level_mcp_surface"] is True


def test_pyproject_pins_and_surface_module() -> None:
    text = open("pyproject.toml", encoding="utf-8").read()
    assert "gimp_mcp_surface" in text
    assert "mcp>=1.10,<2" in text or "mcp>=1.10,<2" in text
    assert "fastmcp>=2.10,<3" in text
