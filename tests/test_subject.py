"""Offline tests for host subject isolation (track 0032) — no rembg required."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import gimp_mcp_security as sec
import gimp_mcp_subject as subject
from gimp_agent import exit_codes as ec
from gimp_agent.cli import main

# Minimal valid-looking PNG signature + junk (rembg mocked; we only write bytes).
_FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture(autouse=True)
def _clear_sessions() -> Any:
    subject.clear_session_cache()
    yield
    subject.clear_session_cache()


def test_rembg_available_false_when_import_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def _blocked(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "rembg" or name.startswith("rembg."):
            raise ImportError("rembg not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    assert subject.rembg_available() is False


def test_isolate_without_rembg_raises_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(sec.ENV_WORKSPACE, str(tmp_path))
    src = tmp_path / "in.png"
    src.write_bytes(_FAKE_PNG)
    out = tmp_path / "out.png"
    monkeypatch.setattr(subject, "rembg_available", lambda: False)
    with pytest.raises(sec.GimpMcpError) as ei:
        subject.isolate_subject(str(src), str(out))
    assert ei.value.code == sec.CODE_UNSUPPORTED
    assert "uv sync --extra subject" in ei.value.message


def test_isolate_path_jail_denies_outside(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(sec.ENV_WORKSPACE, str(tmp_path))
    outside = tmp_path.parent / "escape.png"
    monkeypatch.setattr(subject, "rembg_available", lambda: True)
    with pytest.raises(sec.SecurityError) as ei:
        subject.isolate_subject(str(outside), str(tmp_path / "out.png"))
    assert ei.value.code == sec.CODE_PATH_DENIED


def test_isolate_mocked_rembg_writes_png(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(sec.ENV_WORKSPACE, str(tmp_path))
    src = tmp_path / "in.jpg"
    src.write_bytes(b"fake-jpeg-bytes")
    out = tmp_path / "out.png"

    mock_session = object()

    def _fake_remove(
        data: bytes,
        session: Any = None,
        alpha_matting: bool = False,
        force_return_bytes: bool = False,
        **_kw: Any,
    ) -> bytes:
        assert data == b"fake-jpeg-bytes"
        assert session is mock_session
        assert alpha_matting is False
        assert force_return_bytes is True
        return _FAKE_PNG

    monkeypatch.setattr(subject, "rembg_available", lambda: True)
    with patch.dict("sys.modules", {"rembg": MagicMock()}):
        with patch("rembg.remove", _fake_remove):
            # Bypass session factory; pass session directly
            result = subject.isolate_subject(
                str(src), str(out), session=mock_session, model="u2net"
            )

    assert out.is_file()
    assert out.read_bytes() == _FAKE_PNG
    assert result["format"] == "png"
    assert result["bytes_written"] == len(_FAKE_PNG)
    assert result["model"] == "u2net"
    assert result["alpha_matting"] is False


def test_isolate_download_exception_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(sec.ENV_WORKSPACE, str(tmp_path))
    src = tmp_path / "in.png"
    src.write_bytes(_FAKE_PNG)
    out = tmp_path / "out.png"

    def _boom(*_a: Any, **_k: Any) -> bytes:
        raise RuntimeError("pooch download failed: network unreachable")

    monkeypatch.setattr(subject, "rembg_available", lambda: True)
    with patch.dict("sys.modules", {"rembg": MagicMock()}):
        with patch("rembg.remove", _boom):
            with pytest.raises(sec.GimpMcpError) as ei:
                subject.isolate_subject(str(src), str(out), session=object())
    assert ei.value.code == sec.CODE_UNSUPPORTED
    assert "U2NET_HOME" in ei.value.message


def test_session_cache_reuses_same_session(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    fake = object()

    def _new_session(model: str) -> Any:
        calls.append(model)
        return fake

    mock_rembg = MagicMock()
    mock_rembg.new_session = _new_session
    monkeypatch.setattr(subject, "rembg_available", lambda: True)
    with patch.dict("sys.modules", {"rembg": mock_rembg}):
        # Force re-import path inside _get_session via real import of new_session
        with patch("rembg.new_session", _new_session):
            s1 = subject._get_session("u2net")
            s2 = subject._get_session("u2net")
            s3 = subject._get_session("isnet-anime")
    assert s1 is s2 is fake
    assert s3 is fake
    assert calls == ["u2net", "isnet-anime"]


def test_cli_subject_isolate_help_mentions_verb() -> None:
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(["--help"])
    assert code == 0
    text = buf.getvalue()
    assert "subject-isolate" in text


def test_cli_subject_isolate_flags_present() -> None:
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(["subject-isolate", "--help"])
    assert code == 0
    text = buf.getvalue()
    assert "--input" in text
    assert "--output" in text
    assert "--model" in text
    assert "--alpha-matting" in text


def test_cli_subject_isolate_missing_rembg_exit_12(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(sec.ENV_WORKSPACE, str(tmp_path))
    src = tmp_path / "in.png"
    src.write_bytes(_FAKE_PNG)
    out = tmp_path / "out.png"
    monkeypatch.setattr(subject, "rembg_available", lambda: False)
    # Isolate is imported inside CLI command; patch module attribute
    monkeypatch.setattr(
        "gimp_mcp_subject.rembg_available",
        lambda: False,
    )
    code = main(
        [
            "subject-isolate",
            "--input",
            str(src),
            "--output",
            str(out),
            "--json",
        ]
    )
    assert code == ec.EXIT_UNSUPPORTED
    assert code == 12


def test_host_only_module_includes_subject() -> None:
    from gimp_agent import install as install_mod
    from gimp_agent import paths as pathmod

    assert "gimp_mcp_subject.py" in install_mod.HOST_ONLY_MODULE_NAMES
    assert "gimp_mcp_subject.py" not in pathmod.EXPECTED_PLUGIN_FILES
    assert len(pathmod.EXPECTED_PLUGIN_FILES) == 10


def test_pyproject_subject_extra_and_triad() -> None:
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'subject = ["rembg[cpu]>=2.0.77"]' in text or "rembg[cpu]>=2.0.77" in text
    assert "gimp_mcp_subject" in text
    # Default deps must not require rembg
    # crude: rembg only under optional-dependencies section
    deps_idx = text.index("dependencies = [")
    opt_idx = text.index("[project.optional-dependencies]")
    default_block = text[deps_idx:opt_idx]
    assert "rembg" not in default_block


def test_subject_in_host_ops() -> None:
    import gimp_mcp_recipes as recipes

    assert "subject_isolate" in recipes.HOST_OPS


@pytest.mark.slow
def test_live_rembg_optional_skip_if_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Optional live rembg path — skip when extra not installed (never fail default CI)."""
    if not subject.rembg_available():
        pytest.skip("rembg not installed (optional extra subject)")
    monkeypatch.setenv(sec.ENV_WORKSPACE, str(tmp_path))
    # Tiny synthetic RGB PNG via stdlib builder if available
    try:
        from tests._png_builder import build_minimal_png

        png = build_minimal_png(width=8, height=8, color_type=2)
    except Exception:
        png = _FAKE_PNG
    src = tmp_path / "live_in.png"
    src.write_bytes(png)
    out = tmp_path / "live_out.png"
    result = subject.isolate_subject(str(src), str(out), model="u2netp")
    assert out.is_file()
    assert result["bytes_written"] > 0
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
