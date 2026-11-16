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

## Known issues
See docs/TAKEOVER.md.

## Open security findings
- clappr@latest unpinned on 14 pages (medium).
