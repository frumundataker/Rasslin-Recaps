#!/usr/bin/env python3
"""
WWE Recap Bot
Polls 411mania live coverage during Raw / SmackDown / NXT / PLEs,
posts segment-by-segment updates to a Telegram channel, and posts a
full recap when the show goes off the air.

Designed to run on a GitHub Actions cron (every ~10 min during show windows).
State is persisted as JSON files committed back to the repo.

Env vars:
  TELEGRAM_BOT_TOKEN   (required) from @BotFather
  TELEGRAM_CHAT_ID     (required) @channelhandle or -100... numeric id
  ANTHROPIC_API_KEY    (optional) enables Claude-written summaries
  CLAUDE_MODEL         (optional) default: claude-haiku-4-5
  SPOILER_MODE         (optional) "1" wraps results in Telegram spoiler tags
"""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

ET = ZoneInfo("America/New_York")
BASE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(BASE, "state")
LISTING_URL = "https://411mania.com/wrestling/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 wwe-recap-bot"
}

CLOSER_RE = re.compile(
    r"(that'?s (it|all)( for)?|so long for now|thanks for (joining|watching|reading)"
    r"|good ?night|end of (the )?show|as our live event comes to an end"
    r"|see you (next|on)|show comes to (a close|an end))",
    re.I,
)
BOILERPLATE_RE = re.compile(
    r"(newsletter|subscribe|comment section|social media|e-?mail is a better option"
    r"|keep refreshing|don'?t be a dick|share on|click here to do so)",
    re.I,
)
WINNER_RE = re.compile(r"^winner", re.I)
MATCH_RE = re.compile(r"\bvs\.?\b", re.I)


# ---------------------------------------------------------------- config

def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def load_shows():
    return load_json(os.path.join(BASE, "shows.json"), {})


def load_events():
    return load_json(os.path.join(BASE, "events.json"), [])


def active_show(now_et, shows, events):
    """Return (key, cfg) for whichever show should be live right now, else None."""
    today = now_et.date()
    hhmm = now_et.strftime("%H:%M")

    for ev in events:
        if ev.get("date") == today.isoformat():
            start, end = ev.get("window", ["18:00", "23:59"])
            if start <= hhmm <= end:
                key = "ple-" + re.sub(r"[^a-z0-9]+", "-", ev["name"].lower()).strip("-")
                return key, {
                    "label": ev["name"],
                    "keywords": ev.get("keywords", []),
                    "slug": ev.get("slug"),
                    "window_end": end,
                    "is_ple": True,
                }

    for key, cfg in shows.items():
        if now_et.weekday() == cfg["weekday"]:
            start, end = cfg["window"]
            if start <= hhmm <= end:
                return key, {
                    "label": cfg["label"],
                    "keywords": cfg.get("keywords", [key]),
                    "slug": key,
                    "window_end": end,
                    "is_ple": False,
                }
    return None, None


# ---------------------------------------------------------------- fetching

