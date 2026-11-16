# Status

Updated 2026-11-16.

## Done
- Moved from old repo, Pages live, links repointed.
- Streams repaired, daily stream check, ABC 10 backup, labels, No signal card, quality cap.
- Headlines redesigned (no timeouts), feed list in headlines-feeds.json.
- Captions on streams that carry them, 26% smaller, transparent box.

## In progress
- Takeover audit finished. Plan for 1080p layout and new ticker awaiting approval.

## Decisions
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
  starts its successor at second 0, headlines.yml still uses unpinned checkout@v4.

## Known issues
See docs/TAKEOVER.md.

## Open security findings
- clappr@latest unpinned on 14 pages (medium).
