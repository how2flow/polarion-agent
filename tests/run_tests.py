#!/usr/bin/env python3
"""Regression suite — offline, no Polarion, no dependencies.

    python3 tests/run_tests.py [-v]

Every case here exists because something actually went wrong: a write landed in
the wrong column, a guard failed to fire, a fix broke the fix before it. Run it
after every change to the review pipeline, before handing a diff to review.
"""
import io, json, os, re, sys, tempfile, traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "skills", "doc-review"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import checklist_columns as C
import polarion_config as cfg
import fetch_checklist as F
import write_checklist as W
import fixtures as X

LABELS = {"category": "분류", "item": "항목", "criteria": "기준"}


# ---------------------------------------------------------------- column roles
def test_roles_7col():
    r = C.resolve(X.H7)
    assert (r["applicable"], r["result"], r["comment"], r["defect_status"]) == (3, 4, 5, 6)
    assert r["template"] == [0, 1, 2] and r["ncols"] == 7


def test_roles_6col_shifts_left():
    r = C.resolve(X.H6)
    assert (r["applicable"], r["result"], r["comment"], r["defect_status"]) == (2, 3, 4, 5)
    assert r["template"] == [0, 1] and r["ncols"] == 6


def test_roles_reject_unknown_header():
    bad = [h.replace("판정 결과", "검토 결론") for h in X.H6]
    try:
        C.resolve(bad)
    except C.LayoutError as e:
        assert "result" in str(e)
        return
    raise AssertionError("an unrecognized header must not resolve")


def test_roles_reject_ambiguous():
    dup = X.H7 + ["추가 의견"]          # two columns match `comment`
    try:
        C.resolve(dup)
    except C.LayoutError:
        return
    raise AssertionError("two columns matching one role must not resolve")


def test_profile_rules_must_cover_required_roles():
    try:
        C.resolve(X.H7, {"applicable": ("대상", None), "verdict": ("판정", "기준")})
    except C.LayoutError as e:
        assert "required role" in str(e)
        return
    raise AssertionError("a profile missing a required role must be rejected")


# ------------------------------------------------------------------- comparing
def test_compare_detects_moved_role():
    live = C.resolve(X.H7)
    assert C.compare(dict(live, result=9), live)


def test_compare_ignores_key_missing_from_fetched():
    live = C.resolve(X.H7)
    assert C.compare({"applicable": 3, "ncols": 7}, live) == []


def test_compare_sees_profile_defined_extra_role():
    live = dict(C.resolve(X.H7), custom=9)
    assert C.compare(dict(live, custom=2), live)


def test_same_text_ignores_whitespace_only():
    assert C.same_text("a. (b) c", "a.(b)c")
    assert not C.same_text("a b", "a c")


# --------------------------------------------------------------- cell  parsing
def test_both_parsers_agree_on_cell_text():
    """fetch parses with HTMLParser, write with a regex; a mismatch once made
    every multi-tag cell look edited and aborted the write."""
    rows = [X.row7(i) for i in range(1, 4)]
    html = X.table_html(X.H7, rows)
    p = F._Table(); p.feed(html)
    fetched = [r for r in p.rows if any(c.strip() for c in r)]
    written = [W.cells_text(m.group(0)) for m in W.split_rows(html)]
    assert len(fetched) == len(written)
    for a, b in zip(fetched, written):
        assert all(C.same_text(x, y) for x, y in zip(a, b)), (a, b)


# ------------------------------------------------------------------- injection
def _inject(header, rows, result="Pass"):
    html = X.table_html(header, rows)
    roles = C.resolve(header)
    filled = X.findings(rows, roles, result=result)
    new, skipped = W.inject(html, filled["results"], roles)
    W.validate(html, new, roles)
    return html, new, roles, skipped


def test_inject_writes_only_reviewer_columns_7col():
    rows = [X.row7(i) for i in range(1, 5)]
    old, new, roles, skipped = _inject(X.H7, rows)
    assert skipped == []
    fill = set(C.fill_map(roles))
    o = [W.cells_text(m.group(0)) for m in W.split_rows(old)]
    n = [W.cells_text(m.group(0)) for m in W.split_rows(new)]
    for i in range(1, len(o)):
        for c in range(len(o[i])):
            changed = o[i][c] != n[i][c]
            assert changed == (c in fill), f"row {i} col {c} changed={changed}"


def test_inject_writes_only_reviewer_columns_6col():
    rows = [X.row6(i) for i in range(1, 5)]
    old, new, roles, skipped = _inject(X.H6, rows)
    assert skipped == []
    n = [W.cells_text(m.group(0)) for m in W.split_rows(new)]
    assert n[1][roles["applicable"]] == "O"
    assert n[1][roles["result"]] == "Pass"
    assert n[1][roles["defect_status"]] == "", "defect status is human-owned"


def test_human_guard_skips_row_with_defect_status():
    rows = [X.row7(1, defect="Open"), X.row7(2)]
    _, _, _, skipped = _inject(X.H7, rows)
    assert skipped == [0]


def test_human_guard_skips_row_with_result_only():
    """The gap that nearly overwrote 3 human Fail comments: a person fills the
    verdict and leaves the defect status blank."""
    rows = [X.row7(1, result="Fail", comment="human wrote this"), X.row7(2)]
    _, new, roles, skipped = _inject(X.H7, rows)
    assert skipped == [0]
    n = [W.cells_text(m.group(0)) for m in W.split_rows(new)]
    assert n[1][roles["comment"]] == "human wrote this"


def test_inject_rejects_row_count_mismatch():
    rows = [X.row7(i) for i in range(1, 4)]
    html = X.table_html(X.H7, rows)
    roles = C.resolve(X.H7)
    filled = X.findings(rows[:2], roles)
    try:
        W.inject(html, filled["results"], roles)
    except SystemExit:
        return
    raise AssertionError("fewer filled entries than data rows must abort")


# ------------------------------------------------------------ write-side gates
def test_layout_gate_rejects_header_without_th():
    rows = [X.row7(1)]
    html = X.table_html(X.H7, rows).replace("<th", "<td").replace("</th>", "</td>")
    try:
        W.resolve_layout(html, {})
    except SystemExit as e:
        assert "LAYOUT" in str(e)
        return
    raise AssertionError("a table with no <th> header must abort")


def test_layout_gate_rejects_drift_since_fetch():
    rows = [X.row7(1)]
    html = X.table_html(X.H7, rows)
    stale = {"columns": {"roles": dict(C.resolve(X.H7), result=9)}}
    try:
        W.resolve_layout(html, stale)
    except SystemExit as e:
        assert "LAYOUT DRIFT" in str(e)
        return
    raise AssertionError("a role that moved since review must abort")


def test_lost_update_detects_template_edit():
    rows = [X.row7(i) for i in range(1, 4)]
    html = X.table_html(X.H7, rows)
    roles = C.resolve(X.H7)
    filled = X.findings(rows, roles)
    edited = [list(r) for r in rows]
    edited[1][1] = "this item was reworded after the review"
    filled["template_rows"] = edited
    try:
        W.lost_update_check(filled, W.split_rows(html), roles)
    except SystemExit as e:
        assert "LOST-UPDATE" in str(e)
        return
    raise AssertionError("a template column edited since review must abort")


def test_lost_update_tolerates_whitespace_only_difference():
    rows = [X.row7(i) for i in range(1, 4)]
    html = X.table_html(X.H7, rows)
    roles = C.resolve(X.H7)
    filled = X.findings(rows, roles)
    filled["template_rows"] = [[c.replace(" ", "  ") for c in r] for r in rows]
    W.lost_update_check(filled, W.split_rows(html), roles)   # must not raise


def test_lost_update_detects_row_count_change():
    rows = [X.row7(i) for i in range(1, 4)]
    html = X.table_html(X.H7, rows)
    roles = C.resolve(X.H7)
    filled = X.findings(rows[:2], roles)
    try:
        W.lost_update_check(filled, W.split_rows(html), roles)
    except SystemExit as e:
        assert "LOST-UPDATE" in str(e)
        return
    raise AssertionError("a changed row count must abort")


def test_type_gate_rejects_non_review_types():
    for wtype in ("MRS", "Ins_defect", "task", ""):
        wi = X.work_item(wtype=wtype)
        assert W.wi_type(wi) == wtype
        assert W.wi_type(wi) != "REVIEW" or wtype == "REVIEW"


def test_ownership_helpers():
    assert W.wi_owners(X.work_item(owner="someone")) == {"someone"}
    assert W.wi_owners(X.work_item(owner=None)) == set()


# ------------------------------------------------------- template comparison
def test_template_compare_current():
    rows = [X.row7(i) for i in range(1, 4)]
    r = F.compare_to_template(X.payload_from(X.H7, rows), X.payload_from(X.H7, rows), LABELS)
    assert r["status"] == "current", r


def test_template_compare_reports_missing():
    doc = [X.row7(i) for i in range(1, 3)]
    tpl = [X.row7(i) for i in range(1, 4)]
    r = F.compare_to_template(X.payload_from(X.H7, doc), X.payload_from(X.H7, tpl), LABELS)
    assert r["status"] == "outdated" and len(r["missing_items"]) == 1


def test_template_compare_same_item_under_two_categories_stays_distinct():
    a = X.row7(1, category="스타일"); b = X.row7(1, category="Overall")
    r = F.compare_to_template(X.payload_from(X.H7, [a, b]), X.payload_from(X.H7, [a, b]), LABELS)
    assert r["document_item_count"] == 2, r


def test_template_compare_counts_duplicates_separately():
    a = X.row7(1)
    r = F.compare_to_template(X.payload_from(X.H7, [a, list(a)]),
                              X.payload_from(X.H7, [a]), LABELS)
    assert r.get("duplicate_rows", {}).get("document") == 1
    assert "unkeyed_rows" not in r, "a duplicate is not a short row"


