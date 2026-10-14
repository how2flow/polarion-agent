#!/usr/bin/env python3
"""write_checklist.py — write a filled review checklist back to its Polarion
REVIEW work item, by SURGICALLY injecting the reviewer values into the original
`checklist` HTML (preserving everything else). Deterministic plumbing; the AI
only supplies the cell values (in the filled findings JSON).

What it fills: per data row, the reviewer columns applicable(O/X) / result(Pass/Fail)
/ comment. Their indices are NOT hardcoded — the checklist layout is
doctype-specific (7 columns with a category, 6 without), so every column's role is
resolved from the header row via scripts/checklist_columns.py. Template columns
and defect-status (human-owned) are never touched.

Safety:
  (#6 type) the target must be a REVIEW work item (--review-type, default
       REVIEW); any other type aborts. This script writes nothing else.
  (#0 layout) column roles are resolved from the LIVE header at write time and
       compared against the roles recorded at fetch time; an unresolvable header
       or any role that moved aborts the write (never falls back to a guess).
  (#2 lost-update) re-fetches the work item right before writing and verifies the
       template columns still match `template_rows` (verbatim text captured at
       fetch time); aborts on drift.
  (#3 structural) after injection, re-parses and asserts same row/col count and
       that template + defect-status columns are byte-equal to the source; only
       the three reviewer columns differ.
  (#4) defect-status column is never written.
  (#5 human-reviewed guard) a data row whose defect-status (open/closed) OR
       result (Pass/Fail) is already filled was reviewed by a human — that row is
       left ENTIRELY untouched (its applicable/result/comment are never
       overwritten). To re-review such a row, clear those cells in Polarion.

Auth: POLARION_URL + POLARION_TOKEN (env or <repo>/.env).

Usage:
  write_checklist.py --project P --review-wi ID --filled filled.json [--dry-run]
  write_checklist.py --from-file wi_dump.json --filled filled.json --dry-run   # offline test
"""
import argparse, base64, json, os, re, sys, html
# polarion_rest is shared infra in <repo>/scripts — add it to the import path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
import polarion_rest  # shared REST helper with ALM-seat-limit retry
from polarion_rest import seg
import checklist_columns  # header-driven column roles (layout varies by doctype)
import polarion_config as cfg  # instance/org profile

REST = None
# Column indices are resolved per checklist from its header row — see
# scripts/checklist_columns.py. Nothing about the layout is hardcoded here.
#
# defect-status (open/closed) is human-owned and the review never writes it. A
# data row where that column — or the result column — is already filled means a
# human already reviewed the row, so it is skipped entirely.
#
# lost-update compares the live template columns against `template_rows`, the
# verbatim rows captured by fetch_checklist.py. (It must NOT compare against
# results[], whose text the AI may paraphrase — that yields false drift.)
SPAN = ("<span style=\"font-size: 10pt;font-family: &#39;Segoe UI&#39;, Selawik, "
        "&#39;Open Sans&#39;, sans-serif;line-height: 1.5;\">{}</span>")


def load_env():
    url = os.environ.get("POLARION_URL"); tok = os.environ.get("POLARION_TOKEN")
    if not (url and tok):
        here = os.path.dirname(os.path.abspath(__file__)); root = here
        while root != "/" and not os.path.exists(os.path.join(root, ".polarion-root")):
            root = os.path.dirname(root)
        envf = os.path.join(root, ".env")
        if os.path.exists(envf):
            for line in open(envf):
                if line.startswith("POLARION_URL="): url = line.strip().split("=", 1)[1]
                elif line.startswith("POLARION_TOKEN="): tok = line.strip().split("=", 1)[1]
    if not (url and tok): sys.exit("POLARION_URL / POLARION_TOKEN not set")
    return url.rstrip("/").removesuffix("/polarion"), tok


def get_my_user():
    """Current user = the `sub` claim of the Polarion PAT (env, .env, or .token)."""
    tok = os.environ.get("POLARION_TOKEN")
    if not tok:
        here = os.path.dirname(os.path.abspath(__file__)); root = here
        while root != "/" and not os.path.exists(os.path.join(root, ".polarion-root")):
            root = os.path.dirname(root)
        for cand in (os.path.join(root, ".env"), os.path.join(root, ".token")):
            if os.path.exists(cand):
                txt = open(cand).read()
                if "POLARION_TOKEN=" in txt:
                    tok = txt.split("POLARION_TOKEN=", 1)[1].splitlines()[0].strip()
                elif cand.endswith(".token"):
                    tok = txt.strip()
                if tok:
                    break
    if not tok:
        sys.exit("cannot determine current user: no token")
    p = tok.split(".")[1]; p += "=" * (-len(p) % 4)
    return json.loads(base64.urlsafe_b64decode(p)).get("sub")


