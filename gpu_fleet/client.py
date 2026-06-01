"""
gpu_fleet.client - report a research project's fleet usage to the center node.

General (not tied to any project): any job on any box calls this to tell the
central dashboard "project P is now running on boxes X,Y" and "P finished".

Transport (auto, in order):
  1. HTTP  if FLEET_CENTER set (e.g. "http://54.251.156.216:5055") -> POST /ingest
  2. SSH   if FLEET_CENTER_SSH set (e.g. "ubuntu@54.251.156.216 -i ~/.ssh/key")
           -> ssh center 'curl -s localhost:5055/ingest ...'  (works with only
              port 22 open; the ingest still listens on the center's localhost)
Auth: FLEET_TOKEN (appended as ?token=...). FLEET_INGEST_PORT overrides 5055.

Library:
    from gpu_fleet.client import report
    report("start", project="mahjong-pbt", boxes=["36994217"], detail="gen 1/8")
    report("done",  project="mahjong-pbt", detail="champion gen2 +968")

CLI (via `gpufleet report ...`): see gpu_fleet/cli.py.
"""
from __future__ import annotations
import json, os, shlex, subprocess, urllib.request

INGEST_PORT = os.environ.get("FLEET_INGEST_PORT", "5055")

def _payload(event, project, boxes, detail, note, extra, ts):
    d = {"event": event, "project": project,
         "boxes": [str(b) for b in (boxes or [])],
         "detail": detail or "", "note": note or "", "ts": ts}
    if extra:
        d.update(extra)
    return d

def _via_http(center, token, payload):
    url = center.rstrip("/") + "/ingest" + (f"?token={token}" if token else "")
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return f"http {r.status}"

def _via_ssh(ssh_target, token, payload):
    # ssh <target> 'curl -s -XPOST localhost:PORT/ingest?token=.. -d <json>'
    q = f"?token={token}" if token else ""
    body = shlex.quote(json.dumps(payload))
    remote = (f"curl -s -m 10 -X POST "
              f"'http://localhost:{INGEST_PORT}/ingest{q}' "
              f"-H 'Content-Type: application/json' -d {body}")
    parts = shlex.split(ssh_target)                 # "user@host -i key" -> tokens
    cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
           "-o", "ConnectTimeout=12"] + parts[1:] + [parts[0], remote]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return f"ssh err rc={r.returncode} {r.stderr[-120:]}"
    return "ssh ok: " + (r.stdout[:120] or "(no body)")

def report(event, project, boxes=None, detail="", note="", extra=None, ts=None):
    """event: start|ping|done|failed. Returns a status string (never raises)."""
    import time
    ts = ts or time.strftime("%Y-%m-%d %H:%M:%S")
    payload = _payload(event, project, boxes, detail, note, extra, ts)
    token = os.environ.get("FLEET_TOKEN", "")
    center = os.environ.get("FLEET_CENTER", "").strip()
    ssh_t = os.environ.get("FLEET_CENTER_SSH", "").strip()
    errs = []
    if center:
        try:
            return _via_http(center, token, payload)
        except Exception as e:
            errs.append(f"http: {e}")
    if ssh_t:
        try:
            return _via_ssh(ssh_t, token, payload)
        except Exception as e:
            errs.append(f"ssh: {e}")
    if not center and not ssh_t:
        return "not configured (set FLEET_CENTER or FLEET_CENTER_SSH)"
    return "FAILED: " + "; ".join(errs)
