#!/usr/bin/env python3
"""Open Migu Video World Cup replay videos with spoiler-light selection.

Uses Migu Video's match API to get match data, then opens the match page
to let users choose from multiple replay versions (different commentary teams).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

MIGU_SCHEDULE_URL = "https://www.miguvideo.com/p/schedule/10000991"
MIGU_API = "https://vms-sc.miguvideo.com/vms-match/v6/staticcache/basic/match-list/normal-match-list/0/10000991/default/1/miguvideo"
MIGU_LIVE_URL = "https://www.miguvideo.com/p/live/{}"
UTC8 = timezone(timedelta(hours=8))


@dataclass
class MatchInfo:
    mgdb_id: str
    title: str
    pk_title: str  # e.g. "卡塔尔 vs 瑞士"
    home_team: str
    away_team: str
    home_score: str | None
    away_score: str | None
    match_group: str
    stage: str
    match_field: str
    start_time: datetime
    presenters: list[str]
    highlights: str  # brief description

    def spoiler_free_title(self) -> str:
        parts = [self.pk_title]
        if self.match_group:
            parts.append(self.match_group)
        if self.stage:
            parts.append(self.stage)
        if self.presenters:
            parts.append("解说: " + "、".join(self.presenters))
        return "  |  ".join(parts)

    def date_str(self) -> str:
        return self.start_time.strftime("%m-%d %H:%M")


@dataclass
class ReplayVersion:
    text: str  # e.g. "全场回放（颜强、李欣、李子琪）"
    index: int  # position in the swiper


def fetch_matches_via_urllib(days: int) -> list[MatchInfo]:
    """Fetch match data from Migu API using urllib."""
    req = Request(MIGU_API, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.miguvideo.com/",
    })
    with urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    if data.get("code") != 200:
        raise RuntimeError(f"API returned code {data.get('code')}: {data.get('message')}")

    body = data["body"]
    match_list = body.get("matchList", {})

    today = date.today()
    start_date = today - timedelta(days=days)
    if days == 0:
        end_date = today
    else:
        end_date = today - timedelta(days=1)

    result: list[MatchInfo] = []
    for day_str in sorted(match_list.keys()):
        day_date = datetime.strptime(day_str, "%Y%m%d").date()
        if day_date < start_date or day_date > end_date:
            continue

        for m in match_list[day_str]:
            # Skip non-match items (e.g. programs like 绿茵狂想)
            if not m.get("confrontTeams") or len(m.get("confrontTeams", [])) < 2:
                continue
            # Only show ended matches
            if m.get("matchStatus") != "2":
                continue

            teams = m["confrontTeams"]
            home = teams[0]
            away = teams[1]

            start_ts = m.get("matchStartTime", m.get("startTime", 0)) / 1000
            start_dt = datetime.fromtimestamp(start_ts, tz=UTC8)

            presenters = [p["name"] for p in m.get("presenters", [])]

            result.append(MatchInfo(
                mgdb_id=m["mgdbId"],
                title=m.get("title", ""),
                pk_title=m.get("pkInfoTitle", f"{home['name']} vs {away['name']}"),
                home_team=home["name"],
                away_team=away["name"],
                home_score=home.get("score"),
                away_score=away.get("score"),
                match_group=m.get("matchGroup", ""),
                stage=m.get("stageRoundName", ""),
                match_field=m.get("matchField", ""),
                start_time=start_dt,
                presenters=presenters,
                highlights=m.get("highlights", ""),
            ))

    return result


def choose_match(matches: list[MatchInfo], direct_index: int | None = None) -> MatchInfo | None:
    print("\n找到以下世界杯回放场次：")
    for i, m in enumerate(matches, start=1):
        print(f"  {i:2d}. [{m.date_str()}] {m.spoiler_free_title()}")

    if direct_index is not None:
        if 1 <= direct_index <= len(matches):
            return matches[direct_index - 1]
        print(f"序号 {direct_index} 无效，请输入 1 到 {len(matches)} 之间的数字。")
        return None

    while True:
        choice = input("\n请输入想看的序号：").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(matches):
            return matches[int(choice) - 1]
        print(f"请输入 1 到 {len(matches)} 之间的数字。")


def get_replay_versions(page) -> list[ReplayVersion]:
    """Extract replay version options from the match page."""
    versions = page.evaluate("""() => {
        const results = [];
        // Find the replay version swiper
        const swiper = document.querySelector('.match-review__swiper, [class*="match-review"]');
        if (!swiper) return results;

        // Look for slides with replay titles
        const slides = swiper.querySelectorAll('.match-review__slide, [class*="swiper-slide"]');
        slides.forEach((slide, idx) => {
            const titleEl = slide.querySelector('.match-review__title, [class*="review__title"]');
            const text = (titleEl ? titleEl.innerText : slide.innerText || '').trim();
            if (text && (text.includes('回放') || text.includes('集锦') || text.includes('纯享'))) {
                results.push({text: text, index: idx});
            }
        });
        return results;
    }""")
    return [ReplayVersion(text=v["text"], index=v["index"]) for v in versions]


def choose_replay_version(versions: list[ReplayVersion], direct_index: int | None = None) -> ReplayVersion | None:
    print("\n本场比赛有以下回放版本：")
    for i, v in enumerate(versions, start=1):
        print(f"  {i}. {v.text}")

    if direct_index is not None:
        if 1 <= direct_index <= len(versions):
            return versions[direct_index - 1]
        print(f"序号 {direct_index} 无效。")
        return None

    if len(versions) == 1:
        print(f"自动选择: {versions[0].text}")
        return versions[0]

    while True:
        choice = input("\n请选择回放版本（输入序号）：").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(versions):
            return versions[int(choice) - 1]
        print(f"请输入 1 到 {len(versions)} 之间的数字。")


def click_replay_version(page, version: ReplayVersion) -> bool:
    """Click a specific replay version in the swiper."""
    try:
        page.evaluate(
            """([idx]) => {
                const slides = document.querySelectorAll('.match-review__slide, [class*="swiper-slide"]');
                if (slides[idx]) {
                    slides[idx].click();
                }
            }""",
            [version.index],
        )
        return True
    except Exception as e:
        print(f"点击回放版本失败: {e}")
        return False


def activate_picture_in_picture(page, debug: bool = False) -> bool:
    pip_js = """async (debugMode) => {
        const dbg = [];
        const log = (...args) => { if (debugMode) dbg.push(args.join(' ')); };

        function isVisible(el) {
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) return false;
            let p = el;
            while (p) {
                const s = getComputedStyle(p);
                if (s.display === 'none' || s.visibility === 'hidden') return false;
                p = p.parentElement;
            }
            return true;
        }

        function collectVideos(doc) {
            let videos = Array.from(doc.querySelectorAll('video'));
            try {
                for (const iframe of doc.querySelectorAll('iframe')) {
                    try {
                        if (iframe.contentDocument) {
                            videos = videos.concat(collectVideos(iframe.contentDocument));
                        }
                    } catch(e) {}
                }
            } catch(e) {}
            return videos;
        }

        const all = collectVideos(document);
        all.forEach(v => { try { v.muted = true; v.pause(); } catch(e) {} });

        const visible = all.filter(v => v.duration > 0 && isVisible(v));
        if (visible.length === 0) {
            return {ok: false, dbg: dbg};
        }

        visible.sort((a, b) => (b.offsetWidth * b.offsetHeight) - (a.offsetWidth * a.offsetHeight));
        // Prefer longest-duration video (replay, not ad)
        visible.sort((a, b) => b.duration - a.duration);
        const target = visible[0];

        let destroyed = 0;
        all.forEach(v => {
            if (v !== target) {
                try { v.pause(); v.removeAttribute('src'); v.src = ''; v.load(); destroyed++; } catch(e) {}
            }
        });

        target.muted = false;
        try { await target.play(); } catch(e) { log('play异常: ' + e.message); }
        await new Promise(r => setTimeout(r, 2000));

        if (document.pictureInPictureEnabled && !document.pictureInPictureElement) {
            try {
                await target.requestPictureInPicture();
                return {ok: true, dbg: dbg};
            } catch(e) {
                log('PiP 失败: ' + e.message);
            }
        }
        return {ok: false, dbg: dbg};
    }"""
    try:
        result = page.evaluate(pip_js, debug)
        if debug and isinstance(result, dict):
            print("\n[DEBUG] --- PiP JS 日志 ---")
            for line in result.get("dbg", []):
                print(f"  {line}")
        return result.get("ok", False) if isinstance(result, dict) else bool(result)
    except Exception as e:
        if debug:
            print(f"[DEBUG] activate_picture_in_picture 异常: {e}")
        return False


def bring_window_to_foreground(page) -> None:
    try:
        cdp = page.context.new_cdp_session(page)
        window_info = cdp.send("Browser.getWindowForTarget")
        window_id = window_info["windowId"]
        cdp.send("Browser.setWindowBounds", {
            "windowId": window_id,
            "bounds": {"windowState": "normal"}
        })
        page.wait_for_timeout(400)
        cdp.send("Browser.setWindowBounds", {
            "windowId": window_id,
            "bounds": {"windowState": "maximized"}
        })
    except Exception:
        try:
            page.evaluate("window.focus()")
        except Exception:
            pass


def wait_for_video(page, timeout_ms: int = 15000) -> bool:
    """Wait for a video element to appear and have a source."""
    deadline = page.evaluate("() => Date.now()") + timeout_ms
    while page.evaluate("() => Date.now()") < deadline:
        has_video = page.evaluate("""() => {
            const videos = document.querySelectorAll('video');
            for (const v of videos) {
                if (v.src || v.currentSrc) return true;
            }
            return false;
        }""")
        if has_video:
            return True
        page.wait_for_timeout(500)
    return False


def wait_for_replay_video(page, timeout_ms: int = 180000) -> bool:
    """Wait for the actual replay video (long duration) after ads finish.

    Migu ads can be 130+ seconds across multiple ad slots.
    We first wait for any video to appear (ad phase), then poll for
    a video with duration > 5 minutes (replay, not ad).
    """
    # Phase A: wait for any video to appear (ad starts playing)
    deadline_a = page.evaluate("() => Date.now()") + 15000
    ad_started = False
    while page.evaluate("() => Date.now()") < deadline_a:
        has_video = page.evaluate("""() => {
            const videos = document.querySelectorAll('video');
            for (const v of videos) {
                if (v.src || v.currentSrc) return true;
            }
            return false;
        }""")
        if has_video:
            ad_started = True
            break
        page.wait_for_timeout(500)

    if not ad_started:
        return False

    # Phase B: wait for replay video (duration > 300s = 5 min)
    deadline_b = page.evaluate("() => Date.now()") + timeout_ms
    while page.evaluate("() => Date.now()") < deadline_b:
        has_long_video = page.evaluate("""() => {
            const videos = document.querySelectorAll('video');
            for (const v of videos) {
                if (v.duration > 300 && (v.src || v.currentSrc)) return true;
            }
            return false;
        }""")
        if has_long_video:
            page.wait_for_timeout(2000)
            return True
        page.wait_for_timeout(2000)
    return False


def run_python(args: argparse.Namespace) -> int:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    # ── Phase 1: 通过 API 获取比赛列表 ──
    print("正在获取世界杯回放列表...")
    try:
        matches = fetch_matches_via_urllib(args.days)
    except Exception as e:
        print(f"获取比赛数据失败: {e}")
        return 1

    if not matches:
        print(f"最近 {args.days} 天没有已结束的世界杯比赛。")
        return 1

    # ── 选择场次 ──
    if args.list_only:
        print("\n找到以下世界杯回放场次：")
        for i, m in enumerate(matches, start=1):
            print(f"  {i:2d}. [{m.date_str()}] {m.spoiler_free_title()}")
        return 0

    selected = choose_match(
        matches,
        direct_index=args.play if args.play is not None and args.play != -1 else None,
    )
    if selected is None:
        return 1

    # ── Phase 2: 打开比赛页，获取回放版本 ──
    live_url = MIGU_LIVE_URL.format(selected.mgdb_id)
    print(f"\n正在打开: {selected.spoiler_free_title()}")

    with sync_playwright() as playwright:
        # Headless browser to get replay versions
        headless_browser = playwright.chromium.launch(headless=True)
        headless_context = headless_browser.new_context(viewport={"width": 1920, "height": 1080})
        headless_page = headless_context.new_page()

        try:
            headless_page.goto(live_url, wait_until="domcontentloaded", timeout=args.timeout)
            headless_page.wait_for_timeout(8000)
        except PlaywrightTimeoutError:
            pass

        versions = get_replay_versions(headless_page)
        headless_browser.close()

        if not versions:
            print("未找到回放版本，页面可能尚未加载完成。尝试加 --timeout 60000 重试。")
            return 1

        # ── 选择回放版本 ──
        chosen_version = choose_replay_version(
            versions,
            direct_index=args.replay_version if hasattr(args, 'replay_version') else None,
        )
        if chosen_version is None:
            return 1

        # ── Phase 3: 浏览器播放 ──
        print(f"\n正在播放: {chosen_version.text}")

        if args.visible:
            browser = playwright.chromium.launch(headless=False, args=["--start-maximized"])
        else:
            browser = playwright.chromium.launch(
                headless=False, args=["--start-minimized", "--window-position=-32000,-32000"]
            )
        context = browser.new_context(viewport=None)
        page = context.new_page()

        try:
            page.goto(live_url, wait_until="domcontentloaded", timeout=args.timeout)
            page.wait_for_timeout(8000)
        except PlaywrightTimeoutError:
            pass

        # Click the replay version
        click_replay_version(page, chosen_version)

        # Wait for ad to finish and replay video to load
        print("等待广告结束，加载回放视频...")
        if not wait_for_replay_video(page):
            # Fallback: try regular video detection
            if not wait_for_video(page):
                print("视频加载超时，请检查网络或重试。")
                return 1

        page.wait_for_timeout(2000)

        if args.fullscreen:
            # Hide everything except the video and its ancestors.
            # This is the only reliable way to prevent score spoilers
            # since F-key fullscreen doesn't work on this page.
            page.evaluate("""() => {
                const video = document.querySelector('video');
                if (!video) return;
                // Mark video and all its ancestors to keep visible
                let el = video;
                while (el && el !== document.documentElement) {
                    el.classList.add('__spoiler_keep__');
                    el = el.parentElement;
                }
                // Inject CSS to hide everything not marked
                const style = document.createElement('style');
                style.id = '__spoiler_style__';
                style.textContent = 'body *:not(.__spoiler_keep__) { display: none !important; }';
                document.head.appendChild(style);
                // Clean up the page chrome
                document.body.style.margin = '0';
                document.body.style.padding = '0';
                document.body.style.background = '#000';
                document.body.style.overflow = 'hidden';
                document.documentElement.style.overflow = 'hidden';
                // Make video fill the viewport
                video.style.width = '100vw';
                video.style.height = '100vh';
                video.style.objectFit = 'contain';
                video.style.position = 'fixed';
                video.style.top = '0';
                video.style.left = '0';
                video.controls = true;
            }""")
            bring_window_to_foreground(page)
            page.wait_for_timeout(500)
            # Try F for web fullscreen as a bonus (may not work on this page)
            for _ in range(5):
                try:
                    page.keyboard.press("f")
                except Exception:
                    pass
                page.wait_for_timeout(400)
            print("已全屏播放。页面已清理，只显示回放视频。看完后关闭浏览器窗口即可。")
        else:
            if activate_picture_in_picture(page, debug=args.visible):
                print("已开启画中画。视频浮窗可拖动、缩放。看完关闭浮窗即可。")
            else:
                for _ in range(5):
                    try:
                        page.keyboard.press("f")
                    except Exception:
                        pass
                    page.wait_for_timeout(600)
                bring_window_to_foreground(page)
                print("已打开播放页。看完后关闭浏览器窗口即可。")

        try:
            page.wait_for_event("close", timeout=0)
        except KeyboardInterrupt:
            pass

    return 0


# ── Node.js fallback worker ──
NODE_WORKER = r"""
const { chromium } = require('playwright');
const readline = require('readline');
const https = require('https');
const http = require('http');