def wi_type(wi):
    """The work item's type id (e.g. 'REVIEW')."""
    return (wi.get("data", {}).get("attributes", {}) or {}).get("type", "")


def wi_owners(wi):
    """Set of assigned reviewer + assignee user ids on the work item."""
    rel = wi.get("data", {}).get("relationships", {})
    ids = set()
    for k in ("reviewer", "assignee"):
        data = rel.get(k, {}).get("data")
        if isinstance(data, dict):
            ids.add(data.get("id"))
        elif isinstance(data, list):
            for x in data:
                ids.add(x.get("id"))
    ids.discard(None)
    return ids


def fetch_wi(token, project, wi_id):
    return polarion_rest.get(
        token, REST + f"/projects/{seg(project)}/workitems/{seg(wi_id)}?fields%5Bworkitems%5D=%40all")


def count_fails(htmlstr, roles):
    """Failed items in this table -- every data row whose verdict reads 'Fail'.

    Counts the WHOLE table, not just the rows this run wrote: a human-reviewed
    row left untouched is still a failed item, and the field has to agree with
    what a reader sees. Tolerates the emoji the column header names (🟥Fail).
    """
    i = roles["result"]
    return sum(1 for m in data_row_matches(split_rows(htmlstr))
               for c in [cells_text(m.group(0))]
               if len(c) > i and re.sub(r"[^A-Za-z]", "", c[i] or "").lower() == "fail")


def patch_checklist(token, project, wi_id, new_html, field="checklist",
                    fail_field=None, fails=None):
    """PATCH the checklist, and the failed-item count in the same request.

    One request, so the count can never describe a table that was not written.
    """
    attrs = {field: {"type": "text/html", "value": new_html}}
    if fail_field and fails is not None:
        attrs[fail_field] = fails
    body = {"data": {"type": "workitems", "id": f"{project}/{wi_id}",
                     "attributes": attrs}}
    return polarion_rest.patch(token, REST + f"/projects/{seg(project)}/workitems/{seg(wi_id)}", body)


