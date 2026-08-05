#!/usr/bin/env python3
"""Run offline/structure eval cases and write output/eval-report.json.

Usage:
    uv run python scripts/run_eval_report.py --offline
    uv run python scripts/run_eval_report.py --offline --require-pass   # alias of default
    uv run python scripts/run_eval_report.py --offline --inspect        # write report, exit 0

Default exit is **fail-closed**: overall PASS → 0, else 1.
``--inspect`` / ``--no-require-pass`` always exit 0 after writing the report
(operator inspection only — do not use as a release gate).

Only this script subprocesses pytest for the rubric report. Unit tests of the
pure scorer use synthetic passed_nodeids maps only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.evals.loader import load_cases, offline_structure_cases  # noqa: E402
from tests.evals.scorer import parse_junit_xml, score  # noqa: E402


def resolve_exit_code(overall: str, *, require_pass: bool) -> int:
    """Map rubric overall to process exit code.

    When ``require_pass`` is True (default / release gate), FAIL → 1.
    When False (``--inspect`` / ``--no-require-pass``), always 0 after write.
    """
    if not require_pass:
        return 0
    return 0 if overall == "PASS" else 1


def resolve_require_pass(
    *,
    require_pass: bool,
    inspect: bool,
    no_require_pass: bool,
) -> bool:
    """Derive fail-closed mode from CLI flags.

    Default (no flags): True.
    ``--require-pass``: True (explicit alias).
    ``--inspect`` or ``--no-require-pass``: False.
    Explicit ``--require-pass`` wins if combined with inspect flags.
    """
    if require_pass:
        return True
    if inspect or no_require_pass:
        return False
    return True


def _collect_offline_nodeids() -> list[str]:
    cases = offline_structure_cases(load_cases())
    nodeids: list[str] = []
    seen: set[str] = set()
    for case in cases:
        for nid in case.test_nodeids:
            if nid not in seen:
                seen.add(nid)
                nodeids.append(nid)
    return nodeids


def _run_pytest_junit(nodeids: list[str], junit_path: Path) -> int:
    if not nodeids:
        return 0
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--disable-warnings",
        f"--junitxml={junit_path}",
        *nodeids,
    ]
    print("Running:", " ".join(cmd), flush=True)
    proc = subprocess.run(cmd, cwd=str(ROOT), check=False)
    return int(proc.returncode)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate offline eval rubric report")
    parser.add_argument(
        "--offline",
        action="store_true",
        default=True,
        help="Score offline/structure cases (default)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "output" / "eval-report.json",
        help="Report JSON path (default: output/eval-report.json)",
    )
    parser.add_argument(
        "--require-pass",
        action="store_true",
        default=False,
        help="Fail-closed exit (default behavior): overall PASS → 0, else 1",
    )
    parser.add_argument(
        "--inspect",
        action="store_true",
        default=False,
        help="Write report and always exit 0 (operator inspection; not a release gate)",
    )
    parser.add_argument(
        "--no-require-pass",
        action="store_true",
        default=False,
        help="Same as --inspect: write report and always exit 0",
    )
    args = parser.parse_args(argv)

    require_pass = resolve_require_pass(
        require_pass=bool(args.require_pass),
        inspect=bool(args.inspect),
        no_require_pass=bool(args.no_require_pass),
    )

    cases = load_cases()
    nodeids = _collect_offline_nodeids()

    with tempfile.TemporaryDirectory(prefix="gimp-mcp-eval-") as tmp:
        junit_path = Path(tmp) / "junit.xml"
        # Run even if some fail — scorer uses pass set, not process exit.
        _run_pytest_junit(nodeids, junit_path)
        if junit_path.is_file():
            passed = parse_junit_xml(junit_path)
        else:
            passed = set()

    # JUnit classname style may not match catalog path-style nodeids.
    # Also accept path-style if pytest wrote them; merge with a second pass
    # that normalizes alternate forms already handled in parse_junit_xml.
    # Additionally, re-map common pytest classname forms to catalog paths.
    normalized: set[str] = set(passed)
    for nid in list(passed):
        # If we already have path form, keep it.
        if nid.startswith("tests/"):
            normalized.add(nid)
        # tests.test_foo.py::name already handled in adapter
    # Prefer exact catalog nodeids when present under either form
    catalog_nids = set(nodeids)
    matched = set()
    for want in catalog_nids:
        if want in normalized:
            matched.add(want)
            continue
        # try classname form variants
        alt = want.replace("/", ".").replace(".py::", "::")
        # tests/test_verify.py::foo → tests.test_verify::foo was converted to
        # tests/test_verify.py::foo by adapter — already path form.
        if alt in normalized:
            matched.add(want)
        # bare function match against any passed ending
        leaf = want.split("::", 1)[-1]
        for p in normalized:
            if p.endswith("::" + leaf) or p == leaf:
                # ensure same module stem
                want_mod = want.split("::", 1)[0]
                p_mod = p.split("::", 1)[0]
                if Path(want_mod).stem == Path(p_mod).stem or want_mod in p_mod.replace(".", "/"):
                    matched.add(want)
                    break

    report = score(cases, matched if matched else normalized)
    out_path: Path = args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )

    # Human summary
    print()
    print("=== Eval report ===")
    print(f"overall:            {report.overall}")
    print(f"offline_pass_rate:  {report.offline_pass_rate:.4f}")
    print("release_gates:")
    for g in report.release_gates:
        mark = "PASS" if g.passed else "FAIL"
        print(f"  [{mark}] {g.case_id}")
    print("category_scores:")
    for c in report.category_scores:
        print(
            f"  {c.category:24s} w={c.weight:2d}  {c.passed}/{c.total}  rate={c.case_pass_rate:.3f}"
        )
    if report.residuals:
        print(f"residuals ({len(report.residuals)}):")
        for r in report.residuals[:12]:
            print(f"  - {r}")
        if len(report.residuals) > 12:
            print(f"  … +{len(report.residuals) - 12} more")
    print(f"wrote {out_path}")
    print(f"require_pass:       {require_pass}")

    return resolve_exit_code(report.overall, require_pass=require_pass)


if __name__ == "__main__":
    raise SystemExit(main())