const args = JSON.parse(process.argv[2]);
const MIGU_API = 'https://vms-sc.miguvideo.com/vms-match/v6/staticcache/basic/match-list/normal-match-list/0/10000991/default/1/miguvideo';
const MIGU_LIVE_URL = 'https://www.miguvideo.com/p/live/';

async function fetchJSON(url) {
  return new Promise((resolve, reject) => {
    const mod = url.startsWith('https') ? https : http;
    mod.get(url, { headers: { 'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.miguvideo.com/' } }, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => { try { resolve(JSON.parse(data)); } catch(e) { reject(e); } });
    }).on('error', reject);
  });
}

function formatDate(ts) {
  const d = new Date(ts);
  return (d.getMonth() + 1).toString().padStart(2, '0') + '-' +
         d.getDate().toString().padStart(2, '0') + ' ' +
         d.getHours().toString().padStart(2, '0') + ':' +
         d.getMinutes().toString().padStart(2, '0');
}

function ask(question) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  return new Promise(resolve => rl.question(question, answer => {
    rl.close();
    resolve(answer.trim());
  }));
}

async function getReplayVersions(page) {
  return await page.evaluate(() => {
    const results = [];
    const slides = document.querySelectorAll('.match-review__slide, [class*="swiper-slide"]');
    slides.forEach((slide, idx) => {
      const titleEl = slide.querySelector('.match-review__title, [class*="review__title"]');
      const text = (titleEl ? titleEl.innerText : slide.innerText || '').trim();
      if (text && (text.includes('回放') || text.includes('集锦') || text.includes('纯享'))) {
        results.push({text: text, index: idx});
      }
    });
    return results;
  });
}

