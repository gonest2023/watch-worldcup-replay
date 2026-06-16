#!/usr/bin/env python3
"""Open CCTV Sports World Cup replay videos with spoiler-light selection."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin


DEFAULT_URL = "https://cbs.sports.cctv.com/index.html"
VENDOR_DIR = Path(__file__).resolve().parent / "vendor"
if VENDOR_DIR.exists():
    sys.path.insert(0, str(VENDOR_DIR))
WORLD_CUP_TERMS = ("世界杯", "FIFA", "World Cup")
VIDEO_TERMS = (
    "集锦",
    "回放",
    "录像",
    "视频",
    "全场",
    "完整",
    "完整版",
    "赛事回放",
    "战报",
)
NOISE_TERMS = ("积分榜", "赛程", "排名", "预测", "前瞻", "直播中", "未开始", "直播")
SCORE_PATTERNS = (
    re.compile(r"\b\d+\s*[-:：]\s*\d+\b"),
    re.compile(r"\d+\s*比\s*\d+"),
    re.compile(r"(\S+)\s+\d+\s+(\S+)\s+\d+"),
)


@dataclass(frozen=True)
class MatchLink:
    title: str
    url: str


def import_playwright():
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        return None, None
    return sync_playwright, PlaywrightTimeoutError


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


NODE_WORKER = r"""
const { chromium } = require('playwright');
const readline = require('readline');
const os = require('os');

const args = JSON.parse(process.argv[2]);
const worldCupTerms = ['世界杯', 'FIFA', 'World Cup'];
const videoTerms = ['集锦', '回放', '录像', '视频', '全场', '完整', '完整版', '赛事回放', '战报'];
const noiseTerms = ['积分榜', '赛程', '排名', '预测', '前瞻', '直播中', '未开始', '直播'];
const scorePatterns = [/\b\d+\s*[-:：]\s*\d+\b/g, /\d+\s*比\s*\d+/g, /(\S+)\s+\d+\s+(\S+)\s+\d+/g];

function normalizeText(value) {
  return (value || '').replace(/\s+/g, ' ').trim()
    .replace(/^[\[\(（【]?\s*(?:视频|集锦|回放)\s*[\]\)）】]?\s*/, '');
}

function spoilerLightTitle(title) {
  let cleaned = normalizeText(title);
  cleaned = cleaned.replace(scorePatterns[0], '[比分已隐藏]');
  cleaned = cleaned.replace(scorePatterns[1], '[比分已隐藏]');
  cleaned = cleaned.replace(scorePatterns[2], '$1 [比分已隐藏] $2 [比分已隐藏]');
  return cleaned;
}

function includesAny(value, terms) {
  const lower = value.toLowerCase();
  return terms.some(term => lower.includes(term.toLowerCase()));
}

function looksLikeVideo(text, href) {
  if (args.show_all) return true;
  const combined = `${text} ${href}`;
  if (noiseTerms.some(term => combined.includes(term))) return false;
  return videoTerms.some(term => combined.includes(term));
}

async function collectLinks(page) {
  const anchors = await page.locator('a').evaluateAll(els => els.map(a => ({
    text: (a.innerText || a.textContent || a.title || '').trim(),
    href: a.href || a.getAttribute('href') || '',
    title: a.title || ''
  })));
  const seen = new Set();
  const links = [];
  for (const anchor of anchors) {
    const text = normalizeText(anchor.text || anchor.title || '');
    const href = anchor.href || '';
    if (!text || !href || href.startsWith('javascript:')) continue;
    if (!includesAny(`${text} ${href}`, worldCupTerms)) continue;
    if (!looksLikeVideo(text, href)) continue;
    const url = new URL(href, args.url).href.split('#')[0];
    if (seen.has(url)) continue;
    seen.add(url);
    links.push({ title: spoilerLightTitle(text), url });
  }
  return links;
}

function ask(question) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  return new Promise(resolve => rl.question(question, answer => {
    rl.close();
    resolve(answer.trim());
  }));
}

function printMatches(matches) {
  console.log('\n找到以下可观看的世界杯相关视频：');
  matches.forEach((match, index) => console.log(`${index + 1}. ${match.title}`));
}

