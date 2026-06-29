# gpu-fleet/skills

One-shot setup scripts for common tasks on EC2 and Vast.ai boxes.

## setup_proxy.sh — VLESS proxy + Clash subscription server

Sets up a personal proxy on any Linux box (EC2 or Vast.ai) and serves a
Clash-compatible subscription URL you can import into Clash Verge on Mac/Windows.

**Requires:** port 443 open (proxy) + port 80 or 8899 open (config server).

```bash
# Basic usage — auto-detects everything
bash <(curl -s https://raw.githubusercontent.com/SuuTTT/gpu-fleet/master/skills/setup_proxy.sh)

# Custom ports
PROXY_PORT=8443 SUB_PORT=8899 bash setup_proxy.sh
```

After running, paste the printed URL into **Clash Verge → Profiles → +**.

### What it installs
| Component | Details |
|---|---|
| Xray core | VLESS/TCP proxy, port 443 |
| Python sub-server | Serves `config.yaml` at `http://IP:PORT/?token=XXX` |
| Cron reboot entry | Both services restart on reboot |

### EC2 vs Vast.ai
| | EC2 | Vast.ai |
|---|---|---|
| User | `ubuntu` (uses `sudo`) | `root` (no sudo) |
| Port 80 | usually free | taken by Jupyter — auto-bumps to 8899 |
| Systemd | yes | sometimes missing — falls back to nohup |
