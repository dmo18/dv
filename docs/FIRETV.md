# Red Macho Devil TV on Fire TV

An Android app (folder `firetv/`) that opens the live TV page (`tv.html`) full screen. The app is only a window onto
https://dmo18.github.io/dv/tv.html, so every change made to the page shows up on the TV with no reinstall.

## Install (once)
1. Fire TV: Settings, My Fire TV, Developer Options, turn on "Install unknown apps" for the Downloader app (install
   Downloader from the Amazon Appstore first). Newer Fire TV models that run Vega OS do not accept Android apps;
   use the Silk browser and bookmark the TV page instead.
2. Open Downloader and type: `https://github.com/dmo18/dv/releases/download/firetv/RedMachoDevilTV.apk`
3. Install, then open "Red Macho Devil TV" from Your Apps and Channels.

## Remote control
| Button | Does |
|---|---|
| Up, Down | next or previous channel (in the guide: move through the list) |
| Left, Right | move between the bottom bar buttons |
| Select | press the highlighted button or channel |
| Menu | open or close the guide |
| Play/Pause | mute or unmute |
| Back | close the guide, or leave the app |

## Build or rebuild
GitHub, Actions tab, "Build Fire TV app", Run workflow. It builds `firetv/` and replaces the file in the release
named `firetv`. The app is signed with a throwaway debug key, so to install a newer build over an older one, remove the old
app first. Rebuilds are rarely needed because the app opens the live site.

## Notes
- Sound starts without a tap (the app allows it). The app presses "Tap to rock" itself.
- Streams that use plain `http` addresses (WION) are blocked in any web view; they are not expected to play here.
- Educational use only, like the rest of the site.
