"""Integrity tests for eval catalog: nodeid collect-only match + H3 non-status rule."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.evals.loader import load_cases, offline_structure_cases

EVALS_DIR = Path(__file__).resolve().parent

# Allowlisted non-status signal names for H3 (AST / source scan).
_H3_ALLOWLIST = frozenset(
    {
        "mae",
        "changed_pixels",
        "has_alpha",
        "width",
        "height",
        "code",
        "pass",
        "failures",
        "ok",
        "POLICY_DENIED",
        "STALE_HANDLE",
        "require_mutation",
        "max_edge",
        "hard_max",
        "PROCEDURE_NAME",
        "validate_job",
        "read_text",
        "re",
        "search",
        "steps",
        "id",
        "ids",
        "v",
        "name",
        "op",
        "transparent-png",
        "web-export",
        "batch-interpreter",
        "plug-in-gimp-mcp-batch",
        "len",
        "recipe",
        "recipes",
        "CODE_POLICY_DENIED",
        "CODE_PATH_DENIED",
        "PATH_DENIED",
        "check_path_under_root",
        "resolve_under_root",
        "load_package_recipes",
        "build_console_argv",
        "SecurityError",
    }
)

# Eval test modules that may only re-map (none currently); thin wrappers must assert.
_EVAL_TEST_FILES = (
    "test_eval_cases.py",
    "test_eval_integrity.py",
    "test_eval_docs.py",
    "test_loader.py",
    "test_scorer.py",
)


def test_offline_structure_nodeids_exist_in_collection() -> None:
    """AI1 BS1: every offline/structure nodeid is collectable by pytest."""
    cases = offline_structure_cases(load_cases())
    wanted: set[str] = set()
    for case in cases:
        wanted.update(case.test_nodeids)
    assert wanted, "expected offline/structure nodeids"

    class _Collector:
        def __init__(self) -> None:
            self.nodeids: set[str] = set()

        def pytest_collection_finish(self, session: pytest.Session) -> None:
            for item in session.items:
                self.nodeids.add(item.nodeid)

    # Collect the whole tests/ tree (includes tests/evals) without running.
    collector = _Collector()
    # Restrict collection roots to unique parent modules of wanted nodeids + evals.
    roots = sorted({nid.split("::", 1)[0] for nid in wanted})
    code = pytest.main(
        ["--collect-only", "-q", "--disable-warnings", *roots],
        plugins=[collector],
    )
    assert code == 0, f"pytest collect-only failed with code {code}"
    missing = sorted(wanted - collector.nodeids)
    assert not missing, "catalog nodeids not found in collection:\n" + "\n".join(missing)


def test_no_shared_nodeids_verify_vs_snapshot_budget() -> None:
    """M7: E-VERIFY-BUDGET and E-SNAPSHOT-BUDGET must not share nodeids."""
    by_id = {c.id: c for c in load_cases()}
    v = set(by_id["E-VERIFY-BUDGET"].test_nodeids)
    s = set(by_id["E-SNAPSHOT-BUDGET"].test_nodeids)
    assert v.isdisjoint(s), f"shared nodeids: {v & s}"


def test_batch_small_is_structure_only() -> None:
    """H1: E-BATCH-SMALL must be structure mode (not offline host batch)."""
    case = next(c for c in load_cases() if c.id == "E-BATCH-SMALL")
    assert case.mode == "structure"
    assert case.test_nodeids
    assert all("test_eval_cases" in n or "test_batch" in n for n in case.test_nodeids)


def _function_assert_sources(tree: ast.AST) -> list[tuple[str, str]]:
    """Return (func_name, joined_assert_source_approx) for each test function."""
    out: list[tuple[str, str]] = []
    for node in tree.body if isinstance(tree, ast.Module) else []:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        chunks: list[str] = []
        for child in ast.walk(node):
            if isinstance(child, ast.Assert):
                chunks.append(ast.dump(child))
        out.append((node.name, "\n".join(chunks)))
    return out


def test_h3_eval_tests_not_status_only() -> None:
    """H3: offline eval tests under tests/evals/ must not be status-only green."""
    offenders: list[str] = []
    for name in _EVAL_TEST_FILES:
        path = EVALS_DIR / name
        if not path.is_file():
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for func_name, assert_blob in _function_assert_sources(tree):
            if not assert_blob:
                continue
            # Status-only pattern: every assert only mentions status/success without allowlist.
            lower = assert_blob.lower()
            only_status = (
                "status" in lower
                and "success" in lower
                and not any(tok.lower() in lower for tok in _H3_ALLOWLIST)
            )
            # Also flag bare ["status"] == "success" with no allowlisted name in function source.
            func_src = ""
            # Use raw source segment for allowlist scan of whole function body
            for node in ast.walk(tree):
                if (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == func_name
                ):
                    func_src = ast.get_source_segment(source, node) or ""
                    break
            allow_hit = any(tok in func_src for tok in _H3_ALLOWLIST)
            # Structure greps: read_text / re.search / "in source"
            structure_ok = (
                "read_text" in func_src
                or "re.search" in func_src
                or " in source" in func_src
                or "in text" in func_src
            )
            status_eq_success = (
                '["status"]' in func_src or "['status']" in func_src or ".status" in func_src
            ) and "success" in func_src
            if status_eq_success and not allow_hit and not structure_ok:
                offenders.append(f"{name}::{func_name}")
            if only_status and not structure_ok:
                offenders.append(f"{name}::{func_name}:status-only-assert-dump")
            # Proxy: if function has asserts but no allowlist hit and only status words
            if func_src and not allow_hit and not structure_ok:
                # Integrity/docs/loader/scorer always hit allowlist via assert content;
                # flag pure status green.
                if "status" in func_src and "success" in func_src and "assert" in func_src:
                    # require at least one non-status field name
                    non_status = any(
                        t in func_src
                        for t in (
                            "mae",
                            "code",
                            "nodeid",
                            "mode",
                            "weight",
                            "category",
                            "overall",
                            "passed",
                            "PROCEDURE",
                            "recipe",
                            "header",
                            "evaluation",
                            "CatalogError",
                            "missing",
                        )
                    )
                    if not non_status:
                        offenders.append(f"{name}::{func_name}:no-allowlist")

    assert not offenders, "H3 status-only or weak asserts:\n" + "\n".join(offenders)


def test_cases_json_is_json_only() -> None:
    """L1: catalog is cases.json (not TOML)."""
    assert (EVALS_DIR / "cases.json").is_file()
    assert not (EVALS_DIR / "cases.toml").exists()
