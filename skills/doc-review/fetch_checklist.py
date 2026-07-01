#!/usr/bin/env python3
"""fetch_checklist.py — fetch the review checklist source for a Polarion document.

Deterministic, vendor-neutral plumbing for the doc-review skill. It pulls raw
data and emits a JSON file; it does NOT decide review criteria — doc-review (AI)
interprets the JSON. Generic extraction only (HTML table -> rows); assigning
column meaning is left to the AI.

Decision tree (emitted as `status`):
  1. no REVIEW-type work item for the document        -> status="no-review-wi"      (+ doc reference)
  2. REVIEW work item but empty/absent checklist field -> status="review-wi-no-checklist" (+ wi content)
  3. checklist present                                 -> status="checklist-present" (+ rows, item_count)

Config (vendor-specific names live here, not hardcoded in logic):
  --review-type   (default REVIEW)     work item type that carries the checklist
  --checklist-field (default checklist) attribute holding the checklist

Auth: POLARION_URL + POLARION_TOKEN from env, else from <repo>/.env.
Credentials are never written to the output.

Usage:
  fetch_checklist.py --project P --space S --document D [--doctype T] [--review-wi ID] [--out FILE]
  fetch_checklist.py --from-file wi_dump.json            # offline: test extraction on a cached @all dump
"""
import argparse, base64, json, os, re, sys, html
from html.parser import HTMLParser
from urllib import request, parse, error
# polarion_rest is shared infra in <repo>/scripts — add it to the import path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
import polarion_rest  # shared REST helper with ALM-seat-limit retry

REST = None  # set after URL resolved


def load_env():
    url = os.environ.get("POLARION_URL")
    tok = os.environ.get("POLARION_TOKEN")
    if url and tok:
        return url.rstrip("/").removesuffix("/polarion"), tok
    # fall back to repo .env
    here = os.path.dirname(os.path.abspath(__file__))
    root = here
    while root != "/" and not os.path.exists(os.path.join(root, ".polarion-root")):
        root = os.path.dirname(root)
    envf = os.path.join(root, ".env")
    if os.path.exists(envf):
        for line in open(envf):
            line = line.strip()
            if line.startswith("POLARION_URL="):
                url = line.split("=", 1)[1]
            elif line.startswith("POLARION_TOKEN="):
                tok = line.split("=", 1)[1]
    if not (url and tok):
        sys.exit("POLARION_URL / POLARION_TOKEN not set (env or .env)")
    return url.rstrip("/").removesuffix("/polarion"), tok


def rest_get(token, path, params=None):
    qs = ("?" + parse.urlencode(params)) if params else ""
    return polarion_rest.get(token, REST + path + qs)   # seat-limit retry inside


class _Table(HTMLParser):
    """Generic HTML table -> list of rows, each a list of cell texts."""
    def __init__(self):
        super().__init__(); self.rows = []; self.cur = None; self.cell = None; self.buf = []
    def handle_starttag(self, t, a):
        if t == "tr": self.cur = []
        elif t in ("td", "th"): self.cell = []
    def handle_endtag(self, t):
        if t == "tr" and self.cur is not None:
            self.rows.append(self.cur); self.cur = None
        elif t in ("td", "th") and self.cell is not None:
            txt = html.unescape(re.sub(r"\s+", " ", " ".join(self.cell))).strip()
            if self.cur is not None: self.cur.append(txt)
            self.cell = None
    def handle_data(self, d):
        if self.cell is not None: self.cell.append(d)


def detag(s):
    s = re.sub(r"(?is)<br\s*/?>", " ", s or "")
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s))).strip()


def extract_checklist(wi_json, checklist_field="checklist"):
    """Given a work-item @all JSON, return (status_part, payload)."""
    data = wi_json.get("data", wi_json)
    attrs = data.get("attributes", {}) if isinstance(data, dict) else {}
    raw = attrs.get(checklist_field)
    value = raw.get("value") if isinstance(raw, dict) else raw
    if not value or not detag(value):
        return "review-wi-no-checklist", {"wi_text": detag(attrs.get("description", {}).get("value", "")
                                                          if isinstance(attrs.get("description"), dict) else "")}
    p = _Table(); p.feed(value)
    rows = [r for r in p.rows if any(c.strip() for c in r)]
    return "checklist-present", {
        "rows": rows,
        "item_count": len(rows),
        "checklist_text": detag(value),  # generic fallback for the AI
    }


