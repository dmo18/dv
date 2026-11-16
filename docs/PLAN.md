# Plan: 18 feeds and a new ticker

Written 2026-11-16. Status: waiting for approval. Nothing has been changed yet.

## 1. What you get

- 18 video feeds instead of 11. The top 3 stay exactly the size they are now.
- A new stock ticker: one thin line, smooth, live crypto, no outside widget.
- A wall that is tested for load before it goes on the PC, with a safety switch.

## 2. Goals and non-goals

Goals
1. More feeds at 1920x1080 with zero shrink of BBC, WSVN and LiveNOW.
2. Ticker about one third of today's height, nicer, smooth, moving with the market.
3. No third-party widget or script in the ticker.
4. The wall keeps running when a data source or the internet fails.

Non-goals (not in this work)
- Sound, touch or any interaction on the wall.
- Replacing the WSVN Brightcove player (it stays as is).
- Hosting Video.js and Clappr inside the repo (possible later, see section 6).
- A paid data feed, a server, or any account sign-up.
- Reworking the old single pages (only one safe line is changed in them).

## 3. Layout

### 3.1 Chosen layout (A): 3 large + 15 small = 18 tiles

Exact geometry at 1920x1080:

| Row | Top edge | Height | Content |
|---|---|---|---|
| Ticker | 0 | 32 | new ticker (was 108) |
| Headlines | 32 | 40 | headlines strip (was 72) |
| Row 1 | 72 | 360 | 3 tiles, 640x360 each (unchanged size) |
| Row 2 | 432 | 216 | 5 tiles, 384x216 each |
| Row 3 | 648 | 216 | 5 tiles, 384x216 each |
| Row 4 | 864 | 216 | 5 tiles, 384x216 each |

32 + 40 + 360 + 216 + 216 + 216 = 1080. Every tile is exactly 16:9, no gaps.

Tile order (can be changed in one list in wallboard.html):

| Row | Tiles |
|---|---|
| 1 | BBC News, WSVN 7News, LiveNOW Fox |
| 2 | CNN, Fox News, ABC News Live, CBS News, Bloomberg |
| 3 | IL13, i24, TRT World (or Kan 11), Al Jazeera, RT |
| 4 | ABC 10, Global News, Scripps News, DW, Fox Weather |

What it costs, honestly:
- The 8 small tiles you have today go from 480x270 to 384x216.
  That is 80 percent of the width, 64 percent of the area.
- Headline text goes from 38px to about 25px (bar 72 to 40).
- Captions on a small tile would be 12px. See caption policy, section 8.

What was measured (mock wall of these 18 feeds, headless Chromium, 1920x1080):
- 18 of 18 tiles played for the whole test, no stalls.
- Dropped frames: 0 to 0.6 percent per tile.
- Processor: 1.85 cores for 18 tiles, against 1.31 cores for today's 11.
  That test had no graphics card help at all, so it is a worst case.
- Download: about 18 Mbps for 18 tiles, about 12 Mbps for today's 11 (lower bound).
- Channel headlines on 384x216 tiles are readable. Small crawl text is not.
- Fox News only exists in 240p, so a small tile suits it better than today.

### 3.2 Alternatives looked at

| Option | Tiles | Small tile | Verdict |
|---|---|---|---|
| A. 5 columns (chosen) | 18 | 384x216 | Exact fit, graphics still readable |
| B. 6 columns | 21 | 320x180 | Captions under 10px, on-air text unreadable, 21 streams, 108px left over |
| C. Row of 4 at 480x270 plus row of 5 | 12 | mixed | Only 1 extra feed, 162px wasted |
| D. One 768x432 feature tile plus 11 small | 15 | 384x216 | Nice, but 3 fewer feeds than A |
| E. Keep today's wall | 11 | 480x270 | Does not meet the request |

### 3.3 Other screen sizes

- The wall is drawn on a 16:9 "stage" that is as large as fits the screen, centred.
- All tile sizes are a fraction of the stage width, so tiles are always 16:9.
- Taller screens (for example 1920x1200): the spare height goes to the two bars,
  ticker up to 48px, headlines up to 72px, the rest stays black.
