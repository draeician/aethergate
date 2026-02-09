# AetherGate Operations Runbook — Admin Console Direct-Port Migration

**Host:** docker02 `/opt/aethergate`
**Date:** 2026-02-08

## Summary of Changes

| Change | File | What |
|--------|------|------|
| NPM cleanup | NPM DB (in-container) | Remove `/admin` custom location from proxy_host 23 |
| Compose: dashboard env | `docker-compose.yml` | Add missing `MASTER_API_KEY=${ADMIN_KEY}` |
| Compose: port binding | `docker-compose.yml` | Explicit `0.0.0.0:8501:8501` |
| Dockerfile: numpy | `Dockerfile` | Pre-install numpy==1.26.4 before other deps |
| TLS decision | N/A | Option B: HTTP LAN-only on :8501, documented |

---

## Task 1: Clean NPM Proxy Host 23

Run these on docker02. First, back up the NPM database:

```bash
# Backup NPM DB
docker exec nginx-proxy-app-1 cp /data/database.sqlite /data/database.sqlite.bak
```

Inspect current state:

```bash
# Check current custom locations for proxy_host 23
docker exec nginx-proxy-app-1 sqlite3 /data/database.sqlite \
  "SELECT id, forward_host, forward_port, path FROM proxy_host_location WHERE proxy_host_id = 23;"

# Check advanced_config
docker exec nginx-proxy-app-1 sqlite3 /data/database.sqlite \
  "SELECT id, domain_names, forward_host, forward_port, advanced_config FROM proxy_host WHERE id = 23;"
```

Remove the `/admin` custom location and clear `advanced_config`:

```bash
# Delete all custom locations for proxy_host 23 (the /admin one)
docker exec nginx-proxy-app-1 sqlite3 /data/database.sqlite \
  "DELETE FROM proxy_host_location WHERE proxy_host_id = 23;"

# Clear advanced_config
docker exec nginx-proxy-app-1 sqlite3 /data/database.sqlite \
  "UPDATE proxy_host SET advanced_config = '' WHERE id = 23;"
```

Regenerate nginx config and verify:

```bash
# Restart NPM to regenerate configs
docker restart nginx-proxy-app-1

# Wait a few seconds, then verify
sleep 5

# Check nginx config is valid
docker exec nginx-proxy-app-1 nginx -t

# Verify the proxy_host config exists and has correct server_name
docker exec nginx-proxy-app-1 cat /data/nginx/proxy_host/23.conf | grep server_name

# Verify no /admin location remains in the config
docker exec nginx-proxy-app-1 cat /data/nginx/proxy_host/23.conf | grep -i admin
# (should return nothing)
```

Verify the API is reachable:

```bash
curl -kI https://aethergate.draeician.com/
# Expected: HTTP/2 200 (or 307/redirect from FastAPI — NOT "unrecognized name" or NPM 404)
```

---

## Task 2: Expose Dashboard on Port 8501

**Already done in compose changes** — see `docker-compose.yml` diff:

- Port: `"0.0.0.0:8501:8501"` (explicit bind to all interfaces)
- Added: `MASTER_API_KEY=${ADMIN_KEY}` (was **missing** — dashboard would crash without it)

### Firewall (if ufw is active)

```bash
# Check if ufw is active
sudo ufw status

# If active, allow LAN access to 8501
sudo ufw allow from 192.168.22.0/24 to any port 8501 proto tcp comment "AetherGate admin dashboard (LAN)"

# Verify
sudo ufw status numbered | grep 8501
```

If no firewall is running, skip this step.

---

## Task 3: TLS Decision

**Decision: Option B — HTTP on :8501, LAN-only.**

Rationale:
- No subdomain/wildcard cert needed
- Admin dashboard is internal-use only
- Firewall restricts access to 192.168.22.0/24
- Avoids additional containers/complexity
- NPM is NOT involved in :8501 traffic