def test_template_compare_refuses_when_labels_differ():
    """Different wording for a template column makes every key differ; calling
    that a mismatch would post a permanent comment claiming everything is gone."""
    other = ["구분"] + X.H7[1:]
    rows = [X.row7(i) for i in range(1, 4)]
    r = F.compare_to_template(X.payload_from(other, rows), X.payload_from(X.H7, rows), LABELS)
    assert r["status"] == "unknown" and "reason" in r


def test_template_compare_keeps_layout_diff_when_unaligned():
    doc = X.payload_from(X.H6, [X.row6(1)])
    tpl = X.payload_from(X.H7, [X.row7(1)])
    r = F.compare_to_template(doc, tpl, LABELS)
    assert r["status"] == "unknown"
    assert r.get("layout_diff"), "the layout difference is the real signal here"


def test_profile_matches_by_project():
    p = cfg.profile_for(project_id="bsp_module", quiet=True)
    assert p["_matched"] is True
    assert cfg.review_type(p) and cfg.checklist_field(p)


def test_defaulted_profile_is_not_matched():
    assert cfg.profile_for(project_id="no_such_project", quiet=True)["_matched"] is False
    assert cfg.profile_for(quiet=True)["_matched"] is False


def test_unknown_profile_name_is_rejected():
    try:
        cfg.profile_for(name="no-such-profile", quiet=True)
    except SystemExit as e:
        assert "unknown profile" in str(e)
        return
    raise AssertionError("an unknown --profile must be rejected")


def test_configured_roles_resolve_live_headers():
    """The shipped profile must actually resolve both real layouts."""
    rules = cfg.column_rules(cfg.profile_for(project_id="bsp_module", quiet=True))
    assert C.resolve(X.H7, rules)["defect_status"] == 6
    assert C.resolve(X.H6, rules)["defect_status"] == 5


# ------------------------------------------------------------------- encoding
def test_path_segments_are_encoded():
    """An id with a space raises http.client.InvalidURL, which is not caught by
    anything in the stack."""
    assert F.seg("a b") == "a%20b"
    assert F.seg("TF-A/UART") == "TF-A%2FUART"
    import http.client
    assert not issubclass(http.client.InvalidURL, (ValueError, OSError))


def _unencoded_path_segments(path):
    """Every {expr} inside a /projects/... f-string that is not a seg(...) call.

    Parsed with ast, not a regex: an f-string can nest quotes inside its
    placeholders (f"...{seg(j['target'])}..."), which no simple pattern survives.
    """
    import ast
    try:
        with io.open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except (SyntaxError, UnicodeDecodeError, OSError) as e:
        print(f"  [warn] raw-path scan skipped {os.path.relpath(path, ROOT)}: {e}",
              file=sys.stderr)
        return []
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        literal = "".join(v.value for v in node.values
                          if isinstance(v, ast.Constant) and isinstance(v.value, str))
        if "/projects/" not in literal:
            continue
        for v in node.values:
            if isinstance(v, ast.FormattedValue):
                expr = ast.unparse(v.value)
                if not expr.startswith("seg("):
                    bad.append(f"{os.path.relpath(path, ROOT)}:{node.lineno} -> {{{expr}}}")
    return bad


def _repo_python_files():
    for base, dirs, names in os.walk(ROOT):
        rel = os.path.relpath(base, ROOT)
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
        if rel == "tests" or rel.startswith("tests" + os.sep):
            continue
        for fn in names:
            if fn.endswith(".py") and not fn.startswith("."):
                yield os.path.join(base, fn)


def test_no_module_builds_a_raw_project_path():
    """Encoding one call site is not the fix; every segment of every site has
    to use it."""
    bad = []
    for path in _repo_python_files():
        bad += _unencoded_path_segments(path)
    assert not bad, "unencoded id in a URL path: " + "; ".join(bad)


def test_the_raw_path_scan_actually_scans_something():
    """The previous version silently checked zero files; prove it sees code."""
    files = list(_repo_python_files())
    assert len(files) >= 5, files
    assert any(f.endswith("fetch_checklist.py") for f in files)


def test_the_raw_path_scan_catches_a_partly_encoded_line(tmp_module):
    """A line may encode one id and leave its neighbour raw."""
    p = tmp_module('x = f"/projects/{project}/workitems/{seg(wi)}"\n')
    assert _unencoded_path_segments(p), "a raw neighbour segment must be caught"
    p = tmp_module("""x = f"/projects/{j['proj']}/workitems/{did}"\n""")
    assert len(_unencoded_path_segments(p)) == 2, "quotes inside a placeholder must not hide it"
    p = tmp_module('x = f"/projects/{seg(a)}/workitems/{seg(b)}"\n')
    assert _unencoded_path_segments(p) == []


def test_runner_records_a_test_that_exits():
    """A guard regressing into sys.exit must show as a failure, not end the
    run. Verified by running the runner, not by reading its source."""
    def boom():
        raise SystemExit("LOST-UPDATE: oops")
    passed, failed = run([("boom", boom)], quiet=True)
    assert (passed, len(failed)) == (0, 1), (passed, failed)
    assert "LOST-UPDATE" in failed[0][1]


def test_runner_lets_keyboard_interrupt_through():
    """Ctrl-C must stop the run, not be recorded as a failed test."""
    def interrupted():
        raise KeyboardInterrupt
    try:
        run([("interrupted", interrupted)], quiet=True)
    except KeyboardInterrupt:
        return
    raise AssertionError("KeyboardInterrupt must not be swallowed")


def test_systemexit_is_not_an_exception():
    """polarion_rest reports every REST failure with sys.exit, so an
    `except Exception` handler silently stops catching them."""
    assert not issubclass(SystemExit, Exception)


def test_seat_exhaustion_is_recognised_by_its_producer():
    """polarion_rest writes the message and also decides what it means, so a
    reword cannot silently disable the callers that re-raise on it."""
    import polarion_rest as PR
    assert PR.is_seat_exhausted(SystemExit(
        f"{PR.SEAT_EXHAUSTED} 7200s budget on GET /x — giving up"))
    assert not PR.is_seat_exhausted(SystemExit("REST 404 on GET /x"))


def test_fetch_template_degrades_on_seat_exhaustion(monkey_find_wis):
    """fetch_template runs last, after a seat already bought the checklist;
    raising here would discard it before --out is written."""
    import shutil, tempfile
    monkey_find_wis(SystemExit("seat-limit persisted beyond 7200s budget"))
    prof = cfg.profile_for(project_id="bsp_module", quiet=True)
    # fetch_template makedirs the cache path before it fails; without this the
    # suite leaves an empty .cache/templates/<profile>/ in the checkout
    orig, d = cfg.cache_dir, tempfile.mkdtemp()
    cfg.cache_dir = lambda *a, **k: d
    try:
        tpl, meta = F.fetch_template("t", prof, "Module_Function_Test_Specification", "checklist")
    finally:
        cfg.cache_dir = orig
        shutil.rmtree(d, ignore_errors=True)
    assert meta.get("source") in ("none", "cache"), meta
    assert "seat-limit" in (meta.get("error") or meta.get("live_error") or "")


def _prof(pid="bsp_module"):
    return cfg.profile_for(project_id=pid, quiet=True)


def _check(**kw):
    return F.run_template_check(
        "t", kw.pop("prof", _prof()), kw.pop("doctype", "D"), "checklist", None,
        "REVIEW", kw.pop("doc_status", "review-wi-no-checklist"),
        kw.pop("project", "p"), {}, **kw)


FALLBACK_MATRIX = [
    # (label, kwargs, with own checklist, without one)
    ("opt-out",            {"enabled": False},            "none", "made-by-agent"),
    ("unmatched profile",  {"profile_id": "no_such"},     "none", "made-by-agent"),
    ("doctype unreadable", {"doctype": ""},               "none", "stop"),
    ("doctype unconfigured", {"doctype": "NoSuchDoctype"}, "none", "made-by-agent"),
    ("template available", {},                            "none", "template"),
]


def _seeded_cache(doctype, header, rows):
    """A cache dir holding one template, so the suite never depends on the
    gitignored .cache/ this machine happens to have."""
    import json, tempfile
    d = tempfile.mkdtemp()
    with io.open(os.path.join(d, f"{doctype}.json"), "w", encoding="utf-8") as f:
        json.dump({"meta": {"source": "live", "document_name": "T"},
                   "payload": X.payload_from(header, rows)}, f)
    return d


def test_fallback_matrix(monkey_find_wis):
    """A document with its own checklist is governed by it whatever the
    template check did; only a document without one depends on the outcome."""
    import shutil
    monkey_find_wis(SystemExit("no network in the suite"))
    d = _seeded_cache("Module_Function_Test_Specification", X.H6,
                      [X.row6(i) for i in range(1, 4)])
    orig, cfg.cache_dir = cfg.cache_dir, (lambda *a, **k: d)
    try:
        _run_matrix()
    finally:
        cfg.cache_dir = orig
        shutil.rmtree(d, ignore_errors=True)


def _run_matrix():
    for label, kw, with_own, without in FALLBACK_MATRIX:
        kw = dict(kw)
        pid = kw.pop("profile_id", None)
        base = {"doctype": kw.pop("doctype", "Module_Function_Test_Specification")}
        if pid:
            base["prof"] = _prof(pid)
        got_own = _check(doc_status="checklist-present", **base, **kw).get("fallback")
        got_not = _check(doc_status="review-wi-no-checklist", **base, **kw).get("fallback")
        assert (got_own, got_not) == (with_own, without), (label, got_own, got_not)


