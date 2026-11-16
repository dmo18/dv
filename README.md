# dv wallboard

A news and markets wall for a second screen (1920x1080), served by GitHub Pages.

Live: https://dmo18.github.io/dv/wallboard.html

## What is on it
- 18 live news feeds: BBC News, WSVN 7News and LiveNOW Fox large; 15 more small.
- A thin stock ticker (indexes, stocks, currencies, gold, oil, crypto) and a headlines strip.
- Nothing needs a login or an account.

## How the live data works (plain words)
- Video comes straight from each broadcaster's free stream into the page.
- Prices and headlines are collected by a GitHub job called "Data relay". It runs in the cloud, about 5.5 hours
  at a time, hands over to itself, and refreshes prices every 2 minutes and headlines every 15 minutes.
  Crypto prices come live from Coinbase straight into the page.
- A daily check ("Check streams") plays every stream and checks the data is fresh. If something is wrong it opens
  one issue on GitHub, which emails the owner. When all is well again it closes the issue.

## Check the wall on the PC
1. Open https://dmo18.github.io/dv/wallboard.html?diag=1 on the wall PC. Wait 10 minutes.
2. Every tile shows its picture size, dropped frames and state; the corner shows "playing 17/17".
3. Windows display scaling should be 100 percent (1080p needs none).

## Run it locally
1. Open a terminal in this folder.
2. Run `python3 -m http.server 8765`
3. Open http://localhost:8765/wallboard.html

## Change it
- Every page has a version in its title. Raise it on every change and add a line to the changelog at the top of the file.
- Merging to main publishes the site.
- Streams for the wall are in wallboard.html (TILES list) and must also be saved in encoder.html.
- Headline sources: headlines-feeds.json. Price symbols: ticker-symbols.json.
- See docs/TAKEOVER.md for what each part does and docs/STATUS.md for decisions and known issues.
