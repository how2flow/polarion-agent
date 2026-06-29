#!/usr/bin/env python3
"""write_checklist.py — write a filled review checklist back to its Polarion
REVIEW work item, by SURGICALLY injecting the reviewer values into the original
`checklist` HTML (preserving everything else). Deterministic plumbing; the AI
only supplies the cell values (in the filled findings JSON).

What it fills: per data row, the reviewer columns 리뷰대상(O/X) / 판정(Pass/Fail) /
리뷰어의견 — by index [3,4,5]. It does NOT touch the template columns
(분류/리뷰항목/판정기준 = [0,1,2]) or 결함상태([6], left to humans).

Safety:
  (#2 lost-update) re-fetches the work item right before writing and verifies
       the template columns are unchanged vs what was reviewed; aborts on drift.
  (#3 structural) after injection, re-parses and asserts same row/col count and
       that columns [0,1,2,6] are byte-equal to the source; only [3,4,5] differ.
  (#4) 결함상태 column is never written.
  (#1 dry-run) --dry-run prints what would change and writes nothing.

Auth: POLARION_URL + POLARION_TOKEN (env or <repo>/.env).

Usage:
  write_checklist.py --project P --review-wi ID --filled filled.json [--dry-run]
  write_checklist.py --from-file wi_dump.json --filled filled.json --dry-run   # offline test
"""
import argparse, base64, json, os, re, sys, html
from html.parser import HTMLParser
from urllib import request, error

