#!/usr/bin/env python3
"""write_defects.py — write reviewer-produced inspection-defect content into the
EXISTING Ins_defect work items, per the AI-decided action. Deterministic
executor; the fill/append/skip *decision* is made by doc-review (which sees both
our content and the existing text) and carried in the findings JSON.

Per result[].inspection_defect.write_action:
  fill    -> only if the defect's description is currently blank: set = our content
  append  -> keep existing description, append our content (surgical, HTML preserved)
  skip    -> do nothing (AI judged it a duplicate)
  needs-new / (missing target) -> do nothing (creating a NEW Ins_defect WI is a
             separate opt-in step, not done here)

Safety (same model as write_checklist.py):
  - ownership gate: current PAT user (`sub`) must be an assigned reviewer/assignee
    of the REVIEW work item (source_wi); aborts otherwise.
  - fill guard: if action=fill but the description is NOT blank, downgrade to skip
    (never clobber existing human text).
  - --dry-run: show what would change, write nothing.

Auth: POLARION_URL + POLARION_TOKEN (env or <repo>/.env).
Usage:
  write_defects.py --filled findings.json [--dry-run]
  write_defects.py --filled findings.json --from-file defect_dump.json --dry-run  # offline test
"""
import argparse, base64, json, os, re, sys, html, subprocess, threading, time
from urllib import request, error

REST = None
LAUNCHER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp-polarion.sh")
DEFECT_TYPE = "Ins_defect"


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


def my_user(token):
    p = token.split(".")[1]; p += "=" * (-len(p) % 4)
    return json.loads(base64.urlsafe_b64decode(p)).get("sub")


def wi_owners(wi):
    rel = wi.get("data", {}).get("relationships", {})
    ids = set()
    for k in ("reviewer", "assignee"):
        d = rel.get(k, {}).get("data")
        if isinstance(d, dict): ids.add(d.get("id"))
        elif isinstance(d, list):
            for x in d: ids.add(x.get("id"))
    ids.discard(None)
    return ids


def rest_get(token, path, params=None):
    from urllib import parse
    qs = ("?" + parse.urlencode(params)) if params else ""
    req = request.Request(REST + path + qs, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/json"})
    with request.urlopen(req, timeout=30) as r:
        return json.load(r)


def rest_patch_desc(token, project, wi_id, new_html):
    body = json.dumps({"data": {"type": "workitems", "id": f"{project}/{wi_id}",
                                "attributes": {"description": {"type": "text/html", "value": new_html}}}}).encode()
    req = request.Request(REST + f"/projects/{project}/workitems/{wi_id}", data=body, method="PATCH",
                          headers={"Authorization": f"Bearer {token}",
                                   "Content-Type": "application/json", "Accept": "application/json"})
    with request.urlopen(req, timeout=30) as r:
        return r.status


def detag(s):
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or ""))).strip()


def block(text):
    return "<p>[auto-review] " + html.escape(text) + "</p>"


def plan(current_html, action, content):
    """Return (do_write, new_html, note). Pure — testable offline."""
    cur = detag(current_html)
    if action == "skip":
        return False, current_html, "skip (duplicate per reviewer)"
    if action in ("needs-new", "", None):
        return False, current_html, "needs-new (created live via MCP create_work_item; not exercised in --from-file)"
    if action == "fill":
        if cur != "":
            return False, current_html, "fill requested but description not blank -> skip (no clobber)"
        return True, block(content), "fill (was blank)"
    if action == "append":
        base = current_html if current_html else ""
        return True, base + block(content), "append"
    return False, current_html, f"unknown action '{action}' -> skip"


class MCP:
    """Minimal stdio MCP client over the shared launcher (for create/move)."""
    def __init__(self):
        self.p = subprocess.Popen([LAUNCHER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1, env=dict(os.environ))
        self.id = 0; self.resp = {}; self.lock = threading.Lock()
        threading.Thread(target=self._rd, daemon=True).start()
        self._call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "write-defects", "version": "0"}})
        self._send("notifications/initialized", notif=True)
    def _rd(self):
        for l in self.p.stdout:
            try: m = json.loads(l)
            except: continue
            if "id" in m:
                with self.lock: self.resp[m["id"]] = m
    def _send(self, method, params=None, notif=False):
        m = {"jsonrpc": "2.0", "method": method}
        if params is not None: m["params"] = params
        rid = None
        if not notif: self.id += 1; m["id"] = self.id; rid = self.id
        self.p.stdin.write(json.dumps(m) + "\n"); self.p.stdin.flush(); return rid
    def _call(self, method, params=None, notif=False, to=60):
        rid = self._send(method, params, notif)
        if notif: return None
        t0 = time.time()
        while time.time() - t0 < to:
            with self.lock:
                if rid in self.resp: return self.resp[rid]
            time.sleep(0.2)
        return {}
    def tool(self, name, args):
        r = self._call("tools/call", {"name": name, "arguments": args})
        res = r.get("result", {}) if r else {}
        if res.get("isError"):
            return {"_error": (res.get("content") or [{}])[0].get("text", "")}
        return res.get("structuredContent") or res
    def stop(self):
        try: self.p.terminate()
        except Exception: pass


def find_defect_section_part(mcp, project, space, document):
    """Best-effort: part id of the '§7.4 Inspection Defect' heading, for positioning."""
    page = 1
    while True:
        r = mcp.tool("read_document_parts", {"project_id": project, "space_id": space,
                                             "document_name": document, "page_size": 50, "page_number": page})
        if r.get("_error"): return None
        for it in r.get("items", []):
            t = (it.get("title") or "") + " " + (it.get("content") or "")
            if it.get("type") == "heading" and ("Inspection Defect" in t or "결함" in t):
                return it.get("id")
        if not r.get("has_more"): break
        page += 1
        if page > 30: break
    return None


