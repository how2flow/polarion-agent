#!/usr/bin/env python3
"""Synthetic checklists and work items for the regression suite.

Deliberately NOT real Polarion data: the guards under test are about table
structure, not document content, so fixtures stay offline, tiny and free of
anything internal.
"""
import json

SPAN = ('<span style="font-size: 10pt;font-family: &#39;Segoe UI&#39;, Selawik, '
        '&#39;Open Sans&#39;, sans-serif;line-height: 1.5;">{}</span>')

# Header wording mirrors the live instance: these strings are what the column
# role keywords match against, so changing them changes what is tested.
H7 = ["분류", "리뷰 항목", "리뷰 방법 및 판정 기준", "리뷰 대상 (O/X)",
      "판정 결과 (Pass/Fail)", "리뷰어 의견", "리뷰 결함 상태 및 결과 (Open/Closed)"]
H6 = ["리뷰 항목", "리뷰 방법 및 판정 기준", "리뷰 대상 (O/X)",
      "판정 결과 (Pass/Fail)", "리뷰어 의견", "리뷰 결함 상태 및 결과 (Open/Closed)"]


def row7(i, applicable="", result="", comment="", defect="", category="스타일"):
    return [category, f"item {i}?", f"criteria {i} (method) check",
            applicable, result, comment, defect]


def row6(i, applicable="", result="", comment="", defect=""):
    return [f"item {i}?", f"criteria {i} (method) check",
            applicable, result, comment, defect]


def table_html(header, rows, wrap_cells=True):
    """A checklist table: header row in <th>, data rows in <td>.

    Data cells wrap their text in a <span> like the live editor does, so the
    parsers are exercised on markup, not bare text.
    """
    out = ["<table>", "<tr>"]
    out += [f'<th style="font-weight: bold;">{h}</th>' for h in header]
    out.append("</tr>")
    for r in rows:
        out.append("<tr>")
        for c in r:
            inner = (SPAN.format(c) if (wrap_cells and c) else c)
            out.append(f'<td style="border: 1px solid #CCC;">{inner}</td>')
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


def work_item(wi_id="PRJ-1", wtype="REVIEW", owner="tester",
              checklist=None, project="proj", field="checklist"):
    data = {"id": f"{project}/{wi_id}", "type": "workitems",
            "attributes": {"type": wtype, "title": f"{wi_id} review"}}
    if checklist is not None:
        data["attributes"][field] = {"type": "text/html", "value": checklist}
    if owner:
        data["relationships"] = {"reviewer": {"data": {"id": owner, "type": "users"}}}
    return {"data": data}


def payload_from(header, rows, roles=None):
    """A fetch-shaped payload (what compare_to_template consumes)."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "scripts"))
    import checklist_columns as C
    return {"columns": {"header": header, "roles": roles or C.resolve(header)},
            "template_rows": rows, "rows": [header] + rows,
            "item_count": len(rows) + 1, "data_row_count": len(rows)}


def findings(rows, roles, source_wi="PRJ-1", project="proj", result="Pass"):
    """A filled-findings JSON shaped the way doc-review.md specifies."""
    t = roles["template"]
    return {
        "document": {"project_id": project},
        "source_wi": source_wi,
        "columns": {"header": None, "roles": roles},
        "template_rows": rows,
        "results": [{"id": f"C-{i+1:03d}",
                     "category": r[t[0]] if len(t) > 2 else "",
                     "item": r[t[-2]], "criteria": r[t[-1]],
                     "applicable": "O", "result": result,
                     "comment": f"checked {i+1}"}
                    for i, r in enumerate(rows)],
    }


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    return path
