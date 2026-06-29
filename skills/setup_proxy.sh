#!/usr/bin/env bash
# setup_proxy.sh — One-shot VLESS proxy + Clash subscription server
# Works on EC2 (ubuntu+sudo) and Vast.ai (root, no systemd needed)
#
# Usage:
#   bash setup_proxy.sh                   # auto-detect everything
#   PROXY_PORT=8443 SUB_PORT=8899 bash setup_proxy.sh
#
# After running, imports the printed URL into Clash Verge:
#   Profiles → + → paste URL → Import → activate → System Proxy on

set -e

# ── Config ───────────────────────────────────────────────────────────────────
PROXY_PORT="${PROXY_PORT:-443}"
SUB_PORT="${SUB_PORT:-80}"
SUB_DIR="${SUB_DIR:-/opt/clash-sub}"
XRAY_BIN="/usr/local/bin/xray"
XRAY_CONF="/usr/local/etc/xray/config.json"

# ── Helpers ──────────────────────────────────────────────────────────────────
info()  { echo "[proxy] $*"; }
warn()  { echo "[proxy] WARN: $*"; }

# Determine privilege escalation
if [ "$(id -u)" = "0" ]; then
    RUN=""          # already root (Vast.ai)
else
    RUN="sudo"      # EC2 ubuntu user
fi

