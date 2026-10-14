#!/usr/bin/env python3
"""checklist_columns.py — resolve review-checklist column ROLES from its header row.

The checklist table layout is NOT fixed — it varies by document type. Observed
on this instance (2026-10-14, project bsp_module):

  7 columns (Module Requirements / Architectural Design / Detailed Design):
    category | item | criteria | applicable | result | comment | defect-status
  6 columns (Module Functional Test Specification) — no category column, so
  every role shifts one to the left:
    item | criteria | applicable | result | comment | defect-status

So column INDICES must never be hardcoded: with [3,4,5] hardwired, a 6-column
checklist gets applicable written into result, result into comment, and comment
into the human-owned defect-status — and the old validator did not catch it.

Roles are matched by header keyword and resolved independently at fetch time and
again at write time. Anything ambiguous raises LayoutError, so a layout we do not
understand aborts the write instead of silently filling the wrong cells.
"""
import re

# role -> (required keyword, forbidden keyword or None)
# These are the Telechips defaults; the authoritative rules come from
# config/polarion.json (profile.column_roles) and are passed into resolve(),
# so another instance's checklist dialect needs no code change.
# The keyword for `result` also appears in the criteria column's header, so
# that role additionally forbids the criteria keyword.
_RULES = {
    "applicable":    ("대상", None),   # applicable (O/X)
    "result":        ("판정", "기준"),  # verdict (Pass/Fail)
    "comment":       ("의견", None),   # reviewer comment
    "defect_status": ("결함", None),   # defect status (Open/Closed)
}

FILL_ROLES = ("applicable", "result", "comment")  # written by the review
KEEP_ROLE = "defect_status"                       # human-owned, never written
ROLE_KEYS = tuple(_RULES)                         # all four, for drift comparison


class LayoutError(ValueError):
    """The header row could not be mapped onto the expected column roles."""


def _norm(s):
    return re.sub(r"\s+", "", s or "")


def same_text(a, b):
    """Compare two cell texts ignoring whitespace.

    fetch_checklist.py parses cells with HTMLParser (text chunks joined by a
    space) while write_checklist.py strips tags with a regex, so the same cell
    can come back as 'ends here. (method) starts' from one and
    'ends here.(method)starts' from the other. That difference is an artifact
    of the parsers, not an edit, and must never trip the lost-update check —
    so comparisons between the two are whitespace-insensitive. Any real edit to
    the words still differs."""
    return _norm(a) == _norm(b)


REQUIRED_ROLES = FILL_ROLES + (KEEP_ROLE,)


def validate_rules(rules):
    """A profile's column_roles must cover exactly the roles the writers use.

    resolve() accepts whatever a profile defines, but fill_map/preserved/
    describe/inject all need these four by name — without this check a profile
    that renames one dies later with a bare KeyError and no hint at the config."""
    if not rules:
        return
    missing = [r for r in REQUIRED_ROLES if r not in rules]
    if missing:
        raise LayoutError(
            "profile column_roles is missing required role(s) "
            f"{missing}; it defines {sorted(rules)}. The writers need "
            f"{list(REQUIRED_ROLES)} by name.")


def resolve(header_cells, rules=None):
    """header_cells -> {applicable, result, comment, defect_status: int,
                        template: [int, ...], ncols: int}

    `rules` is {role: (require, forbid)} — normally profile.column_roles from
    config/polarion.json; falls back to the built-in Telechips defaults.
    Raises LayoutError when a role matches zero or more than one column."""
    validate_rules(rules)
    norm = [_norm(c) for c in header_cells]
    roles = {}
    for role, (need, forbid) in (rules or _RULES).items():
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


def label_template_columns(header_cells, roles, labels):
    """Which template column is category / item / criteria.

    `labels` is profile.template_columns: a keyword per role, as configured.
    Returns {role: index}; a label that matches nothing is simply absent (the
    6-column functional-test layout has no category column)."""
    out = {}
    for role, needle in (labels or {}).items():
        hits = [i for i in roles["template"] if _norm(needle) in _norm(header_cells[i])]
        if len(hits) == 1:
            out[role] = hits[0]
    return out


def describe(roles):
    return (f"{roles['ncols']} cols | applicable={roles['applicable']} "
            f"result={roles['result']} comment={roles['comment']} "
            f"defect_status={roles['defect_status']} template={roles['template']}")


def compare(fetched, live):
    """Role indices resolved at fetch time vs at write time.

    The roles compared are the ones the LIVE resolve produced (so a profile may
    define roles beyond the four shipped ones and a drift in those is still
    seen). A role missing from `fetched` is NOT drift: that dict is copied
    through two LLM stages, and a key it failed to carry must not abort a write
    that is otherwise sound — a real column change shows up in ncols anyway.
    Returns a list of human-readable drift messages ([] when identical)."""
    drift = []
    if fetched.get("ncols") != live.get("ncols"):
        drift.append(f"column count {fetched.get('ncols')} -> {live.get('ncols')}")
    for r in sorted(set(live) - {"template", "ncols"}):
        if r in fetched and fetched.get(r) != live.get(r):
            drift.append(f"role '{r}' index {fetched.get(r)} -> {live.get(r)}")
    return drift
