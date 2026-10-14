#!/usr/bin/env python3
"""fetch_checklist.py — fetch the review checklist source for a Polarion document.

Deterministic, vendor-neutral plumbing for the doc-review skill. It pulls raw
data and emits a JSON file; it does NOT decide review criteria — doc-review (AI)
interprets the JSON. Generic extraction only (HTML table -> rows); judging the
checklist content is left to the AI.

Column ROLES, however, are resolved deterministically from the header row
(`columns.roles`, via scripts/checklist_columns.py) because the layout is
doctype-specific — 7 columns with a category, 6 without — and write-back must never
guess an index. `template_rows` carries the data rows verbatim so the writer can
do a lost-update check against the original text rather than AI-paraphrased
values.

Decision tree (emitted as `status`):
  1. no REVIEW-type work item for the document        -> status="no-review-wi"      (+ doc reference)
  2. REVIEW work item but empty/absent checklist field -> status="review-wi-no-checklist" (+ wi content)
  3. checklist present                                 -> status="checklist-present" (+ rows, item_count)

Config: every instance-specific name (work item types, checklist field, the
header keywords that identify reviewer columns, and where the official template
documents live) comes from config/polarion.json, selected by the document's
project_id. CLI flags (--review-type, --checklist-field, --profile) override it.

Also emits `template_check`: the document's checklist diffed against the
organization's official template document, fetched live each run (cached under
.cache/templates/ and reused only if that fetch fails). Advisory only — the
review and write-back always use the document's own checklist.

Auth: POLARION_URL + POLARION_TOKEN from env, else from <repo>/.env.
Credentials are never written to the output.

Usage:
  fetch_checklist.py --project P --space S --document D [--doctype T] [--review-wi ID] [--out FILE]
  fetch_checklist.py --from-file wi_dump.json            # offline: test extraction on a cached @all dump
"""
import argparse, base64, json, os, re, sys, html
from html.parser import HTMLParser
from urllib import parse
# polarion_rest is shared infra in <repo>/scripts — add it to the import path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
import polarion_rest  # shared REST helper with ALM-seat-limit retry
from polarion_rest import seg
import checklist_columns  # header-driven column roles (layout varies by doctype)
import polarion_config as cfg  # instance/org profile (types, keywords, templates)

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
    """Generic HTML table -> list of rows, each a list of cell texts.

    `header_flags[i]` says whether row i carries a <th>. That, not the row's
    position, is what write_checklist treats as a header -- it leaves every
    <tr> containing a <th> untouched -- and a checklist built as several
    tables has one per table. Deciding it by position made the two sides
    disagree about how many data rows there are."""
    def __init__(self):
        super().__init__()
        self.rows = []; self.header_flags = []
        self.cur = None; self.cell = None; self.is_header = False
    def handle_starttag(self, t, a):
        if t == "tr": self.cur = []; self.is_header = False
        elif t in ("td", "th"):
            self.cell = []
            if t == "th": self.is_header = True
    def handle_endtag(self, t):
        if t == "tr" and self.cur is not None:
            self.rows.append(self.cur); self.header_flags.append(self.is_header)
            self.cur = None
        elif t in ("td", "th") and self.cell is not None:
            txt = html.unescape(re.sub(r"\s+", " ", " ".join(self.cell))).strip()
            if self.cur is not None: self.cur.append(txt)
            self.cell = None
    def handle_data(self, d):
        if self.cell is not None: self.cell.append(d)


def detag(s):
    s = re.sub(r"(?is)<br\s*/?>", " ", s or "")
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s))).strip()


def row_census(htmlstr):
    """(total <tr>, how many carry a <th>) -- write_checklist's definition.

    Mirrors write_checklist.split_rows / inject exactly: the writer treats
    every <tr> without a <th> as a data row and resolves column roles from the
    first <th> row. extract_checklist instead counts text-bearing rows minus
    the first, and the two part company as soon as the HTML has a second
    header row -- which a template built as two tables has."""
    trs = re.findall(r"<tr\b.*?</tr>", htmlstr or "", flags=re.S | re.I)
    return len(trs), sum(1 for t in trs if re.search(r"<th\b", t, flags=re.I))


