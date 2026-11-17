# Status

Updated 2026-11-17.

## Done
- Moved from old repo, Pages live, links repointed.
- Streams repaired, daily stream check, ABC 10 backup, labels, No signal card, quality cap, captions (26% smaller).
- Headlines redesigned (no timeouts, now refreshed every 15 minutes by the Data relay), sized for a 40 px bar.
- Milestone 1: new ticker (no third-party widget), price script, Data relay, player Rev8, encoder Rev17 (25 streams).
- Milestone 2: 18-tile wall v20 on an exact 1920x1080 grid, older pages pinned to Clappr 0.4.7.
- Milestone 3 (in this change): freshness alarm in the daily check, docs rewritten.

## In progress
- Photo check on the wall PC (needs the user, steps in README): ?diag=1 for 10 minutes, photo of the screen, CPU and memory from Task Manager.

## Soak test of the live wall (2026-11-17, headless Chromium on a build machine with no graphics card, so a worst case)
Plan pass marks (section 9): 18 of 18 moving, no tile on No signal over 60 s, dropped frames under 2 percent, memory growth under 15 percent after minute 5.
| Measure | Result | Mark |
|---|---|---|
| Player tiles playing | 17 of 17 almost always; one 30 second dip to 16 (TRT World) | pass (the 18th tile is the WSVN page) |
| Longest No signal | 30 seconds | pass |
| Worst dropped frames, any tile | 1.3 percent | pass |
| CPU | about 1.8 cores (old 11-tile wall: 1.2) | no mark |
| Memory | 1.8 GB at start, 2.3 GB after 30 minutes | grew 13 percent (diagnostic run, 28 min) and 22.6 percent (first run, 30 min) after minute 5: borderline, see below |
- Memory growth is steady, about 15 MB per minute, all in the page-rendering processes (graphics, utility and browser processes are flat). It is bounded by the wall's tile reloads: each tile restarts every 60 to 94 minutes and frees its memory. Needs a reading from the real PC. If it is too high there, shorten the reload (the 60 + 2n minutes in the TILES list).
- Errors: none from our own code in the latest 15 minute capture. The only errors are the RT stream host intermittently serving segments without the cross-origin permission header (a few times per 15 minutes); the RT tile recovers. In two earlier runs a burst of 9 to 12 uncaught errors appeared around minute 11, at the same moment as an RT failure (message text not captured). If RT keeps being noisy, replace it with another channel.

## Decisions
- 2026-11-17: the three indexes (Dow, S&P 500, Nasdaq 100) no longer sit in a fixed block at the left of the ticker: they scroll with
  everything else, so line 1 is one scroller (the owner saw the fixed block plus the scroller as two tickers). Only the "as of"
  clock chip stays fixed. Set "pinned": true in ticker-symbols.json to bring a fixed block back.
- Rule from the user (2026-11-17): never host locally, GitHub only. Pages are tested on the live github.io site, workflows by
  running them on GitHub. Until then, pre-merge browser tests of this project ran on a throwaway local test server inside the
  build sandbox (never published or served to the user); that practice has stopped.
- Stock data: Stooq now blocks bots. Candidate source is Yahoo chart endpoint (no key). Crypto live from Coinbase.

- Ticker Rev2 (task 3), accepted points: at the temporary 108px slot the ticker looks close to the 32px
  version (final 32px bar arrives in task 7); on phone width the indexes move into the scroll and only the
  clock chip stays on the left; percent hides only when an open market price is over 24 hours old.
- Encoder rule: every wallboard stream (main and backup, exact address) must also be in encoder.html.

- Player Rev8 (task 6) passed independent review (integrity hashes match, 5 state timelines, 13.5 min runs, no
  growth in timers). Library failure never leaves a silent black tile. Low findings kept: each restart of a dead
  stream leaks about 0.6 MB (old, bounded by reload=60, so never set reload=0); a very long label with no spaces runs
  off the tile; first 10 s after a recovery can show a high dropped-frames number (the wall's check view ignores it).

- Ticker Rev3 (task 3 redo) passed independent Fable review (fit for the 32px wall; 12 min soak flat, 29 hostile
  ?data= variants ran no script). To fix in task 4 (same file): wrong PC clock behind rejects every file as "future"
  (clamp to now instead), prices near zero need a lower sanity bound, phone width item too wide, closed mark small,
  a few leftovers (unused argument, duplicate class add, comment says "more than" but code uses ">="),
  non-JSON saved copy not removed. Not needed: text size at 48 and 108 px (the final bar is 32px).

- Price script Rev4 (task 1) passed the final independent Fable check (7 findings fixed, no regressions, live run
  24 of 24). Low items left for a later revision: redirects can bypass the Yahoo request cap and hop limit, a socket
  can stay open after a slow connect (harmless, the relay uses a child process), a symbols file with no usable
  items exits 1 not 2, and on a failed Yahoo batch EUR/USD, gold and silver should be fetched ahead of stocks.

- Incident 2026-11-16 20:48 to 20:55 UTC: a reviewer's relay test pushed fake data to the real repo (one fake
  headline on headlines-data, fake prices on a new ticker-data branch). Fixed within minutes: headlines refreshed by
  hand (80 real headlines), ticker-data overwritten with an empty placeholder (the sandbox blocks branch deletion).
  Rule added to CLAUDE.md: tests stay local.
- Task 2 relay Rev2 passed the focused Fable re-check. Open low items for Rev3: two stop signals under 1 ms apart can
  deadlock the handler (fix known, the runner sends them seconds apart), a run shorter than the 10 minute lead
  starts its successor at second 0. (headlines.yml checkout is now pinned, fixed in milestone 3.)

- Milestone 1 merged 2026-11-16 (new ticker, relay, price script, player Rev8, encoder Rev17, wallboard v19).
  Restore point before the merge: main at baef2bd6b1ffb6564fafe30cfa615a5b6d2be81c (wallboard v18).
- Ticker Rev4 review (Opus) passed: no regression vs Rev3. Fixed before merge: live crypto now starts even when
  ticker.json is missing or broken. Open for a later revision: a flood of thousands of messages per second drops frames
  (real stream is about 1 per second), a coin missing from ticker.json gets no chart until the hourly refresh,
  phone text is small, a price near 1e-300 shows 0.00.

- Milestone 2 merged 2026-11-16 (18-tile wall v20, headlines Rev13, older pages pinned to Clappr 0.4.7, check_streams
  reads the new tile format and fails if a wall stream is missing from encoder.html). Restore point before the merge:
  main at 12a9cfc7cececca5b6d7aff534d673697a84d1c3 (wallboard v19, 11 tiles).
- Wall v20 review (Opus) passed: 18 of 18 moving at 60 s and 310 s, CPU 1.74 cores vs 1.21 for the old 11-tile wall
  (headless, no graphics card), memory 1.8 GB. Known limits: at Windows display scaling of 125 or 150 percent a
  one device pixel hairline can show between some tiles (black on black; a snapping fix was tried and made it
  no better and shrank the picture, so it was reverted; 100 percent scaling is exact). Some tile reloads can land
  within 10 s of each other a few times in 6 hours (plan said never; still far better than all at once). The cap
  MAX_LIVE (off) rests tiles round-robin, not heavy feeds first. check_streams counts a wall address missing from the
  encoder as "not playing" in its summary and, in the workflow, the exit code is lost through tee (the issue still
  opens). WSVN tile still shows its own ads with a Skip Ad button.

## Known issues
See docs/TAKEOVER.md.

## Open security findings
- None open. (Clappr is now pinned to 0.4.7 with integrity hashes everywhere; checkout is pinned in all three workflows.)
