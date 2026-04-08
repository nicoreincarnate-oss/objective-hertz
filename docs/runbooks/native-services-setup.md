# Native macOS Services Setup

**Operator decision 2026-04-07**: replace Docker containers with native launchd services for Postgres, Qdrant, Mem0, N8N, and Redis. Saves ~2 GB Docker Desktop VM overhead. Permanent. One-time ~2 hour setup.

This runs on the Mac Studio M4 Max during initial provisioning. Do this BEFORE downloading any models or starting daemons.

---

## Why we're doing this

| Component | Docker overhead | Native overhead | Savings |
|---|---|---|---|
| Docker Desktop VM | ~2 GB | n/a | 2 GB |
| Postgres container | 384 MB | 200 MB native | 184 MB |
| Qdrant container | 512 MB | 300 MB native | 212 MB |
| Mem0 container | 512 MB | 300 MB native | 212 MB |
| N8N container | 512 MB | 250 MB native | 262 MB |
| Redis container | 100 MB | 50 MB native | 50 MB |
| **Total saved** | | | **~2.9 GB** |

On a 36 GB Studio with everything colocated, 2.9 GB is the difference between "fits comfortably" and "thrashes under load."

---

## Prerequisites

- Mac Studio M4 Max running macOS Sonoma or later
- Homebrew installed: `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`
- Operator has admin password for `sudo`
- Existing `docker-compose.yaml` will be DISABLED (not deleted — kept as fallback)

---

## Step 1: Disable Docker Desktop

```bash
# Stop any running containers
docker compose -f /Users/majovega/Desktop/Projects/objective-hertz/docker-compose.yaml down 2>/dev/null

# Quit Docker Desktop
osascript -e 'quit app "Docker"'

# Disable Docker Desktop autostart
defaults write com.docker.docker autostart -bool false

# (Optional) uninstall Docker Desktop entirely
# Settings → "Reset to factory defaults" → restart Mac
```

---

## Step 2: Install Postgres 16 native

```bash
brew install postgresql@16
brew services start postgresql@16

# Add to PATH if not already
echo 'export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc

# Create the perseus database
createdb perseus

# Run the init schema
psql perseus < /Users/majovega/Desktop/Projects/objective-hertz/scripts/init-db.sql

# Run all migrations
for f in /Users/majovega/Desktop/Projects/objective-hertz/scripts/migrations/*.sql; do
    echo "Running $f..."
    psql perseus < "$f"
done

# Verify
psql perseus -c "\dt"
psql perseus -c "SELECT COUNT(*) FROM tier_spend_log;"  # Should return 0 from the new migration
```

**Update Perseus `.env`**:
```bash
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=$(whoami)         # No password needed for local socket auth
POSTGRES_PASSWORD=
POSTGRES_DB=perseus
```

(Postgres on macOS via Homebrew uses peer auth by default — no password needed for local connections from your user account.)

---

## Step 3: Install Qdrant native

Qdrant ships a native macOS binary.

```bash
brew install qdrant
brew services start qdrant

# Verify
curl http://localhost:6333/healthz   # Should return {"title":"qdrant - vector search engine","version":"..."}
```

Qdrant data lives at `/opt/homebrew/var/qdrant/storage`. To match your existing docker-compose data dir, copy from `./qdrant_data`:

```bash
cp -r /Users/majovega/Desktop/Projects/objective-hertz/qdrant_data/* /opt/homebrew/var/qdrant/storage/
brew services restart qdrant
```

**Update `.env`**:
```bash
QDRANT_HOST=localhost
QDRANT_PORT=6333
```

---

## Step 4: Install Mem0 as Python service

Mem0 is a Python server. Install via pip and run as a launchd plist.

```bash
pip install mem0ai

# Test it manually
python -m mem0.server --port 8888
# (ctrl-C to stop after verifying it starts)
```

Create launchd plist at `~/Library/LaunchAgents/com.perseus.mem0.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.perseus.mem0</string>
  <key>ProgramArguments</key>
  <array>
    <string>/opt/homebrew/bin/python3</string>
    <string>-m</string>
    <string>mem0.server</string>
    <string>--port</string>
    <string>8888</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/Users/majovega/Library/Logs/perseus/mem0.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/majovega/Library/Logs/perseus/mem0.error.log</string>
</dict>
</plist>
```

```bash
mkdir -p ~/Library/Logs/perseus
launchctl load ~/Library/LaunchAgents/com.perseus.mem0.plist

# Verify
curl http://localhost:8888/healthz
```

**Update `.env`**:
```bash
MEM0_HOST=localhost
MEM0_PORT=8888
```