def extract_checklist(wi_json, checklist_field="checklist", rules=None):
    """Given a work-item @all JSON, return (status_part, payload)."""
    data = wi_json.get("data", wi_json)
    attrs = data.get("attributes", {}) if isinstance(data, dict) else {}
    raw = attrs.get(checklist_field)
    value = raw.get("value") if isinstance(raw, dict) else raw
    if not value or not detag(value):
        return "review-wi-no-checklist", {
            "wi_text": detag(attrs.get("description", {}).get("value", "")
                             if isinstance(attrs.get("description"), dict) else ""),
            # markup with no text is NOT an empty field; the sync's gate needs
            # to see it, and detag() has already thrown that distinction away
            "checklist_html": value or ""}
    p = _Table(); p.feed(value)
    keep = [(r, h) for r, h in zip(p.rows, p.header_flags) if any(c.strip() for c in r)]
    rows = [r for r, _ in keep]
    headers = [r for r, h in keep if h]
    # Rows before the first header are preamble -- four real checklists open
    # with a colspan'd note above the table. They are not items, and
    # write_checklist skips them for the same reason: counting one as item 0
    # spends a verdict on it and shifts every later row by one.
    first_hdr = next((i for i, (_, h) in enumerate(keep) if h), len(keep))
    data_rows = [r for r, h in keep[first_hdr:] if not h]
    preamble_rows = [r for r, _ in keep[:first_hdr]]
    _census = row_census(value)
    payload = {
        "rows": rows,
        # blank rows are dropped above, so data_row_count can be short of the
        # <tr>s the HTML really carries -- and write_checklist pairs findings
        # with <tr>s, not with the count
        "dropped_blank_rows": len(p.rows) - len(rows),
        # the writer's own census of the same HTML (see row_census)
        "tr_count": _census[0], "th_rows": _census[1],
        "item_count": len(rows),              # rows INCLUDING every header row
        "data_row_count": len(data_rows),     # actual checklist items
        "header_row_count": len(headers),
        "preamble_row_count": len(preamble_rows),
        "template_rows": data_rows,           # verbatim data rows -> lost-update basis
        "checklist_text": detag(value),       # generic fallback for the AI
        "checklist_html": value,              # verbatim source; --sync-template writes this back
    }
    # Column layout is doctype-specific (7 cols with a category, 6 without) — resolve the
    # role of each column from the header row instead of assuming indices.
    # write_checklist resolves from the FIRST <th> row, so this must too. When
    # a checklist is several tables the rest have to agree: if they do not, no
    # single index map describes the table and nothing may be written by index.
    try:
        if not headers:
            raise checklist_columns.LayoutError(
                "no <th> header row (a header of bold <td>s parses as data)")
        roles = checklist_columns.resolve(headers[0], rules)
        for other in headers[1:]:
            if checklist_columns.resolve(other, rules) != roles:
                raise checklist_columns.LayoutError(
                    "the checklist's %d header rows do not agree on the column "
                    "layout" % len(headers))
        payload["columns"] = {"header": headers[0], "roles": roles}
    except (IndexError, checklist_columns.LayoutError) as e:
        payload["columns"] = {"header": (headers or rows or [[]])[0], "roles": None,
                              "error": str(e)}
    return "checklist-present", payload


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


def resolve_doctype(token, project, space, document, fallback=""):
    """(doctype, metadata) from the document itself; `fallback` when it cannot
    be read. Seat exhaustion still ends the run — nothing has been produced yet."""
    try:
        meta = fetch_document(token, project, space, document)
        if meta and meta.get("type"):
            if fallback and fallback != meta["type"]:
                print(f"  [warn] --doctype '{fallback}' != document type "
                      f"'{meta['type']}' — using the document's own type", file=sys.stderr)
            return meta["type"], meta
        return fallback, meta
    except (Exception, SystemExit) as e:
        if polarion_rest.is_seat_exhausted(e):
            raise
        print(f"  [warn] could not read document metadata ({e}); "
              f"falling back to --doctype '{fallback}'", file=sys.stderr)
        return fallback, None


def fetch_document(token, project, space, document):
    """The document's own metadata — type (doctype), title, status.

    The doctype is resolved HERE rather than trusted from --doctype: the
    template-currency check is keyed by it, and a flag the caller forgot would
    silently disable the whole check."""
    # every segment, not just the document: a space id with a space produces
    # http.client.InvalidURL, which is neither HTTPError nor URLError and would
    # escape polarion_rest entirely
    js = rest_get(token, f"/projects/{seg(project)}/spaces/{seg(space)}/documents/{seg(document)}",
                  {"fields[documents]": "@all"})
    a = ((js.get("data") or {}) if isinstance(js, dict) else {}).get("attributes")
    if a is None:
        return None          # nothing was read; an empty dict would read as "was"
    return {"type": a.get("type", ""), "title": a.get("title", ""),
            "status": a.get("status", "")}


