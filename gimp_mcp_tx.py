"""Pure stdlib agent undo-group transaction helpers (track 0017).

Shipped next to ``gimp-mcp-plugin.py`` as the 10th plug-in install file
and importable by the host MCP server for shared constants / pure models.

No third-party imports; no GIMP/gi/security dependency.
"""

from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT_S = 300.0
MAX_DEPTH = 8
MAX_LABEL_LEN = 128
ENV_TIMEOUT = "GIMP_MCP_UNDO_TX_TIMEOUT_S"
RECENT_CLOSED_CAP = 10

DEFAULT_LABEL = "agent"
TXN_PREFIX = "txn_"

TxStatus = Literal["open", "committed", "rolled_back", "force_closed"]

# Product codes as strings so this module stays free of security import.
CODE_TX_MISMATCH = "TX_MISMATCH"
CODE_TX_NOT_FOUND = "TX_NOT_FOUND"
CODE_TX_DEPTH = "TX_DEPTH"
CODE_POLICY_DENIED = "POLICY_DENIED"


# ---------------------------------------------------------------------------
# mint / parse / validate
# ---------------------------------------------------------------------------


def mint_transaction_id() -> str:
    """Mint ``txn_`` + uuid4.hex (same entropy class as ``req_``)."""
    return TXN_PREFIX + uuid.uuid4().hex


def parse_timeout_s(raw: Any) -> float:
    """Parse timeout seconds; clamp to 5..3600. Invalid/None → DEFAULT_TIMEOUT_S."""
    if raw is None:
        return DEFAULT_TIMEOUT_S
    if isinstance(raw, str) and not raw.strip():
        return DEFAULT_TIMEOUT_S
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_S
    if value != value:  # NaN
        return DEFAULT_TIMEOUT_S
    if value < 5.0:
        return 5.0
    if value > 3600.0:
        return 3600.0
    return value


def validate_label(label: str | None) -> str:
    """Normalize TX label: None/empty/whitespace → ``agent``; strip; max 128.

    Raises:
        ValueError: when stripped length exceeds MAX_LABEL_LEN (plugin → POLICY_DENIED).
    """
    if label is None:
        return DEFAULT_LABEL
    text = str(label).strip()
    if not text:
        return DEFAULT_LABEL
    if len(text) > MAX_LABEL_LEN:
        raise ValueError(f"label exceeds max length {MAX_LABEL_LEN}")
    return text


# ---------------------------------------------------------------------------
# TxRecord + TxStack (pure model for unit tests + plugin session state)
# ---------------------------------------------------------------------------


@dataclass
class TxRecord:
    """One agent-owned undo group transaction on an image."""

    transaction_id: str
    label: str
    image_id: int
    opened_mono: float
    depth: int
    status: TxStatus = "open"
    opened_at: float = 0.0  # wall-clock for API / recent summaries
    closed_at: float | None = None


@dataclass
class TxStack:
    """Per-image pure stack. Index 0 = outermost; last = top (deepest)."""

    _stack: list[TxRecord] = field(default_factory=list)

    def push(self, record: TxRecord) -> None:
        """Push an open record. Caller must enforce MAX_DEPTH before GIMP start."""
        self._stack.append(record)

    def pop(self) -> TxRecord | None:
        if not self._stack:
            return None
        return self._stack.pop()

    def top(self) -> TxRecord | None:
        if not self._stack:
            return None
        return self._stack[-1]

    @property
    def depth(self) -> int:
        return len(self._stack)

    def open_list(self) -> list[TxRecord]:
        """Open stack order deepest-last (index 0 = outermost)."""
        return list(self._stack)

    def would_exceed_depth(self, max_depth: int = MAX_DEPTH) -> bool:
        return self.depth >= max_depth

    def find_index(self, transaction_id: str) -> int | None:
        for i, rec in enumerate(self._stack):
            if rec.transaction_id == transaction_id:
                return i
        return None

    def reap_expired(self, now_mono: float, timeout_s: float) -> list[TxRecord]:
        """Force-close expired open TXs deepest-first (wall-clock from opened_mono).

        If any record is expired, close from the top down through the outermost
        expired record (GIMP nesting requires ending deeper groups first).
        Non-expired records above an expired outer are also closed.
        """
        if not self._stack:
            return []
        outer_exp: int | None = None
        for i, rec in enumerate(self._stack):
            if now_mono - rec.opened_mono >= timeout_s:
                outer_exp = i
                break
        if outer_exp is None:
            return []
        closed: list[TxRecord] = []
        while len(self._stack) > outer_exp:
            rec = self._stack.pop()
            rec.status = "force_closed"
            closed.append(rec)
        return closed

    def force_close_from(self, transaction_id: str) -> list[TxRecord] | None:
        """Force-close ``transaction_id`` and all above it (deeper). Bottom remains.

        Returns closed records deepest-first, or None if id not found.
        """
        idx = self.find_index(transaction_id)
        if idx is None:
            return None
        closed: list[TxRecord] = []
        while len(self._stack) > idx:
            rec = self._stack.pop()
            rec.status = "force_closed"
            closed.append(rec)
        return closed

    def force_close_all(self) -> list[TxRecord]:
        """Force-close every open TX deepest-first."""
        closed: list[TxRecord] = []
        while self._stack:
            rec = self._stack.pop()
            rec.status = "force_closed"
            closed.append(rec)
        return closed

    def end_top(self, transaction_id: str | None = None) -> tuple[str, TxRecord | None]:
        """End semantics for commit/rollback of top only.

        Returns (status_code, record):
        - ("ok", record) when top matches (or no id required)
        - ("mismatch", None) when empty or id ≠ top
        """
        top = self.top()
        if top is None:
            return CODE_TX_MISMATCH, None
        if transaction_id is not None and transaction_id != top.transaction_id:
            return CODE_TX_MISMATCH, None
        return "ok", top


class RecentClosed:
    """Per-image ring of closed TX summaries (cap RECENT_CLOSED_CAP)."""

    def __init__(self, maxlen: int = RECENT_CLOSED_CAP) -> None:
        self._dq: deque[TxRecord] = deque(maxlen=maxlen)

    def push(self, record: TxRecord) -> None:
        self._dq.append(record)

    def list(self) -> list[TxRecord]:
        return list(self._dq)

    def clear(self) -> None:
        self._dq.clear()


# ---------------------------------------------------------------------------
# Serialization helpers (status / recent shapes)
# ---------------------------------------------------------------------------


def serialize_open_record(
    rec: TxRecord,
    *,
    now_mono: float,
    timeout_s: float,
) -> dict[str, Any]:
    age = max(0.0, float(now_mono) - float(rec.opened_mono))
    return {
        "transaction_id": rec.transaction_id,
        "label": rec.label,
        "depth": rec.depth,
        "opened_at": rec.opened_at if rec.opened_at else rec.opened_mono,
        "age_s": age,
        "timeout_s": timeout_s,
        "status": rec.status,
    }


def serialize_recent_record(rec: TxRecord) -> dict[str, Any]:
    return {
        "transaction_id": rec.transaction_id,
        "label": rec.label,
        "status": rec.status,
        "closed_at": rec.closed_at if rec.closed_at is not None else 0.0,
    }
