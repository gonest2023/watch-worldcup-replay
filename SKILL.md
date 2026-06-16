---
name: watch-worldcup-replay
description: Open CCTV Sports, find FIFA World Cup matches that already have replay/highlight video, show spoiler-light numbered choices, then open the selected match with Picture-in-Picture (default) or fullscreen playback. Use when the user wants to watch World Cup replays later without seeing scores or manually browsing CCTV pages.
---

# Watch World Cup Replay

Use this skill to help a user watch a completed World Cup match replay from CCTV Sports while minimizing result spoilers.

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

1. **Phase 1 — Headless collection**: Opens CCTV Sports in a headless browser, searches for World Cup match links containing replay terms (`集锦`, `回放`, `全场`, etc.), filters out live streams and noise. Extracts match date from the page's `section-container` elements (format `MM-DD`).
2. **Date filtering**: By default, only shows matches from yesterday (`--days 1`). Use `--days N` to show matches from the last N days, or `--days 0` for today only. Add `--show-all` to skip date filtering.
3. **Spoiler-free listing**: Prints numbered options with scores hidden (`[比分已隐藏]`) — both dash-format (`2-0`) and CCTV space-format (`墨西哥 2 南非 0`) scores are masked. Each match shows its date.
4. **User selection**: Asks the user to enter a match number, or can skip with `--play N`.
5. **Replay direct URL**: Searches all frames (including iframes) for the replay tab's direct URL, navigates to it — bypasses the highlights tab entirely.
6. **Phase 2 — Offscreen playback**: Launches a visible browser minimized + offscreen, starts the video, then:
   - **Default: Picture-in-Picture** — video pops out as a floating window, browser stays hidden (zero spoiler exposure).
   - **`--fullscreen`: Web fullscreen** — presses F for fullscreen then brings browser to foreground.

## Usage

```powershell
# Default: interactive → PiP
uv run python scripts/open_cctv_worldcup_replay.py

# Fullscreen mode (instead of PiP)
uv run python scripts/open_cctv_worldcup_replay.py --fullscreen

# Skip selection, directly play match #4
uv run python scripts/open_cctv_worldcup_replay.py --play 4

# Combine
uv run python scripts/open_cctv_worldcup_replay.py --fullscreen --play 3

# List only (no playback)
uv run python scripts/open_cctv_worldcup_replay.py --list-only

# Debug mode (browser visible, verbose logging)
uv run python scripts/open_cctv_worldcup_replay.py --visible

# Wider search
uv run python scripts/open_cctv_worldcup_replay.py --show-all

# Date filter: yesterday only (default, same as --days 1)
uv run python scripts/open_cctv_worldcup_replay.py --list-only

# Date filter: today only
uv run python scripts/open_cctv_worldcup_replay.py --list-only --days 0

# Date filter: last 3 days
uv run python scripts/open_cctv_worldcup_replay.py --list-only --days 3

# Custom timeout (ms)
uv run python scripts/open_cctv_worldcup_replay.py --timeout 60000
```

## Architecture

- **Main script**: `scripts/open_cctv_worldcup_replay.py`
- **Python Playwright** (primary) with **Node.js Playwright fallback**
- Phase 1: `headless=True` — no visible window during link collection
- Phase 2: `headless=False` with `--start-minimized --window-position=-32000,-32000` — offscreen until PiP or fullscreen is ready
- Replay URL extraction: searches all frames/iframes for `回放` tab links, navigates directly
- Score masking: regex patterns for `N-N`, `N比N`, and CCTV space format `Team N Team N`
- PiP via `video.requestPictureInPicture()` with visibility-aware video selection and non-target video destruction
- Fullscreen via CDP `Browser.setWindowBounds` for window restoration