def find_wis_in(token, project, space, document, wtype):
    """REVIEW/defect work items of a document, via native SQL (the Lucene
    `module.id:` form returns nothing on this instance)."""
    # Polarion REST has no bind parameters, so every literal is escaped here.
    q = lambda v: str(v).replace("'", "''")
    sql = ("SELECT item.* FROM POLARION.WORKITEM item "
           "INNER JOIN POLARION.REL_MODULE_WORKITEM rel ON rel.fk_uri_workitem = item.c_uri "
           "INNER JOIN POLARION.MODULE m ON m.c_uri = rel.fk_uri_module "
           "INNER JOIN POLARION.PROJECT p ON p.c_uri = m.fk_uri_project "
           f"WHERE p.c_id = '{q(project)}' AND m.c_modulefolder = '{q(space)}' "
           f"AND m.c_id = '{q(document)}' AND item.c_type = '{q(wtype)}'")
    res = rest_get(token, f"/projects/{seg(project)}/workitems",
                   {"query": "SQL:(" + sql + ")",
                    "fields[workitems]": "id,type,title", "page[size]": 100})
    return [it["id"].split("/")[-1] for it in res.get("data", [])]


def fetch_template(token, prof, doctype, checklist_field, rules=None, review_type=None):
    """The official template document's checklist for `doctype`.

    Fetched live every run so the comparison baseline can never go stale; the
    result is cached (gitignored) and reused only when the live fetch fails."""
    t = cfg.templates(prof)
    docname = cfg.template_document(prof, doctype)
    tproj, tspace = t.get("project_id"), t.get("space_id")
    meta = {"project_id": tproj, "space_id": tspace, "document_name": docname, "doctype": doctype}
    if not t:
        return None, dict(meta, source="none", configured=False,
                          error="this profile configures no template documents")
    if not (tproj and tspace) or not (t.get("doctypes") or {}):
        return None, dict(meta, source="none", configured=False, config_error=True,
                          error="profile.templates is incomplete (needs project_id, "
                                "space_id and doctypes) — check config/polarion.json")
    if not docname:
        return None, dict(meta, source="none", configured=False,
                          error=f"no template document configured for doctype '{doctype}'")
    cache = None
    try:
        # inside the try: creating the cache dir can fail (read-only checkout,
        # a .cache owned by another cron account) and must not kill the review
        cache = os.path.join(cfg.cache_dir(cfg.name(prof)), f"{doctype}.json")
        rtype = review_type or cfg.review_type(prof)
        wis = sorted(find_wis_in(token, tproj, tspace, docname, rtype))
        if not wis:
            raise RuntimeError(f"no {rtype} work item in template document")
        if len(wis) > 1:   # sorted() keeps the baseline stable across runs
            print(f"  [warn] template document has {len(wis)} {rtype} items "
                  f"{wis}; using {wis[0]}", file=sys.stderr)
        wi = rest_get(token, f"/projects/{seg(tproj)}/workitems/{seg(wis[0])}",
                      {"fields[workitems]": "@all"})
        status, payload = extract_checklist(wi, checklist_field, rules)
        if status != "checklist-present":
            raise RuntimeError(f"template checklist {status}")
        meta.update(review_wi=wis[0], source="live", fetched=_today(),
                    candidates=len(wis))
        tmp = cache + ".tmp"   # cache is set above or we are already in except
        try:   # temp file + replace: a killed run must not leave truncated JSON
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"meta": meta, "payload": payload}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, cache)
        except OSError as e:   # a cache we cannot write must not fail the review
            print(f"  [warn] could not cache template: {e}", file=sys.stderr)
            try: os.unlink(tmp)          # no half-written leftovers
            except OSError: pass
        return payload, meta
    except (SystemExit, RuntimeError, KeyError, OSError,
            AttributeError, TypeError, ValueError) as e:
        # Everything degrades here, seat exhaustion included: this runs after
        # the document and checklist were already fetched with a seat, and
        # raising would discard them before --out is written.
        # An advisory check must never abort the review: an empty body makes
        # polarion_rest.get return None (AttributeError downstream) and an SSO
        # HTML page makes json.loads raise ValueError.
        if cache and os.path.exists(cache):   # fall back to the last good fetch
            try:
                with open(cache, encoding="utf-8") as f:
                    c = json.load(f)
                return c["payload"], dict(c["meta"], source="cache", live_error=str(e))
            except (ValueError, KeyError, OSError) as ce:
                print(f"  [warn] template cache unreadable ({ce}); ignoring it", file=sys.stderr)
        return None, dict(meta, source="none", error=str(e))


def _today():
    import datetime
    return datetime.date.today().isoformat()


