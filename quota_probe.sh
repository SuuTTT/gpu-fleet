#!/bin/bash
# Probe every FREE vast.ai box for its REAL cpu quota (cgroup v1 or v2),
# via direct IP + ControlMaster multiplexing (avoids the shared-proxy SSH throttle).
SSHK=${GPU_FLEET_KEY:-~/.ssh/id_ed25519}
CM="-o ControlMaster=auto -o ControlPath=/tmp/cmq-%h-%p -o ControlPersist=30s"
SSHOPT="-i $SSHK -o StrictHostKeyChecking=no -o ConnectTimeout=15 -o BatchMode=yes $CM"

# free instance IDs from gpufleet
IDS=$(GPU_FLEET_KEY=$SSHK ./gpufleet json 2>/dev/null | python3 -c '
import sys,json
d=json.load(sys.stdin)
rows=d if isinstance(d,list) else d.get("gpus",[])
for g in rows:
    if str(g.get("verdict",""))=="FREE": print(g.get("id"))' 2>/dev/null)
[ -z "$IDS" ] && { echo "no free boxes (or json parse failed)"; exit 0; }

JSON_OUT=${1:-}
printf "%-10s %-22s %-8s %s\n" "ID" "endpoint" "phys" "REAL_QUOTA(cores)" >&2
entries=""
for id in $IDS; do
  url=$(vastai ssh-url "$id" 2>/dev/null | sed 's#ssh://root@##')
  ip=${url%%:*}; port=${url##*:}
  [ -z "$ip" ] && { printf "%-10s unreachable\n" "$id" >&2; continue; }
  q=$(ssh $SSHOPT -p "$port" root@"$ip" '
    p=$(nproc)
    if [ -f /sys/fs/cgroup/cpu.max ]; then read a b < /sys/fs/cgroup/cpu.max; [ "$a" = max ] && echo "$p $p" || echo "$a $b"
    elif [ -f /sys/fs/cgroup/cpu/cpu.cfs_quota_us ]; then a=$(cat /sys/fs/cgroup/cpu/cpu.cfs_quota_us); b=$(cat /sys/fs/cgroup/cpu/cpu.cfs_period_us); [ "$a" = "-1" ] && echo "$p $p" || echo "$a $b"
    else echo "$((p*100000)) 100000"; fi
    echo "PHYS=$p"' 2>/dev/null | grep -v -E "Welcome|Have fun")
  phys=$(echo "$q" | grep PHYS | cut -d= -f2)
  cores=$(echo "$q" | grep -v PHYS | tail -1 | awk '{if($2>0)printf "%.1f",$1/$2; else print "0"}')
  printf "%-10s %-22s %-8s %s\n" "$id" "$ip:$port" "${phys:-?}" "${cores:-?}" >&2
  [ -n "$ip" ] && [ "${cores%.*}" -ge 1 ] 2>/dev/null && \
    entries="$entries{\"id\":\"$id\",\"ip\":\"$ip\",\"port\":$port,\"quota\":$cores},"
done
if [ -n "$JSON_OUT" ]; then
  echo "[${entries%,}]" > "$JSON_OUT"
  echo "wrote $JSON_OUT" >&2
fi