async function chooseMatch(matches, directIndex) {
  printMatches(matches);
  if (directIndex !== undefined && directIndex !== null && directIndex !== -1) {
    if (directIndex >= 1 && directIndex <= matches.length) return matches[directIndex - 1];
    console.log(`序号 ${directIndex} 无效，请输入 1 到 ${matches.length} 之间的数字。`);
    return null;
  }
  while (true) {
    const answer = await ask('\n请输入想看的序号：');
    const index = Number(answer);
    if (Number.isInteger(index) && index >= 1 && index <= matches.length) return matches[index - 1];
    console.log(`请输入 1 到 ${matches.length} 之间的数字。`);
  }
}

async function tryClickFirst(locator, timeout = 2500) {
  try {
    if (await locator.count() === 0) return false;
    await locator.first().click({ timeout });
    return true;
  } catch {
    return false;
  }
}

async function startVideo(page) {
  await page.waitForLoadState('domcontentloaded');
  await page.waitForTimeout(2500);
  // 1. 先停掉所有视频（集锦自动在播）
  try { await page.evaluate("document.querySelectorAll('video').forEach(v => { v.pause(); v.muted = true; })"); } catch {}
  // 2. 点击「回放」标签
  for (const selector of [
    'text=回放', "button:has-text('回放')", "[title*='回放']",
    "a:has-text('回放')", "span:has-text('回放')", "li:has-text('回放')",
    ".tab-item:has-text('回放')", "[data-tab='回放']"
  ]) {
    if (await tryClickFirst(page.locator(selector))) {
      await page.waitForTimeout(2000);
      break;
    }
  }
  // 3. 再次停掉所有视频（切换 tab 后集锦可能又启动了）
  try { await page.evaluate("document.querySelectorAll('video').forEach(v => { v.pause(); v.muted = true; })"); } catch {}
  await page.waitForTimeout(500);
  // 4. 点击播放
  for (const selector of [
    "button:has-text('播放')", 'text=播放', '.vjs-big-play-button',
    '.xgplayer-start', '.xgplayer-play', 'video'
  ]) {
    if (await tryClickFirst(page.locator(selector))) break;
  }
  await page.waitForTimeout(2000);
}