def build_template_check(doc_status, tpl, labels, live, meta=None):
    """The template-check body for one run.

    Returns `fallback`: "none" (the document has its own checklist, so this is
    advisory), "template" (use the template as the checklist), "made-by-agent"
    (no governed template exists) or "stop" (whether one exists could not be
    established)."""
    if doc_status == "checklist-present":
        # The document's own checklist governs; the template check is advisory
        # and must never redirect the review, however it turned out.
        if tpl:
            return dict(compare_to_template(live, tpl, labels), fallback="none")
        return {"status": "unavailable", "fallback": "none"}
    if tpl:
        return {"status": "no-document-checklist",
                "fallback": "template",
                "template_item_count": tpl.get("data_row_count"),
                "template_checklist": {"columns": tpl.get("columns"),
                                       "rows": tpl.get("rows"),
                                       # the item list, headers excluded
                                       "template_rows": tpl.get("template_rows"),
                                       "item_count": tpl.get("item_count"),
                                       "data_row_count": tpl.get("data_row_count")}}
    m = meta or {}
    # Improvising is only safe when we know no governed template exists. A
    # template we could not reach, and a config we cannot trust, are both
    # "unknown", and inventing criteria there would pass for a governed review.
    settled = m.get("configured", True) is False and not m.get("config_error")
    return {"status": "unavailable",
            "fallback": "made-by-agent" if settled else "stop"}


def compare_to_template(live, tpl, labels):
    """Row-level diff of a document's checklist against the official template.

    Rows are matched by their 'item' text (whitespace-insensitive), so a
    reordered checklist is not reported as changed."""
    def index(payload):
        """({key: row}, labels_used, short_rows, duplicate_rows).

        Keyed by (category, item) — category included so two items with the same
        wording under different categories stay distinct. Rows too short to carry
        an item, and rows whose key repeats, are COUNTED rather than silently
        dropped so the reported totals stay honest."""
        roles = (payload.get("columns") or {}).get("roles")
        if not roles:
            return None, None, 0, 0
        cols = checklist_columns.label_template_columns(payload["columns"]["header"], roles, labels)
        if "item" not in cols:
            return None, None, 0, 0
        out, unkeyed, dupes = {}, 0, 0
        def cell(row, role):
            i = cols.get(role)
            return row[i] if i is not None and i < len(row) else ""
        for row in payload.get("template_rows") or []:
            item = cell(row, "item")
            if not checklist_columns._norm(item):
                unkeyed += 1
                continue
            key = (checklist_columns._norm(cell(row, "category")),
                   checklist_columns._norm(item))
            if key in out:
                dupes += 1          # counted, not silently collapsed
                continue
            out[key] = {"item": item, "criteria": cell(row, "criteria")}
        return out, frozenset(cols), unkeyed, dupes
    a, a_cols, a_short, a_dup = index(live)
    b, b_cols, b_short, b_dup = index(tpl)
    if a is None or b is None:
        return {"status": "unknown", "reason": "could not label item/criteria columns"}
    lr0 = (live.get("columns") or {}).get("roles") or {}
    tr0 = (tpl.get("columns") or {}).get("roles") or {}
    if a_cols != b_cols:
        # The two headers word a template column differently, so one side has a
        # label the other lacks: every key would differ and the whole checklist
        # would look missing+extra. Row-level comparison is impossible — but the
        # layout difference itself is often the real story (a document still on
        # the old 6-column template), so report that rather than nothing.
        return {"status": "unknown",
                "reason": "document and template label different template columns "
                          f"({sorted(a_cols)} vs {sorted(b_cols)}) — rows cannot be "
                          "aligned, so missing/extra were not computed",
                "document_item_count": len(a), "template_item_count": len(b),
                "layout_diff": checklist_columns.compare(tr0, lr0) or None}
    missing = [b[k] for k in b if k not in a]          # in template, not in document
    extra   = [a[k] for k in a if k not in b]          # in document only
    changed = [{"item": b[k]["item"], "template_criteria": b[k]["criteria"],
                "document_criteria": a[k]["criteria"]}
               for k in b if k in a
               and not checklist_columns.same_text(a[k]["criteria"], b[k]["criteria"])]
    lr = (live.get("columns") or {}).get("roles") or {}
    tr = (tpl.get("columns") or {}).get("roles") or {}
    layout_diff = checklist_columns.compare(tr, lr) or None
    status = "current" if not (missing or extra or changed or layout_diff) else \
             "outdated" if missing else "diverged"
    return {"status": status,
            "document_item_count": len(a), "template_item_count": len(b),
            **({"unkeyed_rows": {"document": a_short, "template": b_short}}
               if (a_short or b_short) else {}),
            **({"duplicate_rows": {"document": a_dup, "template": b_dup}}
               if (a_dup or b_dup) else {}),
            "missing_items": missing, "extra_items": extra,
            "changed_criteria": changed, "layout_diff": layout_diff}