def test_compare_result_carries_a_fallback():
    """Callers read `fallback` unconditionally; the compare path once omitted it
    and was only well-defined because run_template_check pre-seeded the key."""
    rows = [X.row7(i) for i in range(1, 3)]
    tpl = X.payload_from(X.H7, rows)
    r = F.build_template_check("checklist-present", tpl, LABELS, tpl)
    assert r.get("fallback") == "none", r


def test_untyped_document_is_permanent_not_transient():
    """Metadata read fine, the document just has no type: no template can match
    it, and stopping would repeat forever."""
    r = _check(doctype="", doc_meta={"title": "x", "type": ""})
    assert r["fallback"] == "made-by-agent", r
    assert "no type" in r["error"]


def test_unreadable_metadata_stops():
    r = _check(doctype="", doc_meta=None)
    assert r["fallback"] == "stop", r


def test_fetch_document_returns_none_when_nothing_was_read(monkey_rest_get):
    """An empty dict would read as "the document was read and has no type",
    which sends the agent off to invent criteria on a transient blip."""
    monkey_rest_get(None)
    assert F.fetch_document("t", "p", "s", "d") is None
    monkey_rest_get({"data": {"attributes": {"type": "X", "title": "T"}}})
    assert F.fetch_document("t", "p", "s", "d")["type"] == "X"


def test_profile_without_templates_does_not_block():
    """A project that genuinely has no official templates must still be
    reviewable; only a half-finished templates block is a config error."""
    prof = dict(_prof()); prof.pop("templates", None)
    _, meta = F.fetch_template("t", prof, "D", "checklist")
    assert not meta.get("config_error")
    assert F.build_template_check("review-wi-no-checklist", None, LABELS, {}, meta)["fallback"] \
        == "made-by-agent"


def test_half_configured_templates_block_stops():
    for broken in ({"project_id": "G"},
                   {"project_id": "G", "space_id": "S", "doctypes": {}}):
        prof = dict(_prof()); prof["templates"] = broken
        _, meta = F.fetch_template("t", prof, "D", "checklist")
        assert meta.get("config_error") is True, broken
        assert F.build_template_check("review-wi-no-checklist", None, LABELS, {}, meta)["fallback"] \
            == "stop", broken


def test_main_passes_document_metadata_to_the_check():
    """Without it the two doctype-empty causes collapse into one."""
    import inspect
    src = inspect.getsource(F.main)
    assert "doc_meta=dmeta" in src


def test_missing_doctypes_map_is_a_config_error():
    """Dropping the map is as much a deployment bug as dropping space_id;
    one stopping and the other improvising would be arbitrary."""
    prof = dict(_prof()); prof["templates"] = {"project_id": "G", "space_id": "S"}
    _, meta = F.fetch_template("t", prof, "D", "checklist")
    assert meta.get("config_error") is True, meta
    assert F.build_template_check("review-wi-no-checklist", None, LABELS, {}, meta)["fallback"] == "stop"


def test_prompts_do_not_list_unreadable_doctype_as_improvisable():
    """It maps to "stop"; naming it under made-by-agent gives the agent two
    opposite instructions for one state."""
    # the enumeration of reasons, not just any mention of the word
    anchors = {"doc-review.md": ("**Made-by-agent**", "**Stop instead"),
               "requirements.md": ("3. **made-by-agent**",
                                   "is the single authority")}
    for name, (start, end) in anchors.items():
        md = re.sub(r"\s+", " ", io.open(
            os.path.join(ROOT, "skills", "doc-review", name), encoding="utf-8").read())
        assert start in md and end in md, (name, "anchors moved")
        block = md[md.index(start):md.index(end)]
        for phrase in ("doctype could not be read", "doctype unreadable"):
            assert phrase not in block, (name, phrase)


def test_broken_template_config_stops_rather_than_improvising():
    """A dropped project_id/space_id is a deployment bug; inventing criteria
    would hide it behind something that reads like a governed review."""
    prof = dict(_prof()); prof["templates"] = {"doctypes": {"D": "doc"}}
    _, meta = F.fetch_template("t", prof, "D", "checklist")
    assert meta.get("config_error") is True and "config/polarion.json" in meta["error"]
    r = F.build_template_check("review-wi-no-checklist", None, LABELS, {}, meta)
    assert r["fallback"] == "stop", r
    r = F.build_template_check("checklist-present", None, LABELS, {}, meta)
    assert r["fallback"] == "none", r


def test_unreachable_template_stops_instead_of_improvising(monkey_find_wis):
    """Live fetch failed and no stored copy exists."""
    import shutil, tempfile
    monkey_find_wis(SystemExit("seat-limit persisted beyond 7200s budget"))
    orig, empty = cfg.cache_dir, tempfile.mkdtemp()
    cfg.cache_dir = lambda *a, **k: empty
    try:
        r = _check(doctype="Module_Function_Test_Specification")
    finally:
        cfg.cache_dir = orig
        shutil.rmtree(empty, ignore_errors=True)
    assert r["fallback"] == "stop", r


def test_cached_template_is_still_usable(monkey_find_wis):
    """A stored copy provides governed criteria, so nothing stops."""
    import json, shutil, tempfile
    monkey_find_wis(SystemExit("seat-limit persisted beyond 7200s budget"))
    rows = [X.row6(i) for i in range(1, 4)]
    payload = X.payload_from(X.H6, rows)
    d = tempfile.mkdtemp()
    with io.open(os.path.join(d, "Module_Function_Test_Specification.json"),
                 "w", encoding="utf-8") as f:
        json.dump({"meta": {"source": "live", "document_name": "T4"}, "payload": payload}, f)
    orig, cfg.cache_dir = cfg.cache_dir, (lambda *a, **k: d)
    try:
        r = _check(doctype="Module_Function_Test_Specification")
    finally:
        cfg.cache_dir = orig
        shutil.rmtree(d, ignore_errors=True)
    assert r["template"]["source"] == "cache" and r["fallback"] == "template", r


def test_prompts_point_at_the_fallback_field():
    """Both files are concatenated into one prompt, so a rule stated in one and
    contradicted in the other is what the agent actually receives."""
    directives = ("do not infer", "single authority")
    for name in ("doc-review.md", "requirements.md"):
        raw = io.open(os.path.join(ROOT, "skills", "doc-review", name),
                      encoding="utf-8").read()
        md = re.sub(r"\s+", " ", raw)      # prose wraps; compare unwrapped
        for value in ('"template"', '"made-by-agent"', '"stop"', '"none"'):
            assert value in md, (name, value)
        assert any(d in md for d in directives), \
            f"{name} must say fallback decides, not error text"
    # requirements.md owns the tier numbering; doc-review.md names the tiers in
    # words. Numbering them in both is how the two came to disagree.
    dr = re.sub(r"\s+", " ", io.open(
        os.path.join(ROOT, "skills", "doc-review", "doc-review.md"),
        encoding="utf-8").read())
    mapping = dr[dr.index("`template_check.fallback` says"):]
    mapping = mapping[:mapping.index("1. **Default template**")]
    assert not re.search(r"tier \d", mapping, re.I), \
        f"doc-review.md must not number the tiers: {mapping[:120]}"


def test_tier2_payload_carries_the_template_items():
    rows = [X.row7(i) for i in range(1, 4)]
    tpl = X.payload_from(X.H7, rows)
    r = F.build_template_check("review-wi-no-checklist", tpl, LABELS, {}, {})
    assert r["status"] == "no-document-checklist"
    assert r["template_checklist"]["rows"][0] == X.H7, "rows[0] must be the header"
    assert r["template_checklist"]["data_row_count"] == 3
    assert r["fallback"] == "template"


def test_doctype_degrades_on_rest_failure(monkey_fetch_doc):
    """A 404 on the document must not end the run: --doctype takes over."""
    monkey_fetch_doc(SystemExit("REST 404 on GET .../documents/X"))
    doctype, meta = F.resolve_doctype("t", "p", "s", "d", fallback="FromFlag")
    assert doctype == "FromFlag"
    assert meta is None, "None means the read failed; {} would read as 'read, untyped'"


def test_doctype_resolution_reraises_seat_exhaustion(monkey_fetch_doc):
    """Nothing has been produced at this point, so exhaustion still ends it."""
    monkey_fetch_doc(SystemExit("seat-limit persisted beyond 7200s budget"))
    try:
        F.resolve_doctype("t", "p", "s", "d", fallback="FromFlag")
    except SystemExit:
        return
    raise AssertionError("seat exhaustion must not be degraded here")


def test_document_type_wins_over_the_flag(monkey_fetch_doc):
    monkey_fetch_doc({"type": "FromDocument", "title": "t", "status": "draft"})
    assert F.resolve_doctype("t", "p", "s", "d", fallback="FromFlag")[0] == "FromDocument"


def test_fetch_document_propagates_rest_failure():
    """fetch_document deliberately lets polarion_rest's sys.exit through; main()
    is what degrades it to --doctype."""
    import polarion_rest as PR
    assert not issubclass(SystemExit, Exception)


# ------------------------------------------------------------- template sync
SYNC_LIVE = {"source": "live", "document_name": "Template2_Module Architectural Design"}


def _tpl_payload(html=None):
    html = html if html is not None else X.table_html(X.H7, [X.row7(1), X.row7(2)])
    return {"checklist_html": html, "rows": [X.H7, X.row7(1), X.row7(2)],
            "item_count": 3, "data_row_count": 2,
            "template_rows": [X.row7(1), X.row7(2)],
            "checklist_text": "t", "dropped_blank_rows": 0,
            # the writer's census of the same html, not a guess
            **dict(zip(("tr_count", "th_rows"), F.row_census(html))),
            # real roles: a template whose header does not resolve is refused
            "columns": {"header": X.H7, "roles": C.resolve(X.H7)}}