- Wider screens (ultrawide): black bands left and right. Today's wall overflows there.
- Phone: the whole wall scales down. It is a preview only, not meant to be read.
- ticker.html and headlines.html size their text from their own height,
  so they look right in any bar height and when opened alone on a phone.

## 4. Ticker design

### 4.1 Look

- One line, 32px tall, black background, no logos, no boxes.
- Left, fixed (does not scroll): Dow, S&P 500, Nasdaq 100 with value and percent.
  Then a small clock chip, for example "as of 14:05".
- Right, scrolling: Stocks, then FX and commodities, then Crypto.
  A thin divider and a small grey group name between groups.
- Each item: NAME (grey, 13px bold), price (white, 17px), arrow and percent
  (green up, red down, grey flat, 14px), then a tiny chart of the day (44x14).
- Digits use fixed-width figures and each item has a reserved width,
  so nothing jiggles when a price changes.
- Soft fade at the left and right edge of the scrolling part.

### 4.2 Smooth movement

- The strip is one layer moved by a browser animation that runs on the
  graphics side of the browser (a transform animation, Web Animations API).
- Why: all 18 players share one script thread with the ticker.
  A script-driven scroll would stutter when players are busy. This one cannot.
- Constant speed 50px per second. Two copies of the strip make the loop seamless.
- Prices change in place. The strip is never rebuilt while it is on screen,
  so there is no jump. A changed symbol list is applied at the end of a pass.

### 4.3 Dynamic

- Change flash: when a price changes, that item gets a short soft glow,
  green if up, red if down, about 0.8 seconds, then fades.
- Crypto is truly live: the page listens to Coinbase's public price stream
  (verified in a browser: 6 coins, about one update a second, no key).
- Stocks, indexes, FX, gold, silver and oil update every few minutes (section 5).
- Tiny charts: drawn as simple SVG lines from 5-minute points. Worth it:
  they add the "shape of the day" in 44px and cost almost nothing.
- Percent change: stocks against yesterday's close. Crypto against 24 hours ago.

### 4.4 Stale and closed

- Market closed: item shown slightly dimmer with a small "closed" mark. This is normal.
- Stale: market is open but the price is older than 20 minutes.
  Item dims to about half, tiny chart hidden, clock chip turns amber "as of 13:10".
- Very stale (over 24 hours of trading time): price stays, percent is hidden.
- The ticker never goes blank and never shows a "loading" state after first use.

### 4.5 Symbol file

New file ticker-symbols.json, same idea as headlines-feeds.json.
One entry per item: display name, group, source, source symbol, decimals, pinned yes/no.
Starting list (all verified to return data today):

| Group | Items |
|---|---|
| Pinned | Dow (^DJI), S&P 500 (^GSPC), Nasdaq 100 (^NDX) |
| Stocks | AAPL, AMD, AMZN, GOOGL, INTC, INTZ, META, MSFT, NVDA, TSLA, Emerging Mkts (EEM) |
| FX and commodities | EUR/USD, Gold (GC=F), Silver (SI=F), WTI Oil (CL=F) |
| Crypto (Coinbase) | BTC, ETH, SOL, XRP, ADA, DOGE |

Note: today's ticker shows funds (DIA, QQQ, SPY) under index names.
The new one shows the real index values.

## 5. Price data pipeline

### 5.1 What was verified today

| Fact | Result |
|---|---|
| Browser calling Yahoo directly | Blocked (no cross-site permission). Tested in Chromium. |
| Browser calling Nasdaq API directly | Blocked, same reason |
| Browser calling Coinbase REST and live stream | Works, no key |
| Yahoo from a script, all 21 non-crypto symbols | Works, gives price, previous close, 5-minute points |
| Yahoo batch call (many symbols in one request) | Works (v7 spark endpoint) |
| Stooq, CNBC, Pyth (needs a key now) | Not usable |
| GitHub's scheduler | The headlines job is set to every 15 minutes. It really ran every 4 to 8 hours (13 runs in 3.4 days). |
| Published file cache (raw.githubusercontent.com) | 5 minutes |

The scheduler finding matters most. A plain scheduled job would show
stock prices that are hours old. It also means today's headlines are
up to 8 hours old (measured: 8 hours 51 minutes old at test time).

### 5.2 Chosen design: a data relay job

