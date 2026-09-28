#!/usr/bin/env python3
"""Measure how reliably pubmed.py finds real papers and rejects fabricated ones.

Every case is looked up by TITLE ONLY - the hardest realistic condition, and the
one that matters, since a hallucinated citation hands you a title and nothing else.

Run:  python tests/reliability.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import pubmed  # noqa: E402

CASES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "citation_cases.json")


def main() -> int:
    with open(CASES, encoding="utf-8") as fh:
        cases = json.load(fh)

    rows, failures = [], []
    for case in cases:
        res = pubmed.verify(title=case["title"])
        got, want = res["status"], case["expect"]

        if want == "any":
            verdict = "INFO"
        elif want == got:
            verdict = "pass"
        elif want == "verified" and got == "partial":
            verdict = "pass*"          # found the paper, flagged wording - acceptable
        else:
            verdict = "FAIL"
            failures.append((case, res))

        if case.get("expect_flag") == "retract":
            flagged = any("RETRACT" in f.upper() for f in res["flags"])
            if not flagged:
                verdict, _ = "FAIL", failures.append((case, res))

        rows.append((verdict, want, got, res["confidence"], case["title"], case["note"],
                     res.get("match") or {}))

    real = [r for r in rows if r[1] == "verified"]
    fake = [r for r in rows if r[1] == "not_found"]
    info = [r for r in rows if r[1] == "any"]

    print("=" * 78)
    print("REAL PAPERS (should resolve)")
    print("=" * 78)
    for v, _, got, conf, title, note, match in real:
        print(f"[{v:5}] {got:10} conf={conf:<5} {title[:52]}")
        print(f"         {note} -> PMID {match.get('pmid', '-')} {match.get('year', '')}")

    print()
    print("=" * 78)
    print("FABRICATED PAPERS (should NOT resolve)")
    print("=" * 78)
    for v, _, got, conf, title, note, _m in fake:
        print(f"[{v:5}] {got:10} conf={conf:<5} {title[:52]}")

    print()
    print("=" * 78)
    print("EDGE CASES (informational)")
    print("=" * 78)
    for _v, _w, got, conf, title, note, match in info:
        print(f"[     ] {got:10} conf={conf:<5} {title[:52]}")
        print(f"         {note}")
        if match:
            print(f"         resolved to -> {match.get('title', '')[:60]}")

    tp = sum(1 for r in real if r[0].startswith("pass"))
    tn = sum(1 for r in fake if r[0] == "pass")
    print()
    print("=" * 78)
    print(f"Real papers found:        {tp}/{len(real)}")
    print(f"Fabricated ones rejected: {tn}/{len(fake)}")
    print(f"Failures: {len(failures)}")
    for case, res in failures:
        print(f"  - {case['title'][:60]} (wanted {case['expect']}, got {res['status']})")
    print("=" * 78)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
