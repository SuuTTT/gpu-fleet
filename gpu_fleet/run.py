"""
gpu_fleet.run - scale an experiment onto a free GPU.

Picks (or accepts) a FREE GPU, rsyncs a local project directory to it, optionally
installs requirements, then launches a command detached (survives disconnect) and
returns immediately. Logs land in <remote_workspace>/<run>.log on the worker.
"""
from __future__ import annotations

import shlex
import subprocess
import time

from . import core


def _ssh(host, port, cmd, timeout=60):
    args = core._ssh_base(port) + [f"root@{host}", cmd]
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout.strip()


def pick_free(min_vram_mib=0, gpu_substr=None, rows=None):
    """Return the cheapest FREE GPU matching filters, or None."""
    free = core.free_gpus(rows)
    out = []
    for r in free:
        p = r.get("probe") or {}
        if min_vram_mib and p.get("mem_total", 0) < min_vram_mib:
            continue
        if gpu_substr and gpu_substr.lower() not in r["gpu"].lower():
            continue
        out.append(r)
    out.sort(key=lambda r: float(r["dph"] or 0))
    return out[0] if out else None


def sync(host, port, local_dir, remote_dir, excludes=("__pycache__", "*.pyc", ".git")):
    """rsync a local directory to the worker."""
    rsh = " ".join(core._ssh_base(port))
    ex = []
    for e in excludes:
        ex += ["--exclude", e]
    local = local_dir.rstrip("/") + "/"
    cmd = ["rsync", "-az", "-e", rsh] + ex + [local, f"root@{host}:{remote_dir}/"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    return r.returncode == 0, r.stderr.strip()


def launch(host, port, remote_dir, command, run_name=None, pip=None):
    """Install (optional) then launch `command` detached under remote_dir.
    Returns (ok, run_name, logpath)."""
    run_name = run_name or ("run_%d" % int(time.time()))
    log = f"{remote_dir}/{run_name}.log"
    pip_line = f"pip install -q {pip} >> {log} 2>&1; " if pip else ""
    script = (f"mkdir -p {remote_dir}; cd {remote_dir}; "
              f"{pip_line}"
              f"setsid bash -c {shlex.quote(command)} >> {log} 2>&1 < /dev/null & "
              f"echo STARTED_PID $!")
    rc, out = _ssh(host, port, script, timeout=300)
    return (rc == 0 and "STARTED_PID" in out), run_name, log


def scale(local_dir, remote_dir, command, *, instance_id=None, gpu_substr=None,
          min_vram_mib=0, pip=None, run_name=None):
    """One call: choose a free GPU (or a specific instance_id), sync, launch."""
    rows = core.collect()
    target = None
    if instance_id is not None:
        for r in rows:
            if str(r["id"]) == str(instance_id):
                target = r
                break
        if target is None:
            return {"ok": False, "error": f"instance {instance_id} not found"}
        if target["verdict"] != "FREE":
            return {"ok": False, "error": f"instance {instance_id} is {target['verdict']}, not FREE"}
    else:
        target = pick_free(min_vram_mib, gpu_substr, rows)
        if target is None:
            return {"ok": False, "error": "no FREE GPU matching filters"}

    host, port = target["host"], target["port"]
    ok, err = sync(host, port, local_dir, remote_dir)
    if not ok:
        return {"ok": False, "error": f"rsync failed: {err}", "target": target["id"]}
    ok, rn, log = launch(host, port, remote_dir, command, run_name, pip)
    return {"ok": ok, "instance": target["id"], "gpu": target["gpu"],
            "ssh": target["ssh"], "run_name": rn, "log": log, "remote_dir": remote_dir}