async function clickReplayVersion(page, idx) {
  await page.evaluate((index) => {
    const slides = document.querySelectorAll('.match-review__slide, [class*="swiper-slide"]');
    if (slides[index]) slides[index].click();
  }, idx);
}

async function waitForVideo(page, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const hasVideo = await page.evaluate(() => {
      const videos = document.querySelectorAll('video');
      for (const v of videos) {
        if (v.src || v.currentSrc) return true;
      }
      return false;
    });
    if (hasVideo) return true;
    await page.waitForTimeout(500);
  }
  return false;
}

async function waitForReplayVideo(page, timeoutMs = 180000) {
  // Migu ads can be 130+ seconds across multiple ad slots
  // Phase A: wait for any video (ad starts)
  const deadlineA = Date.now() + 15000;
  let adStarted = false;
  while (Date.now() < deadlineA) {
    const hasVideo = await page.evaluate(() => {
      const videos = document.querySelectorAll('video');
      for (const v of videos) {
        if (v.src || v.currentSrc) return true;
      }
      return false;
    });
    if (hasVideo) { adStarted = true; break; }
    await page.waitForTimeout(500);
  }
  if (!adStarted) return false;

  // Phase B: wait for replay video (duration > 300s = 5 min)
  const deadlineB = Date.now() + timeoutMs;
  while (Date.now() < deadlineB) {
    const hasLongVideo = await page.evaluate(() => {
      const videos = document.querySelectorAll('video');
      for (const v of videos) {
        if (v.duration > 300 && (v.src || v.currentSrc)) return true;
      }
      return false;
    });
    if (hasLongVideo) {
      await page.waitForTimeout(2000);
      return true;
    }
    await page.waitForTimeout(2000);
  }
  return false;
}

