"""gpu_fleet.cli - the `gpufleet` command."""
from __future__ import annotations

import argparse
import json

from . import core, run as runmod


def _render_table(rows, free_only=False):
    hdr = (f"{'ID':>9} {'GPU':<14} {'st':<8} {'util':>5} {'gpu-mem':>11} {'cpu':>3} "
           f"{'load':>5} {'ram MB':>11} {'disk':>10} {'job':>3} {'$/hr':>6} "
           f"{'project':<11} {'verdict':<11} env")
    print(hdr); print("-" * 160)
    for r in rows:
        if free_only and not r["verdict"].startswith("FREE"):
            continue
        p = r.get("probe") or {}
        util = f"{p.get('util','?')}%" if p else "-"
        gmem = f"{p.get('mem_used','?')}/{p.get('mem_total','?')}" if p else "-"
        disk = f"{p.get('disk','-')} {p.get('disk_pct','')}".strip() if p else "-"
        env = (p.get("env", "") or "")[:46]
        print(f"{r['id']:>9} {r['gpu'][:14]:<14} {r['status'][:8]:<8} {util:>5} {gmem:>11} "
              f"{str(p.get('cpu','-')):>3} {str(p.get('load','-')):>5} {str(p.get('ram','-')):>11} "
              f"{disk:>10} {str(p.get('jobs','-')):>3} {float(r['dph'] or 0):>6.3f} "
              f"{r['project'][:11]:<11} {r['verdict']:<11} {env}")
    free = [r for r in rows if r["verdict"] == "FREE"]
    print(f"\nFREE: {len(free)}  |  total: {len(rows)}  "
          f"|  online: {sum(1 for r in rows if r['status']=='running')}")
    if free:
        print("  -> " + ", ".join(f"{r['id']}({r['gpu'][:10]} {r['ssh']})" for r in free))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gpufleet", description="See and use your Vast.ai GPU fleet.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="show all GPUs (terminal table)")
    sub.add_parser("free", help="show only schedulable FREE GPUs")
    sub.add_parser("json", help="dump full status as JSON")

    w = sub.add_parser("web", help="run the web dashboard")
    w.add_argument("--port", type=int, default=5050)
    w.add_argument("--refresh", type=int, default=30)

    r = sub.add_parser("run", help="sync a local dir to a FREE GPU and launch a command")
    r.add_argument("--dir", required=True, help="local project directory to sync")
    r.add_argument("--cmd", required=True, help="command to run on the GPU")
    r.add_argument("--remote", default="/root/work", help="remote workspace dir")
    r.add_argument("--instance", type=int, default=None, help="target a specific instance id (must be FREE)")
    r.add_argument("--gpu", default=None, help="only pick GPUs whose name contains this substring")
    r.add_argument("--min-vram", type=int, default=0, help="minimum GPU memory MiB")
    r.add_argument("--pip", default=None, help="pip packages to install before running")
    r.add_argument("--name", default=None, help="run name (log file prefix)")

    a = ap.parse_args(argv)

    if a.cmd == "list":
        _render_table(core.collect())
    elif a.cmd == "free":
        _render_table(core.collect(), free_only=True)
    elif a.cmd == "json":
        print(json.dumps(core.collect(), indent=2, default=str))
    elif a.cmd == "web":
        from . import web
        web.serve(a.port, a.refresh)
    elif a.cmd == "run":
        res = runmod.scale(a.dir, a.remote, a.cmd, instance_id=a.instance,
                           gpu_substr=a.gpu, min_vram_mib=a.min_vram, pip=a.pip, run_name=a.name)
        print(json.dumps(res, indent=2, default=str))
        if res.get("ok"):
            host, port = res["ssh"].split(":")
            print(f"\nLaunched on {res['gpu']} ({res['ssh']}).")
            print(f"Tail logs:  ssh -i {core.SSH_KEY} -p {port} root@{host} 'tail -f {res['log']}'")


if __name__ == "__main__":
    main()
