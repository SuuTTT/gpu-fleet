"""
gpu_fleet.web - non-blocking web dashboard.

A background thread refreshes the fleet snapshot every `refresh` seconds, so HTTP
requests are answered instantly from cache (the page is never blank waiting on
SSH). HTML is built with plain string concatenation.
"""
from __future__ import annotations

import html
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import core

VCOLOR = {"FREE": "#1a7f37", "BUSY": "#9a6700", "OFFLINE": "#6e7781", "UNREACHABLE": "#cf222e"}
_STATE = {"rows": None, "ts": 0.0, "err": None}


def _vcolor(v):
    for k, c in VCOLOR.items():
        if v.startswith(k):
            return c
    return "#333"


def _refresher(refresh):
    while True:
        try:
            _STATE["rows"] = core.collect()
            _STATE["ts"] = time.time()
            _STATE["err"] = None
        except Exception as e:
            _STATE["err"] = str(e)
        time.sleep(refresh)


def build_html(rows, ts, refresh, err):
    if rows is None:
        return ("<!doctype html><html><head><meta charset='utf-8'>"
                "<meta http-equiv='refresh' content='3'><title>GPU Fleet</title></head>"
                "<body style='font-family:sans-serif;margin:40px'><h1>GPU Fleet</h1>"
                "<p>Collecting first snapshot (probing hosts, ~15s). Auto-refreshing...</p>"
                + (f"<p style='color:#cf222e'>last error: {html.escape(err)}</p>" if err else "")
                + "</body></html>")
    n_free = sum(1 for r in rows if r["verdict"] == "FREE")
    n_online = sum(1 for r in rows if r["status"] == "running")
    total = sum(float(r["dph"] or 0) for r in rows if r["status"] == "running")
    age = int(time.time() - ts)

    p = ["<!doctype html><html><head><meta charset='utf-8'>",
         "<meta http-equiv='refresh' content='%d'>" % refresh,
         "<title>GPU Fleet</title><style>",
         "body{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:18px;background:#fafbfc;color:#24292f}",
         "h1{font-size:20px;margin:0 0 6px}.sub{color:#57606a;font-size:13px;margin-bottom:14px}",
         "table{border-collapse:collapse;width:100%;font-size:13px;background:#fff}",
         "th,td{padding:6px 9px;text-align:left;border-bottom:1px solid #eaecef;white-space:nowrap}",
         "th{background:#f6f8fa;font-size:12px}tr:hover{background:#f6f8fa}",
         ".pill{display:inline-block;padding:2px 9px;border-radius:10px;color:#fff;font-size:12px;margin-right:6px}",
         "</style></head><body><h1>GPU Fleet Dashboard</h1><div class='sub'>",
         "<span class='pill' style='background:#1a7f37'>FREE %d</span>" % n_free,
         "<span class='pill' style='background:#0969da'>online %d/%d</span>" % (n_online, len(rows)),
         "<span class='pill' style='background:#57606a'>$%.3f/hr</span>" % total,
         "data %ds old &middot; auto-refresh %ds</div>" % (age, refresh),
         "<table><thead><tr>"]
    for h in ["ID", "GPU", "state", "verdict", "GPU util", "gpu-mem MiB", "cpu", "load",
              "ram MB", "disk", "jobs", "cuda", "project", "env (py + libs)", "ssh", "$/hr"]:
        p.append("<th>%s</th>" % h)
    p.append("</tr></thead><tbody>")
    for r in rows:
        pr = r.get("probe") or {}
        util = pr.get("util")
        if util is None:
            ucell = "-"
        else:
            uc = "#cf222e" if util >= 50 else ("#9a6700" if util >= 10 else "#1a7f37")
            ucell = ("<div style='background:#eee;border-radius:3px;width:55px;display:inline-block;vertical-align:middle'>"
                     "<div style='background:%s;width:%d%%;height:10px;border-radius:3px'></div></div> %d%%"
                     % (uc, min(util, 100), util))
        gmem = ("%s/%s" % (pr.get("mem_used", "?"), pr.get("mem_total", "?"))) if pr else "-"
        disk = ("%s %s" % (pr.get("disk", "-"), pr.get("disk_pct", ""))).strip() if pr else "-"
        try:
            dpct = int(str(pr.get("disk_pct", "0%")).rstrip("%"))
        except Exception:
            dpct = 0
        dstyle = " style='color:#cf222e;font-weight:bold'" if dpct >= 90 else ""
        env = html.escape(pr.get("env", "") or "")
        row = ("<tr><td>%s</td><td><b>%s</b></td><td>%s</td>"
               "<td><span style='color:%s;font-weight:bold'>%s</span></td><td>%s</td><td>%s</td>"
               "<td>%s</td><td>%s</td><td>%s</td><td%s>%s</td><td>%s</td><td>%s</td>"
               "<td>%s</td><td style='font-size:11px'>%s</td><td>%s</td><td>%.3f</td></tr>") % (
            r["id"], html.escape(r["gpu"]), html.escape(r["status"]),
            _vcolor(r["verdict"]), r["verdict"], ucell, gmem,
            pr.get("cpu", "-"), pr.get("load", "-"), pr.get("ram", "-"),
            dstyle, disk, pr.get("jobs", "-"), html.escape(str(r.get("cuda", "?"))),
            html.escape(str(r.get("project", "-"))), env, html.escape(r["ssh"]),
            float(r["dph"] or 0))
        p.append(row)
    p.append("</tbody></table>")
    p.append("<p style='color:#57606a;font-size:12px'>FREE = util&lt;%d%% &amp; gpu-mem&lt;%dMiB &amp; no GPU job &amp; disk&lt;%d%%. "
             "Project labels: edit assignments.json. JSON API at <code>/api</code>.</p>"
             % (core.GPU_FREE_UTIL, core.GPU_FREE_MEM, core.DISK_FULL_PCT))
    p.append("</body></html>")
    return "".join(p)


def serve(port=5050, refresh=30):
    threading.Thread(target=_refresher, args=(refresh,), daemon=True).start()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass
        def do_GET(self):
            if self.path.rstrip("/") in ("/api", "/api.json"):
                body = json.dumps(_STATE["rows"] or [], default=str).encode()
                ct = "application/json"
            else:
                body = build_html(_STATE["rows"], _STATE["ts"], refresh, _STATE["err"]).encode()
                ct = "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("0.0.0.0", port), H)
    print("GPU Fleet web dashboard on http://0.0.0.0:%d  (Ctrl-C to stop)" % port, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