# Auto-detect public IP (try multiple sources)
get_public_ip() {
    for url in \
        "https://checkip.amazonaws.com" \
        "https://api.ipify.org" \
        "https://ifconfig.me"; do
        ip=$(curl -sf --max-time 4 "$url" 2>/dev/null | tr -d '[:space:]')
        if [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            echo "$ip"; return 0
        fi
    done
    # Fallback: hostname
    hostname -I | awk '{print $1}'
}

# Check if port is already bound
port_free() { ! ss -tlnp 2>/dev/null | grep -q ":$1 "; }

# ── 1. Detect environment ─────────────────────────────────────────────────────
info "Detecting environment..."
PUBLIC_IP=$(get_public_ip)
info "Public IP: $PUBLIC_IP"

# On Vast.ai port 80 is usually taken by Jupyter portal; bump to 8899
if [ -f /etc/vast_containerlabel ] || ss -tlnp | grep -q ":80 "; then
    if [ "$SUB_PORT" = "80" ]; then
        SUB_PORT=8899
        warn "Port 80 in use — using SUB_PORT=$SUB_PORT for config server"
    fi
fi

# ── 2. Install Xray ───────────────────────────────────────────────────────────
if [ ! -x "$XRAY_BIN" ]; then
    info "Installing Xray..."
    $RUN bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install
else
    info "Xray already installed: $($XRAY_BIN version 2>&1 | head -1)"
fi

# ── 3. Generate credentials ───────────────────────────────────────────────────
UUID=$(cat /proc/sys/kernel/random/uuid)
TOKEN=$(openssl rand -hex 16)
info "UUID:  $UUID"
info "Token: $TOKEN"

# ── 4. Write Xray config ──────────────────────────────────────────────────────
info "Writing Xray config on port $PROXY_PORT..."
$RUN mkdir -p "$(dirname $XRAY_CONF)"
$RUN tee "$XRAY_CONF" > /dev/null << EOF
{
  "inbounds": [{
    "port": $PROXY_PORT,
    "protocol": "vless",
    "settings": {
      "clients": [{"id": "$UUID", "level": 0}],
      "decryption": "none"
    },
    "streamSettings": {"network": "tcp"}
  }],
  "outbounds": [{
    "protocol": "freedom",
    "settings": {}
  }]
}
EOF

# ── 5. Start Xray ─────────────────────────────────────────────────────────────
if systemctl is-active --quiet xray 2>/dev/null; then
    $RUN systemctl restart xray
    info "Xray restarted via systemd"
else
    # No systemd (some Vast.ai containers) — kill old, start fresh
    $RUN pkill -f "xray run" 2>/dev/null || true
    $RUN nohup "$XRAY_BIN" run -c "$XRAY_CONF" > /var/log/xray.log 2>&1 &
    info "Xray started via nohup (PID $!)"
fi
sleep 2

# ── 6. Write Clash YAML ───────────────────────────────────────────────────────
info "Writing Clash config..."
$RUN mkdir -p "$SUB_DIR"
$RUN tee "$SUB_DIR/config.yaml" > /dev/null << EOF
mixed-port: 7890
allow-lan: false
mode: rule
log-level: info
external-controller: 127.0.0.1:9090

proxies:
  - name: "$(hostname)-proxy"
    type: vless
    server: $PUBLIC_IP
    port: $PROXY_PORT
    uuid: $UUID
    network: tcp
    udp: true

proxy-groups:
  - name: "Proxy"
    type: select
    proxies:
      - "$(hostname)-proxy"
      - DIRECT

rules:
  - GEOIP,CN,Proxy
  - MATCH,DIRECT
EOF

# ── 7. Write token-gated subscription server ──────────────────────────────────
$RUN tee "$SUB_DIR/server.py" > /dev/null << 'PYEOF'
#!/usr/bin/env python3
import http.server, urllib.parse, os, sys

TOKEN   = os.environ.get("CLASH_TOKEN", "")
CONFIG  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
PORT    = int(os.environ.get("SUB_PORT", "80"))

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass
    def do_GET(self):
        q = urllib.parse.urlparse(self.path).query
        params = dict(x.split("=", 1) for x in q.split("&") if "=" in x)
        if TOKEN and params.get("token") != TOKEN:
            self.send_response(403); self.end_headers()
            self.wfile.write(b"Forbidden\n"); return
        try:
            data = open(CONFIG, "rb").read()
        except FileNotFoundError:
            self.send_response(404); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Type", "application/yaml")
        self.send_header("Content-Disposition", "attachment; filename=config.yaml")
        self.end_headers()
        self.wfile.write(data)

print(f"[sub-server] listening on :{PORT}")
http.server.HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
PYEOF
$RUN chmod +x "$SUB_DIR/server.py"

# ── 8. Start subscription server ─────────────────────────────────────────────
$RUN pkill -f "server.py" 2>/dev/null || true
sleep 1
export CLASH_TOKEN="$TOKEN"
export SUB_PORT="$SUB_PORT"
$RUN nohup env CLASH_TOKEN="$TOKEN" SUB_PORT="$SUB_PORT" \
    python3 "$SUB_DIR/server.py" > "$SUB_DIR/server.log" 2>&1 &
SUB_PID=$!
info "Sub server started (PID $SUB_PID)"
sleep 2

# ── 9. Persist across reboots ─────────────────────────────────────────────────
# Write a reboot script
$RUN tee /etc/rc.local.proxy > /dev/null << RCEOF
#!/bin/bash
# Proxy auto-start — generated by setup_proxy.sh
sleep 5
$([ "$RUN" = "sudo" ] && echo "sudo") systemctl start xray 2>/dev/null || \
    nohup $XRAY_BIN run -c $XRAY_CONF > /var/log/xray.log 2>&1 &
nohup env CLASH_TOKEN=$TOKEN SUB_PORT=$SUB_PORT \
    python3 $SUB_DIR/server.py > $SUB_DIR/server.log 2>&1 &
RCEOF
# Add to crontab if not already there
(crontab -l 2>/dev/null | grep -v 'rc.local.proxy'; \
 echo "@reboot bash /etc/rc.local.proxy") | crontab - 2>/dev/null || true

# ── 10. Verify & print result ─────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║               PROXY SETUP COMPLETE                      ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  Subscription URL (paste into Clash Verge):             ║"
echo "║                                                          ║"
echo "║  http://$PUBLIC_IP:$SUB_PORT/?token=$TOKEN  ║"
echo "║                                                          ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  Proxy:  VLESS  $PUBLIC_IP:$PROXY_PORT                  ║"
echo "║  UUID:   $UUID  ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo "Import into Clash Verge: Profiles → + → paste URL → Import"
echo "Then: activate profile → Proxies → select node → System Proxy ON"