def test_should_sync_on_any_mismatch():
    """The rule is the one asked for: if it does not match the template,
    replace it. What must be sound is the table being WRITTEN, which
    template_write_problem checks -- not the one being replaced."""
    for st in ("outdated", "diverged", "no-document-checklist", "unknown"):
        assert F.should_sync({"status": st}), st
    for st in ("current", "unavailable"):
        assert not F.should_sync({"status": st}), st


def test_an_unreadable_checklist_is_the_clearest_mismatch():
    """Three real checklists are 6 columns with no defect-status column, so
    their roles never resolve, the comparison cannot run, and the status is
    'unknown' with no layout_diff. Requiring one meant the documents most in
    need of the template were the only ones that never got it."""
    assert F.should_sync({"status": "unknown", "layout_diff": {"result": [4, 3]}})
    assert F.should_sync({"status": "unknown", "layout_diff": None})
    assert F.should_sync({"status": "unknown",
                          "reason": "could not label item/criteria columns"})


def test_sync_writes_the_template_html_verbatim(monkey_patch):
    calls = monkey_patch()
    tpl = _tpl_payload()
    r = F.sync_to_template("t", "p", "W-1", "checklist",
                           {"status": "outdated", "template": SYNC_LIVE}, tpl, True,
                           wi_type="REVIEW", review_type="REVIEW", live={})
    assert r["synced"] and len(calls) == 1, (r, calls)
    url, body = calls[0]
    # The RAW field value. checklist_text is detagged, and writing that back
    # would replace the table with a line of prose.
    assert body["data"]["attributes"]["checklist"]["value"] == tpl["checklist_html"]
    assert "<table" in body["data"]["attributes"]["checklist"]["value"]
    assert body["data"]["id"] == "p/W-1" and body["data"]["type"] == "workitems"
    assert url.endswith("/projects/p/workitems/W-1"), url


def test_sync_honours_the_configured_checklist_field(monkey_patch):
    calls = monkey_patch()
    F.sync_to_template("t", "p", "W-1", "reviewChecklist",
                       {"status": "outdated", "template": SYNC_LIVE},
                       _tpl_payload(), True, wi_type="REVIEW", review_type="REVIEW", live={})
    assert "reviewChecklist" in calls[0][1]["data"]["attributes"]


def test_sync_refuses_and_writes_nothing_when_a_gate_fails(monkey_patch):
    calls = monkey_patch()
    live = {"status": "outdated", "template": SYNC_LIVE}
    tpl = _tpl_payload()
    empty = {"status": "no-document-checklist", "template": SYNC_LIVE}
    cases = {
        "nothing to sync":   ({"status": "current", "template": SYNC_LIVE}, tpl, "W-1", True, {}),
        "cached or missing": ({"status": "outdated", "template": {"source": "cache"}},
                              tpl, "W-1", True, {}),
        "no checklist HTML": (live, {"checklist_html": ""}, "W-1", True, {}),
        "no REVIEW work item": (live, tpl, "", True, {}),
        "does not own":      (live, tpl, "W-1", False, {}),
        # --review-wi bypasses the type filter and is chosen by an AI
        "not 'REVIEW'":      (live, tpl, "W-1", True, {"wi_type": "Ins_defect"}),
        # an empty-looking field that is not actually empty
        "markup with no text": (empty, tpl, "W-1", True,
                                {"live": {"checklist_html": "<p><img src=x/></p>"}}),
        # a template that is prose, not a table
        "did not parse as a checklist": (live, dict(tpl, data_row_count=0), "W-1", True, {}),
        # several review items in the template document: no safe pick
        "refusing to guess which one": ({"status": "outdated",
                                         "template": dict(SYNC_LIVE, candidates=2)},
                                        tpl, "W-1", True, {}),
    }
    for expect, (check, t, wi, owned, over) in cases.items():
        kw = dict({"wi_type": "REVIEW", "review_type": "REVIEW", "live": {}},
                  **over)
        r = F.sync_to_template("t", "p", wi, "checklist", check, t, owned, **kw)
        assert not r["synced"], (expect, r)
        assert expect in r["reason"], (expect, r)
    assert calls == [], f"a refused sync still issued a PATCH: {calls}"


def test_sync_writes_into_a_genuinely_empty_checklist(monkey_patch):
    """The counterpart to the markup gate: a field with nothing in it is the
    case the no-document-checklist branch exists for."""
    calls = monkey_patch()
    r = F.sync_to_template("t", "p", "W-1", "checklist",
                           {"status": "no-document-checklist", "template": SYNC_LIVE},
                           _tpl_payload(), True,
                           wi_type="REVIEW", review_type="REVIEW", live={})
    assert r["synced"] and len(calls) == 1


def test_sync_refuses_a_missing_template_payload(monkey_patch):
    """run_template_check returns no payload on every degraded branch; None
    must refuse, not raise."""
    calls = monkey_patch()
    r = F.sync_to_template("t", "p", "W-1", "checklist",
                           {"status": "outdated", "template": SYNC_LIVE}, None, True,
                           wi_type="REVIEW", review_type="REVIEW", live={})
    assert not r["synced"] and calls == []


def test_sync_dry_run_writes_nothing(monkey_patch):
    calls = monkey_patch()
    r = F.sync_to_template("t", "p", "W-1", "checklist",
                           {"status": "outdated", "template": SYNC_LIVE},
                           _tpl_payload(), True, wi_type="REVIEW", review_type="REVIEW", live={}, dry=True)
    assert r["synced"] is False and r["dry_run"] and calls == []


# --------------------------------------------------------------- main() paths
def _fake_token(sub):
    """A PAT-shaped token: my_user() decodes the middle segment as JSON."""
    import base64
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": sub}).encode()).rstrip(b"=").decode()
    return f"hdr.{payload}.sig"


def _run_main(rest, argv_extra=(), out_name="out.json", check_template=False):
    """Drive fetch_checklist.main() with a stubbed REST layer.

    Unit tests exercise the helpers in isolation; every defect that reached
    main() so far lived in the wiring between them."""
    import shutil, tempfile
    d = tempfile.mkdtemp()
    out = os.path.join(d, out_name)
    saved = (F.rest_get, F.load_env, sys.argv, F.REST,   # main() sets REST
             cfg.cache_dir)
    F.rest_get = rest
    F.load_env = lambda: ("https://example.invalid", _fake_token("tester"))
    # fetch_template caches what it fetched; without this the suite writes
    # fixture rows over the repo's real fallback baseline for the doctype.
    cfg.cache_dir = lambda *a, **k: d
    sys.argv = ["fetch_checklist.py", "--project", "bsp_module", "--space", "S",
                "--document", "D", "--review-wi", "RV-1",
                *([] if check_template else ["--no-template-check"]),
                "--out", out, *argv_extra]
    try:
        F.main()
        return json.load(io.open(out, encoding="utf-8")) if os.path.exists(out) else None
    finally:
        (F.rest_get, F.load_env, sys.argv, F.REST, cfg.cache_dir) = saved
        shutil.rmtree(d, ignore_errors=True)


def _rest_for(checklist_html, doc_attrs=None, fail_document=False):
    def rest(token, path, params=None):
        if "/documents/" in path:
            if fail_document:
                raise SystemExit("REST 404 on GET " + path)
            return {"data": {"attributes": doc_attrs}} if doc_attrs else None
        if "/workitems/" in path:
            return X.work_item("RV-1", "REVIEW", "tester", checklist_html, "bsp_module")
        return {"data": []}
    return rest


TPL_DOCTYPE = "ModuleArchitecturalDesignSpecification"


def _rest_for_sync(doc_rows, tpl_rows, doc_header=None, tpl_header=None,
                   doc_type="REVIEW", doc_raw=None):
    """REST stub serving BOTH the document's REVIEW item and the template's."""
    doc_html = X.table_html(doc_header or X.H7, doc_rows) if doc_rows is not None else None
    tpl_html = X.table_html(tpl_header or X.H7, tpl_rows)

    def rest(token, path, params=None):
        if "/documents/" in path:
            return {"data": {"attributes": {"type": TPL_DOCTYPE, "title": "Doc",
                                            "status": "draft"}}}
        if path.endswith("/workitems"):            # find_wis_in (template lookup)
            return {"data": [{"id": "Guide/GUID-1"}]} if "/Guide/" in path else {"data": []}
        if "GUID-1" in path:
            return X.work_item("GUID-1", "REVIEW", "anyone", tpl_html, "Guide")
        return X.work_item("RV-1", doc_type, "tester",
                           doc_raw if doc_raw is not None else doc_html, "bsp_module")
    return rest, tpl_html


def test_sync_refuses_a_target_that_is_not_the_review_type(monkey_patch):
    """--review-wi bypasses find_wis_in's type filter and is filled in by the
    Stage-1 agent, so a misidentified id reaches here. An Ins_defect the user
    is assigned to has no checklist field, which reads as "nothing to lose" --
    the one combination that would overwrite a defect with a review table."""
    calls = monkey_patch()
    rest, _ = _rest_for_sync(None, [X.row7(1)], doc_type="Ins_defect")
    got = _run_main(rest, argv_extra=["--sync-template"], check_template=True)
    assert not got["template_sync"]["synced"], got["template_sync"]
    assert "not 'REVIEW'" in got["template_sync"]["reason"]
    assert calls == [], f"a defect work item was overwritten: {calls}"


def test_sync_refuses_a_field_holding_markup_with_no_text(monkey_patch):
    """End-to-end, because the gate reads live_html out of the fetch payload:
    passing the markup to sync_to_template directly tests a value main() may
    not produce, and that is how this gate first shipped as dead code."""
    calls = monkey_patch()
    rest, _ = _rest_for_sync(None, [X.row7(1)], doc_raw='<p><img src="x"/></p>')
    got = _run_main(rest, argv_extra=["--sync-template"], check_template=True)
    assert not got["template_sync"]["synced"], got["template_sync"]
    assert "markup with no text" in got["template_sync"]["reason"]
    assert calls == [], f"an image-only checklist field was overwritten: {calls}"


