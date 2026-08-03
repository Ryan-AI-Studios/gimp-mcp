"""Offline tests for gimp-agent CLI (track 0012) — no live GIMP required."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

import gimp_mcp_security as sec
from gimp_agent import exit_codes as ec
from gimp_agent import jsonio
from gimp_agent import paths as pathmod
from gimp_agent import probe as probe_mod
from gimp_agent.cli import main
from gimp_agent.doctor import run_doctor

# ---------------------------------------------------------------------------
# Exit map
# ---------------------------------------------------------------------------


def test_exit_stale_handle_is_5() -> None:
    assert ec.exit_code_for(sec.CODE_STALE_HANDLE) == 5


def test_exit_none_ok_false_is_1() -> None:
    assert ec.exit_code_for(None, ok=False) == 1


def test_exit_ok_true_is_0() -> None:
    assert ec.exit_code_for(sec.CODE_STALE_HANDLE, ok=True) == 0
    assert ec.exit_code_for(None, ok=True) == 0


def test_exit_policy_is_6() -> None:
    assert ec.exit_code_for(sec.CODE_POLICY_DENIED) == 6
    assert ec.exit_code_for(sec.CODE_CONFIRM_REQUIRED) == 6
    assert ec.exit_code_for(sec.CODE_PATH_DENIED) == 6


def test_exit_partial_is_10() -> None:
    assert ec.exit_code_for(sec.CODE_PARTIAL_MUTATION) == 10


def test_exit_alpha_is_8() -> None:
    assert ec.exit_code_for(sec.CODE_ALPHA_LOST) == 8


def test_exit_timeout_is_9() -> None:
    assert ec.exit_code_for(sec.CODE_TIMEOUT) == 9


def test_exit_unsupported_is_12() -> None:
    assert ec.exit_code_for(sec.CODE_UNSUPPORTED) == 12


def test_exit_cli_usage_is_2() -> None:
    assert ec.exit_code_for(ec.CLI_USAGE) == 2


def test_exit_gimp_plugin_not_found_is_3() -> None:
    assert ec.exit_code_for(ec.GIMP_NOT_FOUND) == 3
    assert ec.exit_code_for(ec.PLUGIN_NOT_FOUND) == 3


def test_exit_transport_is_4() -> None:
    assert ec.exit_code_for(sec.CODE_CONNECTION_FAILED) == 4
    assert ec.exit_code_for(sec.CODE_AUTH_FAILED) == 4
    assert ec.exit_code_for(sec.CODE_BIND_DENIED) == 4


def test_exit_unmapped_code_is_7() -> None:
    assert ec.exit_code_for("SOME_FUTURE_CODE") == 7


def test_reverse_map_exit_5_has_multiple_codes() -> None:
    reverse = ec.exit_to_codes_table()
    codes = reverse[5]
    assert sec.CODE_STALE_HANDLE in codes
    assert sec.CODE_FOREIGN_SESSION in codes
    assert sec.CODE_INVALID_HANDLE in codes
    assert sec.CODE_HANDLE_NOT_FOUND in codes
    assert sec.CODE_SELECTION_CONFLICT in codes
    assert len(codes) >= 5


def test_code_to_exit_table_contains_cli_local() -> None:
    table = ec.code_to_exit_table()
    assert table[ec.CLI_USAGE] == 2
    assert table[ec.GIMP_NOT_FOUND] == 3
    assert table[ec.PLUGIN_NOT_FOUND] == 3


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------


def test_expected_plugin_files_completeness() -> None:
    expected = {
        "gimp-mcp-plugin.py",
        "gimp_mcp_security.py",
        "gimp_mcp_snapshot.py",
        "gimp_mcp_export.py",
        "gimp_mcp_handles.py",
        "gimp_mcp_coords.py",
        "gimp_mcp_policy.py",
        "gimp_mcp_atomic.py",
    }
    assert set(pathmod.EXPECTED_PLUGIN_FILES) == expected
    assert len(pathmod.EXPECTED_PLUGIN_FILES) == 8
    assert "gimp_mcp_state.py" not in pathmod.EXPECTED_PLUGIN_FILES
    assert "gimp_mcp_surface.py" not in pathmod.EXPECTED_PLUGIN_FILES


def test_exit_output_collision_is_11() -> None:
    assert ec.exit_code_for(sec.CODE_OUTPUT_COLLISION) == 11
    reverse = ec.exit_to_codes_table()
    assert sec.CODE_OUTPUT_COLLISION in reverse[11]


def test_exit_verify_failed_is_8() -> None:
    assert ec.exit_code_for(sec.CODE_VERIFY_FAILED) == 8


def test_semver_int_tuple_picks_3_10_over_3_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "GIMP"
    (base / "3.2").mkdir(parents=True)
    (base / "3.10").mkdir(parents=True)
    (base / "not-a-version").mkdir()
    chosen = pathmod.highest_gimp_version_dir(base)
    assert chosen is not None
    assert chosen.name == "3.10"

    plugin = pathmod.find_plugin_dir(base)
    assert plugin is not None
    assert plugin == base / "3.10" / "plug-ins" / "gimp-mcp-plugin"


def test_parse_semver_tuple() -> None:
    assert pathmod.parse_semver_tuple("3.2") == (3, 2)
    assert pathmod.parse_semver_tuple("3.10") == (3, 10)
    assert pathmod.parse_semver_tuple("3.2.4") == (3, 2, 4)
    assert pathmod.parse_semver_tuple("foo") is None
    assert pathmod.parse_semver_tuple("2.10") is None


def test_missing_plugin_files_detects_security(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plug-ins" / "gimp-mcp-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "gimp-mcp-plugin.py").write_text("# stub\n", encoding="utf-8")
    missing = pathmod.missing_plugin_files(plugin_dir)
    assert "gimp_mcp_security.py" in missing
    assert "gimp-mcp-plugin.py" not in missing


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def test_doctor_missing_security_strict_exit_3(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Fake GIMP config with incomplete plugin install
    gimp_base = tmp_path / "GIMP"
    plugin_dir = gimp_base / "3.2" / "plug-ins" / "gimp-mcp-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "gimp-mcp-plugin.py").write_text("# only plugin\n", encoding="utf-8")
    # Leave gimp_mcp_security.py missing

    monkeypatch.setenv("APPDATA", str(tmp_path))
    # Avoid real console discovery affecting required check order — provide fake console
    fake_console = tmp_path / "gimp-console-3.2.exe"
    fake_console.write_bytes(b"")

    def _fake_find_console() -> Path:
        return fake_console

    def _fake_version(console: Path, *, timeout: float = 15.0) -> tuple[str | None, str | None]:
        return "GIMP 3.2.4", None

    monkeypatch.setattr(pathmod, "find_gimp_console", _fake_find_console)
    monkeypatch.setattr(pathmod, "run_console_version", _fake_version)
    monkeypatch.setattr(pathmod, "find_gimp_gui", lambda: None)
    monkeypatch.setattr(pathmod, "gimp_config_base", lambda: gimp_base)
    # find_plugin_dir uses highest_gimp_version_dir(base) — patch find_plugin_dir
    monkeypatch.setattr(pathmod, "find_plugin_dir", lambda base=None: plugin_dir)

    # Token / TCP / workspace — keep quiet
    monkeypatch.delenv(sec.ENV_TOKEN, raising=False)
    monkeypatch.setattr(sec, "read_token_file", lambda path=None: None)
    monkeypatch.setattr(sec, "default_token_path", lambda: tmp_path / "session.token")
    monkeypatch.setattr(sec, "workspace_root", lambda: None)
    monkeypatch.setattr(sec, "get_port", lambda: 9877)
    monkeypatch.setattr(
        "gimp_agent.doctor._tcp_connect_only",
        lambda host, port, timeout=2.0: (False, "refused"),
    )

    report = run_doctor(strict=True)
    assert report.ok is False
    assert report.code == ec.PLUGIN_NOT_FOUND
    assert report.exit_code == 3
    # All checks still present
    names = [c.name for c in report.checks]
    assert "gimp_console" in names
    assert "plugin_files" in names
    assert "tool_pins" in names
    assert report.envelope_data()["batch_interpreter"] is False


def _patch_doctor_incomplete_plugin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Shared fixtures: incomplete plugin install + quiet network/token."""
    gimp_base = tmp_path / "GIMP"
    plugin_dir = gimp_base / "3.2" / "plug-ins" / "gimp-mcp-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "gimp-mcp-plugin.py").write_text("# only plugin\n", encoding="utf-8")

    monkeypatch.setenv("APPDATA", str(tmp_path))
    fake_console = tmp_path / "gimp-console-3.2.exe"
    fake_console.write_bytes(b"")

    monkeypatch.setattr(pathmod, "find_gimp_console", lambda: fake_console)
    monkeypatch.setattr(
        pathmod,
        "run_console_version",
        lambda console, *, timeout=15.0: ("GIMP 3.2.4", None),
    )
    monkeypatch.setattr(pathmod, "find_gimp_gui", lambda: None)
    monkeypatch.setattr(pathmod, "gimp_config_base", lambda: gimp_base)
    monkeypatch.setattr(pathmod, "find_plugin_dir", lambda base=None: plugin_dir)

    monkeypatch.delenv(sec.ENV_TOKEN, raising=False)
    monkeypatch.setattr(sec, "read_token_file", lambda path=None: None)
    monkeypatch.setattr(sec, "default_token_path", lambda: tmp_path / "session.token")
    monkeypatch.setattr(sec, "workspace_root", lambda: None)
    monkeypatch.setattr(sec, "get_port", lambda: 9877)
    monkeypatch.setattr(
        "gimp_agent.doctor._tcp_connect_only",
        lambda host, port, timeout=2.0: (False, "refused"),
    )
    return plugin_dir


