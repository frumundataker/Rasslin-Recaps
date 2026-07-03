# WWE Recap Bot 🤼

Posts live segment-by-segment updates to a **Telegram channel** during
**Monday Night Raw, SmackDown, NXT, and premium live events**, then posts a
full recap the moment the show goes off the air.

Runs entirely for free on **GitHub Actions** — no server, nothing on your
computer. Anyone you invite to the Telegram channel gets push notifications
for every update.

Coverage source: [411mania's live coverage](https://411mania.com/wrestling/),
which is updated throughout each show. Updates always link back to the full
article.

---

## Setup (~15 minutes, one time)

### 1. Create the Telegram bot

1. In Telegram, message **@BotFather** → send `/newbot`.
2. Give it a name (e.g. `WWE Recaps`) and a username (e.g. `wes_wwe_recap_bot`).
3. Copy the **bot token** it gives you (looks like `123456789:AAF...`).

### 2. Create the channel

1. Telegram → New Channel. Name it whatever you like (e.g. *Wrestling Recaps*).
2. **Public channel is easiest**: give it a handle like `@wes_wrestling_recaps`.
   Your chat id is then simply `@wes_wrestling_recaps`.
   - *Private channel instead?* Add the bot as admin, post any message in the
     channel, then open
     `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and
     copy the `"chat":{"id":-100...}` number. That's your chat id.
3. Channel settings → **Administrators → Add Admin** → add your bot. It only
   needs the "Post messages" permission.

Friends join via the channel link and automatically get push notifications
for every post (that's Telegram's default for channels).

### 3. Put this on GitHub

1. Create a **new GitHub repo** (public recommended — Actions minutes are
   unlimited on public repos; private works too but has a monthly quota).
2. Upload everything in this folder (keep the `.github` folder!), or:
   ```bash
   cd wwe-recap-bot
   git init && git add -A && git commit -m "initial"
   git branch -M main
   git remote add origin https://github.com/YOURNAME/wwe-recap-bot.git
   git push -u origin main
   ```
3. Repo → **Settings → Secrets and variables → Actions → New repository secret**:
   - `TELEGRAM_BOT_TOKEN` — the token from BotFather
   - `TELEGRAM_CHAT_ID` — `@yourchannelhandle` or the `-100...` number
   - `ANTHROPIC_API_KEY` — *(optional but recommended)* a Claude API key from
     [console.anthropic.com](https://console.anthropic.com). With it, updates and
     recaps are written as clean summaries. Without it, the bot falls back to
     structured results + short excerpts. Cost is pennies per show (uses Haiku).
4. Repo → **Actions** tab → enable workflows if prompted.

### 4. Test it

Actions tab → **WWE live watch** → **Run workflow** → mode: `selftest`.
Within a minute your channel should get: *"✅ WWE Recap Bot is connected!"*

You can also dry-run locally with no Telegram needed:
```bash
pip install -r requirements.txt
python bot.py --force-show raw --dry-run
```

---

## What it does on show nights

| Show | When (ET) | Behavior |
|---|---|---|
| Raw | Mon ~6–11 PM | Poll every ~10 min, push new segments, recap at off-air |
| NXT | Tue 8–10 PM | Same |
| SmackDown | Fri 8–10 PM | Same |
| PLEs | Dates in `events.json` | Same |

- **Segment updates** — every ~10 minutes, anything new (match results, angles,
  announcements) is posted as short bullets.
- **End-of-show recap** — posted when the coverage wraps up (sign-off detected,
  coverage stops updating, or the show window ends): all match results plus the
  big angles, with a link to full coverage.

## Tweaks

- **Spoiler mode**: repo → Settings → Secrets and variables → Actions →
  *Variables* tab → add `SPOILER_MODE` = `1`. Results get wrapped in
  Telegram spoiler blur — tap to reveal. Great if some subscribers watch
  on delay.
- **Add PLE dates**: edit `events.json` (WWE announces dates a few months
  out — check the [WWE events page](https://www.wwe.com/shows)). SummerSlam,
  SNME July 18, and Money in the Bank Oct 10 are pre-loaded for 2026.
- **Special start times**: Raw occasionally starts early (6 PM ET, or
  afternoon for international shows). The default window already covers
  5:45 PM onward; for afternoon shows, widen `window` in `shows.json` for
  that week or trigger a manual run (Actions → Run workflow → `force-raw`).
- **Drop a show**: delete its entry from `shows.json`.
- **Update cadence**: GitHub cron sometimes runs a few minutes late — updates
  typically land within 10–15 minutes of what happens on screen.

## How it works

Every 10 minutes during show windows, a GitHub Action fetches 411mania's live
coverage article, diffs it against what it's already posted (state is committed
to `state/` in this repo), pushes anything new to Telegram, and detects when
the show ends to fire the recap. Times are computed in Eastern Time, so DST is
handled automatically.