def test_a_cleared_field_is_empty_whatever_the_editor_left_behind(monkey_patch):
    """Selecting the table and deleting it is the ordinary way a reviewer
    empties the field, and a rich-text editor leaves a husk behind. Refusing
    those would refuse the feature's headline case -- and Stage 4 would then
    die on 'checklist has no <th> header row'."""
    for husk in ("<p>&nbsp;</p>", "<p></p>", "<div><br/></div>"):
        calls = monkey_patch()
        rest, _ = _rest_for_sync(None, [X.row7(1)], doc_raw=husk)
        got = _run_main(rest, argv_extra=["--sync-template"], check_template=True)
        assert got["template_sync"]["synced"], (husk, got["template_sync"])
        assert len(calls) == 1, (husk, calls)


def test_sync_refuses_a_half_built_table_too(monkey_patch):
    """The other shape detag() reports as empty: a table skeleton with no
    text in any cell."""
    calls = monkey_patch()
    rest, _ = _rest_for_sync(None, [X.row7(1)],
                             doc_raw="<table><tr><td></td></tr></table>")
    got = _run_main(rest, argv_extra=["--sync-template"], check_template=True)
    assert not got["template_sync"]["synced"], got["template_sync"]
    assert calls == []


def test_sync_fills_a_genuinely_empty_field_end_to_end(monkey_patch):
    """The counterpart: the gate must not block the case it sits next to."""
    calls = monkey_patch()
    rest, tpl_html = _rest_for_sync(None, [X.row7(1), X.row7(2)])
    got = _run_main(rest, argv_extra=["--sync-template"], check_template=True)
    assert got["template_sync"]["synced"], got["template_sync"]
    assert calls[0][1]["data"]["attributes"]["checklist"]["value"] == tpl_html
    assert got["data_row_count"] == 2
    # the tier label has to follow the rows: they are in the REVIEW item now
    assert got["template_check"]["fallback"] == "none"
    assert "template_checklist" not in got["template_check"]


def _merged_cell_html():
    """A 7-column table whose second row omits the merged category cell, the
    way Polarion renders rowspan. _Table does not honour rowspan, so the row
    comes back one cell short."""
    first = X.table_html(X.H7, [X.row7(1)]).replace("</table>", "")
    rest = "".join(f"<td>{c}</td>" for c in X.row7(2)[1:])
    return first + "<tr>" + rest + "</tr></table>"


def test_a_checklist_of_several_tables_counts_data_rows_like_the_writer():
    """22 of 40 real ModuleDetailedDesignSpecification review items are two
    tables, so this is the normal shape for that doctype, not an edge case.
    write_checklist walks every <tr> WITHOUT a <th>; counting "all rows but
    the first" instead reported one item too many, handed the second header
    to the reviewer as a criterion, and killed Stage 4 with "row mismatch"."""
    for n_tables in (1, 2, 3, 5):
        html = "".join(X.table_html(X.H7, [X.row7(i * 2 + 1), X.row7(i * 2 + 2)])
                       for i in range(n_tables))
        st, p = F.extract_checklist(X.work_item(checklist=html), "checklist")
        assert st == "checklist-present"
        assert p["header_row_count"] == n_tables
        assert p["data_row_count"] == 2 * n_tables, (n_tables, p["data_row_count"])
        # no header row may reach the reviewer as a checklist item
        assert X.H7 not in p["template_rows"]
        # and the writer has to agree about the count
        tr, th = F.row_census(html)
        assert tr - th == p["data_row_count"]
        assert F.template_write_problem(p) is None, F.template_write_problem(p)


def test_validate_rejects_html_that_inject_would_never_produce():
    """validate() had only ever been handed inject's own output, which cannot
    violate it -- so every assertion in it passed mutation while two of them
    are the live guard against a silent wrong write on real data (8 real
    checklists are ragged, and the preserved-column rule is the stated safety
    property). Corrupt the output directly instead."""
    note = "<table><tr><td>※ keep the first verdict</td></tr></table>"
    html = note + X.table_html(X.H7, [X.row7(1), X.row7(2)])
    _, p = F.extract_checklist(X.work_item(checklist=html), "checklist")
    roles = W.resolve_layout(html, {"columns": p["columns"]})
    good, _ = W.inject(html, X.findings(p["template_rows"], roles)["results"], roles)
    W.validate(html, good, roles)          # the honest output still passes

    def rejects(label, broken):
        try:
            W.validate(html, broken, roles)
        except AssertionError:
            return
        raise AssertionError("validate accepted " + label)

    # a row dropped or added
    rejects("a dropped row", good.replace(
        W.split_rows(good)[-1].group(0), "", 1))
    # a preserved column (the criteria text) rewritten
    rejects("an edited template column",
            good.replace("criteria 1 (method) check", "something else", 1))
    # the human-owned defect-status column filled in
    last = W.split_rows(good)[-1].group(0)
    rejects("a filled defect-status cell",
            good.replace(last, last.replace("<td style=\"border: 1px solid #CCC;\"></td>",
                                            "<td>Closed</td>", 1), 1))
    # a cell removed from a data row -> no longer the header's width
    dm = W.split_rows(good)[2].group(0)
    rejects("a data row one cell short",
            good.replace(dm, re.sub(r"<td\b.*?</td>", "", dm, count=1,
                                    flags=re.S | re.I), 1))
    # the preamble note edited: not a checklist item, so byte-identical is the
    # rule -- the per-column one cannot see it
    rejects("an edited preamble row",
            good.replace("※ keep the first verdict", "※ edited", 1))
    # a cell ADDED to a data row: wider than the source, but the source is
    # still the header's width, so only the o/n comparison can see it
    dm2 = W.split_rows(good)[2].group(0)
    rejects("a data row one cell too wide",
            good.replace(dm2, dm2.replace("</tr>", "<td>x</td></tr>"), 1))


def test_validate_rejects_a_ragged_source_table():
    """Eight real checklists have a row that is not the header's width. That
    assertion is the only thing standing between them and a write whose cells
    land one column off, and it reads the SOURCE row -- so corrupting the
    output cannot exercise it."""
    short = X.row7(2)[:-1]                       # one cell short
    html = X.table_html(X.H7, [X.row7(1), short])
    _, p = F.extract_checklist(X.work_item(checklist=html), "checklist")
    roles = W.resolve_layout(html, {"columns": p["columns"]})
    new, _ = W.inject(html, X.findings([X.row7(1), X.row7(2)], roles)["results"], roles)
    try:
        W.validate(html, new, roles)
    except AssertionError as e:
        assert "layout not uniform" in str(e), e
        return
    raise AssertionError("validate accepted a ragged source table")


def test_a_template_with_a_preamble_note_is_still_writable():
    """The writer handles a note above the header, so the sync gate must not
    refuse it -- and the <tr> identity has to subtract preamble rows now that
    data_row_count excludes them, or it trips on every such template."""
    for note in ("<tr><td>※ keep the first verdict</td></tr>",
                 "<tr>" + "".join("<td>%s</td>" % c
                                  for c in ["※ note"] + [""] * 6) + "</tr>"):
        html = ("<table>" + note + "</table>"
                + X.table_html(X.H7, [X.row7(i) for i in (1, 2, 3)]))
        _, p = F.extract_checklist(X.work_item(checklist=html), "checklist")
        assert p["preamble_row_count"] == 1 and p["data_row_count"] == 3
        assert F.template_write_problem(p) is None, F.template_write_problem(p)
        # and the writer really does handle it
        roles = W.resolve_layout(html, {"columns": p["columns"]})
        new, _ = W.inject(html, X.findings(p["template_rows"], roles)["results"], roles)
        W.validate(html, new, roles)


def test_header_rows_are_compared_by_role_not_by_wording():
    """180 of 200 real two-table review items word their two headers
    differently (리뷰 결함 상태 vs 리뷰 결함 처리 상태) while meaning the same
    columns. Comparing header TEXT would unresolve the layout for 90% of the
    population this support exists for."""
    other = list(X.H7)
    other[6] = "리뷰 결함 처리 상태 및 결과 (🟡Open/⚫Closed)"
    other[4] = "판정 결과 (🟩Pass/🟥Fail) "          # emoji + trailing space
    assert other != X.H7
    html = X.table_html(X.H7, [X.row7(1)]) + X.table_html(other, [X.row7(2)])
    _, p = F.extract_checklist(X.work_item(checklist=html), "checklist")
    assert p["columns"]["roles"] is not None, p["columns"].get("error")
    assert p["data_row_count"] == 2 and p["header_row_count"] == 2


def wide_note_html():
    """The same note written full width instead of colspan'd."""
    return ("<table><tr>" + "".join("<td>%s</td>" % c for c in
            ["※ keep the first verdict"] + [""] * 6) + "</tr></table>"
            + X.table_html(X.H7, [X.row7(1), X.row7(2)]))