def create_defect(mcp, project, space, document, title, description, dry_run):
    """Create an Ins_defect work item (title + description ONLY — all standard
    fields like severity/assignee/due/safety-relevance are left to the type's
    Polarion defaults / the human) and move it into the document's defect section."""
    ci = mcp.tool("create_work_item", {"project_id": project, "title": title, "type": DEFECT_TYPE,
                                       "description": description, "dry_run": dry_run})
    if ci.get("_error"):
        return None, ci["_error"]
    wid = ci.get("id") or ci.get("work_item_id") or (ci.get("data", {}) or {}).get("id")
    if dry_run:
        return wid or "(dry-run)", "created (dry-run)"
    if not wid:
        return None, f"create returned no id: {json.dumps(ci, ensure_ascii=False)[:160]}"
    prev = find_defect_section_part(mcp, project, space, document)
    mv = {"project_id": project, "work_item_id": wid, "target_space_id": space,
          "target_document_name": document, "dry_run": dry_run}
    if prev: mv["previous_part_id"] = prev
    mres = mcp.tool("move_work_item_to_document", mv)
    if mres.get("_error"):
        return wid, f"created {wid} but move failed: {mres['_error']}"
    return wid, f"created {wid} + moved into document (after {prev or 'default position'})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--filled", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--from-file", default="", help="offline: single defect @all dump for plan() test")
    args = ap.parse_args()

    filled = json.load(open(args.filled))
    project = filled.get("document", {}).get("project_id")
    review_wi = filled.get("source_wi")
    existing = {d.get("id"): d for d in (filled.get("inspection_defects") or [])}

    # collect defect write jobs from results
    jobs = []
    for r in filled.get("results", []):
        d = r.get("inspection_defect")
        if not d:
            continue
        action = d.get("write_action", "")
        target = d.get("target_defect_wi") or (r.get("existing_defect") or {}).get("id")
        jobs.append({"item": r.get("id"), "target": target, "action": action,
                     "content": d.get("description", ""),
                     "title": (d.get("title") or r.get("item", ""))})
    if not jobs:
        print("no inspection-defect write jobs"); return

    if args.from_file:  # offline plan test (no auth, no write)
        dwi = json.load(open(args.from_file))
        cur = dwi["data"]["attributes"].get("description", {})
        cur = cur.get("value", "") if isinstance(cur, dict) else (cur or "")
        for j in jobs:
            do, new, note = plan(cur, j["action"], j["content"])
            print(f"[{j['item']}->{j['target']}] action={j['action']} do_write={do} :: {note}")
            if do: print("   new_html:", new[:200])
        return

    global REST
    url, token = load_env(); REST = url + "/polarion/rest/v1"
    if not (project and review_wi):
        sys.exit("filled JSON needs document.project_id + source_wi")

    # ownership gate on the REVIEW work item
    rwi = rest_get(token, f"/projects/{project}/workitems/{review_wi}", {"fields[workitems]": "@all"})
    owners = wi_owners(rwi); me = my_user(token)
    if not owners:
        sys.exit("OWNERSHIP: review work item has no reviewer/assignee — refusing to write")
    if me not in owners:
        sys.exit(f"OWNERSHIP: current user '{me}' not an assigned reviewer/assignee {sorted(owners)} — refusing to write")
    print(f"ownership OK ({me}); {len(jobs)} candidate defect job(s)")

    space = filled.get("document", {}).get("space_id")
    document = filled.get("document", {}).get("document_name")
    # idempotency: don't create a defect whose title already exists in the document
    existing_titles = {(d.get("title") or "").strip() for d in (filled.get("inspection_defects") or [])}
    mcp = None

    for j in jobs:
        act = j["action"]
        if act == "needs-new":
            title = (j["title"] or "").strip()
            if not title:
                print(f"[{j['item']}] needs-new but no title -> skip"); continue
            if title in existing_titles:
                print(f"[{j['item']}] needs-new but a defect titled '{title[:40]}' already exists -> skip (idempotent)"); continue
            if not (space and document):
                print(f"[{j['item']}] needs-new but document space/name missing -> skip"); continue
            if mcp is None:
                mcp = MCP()
            wid, note = create_defect(mcp, project, space, document, title, j["content"], args.dry_run)
            existing_titles.add(title)  # avoid duplicate creates within this run
            print(f"[{j['item']}] CREATE {'(dry-run) ' if args.dry_run else ''}{note}")
            continue
        if not j["target"]:
            print(f"[{j['item']}] action={act} but no target defect WI -> skip"); continue
        dwi = rest_get(token, f"/projects/{project}/workitems/{j['target']}", {"fields[workitems]": "@all"})
        cur = dwi["data"]["attributes"].get("description", {})
        cur = cur.get("value", "") if isinstance(cur, dict) else (cur or "")
        do, new, note = plan(cur, j["action"], j["content"])
        if not do:
            print(f"[{j['item']}->{j['target']}] {note}"); continue
        if args.dry_run:
            print(f"[{j['item']}->{j['target']}] DRY-RUN {note}; new len={len(new)}"); continue
        code = rest_patch_desc(token, project, j["target"], new)
        print(f"[{j['item']}->{j['target']}] {note} -> PATCH HTTP {code}")

    if mcp:
        mcp.stop()


if __name__ == "__main__":
    main()
