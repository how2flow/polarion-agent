#!/usr/bin/env python3
"""checklist_columns.py — resolve review-checklist column ROLES from its header row.

The checklist table layout is NOT fixed — it varies by document type. Observed
on this instance (2026-10-14, project bsp_module):

  7 columns (Module Requirements / Architectural Design / Detailed Design):
    분류 | 리뷰 항목 | 리뷰 방법 및 판정 기준 | 리뷰 대상 | 판정 결과 | 리뷰어 의견 | 리뷰 결함 상태
  6 columns (Module Functional Test Specification) — no 분류 column, so every
  role shifts one to the left:
    리뷰 항목 | 리뷰 방법 및 판정 기준 | 리뷰 대상 | 판정 결과 | 리뷰어 의견 | 리뷰 결함 상태

So column INDICES must never be hardcoded: with [3,4,5] hardwired, a 6-column
checklist gets 리뷰대상 written into 판정, 판정 into 의견, and 의견 into the
human-owned 결함상태 — and the old validator did not catch it.

Roles are matched by header keyword and resolved independently at fetch time and
again at write time. Anything ambiguous raises LayoutError, so a layout we do not
understand aborts the write instead of silently filling the wrong cells.
"""
import re

# role -> (required keyword, forbidden keyword or None)
# '판정' appears in BOTH '리뷰 방법 및 판정 기준' (a template column) and
# '판정 결과' (the reviewer's verdict), so `result` additionally forbids '기준'.
_RULES = {
    "applicable":    ("대상", None),   # 리뷰 대상 (O/X)
    "result":        ("판정", "기준"),  # 판정 결과 (Pass/Fail)
    "comment":       ("의견", None),   # 리뷰어 의견
    "defect_status": ("결함", None),   # 리뷰 결함 상태 및 결과 (Open/Closed)
}

FILL_ROLES = ("applicable", "result", "comment")  # written by the review
KEEP_ROLE = "defect_status"                       # human-owned, never written
ROLE_KEYS = tuple(_RULES)                         # all four, for drift comparison


class LayoutError(ValueError):
    """The header row could not be mapped onto the expected column roles."""


def _norm(s):
    return re.sub(r"\s+", "", s or "")


def resolve(header_cells):
    """header_cells -> {applicable, result, comment, defect_status: int,
                        template: [int, ...], ncols: int}

    Raises LayoutError when a role matches zero or more than one column."""
    norm = [_norm(c) for c in header_cells]
    roles = {}
    for role, (need, forbid) in _RULES.items():
        hits = [i for i, c in enumerate(norm)
                if need in c and (forbid is None or forbid not in c)]
        if len(hits) != 1:
            raise LayoutError(
                f"column role '{role}' matched {len(hits)} column(s) {hits} "
                f"in header {header_cells!r} — refusing to guess")
        roles[role] = hits[0]
    taken = set(roles.values())
    if len(taken) != len(roles):
        raise LayoutError(f"column roles collide: {roles} (header {header_cells!r})")
    roles["template"] = [i for i in range(len(header_cells)) if i not in taken]
    roles["ncols"] = len(header_cells)
    return roles


def fill_map(roles):
    """{column index: finding key} for the columns the review fills."""
    return {roles[r]: r for r in FILL_ROLES}


def preserved(roles):
    """Column indices that must stay byte-identical: template + defect status."""
    return list(roles["template"]) + [roles[KEEP_ROLE]]


def describe(roles):
    return (f"{roles['ncols']} cols | applicable={roles['applicable']} "
            f"result={roles['result']} comment={roles['comment']} "
            f"defect_status={roles['defect_status']} template={roles['template']}")


def compare(fetched, live):
    """Role indices resolved at fetch time vs at write time.
    Returns a list of human-readable drift messages ([] when identical)."""
    drift = []
    if fetched.get("ncols") != live.get("ncols"):
        drift.append(f"column count {fetched.get('ncols')} -> {live.get('ncols')}")
    for r in ROLE_KEYS:
        if fetched.get(r) != live.get(r):
            drift.append(f"role '{r}' index {fetched.get(r)} -> {live.get(r)}")
    return drift