def test_a_note_row_above_the_header_is_not_a_checklist_item():
    """Four real checklists open with a colspan'd note above the table. Read
    as item 0 it spends a verdict and shifts every later row by one -- and
    with the note widened to full width nothing downstream would catch it."""
    note = '<table><tr><td>※ keep the first verdict</td></tr></table>'
    html = note + X.table_html(X.H7, [X.row7(1), X.row7(2)])
    _, p = F.extract_checklist(X.work_item(checklist=html), "checklist")
    assert p["preamble_row_count"] == 1
    assert p["data_row_count"] == 2, p["data_row_count"]
    assert p["template_rows"][0] == X.row7(1), p["template_rows"][0]
    assert p["columns"]["header"] == X.H7
    # and the writer must agree, or the counts diverge all over again.
    # Both widths: the 1-cell colspan is what real documents carry and is the
    # row the per-column rule used to reject ("row 0 has 1 cells, header has
    # 7"); the widened one is the shape where nothing downstream would catch
    # a misalignment.
    for src_html in (html, wide_note_html()):
        _, q = F.extract_checklist(X.work_item(checklist=src_html), "checklist")
        assert q["data_row_count"] == 2, (src_html[:40], q["data_row_count"])
        roles = W.resolve_layout(src_html, {"columns": q["columns"]})
        new, _ = W.inject(src_html, X.findings(q["template_rows"], roles)["results"], roles)
        W.validate(src_html, new, roles)
        first = W.cells_text(W.split_rows(new)[0].group(0))
        assert first[0].startswith("※"), first
        assert not any(c.strip() for c in first[1:]), first


def test_lost_update_counts_data_rows_the_way_the_fetch_did():
    """The lost-update check had its own copy of "which rows are data", and a
    copy that disagrees reports drift that is not there: the operator is told
    someone edited the checklist mid-review, which is false."""
    note = "<table><tr><td>※ keep the first verdict</td></tr></table>"
    html = note + X.table_html(X.H7, [X.row7(1), X.row7(2)])
    _, p = F.extract_checklist(X.work_item(checklist=html), "checklist")
    roles = W.resolve_layout(html, {"columns": p["columns"]})
    # no drift: the same checklist, unchanged
    W.lost_update_check({"template_rows": p["template_rows"]},
                        W.split_rows(html), roles)


def test_write_refuses_when_the_fetch_could_not_resolve_the_roles():
    """columns.roles is null when the header was unreadable or the tables
    disagree -- the fetch saying no index describes this table. Resolving
    from the live header anyway writes by an index it had just refused, into
    whichever table lost. doc-review.md promises a refusal."""
    html = X.table_html(X.H7, [X.row7(1)])
    filled = {"columns": {"header": X.H7, "roles": None, "error": "headers disagree"}}
    try:
        W.resolve_layout(html, filled)
        assert False, "resolve_layout wrote by an index the fetch refused"
    except SystemExit as e:
        assert "could not resolve column roles" in str(e), e
        assert "headers disagree" in str(e), e
    # a findings file with no columns block at all is a pre-layout-aware run,
    # not a refusal: it warns and uses the live header
    assert W.resolve_layout(html, {})["ncols"] == 7


def test_header_rows_that_disagree_leave_the_layout_unresolved():
    """Several tables are fine while they share a layout. When they do not,
    no single index map describes the table, and writing by index would put
    comments in the result column of whichever table lost."""
    html = (X.table_html(X.H7, [X.row7(1)]) + X.table_html(X.H6, [X.row6(2)]))
    _, p = F.extract_checklist(X.work_item(checklist=html), "checklist")
    assert p["columns"]["roles"] is None
    assert "do not agree" in p["columns"]["error"], p["columns"]


def test_a_header_of_bold_cells_is_not_a_header():
    """_Table cannot tell a bold <td> from data, but write_checklist needs a
    real <th> -- resolve_layout exits without one."""
    html = (X.table_html(X.H7, [X.row7(1)])
            .replace("<th", "<td").replace("</th>", "</td>"))
    _, p = F.extract_checklist(X.work_item(checklist=html), "checklist")
    assert p["columns"]["roles"] is None and "no <th>" in p["columns"]["error"]
    assert F.template_write_problem(p) == "it has no <th> header row"


def test_sync_refuses_a_malformed_template(monkey_patch):
    """The structural check has to cover the table being WRITTEN, not only
    the one being destroyed. A merged cell in the template fabricates a diff
    against a correct checklist and then leaves a table Stage 4 refuses as
    "layout not uniform"; a blank spacer row makes data_row_count short of
    the <tr>s actually written, and Stage 4 aborts with "more data rows than
    filled entries". Either way the live checklist is already gone."""
    for label, tpl_html in (
            ("cell count", _merged_cell_html()),
            ("blank spacer rows",
             X.table_html(X.H7, [X.row7(1), ["", "", "", "", "", "", ""],
                                 X.row7(2)])),
            # a <tr> the census sees and the parser does not
            ("do not match",
             X.table_html(X.H7, [X.row7(2)]).replace(
                 "</table>", "<!-- <tr><td>x</td></tr> --></table>"))):
        calls = monkey_patch()
        base, _ = _rest_for_sync([X.row7(1)], [X.row7(1)])

        def rest(token, path, params=None, _t=tpl_html):
            if "GUID-1" in path:
                return X.work_item("GUID-1", "REVIEW", "anyone", _t, "Guide")
            return base(token, path, params)
        got = _run_main(rest, argv_extra=["--sync-template"], check_template=True)
        assert got["template_check"]["status"] == "outdated", (label, got["template_check"])
        assert not got["template_sync"]["synced"], (label, got["template_sync"])
        assert label in got["template_sync"]["reason"], (label, got["template_sync"])
        assert calls == [], (label, calls)


def test_sync_replaces_a_table_with_merged_cells(monkey_patch):
    """A checklist whose rows do not line up is a checklist to replace, not
    one to protect: write_checklist refuses it outright, so leaving it alone
    leaves the document unreviewable. The guard that matters is on the table
    being written, which is well-formed here."""
    calls = monkey_patch()
    rest, tpl_html = _rest_for_sync(None, [X.row7(1), X.row7(2), X.row7(3)],
                                    doc_raw=_merged_cell_html())
    got = _run_main(rest, argv_extra=["--sync-template"], check_template=True)
    assert got["template_sync"]["synced"], got["template_sync"]
    assert calls[0][1]["data"]["attributes"]["checklist"]["value"] == tpl_html
    assert got["data_row_count"] == 3


def test_sync_keeps_what_it_replaced_and_says_what_was_discarded(monkey_patch):
    """The PATCH cannot be undone from here and nothing downstream rewrites
    the defect-status column, so the run has to carry the replaced table and
    count what went with it."""
    calls = monkey_patch()
    doc = [X.row7(1, defect="Open"), X.row7(2, defect="Closed")]
    doc_html = X.table_html(X.H7, doc)
    rest, _ = _rest_for_sync(doc, [X.row7(1), X.row7(2), X.row7(3)])
    got = _run_main(rest, argv_extra=["--sync-template"], check_template=True)
    assert got["template_sync"]["synced"] and len(calls) == 1
    assert got["template_sync"]["discarded_defect_status"] == 2, got["template_sync"]
    assert got["template_sync"]["discarded_rows"] == 2
    # the replaced table, not the one just written
    assert got["replaced_checklist_html"] == doc_html
    assert got["replaced_checklist_html"] != got["checklist_html"]


def test_sync_refuses_a_template_that_is_not_a_checklist(monkey_patch):
    """Nothing parses the template on the no-document-checklist branch, so a
    template holding a sentence would be written in, reported as a governed
    0-item checklist with the tier-2 fallback suppressed, and Stage 4 would
    then die on the missing header row."""
    calls = monkey_patch()
    rest, _ = _rest_for_sync(None, [X.row7(1)], doc_raw="")
    def prose(token, path, params=None):
        if "GUID-1" in path:
            return X.work_item("GUID-1", "REVIEW", "anyone",
                               "<p>See the attached spreadsheet.</p>", "Guide")
        return rest(token, path, params)
    got = _run_main(prose, argv_extra=["--sync-template"], check_template=True)
    assert not got["template_sync"]["synced"], got["template_sync"]
    assert "did not parse as a checklist" in got["template_sync"]["reason"]
    assert calls == []


def test_sync_refuses_when_the_template_document_has_several_review_items(monkey_patch):
    """sorted(wis)[0] is a stable baseline for a comparison but not a safe
    choice of bytes to write: another could be a reviewer's filled copy."""
    calls = monkey_patch()
    rest, _ = _rest_for_sync(None, [X.row7(1)], doc_raw="")
    def two(token, path, params=None):
        if path.endswith("/workitems") and "/Guide/" in path:
            return {"data": [{"id": "Guide/GUID-1"}, {"id": "Guide/GUID-2"}]}
        return rest(token, path, params)
    got = _run_main(two, argv_extra=["--sync-template"], check_template=True)
    assert not got["template_sync"]["synced"], got["template_sync"]
    assert "refusing to guess" in got["template_sync"]["reason"]
    assert calls == []


def test_a_polarion_macro_is_content_not_a_husk(monkey_patch):
    """An allowlist of content tags cannot be complete; this is a div, which
    IS a husk tag -- what makes it content is the non-presentational
    attribute Polarion identifies the embedded work item by."""
    calls = monkey_patch()
    rest, _ = _rest_for_sync(None, [X.row7(1)],
                             doc_raw='<div id="polarion_wiki macro name=x"></div>')
    got = _run_main(rest, argv_extra=["--sync-template"], check_template=True)
    assert not got["template_sync"]["synced"], got["template_sync"]
    assert calls == []


def test_husk_detection_reads_markup_instead_of_scanning_it():
    """A raw pattern scan reports a tag name written inside a comment; the
    parser does not, so that case no longer refuses a cleared field. The
    allowlist it replaces also had to name every content tag -- image, input,
    picture, a Polarion macro div -- and could not."""
    for empty in ("<p>&nbsp;</p>", "<p></p>", "<div><br/></div>", "",
                  "   ", "<!-- <table> -->", "<p style='margin:0'></p>",
                  "<div class='x'><span></span><br/></div>"):
        assert F.is_husk(empty), empty
    for content in ('<p><img src="x"/></p>', "<table><tr><td></td></tr></table>",
                    "<p><IMG src=x></p>", "<p><image src=x/></p>",
                    "<picture><source srcset=x></picture>",
                    '<p><input type="checkbox"/></p>',
                    '<p><a href="/attach/spec.xlsx"></a></p>',
                    '<div id="polarion_wiki macro name=x"></div>',
                    # a non-presentational attribute on a husk tag is content:
                    # it is there to carry something, and refusing is the safe
                    # side of a case too rare to decode
                    '<p title="see <table">&nbsp;</p>',
                    "text"):
        assert not F.is_husk(content), content