def test_doctor_nonstrict_required_fail_exit_0(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-strict doctor: required failure → process/envelope exit 0, ok=False."""
    _patch_doctor_incomplete_plugin(tmp_path, monkeypatch)

    report = run_doctor(strict=False)
    assert report.ok is False
    assert report.code == ec.PLUGIN_NOT_FOUND
    assert report.exit_code == 0
    assert report.exit_code == ec.EXIT_SUCCESS

    # CLI main returns the same process exit (0) with ok=false envelope
    code = main(["doctor", "--json"])
    assert code == 0


def test_doctor_nonstrict_cli_envelope_ok_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """JSON envelope from non-strict doctor reports ok=false with exit_code 0."""
    _patch_doctor_incomplete_plugin(tmp_path, monkeypatch)

    code = main(["doctor", "--json"])
    assert code == 0
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is False
    assert body["exit_code"] == 0
    assert body["code"] == ec.PLUGIN_NOT_FOUND
    assert isinstance(body["data"].get("checks"), list)


# ---------------------------------------------------------------------------
# probe (mocked socket)
# ---------------------------------------------------------------------------


def test_probe_auth_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe_mod, "load_probe_token", lambda: "bad-token")
    monkeypatch.setattr(probe_mod, "_resolve_host", lambda: "127.0.0.1")
    monkeypatch.setattr(sec, "get_port", lambda: 9877)

    def _fake_send(**kwargs: Any) -> dict[str, Any]:
        return sec.make_error(sec.CODE_AUTH_FAILED, "Authentication failed")

    monkeypatch.setattr(probe_mod, "send_get_gimp_info", _fake_send)
    report = probe_mod.run_probe(timeout=0.5)
    assert report.ok is False
    assert report.code == sec.CODE_AUTH_FAILED
    assert report.exit_code == 4


def test_probe_connection_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe_mod, "load_probe_token", lambda: "tok")
    monkeypatch.setattr(probe_mod, "_resolve_host", lambda: "127.0.0.1")
    monkeypatch.setattr(sec, "get_port", lambda: 9877)

    def _raise(**kwargs: Any) -> dict[str, Any]:
        raise ConnectionRefusedError("Connection refused")

    monkeypatch.setattr(probe_mod, "send_get_gimp_info", _raise)
    report = probe_mod.run_probe(timeout=0.5)
    assert report.ok is False
    assert report.code == sec.CODE_CONNECTION_FAILED
    assert report.exit_code == 4


def test_probe_timeout_is_9(monkeypatch: pytest.MonkeyPatch) -> None:
    """Socket/read TimeoutError → TIMEOUT code → exit 9 (not transport 4)."""
    monkeypatch.setattr(probe_mod, "load_probe_token", lambda: "tok")
    monkeypatch.setattr(probe_mod, "_resolve_host", lambda: "127.0.0.1")
    monkeypatch.setattr(sec, "get_port", lambda: 9877)

    def _timeout(**kwargs: Any) -> dict[str, Any]:
        raise TimeoutError("timed out")

    monkeypatch.setattr(probe_mod, "send_get_gimp_info", _timeout)
    report = probe_mod.run_probe(timeout=0.5)
    assert report.ok is False
    assert report.code == sec.CODE_TIMEOUT
    assert report.exit_code == ec.exit_code_for(sec.CODE_TIMEOUT)
    assert report.exit_code == 9
    assert report.exit_code != 4


def test_probe_success_extracts_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe_mod, "load_probe_token", lambda: "tok")
    monkeypatch.setattr(probe_mod, "_resolve_host", lambda: "127.0.0.1")
    monkeypatch.setattr(sec, "get_port", lambda: 9877)

    def _ok(**kwargs: Any) -> dict[str, Any]:
        return {
            "status": "success",
            "results": {"version": {"detected_version": "3.2.4", "major_version": 3}},
        }

    monkeypatch.setattr(probe_mod, "send_get_gimp_info", _ok)
    report = probe_mod.run_probe()
    assert report.ok is True
    assert report.exit_code == 0
    assert report.data.get("gimp_version") == "3.2.4"


def test_send_get_gimp_info_framing() -> None:
    """Local client sends newline JSON with auth always."""
    sent: list[bytes] = []

    class FakeSock:
        def __init__(self) -> None:
            self._sent = False

        def settimeout(self, t: float) -> None:
            pass

        def sendall(self, data: bytes) -> None:
            sent.append(data)

        def recv(self, n: int) -> bytes:
            if not self._sent:
                self._sent = True
                return json.dumps({"status": "success", "results": {}}).encode("utf-8")
            return b""

        def __enter__(self) -> FakeSock:
            return self

        def __exit__(self, *args: object) -> None:
            pass

    with patch("gimp_agent.probe.socket.create_connection", return_value=FakeSock()):
        result = probe_mod.send_get_gimp_info(
            host="127.0.0.1",
            port=9877,
            token="secret",
            timeout=1.0,
        )
    assert result["status"] == "success"
    assert len(sent) == 1
    payload = json.loads(sent[0].decode("utf-8").strip())
    assert payload["type"] == "get_gimp_info"
    assert payload["params"] == {}
    assert payload["auth"] == "secret"
    assert sent[0].endswith(b"\n")


# ---------------------------------------------------------------------------
# JSON envelope
# ---------------------------------------------------------------------------


def test_json_envelope_shape() -> None:
    env = jsonio.make_envelope(
        ok=True,
        exit_code=0,
        code=None,
        message="doctor ok",
        data={"batch_interpreter": False},
    )
    assert set(env.keys()) == {"ok", "exit_code", "code", "message", "data"}
    assert env["ok"] is True
    assert env["exit_code"] == 0
    assert env["code"] is None
    assert env["data"]["batch_interpreter"] is False


def test_json_mode_flag_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(jsonio.ENV_JSON, "0")
    assert jsonio.json_mode_enabled(flag=True) is True
    monkeypatch.setenv(jsonio.ENV_JSON, "true")
    assert jsonio.json_mode_enabled(flag=None) is True
    monkeypatch.delenv(jsonio.ENV_JSON, raising=False)
    assert jsonio.json_mode_enabled(flag=None) is False


# ---------------------------------------------------------------------------
# CLI integration (in-process)
# ---------------------------------------------------------------------------


def test_cli_codes_json(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["codes", "--json"])
    assert code == 0
    out = capsys.readouterr().out
    body = json.loads(out)
    assert body["ok"] is True
    assert body["exit_code"] == 0
    assert "code_to_exit" in body["data"]
    assert body["data"]["code_to_exit"][sec.CODE_STALE_HANDLE] == 5
    assert "exit_to_codes" in body["data"]
    # JSON object keys are strings
    assert sec.CODE_STALE_HANDLE in body["data"]["exit_to_codes"]["5"]


def test_cli_global_json_before_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    """P1-001: ``gimp-agent --json codes`` must emit parseable JSON (not human)."""
    code = main(["--json", "codes"])
    assert code == 0
    out = capsys.readouterr().out
    body = json.loads(out)
    assert body["ok"] is True
    assert body["exit_code"] == 0
    assert "code_to_exit" in body["data"]


# ---------------------------------------------------------------------------
# save-xcf / export (mocked TCP — track 0013)
# ---------------------------------------------------------------------------


def test_cli_save_xcf_happy_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _ok(cmd: str, params: dict[str, Any] | None = None, *, timeout: float = 30.0) -> dict:
        assert cmd == "save_xcf"
        assert params is not None
        assert params["collision"] == "fail"
        assert params["verify_reopen"] is True
        return {
            "status": "success",
            "results": {
                "file_path": params["file_path"],
                "bytes": 12,
                "sha256": "a" * 64,
                "collision": "fail",
                "collision_resolved": False,
                "backup_path": None,
                "atomic": True,
                "reopen_verified": True,
            },
        }

    monkeypatch.setattr(probe_mod, "send_authenticated_command", _ok)
    code = main(["save-xcf", r"C:\ws\out.xcf", "--json"])
    assert code == 0
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is True
    assert body["data"]["sha256"] == "a" * 64
    assert body["data"]["atomic"] is True


def test_cli_save_xcf_collision_exit_11(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _collide(cmd: str, params: dict[str, Any] | None = None, *, timeout: float = 30.0) -> dict:
        return sec.make_error(sec.CODE_OUTPUT_COLLISION, "output path already exists")

    monkeypatch.setattr(probe_mod, "send_authenticated_command", _collide)
    code = main(["save-xcf", r"C:\ws\out.xcf", "--json"])
    assert code == 11
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is False
    assert body["code"] == sec.CODE_OUTPUT_COLLISION
    assert body["exit_code"] == 11


def test_cli_export_connection_fail_exit_4(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _raise(cmd: str, params: dict[str, Any] | None = None, *, timeout: float = 30.0) -> dict:
        raise ConnectionRefusedError("Connection refused")

    monkeypatch.setattr(probe_mod, "send_authenticated_command", _raise)
    code = main(["export", r"C:\ws\out.png", "--format", "png", "--json"])
    assert code == 4
    body = json.loads(capsys.readouterr().out)
    assert body["code"] == sec.CODE_CONNECTION_FAILED
    assert body["exit_code"] == 4


def test_cli_export_happy_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _ok(cmd: str, params: dict[str, Any] | None = None, *, timeout: float = 30.0) -> dict:
        assert cmd == "export_image"
        assert params is not None
        assert params["format"] == "png"
        assert params["collision"] == "replace"
        return {
            "status": "success",
            "results": {
                "file_path": params["file_path"],
                "format": "png",
                "file_size_bytes": 99,
                "sha256": "b" * 64,
                "atomic": True,
                "collision": "replace",
            },
        }

    monkeypatch.setattr(probe_mod, "send_authenticated_command", _ok)
    code = main(
        [
            "export",
            r"C:\ws\out.png",
            "--format",
            "png",
            "--collision",
            "replace",
            "--json",
        ]
    )
    assert code == 0
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is True
    assert body["data"]["atomic"] is True


def test_cli_invalid_collision_exit_2() -> None:
    code = main(["save-xcf", r"C:\ws\out.xcf", "--collision", "overwrite"])
    assert code == 2


def test_probe_empty_or_unexpected_status_not_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-002: ``{}`` or unexpected status must yield ok=False (not false-success)."""
    monkeypatch.setattr(probe_mod, "load_probe_token", lambda: "tok")
    monkeypatch.setattr(probe_mod, "_resolve_host", lambda: "127.0.0.1")
    monkeypatch.setattr(sec, "get_port", lambda: 9877)

    monkeypatch.setattr(probe_mod, "send_get_gimp_info", lambda **_k: {})
    report = probe_mod.run_probe(timeout=0.5)
    assert report.ok is False
    assert report.exit_code != 0
    assert report.code == sec.CODE_INTERNAL
    assert report.data.get("probe") != "ok"

    monkeypatch.setattr(
        probe_mod,
        "send_get_gimp_info",
        lambda **_k: {"status": "unexpected"},
    )
    report2 = probe_mod.run_probe(timeout=0.5)
    assert report2.ok is False
    assert report2.exit_code != 0
    assert report2.code == sec.CODE_INTERNAL
    assert report2.data.get("probe") != "ok"


def test_run_console_version_nonzero_rc_is_error() -> None:
    """P1-003: non-zero --version returncode must not be treated as success."""
    from subprocess import CompletedProcess

    fake = CompletedProcess(
        args=["gimp-console", "--version"],
        returncode=1,
        stdout="err",
        stderr="",
    )
    with patch("gimp_agent.paths.subprocess.run", return_value=fake):
        out, err = pathmod.run_console_version(Path("C:/fake/gimp-console.exe"))
    assert out is None
    assert err is not None
    assert "exit 1" in err


def test_cli_probe_invalid_timeout_exit_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """P2-001: timeout <= 0 → exit 2 CLI_USAGE envelope (not raw ValueError)."""
    code = main(["probe", "--timeout", "-1", "--json"])
    assert code == 2
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is False
    assert body["exit_code"] == 2
    assert body["code"] == ec.CLI_USAGE


def test_cli_env_json_without_flag(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """P2-004: GIMP_AGENT_JSON=1 enables JSON when no --json flag is present."""
    monkeypatch.setenv("GIMP_AGENT_JSON", "1")
    code = main(["codes"])
    assert code == 0
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is True
    assert "code_to_exit" in body["data"]


def test_cli_probe_nan_timeout_json_compliant(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """P2-005: NaN/Infinity timeout must emit standards-compliant JSON (no bare NaN)."""
    code = main(["probe", "--timeout", "nan", "--json"])
    assert code == 2
    raw = capsys.readouterr().out
    # Strict JSON parsers reject Python's non-standard NaN token
    body = json.loads(raw)
    assert body["ok"] is False
    assert body["code"] == ec.CLI_USAGE
    assert isinstance(body["data"].get("timeout"), str)


def test_cli_help_exits_0() -> None:
    code = main(["--help"])
    assert code == 0


def test_cli_usage_error_exits_2() -> None:
    # missing required subcommand
    code = main([])
    assert code == 2


# ---------------------------------------------------------------------------
# Optional subprocess smoke
# ---------------------------------------------------------------------------


def test_subprocess_gimp_agent_codes_json() -> None:
    import subprocess
    import sys

    completed = subprocess.run(
        [sys.executable, "-m", "gimp_agent", "codes", "--json"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert completed.returncode == 0, completed.stderr
    body = json.loads(completed.stdout)
    assert body["ok"] is True
    assert "code_to_exit" in body["data"]
