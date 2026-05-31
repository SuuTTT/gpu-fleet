"""
gpu_fleet.core - discover Vast.ai instances and probe their live status.
Single source of truth for "what GPUs do I have and which are free".
"""
from __future__ import annotations

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

SSH_KEY = os.path.expanduser(os.environ.get("GPU_FLEET_KEY", "~/.ssh/vastai_id_ed25519"))
ASSIGN_FILE = os.path.expanduser(os.environ.get("GPU_FLEET_ASSIGNMENTS", "~/gpu-fleet/assignments.json"))

GPU_FREE_UTIL = 10      # GPU utilisation % below this
GPU_FREE_MEM = 500      # GPU memory used (MiB) below this
DISK_FULL_PCT = 90      # disk %used below this (else flagged)
SSH_TIMEOUT = 14        # seconds per host; dead hosts must not stall the fleet

_PROBE = r'''
G=$(nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits 2>/dev/null | head -1)
C=$(nproc 2>/dev/null)
L=$(cut -d' ' -f1 /proc/loadavg 2>/dev/null)
R=$(free -m | awk '/Mem:/{print $3"/"$2}')
D=$(df -P / | awk 'NR==2{print $3"/"$2" "$5}')
J=$(nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null | grep -c . || echo 0)
E=$(python3 - <<'PY' 2>/dev/null
import sys
try:
    import importlib.metadata as M
    def v(p):
        try: return M.version(p)
        except Exception: return None
except Exception:
    def v(p): return None
libs=[('torch','torch'),('numpy','numpy'),('pandas','pandas'),('sklearn','scikit-learn'),('statsmodels','statsmodels'),('jax','jax'),('transformers','transformers'),('tf','tensorflow')]
parts=["py%d.%d"%sys.version_info[:2]]
for s,pkg in libs:
    ver=v(pkg)
    if ver: parts.append(s+ver)
print(";".join(parts))
PY
)
echo "GPU=$G|CPU=$C|LOAD=$L|RAM=$R|DISK=$D|JOBS=$J|ENV=$E"
'''


def live_instances():
    try:
        r = subprocess.run(["vastai", "show", "instances", "--raw"],
                           capture_output=True, text=True, timeout=30)
        return json.loads(r.stdout)
    except Exception:
        return []


def load_assignments():
    try:
        d = json.load(open(ASSIGN_FILE))
        return {k: v for k, v in d.items() if not k.startswith("_")}
    except Exception:
        return {}


def _ssh_base(port):
    return ["ssh", "-i", SSH_KEY, "-p", str(port),
            "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=3",
            "-o", "ServerAliveCountMax=2"]


def _probe_once(host, port):
    try:
        r = subprocess.run(_ssh_base(port) + [f"root@{host}", _PROBE],
                           capture_output=True, text=True, timeout=SSH_TIMEOUT)
        for ln in r.stdout.splitlines():
            if ln.startswith("GPU="):
                return _parse(ln)
        return None
    except Exception:
        return None


def probe(host, port):
    """Vast.ai's API sometimes reports the proxy port; the real sshd is at
    port+1. Try the reported port, then port+1; record what worked."""
    for cand in (port, port + 1):
        res = _probe_once(host, cand)
        if res is not None:
            res["ssh_port"] = cand
            return res
    return None


def _parse(line):
    d = {}
    for part in line.split("|"):
        if "=" in part:
            k, v = part.split("=", 1)
            d[k] = v.strip()
    out = {}
    g = d.get("GPU", "")
    if g and "," in g:
        try:
            u, mu, mt = [x.strip() for x in g.split(",")[:3]]
            out["util"] = int(float(u)); out["mem_used"] = int(float(mu)); out["mem_total"] = int(float(mt))
        except Exception:
            pass
    out["cpu"] = d.get("CPU", "?")
    out["load"] = d.get("LOAD", "?")
    out["ram"] = d.get("RAM", "?")
    disk = d.get("DISK", "")
    if disk:
        sz, pct = (disk.split() + ["?"])[:2]
        out["disk"] = sz; out["disk_pct"] = pct
    out["jobs"] = int(d.get("JOBS", "0") or 0)
    out["env"] = d.get("ENV", "")
    return out


def verdict(inst_status, p):
    if inst_status != "running":
        return "OFFLINE"
    if p is None:
        return "UNREACHABLE"
    busy = (p.get("util", 0) >= GPU_FREE_UTIL or p.get("mem_used", 0) >= GPU_FREE_MEM
            or p.get("jobs", 0) > 0)
    try:
        disk_full = int(str(p.get("disk_pct", "0%")).rstrip("%")) >= DISK_FULL_PCT
    except Exception:
        disk_full = False
    if disk_full:
        return "FREE*diskfull" if not busy else "BUSY*diskfull"
    return "BUSY" if busy else "FREE"


def collect():
    """Probe every running instance in parallel; return a list of row dicts."""
    insts = live_instances()
    assign = load_assignments()
    running = [(i, i.get("ssh_host"), i.get("ssh_port")) for i in insts
              if i.get("actual_status") == "running" and i.get("ssh_host")]
    probes = {}
    with ThreadPoolExecutor(max_workers=20) as ex:
        futs = {ex.submit(probe, h, port): i["id"] for i, h, port in running}
        for f in futs:
            probes[futs[f]] = f.result()
    rows = []
    for i in insts:
        iid = i["id"]; p = probes.get(iid); a = assign.get(str(iid), {})
        eff_port = (p or {}).get("ssh_port", i.get("ssh_port", "?"))
        rows.append({
            "id": iid, "gpu": i.get("gpu_name", "?"), "status": i.get("actual_status", "?"),
            "host": i.get("ssh_host", "?"), "port": eff_port,
            "ssh": f"{i.get('ssh_host','?')}:{eff_port}",
            "dph": i.get("dph_total", 0), "cuda": i.get("cuda_max_good", "?"),
            "project": a.get("project", "-"), "note": a.get("note", ""),
            "probe": p, "verdict": verdict(i.get("actual_status"), p),
        })
    rows.sort(key=lambda r: (r["verdict"] != "FREE", r["status"] != "running", -float(r["dph"] or 0)))
    return rows


def free_gpus(rows=None):
    rows = rows if rows is not None else collect()
    return [r for r in rows if r["verdict"] == "FREE"]
