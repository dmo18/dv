# Takeover report (updated 2026-11-16, after milestones 1 to 3)

Static wallboard for a second screen on a standalone PC. Plain HTML files served by
GitHub Pages at https://dmo18.github.io/dv/. No build step, no package manager.

## What each part does
| Part | Files | Job |
|---|---|---|
| Wallboard | wallboard.html | 1920x1080 stage: 32 px ticker, 40 px headlines, 3 large and 15 small video tiles, check view ?diag=1 |
| Player | player.html | Plays one HLS stream: label, captions, backup stream, No signal card, quality cap, health report |
| Encoder | encoder.html | Every stream saved by exact address (25), builds player links, works on its own |
| Ticker | ticker.html, ticker-symbols.json | Prices with no third-party widget: smooth scroll, live crypto, stale and closed states |
| Headlines | headlines.html, headlines-feeds.json | Scrolling news strip sized from its bar height |
| Data relay | .github/workflows/relay.yml, .github/scripts/relay_loop.py, fetch_prices.py, fetch_headlines.py | Cloud job that publishes prices (2 min) and headlines (15 min) to branches ticker-data and headlines-data |
| Safety nets | relay.yml schedule (every 5 h) restarts the relay (prices and headlines); headlines.yml (every 6 h) refreshes headlines only | Keep the data going if the relay ever stops |
| Daily check | stream-check.yml, check_streams.py | Plays every stream, checks data freshness, opens one issue if anything is wrong |
| WSVN tile | wsvn.html | The one tile that is its own page (Brightcove player with the station's ads), not player.html |
| Older pages | abc1-3, bloomberg, sky, i24, IL13, rt, livenow, globalnews, fnc, stock, nvda, flights, weather, myticker, others | Standalone pages, not used by the wall; 13 now load the pinned player library |

## How the data flows (plain words)
The Data relay runs on GitHub for about 5.5 hours, then starts its own replacement (a second run waits in line,
never two at once). Every 2 minutes it fetches prices (Yahoo, with Nasdaq, gold-api, Frankfurter and Coinbase as
back-ups) and publishes one file; every 15 minutes it also refreshes headlines. The pages read those files in the
background and always keep the last good copy, so a slow or failed fetch changes nothing on screen.

## How to run
Everything is hosted on GitHub only (no local hosting). Merging to main publishes to GitHub Pages in about a minute;
open https://dmo18.github.io/dv/wallboard.html. Restart the relay by hand: Actions, "Data relay", Run workflow.

## Baseline (measured)
- Pages: 29 HTML files, all load. Wall: 18 of 18 tiles moving over a 310 second headless run; CPU 1.74 cores
  (headless, no graphics card) vs 1.21 for the old 11-tile wall.
- Streams: daily check. Two encoder-only spares, IRINN and Kan 11, fail from US servers; they are listed in the report but do not count as problems because the wall does not use them.
- No test suite in the repo; checks are run by hand with Playwright against a local server.
- Secrets: none. The WSVN entry in the encoder carries a public playback token that wsvn.com gives every visitor.

## Known issues
- index.html contains only "test". Older pages abc1-3, -birdseye (missing birdseye.js), -IL12, -newsnation, sky use dead streams.
- The WSVN tile shows the station's own ads with a "Skip Ad" button.
- Windows display scaling of 125 or 150 percent can show a one device pixel hairline between tiles; 100 percent is exact.
- IRINN is dead from outside Iran; kept in the encoder only.

## Risks
1. Prices come from Yahoo's unofficial chart endpoint. If it changes or blocks GitHub, the fallbacks cover most items and the ticker dims stale ones; the daily check flags old data.
2. GitHub pauses scheduled workflows in a public repo after 60 days with no repository activity. A commit to main, or enabling the workflow again, fixes it.
3. The relay can only be proven on GitHub; the 5.5 hour handover was verified in a 3 minute test chain, the first real handover is checked by hand.
4. Free news streams change addresses or go off air without notice; the daily check lists what is dead.
5. Real load on the wall PC is unmeasured until the ?diag=1 photo check.

## Third-party code loaded by pages
Video.js 8.10.0 and Clappr 0.4.7 (both pinned with integrity hashes in the player and the 13 older pages),
Leaflet 1.9.4 (flights page), TradingView widgets (stock pages only), Fox and Brightcove embeds, Flashphoner (dead page).
The wall loads no third-party scripts apart from the two player libraries and, for the WSVN tile only, the Brightcove player (players.brightcove.net) inside wsvn.html.
