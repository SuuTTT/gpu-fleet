# gpu-fleet — state & TODO

## What exists (done)
- **Discovery**: `gpufleet list|free|json` (vastai API + parallel SSH probe), web dashboard
  (`gpufleet web :5050`).
- **Real CPU quota probe**: `quota_probe.sh out.json` — vast.ai rents a cgroup CPU-time
  *quota*, not physical cores (a "72-core" box gave 8.6–17.3 usable). **Size worker pools to
  the quota** (40 workers on an 8-core quota ran ~14× slower).
- **General project protocol** (project-agnostic):
  - client: `gpufleet report start|ping|done --project P --boxes ids --detail ...`
    (`gpu_fleet/client.py`). Transports: HTTP (`FLEET_CENTER`) or SSH→localhost
    (`FLEET_CENTER_SSH`, works with only port 22). Auth `FLEET_TOKEN`, port `FLEET_INGEST_PORT`.
  - center: `fleet_ingest.py` → `assignments.json` (project column) + `fleet_status.json`
    (live per-project state) + `notifications.log`.
  - dashboard: "Projects (live, self-reported)" panel from `fleet_status.json`.
  - docs: `PROJECTS.md`.
- **Deployed on center** AWS `ubuntu@54.251.156.216`: dashboard `:5050`, ingest `:5056`
  (5055 belongs to glass-tdmpc — untouched). `FLEET_TOKEN` in `~/gpu-fleet/.fleet_env`.
  Verified end-to-end (a vast box reported in over SSH → showed on the dashboard).

## Infra facts / constraints
- EC2 security group allows **only inbound 22**. 5050/5055/5056 listen on 0.0.0.0 but are
  blocked externally. `ufw` inactive; **no `aws-cli`** on the node (can't edit SG from CLI).
- Until a port is opened, projects must use the **SSH transport** (`FLEET_CENTER_SSH`).
- Services are `nohup`'d → **do not survive reboot**.
- Center SSH key for our boxes: `~/.ssh/aws_fleet_ed25519` (pubkey in AWS authorized_keys).

## TODO (priority order)
1. **Open EC2 inbound ports** (AWS console — no aws-cli): `5050` (view dashboard remotely),
   `5056` (HTTP ingest so any box can POST without an SSH key). Restrict source to known IPs
   + rely on `FLEET_TOKEN`.
2. **Persistence**: systemd units for `gpufleet web` and `fleet_ingest.py` (auto-start on
   reboot, restart on crash). Today they're bare `nohup`.
3. **Auto-report from `gpufleet run`**: have `run.scale()` emit `report start` on launch and
   wrap the remote command to emit `report done` at exit — so launched jobs self-register.
4. **Heartbeat/staleness**: dashboard should grey out projects whose last `ping`/update is
   older than N minutes (detect dead runs); add a `ping` cadence example.
5. **Auth hardening**: per-project tokens; reject POSTs without a valid token (ingest already
   supports one shared token).
6. **Free-on-done**: when a project reports `done`, optionally clear its `assignments.json`
   project tag back to `-` after a grace period (currently marks `project(done)`).
7. **assignments.json contention**: ingest does read-modify-write with no lock; fine for low
   volume, add file-locking if many projects report concurrently.

## Consumers
- `mahjong` (IJCAI bot) is the first consumer; it reports via the general client. Its PBT
  loop (`IJCAI-mahjong/train/pbt_loop.py`) can be auto-wired to `gpufleet report` (TODO 3).
