# Takeover report (updated 2026-11-19)

Static wallboard for a second screen on a standalone PC. Plain HTML files served by
GitHub Pages at https://dmo18.github.io/dv/. No build step, no package manager.

## What each part does
| Part | Files | Job |
|---|---|---|
| Wallboard | wallboard.html | 1920x1080 stage: 40 px ticker, 50 px headlines, 4 large (480x270) and 24 small (320x180) framed video tiles, all exact 16:9; no status line on tiles; old 18-tile wall at ?layout=18; check view ?diag=1 |
| Player | player.html | Plays one HLS stream: label, captions, backup stream, No signal card, quality cap, health report |
| Channel list | channels.json | The one list of feeds (79: name, address, description, off-air flag) used by the encoder, the TV page and the daily check |
| Encoder | encoder.html | Shows every feed in channels.json with a preview and description, Play all / Pause all / Stop all, Open with sound and controls, builds player links |
| TV page | tv.html | Red Macho Devil TV: full-screen channel changer with guide, now and next, skip off air (button), channel guide with groups, captions, stats in the top bar; overlay hides after 5 s |
| TV guide data | epg.yml, fetch_epg.py, epg-map.json | Every 30 min: real programme names for about 20 channels (Pluto TV public guide) to branch epg-data |
| Fire TV app | firetv/, rmtv.apk, firetv-lab-test.yml, docs/FIRETV.md | Android app that opens tv.html full screen with remote control keys; lab tested on a virtual device |
| Ticker | ticker.html, ticker-symbols.json | Prices with no third-party widget: smooth scroll, live crypto, stale and closed states |
| Headlines | headlines.html, headlines-feeds.json | Scrolling news strip sized from its bar height |
| Data relay | .github/workflows/relay.yml, .github/scripts/relay_loop.py, fetch_prices.py, fetch_headlines.py | Cloud job that publishes prices (2 min) and headlines (15 min) to branches ticker-data and headlines-data |
| Safety nets | relay.yml schedule (every 5 h) restarts the relay (prices and headlines); headlines.yml (every 6 h) refreshes headlines only | Keep the data going if the relay ever stops |
| Daily check | stream-check.yml, check_streams.py | Plays every stream, checks data freshness, opens one issue if anything is wrong |
| WSVN tile | player.html (since wallboard v22) | Plays WSVN's stream like every other tile and recovers by itself (the old wsvn.html page was removed) |
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
- Wall: 28 tiles (since v33). The 18-tile wall measured 18 of 18 tiles moving over a 310 second headless run at CPU 1.74
  cores (headless, no graphics card); the 28-tile wall has not been measured on the real PC.
- Streams: daily check. About 20 spare feeds (ABC News Live 1 to 10, ten CBS city streams) are only on air during events; they are listed but do not count as problems.
- No test suite in the repo; checks are run by hand with Playwright against the live site, and the Fire TV app by the lab workflow.
- Secrets: none. The WSVN entry in the encoder carries a public playback token that wsvn.com gives every visitor.

## Known issues
- Fox News has no sound: its only free picture feed is video only, and no free stream carries picture and sound together. A paired audio stream was tried and removed (it did not match).
- 16 of the wall's feeds carry no captions; NewsNation has no working free stream.
- BBC News "now and next" comes from Pluto's BBC News channel, which may differ slightly from the North America feed shown.
- index.html contains only "test". Older pages abc1-3, -birdseye (missing birdseye.js), -IL12, -newsnation, sky use dead streams.
- The WSVN tile shows the station's own "right back" card during its ad breaks (the stream is the station's feed with ads filled in).
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
The wall loads no third-party scripts apart from the two player libraries.
