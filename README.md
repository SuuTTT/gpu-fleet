# GPU Fleet

See your **Vast.ai GPU fleet** at a glance and **scale experiments onto free GPUs** —
no scheduler, no database, just the `vastai` CLI + SSH.

Answer two questions instantly: *which GPUs are free right now?* and *run my
experiment on one.*

```
$ ./gpufleet free
       ID GPU            st      util   gpu-mem  cpu  ram MB     disk  job  $/hr  project    verdict  env
 38764529 RTX A4000      running   0%  27/16376  16  2.3/48GB   52%    0  0.086  nbeatsx-dc FREE
 38768212 RTX A4000      running   0%   1/16376   4  6.2/48GB   51%    0  0.086  -          FREE
FREE: 2  |  total: 15  |  online: 14
  -> 38764529(RTX A4000 ssh1.vast.ai:14528), 38768212(RTX A4000 ssh2.vast.ai:18212)
```

---

## Requirements

- Python 3.8+ (standard library only — nothing to pip-install for the tool itself)
- The [`vastai`](https://pypi.org/project/vastai/) CLI, logged in:
  `pip install vastai && vastai set api-key <KEY>`
- An SSH private key your instances accept (default `~/.ssh/vastai_id_ed25519`)
- `rsync` + `ssh` on your control machine (for `gpufleet run`)

No server to install. Clone, then run `./gpufleet`.

---

## Quick start

```bash
git clone <this-repo> ~/gpu-fleet
cd ~/gpu-fleet

./gpufleet list      # every GPU: util, mem, cpu, ram, disk, env, project, verdict
./gpufleet free      # only the schedulable (FREE) GPUs
./gpufleet json      # full status as JSON (pipe to jq)
./gpufleet web       # web dashboard on http://localhost:5050
```

### Run an experiment on a free GPU

```bash
# sync ./my_experiment to a free GPU and launch train.py, detached
./gpufleet run --dir ./my_experiment --cmd "python3 train.py --epochs 50" \
    --remote /root/my_experiment --pip "torch numpy pandas"

# constrain the choice
./gpufleet run --dir . --cmd "python3 sweep.py" --gpu A4000 --min-vram 16000
./gpufleet run --dir . --cmd "python3 sweep.py" --instance 38764529   # a specific GPU
```

`run` picks the cheapest FREE GPU matching your filters, rsyncs your code, installs
any `--pip` packages, then launches with `setsid` so the job survives SSH
disconnect. It prints the instance, the log path, and a ready-to-paste `tail -f`.

---

## Web dashboard

```bash
./gpufleet web --port 5050 --refresh 30
```
From your laptop, tunnel and open it:
```bash
ssh -L 5050:localhost:5050 <control-box>
# then browse to  http://localhost:5050   (JSON at /api)
```
A background thread refreshes the snapshot every `--refresh` seconds, so the page
loads **instantly** — it never blocks on SSH probes (this was the cause of the
old "blank white page": the first load used to wait ~15s for all probes).

---

## What "FREE" means

A GPU is schedulable (**FREE**) when **all** hold:

| check | threshold |
|---|---|
| GPU utilisation | < 10 % |
| GPU memory used | < 500 MiB |
| GPU compute jobs | none (`nvidia-smi --query-compute-apps`) |
| disk used | < 90 % |

Other verdicts: **BUSY**, **OFFLINE** (not running), **UNREACHABLE** (running but
SSH didn't answer).

---

## Labeling what each GPU is working on

Edit `assignments.json` to tag instances by project — shown in the `project`
column of every view:

```json
{
  "38764529": {"project": "nbeatsx-dc", "note": "EPF sweep"},
  "38751740": {"project": "rl-glass",   "note": "reserved"}
}
```

---

## Configuration (env vars)

| var | default | meaning |
|---|---|---|
| `GPU_FLEET_KEY` | `~/.ssh/vastai_id_ed25519` | SSH private key |
| `GPU_FLEET_ASSIGNMENTS` | `~/gpu-fleet/assignments.json` | project-label map |

Thresholds (`GPU_FREE_UTIL`, `GPU_FREE_MEM`, `DISK_FULL_PCT`, `SSH_TIMEOUT`) are at
the top of `gpu_fleet/core.py`.

---

## How it works

- `core.collect()` — `vastai show instances --raw`, then probes every running host
  in parallel over SSH (one round-trip each) for GPU/CPU/RAM/disk/jobs/env, and
  classifies each FREE/BUSY/OFFLINE/UNREACHABLE.
- `web.serve()` — background refresher + stdlib HTTP server; instant responses.
- `run.scale()` — pick a free GPU → `rsync` code → `setsid` launch → return.

**Gotcha it handles:** Vast.ai's API sometimes reports the SSH *proxy* port while
the real `sshd` listens at **port + 1**. `core.probe()` tries the reported port,
then `port+1`, and remembers what worked — so boxes that look "unreachable" in the
raw API show up correctly here.

---

## Layout

```
gpu-fleet/
├── gpufleet                 # ./gpufleet launcher (no install needed)
├── assignments.json         # instance_id -> {project, note}
├── gpu_fleet/
│   ├── core.py              # discovery + probing + FREE/BUSY logic
│   ├── web.py               # non-blocking web dashboard
│   ├── run.py               # scale: pick free GPU, rsync, launch
│   └── cli.py               # `gpufleet` subcommands
└── examples/hello_gpu/      # minimal experiment you can `gpufleet run`
```

## Library use

```python
from gpu_fleet import core, run
free = core.free_gpus()
res  = run.scale("./exp", "/root/exp", "python3 train.py", gpu_substr="A4000")
print(res)   # {ok, instance, gpu, ssh, run_name, log, remote_dir}
```