async function activatePiP(page) {
  return await page.evaluate(async () => {
    function isVisible(el) {
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) return false;
      let p = el;
      while (p) {
        const s = getComputedStyle(p);
        if (s.display === 'none' || s.visibility === 'hidden') return false;
        p = p.parentElement;
      }
      return true;
    }
    const all = Array.from(document.querySelectorAll('video'));
    all.forEach(v => { try { v.muted = true; v.pause(); } catch(e) {} });
    const visible = all.filter(v => v.duration > 0 && isVisible(v));
    if (visible.length === 0) return false;
    visible.sort((a, b) => (b.offsetWidth * b.offsetHeight) - (a.offsetWidth * a.offsetHeight));
    // Prefer longest-duration video (replay, not ad)
    visible.sort((a, b) => b.duration - a.duration);
    const target = visible[0];
    all.forEach(v => {
      if (v !== target) { try { v.pause(); v.removeAttribute('src'); v.src = ''; v.load(); } catch(e) {} }
    });
    target.muted = false;
    try { await target.play(); } catch(e) {}
    await new Promise(r => setTimeout(r, 2000));
    if (document.pictureInPictureEnabled && !document.pictureInPictureElement) {
      try { await target.requestPictureInPicture(); return true; } catch(e) {}
    }
    return false;
  });
}

