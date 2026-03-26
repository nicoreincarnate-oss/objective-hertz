> **NOTE:** The auto-generation pipeline described in this document is not currently wired.
> `scripts/scan_architecture.py` and `scripts/watch_and_update.sh` do not exist, and the
> `make explorer` / `make watch-explorer` Makefile targets have been removed. This document
> is a historical reference for the intended design.

# System Explorer Auto-Regeneration

The Perseus system explorer (`system-explorer.html`) is a live, interactive dashboard of the entire system architecture. It visualizes all daemons, services, databases, connections, and the data flow between them.

## Problem

The explorer contains hardcoded JavaScript data arrays (NODES, CONNECTIONS, PIPELINE_STATES, MEMORY_FLOW, GROUPS) that describe the system. When the codebase changes — new modules, renamed files, new imports, changed connections — **the dashboard data goes stale**.

## Solution

This infrastructure automatically scans the Perseus codebase and regenerates the explorer whenever code changes.

## Components

### 1. Scanner: `scripts/scan_architecture.py`

The scanner is a Python script that:

1. **Scans the codebase** for:
   - Daemon modules in `perseus/`, `titan/`, `hermes/`, `clawdbot/`
   - Database tables from `scripts/init-db.sql`
   - Pipeline stages from `titan/pipeline/*.py`
   - Submodules and imports across all daemons
   - A2A port references
   - External service integrations

2. **Reads the current HTML** and extracts the template (everything before and after the data section)

3. **Generates updated JavaScript data arrays** from what it discovered

4. **Merges with overrides** (`docs/explorer-overrides.json`) to preserve manual descriptions, icons, and other metadata

5. **Writes the updated HTML** by splicing the new data into the template using markers

#### Usage

```bash
# Regenerate explorer manually
python3 scripts/scan_architecture.py

# Or via Makefile
make explorer
```

### 2. Watcher: `scripts/watch_and_update.sh`

A shell script that watches for `.py` and `.sql` file changes and auto-runs the scanner.

Supports both macOS (`fswatch`) and Linux (`inotifywait`).

#### Usage

```bash
# Watch and auto-update on file changes
bash scripts/watch_and_update.sh

# Or via Makefile
make watch-explorer
```

#### Installation

**macOS:**
```bash
brew install fswatch
```

**Linux:**
```bash
sudo apt-get install inotify-tools
```

### 3. Overrides: `docs/explorer-overrides.json`

A JSON file that preserves manual metadata when the scanner runs. It contains:

- **nodes**: Per-node metadata (descriptions, icons, labels, tech stack, paths)
- **connections**: Per-connection metadata (labels, groups)
- **groups**: Group definitions (colors, labels, y-coordinates)
- **memory_flow**: Memory layer descriptions

On first run, the scanner extracts all current hardcoded data from `system-explorer.html` and saves it to this file. This ensures nothing is lost.

**Example structure:**
```json
{
  "nodes": {
    "titan": {
      "label": "Titan",
      "desc": "Revenue engine. Polls task_queue, executes 10-stage pipeline...",
      "icon": "⚡",
      "tech": "Python asyncio, :9001",
      "path": "titan/daemon.py"
    }
  },
  "connections": {
    "titan→postgres": {
      "label": "R/W clients, deals, outreach_metrics, task_queue",
      "group": "data"
    }
  },
  "groups": {
    "execution": {
      "label": "Execution Plane",
      "color": "#f97316",
      "y": 310
    }
  },
  "memory_flow": [
    {
      "id": "mf_pg",
      "label": "Postgres (Structured)",
      "color": "#22c55e"
    }
  ]
}
```

## Workflow

### Typical Development Flow

1. **Add a new daemon module**, e.g., `titan/new_stage.py`
   - Next time `make explorer` or `make watch-explorer` detects the change, it scans the new file
   - The scanner adds it to NODES with auto-generated defaults

2. **Edit a description** in the explorer
   - Update `docs/explorer-overrides.json` with the new description
   - Re-run `make explorer` to regenerate with the updated metadata

3. **Watch mode** (continuous development)
   - Run `make watch-explorer` in a terminal
   - Every `.py` or `.sql` file change triggers an auto-scan
   - Explorer stays in sync without manual intervention

### First-Run Setup

```bash
# This creates docs/explorer-overrides.json from current HTML
make explorer
```

After this, the scanner will preserve all existing descriptions and metadata through the overrides file.

## HTML Structure

The explorer HTML has two markers that delimit the auto-generated data section:

```javascript
// ═══ AUTO-GENERATED DATA START ═══

const GROUPS = { ... };
const NODES = [ ... ];
const CONNECTIONS = [ ... ];
const PIPELINE_STATES = [ ... ];
const MEMORY_FLOW = [ ... ];

// ═══ AUTO-GENERATED DATA END ═══
```

Everything between these markers is replaced on each scan. Everything outside is preserved:
- Style, layout, SVG rendering
- Event handlers and interactivity
- App state management
- Legend and controls

## Customization

### Add Manual Metadata

Edit `docs/explorer-overrides.json` to add or update node descriptions, icons, etc.:

```json
{
  "nodes": {
    "my_new_daemon": {
      "label": "My Daemon",
      "desc": "Detailed description of what it does...",
      "icon": "🚀",
      "tech": "Python, asyncio",
      "path": "my_daemon/main.py"
    }
  }
}
```

Then run `make explorer` to regenerate.

### Adjust Group Colors/Layout

Edit the `groups` section in `docs/explorer-overrides.json`:

```json
{
  "groups": {
    "execution": {
      "label": "Execution Plane",
      "color": "#f97316",
      "y": 310
    }
  }
}
```

### Add Custom Connections

Modify the `scan_architecture.py` script's `generate_connections()` function to add hardcoded critical connections that the scanner can't auto-detect.

## Architecture

### Data Flow During Scan

```
Codebase Files (.py, .sql)
        ↓
scan_architecture.py
├─ scan_daemons()          → Find daemon dirs, modules
├─ scan_pipeline_stages()  → Find titan/pipeline/*.py
├─ scan_database_tables()  → Parse scripts/init-db.sql
├─ scan_imports_across_codebase() → Detect module dependencies
└─ load_overrides()        → Merge with explorer-overrides.json
        ↓
generate_*()
├─ generate_nodes()        → Create NODES array
├─ generate_connections()  → Create CONNECTIONS array
├─ generate_pipeline_states() → Create PIPELINE_STATES
├─ generate_memory_flow()  → Create MEMORY_FLOW
└─ generate_groups()       → Create GROUPS object
        ↓
Read HTML template (extract pre/post data sections)
        ↓
Assemble new HTML
├─ Pre-data section (unchanged styles, JS)
├─ Auto-generated data section
└─ Post-data section (unchanged event handlers)
        ↓
Write updated HTML
```

## Troubleshooting

### "Could not find data markers in HTML"

The HTML doesn't have the start/end markers. Add them:

```javascript
// ═══ AUTO-GENERATED DATA START ═══

const GROUPS = { ... };

// ═══ AUTO-GENERATED DATA END ═══
```

### Scanner not detecting new modules

The scanner looks for `.py` files in specific directories:
- `perseus/`
- `titan/`
- `hermes/`
- `clawdbot/`
- `shared/`

If your module is elsewhere, it won't be auto-detected. Add it manually to `docs/explorer-overrides.json`.

### Watch mode not triggering on file change

Ensure you have the file watcher installed:
- **macOS**: `brew install fswatch`
- **Linux**: `sudo apt-get install inotify-tools`

Check the watch script is executable:
```bash
chmod +x scripts/watch_and_update.sh
make watch-explorer
```

### Generated JavaScript is invalid

This shouldn't happen, but if it does:
1. Check the Python script for syntax errors
2. Verify `docs/explorer-overrides.json` is valid JSON
3. Ensure the HTML has proper markers
4. Run the scanner manually to see error output: `python3 scripts/scan_architecture.py`

## Performance

- **Scanner runtime**: ~1 second (depends on codebase size)
- **Watch debounce**: File changes trigger scan immediately (fswatch/inotifywait handle debouncing)
- **HTML size**: ~1.1 MB (mostly unchanged from original)

## Future Enhancements

- [ ] Auto-detect A2A connections from port references
- [ ] Extract pipeline stage descriptions from docstrings
- [ ] Build dependency graph from import analysis
- [ ] Generate connection strength from import frequency
- [ ] Export architecture as JSON API for external tools
- [ ] Create Mermaid/PlantUML diagram exports
- [ ] Add Git blame to track when connections were added/removed

## See Also

- `docs/system-explorer.html` — The dashboard itself
- `docs/explorer-overrides.json` — Metadata storage
- `scripts/scan_architecture.py` — The scanner
- `scripts/watch_and_update.sh` — The file watcher
- `Makefile` — `make explorer`, `make watch-explorer`