def test_the_suite_never_writes_the_checkout_template_cache(monkey_patch):
    """fetch_template caches what it fetched, and these tests feed it fixture
    rows. Without the redirect in _run_main the suite silently replaces the
    stored fallback baseline for a real doctype -- which a degraded run then
    reports as a wholesale mismatch against a correct checklist."""
    real = cfg.cache_dir("telechips-bsp")
    monkey_patch()
    base, _ = _rest_for_sync([X.row7(1)], [X.row7(1), X.row7(2)])
    seen = {}

    def rest(token, path, params=None):
        # sampled DURING main(), while _run_main's redirect is in force --
        # comparing the directory listing before and after would pass once an
        # earlier test in the same run had already created the file
        seen["dir"] = cfg.cache_dir("telechips-bsp")
        return base(token, path, params)

    got = _run_main(rest, argv_extra=["--sync-template"], check_template=True)
    assert got["template_sync"]["synced"]       # the cache write really ran
    assert seen["dir"] != real, f"fetch_template cached into the checkout: {real}"


def test_sync_replaces_the_column_roles_too(monkey_patch):
    """The headline case: a document still on the 6-column layout. Its roles
    map (result=3, comment=4) is one short of the template's (result=4,
    comment=5) -- keeping it would make write_checklist put every comment in
    the result column of the table the sync just wrote."""
    monkey_patch()
    rest, _ = _rest_for_sync([X.row6(1), X.row6(2)], [X.row7(1), X.row7(2)],
                             doc_header=X.H6, tpl_header=X.H7)
    got = _run_main(rest, argv_extra=["--sync-template"], check_template=True)
    assert got["template_sync"]["synced"], got["template_sync"]
    roles = got["columns"]["roles"]
    assert got["columns"]["header"] == X.H7, got["columns"]["header"]
    assert roles["ncols"] == 7 and roles["result"] == 4 and roles["comment"] == 5, roles


def test_sync_makes_the_template_the_checklist_under_review(monkey_patch):
    """End-to-end: the whole point is that the REVIEW that follows runs on the
    template's rows, not the superseded ones. Asserting only that a PATCH went
    out would pass even if the output still carried the old checklist."""
    calls = monkey_patch()
    rest, tpl_html = _rest_for_sync([X.row7(1)], [X.row7(1), X.row7(2), X.row7(3)])
    got = _run_main(rest, argv_extra=["--sync-template"], check_template=True)
    assert got["template_sync"]["synced"], got.get("template_sync")
    assert len(calls) == 1
    assert calls[0][1]["data"]["attributes"]["checklist"]["value"] == tpl_html
    # the output the reviewing agent consumes is now the template's table
    assert got["data_row_count"] == 3, got["data_row_count"]
    assert got["template_rows"] == [X.row7(1), X.row7(2), X.row7(3)]
    assert got["status"] == "checklist-present"


def test_sync_is_off_unless_asked(monkey_patch):
    calls = monkey_patch()
    rest, _ = _rest_for_sync([X.row7(1)], [X.row7(1), X.row7(2)])
    got = _run_main(rest, check_template=True)
    assert "template_sync" not in got and calls == []


def test_sync_leaves_a_current_checklist_alone(monkey_patch):
    calls = monkey_patch()
    rows = [X.row7(1), X.row7(2)]
    rest, _ = _rest_for_sync(rows, rows)
    got = _run_main(rest, argv_extra=["--sync-template"], check_template=True)
    assert got["template_check"]["status"] == "current"
    assert not got["template_sync"]["synced"] and calls == []
    assert got["data_row_count"] == 2


def test_internal_template_payload_never_reaches_the_output(monkey_patch):
    """_tpl is passed through template_check in-memory; serializing it would
    double the file and leak the template's raw HTML into every findings JSON."""
    monkey_patch()
    rest, _ = _rest_for_sync([X.row7(1)], [X.row7(1), X.row7(2)])
    for extra in ([], ["--sync-template"]):
        got = _run_main(rest, argv_extra=extra, check_template=True)
        assert "_tpl" not in got["template_check"], extra


def test_main_tests_leave_no_module_state_behind():
    """main() assigns the REST global; a leaked value would let a later test
    build a real URL and reach the network the suite promises not to touch."""
    before = F.REST
    _run_main(_rest_for(X.table_html(X.H7, [X.row7(1)])))
    assert F.REST == before, f"F.REST leaked: {F.REST}"


def test_main_writes_output_when_metadata_cannot_be_read():
    """The degrade path must still produce --out; it once died on None.get."""
    html = X.table_html(X.H7, [X.row7(i) for i in range(1, 4)])
    got = _run_main(_rest_for(html, fail_document=True))
    assert got is not None, "the run ended without writing --out"
    assert got["status"] == "checklist-present" and got["data_row_count"] == 3
    assert got["document"]["title"] == "" and got["document"]["doctype"] == ""


def test_main_writes_output_when_the_document_has_no_payload():
    html = X.table_html(X.H7, [X.row7(1)])
    got = _run_main(_rest_for(html, doc_attrs=None))
    assert got is not None and got["document"]["title"] == ""


def test_main_uses_the_document_type_and_title():
    html = X.table_html(X.H6, [X.row6(i) for i in range(1, 3)])
    got = _run_main(_rest_for(html, doc_attrs={"type": "T4", "title": "Doc", "status": "draft"}))
    assert got["document"]["doctype"] == "T4"
    assert got["document"]["title"] == "Doc" and got["document"]["status"] == "draft"
    assert got["columns"]["roles"]["defect_status"] == 5


# ---------------------------------------------------------------- the  runner
def _tmp_module_factory(cleanup):
    import shutil, tempfile
    d = tempfile.mkdtemp()
    cleanup.append(lambda: shutil.rmtree(d, ignore_errors=True))

    def make(src):
        fd, path = tempfile.mkstemp(suffix=".py", dir=d, text=True)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(src)
        return path
    return make


def _monkey_rest_get_factory(restore):
    def install(payload):
        restore.append((F, "rest_get", F.rest_get))
        F.rest_get = lambda *a, **k: payload
    return install


def _monkey_fetch_doc_factory(restore):
    def install(outcome):
        def fake(*a, **k):
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        restore.append((F, "fetch_document", F.fetch_document))
        F.fetch_document = fake
    return install


def _monkey_find_wis_factory(restore):
    def install(outcome):
        def fake(*a, **k):
            raise outcome
        restore.append((F, "find_wis_in", F.find_wis_in))
        F.find_wis_in = fake
    return install


def _monkey_patch_factory(restore):
    """Capture PATCH calls instead of issuing them: the sync's only side effect.

    Returns the list of (url, body) so a test can assert BOTH that a write
    happened and what it carried -- and, for the refusal tests, that no gate
    leaked one."""
    def install():
        import polarion_rest as PR
        calls = []
        restore.append((PR, "patch", PR.patch))
        restore.append((F, "REST", F.REST))   # main() sets it; a unit test must too
        F.REST = "https://example.invalid/polarion/rest/v1"
        PR.patch = lambda token, url, body: (calls.append((url, body)), {"ok": 1})[1]
        return calls
    return install


# --------------------------------------------------- run.sh's Stage-1 gate
def _gate_src():
    """The gate is a python heredoc inside run.sh; extract it so the suite can
    exercise it. Nothing else in tests/ shells out, and with no coverage the
    gate's conditions could all be inverted with the suite still green."""
    sh = io.open(os.path.join(ROOT, "projects", "reviewer", "schedule", "run.sh"),
                 encoding="utf-8").read()
    start = sh.index('python3 - "$CHECKLIST" "$DRYRUN"')
    body = sh[sh.index("\n", start) + 1:]
    return body[:body.index("\nEOF\n")]