---

## Step 5: Install N8N native

```bash
# N8N requires Node.js
brew install node@20
echo 'export PATH="/opt/homebrew/opt/node@20/bin:$PATH"' >> ~/.zshrc

# Install N8N globally
npm install -g n8n

# Test it manually
n8n start
# (ctrl-C after verifying)
```

Create launchd plist at `~/Library/LaunchAgents/com.perseus.n8n.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.perseus.n8n</string>
  <key>ProgramArguments</key>
  <array>
    <string>/opt/homebrew/opt/node@20/bin/n8n</string>
    <string>start</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>N8N_PORT</key>
    <string>5678</string>
    <key>WEBHOOK_URL</key>
    <string>http://localhost:5678</string>
    <key>N8N_USER_FOLDER</key>
    <string>/Users/majovega/.n8n</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/Users/majovega/Library/Logs/perseus/n8n.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/majovega/Library/Logs/perseus/n8n.error.log</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.perseus.n8n.plist

# Verify
curl http://localhost:5678/healthz
```

---

## Step 6: Install Redis native

```bash
brew install redis
brew services start redis

# Verify
redis-cli ping  # Should return PONG
```

**Update `.env`**:
```bash
REDIS_HOST=localhost
REDIS_PORT=6379
```

---

## Step 7: Verify everything

```bash
# All 5 services should be running
brew services list | grep -E "postgres|qdrant|redis"
launchctl list | grep -E "perseus.mem0|perseus.n8n"

# All ports should be reachable
nc -z localhost 5432  # Postgres
nc -z localhost 6333  # Qdrant
nc -z localhost 6379  # Redis
nc -z localhost 8888  # Mem0
nc -z localhost 5678  # N8N

# Memory check
ps aux | grep -E "postgres|qdrant|redis|mem0|n8n" | awk '{sum+=$6} END {print "Total RSS: " sum/1024 " MB"}'
# Should be around 1500 MB total
```

---

## Step 8: Disable docker-compose.yaml (don't delete — keep as fallback)

```bash
mv /Users/majovega/Desktop/Projects/objective-hertz/docker-compose.yaml \
   /Users/majovega/Desktop/Projects/objective-hertz/docker-compose.yaml.disabled
```

If you ever need to fall back to Docker, rename it back and `docker compose up -d`.

---

## Updated `make` targets

The Makefile's `make up` and `make down` currently call `docker compose`. Update them:

```makefile
up:
	@brew services start postgresql@16
	@brew services start qdrant
	@brew services start redis
	@launchctl load ~/Library/LaunchAgents/com.perseus.mem0.plist 2>/dev/null || true
	@launchctl load ~/Library/LaunchAgents/com.perseus.n8n.plist 2>/dev/null || true
	@echo "All native services started"

down:
	@launchctl unload ~/Library/LaunchAgents/com.perseus.n8n.plist 2>/dev/null || true
	@launchctl unload ~/Library/LaunchAgents/com.perseus.mem0.plist 2>/dev/null || true
	@brew services stop redis
	@brew services stop qdrant
	@brew services stop postgresql@16
	@echo "All native services stopped"
```

---

## Total memory check

After native services are running, before any Perseus daemons or MLX models:

```bash
vm_stat | grep "Pages free\|Pages active\|wired"
```

Expected free memory: **~32 GB** (4 GB consumed by macOS + 1.5 GB by native services).

That leaves ~32 GB for Perseus daemons (~8 GB) + MLX hot set (~22 GB) + headroom (~2 GB).

---

## Backup strategy

Native Postgres backups via Homebrew:

```bash
# One-shot backup
pg_dump perseus | gzip > /Users/majovega/Backups/perseus-$(date +%Y%m%d).sql.gz

# Add to Perseus scheduler (perseus/scheduler.py) — already exists in some form
```

Qdrant snapshots:

```bash
curl -X POST http://localhost:6333/collections/perseus/snapshots
# Snapshot lands in /opt/homebrew/var/qdrant/storage/perseus/snapshots/
```

---

## Rollback to Docker (if native services break)

```bash
# Stop native services
make down

# Restore docker-compose
mv docker-compose.yaml.disabled docker-compose.yaml
docker compose up -d

# Update .env back to docker hostnames if needed
# (Most env vars stay the same — both expose same ports on localhost)
```

---

## What this enables

With ~2 GB freed up from Docker overhead, the MLX hot set can stay at ~22 GB instead of dropping to ~20 GB. That means we can keep one extra model in always-resident memory — likely Qwen3-8B for fast routing decisions, instead of swapping it on demand.