def type_of(wi):
    """A work item's type id, or None when it could not be read."""
    if not isinstance(wi, dict):
        return None
    return ((wi.get("data") or {}).get("attributes") or {}).get("type")


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


def run_template_check(token, prof, doctype, checklist_field, rules, review_type,
                       doc_status, project, live, enabled=True, doc_meta=None):
    """The whole template_check block: pick a branch, fetch, assemble.

    Every return carries `fallback`, which is what the reviewing agent must do:
    "none" (the document has its own checklist), "template" (use the template as
    the checklist), "made-by-agent" (no governed template exists) or "stop"
    (whether one exists could not be established — unreachable template,
    unreadable metadata, or a half-configured profile). Leaving a branch without
    it is how the prompt ended up with undefined states."""
    own = doc_status == "checklist-present"      # tier 1 applies regardless
    if not enabled:
        return {"status": "unavailable",
                "fallback": "none" if own else "made-by-agent",
                "error": "disabled (--no-template-check)"}
    if not prof.get("_matched"):
        # A defaulted profile points at ANOTHER organization's template
        # documents; diffing against those means nothing.
        return {"status": "unavailable",
                "fallback": "none" if own else "made-by-agent",
                "error": f"no profile matches project '{project}' (fell back to "
                         f"'{cfg.name(prof)}', whose templates belong to another "
                         f"project). Add a profile for this project in "
                         f"config/polarion.json. Tier-2 fallback is unavailable "
                         f"meanwhile. (--profile <name> overrides this, but then "
                         f"THAT profile's template documents are treated as this "
                         f"project's — only do so if they really are.)"}
    if not doctype:
        if doc_meta is not None:
            # The metadata was read and the document simply has no type, so no
            # template can match it. Permanent, and retrying changes nothing.
            return {"status": "unavailable",
                    "fallback": "none" if own else "made-by-agent",
                    "error": "the document has no type, so no template applies"}
        # Metadata unreadable: whether governed criteria exist is unknown, and
        # that is not a licence to invent them.
        return {"status": "unavailable",
                "fallback": "none" if own else "stop",
                "error": "doctype unknown — document metadata unreadable "
                         "and no --doctype given"}
    tpl, tmeta = fetch_template(token, prof, doctype, checklist_field, rules, review_type)
    check = {"template": tmeta, "fallback": "none"}
    if tmeta.get("error"):          # surface it where doc-review.md says to look
        check["error"] = tmeta["error"]
    check.update(build_template_check(doc_status, tpl,
                                      prof.get("template_columns"), live, tmeta))
    check["_tpl"] = tpl   # popped by the caller; never serialized
    return check

# --- template sync ---------------------------------------------------------
# A checklist that is behind the official template is overwritten with the
# template's own table. A changed template means changed criteria, so verdicts
# recorded against the old ones are stale by definition: they are not carried
# over, and the review that follows runs the whole checklist again. Copying the
# template's HTML verbatim is exactly what a reviewer does by hand.
SYNC_STATUSES = ("outdated", "diverged", "no-document-checklist", "unknown")

# Tags a cleared rich-text field is left holding: the editor keeps a paragraph
# or a line break behind when a reviewer selects the table and deletes it.
# Anything else -- an image, a table skeleton, a Polarion macro div -- is
# content detag() cannot see, and the field is not empty.
HUSK_TAGS = {"p", "div", "br", "span", "font", "b", "i", "u", "em", "strong"}
HUSK_ATTRS = {"style", "class", "align", "dir"}


