#!/usr/bin/env python3
"""Shared Polarion REST helper with ALM-seat-limit retry.

Polarion's floating ALM license (15 concurrent users) returns HTTP 401 with a
body like "... the limit of 15 concurrent users with ALM license was reached ..."
This is TRANSIENT. This helper retries ONLY that case (poll every SEAT_POLL up to
SEAT_WAIT); every other 401 (bad/expired token, "No access token") and all other
HTTP errors fail immediately — so a dead token never loops forever.

Env:
  POLARION_SEAT_POLL   poll interval seconds (default 30)
  POLARION_SEAT_WAIT   max total wait seconds (default 7200 = 2h)

Used by fetch_checklist.py / write_checklist.py / write_defects.py at the exact
fetch/write moments (the only points that consume a seat).
"""
import json, os, sys, time
from urllib import request, error

SEAT_POLL = int(os.environ.get("POLARION_SEAT_POLL", "30"))
SEAT_WAIT = int(os.environ.get("POLARION_SEAT_WAIT", "7200"))  # 2 hours


def is_seat_limited(code, body):
    b = (body or "").lower()
    return code == 401 and "concurrent" in b and "user" in b


def call(method, url, token, body=None):
    """REST call with seat-limit retry.
    Returns (status_code, parsed_json_or_None). Non-seat HTTP/transport errors
    raise SystemExit (fatal). Seat-limit 401 retries until SEAT_WAIT elapses."""
    data = json.dumps(body).encode() if body is not None else None
    deadline = time.time() + SEAT_WAIT
    attempt = 0
    while True:
        attempt += 1
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = request.Request(url, data=data, method=method, headers=headers)
        try:
            with request.urlopen(req, timeout=30) as r:
                raw = r.read().decode("utf-8", "replace")
                return r.status, (json.loads(raw) if raw.strip() else None)
        except error.HTTPError as e:
            eb = e.read().decode("utf-8", "replace")
            if is_seat_limited(e.code, eb) and time.time() < deadline:
                left = int(deadline - time.time())
                print(f"[seat] ALM concurrent-user limit (401) — retrying in {SEAT_POLL}s "
                      f"(attempt {attempt}, ~{left}s of {SEAT_WAIT}s budget left)", file=sys.stderr)
                time.sleep(SEAT_POLL)
                continue
            if is_seat_limited(e.code, eb):
                sys.exit(f"seat-limit persisted beyond {SEAT_WAIT}s budget on {method} {url.split('?')[0]} — giving up")
            sys.exit(f"REST {e.code} on {method} {url.split('?')[0]}: {eb[:200]}")
        except error.URLError as e:
            sys.exit(f"REST transport error on {method} {url.split('?')[0]}: {e}")


def get(token, url):
    _, js = call("GET", url, token)
    return js


def patch(token, url, body):
    code, _ = call("PATCH", url, token, body)
    return code