def _run_gate(payload, dry="0"):
    """(exit code, output) of the gate for one fetch payload."""
    import subprocess, tempfile, shutil
    d = tempfile.mkdtemp()
    try:
        g = os.path.join(d, "gate.py")
        io.open(g, "w", encoding="utf-8").write(_gate_src())
        c = os.path.join(d, "c.json")
        with io.open(c, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        r = subprocess.run([sys.executable, g, c, dry,
                            os.path.join(ROOT, "skills")],
                           capture_output=True, text=True)
        return r.returncode, (r.stdout + r.stderr)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _healthy_payload():
    html = X.table_html(X.H7, [X.row7(1), X.row7(2)])
    _, p = F.extract_checklist(X.work_item(checklist=html), "checklist")
    p["status"] = "checklist-present"
    return p


def test_fail_count_counts_the_whole_table_either_token():
    """The field has to agree with what a reader sees, so it counts every data
    row -- including ones this run skipped because a human owned them -- and
    tolerates the emoji the column header names."""
    rows = [X.row7(1, result="🟥Fail"), X.row7(2, result="Fail"),
            X.row7(3, result="🟩Pass"), X.row7(4, result="N/A"),
            X.row7(5, result="", defect="🟡Open")]      # no verdict: not a fail
    html = X.table_html(X.H7, rows)
    _, p = F.extract_checklist(X.work_item(checklist=html), "checklist")
    assert W.count_fails(html, p["columns"]["roles"]) == 2
    # Header and preamble rows are never counted -- not even one whose cell at
    # the result index literally reads 🟥Fail, which is the only shape that can
    # tell "data rows only" apart from "every <tr>".
    note = ("<table><tr>" + "".join("<td>%s</td>" % c for c in
            ["※ note", "", "", "", "🟥Fail", "", ""]) + "</tr></table>")
    html2 = note + X.table_html(X.H7, rows) + X.table_html(X.H7, [X.row7(6, result="🟥Fail")])
    _, q = F.extract_checklist(X.work_item(checklist=html2), "checklist")
    assert q["preamble_row_count"] == 1 and q["header_row_count"] == 2
    assert W.count_fails(html2, q["columns"]["roles"]) == 3


def test_fail_count_rides_in_the_checklist_patch():
    """One request, so the count can never describe a table that was not
    written -- and nothing is sent when the profile names no field."""
    sent = {}
    import polarion_rest as PR
    orig, orig_rest = PR.patch, W.REST
    W.REST = "https://example.invalid/polarion/rest/v1"   # main() sets it
    PR.patch = lambda t, u, b: sent.setdefault("body", b) or 204
    try:
        W.patch_checklist("t", "p", "W-1", "<table/>", "checklist", "fail", 7)
        a = sent["body"]["data"]["attributes"]
        assert a["fail"] == 7 and a["checklist"]["value"] == "<table/>"
        sent.clear()
        W.patch_checklist("t", "p", "W-1", "<table/>", "checklist", None, 7)
        assert "fail" not in sent["body"]["data"]["attributes"]
        sent.clear()
        W.patch_checklist("t", "p", "W-1", "<table/>", "checklist", "fail", None)
        assert "fail" not in sent["body"]["data"]["attributes"]
    finally:
        PR.patch, W.REST = orig, orig_rest


def test_profile_names_the_fail_count_field():
    assert cfg.fail_count_field(cfg.profile_for(project_id="bsp_module", quiet=True)) == "fail"
    assert cfg.fail_count_field({}) is None


def test_gate_lets_a_writable_checklist_through():
    """A false abort costs a seat and an AI stage for nothing, so this is the
    half that matters most."""
    rc, out = _run_gate(_healthy_payload())
    assert rc == 0, out


def test_gate_stops_every_shape_stage_4_cannot_write():
    good = _healthy_payload()
    ragged = dict(good, template_rows=[X.row7(1), X.row7(2)[:-1]])
    spacer = dict(good, dropped_blank_rows=1)
    noroles = {"status": "checklist-present", "rows": [X.H7],
               "columns": {"roles": None, "error": "no <th> header row"}}
    # a checklist pasted as plain text parses to no rows at all -- the first
    # version of this gate required rows to be non-empty and let it through
    pasted = {"status": "checklist-present", "rows": [],
              "columns": {"roles": None, "error": "no <th> header row"}}
    for label, payload, expect in (
            ("no review work item", {"status": "no-review-wi"}, "nowhere to write"),
            ("empty checklist", {"status": "review-wi-no-checklist"},
             "no checklist to fill"),
            ("sync reason quoted",
             {"status": "review-wi-no-checklist",
              "template_sync": {"reason": "the current user does not own this"}},
             "does not own"),
            ("unresolved header", noroles, "could not be resolved"),
            ("pasted as plain text", pasted, "could not be resolved"),
            ("ragged rows", ragged, "header's cell count"),
            ("blank spacer row", spacer, "blank spacer rows")):
        rc, out = _run_gate(payload)
        assert rc == 1, (label, rc, out)
        assert expect in out, (label, out)


def test_gate_runs_stage_4s_own_layout_check():
    """Two real checklists nest the note INSIDE the header row, so the writer
    reads '※ ... 분류' as cell 0 and resolves a different layout from the one
    the fetch recorded. Only the writer's parser can see that, so the gate
    calls it rather than re-deriving the answer."""
    # the real shape: a note and the checklist share one cell of an outer
    # table, so the writer's <tr>...</tr> match starts at the outer row
    inner = X.table_html(X.H7, [X.row7(1), X.row7(2)])
    # the real note text, which collides with the 판정 ('result') keyword --
    # that collision is what makes the writer resolve a different layout
    html = ("<table><tr><td>※ 판정결과는 결함 해결 여부와 관계없이 최초 "
            "판정결과를 유지한다." + inner + "</td></tr></table>")
    _, p = F.extract_checklist(X.work_item(checklist=html), "checklist")
    p["status"] = "checklist-present"
    assert (p["columns"] or {})["roles"], "the fetch resolves it; the writer must not"
    assert W.header_cells(html) != p["columns"]["header"], "shape did not reproduce"
    assert F.template_write_problem(p) is None, "a different gate caught it"
    rc, out = _run_gate(p)
    assert rc == 1, out
    assert "Stage 4 would refuse" in out, out


def test_gate_stops_when_the_user_does_not_own_the_review_item():
    """619 of 815 real items die in Stage 4 on ownership, and the fetch has
    already recorded it -- with a comment saying the write will be rejected.
    Reading everything else out of that file and not this one key is the
    whole cost the gate exists to avoid."""
    p = _healthy_payload()
    p.update(source_wi="BSPM-1", source_wi_reviewer_matched=False)
    rc, out = _run_gate(p)
    assert rc == 1, out
    assert "not an assigned reviewer" in out and "BSPM-1" in out, out
    # and it must not fire when the user DOES own it
    p["source_wi_reviewer_matched"] = True
    rc, out = _run_gate(p)
    assert rc == 0, out


def test_gate_does_not_claim_a_dry_run_would_have_stopped():
    """sync_to_template clears every real gate before its dry-run return, so
    reason '--dry-run' is reached only when a real run would have synced and
    carried on. Saying "a real run would stop here" is then the opposite of
    the truth, and --no-write the wrong remedy."""
    rc, out = _run_gate({"status": "review-wi-no-checklist",
                         "template_sync": {"dry_run": True, "reason": "--dry-run"}},
                        dry="1")
    assert rc == 0, out
    assert "a real run would stop here" not in out, out
    assert "would have written it into the checklist" in out, out
    # a sync refused for a real reason still warns that a real run stops
    rc, out = _run_gate({"status": "review-wi-no-checklist",
                         "template_sync": {"reason": "the current user does not own this"}},
                        dry="1")
    assert rc == 0 and "a real run would stop here" in out, out


def test_gate_resolves_roles_with_the_profile_rules():
    """Every other role resolution in the pipeline uses the profile's
    keywords; falling back to the built-in defaults makes the gate answer for
    a different checklist dialect than the one Stage 4 will read."""
    import inspect
    src = _gate_src()
    assert "column_rules" in src, "the gate resolves with the built-in defaults"
    assert inspect.signature(W.resolve_layout).parameters.get("rules") is not None


def test_gate_says_nothing_on_a_healthy_run():
    """resolve_layout prints 'layout OK: ...' and the gate runs it at Stage 1.
    Without the stdout redirect every run would carry that line twice, from a
    check that is supposed to be silent when it passes."""
    rc, out = _run_gate(_healthy_payload())
    assert rc == 0 and out.strip() == "", repr(out)


def test_gate_passes_when_there_is_no_fetch_output_to_judge():
    """The fetch output is written by the Stage-1 agent; a run where it is
    missing or truncated still has the results gate and Stage 4 behind it,
    and must not be killed by the advisory one."""
    import subprocess, tempfile, shutil
    d = tempfile.mkdtemp()
    try:
        g = os.path.join(d, "gate.py")
        io.open(g, "w", encoding="utf-8").write(_gate_src())
        for label, content in (("missing", None), ("truncated", '{"status":'),
                               ("empty", "")):
            c = os.path.join(d, "c.json")
            if content is None:
                c = os.path.join(d, "nope.json")
            else:
                io.open(c, "w", encoding="utf-8").write(content)
            r = subprocess.run([sys.executable, g, c, "0",
                                os.path.join(ROOT, "skills")],
                               capture_output=True, text=True)
            assert r.returncode == 0, (label, r.stdout + r.stderr)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_gate_only_warns_under_dry_run():
    """A preview's product IS the report; aborting here would destroy the one
    thing a dry run exists to make."""
    rc, out = _run_gate({"status": "review-wi-no-checklist"}, dry="1")
    assert rc == 0, out
    assert "a real run would stop here" in out, out


def run(tests, verbose=False, quiet=False):
    """Run (name, fn) pairs; return (passed, failures). Never raises."""
    passed, failed = 0, []
    for name, fn in tests:
        restore, cleanup = [], []
        try:
            params = fn.__code__.co_varnames[:fn.__code__.co_argcount]
            if "monkey_find_wis" in params:
                fn(_monkey_find_wis_factory(restore))
            elif "monkey_rest_get" in params:
                fn(_monkey_rest_get_factory(restore))
            elif "monkey_fetch_doc" in params:
                fn(_monkey_fetch_doc_factory(restore))
            elif "monkey_patch" in params:
                fn(_monkey_patch_factory(restore))
            elif "tmp_module" in params:
                fn(_tmp_module_factory(cleanup))
            else:
                fn()
            passed += 1
            if verbose:
                print(f"  ok   {name}")
        except (Exception, SystemExit):
            # SystemExit is how every guard in this pipeline aborts, and it is
            # not an Exception. KeyboardInterrupt is deliberately NOT caught:
            # Ctrl-C must stop the run, not fail 44 tests one at a time.
            failed.append((name, traceback.format_exc()))
            if not quiet:
                print(f"  FAIL {name}")
        finally:
            for obj, attr, orig in reversed(restore):
                setattr(obj, attr, orig)
            for undo in reversed(cleanup):
                undo()
    return passed, failed


def main():
    verbose = "-v" in sys.argv
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed, failed = run(tests, verbose)
    print(f"\n{passed} passed, {len(failed)} failed, {len(tests)} total")
    for name, tb in failed:
        print(f"\n--- {name}\n{tb}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