class _Husk(HTMLParser):
    """Is this markup what an emptied field looks like, or does it hold
    something? A tag outside HUSK_TAGS, or a husk tag carrying an attribute
    that is not presentational (a Polarion macro div is identified by `id`),
    counts as content -- as does any text."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.content = False

    def handle_starttag(self, tag, attrs):
        if tag.lower() not in HUSK_TAGS or any(
                k.lower() not in HUSK_ATTRS for k, _ in attrs):
            self.content = True
    handle_startendtag = handle_starttag

    def handle_data(self, data):
        if data.strip():
            self.content = True


def is_husk(htmlstr):
    """True when the field holds nothing but an emptied editor's leftovers.

    A parser, not a pattern: an allowlist of content tags cannot be complete,
    and a raw scan cannot tell a tag from the same word inside a comment or an
    attribute value."""
    if not (htmlstr or "").strip():
        return True
    p = _Husk()
    try:
        p.feed(htmlstr)
        p.close()
    except (AssertionError, ValueError):
        return False          # unparseable is not "empty"
    return not p.content


def should_sync(check):
    """True when the live checklist does not match the official template.

    "unknown" counts. A checklist the comparison could not read is not a
    checklist to protect -- it is the clearest mismatch there is, and
    refusing it meant the documents most in need of the template were the
    only ones that never got it."""
    return check.get("status") in SYNC_STATUSES


def template_write_problem(tpl):
    """Why this template must not be written over a checklist, or None.

    Four ways the HTML parses cleanly and is still unusable, each ending the
    same way -- the live checklist gone and Stage 4 unable to write the
    verdicts back. The first two matter because write_checklist reads the
    same bytes by a different rule: every <tr> without a <th> is a data row,
    and column roles come from the FIRST <th> row."""
    tr, th = tpl.get("tr_count", 0), tpl.get("th_rows", 0)
    rows = tpl.get("data_row_count")
    if th < 1:
        # a header of bold <td>s: _Table cannot tell it from data, so the
        # parse looks fine and resolve_layout exits with "checklist has no
        # <th> header row" -- after the live checklist is already gone
        return "it has no <th> header row"
    if tpl.get("dropped_blank_rows"):
        # dropped by the row filter, so data_row_count is short of the <tr>s
        # the writer will walk ("more data rows than filled entries"). The
        # identity below would catch it too; this names the cause.
        return "it has %d blank spacer rows" % tpl["dropped_blank_rows"]
    pre = tpl.get("preamble_row_count", 0)
    if tr - th - pre != rows:
        # the writer walks every <tr> after the first header without a <th>;
        # any other count means the two sides disagree about how many data
        # rows exist, whatever the cause (a <tr> inside a comment, an
        # unclosed tag, a nested table)
        return ("its %d <tr> minus %d header and %d preamble rows do not "
                "match the %s data rows parsed" % (tr, th, pre, rows))
    if ragged_rows(tpl):
        # a merged cell is read as a short row, which also fabricates the diff
        # that condemned the live checklist in the first place
        return "its rows do not all have the header's cell count"
    return None


def ragged_rows(live):
    """True when the parsed rows do not all have the header's cell count.

    _Table ignores rowspan/colspan, so a merged cell yields a short row and
    every cell after it is read one column to the left -- which the template
    comparison then reports as a missing item plus an extra one, i.e. a false
    'outdated' over a checklist that actually matches. write_checklist refuses
    the same table outright ("layout not uniform"), so without this the sync
    would be the one write in the pipeline with no structural guard on the
    table it destroys."""
    hdr = (live.get("columns") or {}).get("header") or []
    rows = live.get("template_rows") or []
    if not hdr or not rows:
        return False
    # data rows only, measured against the header. A preamble note is
    # legitimately one colspan'd cell wide and is not an item.
    return any(len(r) != len(hdr) for r in rows)


def filled_tracking(live):
    """How many rows carry a human-entered defect status (Open/Closed).

    Reported, not preserved: that column tracks defects raised against the
    criteria the template just replaced, so it is as stale as the verdicts
    beside it. But it is the one column nothing in the pipeline rewrites --
    the writer never fills it -- so an overwrite leaves it blank until a human
    refills it, and the count tells the operator what the run discarded."""
    roles = (live.get("columns") or {}).get("roles") or {}
    i = roles.get("defect_status")
    if i is None:
        return 0
    return sum(1 for r in (live.get("template_rows") or [])
               if len(r) > i and (r[i] or "").strip())


def sync_to_template(token, project, wi_id, field, check, tpl, owned,
                     *, wi_type, review_type, live, dry=False):
    """Overwrite the REVIEW work item's checklist with the official template's.

    Always returns a block: when a gate refuses, `synced` is False and `reason`
    says which one, so a skipped sync is visible in the output instead of
    silent. The gates mirror the write-back ones -- live baseline, the review
    type, a real target, ownership by the current user -- plus the ones only a
    destructive overwrite needs: the template has to parse as a checklist, the
    comparison that condemned the live one has to be trustworthy, and nothing
    human-owned may be lost."""
    tmeta = check.get("template") or {}
    tpl = tpl or {}
    live = live or {}
    if not should_sync(check):
        why = "checklist is '%s' -- nothing to sync" % check.get("status")
    elif tmeta.get("source") != "live":
        why = ("the template was not fetched live (source=%s) -- refusing to "
               "overwrite from a cached or missing baseline" % tmeta.get("source"))
    elif (tmeta.get("candidates") or 1) > 1:
        # sorted(wis)[0] is a stable baseline for a comparison; it is not a
        # safe choice of bytes to write, and one of the others could be a
        # reviewer's partly filled copy.
        why = ("the template document holds %d review work items -- refusing "
               "to guess which one is the template"
               % tmeta.get("candidates"))
    elif not tpl.get("checklist_html"):
        why = "the template work item carries no checklist HTML"
    elif not ((tpl.get("columns") or {}).get("roles") and tpl.get("data_row_count")):
        # On the no-document-checklist branch nothing ever parses the template,
        # so without this a template holding a sentence instead of a table is
        # written in, reported as a governed 0-item checklist, and Stage 4 dies
        # on "checklist has no <th> header row".
        why = ("the official template did not parse as a checklist "
               "(%s data rows, column roles %s)"
               % (tpl.get("data_row_count"),
                  "resolved" if (tpl.get("columns") or {}).get("roles") else "unresolved"))
    elif not wi_id:
        why = "no REVIEW work item to write to"
    elif not owned:
        why = "the current user does not own this REVIEW work item"
    elif wi_type != review_type:
        # --review-wi bypasses find_wis_in's type filter and is supplied by the
        # Stage-1 agent, so the type is an AI choice until it is checked here.
        # Nothing but a review item is a legitimate target: a defect or a
        # requirement that happens to carry a `checklist` field would be
        # silently overwritten, and write_checklist's own type gate would then
        # refuse the target without ever surfacing the write that already went.
        why = ("target is a '%s' work item, not '%s' -- refusing to overwrite"
               % (wi_type, review_type))
    elif check.get("status") == "no-document-checklist" and not is_husk(
            live.get("checklist_html")):
        # "nothing to lose" is the whole justification for writing into an
        # empty checklist field. A field holding an image or a half-built
        # table is not empty, and detag() cannot tell -- it sees "" for both.
        why = ("the checklist field holds markup with no text -- not empty, "
               "and not safe to overwrite unseen")
    elif template_write_problem(tpl):
        why = ("the official template's own table is unusable -- %s; refusing "
               "to write it over a checklist" % template_write_problem(tpl))
    else:
        why = None
    if why:
        return {"synced": False, "reason": why}
    info = {"wi": wi_id, "was": check.get("status"),
            "template": tmeta.get("document_name"),
            "discarded_rows": len(live.get("template_rows") or []),
            "discarded_defect_status": filled_tracking(live)}
    if dry:
        return dict(info, synced=False, dry_run=True, reason="--dry-run")
    body = {"data": {"type": "workitems", "id": "%s/%s" % (project, wi_id),
                     "attributes": {field: {"type": "text/html",
                                            "value": tpl["checklist_html"]}}}}
    polarion_rest.patch(token, REST + "/projects/%s/workitems/%s"
                        % (seg(project), seg(wi_id)), body)
    return dict(info, synced=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project"); ap.add_argument("--space"); ap.add_argument("--document")
    ap.add_argument("--doctype", default="",
                    help="optional; the document's own type is used when readable")
    ap.add_argument("--review-wi", default="")
    ap.add_argument("--profile", default="", help="config profile name (default: matched by project)")
    ap.add_argument("--review-type", default=None)
    ap.add_argument("--defect-type", default=None)
    ap.add_argument("--defect-wi", default="", help="csv of defect ids from parts (bypasses discovery query)")
    ap.add_argument("--checklist-field", default=None)
    ap.add_argument("--no-template-check", action="store_true",
                    help="skip fetching the official template for comparison")
    ap.add_argument("--sync-template", action="store_true",
                    help="overwrite the REVIEW checklist with the official template "
                         "when it is behind it (discards verdicts made against the "
                         "superseded criteria)")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --sync-template: report what would be written, write nothing")
    ap.add_argument("--out", default="")
    ap.add_argument("--from-file", default="", help="offline: extract from a cached @all dump")
    args = ap.parse_args()

    # Profile supplies every instance-specific name; CLI flags still win.
    prof = cfg.profile_for(project_id=args.project, name=args.profile or None)
    review_type = args.review_type or cfg.review_type(prof)
    defect_type = args.defect_type or cfg.defect_type(prof)
    checklist_field = args.checklist_field or cfg.checklist_field(prof)
    rules = cfg.column_rules(prof)

    # Offline extraction test
    if args.from_file:
        wi = json.load(open(args.from_file))
        status, payload = extract_checklist(wi, checklist_field, rules)
        print(json.dumps({"status": status, **payload}, ensure_ascii=False, indent=2)[:2000])
        return

    global REST
    url, token = load_env()
    REST = url + "/polarion/rest/v1"

    doctype, dmeta = resolve_doctype(token, args.project, args.space,
                                     args.document, args.doctype)

    dm = dmeta or {}          # dmeta is None when the metadata could not be read
    out = {"document": {"project_id": args.project, "space_id": args.space,
                        "document_name": args.document, "doctype": doctype,
                        "title": dm.get("title", ""), "status": dm.get("status", "")},
           "profile": cfg.name(prof), "profile_matched": bool(prof.get("_matched")),
           "review_type": review_type, "checklist_field": checklist_field}

    review_wis = [x.strip() for x in args.review_wi.split(",") if x.strip()] if args.review_wi else \
        find_wis_in(token, args.project, args.space, args.document, review_type)

    chosen_wi = None          # the sync's type gate reads it outside this branch
    if not review_wis:
        out["status"] = "no-review-wi"
    else:
        # Pick the REVIEW work item the CURRENT USER owns (reviewer/assignee), so
        # the write-back target matches the ownership gate. Deterministic — not an
        # AI guess. Fall back to the first candidate (matched=False) if none match.
        me = my_user(token)
        chosen, chosen_wi, matched, cache = None, None, False, {}
        for wid in review_wis:
            w = rest_get(token, f"/projects/{seg(args.project)}/workitems/{seg(wid)}",
                         {"fields[workitems]": "@all"})
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
        status, payload = extract_checklist(chosen_wi, checklist_field, rules)
        out["status"] = status
        out.update(payload)

    # Existing inspection defects in the document (only present when they exist).
    defect_ids = [d.strip() for d in args.defect_wi.split(",") if d.strip()] if args.defect_wi \
        else find_wis_in(token, args.project, args.space, args.document, defect_type)
    defects = []
    for did in defect_ids:
        dwi = rest_get(token, f"/projects/{seg(args.project)}/workitems/{seg(did)}",
                       {"fields[workitems]": "@all"})
        defects.append(extract_defect(dwi))
    out["inspection_defects"] = defects  # [] when none

    # Compare the document's checklist against the organization's official
    # template (fetched live, so the baseline is never stale). Advisory only —
    # the review and the write-back always use the document's own checklist.
    out["template_check"] = run_template_check(
        token, prof, doctype, checklist_field, rules, review_type,
        out.get("status"), args.project, out,
        enabled=not args.no_template_check, doc_meta=dmeta)
    tpl = out["template_check"].pop("_tpl", None)   # internal; never serialized

    if args.sync_template:
        out["template_sync"] = sync_to_template(
            token, args.project, out.get("source_wi"), checklist_field,
            out["template_check"], tpl, out.get("source_wi_reviewer_matched"),
            wi_type=type_of(chosen_wi), review_type=review_type,
            live=out, dry=args.dry_run)
        if out["template_sync"]["synced"]:
            # The PATCH cannot be undone from here, and nothing downstream
            # rewrites the defect-status column, so the run keeps the table it
            # replaced. It rides in the fetch output (preserved in out/), not
            # in template_sync, which the agent copies into the findings.
            out["replaced_checklist_html"] = out.get("checklist_html") or ""
            # The template's table IS the live checklist now, so the review,
            # the column roles and the lost-update basis must all come from it.
            out.update(tpl)
            out["status"] = "checklist-present"
            # ... and so must the tier label: fallback "template" would make
            # Stage 1 report checklist_source "default-template" for rows that
            # are now genuinely in the REVIEW work item.
            out["template_check"]["fallback"] = "none"
            out["template_check"].pop("template_checklist", None)

    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.out:
        open(args.out, "w").write(text)
        cols = out.get("columns") or {}
        layout = ("; layout: " + checklist_columns.describe(cols["roles"])) if cols.get("roles") \
            else ("; LAYOUT UNRESOLVED: " + cols["error"]) if cols.get("error") else ""
        print(f"{out['status']} -> {args.out}"
              + (f" ({out.get('data_row_count')} items)" if out.get("data_row_count") else "")
              + layout)
        tc = out.get("template_check")
        if tc:
            st = tc.get("status")
            src = (tc.get("template") or {}).get("source")
            if st == "current":
                print(f"template-check: up to date with the official template ({src})")
            elif st in ("outdated", "diverged"):
                print(f"template-check: {st.upper()} vs official template ({src}) — "
                      f"{len(tc.get('missing_items') or [])} missing, "
                      f"{len(tc.get('extra_items') or [])} extra, "
                      f"{len(tc.get('changed_criteria') or [])} criteria changed"
                      + ("; LAYOUT DIFFERS" if tc.get("layout_diff") else ""))
            else:
                why = tc.get("reason") or tc.get("error") \
                    or (tc.get("template") or {}).get("error", "")
                print(f"template-check: {st} ({why})"
                      + ("; LAYOUT DIFFERS" if tc.get("layout_diff") else ""))
        ts = out.get("template_sync")
        if ts:
            print("template-sync: " + ("overwrote %s with %s -- the review below "
                  "runs on the template's rows" % (ts["wi"], ts["template"])
                  if ts["synced"] else "not synced -- " + ts["reason"]))
    else:
        print(text)


if __name__ == "__main__":
    main()