def find_wis(token, project, space, document, wtype):
    """Best-effort: work items of a given type in the document's module.
    NOTE: the Lucene query needs seat-verification on a live instance; callers
    can bypass it by passing explicit ids discovered from read_document_parts."""
    q = f'type:{wtype} AND module.id:{space}/{document}'
    res = rest_get(token, f"/projects/{project}/workitems",
                   {"query": q, "fields[workitems]": "id,type,title", "page[size]": 100})
    return [it["id"].split("/")[-1] for it in res.get("data", [])]


def extract_defect(wi):
    """Extract an existing inspection-defect work item into a compact record."""
    data = wi.get("data", {})
    a = data.get("attributes", {})
    desc = a.get("description")
    return {
        "id": (data.get("id") or "").split("/")[-1] or a.get("id", ""),
        "title": a.get("title", ""),
        "status": a.get("status", ""),
        "severity": a.get("severity", ""),
        "outline": a.get("outlineNumber", ""),
        "description": detag(desc.get("value") if isinstance(desc, dict) else (desc or "")),
    }


def my_user(token):
    """Current user = the PAT's `sub` claim."""
    p = token.split(".")[1]; p += "=" * (-len(p) % 4)
    return json.loads(base64.urlsafe_b64decode(p)).get("sub")


def owners_of(wi):
    """reviewer + assignee user ids on a work item (matches write ownership gate)."""
    rel = wi.get("data", {}).get("relationships", {})
    ids = set()
    for k in ("reviewer", "assignee"):
        d = rel.get(k, {}).get("data")
        if isinstance(d, dict): ids.add(d.get("id"))
        elif isinstance(d, list):
            for x in d: ids.add(x.get("id"))
    ids.discard(None)
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project"); ap.add_argument("--space"); ap.add_argument("--document")
    ap.add_argument("--doctype", default="")
    ap.add_argument("--review-wi", default="")
    ap.add_argument("--review-type", default="REVIEW")
    ap.add_argument("--defect-type", default="Ins_defect")
    ap.add_argument("--defect-wi", default="", help="csv of Ins_defect ids from parts (bypasses discovery query)")
    ap.add_argument("--checklist-field", default="checklist")
    ap.add_argument("--out", default="")
    ap.add_argument("--from-file", default="", help="offline: extract from a cached @all dump")
    args = ap.parse_args()

    # Offline extraction test
    if args.from_file:
        wi = json.load(open(args.from_file))
        status, payload = extract_checklist(wi, args.checklist_field)
        print(json.dumps({"status": status, **payload}, ensure_ascii=False, indent=2)[:2000])
        return

    global REST
    url, token = load_env()
    REST = url + "/polarion/rest/v1"

    out = {"document": {"project_id": args.project, "space_id": args.space,
                        "document_name": args.document, "doctype": args.doctype},
           "review_type": args.review_type, "checklist_field": args.checklist_field}

    review_wis = [x.strip() for x in args.review_wi.split(",") if x.strip()] if args.review_wi else \
        find_wis(token, args.project, args.space, args.document, args.review_type)

    if not review_wis:
        out["status"] = "no-review-wi"
    else:
        # Pick the REVIEW work item the CURRENT USER owns (reviewer/assignee), so
        # the write-back target matches the ownership gate. Deterministic — not an
        # AI guess. Fall back to the first candidate (matched=False) if none match.
        me = my_user(token)
        chosen, chosen_wi, matched, cache = None, None, False, {}
        for wid in review_wis:
            w = rest_get(token, f"/projects/{args.project}/workitems/{wid}", {"fields[workitems]": "@all"})
            cache[wid] = w
            if me in owners_of(w):
                chosen, chosen_wi, matched = wid, w, True
                break
        if chosen is None:
            chosen, chosen_wi = review_wis[0], cache[review_wis[0]]
        out["source_wi"] = chosen
        out["source_wi_reviewer_matched"] = matched  # False -> ownership gate will reject the write
        if len(review_wis) > 1:
            out["review_wi_candidates"] = review_wis
        status, payload = extract_checklist(chosen_wi, args.checklist_field)
        out["status"] = status
        out.update(payload)

    # Existing inspection defects in the document (only present when they exist).
    defect_ids = [d.strip() for d in args.defect_wi.split(",") if d.strip()] if args.defect_wi \
        else find_wis(token, args.project, args.space, args.document, args.defect_type)
    defects = []
    for did in defect_ids:
        dwi = rest_get(token, f"/projects/{args.project}/workitems/{did}",
                       {"fields[workitems]": "@all"})
        defects.append(extract_defect(dwi))
    out["inspection_defects"] = defects  # [] when none

    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.out:
        open(args.out, "w").write(text)
        print(f"{out['status']} -> {args.out}"
              + (f" ({out.get('item_count')} items)" if out.get("item_count") else ""))
    else:
        print(text)


if __name__ == "__main__":
    main()
