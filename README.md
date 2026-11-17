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
  at a time, hands over to itself, and refreshes prices every 2 minutes (every 15 minutes while all markets are closed)
  and headlines every 15 minutes.
  Crypto prices come live from Coinbase straight into the page.
- A daily check ("Check streams") plays every stream and checks the data is fresh. If something is wrong it opens
  one issue on GitHub (with a new comment each time the list changes, which is what sends the email). When all is
  well again it closes the issue. Spare streams that only sit in the encoder are listed but do not open an issue.

## Check the wall on the PC
1. On the wall PC open https://dmo18.github.io/dv/wallboard.html?diag=1 in the browser and press F11 for full screen.
2. Wait 10 minutes. Each video tile now shows a small line with its picture size, dropped frames and state, and the
   corner shows "playing 17/17 (+1 page tile)". The WSVN tile is its own page and shows "page tile, no data".
3. Take a photo of the screen with your phone. Also open Task Manager (Ctrl+Shift+Esc) and note the CPU percentage.
4. Send both to Claude. Good result: every tile under 5 percent dropped frames and CPU under 70 percent.
5. Windows display scaling should read 100 percent (right-click the desktop, Display settings, Scale). A 1080p
   screen does not need more.

## Open it
There is no local copy to run: everything is hosted on GitHub. Open https://dmo18.github.io/dv/wallboard.html
(or ticker.html, headlines.html, encoder.html in the same place).

## Change it
- Every page has a version in its title. Raise it on every change and add a line to the changelog at the top of the file.
- Merging to main publishes the site.
- Streams for the wall are in wallboard.html (TILES list) and must also be saved in encoder.html.
- Headline sources: headlines-feeds.json. Price symbols: ticker-symbols.json.
- See docs/TAKEOVER.md for what each part does and docs/STATUS.md for decisions and known issues.
