# dv wallboard

Static HTML wallboard (no build, no packages) on GitHub Pages. Target screen: 1920x1080, standalone PC.
Wall: 28 video tiles (4 large 480x270, 24 small 320x180, exact 16:9, dark frames) + a 40 px ticker + a 50 px headlines bar.
Live site: https://dmo18.github.io/dv/wallboard.html (the old 18-tile wall is wallboard.html?layout=18).
Also: tv.html (Red Macho Devil TV, a channel changer with a guide) and its Fire TV app (firetv/, install file rmtv.apk).

## Run and check
- GitHub only: NEVER host anything locally (no http.server, no localhost, no local copies served). Pages are opened and
  tested on the live site https://dmo18.github.io/dv/wallboard.html (add ?diag=1 for the per-tile check view).
- Checks: headless Chromium via Playwright (`require('playwright')`) against the live github.io pages; static checks
  (compile, YAML parse, dash and secret scans) run on the files directly.
- Jobs: relay.yml (Data relay, prices every 2 min, headlines every 15 min), stream-check.yml (daily), headlines.yml (safety net),
  epg.yml (TV guide "now and next", every 30 min, branch epg-data), firetv-lab-test.yml (by hand: builds the Fire TV app and
  tests it on a virtual Android device; results in the release "firetv-lab")

## Conventions
- Every file title carries a version (Rev N or vN). Bump it on every change and add a changelog line at the top of the file.
- Wallboard self-reloads when its title version changes, so bump wallboard.html when a child page changes.
- Use relative links, not absolute site addresses.
- Merge to main after testing (standing instruction). Branch name: claude/awesome-volta-kwfe4l, restart from main after each merge.
- Channel list: channels.json (name, url, desc, off) is the one list used by encoder.html, tv.html and the daily check.
  Every video stream used on the wallboard (main and backup addresses, exact match) must also be in channels.json.
- tv.html must work in old web views (the Fire TV lab uses Chrome 83): no CSS "inset", no :focus-visible in selector lists.
- rmtv.apk is replaced only with the exact file from a passing "Fire TV lab test" run.
- Never use em dashes or en dashes in text.
- Plain language for the user, who works from a phone.

## Tests run on GitHub, with real data only
- Workflows are tested by running them on GitHub (workflow_dispatch with short test inputs), never from this sandbox
  and never with fake data. This sandbox adds real credentials to github.com requests, so a "fake token" test still
  writes for real (it once replaced the live headlines for 7 minutes).
- Never write test or placeholder data to the branches ticker-data, headlines-data or epg-data.

## Do not touch
- Branches ticker-data and headlines-data: written by the Data relay only (one commit each, replaced every cycle).
- Branch epg-data: written by epg.yml only.
- .github/workflows without testing the script locally first.

## Docs
docs/TAKEOVER.md (what each part does), docs/PLAN.md, docs/STATUS.md (done, decisions, known issues), docs/FIRETV.md (TV app)
