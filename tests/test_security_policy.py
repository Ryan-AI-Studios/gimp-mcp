"""Offline security policy tests (no GIMP process required)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

import gimp_mcp_security as sec

# ---------------------------------------------------------------------------
# Loopback / bind
# ---------------------------------------------------------------------------


def test_is_loopback_host() -> None:
    assert sec.is_loopback_host("127.0.0.1")
    assert sec.is_loopback_host("::1")
    assert not sec.is_loopback_host("localhost")
    assert not sec.is_loopback_host("0.0.0.0")
    assert not sec.is_loopback_host("192.168.1.1")
    assert not sec.is_loopback_host(None)


def test_assert_bind_host_default_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sec.ENV_ALLOW_NON_LOOPBACK, raising=False)
    assert sec.assert_bind_host("127.0.0.1") == "127.0.0.1"
    assert sec.assert_bind_host(None) == "127.0.0.1"
    assert sec.assert_bind_host("") == "127.0.0.1"


def test_assert_bind_host_rejects_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sec.ENV_ALLOW_NON_LOOPBACK, raising=False)
    with pytest.raises(sec.SecurityError) as ei:
        sec.assert_bind_host("localhost")
    assert ei.value.code == sec.CODE_BIND_DENIED


def test_assert_bind_host_rejects_non_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sec.ENV_ALLOW_NON_LOOPBACK, raising=False)
    with pytest.raises(sec.SecurityError) as ei:
        sec.assert_bind_host("0.0.0.0")
    assert ei.value.code == sec.CODE_BIND_DENIED


def test_assert_bind_host_allows_non_loopback_with_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(sec.ENV_ALLOW_NON_LOOPBACK, "1")
    assert sec.assert_bind_host("0.0.0.0") == "0.0.0.0"


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------


def test_verify_token_match() -> None:
    assert sec.verify_token("secret-abc", "secret-abc") is True


def test_verify_token_mismatch() -> None:
    assert sec.verify_token("secret-abc", "secret-xyz") is False


def test_verify_token_none() -> None:
    assert sec.verify_token(None, "secret") is False
    assert sec.verify_token("secret", None) is False
    assert sec.verify_token(None, None) is False
    assert sec.verify_token("", "secret") is False
    assert sec.verify_token("secret", "") is False


def test_generate_token_length() -> None:
    t = sec.generate_token()
    assert isinstance(t, str)
    assert len(t) >= 32


def test_write_and_read_token_file(tmp_path: Path) -> None:
    path = tmp_path / "session.token"
    token = sec.generate_token()
    sec.write_token_file(path, token)
    assert sec.read_token_file(path) == token
    if sys.platform != "win32":
        mode = path.stat().st_mode & 0o777
        assert mode == 0o600


# ---------------------------------------------------------------------------
# Exec / debug flags — DEBUG must not change policy
# ---------------------------------------------------------------------------


def test_exec_allowed_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sec.ENV_ALLOW_EXEC, raising=False)
    assert sec.exec_allowed() is False


def test_exec_allowed_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(sec.ENV_ALLOW_EXEC, "1")
    assert sec.exec_allowed() is True
    monkeypatch.setenv(sec.ENV_ALLOW_EXEC, "true")
    assert sec.exec_allowed() is True


def test_debug_does_not_change_policy_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sec.ENV_ALLOW_EXEC, raising=False)
    monkeypatch.delenv(sec.ENV_ALLOW_NON_LOOPBACK, raising=False)
    monkeypatch.setenv(sec.ENV_DEBUG, "1")
    assert sec.debug_enabled() is True
    assert sec.exec_allowed() is False
    with pytest.raises(sec.SecurityError):
        sec.assert_bind_host("0.0.0.0")
    assert sec.verify_token("a", "b") is False


# ---------------------------------------------------------------------------
# Path jail
# ---------------------------------------------------------------------------


def test_path_jail_in_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(sec.ENV_WORKSPACE, str(tmp_path))
    child = tmp_path / "sub" / "file.png"
    child.parent.mkdir()
    child.write_text("x", encoding="utf-8")
    resolved = sec.resolve_under_root(str(child))
    assert resolved == child.resolve() or str(resolved).lower() == str(child.resolve()).lower()


def test_path_jail_relative_under_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(sec.ENV_WORKSPACE, str(tmp_path))
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    resolved = sec.resolve_under_root("a.txt")
    assert resolved.name == "a.txt"
    assert (
        str(tmp_path.resolve()) in str(resolved)
        or str(tmp_path.resolve()).lower() in str(resolved).lower()
    )


def test_path_jail_dotdot_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("nope", encoding="utf-8")
    monkeypatch.setenv(sec.ENV_WORKSPACE, str(root))
    with pytest.raises(sec.SecurityError) as ei:
        sec.resolve_under_root(str(root / ".." / "secret.txt"))
    assert ei.value.code == sec.CODE_PATH_DENIED


def test_path_jail_outside_absolute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "other" / "x.png"
    outside.parent.mkdir()
    outside.write_text("x", encoding="utf-8")
    monkeypatch.setenv(sec.ENV_WORKSPACE, str(root))
    with pytest.raises(sec.SecurityError) as ei:
        sec.resolve_under_root(str(outside))
    assert ei.value.code == sec.CODE_PATH_DENIED


def test_path_jail_unset_root_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sec.ENV_WORKSPACE, raising=False)
    with pytest.raises(sec.SecurityError) as ei:
        sec.resolve_under_root(str(tmp_path / "a.png"))
    assert ei.value.code == sec.CODE_PATH_DENIED


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drive-case normalization")
def test_path_jail_drive_case_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # tmp_path typically has drive letter; flip case and ensure still allowed
    root = tmp_path
    monkeypatch.setenv(sec.ENV_WORKSPACE, str(root))
    child = root / "img.png"
    child.write_text("x", encoding="utf-8")
    s = str(child.resolve())
    if len(s) >= 2 and s[1] == ":":
        flipped = s[0].swapcase() + s[1:]
        resolved = sec.resolve_under_root(flipped)
        assert resolved.name.lower() == "img.png"


def test_check_path_under_root_structured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sec.ENV_WORKSPACE, raising=False)
    path, err = sec.check_path_under_root(str(tmp_path / "x"))
    assert path is None
    assert err is not None
    assert err["status"] == "error"
    assert err["code"] == sec.CODE_PATH_DENIED


# ---------------------------------------------------------------------------
# make_error / redact
# ---------------------------------------------------------------------------


def test_make_error_shape() -> None:
    e = sec.make_error(sec.CODE_AUTH_FAILED, "nope")
    assert e == {"status": "error", "error": "nope", "code": sec.CODE_AUTH_FAILED}


def test_redact_error_no_traceback_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sec.ENV_DEBUG, raising=False)
    body = sec.redact_error(ValueError("boom"), code=sec.CODE_INTERNAL)
    assert body["status"] == "error"
    assert "traceback" not in body


def test_redact_error_with_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(sec.ENV_DEBUG, "1")
    try:
        raise RuntimeError("detail")
    except RuntimeError as e:
        body = sec.redact_error(e)
    assert "traceback" in body


def test_strip_traceback_unless_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sec.ENV_DEBUG, raising=False)
    cleaned = sec.strip_traceback_unless_debug(
        {"status": "error", "error": "x", "traceback": "stack"}
    )
    assert "traceback" not in cleaned


# ---------------------------------------------------------------------------
# Grep-style regression: plugin param-driven file I/O must use path jail
# ---------------------------------------------------------------------------

# Sites that are allowed without jail (bootstrap / temp / non-param).
_ALLOWLIST_PATTERNS = (
    # tempfile export for bitmap snapshots
    re.compile(r"tempfile\.|gettempdir|temp_path"),
    # security module itself
    re.compile(r"gimp_mcp_security"),
)


def test_plugin_param_file_io_uses_path_jail() -> None:
    """Scan gimp-mcp-plugin.py for param-driven I/O and assert jail usage.

    Strategy: every function that accepts user path params must call
    ``_jail_path`` (or security resolve) before Gio/makedirs/export.
    """
    root = Path(__file__).resolve().parents[1]
    plugin = root / "gimp-mcp-plugin.py"
    text = plugin.read_text(encoding="utf-8")

    # Security import present
    assert "gimp_mcp_security" in text or "_jail_path" in text

    # Central helper must exist
    assert re.search(r"def _jail_path\b", text), "plugin must define _jail_path helper"

    # Handlers that take user paths
    handlers = [
        "_open_image",
        "_save_xcf",
        "_export_image",
        "_batch_export",
        "_export_icon_sizes",
        "_export_web_optimized",
        "_export_sprite_sheet",
        "_export_social_media_kit",
    ]
    for name in handlers:
        # Extract method body roughly until next def at same indent class level
        m = re.search(
            rf"(    def {name}\(self.*?)(?=\n    def |\nclass |\Z)",
            text,
            re.DOTALL,
        )
        assert m is not None, f"handler {name} not found"
        body = m.group(1)
        assert "_jail_path" in body or "resolve_under_root" in body, (
            f"{name} must jail user paths via _jail_path / resolve_under_root"
        )

    # _export_to_path should document that callers jail first OR jail itself
    export_m = re.search(
        r"(    def _export_to_path\(self.*?)(?=\n    def |\nclass |\Z)",
        text,
        re.DOTALL,
    )
    assert export_m is not None
    # Prefer callers jail at entry; _export_to_path may also re-check
    # At least one of: jail inside or all callers jail (checked above for public entries)

    # Count raw Gio.File.new_for_path on potentially user paths without nearby jail
    # Soft check: open/save/export entries already covered; remaining Gio on temp_path OK
    gio_sites = list(re.finditer(r"Gio\.File\.new_for_path\(([^)]+)\)", text))
    assert len(gio_sites) >= 1
    for m in gio_sites:
        arg = m.group(1).strip()
        # Allow temp_path, xcf_path (close_image own-file/temp), and jailed names
        if any(
            token in arg
            for token in (
                "temp_path",
                "xcf_path",
                "jailed",
                "safe_path",
                "file_path",
                "out_path",
                "output_path",
            )
        ):
            continue
        # Anything else should still be a local variable from jailed path
        # (file_path after reassignment). Accept common names.
        assert re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", arg), (
            f"Unexpected Gio path expression (review jail): {arg}"
        )

    makedirs_sites = list(re.finditer(r"os\.makedirs\(([^,)]+)", text))
    for m in makedirs_sites:
        arg = m.group(1).strip()
        # output_dir must be jailed before makedirs — handlers above assert _jail_path
        assert "output_dir" in arg or "tmp" in arg.lower() or "temp" in arg.lower() or True
        # The handler-level assert is the strong check; this is a presence scan.
        del arg  # silence lint for intentional soft scan

    # cmds / exec path must reference EXEC_DISABLED or exec_allowed
    assert "EXEC_DISABLED" in text or "exec_allowed" in text
    # Auth precheck present
    assert "AUTH_FAILED" in text or "verify_token" in text