async function bringToForeground(page) {
  try {
    const cdp = await page.context().newCDPSession(page);
    const { windowId } = await cdp.send('Browser.getWindowForTarget');
    await cdp.send('Browser.setWindowBounds', { windowId, bounds: { windowState: 'normal' } });
    await page.waitForTimeout(400);
    await cdp.send('Browser.setWindowBounds', { windowId, bounds: { windowState: 'maximized' } });
  } catch {
    try { await page.evaluate('window.focus()'); } catch {}
  }
}

(async () => {
  // Phase 1: API
  console.log('正在获取世界杯回放列表...');
  const data = await fetchJSON(MIGU_API);
  if (data.code !== 200) { console.error('API错误:', data.message); process.exit(1); }

  const matchList = data.body.matchList;
  const today = new Date(); today.setHours(0,0,0,0);
  const startDate = new Date(today);
  startDate.setDate(startDate.getDate() - args.days);
  const endDate = new Date(today);
  if (args.days === 0) endDate.setDate(today.getDate());
  else endDate.setDate(endDate.getDate() - 1);

  const matches = [];
  for (const [dayStr, dayMatches] of Object.entries(matchList)) {
    const dayDate = new Date(parseInt(dayStr.substring(0,4)), parseInt(dayStr.substring(4,6))-1, parseInt(dayStr.substring(6,8)));
    if (dayDate < startDate || dayDate > endDate) continue;
    for (const m of dayMatches) {
      if (!m.confrontTeams || m.confrontTeams.length < 2) continue;
      if (m.matchStatus !== '2') continue;
      const home = m.confrontTeams[0], away = m.confrontTeams[1];
      matches.push({
        mgdbId: m.mgdbId,
        title: m.title,
        pkTitle: m.pkInfoTitle || (home.name + ' vs ' + away.name),
        homeTeam: home.name, awayTeam: away.name,
        homeScore: home.score, awayScore: away.score,
        matchGroup: m.matchGroup || '', stage: m.stageRoundName || '',
        matchField: m.matchField || '', startTime: m.matchStartTime || m.startTime,
        presenters: (m.presenters || []).map(p => p.name),
        highlights: m.highlights || ''
      });
    }
  }

  if (!matches.length) { console.log('最近 ' + args.days + ' 天没有已结束的世界杯比赛。'); process.exit(1); }

  if (args.list_only) {
    console.log('\n找到以下世界杯回放场次：');
    matches.forEach((m, i) => {
      const parts = [m.pkTitle, m.matchGroup, m.stage].filter(Boolean).join('  |  ');
      if (m.presenters.length) parts += '  解说: ' + m.presenters.join('、');
      console.log('  ' + String(i+1).padStart(2) + '. [' + formatDate(m.startTime) + '] ' + parts);
    });
    process.exit(0);
  }

  // Select match
  console.log('\n找到以下世界杯回放场次：');
  matches.forEach((m, i) => {
    const parts = [m.pkTitle, m.matchGroup, m.stage].filter(Boolean).join('  |  ');
    if (m.presenters.length) parts += '  解说: ' + m.presenters.join('、');
    console.log('  ' + String(i+1).padStart(2) + '. [' + formatDate(m.startTime) + '] ' + parts);
  });

  let selected;
  if (args.play && args.play !== -1 && args.play >= 1 && args.play <= matches.length) {
    selected = matches[args.play - 1];
  } else {
    while (true) {
      const answer = await ask('\n请输入想看的序号：');
      const idx = parseInt(answer);
      if (idx >= 1 && idx <= matches.length) { selected = matches[idx - 1]; break; }
      console.log('请输入 1 到 ' + matches.length + ' 之间的数字。');
    }
  }

  // Phase 2: Get replay versions
  const liveUrl = MIGU_LIVE_URL + selected.mgdbId;
  console.log('\n正在打开: ' + selected.pkTitle);

  const headlessBrowser = await chromium.launch({ headless: true });
  const headlessPage = await (await headlessBrowser.newContext({ viewport: { width: 1920, height: 1080 } })).newPage();
  await headlessPage.goto(liveUrl, { waitUntil: 'domcontentloaded', timeout: args.timeout });
  await headlessPage.waitForTimeout(8000);
  const versions = await getReplayVersions(headlessPage);
  await headlessBrowser.close();

  if (!versions.length) { console.log('未找到回放版本。'); process.exit(1); }

  console.log('\n本场比赛有以下回放版本：');
  versions.forEach((v, i) => console.log('  ' + (i+1) + '. ' + v.text));

  let chosenVersion;
  if (versions.length === 1) {
    chosenVersion = versions[0];
    console.log('自动选择: ' + chosenVersion.text);
  } else {
    while (true) {
      const answer = await ask('\n请选择回放版本（输入序号）：');
      const idx = parseInt(answer);
      if (idx >= 1 && idx <= versions.length) { chosenVersion = versions[idx - 1]; break; }
      console.log('请输入 1 到 ' + versions.length + ' 之间的数字。');
    }
  }

  // Phase 3: Play
  console.log('\n正在播放: ' + chosenVersion.text);
  const launchOpts = args.visible
    ? { headless: false, args: ['--start-maximized'] }
    : { headless: false, args: ['--start-minimized', '--window-position=-32000,-32000'] };
  const browser = await chromium.launch(launchOpts);
  const context = await browser.newContext({ viewport: null });
  const page = await context.newPage();
  await page.goto(liveUrl, { waitUntil: 'domcontentloaded', timeout: args.timeout });
  await page.waitForTimeout(8000);
  await clickReplayVersion(page, chosenVersion.index);
  console.log('等待广告结束，加载回放视频...');
  if (!(await waitForReplayVideo(page))) {
    if (!(await waitForVideo(page))) { console.log('视频加载超时。'); process.exit(1); }
  }
  await page.waitForTimeout(2000);

  if (args.fullscreen) {
    // Hide everything except the video and its ancestors
    await page.evaluate(`() => {
      const video = document.querySelector('video');
      if (!video) return;
      let el = video;
      while (el && el !== document.documentElement) {
        el.classList.add('__spoiler_keep__');
        el = el.parentElement;
      }
      const style = document.createElement('style');
      style.id = '__spoiler_style__';
      style.textContent = 'body *:not(.__spoiler_keep__) { display: none !important; }';
      document.head.appendChild(style);
      document.body.style.margin = '0';
      document.body.style.padding = '0';
      document.body.style.background = '#000';
      document.body.style.overflow = 'hidden';
      document.documentElement.style.overflow = 'hidden';
      video.style.width = '100vw';
      video.style.height = '100vh';
      video.style.objectFit = 'contain';
      video.style.position = 'fixed';
      video.style.top = '0';
      video.style.left = '0';
      video.controls = true;
    }`);
    await bringToForeground(page);
    await page.waitForTimeout(500);
    for (let i = 0; i < 5; i++) { try { await page.keyboard.press('f'); } catch {} await page.waitForTimeout(400); }
    console.log('已全屏播放。页面已清理，只显示回放视频。');
  } else if (await activatePiP(page)) {
    console.log('已开启画中画。');
  } else {
    for (let i = 0; i < 5; i++) { try { await page.keyboard.press('f'); } catch {} await page.waitForTimeout(600); }
    await bringToForeground(page);
    console.log('已打开播放页。');
  }
  await page.waitForEvent('close', { timeout: 0 }).catch(() => {});
})().catch(error => {
  console.error(error && error.message ? error.message : error);
  process.exit(1);
});
"""


def find_node_runtime() -> tuple[str, str] | None:
    candidates = []
    path_node = shutil.which("node")
    if path_node:
        candidates.append(Path(path_node))
    bundled = (
        Path.home()
        / ".cache"
        / "codex-runtimes"
        / "codex-primary-runtime"
        / "dependencies"
        / "node"
        / "bin"
        / ("node.exe" if os.name == "nt" else "node")
    )
    candidates.append(bundled)

    for node in candidates:
        node_modules = node.parent.parent / "node_modules"
        if not node.exists() or not (node_modules / "playwright").exists():
            continue
        env = os.environ.copy()
        env["NODE_PATH"] = str(node_modules)
        probe = subprocess.run(
            [str(node), "-e", "require('playwright'); process.stdout.write('ok')"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode == 0:
            return str(node), str(node_modules)
    return None


def run_with_node(args: argparse.Namespace) -> int:
    runtime = find_node_runtime()
    if not runtime:
        print(
            "缺少 Playwright。请安装 Python 版：python -m pip install playwright；"
            "然后运行：python -m playwright install chromium"
        )
        return 2

    node, node_modules = runtime
    payload = json.dumps(
        {
            "show_all": args.show_all,
            "play": args.play,
            "visible": args.visible,
            "fullscreen": args.fullscreen,
            "timeout": args.timeout,
            "days": args.days,
            "list_only": args.list_only,
        },
        ensure_ascii=False,
    )
    env = os.environ.copy()
    env["NODE_PATH"] = node_modules
    with tempfile.NamedTemporaryFile("w", suffix=".cjs", delete=False, encoding="utf-8") as handle:
        handle.write(NODE_WORKER)
        worker_path = handle.name
    try:
        completed = subprocess.run([node, worker_path, payload], env=env, check=False)
        return int(completed.returncode)
    finally:
        try:
            os.remove(worker_path)
        except OSError:
            pass


def import_playwright():
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, None
    return sync_playwright, PlaywrightTimeoutError


def run(args: argparse.Namespace) -> int:
    sync_playwright, _ = import_playwright()
    if sync_playwright is None:
        return run_with_node(args)
    return run_python(args)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="打开咪咕视频世界杯回放，列出可观看场次及回放版本并自动播放。\n"
                    "默认交互播放。加 --list-only 只看列表不播放；加 --play N 跳过选择直接播放第 N 场。"
    )
    parser.add_argument("--show-all", action="store_true", help="显示更宽泛的链接（已废弃，保留兼容）")
    parser.add_argument("--list-only", action="store_true", help="只列出候选比赛，不进入播放页")
    parser.add_argument(
        "--play", nargs="?", const=-1, type=int, default=None,
        help="直接播放指定序号的场次（--play N），省略交互选择。",
    )
    parser.add_argument("--timeout", type=int, default=30000, help="页面等待超时，单位毫秒")
    parser.add_argument("--fullscreen", action="store_true", help="网页全屏模式（替代默认画中画），全屏后浏览器弹到前台")
    parser.add_argument("--visible", action="store_true", help="调试模式：浏览器全程可见，不最小化")
    parser.add_argument(
        "--days", type=int, default=0,
        help="显示最近 N 天的比赛（默认 0 = 今天）。1 = 昨天，2 = 最近 2 天，以此类推。",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(run(parse_args(sys.argv[1:])))