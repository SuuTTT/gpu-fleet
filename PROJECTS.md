# Using the fleet from a research project

The center node (AWS) runs the dashboard and an ingest service. **Any** research
project, on **any** box, can: see free GPUs, run on them, and report status so the
central dashboard shows what each project is doing and notifies when runs finish.
Nothing here is project-specific.

## 1. One-time: point your jobs at the center

Set these in the environment of whatever box launches your jobs:

```bash
export FLEET_TOKEN="<shared-secret>"           # same token the center's ingest uses
# Pick ONE transport:
export FLEET_CENTER="http://54.251.156.216:5055"          # HTTP (needs 5055 open in the SG)
# ── or, works with only SSH/port 22 open: ──
export FLEET_CENTER_SSH="ubuntu@54.251.156.216 -i ~/.ssh/your_key"   # ssh -> curl localhost:5055
```

The client prefers HTTP and falls back to SSH (it `ssh`es to the center and curls
the ingest on `localhost:5055`, so no HTTP port needs to be open).

## 2. Discover free GPUs

```bash
cd ~/gpu-fleet && GPU_FLEET_KEY=~/.ssh/your_key ./gpufleet free      # schedulable GPUs
./gpufleet list      # everything, with util / env / project / verdict
```

**Real CPU quota, not physical cores:** vast.ai rents a cgroup CPU-time *quota*.
`quota_probe.sh out.json` reports each free box's true usable cores — **size your
worker pools to the quota** (40 workers on an 8-core quota runs ~14× slower).

## 3. Report status (so the dashboard + notifications reflect your run)

```bash
G="python3 -m gpu_fleet.cli report"     # or ./gpufleet report
$G start --project my-rl --boxes 38664456,36994217 --detail "warmup"
$G ping  --project my-rl --boxes 38664456,36994217 --detail "epoch 12/50"   # optional heartbeat
$G done  --project my-rl --detail "best val 0.94"                            # frees the tag + notifies
```

Or from Python:
```python
from gpu_fleet.client import report
report("start", project="my-rl", boxes=["38664456"], detail="warmup")
report("done",  project="my-rl", detail="best val 0.94")
```

What each report does on the center:
- tags your box IDs in `assignments.json` → dashboard **project** column,
- updates `fleet_status.json` → dashboard **Projects (live)** panel (state/detail/updated),
- appends `notifications.log` → `tail -f ~/gpu-fleet/notifications.log` to watch finishes.

## 4. (Optional) launch + auto-report in one step

`./gpufleet run --dir . --cmd "python train.py" --gpu A4000 --pip "torch"` syncs your
code to the cheapest matching FREE GPU and launches it detached. Wrap your command to
call `report start`/`report done` at its ends to appear on the dashboard.

## Center setup (admin, once)

```bash
# on the center node:
export FLEET_TOKEN=<shared-secret>
nohup python3 ~/gpu-fleet/fleet_ingest.py 5055 > ~/gpu-fleet/ingest.log 2>&1 &   # ingest
nohup ./gpufleet web --port 5050 --refresh 30 > ~/gpu-fleet/web.log 2>&1 &       # dashboard
```
Ingest writes `assignments.json` / `fleet_status.json` / `notifications.log` next to
itself (override with `GPU_FLEET_ASSIGNMENTS` / `FLEET_STATUS` / `FLEET_LOG`); the
dashboard reads the same files. To accept HTTP reports from other boxes, open inbound
**TCP 5055** (and **5050** to view the dashboard) in the EC2 security group; otherwise
projects use the `FLEET_CENTER_SSH` transport over port 22.
