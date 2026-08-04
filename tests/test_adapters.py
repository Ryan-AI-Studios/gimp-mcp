"""Offline tests for track 0021 Codex/Grok adapters + dual-delivery config.

No live GIMP. Validates committed examples, .mcp.json server id, and secret scan.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTERS = ROOT / "adapters"
MCP_JSON = ROOT / ".mcp.json"

_HOME_PATH_RE = re.compile(r"C:/Users/", re.IGNORECASE)


def test_adapter_tree_exists() -> None:
    assert (ADAPTERS / "README.md").is_file()
    assert (ADAPTERS / "grok" / "config.toml.example").is_file()
    assert (ADAPTERS / "grok" / "README.md").is_file()
    assert (ADAPTERS / "codex" / "config.toml.example").is_file()
    assert (ADAPTERS / "codex" / "README.md").is_file()


def test_adapter_toml_parse_grok_and_codex() -> None:
    for rel in ("grok/config.toml.example", "codex/config.toml.example"):
        path = ADAPTERS / rel
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        assert "mcp_servers" in data
        assert "gimp" in data["mcp_servers"], f"{rel} missing [mcp_servers.gimp]"
        gimp = data["mcp_servers"]["gimp"]
        assert gimp.get("enabled") is True
        assert gimp.get("command") == "uv"
        assert "args" in gimp
        assert any("gimp_mcp_server.py" in str(a) for a in gimp["args"])


def test_codex_timeouts_explicit() -> None:
    path = ADAPTERS / "codex" / "config.toml.example"
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    gimp = data["mcp_servers"]["gimp"]
    assert gimp.get("startup_timeout_sec", 0) >= 60
    assert gimp.get("tool_timeout_sec", 0) >= 300


def test_adapters_no_secrets_or_home_paths() -> None:
    """Reject real-looking secrets and C:/Users/ home paths in adapter examples."""
    texts: list[tuple[str, str]] = []
    for path in ADAPTERS.rglob("*"):
        if path.is_file() and path.suffix.lower() in {
            ".md",
            ".example",
            ".toml",
            ".json",
            ".txt",
        }:
            texts.append((str(path.relative_to(ROOT)), path.read_text(encoding="utf-8")))
    if MCP_JSON.is_file():
        texts.append((".mcp.json", MCP_JSON.read_text(encoding="utf-8")))

    # Allow documentation mentions of the words token/secret as concepts, but
    # reject assignment-like patterns and sk- / bearer tokens.
    assign_secret = re.compile(
        r"(?i)(api[_-]?key|password|secret|token)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{8,}"
    )
    sk_bearer = re.compile(r"(?i)(sk-[A-Za-z0-9]{10,}|bearer\s+[A-Za-z0-9\-._~+/]+=*)")

    for label, text in texts:
        assert not _HOME_PATH_RE.search(text), f"home path in {label}"
        assert not assign_secret.search(text), f"secret-like assignment in {label}"
        assert not sk_bearer.search(text), f"sk-/bearer token in {label}"
        # Placeholder paths only
        if "C:/" in text or "C:\\" in text:
            assert "C:/path/to" in text or "C:/path/" in text or "path/to" in text, (
                f"non-placeholder Windows path in {label}"
            )


def test_mcp_json_server_name_gimp() -> None:
    assert MCP_JSON.is_file()
    data = json.loads(MCP_JSON.read_text(encoding="utf-8"))
    servers = data.get("mcpServers") or data.get("mcp_servers")
    assert isinstance(servers, dict)
    assert "gimp" in servers
    assert "gimp-mcp" not in servers
    entry = servers["gimp"]
    assert entry.get("command") == "uv"
    args = entry.get("args") or []
    assert "gimp_mcp_server.py" in args or any("gimp_mcp_server.py" in str(a) for a in args)


def test_adapters_readme_documents_migration_and_timeouts() -> None:
    readme = (ADAPTERS / "README.md").read_text(encoding="utf-8")
    assert "gimp-mcp" in readme and "gimp" in readme  # migration note
    assert "startup_timeout_sec" in readme or "60" in readme
    assert (
        "filesystem_path" in readme
        or "dual-delivery" in readme.lower()
        or "dual delivery" in readme.lower()
    )
    assert "WSL" in readme or "wsl" in readme
