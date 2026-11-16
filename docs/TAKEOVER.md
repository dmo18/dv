# Takeover report (2026-11-16)

Static wallboard for a second screen on a standalone PC. Plain HTML files served by
GitHub Pages at https://dmo18.github.io/dv/. No build step, no package manager.

## What each part does
| Part | Files | Job |
|---|---|---|
| Wallboard | wallboard.html | 1920x1080 grid of iframes: ticker, headlines, 11 video tiles |
| Player | player.html | Plays one HLS stream: label, captions, backup stream, No signal card, quality cap |
| Encoder | encoder.html | Saved streams grid, builds player links |
| Headlines | headlines.html, headlines-feeds.json | Scrolling news strip, fed by a scheduled workflow |
| Ticker | ticker.html | TradingView ticker tape widget (third party) |
| Daily jobs | .github/workflows/headlines.yml, stream-check.yml, .github/scripts/ | Fetch headlines every 15 min, check every stream daily |
| Single pages | abc1-3, bloomberg, sky, i24, IL13, rt, wsvn, livenow, globalnews, fnc, stock, nvda, flights, weather, myticker, others | Older standalone pages, mostly unused by the wallboard |

## How to run
Local preview: `python3 -m http.server 8765` in the repo root, open http://localhost:8765/wallboard.html.
Live: merging to main publishes to GitHub Pages in about a minute.

## Baseline (measured)
- Pages: 29 HTML files, all load.
- Streams: daily check last run found every wallboard and encoder stream playing.
- Tests, lint, build: none exist in the repo.
- Secrets: none found in code or workflows.
- Skills, hooks, plugins, MCP servers shipped with the repo: none.

## Known issues
- Standalone pages abc1-3, -birdseye (missing birdseye.js), -IL12, -newsnation, sky use dead streams.
- index.html contains only "test".
- ticker.html depends on TradingView's widget script.
- myticker.html relies on Stooq, which now blocks automated access.

## Risks
1. clappr is loaded as `@latest` from a CDN on 14 pages. An upstream change breaks or alters every tile.
2. Price data: the replacement for the TradingView ticker would use a free unofficial source that can change without notice.
3. No automated layout or player tests in the repo, so regressions are caught by hand.
4. Many simultaneous video streams on one PC: real load on the target PC is unmeasured.

## Third-party code loaded by pages
clappr@latest (jsdelivr, unpinned), Video.js 8.10.0 (zencdn, pinned), TradingView widgets
(ticker.html, stock pages), Leaflet 1.9.4 (flights), Fox and Brightcove embeds, Flashphoner (dead page).