def cells_text(tr_html):
    """Return list of plain-text cell contents for a <tr> (td or th).

    Tags collapse to a SPACE, not to nothing, so that '<p>a</p><p>b</p>' reads
    as 'a b' — the same way fetch_checklist.py's HTMLParser joins text chunks.
    (Comparisons against fetched text additionally go through
    checklist_columns.same_text, which ignores whitespace entirely.)"""
    out = []
    for m in re.finditer(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", tr_html, flags=re.S | re.I):
        txt = re.sub(r"<[^>]+>", " ", m.group(1))
        out.append(html.unescape(re.sub(r"\s+", " ", txt)).strip())
    return out


def split_rows(htmlstr):
    return list(re.finditer(r"<tr\b.*?</tr>", htmlstr, flags=re.S | re.I))


def is_header(tr_html):
    return bool(re.search(r"<th\b", tr_html, flags=re.I))


def data_row_indices(matches):
    """Indices of the <tr>s that are checklist items: every row after the
    first header row that does not itself carry a <th>.

    One definition, used by inject, the lost-update check and validate alike
    -- disagreeing copies of this predicate are what put fetch and the writer
    out of step. Rows before the first header are preamble (four real
    checklists open with a colspan'd note above the table), not items."""
    out, seen = set(), False
    for i, m in enumerate(matches):
        if is_header(m.group(0)):
            seen = True
        elif seen:
            out.add(i)
    return out


def data_row_matches(matches):
    idx = data_row_indices(matches)
    return [m for i, m in enumerate(matches) if i in idx]


def header_cells(htmlstr):
    """Plain-text cells of the header row (the one carrying <th>), or None."""
    for m in split_rows(htmlstr):
        if is_header(m.group(0)):
            return cells_text(m.group(0))
    return None


def resolve_layout(original, filled, rules=None):
    """(#0) Resolve column roles from the LIVE header, then compare them to the
    roles recorded at fetch time. Aborts rather than guessing an index."""
    hdr = header_cells(original)
    if not hdr:
        sys.exit("LAYOUT: checklist has no <th> header row — cannot resolve column roles; aborting")
    try:
        roles = checklist_columns.resolve(hdr, rules)
    except checklist_columns.LayoutError as e:
        sys.exit(f"LAYOUT: {e}; aborting (never guessing column positions)")
    cols = filled.get("columns")
    fetched = (cols or {}).get("roles")
    if fetched:
        drift = checklist_columns.compare(fetched, roles)
        if drift:
            sys.exit("LAYOUT DRIFT since review: " + "; ".join(drift) + " — aborting")
    elif cols is not None:
        # The fetch emitted a columns block and left roles null: it could not
        # read the header, or the checklist's tables disagree on the layout.
        # Either way it has already established that no index describes this
        # table. Resolving from the live header here would write by an index
        # the fetch refused to trust -- into whichever table lost.
        sys.exit("LAYOUT: the fetch could not resolve column roles"
                 + (f" ({cols.get('error')})" if cols.get("error") else "")
                 + " — aborting rather than writing by an index it refused")
    else:
        print("  [warn] findings JSON carries no columns block (pre-layout-aware "
              "run?); using the live header only", file=sys.stderr)
    print(f"layout OK: {checklist_columns.describe(roles)}")
    return roles


def inject(original, filled_rows, roles):
    """filled_rows: list of dicts with applicable/result/comment, aligned to DATA rows.
    Returns (new_html, skipped_idx). skipped_idx = data-row indices left untouched
    because a human already reviewed them. Raises on row-count mismatch."""
    data_idx = [0]            # index into filled_rows
    skipped = []
    seen_header = [False]
    fill_at = checklist_columns.fill_map(roles)     # {col index: finding key}
    guard_cols = (roles["defect_status"], roles["result"])
    def repl_tr(m):
        tr = m.group(0)
        if is_header(tr):                 # header row — leave as is
            seen_header[0] = True
            return tr
        if not seen_header[0]:
            # preamble above the first header; see data_row_matches
            return tr
        i = data_idx[0]; data_idx[0] += 1
        if i >= len(filled_rows):
            raise SystemExit(f"more data rows than filled entries ({i} >= {len(filled_rows)})")
        # (#5) human-reviewed guard: defect-status (open/closed) or the verdict
        # itself already filled -> a human owns this row; leave it untouched.
        cells = cells_text(tr)
        if any(len(cells) > c and cells[c].strip() for c in guard_cols):
            skipped.append(i)
            return tr
        col = [0]
        def repl_td(tdm):
            c = col[0]; col[0] += 1
            if c not in fill_at:
                return tdm.group(0)
            open_tag = re.match(r"<td\b[^>]*>", tdm.group(0), flags=re.I).group(0)
            v = html.escape(str(filled_rows[i].get(fill_at[c], "") or ""))
            return open_tag + (SPAN.format(v) if v else "") + "</td>"
        return re.sub(r"<td\b[^>]*>.*?</td>", repl_td, tr, flags=re.S | re.I)
    new = re.sub(r"<tr\b.*?</tr>", repl_tr, original, flags=re.S | re.I)
    if data_idx[0] != len(filled_rows):
        raise SystemExit(f"row mismatch: {data_idx[0]} data rows vs {len(filled_rows)} filled entries")
    return new, skipped


def validate(original, new, roles):
    o, n = split_rows(original), split_rows(new)
    assert len(o) == len(n), f"row count changed {len(o)}->{len(n)}"
    keep = checklist_columns.preserved(roles)       # template cols + defect status
    data = data_row_indices(o)
    for i, (om, nm) in enumerate(zip(o, n)):
        ot, nt = om.group(0), nm.group(0)
        if i not in data:
            # header and preamble rows are never written. Byte-identical is a
            # stronger statement than the per-column one below, and the only
            # one that holds for a colspan'd note row, which legitimately has
            # a single cell where the header has seven.
            assert ot == nt, f"row {i} is not a checklist item but was modified"
            continue
        oc, nc = cells_text(ot), cells_text(nt)
        assert len(oc) == len(nc), f"row {i} col count changed"
        assert len(oc) == roles["ncols"], \
            f"row {i} has {len(oc)} cells, header has {roles['ncols']} — layout not uniform"
        for c in keep:
            assert oc[c] == nc[c], f"row {i} col {c} changed (must be preserved): {oc[c]!r} -> {nc[c]!r}"
    return True


def lost_update_check(filled, current_rows, roles):
    """Compare the template columns of the reviewed checklist
    (filled['template_rows'], captured verbatim by fetch_checklist.py) to the
    current work item. Abort on drift."""
    base = filled.get("template_rows")
    if not base:
        print("  [warn] findings JSON carries no template_rows (pre-layout-aware run?); "
              "skipping lost-update check — structural validation still applies", file=sys.stderr)
        return
    cur = [cells_text(m.group(0)) for m in data_row_matches(current_rows)]
    if len(base) != len(cur):
        sys.exit(f"LOST-UPDATE: data row count differs (reviewed {len(base)} vs current {len(cur)}) — aborting")
    for i, (b, c) in enumerate(zip(base, cur)):
        for k in roles["template"]:
            # whitespace-insensitive: the two sides were parsed by different
            # parsers (see checklist_columns.same_text)
            if not checklist_columns.same_text(b[k] if k < len(b) else "",
                                               c[k] if k < len(c) else ""):
                sys.exit(f"LOST-UPDATE: row {i} template col {k} changed since review — aborting\n"
                         f"  reviewed: {(b[k] if k < len(b) else '')[:80]!r}\n"
                         f"  current : {(c[k] if k < len(c) else '')[:80]!r}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project"); ap.add_argument("--review-wi")
    ap.add_argument("--filled", required=True, help="filled findings JSON (rows aligned to checklist)")
    ap.add_argument("--checklist-field", default=None)
    ap.add_argument("--profile", default="", help="config profile (default: matched by project)")
    ap.add_argument("--review-type", default=None,
                    help="work item type this script may write (default: profile's review type)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--from-file", default="", help="offline: use a cached @all dump instead of live fetch")
    args = ap.parse_args()

    filled = json.load(open(args.filled))
    rows = filled.get("results") or filled.get("rows") or []
    if not rows: sys.exit("filled JSON has no results/rows")

    # project / review-wi default from the filled JSON if not given
    project = args.project or filled.get("document", {}).get("project_id")
    review_wi = args.review_wi or filled.get("source_wi")

    prof = cfg.profile_for(project_id=project, name=args.profile or None)
    review_type = args.review_type or cfg.review_type(prof)
    checklist_field = args.checklist_field or cfg.checklist_field(prof)
    col_rules = cfg.column_rules(prof)

    if args.from_file:
        wi = json.load(open(args.from_file))
    else:
        if not (project and review_wi):
            sys.exit("need --project and --review-wi (or document.project_id + source_wi in filled JSON)")
        global REST
        url, token = load_env(); REST = url + "/polarion/rest/v1"
        wi = fetch_wi(token, project, review_wi)             # re-fetch right before write (#2)

    # (#6) type gate — this script only ever writes the checklist of a REVIEW work
    # item. Nothing else is a legitimate target, so a mistyped --review-wi or a
    # findings JSON pointing elsewhere aborts here instead of PATCHing a
    # requirement/defect/whatever that happens to have a `checklist` field.
    actual = wi_type(wi)
    if actual != review_type:
        sys.exit(f"TYPE: target {project}/{review_wi} is type '{actual or '(unknown)'}', "
                 f"not '{review_type}' — refusing to write")
    print(f"type OK: target is a '{actual}' work item")

    # (#5) ownership gate — only the assigned reviewer/assignee, and only when that
    # person IS the current user (PAT owner), may write.
    me = get_my_user()
    owners = wi_owners(wi)
    if not owners:
        sys.exit("OWNERSHIP: the REVIEW work item has no assigned reviewer/assignee — refusing to write")
    if me not in owners:
        sys.exit(f"OWNERSHIP: current user '{me}' is not an assigned reviewer/assignee {sorted(owners)} — refusing to write")
    print(f"ownership OK: current user '{me}' is among the assigned reviewer/assignee")

    original = wi["data"]["attributes"][checklist_field]["value"]
    roles = resolve_layout(original, filled, col_rules)      # (#0)
    current_rows = split_rows(original)
    lost_update_check(filled, current_rows, roles)           # (#2)

    new_html, skipped = inject(original, rows, roles)        # (#4 keep cols; #5 skip human-reviewed)
    validate(original, new_html, roles)                      # (#3)
    written = len(rows) - len(skipped)
    print(f"injection OK: {len(rows)} rows; template+defect-status preserved; only applicable/result/comment written")
    if skipped:
        print(f"human-reviewed guard: {len(skipped)} row(s) left untouched "
              f"(defect-status or result already filled) -> {written} row(s) written, rows {skipped} skipped")
    if written == 0:
        print("nothing to write: every row was already reviewed by a human — skipping PATCH")
        return

    fail_field = cfg.fail_count_field(prof)
    fails = count_fails(new_html, roles) if fail_field else None
    if fail_field:
        print(f"failed items: {fails} -> {fail_field}")
    if args.from_file or args.dry_run:
        print("[dry-run] not writing. new HTML length:", len(new_html))
        return
    code = patch_checklist(token, project, review_wi, new_html, checklist_field,
                           fail_field, fails)
    print(f"PATCH {project}/{review_wi} checklist -> HTTP {code}")


if __name__ == "__main__":
    main()
