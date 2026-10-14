#!/usr/bin/env python3
"""polarion_config.py — load the instance/organization profile.

Every vendor-specific name (work item types, the checklist field, the Korean
header keywords that identify reviewer columns, the defect section headings, and
where the official template documents live) is data in config/polarion.json, not
code. A profile is chosen from the target document's project_id, so
`run.sh '<url>'` needs no extra flags: parse the URL -> project_id -> profile.

Usage:
    import polarion_config as cfg
    p = cfg.profile_for(project_id="bsp_module")     # or name="telechips-bsp"
    cfg.review_type(p), cfg.column_rules(p), cfg.templates(p) ...
"""
import json, os, sys

_CACHE = {}


def repo_root(start=None):
    root = os.path.dirname(os.path.abspath(start or __file__))
    while root != "/" and not os.path.exists(os.path.join(root, ".polarion-root")):
        root = os.path.dirname(root)
    return root


def config_path():
    return os.environ.get("POLARION_CONFIG") or os.path.join(repo_root(), "config", "polarion.json")


def load(path=None):
    path = path or config_path()
    if path not in _CACHE:
        if not os.path.exists(path):
            sys.exit(f"config not found: {path} (set POLARION_CONFIG to override)")
        with open(path, encoding="utf-8") as f:
            _CACHE[path] = json.load(f)
    return _CACHE[path]


def profile_for(project_id=None, name=None, path=None, quiet=False):
    """Resolve a profile: explicit name > project_id match > default_profile."""
    conf = load(path)
    profiles = conf.get("profiles") or {}
    if not profiles:
        sys.exit("config has no profiles")
    if name:
        if name not in profiles:
            sys.exit(f"unknown profile '{name}' (have: {', '.join(sorted(profiles))})")
        return dict(profiles[name], _name=name, _matched=True)
    if project_id:
        for n, p in profiles.items():
            if project_id in ((p.get("match") or {}).get("project_ids") or []):
                return dict(p, _name=n, _matched=True)
    n = conf.get("default_profile") or sorted(profiles)[0]
    if n not in profiles:
        sys.exit(f"default_profile '{n}' is not defined (have: {', '.join(sorted(profiles))})")
    if project_id and not quiet:
        print(f"  [warn] no profile matches project '{project_id}' — using default profile '{n}'",
              file=sys.stderr)
    # Reached only by falling back to default_profile, which by definition did
    # not match: callers acting on PROJECT-SPECIFIC data (template locations
    # above all) must not treat it as authoritative.
    return dict(profiles[n], _name=n, _matched=False)


# ---- accessors (each tolerates a missing key) ----
def name(p):            return p.get("_name", "?")
def review_type(p):     return (p.get("work_item_types") or {}).get("review", "REVIEW")
def defect_type(p):     return (p.get("work_item_types") or {}).get("defect", "Ins_defect")
def checklist_field(p): return p.get("checklist_field", "checklist")
def defect_headings(p): return p.get("defect_section_headings") or ["Inspection Defect", "결함"]
def templates(p):       return p.get("templates") or {}


def fail_count_field(p):
    """Work item field holding the failed-item count, or None when unset."""
    return p.get("fail_count_field") or None


def column_rules(p):
    """{role: (require, forbid)} for checklist_columns.resolve(); None if unset."""
    raw = p.get("column_roles")
    if not raw:
        return None
    return {role: (spec.get("require", ""), spec.get("forbid"))
            for role, spec in raw.items() if isinstance(spec, dict)}


def template_document(p, doctype):
    """Document name of the official template for a doctype, or None."""
    return ((templates(p).get("doctypes")) or {}).get(doctype)


def cache_dir(profile_name, root=None):
    """Where fetched template checklists are cached (gitignored).

    Keyed by PROFILE, not by the template project id — so a renamed or
    temporarily unreachable template project still falls back to the last good
    fetch for this profile."""
    d = os.path.join(root or repo_root(), ".cache", "templates", profile_name or "_")
    os.makedirs(d, exist_ok=True)
    return d


if __name__ == "__main__":
    pid = sys.argv[1] if len(sys.argv) > 1 else None
    p = profile_for(project_id=pid)
    print(json.dumps({
        "profile": name(p), "review_type": review_type(p), "defect_type": defect_type(p),
        "checklist_field": checklist_field(p), "column_rules": column_rules(p),
        "defect_headings": defect_headings(p), "templates": templates(p),
    }, ensure_ascii=False, indent=2))
