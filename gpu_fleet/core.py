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
NG=$(nvidia-smi --query-gpu=index --format=csv,noheader 2>/dev/null | grep -c .)
GALL=$(nvidia-smi --query-gpu=index,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits 2>/dev/null | tr '\n' ';')
C=$(nproc 2>/dev/null)
L=$(cut -d' ' -f1 /proc/loadavg 2>/dev/null)
R=$(free -m | awk '/Mem:/{print $3"/"$2}')
D=$(df -P / | awk 'NR==2{print $3"/"$2" "$5}')
DFREE=$(df -BG -P / | awk 'NR==2{gsub("G","",$4); print $4}')
J=$(nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null | grep -c . || echo 0)
OS=$(. /etc/os-release 2>/dev/null && echo "$VERSION_ID" || echo "?")
GCC=$(command -v gcc >/dev/null 2>&1 && echo y || echo n)
PYDEV=$(ls /usr/include/python3*/Python.h /opt/conda/include/python3*/Python.h 2>/dev/null | head -1 >/dev/null 2>&1 && echo y || echo n)
NET=$(timeout 4 curl -sI -o /dev/null -w '%{http_code}' https://pypi.org 2>/dev/null || echo 0)
# Pick the "richest" python: jobs often run from a venv/conda, not /usr/bin/python3.
# Scan candidate interpreters with a one-line python -c (survives nested SSH heredoc).
PYCANDS=$(ls /root/venv/bin/python /root/*/bin/python /opt/conda/bin/python /opt/conda/envs/*/bin/python /usr/bin/python3 2>/dev/null | awk '!seen[$0]++')
PYSNIP='import sys,importlib.metadata as M
def v(p):
 try: return M.version(p)
 except Exception: return None
L=[("torch","torch"),("jax","jax"),("mjx","mujoco-mjx"),("mujoco","mujoco"),("playground","playground"),("mujoco_playground","mujoco_playground"),("brax","brax"),("flax","flax"),("numpy","numpy"),("pandas","pandas"),("sklearn","scikit-learn"),("statsmodels","statsmodels"),("transformers","transformers"),("tf","tensorflow")]
f=[s+x for s,p in L for x in [v(p)] if x]
pa=sys.argv[1]
t="venv" if ("/venv/" in pa or "/envs/" in pa or "conda" in pa) else "sys"
print("%d\t%s|py%d.%d[%s];%s"%(len(f),pa,sys.version_info[0],sys.version_info[1],t,";".join(f)))'
E=$(for PYB in $PYCANDS; do "$PYB" -c "$PYSNIP" "$PYB" 2>/dev/null; done | sort -rn | head -1 | cut -f2-)
echo "GPU=$G|NG=$NG|GALL=$GALL|CPU=$C|LOAD=$L|RAM=$R|DISK=$D|DFREE=$DFREE|JOBS=$J|OS=$OS|GCC=$GCC|PYDEV=$PYDEV|NET=$NET|ENV=$E"
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
    # ENV is last and itself contains '|' (pybin|pyver;libs), so peel it off first.
    if "|ENV=" in line:
        line, env_val = line.split("|ENV=", 1)
        d["ENV"] = env_val.strip()
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
    # multi-GPU: parse every GPU on the box (GALL = "idx,util,used,total;...")
    try:
        out["ngpu"] = int(d.get("NG", "1") or 1)
    except Exception:
        out["ngpu"] = 1
    gpus = []
    for chunk in (d.get("GALL", "") or "").split(";"):
        chunk = chunk.strip()
        if not chunk or "," not in chunk:
            continue
        try:
            idx, u, mu, mt = [x.strip() for x in chunk.split(",")[:4]]
            gpus.append({"idx": int(idx), "util": int(float(u)),
                         "mem_used": int(float(mu)), "mem_total": int(float(mt))})
        except Exception:
            pass
    if gpus:
        out["gpus"] = gpus
        # box-level summary: max util across GPUs, total mem used
        out["util"] = max(x["util"] for x in gpus)
        out["mem_used"] = sum(x["mem_used"] for x in gpus)
        out["mem_total"] = sum(x["mem_total"] for x in gpus)
    out["cpu"] = d.get("CPU", "?")
    out["load"] = d.get("LOAD", "?")
    out["ram"] = d.get("RAM", "?")
    disk = d.get("DISK", "")
    if disk:
        sz, pct = (disk.split() + ["?"])[:2]
        out["disk"] = sz; out["disk_pct"] = pct
    out["jobs"] = int(d.get("JOBS", "0") or 0)
    out["disk_free"] = d.get("DFREE", "?")        # GiB free on /
    out["os"] = d.get("OS", "?")                  # ubuntu VERSION_ID
    out["gcc"] = d.get("GCC", "?")                # y/n build toolchain
    out["pydev"] = d.get("PYDEV", "?")            # y/n Python.h present
    out["net"] = d.get("NET", "?")                # outbound HTTP code to pypi (200=ok)
    env = d.get("ENV", "")                         # "<pybin>|py3.12[venv];jax...;..."
    if "|" in env:
        out["pybin"], out["env"] = env.split("|", 1)
    else:
        out["pybin"], out["env"] = "", env
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
        gpu_name = i.get("gpu_name", "?")
        ngpu = (p or {}).get("ngpu", 1)
        if ngpu and ngpu > 1:
            gpu_name = f"{gpu_name} x{ngpu}"
        rows.append({
            "id": iid, "gpu": gpu_name, "status": i.get("actual_status", "?"),
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