- New script .github/scripts/fetch_prices.py reads ticker-symbols.json and
  writes one file, ticker.json.
- New workflow "Data relay". One run stays alive for about 5.5 hours.
  Every 2 minutes it fetches prices and publishes ticker.json to a new
  branch "ticker-data" (one commit, replaced each time, like headlines-data).
- Every 15 minutes the same run also refreshes the headlines
  (it calls the existing fetch_headlines.py). This fixes the stale headlines.
- Before it ends, the run starts its own successor.
  The normal schedule stays as a safety net that restarts the chain if it breaks.
- When every market is closed (weekend) it slows to one fetch every 15 minutes.
- The page reads ticker.json in the background, exactly like headlines.html Rev12:
  saved copy first, instant start, never waits on the network.

Expected delay on screen:
- Crypto: live, 1 to 5 seconds.
- Stocks, indexes, EUR/USD: about 3 to 8 minutes.
- Gold, silver, oil: about 12 to 18 minutes (Yahoo delays futures by 10).

ticker.json shape (decided):
`{ "generated": epoch, "items": { "AAPL": { "p": price, "pc": previous close,
"t": epoch of price, "open": true or false, "s": [up to 80 points] } },
"sources": { per symbol: which source answered, or the error } }`

### 5.3 When something fails

| Failure | What the wall shows |
|---|---|
| Yahoo batch call fails | Script tries Yahoo per symbol, then the second Yahoo host |
| Yahoo fully down or rate limited | Stocks and funds from Nasdaq's API. Gold from gold-api.com. EUR/USD from Frankfurter (daily). Anything still missing keeps its last value and time. |
| Every source fails | Old file is left in place. Items go stale and dim. |
| Relay job dies | Safety-net schedule restarts it (could take hours). Items dim meanwhile. A freshness alarm opens a GitHub issue (task 10). |
| Coinbase stream drops | Page reconnects with growing waits (2s up to 60s). Meanwhile crypto comes from ticker.json, which also carries crypto. |
| No internet at all | Ticker keeps scrolling the saved prices, dimmed, with the "as of" time. |
| First ever start with no internet | One quiet line: "Prices will appear when online". |
| Bad or tampered file | Rejected by validation (section 10). Screen keeps the last good data. |

## 6. Third-party code

- ticker.html: no outside scripts, fonts, images or widgets. Plain HTML, CSS, JS.
  It only talks to raw.githubusercontent.com (our own data) and Coinbase (prices).
- The TradingView script is removed from the wall.
- Clappr: 14 pages load "clappr@latest". Verified today that "latest" currently
  resolves to version 0.4.7. Pin to exactly 0.4.7. Nothing changes on screen,
  it only stops surprise updates.
- Does the pin belong in this work? Yes for player.html (every tile depends on it
  and we are editing it anyway). Add an integrity hash for Clappr and Video.js there too.
  The 13 old single pages get the same one-line pin, batched in task 8.
- Later, optional: copy both player libraries into the repo so the wall loads
  no outside scripts at all. Not in this plan.

## 7. Feeds

### 7.1 Verified today, recommended to add (7)

Proof = played in the real player page in headless Chromium for 2 minutes at
384x216, picture moving, then again inside the 18-tile mock wall.

| Feed | Plays | Smallest quality | Captions | Note |
|---|---|---|---|---|
| CNN (FAST channel) | Yes | 640x360, 1.0 Mbps | None seen | Already the ABC 10 backup |
| Bloomberg | Yes | 480x272, 0.4 Mbps | Yes | Use the bloomberg.com master address, not the fixed 1080p one in encoder.html |
| CBS News 24/7 | Yes | 320x180, 0.3 Mbps | Yes | New find |
| Fox Weather | Yes | 512x288, 0.6 Mbps | Yes | Address 247wlive.foxweather.com |
| Scripps News | Yes | 640x360, 1.0 Mbps | Yes | New find |
| DW English | Yes | 480x270, 1.1 Mbps | None seen | New find |
| TRT World | Yes | 640x360, 0.4 Mbps | None seen | New find. Slot can go to Kan 11 instead |

### 7.2 Verified but not recommended