def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def find_coverage_url(show_key, cfg, now_et):
    """Locate today's 411mania live coverage article."""
    keywords = [k.lower() for k in cfg["keywords"]]

    # 1) scan the wrestling front page for a "Join 411's Live ... Coverage" link
    try:
        soup = BeautifulSoup(fetch(LISTING_URL), "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].lower()
            if "join-411s-live" not in href:
                continue
            if any(k in href for k in keywords):
                return a["href"]
    except Exception as e:
        print(f"listing scan failed: {e}")

    # 2) fall back to the predictable slug pattern, e.g.
    #    join-411s-live-wwe-raw-coverage-6-29-26
    if cfg.get("slug"):
        d = now_et
        candidate = (
            f"https://411mania.com/wrestling/join-411s-live-wwe-"
            f"{cfg['slug']}-coverage-{d.month}-{d.day}-{d.strftime('%y')}/"
        )
        try:
            requests.get(candidate, headers=HEADERS, timeout=30).raise_for_status()
            return candidate
        except Exception:
            pass
    return None


# ---------------------------------------------------------------- parsing

def extract_blocks(html_text):
    """Return ordered list of {text, bold, kind} blocks from the article body."""
    soup = BeautifulSoup(html_text, "html.parser")

    container = None
    for sel in ("div.entry-content", "div.article-content", "div.post-content",
                "div.td-post-content", "article"):
        container = soup.select_one(sel)
        if container:
            break
    if container is None:
        container = soup

    blocks = []
    for p in container.find_all("p"):
        text = " ".join(p.get_text(" ", strip=True).split())
        if not text or len(text) < 25:
            continue
        if BOILERPLATE_RE.search(text) and len(text) < 400:
            continue
        # stop once we hit trailing related-stories junk
        if text.lower().startswith(("more trending", "article topics")):
            break
        strong = p.find("strong")
        bold = " ".join(strong.get_text(" ", strip=True).split()) if strong else ""

        if bold and WINNER_RE.match(bold):
            kind = "winner"
        elif bold and MATCH_RE.search(bold) and bold.rstrip().endswith(":"):
            kind = "match"
        else:
            kind = "segment"
        blocks.append({"text": text, "bold": bold, "kind": kind})
    return blocks


def block_hash(block):
    norm = re.sub(r"\s+", " ", block["text"].lower()).strip()
    return hashlib.sha1(norm.encode()).hexdigest()


# ---------------------------------------------------------------- telegram

def tg_send(text, disable_preview=True, dry_run=False):
    if dry_run:
        print("---- TELEGRAM (dry run) ----")
        print(text)
        print("----------------------------")
        return
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    # Telegram hard limit is 4096 chars; chunk on paragraph boundaries
    chunks, cur = [], ""
    for para in text.split("\n\n"):
        if len(cur) + len(para) + 2 > 3900:
            chunks.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        chunks.append(cur)
    for chunk in chunks:
        r = requests.post(url, json={
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": disable_preview,
        }, timeout=30)
        if not r.ok:
            print(f"telegram error: {r.status_code} {r.text}")
            r.raise_for_status()


def spoiler(text):
    if os.environ.get("SPOILER_MODE") == "1":
        return f"<tg-spoiler>{text}</tg-spoiler>"
    return text


# ---------------------------------------------------------------- claude (optional)

def claude(prompt, max_tokens=800):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5"),
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        r.raise_for_status()
        return r.json()["content"][0]["text"].strip()
    except Exception as e:
        print(f"claude call failed, falling back: {e}")
        return None


# ---------------------------------------------------------------- formatting

def excerpt(text, n=220):
    text = text.strip()
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "…"


def format_segment_update(label, new_blocks, url):
    """One Telegram message covering the newly-seen blocks."""
    body_text = "\n\n".join(b["text"] for b in new_blocks)
    summary = claude(
        "You are a wrestling news bot. Summarize the following live-coverage "
        "excerpt into 1-4 short, punchy bullet updates (present tense, one line "
        "each, plain text, no markdown, results stated plainly). Only include "
        "what actually happened; skip author banter and filler.\n\n" + body_text,
        max_tokens=400,
    )
    lines = [f"🔴 <b>{escape(label)} — live update</b>"]
    if summary:
        for ln in summary.splitlines():
            ln = ln.strip().lstrip("-•* ")
            if ln:
                lines.append(spoiler(f"▪️ {escape(ln)}"))
    else:
        for b in new_blocks:
            if b["kind"] == "winner":
                lines.append(spoiler(f"🏁 <b>{escape(b['bold'])}</b>"))
            elif b["kind"] == "match":
                lines.append(f"🔔 Match underway: <b>{escape(b['bold'].rstrip(':'))}</b>")
            else:
                lines.append(spoiler(f"▪️ {escape(excerpt(b['text']))}"))
    lines.append(f'\n<a href="{escape(url)}">Full live coverage → 411mania</a>')
    return "\n".join(lines)


def format_recap(label, state, now_et):
    blocks = state["blocks"]
    results = []
    current_match = None
    for b in blocks:
        if b["kind"] == "match":
            current_match = b["bold"].rstrip(":").strip()
        elif b["kind"] == "winner":
            w = b["bold"].rstrip(".")
            results.append(f"{w}" + (f" — {current_match}" if current_match else ""))
            current_match = None

    full_text = "\n\n".join(b["text"] for b in blocks)[:14000]
    summary = claude(
        f"Write a tight end-of-show recap of tonight's {label} for a wrestling "
        "news channel, based on this live coverage. 4-8 short lines: every match "
        "result first (winner, how), then the major angles/announcements. Plain "
        "text, no markdown, no intro or outro sentence.\n\n" + full_text,
        max_tokens=800,
    )

    lines = [f"📺 <b>{escape(label)} RECAP — {now_et.strftime('%B %-d, %Y')}</b>", ""]
    if summary:
        for ln in summary.splitlines():
            ln = ln.strip().lstrip("-•* ")
            if ln:
                lines.append(spoiler(f"▪️ {escape(ln)}"))
    else:
        if results:
            lines.append("<b>Results:</b>")
            lines += [spoiler(f"✅ {escape(r)}") for r in results]
        else:
            lines.append("(No parsed match results — see full coverage.)")
    lines.append(f'\n<a href="{escape(state["url"])}">Full coverage → 411mania</a>')
    return "\n".join(lines)


# ---------------------------------------------------------------- main

def run(force_show=None, force_recap=False, dry_run=False):
    now_et = datetime.now(ET)
    shows, events = load_shows(), load_events()

    if force_show:
        cfg_raw = shows.get(force_show)
        if cfg_raw:
            key, cfg = force_show, {
                "label": cfg_raw["label"],
                "keywords": cfg_raw.get("keywords", [force_show]),
                "slug": force_show,
                "window_end": cfg_raw["window"][1],
                "is_ple": False,
            }
        else:
            print(f"unknown show: {force_show}")
            return 1
    else:
        key, cfg = active_show(now_et, shows, events)

    if not key:
        print("No show live right now; exiting.")
        return 0

    os.makedirs(STATE_DIR, exist_ok=True)
    state_path = os.path.join(STATE_DIR, f"{key}-{now_et.date().isoformat()}.json")
    state = load_json(state_path, {
        "url": None, "seen": [], "blocks": [], "empty_runs": 0,
        "started_posted": False, "recap_posted": False,
    })

    if state["recap_posted"] and not force_recap:
        print("Recap already posted; nothing to do.")
        return 0

    if not state["url"]:
        state["url"] = find_coverage_url(key, cfg, now_et)
        if not state["url"]:
            print("No live coverage article found yet.")
            return 0
        print(f"coverage url: {state['url']}")

    try:
        blocks = extract_blocks(fetch(state["url"]))
    except Exception as e:
        print(f"fetch/parse failed: {e}")
        return 0

    seen = set(state["seen"])
    new_blocks = [b for b in blocks if block_hash(b) not in seen]

    if not state["started_posted"] and blocks:
        tg_send(f"🟢 <b>{escape(cfg['label'])}</b> is on the air! "
                f"Live updates incoming.", dry_run=dry_run)
        state["started_posted"] = True

    if new_blocks:
        state["empty_runs"] = 0
        # skip the author's pre-show intro chatter on the very first pass
        meaningful = [b for b in new_blocks
                      if b["kind"] != "segment" or len(state["blocks"]) > 0
                      or blocks.index(b) >= max(0, len(blocks) - 12)]
        post_blocks = meaningful or new_blocks
        tg_send(format_segment_update(cfg["label"], post_blocks, state["url"]),
                dry_run=dry_run)
        for b in new_blocks:
            seen.add(block_hash(b))
            state["blocks"].append(b)
        state["seen"] = sorted(seen)
    else:
        state["empty_runs"] += 1
        print(f"no new content (empty_runs={state['empty_runs']})")

    # ---- end-of-show detection
    closer_hit = any(CLOSER_RE.search(b["text"]) for b in state["blocks"][-3:])
    has_results = any(b["kind"] == "winner" for b in state["blocks"])
    past_end = now_et.strftime("%H:%M") >= cfg["window_end"]
    stalled = state["empty_runs"] >= 3 and has_results

    if force_recap or ((closer_hit or past_end or stalled) and state["blocks"]):
        tg_send(format_recap(cfg["label"], state, now_et),
                disable_preview=True, dry_run=dry_run)
        state["recap_posted"] = True
        print("recap posted")

    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)
    return 0


def selftest(dry_run=False):
    now = datetime.now(ET).strftime("%a %b %-d, %-I:%M %p ET")
    tg_send(f"✅ <b>WWE Recap Bot is connected!</b>\nTest message sent {now}. "
            f"You're all set — updates will post automatically on show nights.",
            dry_run=dry_run)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true",
                    help="send a test message to the channel")
    ap.add_argument("--force-show", help="run as if this show is live (raw/smackdown/nxt)")
    ap.add_argument("--force-recap", action="store_true",
                    help="post the recap now from accumulated state")
    ap.add_argument("--dry-run", action="store_true",
                    help="print messages instead of sending")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(selftest(dry_run=args.dry_run))
    sys.exit(run(force_show=args.force_show, force_recap=args.force_recap,
                 dry_run=args.dry_run))