REST = None
FILL_COLS = [3, 4, 5]          # 리뷰대상 / 판정 / 의견
TEMPLATE_COLS = [0, 1, 2]      # 분류 / 리뷰항목 / 판정기준  (validate: must stay byte-identical in HTML)
KEEP_COLS = [6]                # 결함상태 (left to humans)
# lost-update compares row COUNT + only the short, verbatim-reproduced 분류 (col 0).
# 리뷰항목 (col 1) and 판정기준 (col 2) can be long and get paraphrased/truncated by
# the AI in results[], so comparing them against the live checklist yields false
# "drift". Count + category-sequence catches insert/delete/cross-category reorder;
# structural integrity of what we actually write is enforced separately by
# validate() (live-vs-live HTML: cols 0/1/2/6 stay byte-identical, only 3/4/5 change).
LU_COLS = [0]
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
    req = request.Request(REST + f"/projects/{project}/workitems/{wi_id}?fields%5Bworkitems%5D=%40all",
                          headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with request.urlopen(req, timeout=30) as r:
        return json.load(r)


def patch_checklist(token, project, wi_id, new_html):
    body = json.dumps({"data": {"type": "workitems", "id": f"{project}/{wi_id}",
                                "attributes": {"checklist": {"type": "text/html", "value": new_html}}}}).encode()
    req = request.Request(REST + f"/projects/{project}/workitems/{wi_id}", data=body, method="PATCH",
                          headers={"Authorization": f"Bearer {token}",
                                   "Content-Type": "application/json", "Accept": "application/json"})
    with request.urlopen(req, timeout=30) as r:
        return r.status


def cells_text(tr_html):
    """Return list of plain-text cell contents for a <tr> (td or th)."""
    out = []
    for m in re.finditer(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", tr_html, flags=re.S | re.I):
        txt = re.sub(r"<[^>]+>", "", m.group(1))
        out.append(html.unescape(re.sub(r"\s+", " ", txt)).strip())
    return out


def split_rows(htmlstr):
    return list(re.finditer(r"<tr\b.*?</tr>", htmlstr, flags=re.S | re.I))


def inject(original, filled_rows):
    """filled_rows: list of dicts with applicable/result/comment, aligned to DATA rows.
    Returns new html. Raises on row-count mismatch."""
    data_idx = [0]            # index into filled_rows
    def repl_tr(m):
        tr = m.group(0)
        if re.search(r"<th\b", tr, flags=re.I):   # header row — leave as is
            return tr
        i = data_idx[0]; data_idx[0] += 1
        if i >= len(filled_rows):
            raise SystemExit(f"more data rows than filled entries ({i} >= {len(filled_rows)})")
        vals = {3: filled_rows[i].get("applicable", ""),
                4: filled_rows[i].get("result", ""),
                5: filled_rows[i].get("comment", "")}
        col = [0]
        def repl_td(tdm):
            c = col[0]; col[0] += 1
            if c not in FILL_COLS:
                return tdm.group(0)
            open_tag = re.match(r"<td\b[^>]*>", tdm.group(0), flags=re.I).group(0)
            v = html.escape(str(vals[c] or ""))
            return open_tag + (SPAN.format(v) if v else "") + "</td>"
        return re.sub(r"<td\b[^>]*>.*?</td>", repl_td, tr, flags=re.S | re.I)
    new = re.sub(r"<tr\b.*?</tr>", repl_tr, original, flags=re.S | re.I)
    if data_idx[0] != len(filled_rows):
        raise SystemExit(f"row mismatch: {data_idx[0]} data rows vs {len(filled_rows)} filled entries")
    return new


def validate(original, new):
    o, n = split_rows(original), split_rows(new)
    assert len(o) == len(n), f"row count changed {len(o)}->{len(n)}"
    for i, (om, nm) in enumerate(zip(o, n)):
        oc, nc = cells_text(om.group(0)), cells_text(nm.group(0))
        assert len(oc) == len(nc), f"row {i} col count changed"
        for c in TEMPLATE_COLS + KEEP_COLS:
            if c < len(oc):
                assert oc[c] == nc[c], f"row {i} col {c} changed (must be preserved): {oc[c]!r} -> {nc[c]!r}"
    return True


def lost_update_check(filled, current_rows):
    """Compare template cols of the reviewed checklist (filled['template_rows'])
    to the current work item. Abort on drift."""
    base = filled.get("template_rows")
    if not base:
        res = filled.get("results") or []
        if res and any("category" in r for r in res):
            base = [[r.get("category", ""), r.get("item", ""), r.get("criteria", "")] for r in res]
    if not base:
        print("  [warn] no template_rows/results categories; skipping lost-update check", file=sys.stderr)
        return
    cur = [cells_text(m.group(0)) for m in current_rows if not re.search(r"<th\b", m.group(0), flags=re.I)]
    if len(base) != len(cur):
        sys.exit(f"LOST-UPDATE: data row count differs (reviewed {len(base)} vs current {len(cur)}) — aborting")
    for i, (b, c) in enumerate(zip(base, cur)):
        for k in LU_COLS:
            if (b[k] if k < len(b) else "") != (c[k] if k < len(c) else ""):
                sys.exit(f"LOST-UPDATE: row {i} id col {k} changed since review — aborting")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project"); ap.add_argument("--review-wi")
    ap.add_argument("--filled", required=True, help="filled findings JSON (rows aligned to checklist)")
    ap.add_argument("--checklist-field", default="checklist")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--from-file", default="", help="offline: use a cached @all dump instead of live fetch")
    args = ap.parse_args()

    filled = json.load(open(args.filled))
    rows = filled.get("results") or filled.get("rows") or []
    if not rows: sys.exit("filled JSON has no results/rows")

    # project / review-wi default from the filled JSON if not given
    project = args.project or filled.get("document", {}).get("project_id")
    review_wi = args.review_wi or filled.get("source_wi")

    if args.from_file:
        wi = json.load(open(args.from_file))
    else:
        if not (project and review_wi):
            sys.exit("need --project and --review-wi (or document.project_id + source_wi in filled JSON)")
        global REST
        url, token = load_env(); REST = url + "/polarion/rest/v1"
        wi = fetch_wi(token, project, review_wi)             # re-fetch right before write (#2)

    # (#5) ownership gate — only the assigned reviewer/assignee, and only when that
    # person IS the current user (PAT owner), may write.
    me = get_my_user()
    owners = wi_owners(wi)
    if not owners:
        sys.exit("OWNERSHIP: the REVIEW work item has no assigned reviewer/assignee — refusing to write")
    if me not in owners:
        sys.exit(f"OWNERSHIP: current user '{me}' is not an assigned reviewer/assignee {sorted(owners)} — refusing to write")
    print(f"ownership OK: current user '{me}' is among the assigned reviewer/assignee")

    original = wi["data"]["attributes"][args.checklist_field]["value"]
    current_rows = split_rows(original)
    lost_update_check(filled, current_rows)                  # (#2)

    new_html = inject(original, rows)                        # (#4: only cols 3,4,5)
    validate(original, new_html)                             # (#3)
    print(f"injection OK: {len(rows)} rows; template+결함상태 preserved; only 리뷰대상/판정/의견 written")

    if args.from_file or args.dry_run:
        print("[dry-run] not writing. new HTML length:", len(new_html))
        return
    code = patch_checklist(token, project, review_wi, new_html)
    print(f"PATCH {project}/{review_wi} checklist -> HTTP {code}")


if __name__ == "__main__":
    main()