| Feed | Why not |
|---|---|
| ABC1, ABC2 | Play right now, but they are event feeds that go dark, and their smallest quality is 720x480 at 1.9 Mbps. ABC 10 already fills this role. |
| Newsmax | Plays, has captions. Good spare if you want it instead of TRT World or DW. |
| France 24 English | Plays only at fixed 1080p, 5 Mbps. Too heavy for a small tile. |
| Sky News Australia | Address answers and "plays", but shows a "stream has ended" card. Rejected. |

### 7.3 Could not be used or verified

| Feed | Finding |
|---|---|
| Kan 11 | Could not be reached from the test machine (network path, not proof it is dead). The saved address is a 4K-only stream, far too heavy for a small tile. Task 5 looks for a lighter address and tests it from GitHub. |
| Western Wall cam | EarthCam refuses requests that do not come from earthcam.com (403 from our site, 200 with their own referrer). Cannot play on the wall. Drop. |
| Fox Business | No working public address found (26 likely addresses tried, all 404). |
| CNN International | No working address found. The CNN FAST feed above is the international-style channel. |
| Sky News | Dead, already dropped. |

Every added feed is also added to encoder.html, so the daily stream check covers it.
Known gap: the daily check cannot tell a real programme from an "ended" card.

## 8. Caption policy

Captions today: LiveNOW and ABC News Live. New feeds with captions: Bloomberg,
CBS News, Fox Weather, Scripps News.

- Large tiles (640 wide): captions on, as now (about 20px text).
- Small tiles (384 wide): captions off by default. At 12px they are hard to read
  from a chair and they cover the channel's own headline bar
  (seen in the mock wall on LiveNOW and CBS).
- Any small tile can be switched on in the tile list (`cc: true`).
  When on in a small tile: at most 2 lines, never under 12px.
- If you want captions on a channel, the better answer is to move it to row 1.

## 9. Performance safeguards for 18 streams

Already in place: the player picks the smallest quality that fills its tile.
Measured picks in the mock wall: mostly 240p to 396p for small tiles.

New safeguards (all decided):
1. Staggered start: row 1 at once, then one small tile every 0.7 seconds.
   The whole wall is up in about 12 seconds instead of 18 players starting together.
2. Staggered refresh: today every tile reloads at the same minute each hour.
   New: tile n reloads at 60 + 2n minutes, so never two at once.
3. Heavy feeds named: ABC 10 (fixed 720p), IL13 (smallest is 1024x576) and
   Global News (848x480) are the costly small tiles. They stay, but they are first
   in line for the cap below.
4. Decode cap: one number in wallboard.html, MAX_LIVE, default 18 (off).
   If set lower, for example 14, the lowest-priority small tiles take turns:
   a resting tile shows its channel name card, and the set rotates every 2 minutes.
5. Check view: wallboard.html?diag=1 shows on each tile its quality,
   dropped-frame percent and seconds since last stall, plus a wall total.
   Player Rev8 reports these numbers to the wall.
6. Easy way back: the tile list and MAX_LIVE are plain values at the top of
   wallboard.html. Publishing the previous version restores the 11-tile wall,
   and running screens reload by themselves.

How we verify:
- In the build: 30-minute headless run of the full wall. Pass = 18 of 18 moving,
  no tile on the No signal card for more than 60 seconds, dropped frames under
  2 percent per tile, memory not growing more than 15 percent after minute 5.
- On your PC (one photo from you): open the wall with ?diag=1, wait 10 minutes,
  photograph the screen, and tell us what Task Manager shows for CPU.
  Pass = every tile under 5 percent dropped frames and CPU under 70 percent.
  If it fails, we set MAX_LIVE to 14 and repeat.

## 10. Security items

- No secrets or keys anywhere. Every data source is keyless.
- Workflow permissions, least privilege:
  relay job needs `contents: write` (publish the data branch) and
  `actions: write` (start its successor). Nothing else. Other workflows unchanged.
- Actions pinned: `actions/checkout` pinned to a full commit SHA in the new
  workflow (and in the two existing workflows when task 10 touches them).
- The relay can only force-push the two data branches, never main.
- One relay at a time (concurrency group), hard time limit on every run.
- Script side validation: only symbols from ticker-symbols.json, every number
  must be finite and above zero, a jump over 40 percent from the previous value
  is held back for one cycle, output size capped.