Access URL: `http://192.168.22.110:8501/admin`

If HTTPS is later required, add a Caddy sidecar in compose:
```yaml
# Future: uncomment to add TLS termination on 8501
# admin-tls:
#   image: caddy:2-alpine
#   ports:
#     - "8501:8501"
#   command: caddy reverse-proxy --from :8501 --to aethergate-dashboard:8501
#   depends_on:
#     - dashboard
```

---

## Task 4: Fix NumPy / Dashboard Crash

**Root cause:** The Docker image had numpy 2.x pulled in by a transitive dependency
(litellm or streamlit). numpy 2.x wheels require x86-64-v2 CPU instructions that
docker02's CPU doesn't support.

**Fix (in Dockerfile):**
```dockerfile
RUN pip install --no-cache-dir "numpy==1.26.4" && \
    pip install --no-cache-dir -r requirements.txt
```

Pre-installing numpy==1.26.4 ensures pip won't upgrade it when resolving other deps.
numpy 1.26.4's manylinux_2_17 wheel is compatible with all x86_64 CPUs.

**Also fixed:** Added `MASTER_API_KEY=${ADMIN_KEY}` to dashboard env — without this,
dashboard.py raises `RuntimeError` on startup (line 28).

---

## Deploy Commands (run on docker02 in /opt/aethergate)

```bash
# 1. Pull latest source (or scp the changed files)
git pull  # or copy docker-compose.yml + Dockerfile from dev machine

# 2. Rebuild dashboard image (--no-cache to force fresh numpy install)
docker compose build --no-cache dashboard

# 3. Recreate only the dashboard container (API stays untouched)
docker compose up -d dashboard

# 4. Check logs for startup errors
docker compose logs -f dashboard --tail=50
# Look for: "You can now view your Streamlit app in your browser"
# Ctrl+C to exit log follow

# 5. Verify numpy version inside container
docker exec aethergate-dashboard python -c "import numpy; print(numpy.__version__)"
# Expected: 1.26.4
```

---

## Verification

### API via NPM (unchanged)

```bash
curl -kI https://aethergate.draeician.com/
# Expected:
#   HTTP/2 200
#   content-type: application/json
#   (or a redirect — point is it's NOT "SSL: unrecognized name" or NPM default 404)

curl -ks https://aethergate.draeician.com/health | head
# Expected: {"status":"ok"} or similar
```

### Dashboard direct port

```bash
# From docker02 itself
curl -i http://127.0.0.1:8501/admin | head -20
# Expected:
#   HTTP/1.1 200 OK
#   content-type: text/html
#   (Streamlit HTML page)

# From LAN client
curl -i http://192.168.22.110:8501/admin | head -20
# Expected: same as above
```

### Dashboard numpy sanity

```bash
docker exec aethergate-dashboard python -c "import numpy; print(numpy.__version__)"
# Expected: 1.26.4

docker exec aethergate-dashboard python -c "import pandas; print(pandas.__version__)"
# Expected: 2.2.2
```

---

## Rollback

### Rollback NPM (Task 1)

```bash
# Restore the backed-up database
docker exec nginx-proxy-app-1 cp /data/database.sqlite.bak /data/database.sqlite
docker restart nginx-proxy-app-1
sleep 5
docker exec nginx-proxy-app-1 nginx -t
```

### Rollback Compose / Dockerfile (Tasks 2 + 4)

```bash
# Revert to previous commit
cd /opt/aethergate
git checkout HEAD~1 -- docker-compose.yml Dockerfile

# Rebuild and redeploy
docker compose build --no-cache dashboard
docker compose up -d dashboard
```

### Rollback Firewall (Task 2)

```bash
# List rules with numbers
sudo ufw status numbered

# Delete the 8501 rule (replace N with the rule number)
sudo ufw delete N
```

### Emergency: stop dashboard entirely

```bash
docker compose stop dashboard
# API continues running unaffected
```
