---
name: watch-worldcup-replay
description: Open Migu Video, find FIFA World Cup matches that already have replay videos, show spoiler-light numbered choices with multiple replay versions (different commentary teams), then open the selected match with Picture-in-Picture (default) or fullscreen playback. Use when the user wants to watch World Cup replays later without seeing scores or manually browsing Migu pages.
---

# Watch World Cup Replay

Use this skill to help a user watch a completed World Cup match replay from Migu Video while minimizing result spoilers. Supports multiple replay versions (different commentary teams, AI commentary, highlights).

## Quick Start

Run from this skill directory so `uv` can read `pyproject.toml`:

```powershell
cd C:\Users\11270\.claude\skills\watch-worldcup-replay
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\.playwright-browsers"
uv run python scripts/open_cctv_worldcup_replay.py
```

Before first use, install the Playwright browser runtime:

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\.playwright-browsers"
uv --cache-dir .uv-cache run python -m playwright install chromium
```

If the default `uv` cache under `AppData` has Windows permission issues, add `--cache-dir .uv-cache`.

## Workflow

1. **Phase 1 — API-based match collection**: Fetches match data from Migu Video's API (`vms-sc.miguvideo.com`), which returns all World Cup matches with team info, commentary teams, dates, and match IDs. No browser needed — pure HTTP request.
2. **Date filtering**: By default, shows today's matches (`--days 0`), since early-morning matches are on the same calendar day. Use `--days N` to show matches from the last N days, or `--days 1` for yesterday only.
3. **Spoiler-free listing**: Prints numbered options showing team names, group, stage, and commentary team — scores are hidden. Each match shows its date and kickoff time.
4. **User selects match**: Asks the user to enter a match number, or can skip with `--play N`.
5. **Phase 2 — Replay version selection**: Opens the match page in a headless browser, extracts all available replay versions (e.g. 全场回放 with different commentary teams, 纯享版回放, 全场集锦, AI解说, etc.). User selects which version to watch.
6. **Phase 3 — Playback**: Launches a hidden browser, clicks the selected replay version, waits for ads to finish and the actual replay video to load (two-phase detection: first waits for any video to start, then polls for a video with duration > 5 minutes to skip past ads which can be 130+ seconds), then:
   - **Default: Picture-in-Picture** — video pops out as a floating window, targets the longest-duration video (replay, not ad). Browser stays hidden (zero spoiler exposure).
   - **`--fullscreen`: Clean page + fullscreen** — uses CSS `display: none !important` to hide all DOM elements except the video and its ancestors, positions the video to fill the entire viewport, then brings the window to foreground. The user sees only the video on a black background with no scores or page chrome visible.

## Usage

```powershell
# Default: interactive → choose match → choose replay version → PiP
uv run python scripts/open_cctv_worldcup_replay.py

# Fullscreen mode (instead of PiP)
uv run python scripts/open_cctv_worldcup_replay.py --fullscreen

# Skip match selection, directly play match #4 (still asks for replay version)
uv run python scripts/open_cctv_worldcup_replay.py --play 4

# Combine
uv run python scripts/open_cctv_worldcup_replay.py --fullscreen --play 3

# List only (no playback)
uv run python scripts/open_cctv_worldcup_replay.py --list-only

# Debug mode (browser visible, verbose logging)
uv run python scripts/open_cctv_worldcup_replay.py --visible

# Date filter: today only (default)
uv run python scripts/open_cctv_worldcup_replay.py --list-only

# Date filter: yesterday only
uv run python scripts/open_cctv_worldcup_replay.py --list-only --days 1

# Date filter: last 3 days
uv run python scripts/open_cctv_worldcup_replay.py --list-only --days 3

# Custom timeout (ms)
uv run python scripts/open_cctv_worldcup_replay.py --timeout 60000
```

## Architecture

- **Main script**: `scripts/open_cctv_worldcup_replay.py`
- **Python Playwright** (primary) with **Node.js Playwright fallback**
- **Data source**: Migu Video API (`vms-sc.miguvideo.com/vms-match/v6/staticcache/basic/match-list/...`) — returns structured JSON with match data
- **Match pages**: `https://www.miguvideo.com/p/live/{mgdbId}` — each match has a dedicated page with replay version swiper
- **Replay versions**: Extracted from the page's `.match-review__slide` swiper elements. Typical versions include:
  - 全场集锦 / 超长版集锦 (highlights)
  - 全场回放 (commentary team A)
  - 全场回放 (commentary team B)
  - 纯享版回放 (pure replay, no commentary overlay)
  - 全场回放（智能解说）(AI commentary)
  - 全场回放（AI智慧观赛）(AI smart viewing)
- Phase 1: Pure HTTP request to API, no browser needed
- Phase 2: `headless=True` — no visible window, extracts replay versions
- Phase 3: `headless=False` with `--start-minimized --window-position=-32000,-32000` — offscreen until PiP or fullscreen is ready
- PiP via `video.requestPictureInPicture()` with duration-based ad filtering (prefers longest video)
- Fullscreen via CSS injection: hides all non-video DOM elements, fills viewport with video only