- Page side validation: ticker.json must be an object of the expected shape,
  unknown symbols ignored, numbers checked, at most 60 items, names cut to 24
  characters, all text written with textContent (never as HTML).
  Coinbase messages get the same number checks.
- No new runtime third-party scripts. Clappr pinned with an integrity hash.
- Fetch script uses only Python's standard library (no packages to trust).
- Polite use: 1 or 2 Yahoo requests per 2-minute cycle (batch call).

## 11. Tasks

Model rule: stronger model where a wrong result could slip past the checks or is
costly to undo. Reviewer is one tier above the writer
(Haiku, then Sonnet, then Opus, then Fable).

### Milestone 1: the new ticker, live on today's wall

**Task 1. Symbol file and price fetch script**
- Files: ticker-symbols.json (new), .github/scripts/fetch_prices.py (new)
- Depends on: nothing
- Decided: Yahoo batch call first, then per symbol, then second Yahoo host,
  then Nasdaq API, gold-api.com, Frankfurter. Crypto from Coinbase REST.
  Output shape as in 5.2. Standard library only. Keeps previous values on failure.
- Accept: all 27 symbols present with sane values. With Yahoo blocked (simulated)
  stocks still arrive from Nasdaq. With everything blocked the old file is kept
  and the script exits non-zero.
- Check: run locally 3 times (normal, Yahoo blocked, all blocked) and compare
  5 prices by hand against Yahoo's website.
- Model: Sonnet. Reviewer: Opus.

**Task 2. Data relay workflow**
- Files: .github/workflows/relay.yml (new), .github/workflows/headlines.yml
  (schedule reduced to a safety net), .github/scripts/relay_loop.sh or .py (new)
- Depends on: 1
- Decided: one run about 5.5 hours, prices every 2 minutes, headlines every 15,
  slow mode when all markets are closed, starts its successor, schedule as
  safety net, concurrency group with no cancel, time limit 350 minutes,
  permissions as in section 10, checkout pinned to a SHA,
  publishes branch "ticker-data". Cycle and run length adjustable for testing.
- Accept: never two relay runs at once. A killed run is replaced without help.
  ticker.json on the data branch is never older than 5 minutes during a run.
  Headlines file is never older than 20 minutes during a run.
  Yahoo works from GitHub's machines (unverified until this task runs,
  if it does not, the fallbacks from task 1 carry the stocks and we report it).
- Check: loop tested locally with a 10-second cycle and a 1-minute run length
  first (repo rule). Then one real run watched for 3 handovers in short mode,
  then one full-length run. Read the run list to prove single running.
- Model: Opus. Reviewer: Fable.
  Why: a mistake here can loop forever or stop silently, and tests cannot fully prove it.

**Task 3. Ticker page: look, scroll, saved data, stale handling**
- Files: ticker.html (rewritten, title "Ticker Rev2", changelog at top)
- Depends on: 1 (file shape)
- Decided: design in section 4. Sizes derived from the page height.
  Graphics-side scroll animation at 50px per second, two copies, no rebuild while
  visible. Pinned indexes on the left. Reads ticker.json like headlines Rev12
  (saved copy first, background refresh every 60 seconds, retry after 30).
  Validation as in section 10. Self-reload when its Rev changes.
- Accept: no outside script or font requested. Starts in under 1 second from the
  saved copy with the network off. Stale and closed states look as described.
  Looks right at heights 32, 48 and 108 and on a 390px wide phone.
- Check: Playwright with a fake ticker.json: list every network request
  (only our data and Coinbase allowed), offline start, stale file, broken file,
  screenshots at the 4 sizes. Frame timing over 60 seconds: no frame gap over 34ms.
- Model: Sonnet. Reviewer: Opus.

**Task 4. Live layer, then publish milestone 1**
- Files: ticker.html (Rev3), wallboard.html (v19, version bump only so screens reload)
- Depends on: 2, 3
- Decided: Coinbase stream "ticker_batch" for 6 coins, reconnect waits 2s to 60s,
  24-hour open and 5-minute points from Coinbase REST at start and hourly.
  Flash on change, tiny charts, "as of" chip. Crypto falls back to ticker.json.