async function activatePictureInPicture(page) {
  try {
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
      const target = visible[0];
      all.forEach(v => {
        if (v !== target) {
          try { v.pause(); v.removeAttribute('src'); v.src = ''; v.load(); } catch(e) {}
        }
      });
      target.muted = false;
      try { await target.play(); } catch(e) {}
      await new Promise(r => setTimeout(r, 2000));
      if (document.pictureInPictureEnabled && !document.pictureInPictureElement) {
        try { await target.requestPictureInPicture(); return true; } catch(e) {}
      }
      return false;
    });
  } catch { return false; }
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
  // Phase 1: 后台采集
  const headlessBrowser = await chromium.launch({ headless: true });
  const headlessContext = await headlessBrowser.newContext({ viewport: { width: 1920, height: 1080 } });
  const headlessPage = await headlessContext.newPage();
  console.log('正在获取世界杯视频列表...');
  await headlessPage.goto(args.url, { waitUntil: 'domcontentloaded', timeout: args.timeout });
  try { await headlessPage.waitForLoadState('networkidle', { timeout: Math.min(args.timeout, 15000) }); } catch {}
  const matches = await collectLinks(headlessPage);
  await headlessBrowser.close();

  if (!matches.length) {
    console.log('没有找到带回放/集锦标记的世界杯视频。你可以加 --show-all 再试一次。');
    process.exit(1);
  }

  // --list-only：只看列表
  if (args.list_only) {
    printMatches(matches);
    process.exit(0);
  }

  // 选择场次
  const directIndex = (args.play !== undefined && args.play !== null && args.play !== -1) ? args.play : null;
  const selected = await chooseMatch(matches, directIndex);
  if (!selected) { process.exit(1); }

  // Phase 2: 浏览器播放
  console.log(`\n正在打开：${selected.title}`);
  const launchOpts = args.visible
    ? { headless: false, args: ['--start-maximized'] }
    : { headless: false, args: ['--start-minimized', '--window-position=-32000,-32000'] };
  const browser = await chromium.launch(launchOpts);
  const context = await browser.newContext({ viewport: null });
  let page = await context.newPage();
  await page.goto(selected.url, { waitUntil: 'domcontentloaded', timeout: args.timeout });
  // 关掉多余标签页（集锦页面可能额外开了），只保留最新的
  await page.waitForTimeout(2000);
  const pages = context.pages();
  if (pages.length > 1) {
    for (const p of pages.slice(0, -1)) {
      try { await p.close(); } catch {}
    }
    page = pages[pages.length - 1];
    await page.bringToFront();
  }
  await startVideo(page);
  await page.waitForTimeout(1000);
  if (args.fullscreen) {
    for (let i = 0; i < 5; i++) {
      try { await page.keyboard.press('f'); } catch {}
      await page.waitForTimeout(600);
    }
    await bringToForeground(page);
    console.log('已全屏播放。看完后关闭浏览器窗口即可。');
  } else if (await activatePictureInPicture(page)) {
    console.log('已开启画中画。视频浮窗可拖动、缩放。看完关闭浮窗即可。');
  } else {
    for (let i = 0; i < 5; i++) {
      try { await page.keyboard.press('f'); } catch {}
      await page.waitForTimeout(600);
    }
    await bringToForeground(page);
    console.log('已打开播放页。看完后关闭浏览器窗口即可。');
  }
  await page.waitForEvent('close', { timeout: 0 }).catch(() => {});
})().catch(error => {
  console.error(error && error.message ? error.message : error);
  process.exit(1);
});
"""


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
            "url": args.url,
            "show_all": args.show_all,
            "play": args.play,
            "visible": args.visible,
            "fullscreen": args.fullscreen,
            "timeout": args.timeout,
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


def normalize_text(value: str) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    value = re.sub(r"^[\[\(（【]?\s*(?:视频|集锦|回放)\s*[\]\)）】]?\s*", "", value)
    return value


def spoiler_light_title(title: str) -> str:
    cleaned = normalize_text(title)
    # 带分隔符的比分（2-0, 2:0, 2比0）：整个替换
    cleaned = SCORE_PATTERNS[0].sub("[比分已隐藏]", cleaned)
    cleaned = SCORE_PATTERNS[1].sub("[比分已隐藏]", cleaned)
    # 「队名 数字 队名 数字」格式：保留队名，只隐藏比分
    cleaned = SCORE_PATTERNS[2].sub(r"\1 [比分已隐藏] \2 [比分已隐藏]", cleaned)
    return cleaned


def looks_world_cup(text: str) -> bool:
    return any(term.lower() in text.lower() for term in WORLD_CUP_TERMS)


def looks_like_video(text: str, href: str, show_all: bool) -> bool:
    if show_all:
        return True
    combined = f"{text} {href}"
    if any(term in combined for term in NOISE_TERMS):
        return False
    return any(term in combined for term in VIDEO_TERMS)


def dedupe_links(items: Iterable[MatchLink]) -> list[MatchLink]:
    seen: set[str] = set()
    result: list[MatchLink] = []
    for item in items:
        key = item.url.split("#", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def collect_links(page, base_url: str, show_all: bool) -> list[MatchLink]:
    anchors = page.locator("a").evaluate_all(
        """els => els.map(a => ({
            text: (a.innerText || a.textContent || a.title || '').trim(),
            href: a.href || a.getAttribute('href') || '',
            title: a.title || ''
        }))"""
    )
    links: list[MatchLink] = []
    for anchor in anchors:
        text = normalize_text(anchor.get("text") or anchor.get("title") or "")
        href = anchor.get("href") or ""
        if not text or not href or href.startswith("javascript:"):
            continue
        if not looks_world_cup(text + " " + href):
            continue
        if not looks_like_video(text, href, show_all):
            continue
        links.append(MatchLink(title=spoiler_light_title(text), url=urljoin(base_url, href)))
    return dedupe_links(links)


def choose_match(matches: list[MatchLink], direct_index: int | None = None) -> MatchLink | None:
    print("\n找到以下可观看的世界杯相关视频：")
    for index, match in enumerate(matches, start=1):
        print(f"{index}. {match.title}")

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


def try_click_first(locator, timeout: int = 2500) -> bool:
    try:
        if locator.count() == 0:
            return False
        locator.first.click(timeout=timeout)
        return True
    except Exception:
        return False


def activate_picture_in_picture(page, debug: bool = False) -> bool:
    """对回放视频开启画中画：搜索主页面+所有iframe，找到可见视频并销毁其他。"""
    # 构建一个在所有 frame 中执行的聚合脚本
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

        // 收集当前 frame 及其子 iframe 中的所有 video
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
        log('video元素总数(含iframe): ' + all.length);

        all.forEach(v => { try { v.muted = true; v.pause(); } catch(e) {} });
        log('全部暂停+静音完成');

        const visible = all.filter(v => v.duration > 0 && isVisible(v));
        log('可见且有duration的: ' + visible.length + ' 个');
        visible.forEach((v, i) => {
            log('  [' + i + '] ' + v.offsetWidth + 'x' + v.offsetHeight +
                ' dur=' + v.duration.toFixed(1) + 's paused=' + v.paused +
                ' src=' + (v.src||v.currentSrc||'(none)').substring(0, 60));
        });

        if (visible.length === 0) {
            log('❌ 没有可见视频');
            return {ok: false, dbg: dbg};
        }

        visible.sort((a, b) => (b.offsetWidth * b.offsetHeight) - (a.offsetWidth * a.offsetHeight));
        const target = visible[0];
        log('选中目标: ' + target.offsetWidth + 'x' + target.offsetHeight + ' dur=' + target.duration.toFixed(1));

        let destroyed = 0;
        all.forEach(v => {
            if (v !== target) {
                try { v.pause(); v.removeAttribute('src'); v.src = ''; v.load(); destroyed++; } catch(e) {}
            }
        });
        log('已销毁其他视频: ' + destroyed + ' 个');

        target.muted = false;
        try { await target.play(); } catch(e) { log('play异常: ' + e.message); }
        await new Promise(r => setTimeout(r, 2000));

        log('PiP可用=' + document.pictureInPictureEnabled + ' 已有PiP=' + !!document.pictureInPictureElement);
        log('目标状态: paused=' + target.paused + ' readyState=' + target.readyState);

        if (document.pictureInPictureEnabled && !document.pictureInPictureElement) {
            try {
                await target.requestPictureInPicture();
                log('✅ PiP 成功');
                return {ok: true, dbg: dbg};
            } catch(e) {
                log('❌ PiP 失败: ' + e.message);
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
            print("[DEBUG] --- PiP JS 日志结束 ---")
        return result.get("ok", False) if isinstance(result, dict) else bool(result)
    except Exception as e:
        if debug:
            print(f"[DEBUG] activate_picture_in_picture 异常: {e}")
        return False


def bring_window_to_foreground(page) -> None:
    """后备方案：通过 CDP 将浏览器窗口恢复到前台并最大化。"""
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


def start_video(page, debug: bool = False) -> None:
    """点击回放标签 + 播放按钮，启动视频播放。会搜索主页面和所有 iframe。"""
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(3000)

    replay_selectors = [
        "text=回放",
        "button:has-text('回放')",
        "[title*='回放']",
        "a:has-text('回放')",
        "span:has-text('回放')",
        "li:has-text('回放')",
        ".tab-item:has-text('回放')",
        "[data-tab='回放']",
    ]
    play_selectors = [
        "button:has-text('播放')",
        "text=播放",
        ".vjs-big-play-button",
        ".xgplayer-start",
        ".xgplayer-play",
        "video",
    ]

    # 1. 记录当前视频 src 和 duration，用于判断回放是否真的加载了
    old_src = ""
    old_dur = 0
    try:
        old_src = page.evaluate("() => { const v = document.querySelector('video'); return v ? (v.src||v.currentSrc||'') : ''; }")
        old_dur = page.evaluate("() => { const v = document.querySelector('video'); return v ? v.duration : 0; }")
    except Exception:
        pass
    if debug:
        print(f"[DEBUG] 初始视频 dur={old_dur:.0f}s src={old_src[:80]}")

    # 2. 在主页面和所有 iframe 中搜索并点击「回放」
    clicked_replay = False
    for frame in page.frames:
        for selector in replay_selectors:
            try:
                loc = frame.locator(selector)
                if loc.count() > 0:
                    loc.first.click(timeout=2500)
                    clicked_replay = True
                    if debug:
                        fname = frame.name or (frame.url[:60] if frame != page.main_frame else "主页面")
                        print(f"[DEBUG] 在 [{fname}] 中点击了回放 (selector={selector})")
                    break
            except Exception:
                continue
        if clicked_replay:
            break

    if not clicked_replay and debug:
        print("[DEBUG] ⚠ 未找到回放标签！")

    if clicked_replay:
        # 等回放内容加载：轮询 video src 变化或 duration 变长
        for i in range(10):
            page.wait_for_timeout(1500)
            try:
                new_src = page.evaluate("() => { const v = document.querySelector('video'); return v ? (v.src||v.currentSrc||'') : ''; }")
                new_dur = page.evaluate("() => { const v = document.querySelector('video'); return v ? v.duration : 0; }")
                if debug:
                    print(f"[DEBUG] 等待回放加载 [{i+1}/10] dur={new_dur:.0f}s src={new_src[:60]}...")
                if new_src and new_src != old_src:
                    if debug:
                        print(f"[DEBUG] ✅ src 已变化，回放已加载")
                    break
                if new_dur > 300:  # 回放通常超过 5 分钟
                    if debug:
                        print(f"[DEBUG] ✅ duration > 5min，回放已加载")
                    break
            except Exception:
                pass

    # 3. 暂停所有视频
    page.wait_for_timeout(1000)
    try:
        page.evaluate("document.querySelectorAll('video').forEach(v => { v.pause(); v.muted = true; })")
    except Exception:
        pass
    page.wait_for_timeout(500)

    # 4. 点击播放（也在 iframe 中搜索）
    for frame in page.frames:
        for selector in play_selectors:
            try:
                loc = frame.locator(selector)
                if loc.count() > 0:
                    loc.first.click(timeout=2500)
                    break
            except Exception:
                continue

    page.wait_for_timeout(2000)


def _find_replay_url(page, debug: bool = False) -> str | None:
    """在所有 frame 中搜索「回放」标签，提取其链接地址。"""
    js = """() => {
        // 搜索当前文档及子 iframe 中的回放链接
        function searchDoc(doc) {
            const candidates = [];
            // 找所有 a 标签带「回放」文字
            for (const a of doc.querySelectorAll('a')) {
                if (a.innerText.includes('回放') || a.textContent.includes('回放')) {
                    const href = a.getAttribute('href') || a.href || '';
                    if (href && !href.startsWith('javascript:')) {
                        candidates.push(href);
                    }
                }
            }
            // 找所有带「回放」的元素，检查 data-url/data-href 等
            for (const el of doc.querySelectorAll('[data-url*="回放"], [data-href*="回放"]')) {
                const u = el.getAttribute('data-url') || el.getAttribute('data-href');
                if (u) candidates.push(u);
            }
            // 递归 iframe
            for (const iframe of doc.querySelectorAll('iframe')) {
                try {
                    if (iframe.contentDocument) {
                        candidates.push(...searchDoc(iframe.contentDocument));
                    }
                } catch(e) {}
            }
            return candidates;
        }

        const urls = searchDoc(document);
        // 也搜通过 frame 对象可以访问的文档
        for (let i = 0; i < window.frames.length; i++) {
            try {
                const fdoc = window.frames[i].document;
                urls.push(...searchDoc(fdoc));
            } catch(e) {}
        }

        // 优先选包含 replay/full/回放 关键字的 URL
        const replayUrls = urls.filter(u => /replay|full|回放|全场/i.test(u));
        return (replayUrls.length > 0 ? replayUrls[0] : (urls.length > 0 ? urls[0] : null));
    }"""
    try:
        for frame in page.frames:
            try:
                url = frame.evaluate(js)
                if url:
                    full_url = urljoin(frame.url, url)
                    if debug:
                        fname = frame.name or (frame.url[:50] if frame != page.main_frame else "主页面")
                        print(f"[DEBUG] 在 [{fname}] 找到回放链接: {full_url[:120]}")
                    return full_url
            except Exception:
                continue
    except Exception:
        pass
    return None


def run(args: argparse.Namespace) -> int:
    sync_playwright, PlaywrightTimeoutError = import_playwright()
    if sync_playwright is None:
        return run_with_node(args)

    # ── Phase 1: 后台（headless）采集链接 ──
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()
        print("正在获取世界杯视频列表...")
        page.goto(args.url, wait_until="domcontentloaded", timeout=args.timeout)
        try:
            page.wait_for_load_state("networkidle", timeout=min(args.timeout, 15000))
        except PlaywrightTimeoutError:
            pass

        matches = collect_links(page, args.url, args.show_all)
        browser.close()

    if not matches:
        print("没有找到带回放/集锦标记的世界杯视频。你可以加 --show-all 再试一次。")
        return 1

    # ── 选择场次 ──
    if args.list_only:
        print("\n找到以下可观看的世界杯相关视频：")
        for index, match in enumerate(matches, start=1):
            print(f"{index}. {match.title}")
        return 0

    if args.play is not None and args.play != -1:
        # --play N：跳过交互直接播放
        selected = choose_match(matches, direct_index=args.play)
    else:
        # 默认 / --play（无序号）：交互选择
        selected = choose_match(matches)

    if selected is None:
        return 1

    # ── Phase 2: 浏览器播放 ──
    print(f"\n正在打开：{selected.title}")
    with sync_playwright() as playwright:
        if args.visible:
            browser = playwright.chromium.launch(headless=False, args=["--start-maximized"])
        else:
            browser = playwright.chromium.launch(
                headless=False, args=["--start-minimized", "--window-position=-32000,-32000"]
            )
        context = browser.new_context(viewport=None)

        page = context.new_page()
        page.goto(selected.url, wait_until="domcontentloaded", timeout=args.timeout)
        page.wait_for_timeout(3000)

        # ── 尝试获取回放直链，关旧开新 ──
        replay_url = _find_replay_url(page, debug=args.visible)
        if replay_url:
            if args.visible:
                print(f"[DEBUG] 找到回放直链，跳转: {replay_url[:100]}")
            page.goto(replay_url, wait_until="domcontentloaded", timeout=args.timeout)
            page.wait_for_timeout(2000)
        elif args.visible:
            print("[DEBUG] 未找到回放直链，尝试页面内点击切换")
        start_video(page, debug=args.visible)
        page.wait_for_timeout(1000)

        # 调试：列出页面所有 video 元素
        if args.visible:
            try:
                vdbg = page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('video')).map((v, i) => ({
                        idx: i,
                        src: (v.src || v.currentSrc || '').substring(0, 80),
                        w: v.offsetWidth, h: v.offsetHeight,
                        dur: v.duration, paused: v.paused,
                        rect: (() => { const r = v.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height}; })()
                    }));
                }""")
                print(f"\n[DEBUG] 页面 video 元素 ({len(vdbg)} 个):")
                for v in vdbg:
                    print(f"  [{v['idx']}] {v['w']}x{v['h']} dur={v['dur']:.1f}s paused={v['paused']} rect=({v['rect']['x']:.0f},{v['rect']['y']:.0f} {v['rect']['w']:.0f}x{v['rect']['h']:.0f}) src={v['src']}")
            except Exception as e:
                print(f"[DEBUG] 读取 video 信息失败: {e}")

        if args.fullscreen:
            # ── 网页全屏模式：F 全屏后弹到前台 ──
            for _ in range(5):
                try:
                    page.keyboard.press("f")
                except Exception:
                    pass
                page.wait_for_timeout(600)
            bring_window_to_foreground(page)
            print("已全屏播放。看完后关闭浏览器窗口即可。")
        else:
            # ── 默认画中画模式 ──
            if activate_picture_in_picture(page, debug=args.visible):
                print("已开启画中画。视频浮窗可拖动、缩放。看完关闭浮窗即可。")
            else:
                # 画中画失败，后备全屏
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


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="打开央视体育世界杯回放，列出可观看场次并自动进入播放页。\n"
                    "默认交互播放。加 --list-only 只看列表不播放；加 --play N 跳过选择直接播放第 N 场。"
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="央视体育入口页面")
    parser.add_argument("--show-all", action="store_true", help="显示更宽泛的世界杯链接")
    parser.add_argument("--list-only", action="store_true", help="只列出候选视频，不进入播放页")
    parser.add_argument(
        "--play", nargs="?", const=-1, type=int, default=None,
        help="直接播放指定序号的场次（--play N），省略交互选择。",
    )
    parser.add_argument("--timeout", type=int, default=30000, help="页面等待超时，单位毫秒")
    parser.add_argument("--fullscreen", action="store_true", help="网页全屏模式（替代默认画中画），全屏后浏览器弹到前台")
    parser.add_argument("--visible", action="store_true", help="调试模式：浏览器全程可见，不最小化")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(run(parse_args(sys.argv[1:])))
