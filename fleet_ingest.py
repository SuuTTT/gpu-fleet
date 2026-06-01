"""
fleet_ingest.py — tiny webhook receiver for the central gpu-fleet dashboard.

Run this ON THE AWS NODE (54.251.156.216) next to `gpufleet web`. It receives
status POSTs from remote jobs (train/fleet_notify.py FLEET_WEBHOOK) and writes
straight into the files the dashboard already reads:
  • ~/gpu-fleet/assignments.json   -> tags the job's box IDs (project column)
  • ~/gpu-fleet/notifications.log   -> appended line per event (tail to watch)

You do NOT need to modify or re-pull the gpu-fleet repo — this just edits the
same assignments.json the dashboard reads. Run it standalone.

Usage on AWS:
    export FLEET_TOKEN=some-shared-secret          # optional but recommended
    python3 ~/gpu-fleet/fleet_ingest.py 5055        # listens on 0.0.0.0:5055
Then OPEN inbound TCP 5055 in the EC2 security group (source = the vast box IP,
e.g. 174.115.164.43/32, or 0.0.0.0/0 if you don't mind it public).

The remote side then sets:
    FLEET_WEBHOOK="http://54.251.156.216:5055/ingest?token=some-shared-secret"
"""
import json, os, sys, time, http.server, socketserver

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5055
# Robust to user/home: default to the assignments.json sitting NEXT TO this script
# (i.e. the repo dir), and honor GPU_FLEET_ASSIGNMENTS — the SAME env the dashboard's
# gpu_fleet/core.py reads — so both sides can be pinned to one file explicitly.
_HERE = os.path.dirname(os.path.abspath(__file__))
ASSIGN = os.environ.get("GPU_FLEET_ASSIGNMENTS") or os.path.join(_HERE, "assignments.json")
NOTIFY_LOG = os.environ.get("FLEET_LOG") or os.path.join(_HERE, "notifications.log")
STATUS_FILE = os.environ.get("FLEET_STATUS") or os.path.join(_HERE, "fleet_status.json")
TOKEN = os.environ.get("FLEET_TOKEN", "")
print(f"[fleet-ingest] assignments -> {ASSIGN}", flush=True)
print(f"[fleet-ingest] status      -> {STATUS_FILE}", flush=True)
print(f"[fleet-ingest] notifications -> {NOTIFY_LOG}", flush=True)

def _load(path, default):
    try:
        return json.load(open(path)) if os.path.exists(path) else default
    except Exception:
        return default

def handle_event(d):
    project = d.get("project", "remote-job")
    boxes = [str(b) for b in d.get("boxes", [])]
    event = d.get("event", "?")                          # start|ping|done|failed (any string ok)
    detail = d.get("detail", "") or (("champ=" + d["champion"]) if d.get("champion") else "")
    ts = d.get("ts") or time.strftime("%Y-%m-%d %H:%M:%S")
    note = f"{project} {event} {ts}" + (f" {detail}" if detail else "")
    done = event in ("done", "finished", "failed")
    # 1) tag boxes in assignments.json (dashboard 'project' column). On done, mark freed.
    a = _load(ASSIGN, {})
    for b in boxes:
        a[b] = {"project": (project if not done else f"{project}(done)"), "note": note}
    json.dump(a, open(ASSIGN, "w"), indent=2)
    # 2) maintain per-project live status (the general 'what is each project doing' view)
    s = _load(STATUS_FILE, {"projects": {}})
    s.setdefault("projects", {})
    pr = s["projects"].get(project, {"runs": []})
    if event in ("start", "running") and not pr.get("started"):
        pr["started"] = ts
    pr.update({"state": event, "boxes": boxes or pr.get("boxes", []),
               "detail": detail, "updated": ts, "host": d.get("host", pr.get("host", ""))})
    pr.setdefault("runs", []).append({"event": event, "ts": ts, "detail": detail})
    pr["runs"] = pr["runs"][-10:]
    s["projects"][project] = pr
    s["updated"] = ts
    json.dump(s, open(STATUS_FILE, "w"), indent=2)
    # 3) append a notification line
    line = f"{ts}  {event:8s}  {project}  boxes={','.join(boxes)}" + (f"  {detail}" if detail else "")
    open(NOTIFY_LOG, "a").write(line + "\n")
    return note, len(boxes)

class H(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        if TOKEN and ("token=" + TOKEN) not in self.path:
            return self._send(403, '{"error":"bad token"}')
        try:
            n = int(self.headers.get("Content-Length", 0))
            d = json.loads(self.rfile.read(n) or b"{}")
            note, k = handle_event(d)
            self._send(200, json.dumps({"ok": True, "tagged": k, "note": note}))
        except Exception as e:
            self._send(400, json.dumps({"error": str(e)}))
    def do_GET(self):
        try:
            tail = "".join(open(NOTIFY_LOG).readlines()[-20:])
        except Exception:
            tail = "(no notifications yet)"
        self._send(200, json.dumps({"status": "fleet-ingest up", "recent": tail}))
    def log_message(self, *a): pass

if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    print(f"fleet-ingest on 0.0.0.0:{PORT}  (token {'set' if TOKEN else 'OFF'})", flush=True)
    print(f"  remote sets FLEET_WEBHOOK=http://54.251.156.216:{PORT}/ingest"
          + (f"?token={TOKEN}" if TOKEN else ""), flush=True)
    with socketserver.TCPServer(("0.0.0.0", PORT), H) as srv:
        srv.serve_forever()