- Accept: a price change flashes once and never moves the neighbours.
  Stream cut for 5 minutes: crypto continues from the file, then reconnects.
  30 minutes running: no memory growth over 10 percent, scroll still smooth.
- Check: Playwright with the stream blocked, then restored. 30-minute soak.
  Screenshots for the phone (section 12). Then merge to main.
- Model: Sonnet. Reviewer: Opus.

Visible result of milestone 1: the new ticker runs in today's 108px slot.

### Milestone 2: 18 feeds on the wall

**Task 5. Confirm the new feeds and list them**
- Files: encoder.html (Rev17: add 7 feeds, fix Bloomberg to the master address)
- Depends on: nothing
- Decided: the 7 feeds in 7.1. Look for a lighter Kan 11 address
  (not the 4K one). If one plays from GitHub's machines and in the player,
  it replaces TRT World.
- Accept: each feed plays 2 minutes in player.html at 384x216 with a moving
  picture and a real programme on the screenshot (not an "ended" card).
- Check: Playwright run with one screenshot per feed, plus the daily stream
  check script run by hand: DEAD=0.
- Model: Sonnet. Reviewer: Opus.

**Task 6. Player Rev8**
- Files: player.html
- Depends on: nothing
- Decided: Clappr pinned to 0.4.7 with integrity hashes for Clappr and Video.js.
  Captions limited to 2 lines and at least 12px. Player reports quality,
  dropped frames and stalls to the wall once every 5 seconds (same-site message,
  numbers only). No change to the fallback logic.
- Accept: all 11 current tiles behave exactly as today. Backup stream,
  No signal card and return-to-main still work. Integrity hashes match.
- Check: Playwright on 4 cases: good stream, dead stream with backup,
  dead stream without backup, stream with captions. Before and after screenshots.
- Model: Opus. Reviewer: Fable.
  Why: every tile depends on this file and its faults show only after hours.

**Task 7. Wallboard v20: new grid and 18 tiles**
- Files: wallboard.html, .github/scripts/check_streams.py (only if the tile
  list format changes, so the daily check still finds every stream)
- Depends on: 4, 5, 6
- Decided: geometry in 3.1 and 3.3, tile order in 3.1, staggered start and
  refresh, MAX_LIVE cap default 18, ?diag=1 view, captions off on small tiles
  with a per-tile switch.
- Accept: at 1920x1080 every tile edge is on the exact pixels in 3.1.
  18 of 18 moving after 60 seconds. No overflow at 1920x1200, 2560x1080,
  1366x768 or 390x844. With MAX_LIVE set to 14, exactly 14 play and the resting
  tiles rotate. Daily stream check lists all 18 streams.
- Check: Playwright measures every tile box, counts moving tiles,
  screenshots at the 5 sizes, runs the cap test, runs check_streams.py.
- Model: Sonnet. Reviewer: Opus.

**Task 8. Headlines fit, old-page pin, publish milestone 2**
- Files: headlines.html (Rev13), the 13 old pages that load clappr@latest
  (one line each plus a version line), wallboard.html (final v-number check)
- Depends on: 7
- Decided: headline text sized from the bar height (about 25px in a 40px bar),
  source and time on one line beside the headline. Old pages: pin 0.4.7 only.
- Accept: headlines readable and vertically centred at 40px and at 72px.
  No page still contains "clappr@latest".
- Check: screenshots at both heights, one search of the repo for "@latest",
  each old page still loads. Phone screenshots. Then merge to main.
- Model: Haiku. Reviewer: Sonnet.

Visible result of milestone 2: the 18-tile wall with the thin ticker is live.

### Milestone 3: proven on your PC

**Task 9. Soak test and real PC check**
- Files: wallboard.html (only MAX_LIVE or tile order, if the check says so)
- Depends on: 8
- Decided: pass marks in section 9. If the PC fails, MAX_LIVE = 14, then 11.
- Accept: 30-minute headless soak passes. Your photo of ?diag=1 passes,
  or the cap is set so that it does.
- Check: soak numbers written to docs/STATUS.md. Your photo.
- Model: Sonnet. Reviewer: Opus.

