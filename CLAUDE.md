# dv wallboard

Static HTML wallboard (no build, no packages) on GitHub Pages. Target screen: 1920x1080, standalone PC.

## Run and check
- Preview: `python3 -m http.server 8765`, open /wallboard.html
- Checks: headless Chromium via Playwright (`require('playwright')`), played against the local server
- Daily jobs: .github/workflows/stream-check.yml, headlines.yml

## Conventions
- Every file title carries a version (Rev N or vN). Bump it on every change and add a changelog line at the top of the file.
- Wallboard self-reloads when its title version changes, so bump wallboard.html when a child page changes.
- Use relative links, not absolute site addresses.
- Merge to main after testing (standing instruction). Branch name: claude/awesome-volta-kwfe4l, restart from main after each merge.
- Every video stream used on the wallboard (main and backup addresses, exact match) must also be saved in encoder.html. The encoder will be used on its own later.
- Never use em dashes or en dashes in text.
- Plain language for the user, who works from a phone.

## Tests must stay local
- Never push, fetch from or dispatch against the real GitHub remote in a test. This sandbox adds real credentials to
  github.com requests, so a "fake token" test still writes for real (it once replaced the live headlines for 7 minutes).
- Test the relay and any git publishing only against a local bare repository; never pass a github.com URL.

## Do not touch
- .github/workflows without testing the script locally first.
- headlines-data branch (written by the workflow only).

## Docs
docs/TAKEOVER.md, docs/PLAN.md, docs/STATUS.md
