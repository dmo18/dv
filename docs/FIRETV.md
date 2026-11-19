# Red Macho Devil TV on Fire TV

An Android app (folder `firetv/`) that opens the live TV page (`tv.html`) full screen. The app is only a window onto
https://dmo18.github.io/dv/tv.html, so every change made to the page shows up on the TV with no reinstall.

## Install (once)
1. Fire TV: Settings, My Fire TV, Developer Options, turn on "Install unknown apps" for the Downloader app (install
   Downloader from the Amazon Appstore first). Newer Fire TV models that run Vega OS do not accept Android apps;
   use the Silk browser and bookmark the TV page instead.
2. Open Downloader and type: `https://dmo18.github.io/dv/rmtv.apk` (the file `rmtv.apk` in the root of this repository)
3. Install, then open "Red Macho Devil TV" from Your Apps and Channels.

Updating from app 1.0 (the first build): uninstall the old app once (Settings, Applications, Manage Installed
Applications, Red Macho Devil TV, Uninstall), then install `rmtv.apk`. From 1.1 on, the app is signed with a fixed key
kept in this repository (`firetv/rmtv-debug.keystore`, not a secret), so later builds install straight over it.
The page shows "TV RevN · App 1.x" next to the educational note, so you can see what is running.

## Remote control
| Button | Does |
|---|---|
| Up, Down | next or previous channel (skips off-air channels while Skip off air is lit; in the guide: move through the list) |
| Left, Right | move along the bottom buttons (in the guide: switch All / On air) |
| Select | press the lit button or channel (when the overlay is hidden, the first press only shows it) |
| Menu | open or close the guide |
| Play/Pause | mute or unmute |
| Back | close the guide, else hide the overlay; with nothing on screen, "Press Back again to exit" and a second Back leaves |

Volume is the TV remote's own volume (the page's volume buttons show only in a normal browser). The overlay hides by
itself after 5 seconds without a press. While it shows, the top bar lists picture size, the stream's bitrate, the
measured download speed, seconds buffered, dropped frames and the network type.

## Lab test (2026-11-18)
`rmtv.apk` is the exact file that passed the "Fire TV lab test" workflow: a virtual 1080p Android 11 device with an old
Chrome 83 web view (a strict test). Checked: the app starts and presses "Tap to rock" itself, the TV page fills the screen,
Up changes channel, Menu opens the guide, Down moves through it, Back closes it, Play/Pause mutes and unmutes, typing a
channel number then Select tunes it, and the picture moves. Not checked: a real Fire TV remote and hardware.

## Build or rebuild
To rebuild and retest: GitHub, Actions tab, "Fire TV lab test", Run workflow. It builds `firetv/`, runs the lab test, and publishes
the screenshots and the tested file as the release `firetv-lab`; copy that `rmtv.apk` to the root of the repository.
("Build Fire TV app" only builds and replaces the release `firetv`, with no test.) The app is signed with a throwaway debug key, so to install a newer build over an older one, remove the old
app first. Rebuilds are rarely needed because the app opens the live site.

## Notes
- Sound starts without a tap (the app allows it). The app presses "Tap to rock" itself.
- Streams that use plain `http` addresses (WION) are blocked in any web view; they are not expected to play here.
- Educational use only, like the rest of the site.