**Task 10. Freshness alarm and docs**
- Files: .github/workflows/stream-check.yml, .github/scripts/check_streams.py,
  docs/TAKEOVER.md, docs/STATUS.md, CLAUDE.md, README.md
- Depends on: 2, 9
- Decided: the daily check also fails if ticker.json or headlines.json is older
  than 60 minutes, and says so in the same issue. Checkout pinned to a SHA in
  both older workflows. Docs: new branch "ticker-data" added to "do not touch",
  relay explained in plain words, risks updated.
- Accept: a deliberately old data file makes the check report it.
  Docs match what was built.
- Check: script run locally against a fake old file. Read-through of the docs.
- Model: Sonnet. Reviewer: Opus.

Visible result of milestone 3: a short report with your PC's numbers and a wall
that tells us by email when its data stops.

### Totals

10 tasks. Writers: 1 Haiku, 7 Sonnet, 2 Opus. Reviews: 1 Sonnet, 7 Opus, 2 Fable.

### Added requirement (2026-11-16, from the user)

All video sources used on the wall must also be saved in encoder.html (exact address, main and
backup), because the encoder will be used separately later.
- Task 5: encoder gets the exact wall addresses (LiveNOW, ABC 10, Global News, Al Jazeera were
  missing) and WSVN is investigated for a stable stream address (it runs in a Brightcove page).
- Task 7: acceptance adds "every stream in the wall's tile list is in encoder.html by exact address".
- Task 10: the daily check also fails if a wall stream is missing from encoder.html.

## 12. Phone preview

After each milestone you get, in the chat (not stored in the repo):
1. The full wall at 1920x1080 (one picture).
2. Two close-ups at full size: the top-left corner (ticker, headlines, BBC)
   and one bottom-row tile, so text size can be judged on a phone.
3. ticker.html alone at phone size (390 wide), plus 3 frames one second apart
   to show a price flash.
4. The live links, which open on a phone:
   https://dmo18.github.io/dv/ticker.html and .../wallboard.html

## 13. Run cap

- Task limit: 10 planned plus a quarter = 13 task runs. A redo counts as a run.
- Delegated token limit: 4 million tokens in total, 500 thousand per task.
- If either limit is reached, work stops and you get a status note, not more spending.

## 14. Risks

| # | Risk | Chance | What we do |
|---|---|---|---|
| 1 | GitHub slows or objects to a job that runs most of the day | Medium | It is a public repo (free minutes) and a tiny job. If it is stopped: prices fall back to the slow schedule and show as stale. Alternative then: a free relay outside GitHub, your decision. |
| 2 | Yahoo blocks GitHub's machines or changes its unofficial address | Medium | Three fallbacks in the script, stale dimming on screen, alarm issue. Not verified from GitHub yet (task 2). |
| 3 | The PC cannot play 18 streams smoothly | Low to medium | Measured light in test, unmeasured on the PC. Check view plus MAX_LIVE cap. |
| 4 | Internet line too slow (needs about 18 to 25 Mbps) | Low | Players step down in quality by themselves. Cap reduces it further. |
| 5 | A new feed dies or shows an "ended" card | Medium | Daily check catches dead ones. Cards need a human look. Spare feeds listed in 7.2. |
| 6 | Small tiles feel too small in real life | Medium | Milestone 2 is one publish. The old wall is one publish away. |
| 7 | Headlines at 25px too small for you | Medium | One number. Could take 8px from the ticker or go back to a taller bar with 2 rows of 5. |
| 8 | Player change breaks all tiles at once | Low | Strongest writer and reviewer, 4 playback cases tested, quick revert. |
| 9 | Published file cache adds up to 5 minutes | Certain | Included in the stated delay. |
| 10 | Coinbase stream blocked on the PC's network | Low | Crypto falls back to the relay file. |

## 15. Open questions for you

1. Layout: 18 tiles (A)? Recommended yes.
2. Seventh new feed: TRT World now, Kan 11 if a light address is found? Recommended yes.
3. Stock delay of 3 to 8 minutes through a relay job that runs most of the day
   on GitHub? Recommended yes. The clean scheduled way is hours late.
4. Symbol list as in 4.5, with real index values instead of funds? Recommended yes.
5. Pin Clappr to 0.4.7 now? Recommended yes.
