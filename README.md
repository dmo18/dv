# dv wallboard

A news and markets wall for a second screen, served by GitHub Pages.

Live: https://dmo18.github.io/dv/wallboard.html

## Run it locally
1. Open a terminal in this folder.
2. Run `python3 -m http.server 8765`
3. Open http://localhost:8765/wallboard.html

## Change it
- Every page has a version in its title. Raise it on every change and add a line to the changelog at the top of the file.
- Merging to main publishes the site.
- The feed list for headlines is headlines-feeds.json. Streams for the encoder are in encoder.html.
- See docs/TAKEOVER.md for what each part does and docs/PLAN.md for current work.
