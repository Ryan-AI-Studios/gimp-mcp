"""Map product CODE_* / CLI-local codes to process exit codes 0-12."""

from __future__ import annotations

import gimp_mcp_security as sec

# CLI-local codes (not raised by the TCP plugin / MCP tools)
CLI_USAGE = "CLI_USAGE"
GIMP_NOT_FOUND = "GIMP_NOT_FOUND"
PLUGIN_NOT_FOUND = "PLUGIN_NOT_FOUND"

# Exit integer meanings (CGPT / track 0012 locked table)
EXIT_SUCCESS = 0
EXIT_GENERIC = 1
EXIT_CLI_USAGE = 2
EXIT_GIMP_OR_PLUGIN = 3
EXIT_TRANSPORT_AUTH = 4
EXIT_HANDLE = 5
EXIT_POLICY = 6
EXIT_INTERNAL = 7
EXIT_VERIFICATION = 8
EXIT_TIMEOUT = 9
EXIT_PARTIAL = 10
EXIT_COLLISION = 11  # OUTPUT_COLLISION (track 0013)
EXIT_UNSUPPORTED = 12

# Explicit code → exit map (unmapped CODE_* fall through to EXIT_INTERNAL)
_CODE_TO_EXIT: dict[str, int] = {
    # CLI-local
    CLI_USAGE: EXIT_CLI_USAGE,
    GIMP_NOT_FOUND: EXIT_GIMP_OR_PLUGIN,
    PLUGIN_NOT_FOUND: EXIT_GIMP_OR_PLUGIN,
    # Transport / auth
    sec.CODE_CONNECTION_FAILED: EXIT_TRANSPORT_AUTH,
    sec.CODE_AUTH_FAILED: EXIT_TRANSPORT_AUTH,
    sec.CODE_BIND_DENIED: EXIT_TRANSPORT_AUTH,
    # Handles
    sec.CODE_STALE_HANDLE: EXIT_HANDLE,
    sec.CODE_FOREIGN_SESSION: EXIT_HANDLE,
    sec.CODE_INVALID_HANDLE: EXIT_HANDLE,
    sec.CODE_HANDLE_NOT_FOUND: EXIT_HANDLE,
    sec.CODE_SELECTION_CONFLICT: EXIT_HANDLE,
    # Policy / paths / checkpoints
    sec.CODE_POLICY_DENIED: EXIT_POLICY,
    sec.CODE_CONFIRM_REQUIRED: EXIT_POLICY,
    sec.CODE_PATH_DENIED: EXIT_POLICY,
    sec.CODE_EXEC_DISABLED: EXIT_POLICY,
    sec.CODE_CHECKPOINT_EXISTS: EXIT_POLICY,
    sec.CODE_CHECKPOINT_NOT_FOUND: EXIT_POLICY,
    sec.CODE_CHECKPOINT_CORRUPTED: EXIT_POLICY,
    # Undo group transactions (0017) — policy-ish stack ownership
    sec.CODE_TX_MISMATCH: EXIT_POLICY,
    sec.CODE_TX_NOT_FOUND: EXIT_POLICY,
    sec.CODE_TX_DEPTH: EXIT_POLICY,
    # Internal / metadata
    sec.CODE_INTERNAL: EXIT_INTERNAL,
    sec.CODE_METADATA_WRITE_FAILED: EXIT_INTERNAL,
    # Verification
    sec.CODE_ALPHA_LOST: EXIT_VERIFICATION,
    sec.CODE_VERIFY_FAILED: EXIT_VERIFICATION,
    # Collision (0013)
    sec.CODE_OUTPUT_COLLISION: EXIT_COLLISION,
    # Timeout / partial / unsupported
    sec.CODE_TIMEOUT: EXIT_TIMEOUT,
    sec.CODE_PARTIAL_MUTATION: EXIT_PARTIAL,
    sec.CODE_UNSUPPORTED: EXIT_UNSUPPORTED,
}


def exit_code_for(code: str | None, *, ok: bool = False) -> int:
    """Return process exit integer for a product/CLI code.

    - ``ok=True`` → 0 (success), regardless of code
    - ``code is None`` and not ok → 1 (generic failure)
    - known code → mapped exit
    - unknown / unmapped CODE_* → 7 (internal)
    """
    if ok:
        return EXIT_SUCCESS
    if code is None:
        return EXIT_GENERIC
    mapped = _CODE_TO_EXIT.get(code)
    if mapped is not None:
        return mapped
    return EXIT_INTERNAL


def code_to_exit_table() -> dict[str, int]:
    """Return a copy of the code → exit mapping (sorted keys for stable JSON)."""
    return dict(sorted(_CODE_TO_EXIT.items(), key=lambda kv: (kv[1], kv[0])))


def exit_to_codes_table() -> dict[int, list[str]]:
    """Reverse map: exit int → sorted list of codes that map to it.

    Exit 0 and 1 have no product CODE_* entries by design
    (0 = success, 1 = generic/null code). Exit 11 maps to OUTPUT_COLLISION.
    """
    reverse: dict[int, list[str]] = {}
    for code, exit_n in _CODE_TO_EXIT.items():
        reverse.setdefault(exit_n, []).append(code)
    for codes in reverse.values():
        codes.sort()
    # Document special exits with empty lists when no product codes map there
    for exit_n in (
        EXIT_SUCCESS,
        EXIT_GENERIC,
    ):
        reverse.setdefault(exit_n, [])
    return dict(sorted(reverse.items(), key=lambda kv: kv[0]))
