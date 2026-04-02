[🇰🇷 한국어](README.md) | 🇺🇸 English

# reels-catcher-extension

A Chrome Extension + local server that automatically detects Instagram Reels shared via DM, processes them through the reels-catcher pipeline, and syncs them to a Notion database.

## Platform Compatibility

| Component | macOS | Windows | Linux |
|---|---|---|---|
| Chrome Extension | ✅ | ✅ | ✅ |
| Python server (`local_server.py`) | ✅ | ⚠️ Manual setup required | ⚠️ Manual setup required |
| `Start Server.command` (auto-launch) | ✅ | ❌ Not supported | ❌ Not supported |

> **`Start Server.command` and this setup guide are written for macOS.**  
> On Windows/Linux, activate the virtual environment and run the server manually.

## How It Works

Uses Chrome's `chrome.debugger` API (Chrome DevTools Protocol) to passively monitor Instagram Web network traffic.

```
Instagram Web (DM received)
  ↓ WebSocket / HTTP response (CDP monitoring)
background.js (extracts reel URLs)
  ↓ POST /api/reels
local_server.py (dedup + time filter)
  ↓
reels-catcher pipeline
  ├── Download (yt-dlp)
  ├── Metadata extraction
  ├── AI classification
  ├── Obsidian note generation
  └── Notion database sync (with video upload)
```

**Key features:**
- No additional requests to Instagram servers (passive detection)
- No unofficial Private API usage → no account ban risk
- Real-time detection (no page refresh needed)

## File Structure

```
reels-catcher-extension/
├── manifest.json          # Chrome Extension config (MV3)
├── background.js          # Service Worker: CDP events + server forwarding
├── content.js             # Content Script (auxiliary)
├── page-interceptor.js    # Page Script (HTTP fetch auxiliary detection)
├── popup/
│   ├── popup.html         # Extension popup UI
│   └── popup.js           # Popup logic (toggle, counter, server status)
├── icons/                 # Extension icons
├── local_server.py        # Local API server (reels-catcher + Notion integration)
├── notion_writer.py       # Notion API v3 sync module
├── debug_server.py        # Development test server (prints received data only)
├── requirements.txt       # Python dependencies
├── scripts/
│   └── backfill_notion.py # Bulk upload existing data to Notion
└── Start Server.command   # Server launch script (macOS only)
```

## Prerequisites

- [reels-catcher](https://github.com/CatDarkGame/reels-catcher) installed
- Python 3.11+
- Chrome browser
- Logged into instagram.com

## Installation

### 1. Install Dependencies

Install additional packages into the reels-catcher virtual environment:

```bash
cd <path-to-reels-catcher>
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r <path-to-reels-catcher-extension>/requirements.txt
```

### 2. Create Config File

```bash
mkdir -p ~/.local/share/reels-catcher-extension
```

Create `~/.local/share/reels-catcher-extension/config.json`:

```json
{
  "dataset_root": "/path/to/reels-catcher_output",
  "obsidian_vault": "/path/to/obsidian/vault",
  "reels_catcher_src": "/path/to/reels-catcher/src",
  "notion_api_key": "secret_...",
  "notion_db_id": "<Notion database ID>"
}
```

| Key | Description | Required |
|---|---|---|
| `dataset_root` | Path where reels are downloaded | ✅ |
| `obsidian_vault` | Obsidian vault root path | ✅ |
| `reels_catcher_src` | reels-catcher Python package src path | ✅ (during legacy transition) |
| `notion_api_key` | Notion Integration API key | If using Notion |
| `notion_db_id` | Notion database ID | If using Notion |

> **Security:** `config.json` contains secrets. It is listed in `.gitignore` — never commit it.  
> Recommended: set file permissions to `600`: `chmod 600 config.json`

### 3. Notion Setup

1. Create an Integration at [Notion Integrations](https://www.notion.so/my-integrations)
2. Open your target database → `...` → **Connections** → add your Integration
3. Extract the database ID from the URL: `notion.so/<workspace>/<DATABASE_ID>?v=...`
4. Add `notion_api_key` and `notion_db_id` to `config.json`

> Database properties (columns) are created automatically on first server run.

### 4. Load Chrome Extension

1. Go to `chrome://extensions`
2. Enable **Developer mode** (top right)
3. Click **Load unpacked**
4. Select this folder (`reels-catcher-extension/`)

### 5. Start the Local Server

**macOS (recommended):**
```bash
# Double-click Start Server.command
```

**Manual (all platforms):**
```bash
source <venv path>/bin/activate          # Windows: <venv path>\Scripts\activate
python3 <path-to-reels-catcher-extension>/local_server.py
```

Expected output on start:
```
🚀 reels-catcher server started: http://localhost:8000/api/reels
   Server start time: HH:MM:SS (only DMs received after this time will be processed)
   Pipeline: ✅ active
   Notion writer: ✅ active
```

## Usage

1. **Start the server** (double-click `Start Server.command` or run manually)
2. Open **instagram.com/direct**
3. Click the **Extension popup** → confirm "Monitoring" + "Connected"
4. Have another account send a Reel link via DM
5. Check the server terminal for processing output:
   ```
   🎬 Reel received: https://www.instagram.com/reel/SHORTCODE/
   ⬇️  Download started: ...
   ✅ Done: Game Title → /path/to/note.md
   [notion] ✅ Created: SHORTCODE
   ```

## Extension Popup States

| Display | Meaning |
|---|---|
| 🟢 Monitoring + Connected | Working normally |
| 🟠 Monitoring + Server off | Run the server |
| ⚫ Inactive | Toggle is OFF in the popup |

## Backfill Existing Data to Notion

Upload previously collected data to Notion in bulk:

```bash
# Activate your virtual environment first
source <venv path>/bin/activate          # Windows: <venv path>\Scripts\activate

# Preview items (dry-run)
python3 <path-to-reels-catcher-extension>/scripts/backfill_notion.py --dry-run

# Full upload (metadata + video)
python3 <path-to-reels-catcher-extension>/scripts/backfill_notion.py

# Metadata only (no video upload)
python3 <path-to-reels-catcher-extension>/scripts/backfill_notion.py --no-video
```

## Notes

- A **"Chrome is debugging this browser"** banner will appear on the Instagram DM tab — this is expected behavior due to CDP usage
- DMs received before the server started are ignored
- Processed reel shortcodes are cached in `~/.local/share/reels-catcher-extension/ext_seen.json`
- Change port: `local_server.py --port 9000`
- If Chrome updates or the extension reloads, the popup toggle may reset to OFF → turn it back ON and refresh the Instagram DM tab

## Development Test Server

A lightweight server that prints received data without running the full pipeline:

```bash
python3 debug_server.py
```

## License

[MIT](LICENSE)
