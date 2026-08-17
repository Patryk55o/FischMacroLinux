<h3>
<p align='center'>
<img src="https://github.com/Patryk55o/FischMacroLinux/blob/main/FischLinuxLOGO2.svg">
</p>
</h3>

# Fisch Cream's Macro on Python / Debian Linux Port

A line-for-line port of the original AutoHotkey macro
(`Fisch_Cream_s_Macro_free_edition.Ahk` by Cweamya,
https://github.com/Cweamy/Fisch-Cream-s-Macro) to Python, so it runs on
Debian/X11 instead of Windows.

It works the same way the original did: it looks at on-screen pixel colors
and clicks/holds the mouse accordingly. It does not touch Roblox's memory
or process in any way.

## Install

System package (X11 window control, **this will not work under Wayland**):

```bash
sudo apt install xdotool
```

Python packages:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Before running

1. Roblox (or your Linux Roblox client, the official client isn't
   available on Linux, so you're likely using something like Sober) needs
   to be fullscreen with **Camera Mode** enabled for the fishing minigame.
2. Set your display scale to 100% (e.g. `xrandr --dpi 96`, or your desktop
   environment's scaling setting). Like the original AHK script, this
   assumes 1:1 pixel coordinates.
3. Change `Settings.ini` to your control in the game. e.g `0.2`
## Run

```bash
python3 fisch_macro.py
```

On startup it will print "Click on the game window to select it...", click
anywhere on your Roblox/Sober window and it selects and activates that
window (via `xdotool selectwindow`). This is more reliable than matching by
window title, since Linux Roblox clients don't necessarily title their
window "Roblox".

If you'd rather match by title instead of clicking, you can still do that:

```bash
python3 fisch_macro.py --window-title "Sober"
```

Press **F8** at any time to stop (default). This hotkey is **system-wide**,
it works even if the game isn't the focused window, so pick something you
won't press by accident elsewhere. Change it with `--exit-key`, e.g.:

```bash
python3 fisch_macro.py --exit-key esc
```

### Q: How do i configure my `--hold-scale` argument?

A: You need to test that argument how ever you want, the default is `1.0`, but if that value is really unstable (e.g loses fish really frequently) then you can use `--hold-scale 0.2459`, as that's my config. You probably shouldn't use this because the hold scale might depend on your system hardware, which if its weak then you might want to adjust the value to make it work as nicely.

### Q: Why don't you just make `0.2459` the default on the `--hold-scale` argument?

A: Making `0.2459` default would introduce more problems on weaker hardware, as I know there are some users that use weaker hardware (as some players might be using, e.g: a laptop from 2014.). Additionally it might make the macro slower, which is quite needed to react quickly to the minigame. That being said if that value does work, then you can use it.

### Tuning flags

```bash
python3 fisch_macro.py --shake-min-pixels 12 --hold-scale 1.0
```


- `--shake-min-pixels` (default 12): minimum matched white pixels before a
  shake-button detection counts. If the macro seems "stuck" doing nothing
  and never re-casts, raise this, it likely means something else in the
  shake region (glare, UI text) is matching white and keeps resetting the
  fail-safe timer forever. If real shakes are being missed, lower it.
  **Camera angle matters here too**: color matching can't distinguish a
  real shake button from a gray rock/object of similar color sitting in the
  background. Pointing the camera at something low-contrast and non-gray
  (water works well) instead of gray terrain/objects reduces false
  positives significantly, on top of whatever `--shake-min-pixels` you're
  using.
- `--hold-scale` (default 1.0): multiplier on every minigame hold duration.
  On low-Control rods, even the shortest possible pulse can overshoot a
  narrow target zone, so the bar oscillates instead of converging. Try
  0.6-0.8 to shorten pulses. This is experimental, I can't tune it against
  the live game myself, so treat it as a starting point to adjust per rod.
  Ignored if `--pulse-ms` is set.
- `--pulse-ms` (default: unset): skip the variable hold-time formula
  entirely and run a simple, direction-aware bang-bang loop instead: check
  which side the fish is on, hold or release, sleep exactly `--pulse-ms`,
  repeat. No nested waits, no extra delays, the cadence you set is the
  cadence you get. Useful when a rod's Control is so low the fish sits
  centered and the formula-based approach can't converge on it at all.
- `--deadzone-px` (default 6, fixed-pulse mode only): minimum fish-to-bar
  offset in pixels before switching direction. Without this, a naive
  bang-bang controller flips direction on every pixel of noise right around
  the setpoint, which looks like it's constantly "going left and right"
  instead of settling near center. Raise it if it still chatters, lower it
  toward 0 if correction feels sluggish.

## Diagnosing "changing --pulse-ms has no effect"

If capture benchmarks fast (confirmed via `benchmark_capture.py` and the
live `capture Xms` status readout) but lowering `--pulse-ms` still doesn't
visibly speed anything up in-game, the most likely explanation left is that
**Roblox itself only samples input at its own tick rate**, toggling faster
than the game polls for it simply won't be visible, no matter how fast
Python sends the events. Test this by trying something like `--pulse-ms 20`
vs `--pulse-ms 300`: if there's truly no difference across that whole
range, that's strong evidence it's a game-side limit rather than anything
fixable in this script.

If instead the symptom is the bar visibly "going left and right" near
center rather than settling, that's direction chattering, see
`--deadzone-px` above.

## Notes on the port

- `PixelSearch` → `mss` (screenshot capture) + `numpy` (vectorized color
  matching), same hex colors/tolerances as the original.
- `Click` / mouse down-up → `pyautogui`.
- `WinActivate` / window geometry → `xdotool` (X11 only).
- AHK's stacked on-screen `Tooltip`s are replaced with a single live status
  line printed to the terminal (task, click count, catch total, etc.).
  This is the one place I simplified rather than pixel-matched, an overlay
  GUI (e.g. a small always-on-top Tk window) would be a natural follow-up
  if you want an on-screen HUD instead of a terminal line.
- The `Control` bar-size auto-detection, hold-time interpolation table, and
  minigame steering logic are preserved as-is from the original script.
- The subscribe/Discord-prompt block from the original was inert (wrapped
  in a `/* */` comment block in the source) and isn't reproduced.
- The macro was tested on the SOULREAPER rod with -0.1 control, you're free
  to modify the `--hold-scale` argument to your liking.

## Changelog

- **Fixed (major):** the reticle box's position was being detected
  incorrectly most of the time. Analyzing real screenshots showed the box's
  own fill color actually shifts dynamically (observed brown, olive-green,
  and near-white depending on distance from target) - so the old
  "look for pure white, fall back to something else" logic only worked in
  the rare near-aligned state and gave inconsistent, jittery position
  reads the rest of the time. This is very likely what was causing
  "goes left and right, can't reach the target." Detection is now based on
  the two arrow glyphs inside the box, which stay a constant gray
  regardless of the box's fill color - confirmed against 3 real screenshots
  in 3 different box states. Handles both arrows visible (take the
  midpoint) and only one visible (box pushed against a track edge - estimate
  the far edge using the known box width). Also fixed the same theme-color
  problem in the minigame-start detection trigger in the main loop.
- **Added:** `--deadzone-px` (default 6) for fixed-pulse mode, to stop the
  bang-bang controller from chattering direction on every pixel of noise
  near the setpoint (the "going left and right" symptom).
- **Fixed (the real cause):** `--pulse-ms` being effectively capped around
  ~100ms no matter how low it was set. Screenshot capture was ruled out via
  `benchmark_capture.py` (sub-millisecond on this system). The actual cause:
  pyautogui inserts a **0.1 second pause after every single call** by
  default (`pyautogui.PAUSE`), as a built-in safety default, every
  `mouseDown`/`mouseUp`/`keyDown`/`keyUp` in the steering loop was silently
  eating ~100ms on top of whatever pulse length was requested. This was
  never explicitly disabled. Now set to `pyautogui.PAUSE = 0` at startup;
  every other call site already has its own explicit `time.sleep()` where a
  delay is actually needed, so this doesn't remove any intended pacing.
- **Added:** live capture-timing shown in the status line during fixed-pulse
  mode, plus a standalone `benchmark_capture.py` diagnostic script, to
  figure out why `--pulse-ms` might have no visible effect - most likely
  cause is screenshot capture itself being the bottleneck, not the sleep.
  See "Diagnosing" section above.
- **Fixed:** `--pulse-ms` not actually producing a fixed cadence, it was
  reusing the formula-mode loop's machinery (nested `wait()` polling, plus
  a `0.6×` sleep tacked on after every release), so the real on/off timing
  drifted around (e.g. alternating ~100ms/~300ms) instead of holding
  steady, causing overshoot. `--pulse-ms` now runs its own dedicated,
  minimal loop: decide direction, hold or release, sleep exactly the
  requested duration, repeat. Nothing else in the timing.
- **Reverted:** minigame steering back to **mouse** (was briefly switched to
  spacebar). Real-world testing with a hardware autoclicker (100ms interval,
  100ms hold, on the mouse button) confirmed mouse works and spacebar
  didn't, the earlier guess that keyboard events would have lower latency
  didn't hold up. Selectable via `--steering mouse|space` if worth
  revisiting later; default is `mouse`.
- **Fixed:** `--pulse-ms` being silently ignored. The crossing-safety clamp
  (added to fix the fish-tracking bug earlier) was checked *before* the
  fixed-pulse override, so on low-Control rods, where the fish crosses
  paths constantly, it kept forcing 0ms and `--pulse-ms` never got a
  chance to apply. Fixed-pulse mode now always returns the constant value,
  since that clamp only ever existed to guard the variable formula.
- **Changed:** minigame steering (moving the reel bar left/right) now holds
  **spacebar** instead of the mouse button, key events seem to have lower
  input latency than simulated mouse-down/up for this. Casting the rod
  (`reels()`) still uses the mouse, unchanged.
- **Added:** `--pulse-ms` to bypass the variable hold-time formula with a
  fixed-length pulse (e.g. `--pulse-ms 100`), for rods where Control is too
  low for the formula to converge on a centered fish. See Tuning flags above.
- **Fixed:** macro getting permanently "stuck" acting like it's mid-shake
  and never re-casting until you gave manual input. Cause: any white-ish
  pixel match in the shake region (even a single stray pixel from glare or
  UI) reset the fail-safe timer, so a persistent false positive meant the
  fail-safe never tripped and it never re-cast. Now requires a minimum
  matched-pixel blob size (`--shake-min-pixels`, default 12) before counting
  a detection as real.
- **Added:** `--hold-scale` multiplier for minigame hold durations, to help
  tune overshoot on low-Control rods (see Tuning flags above). This is an
  inherent limitation carried over from the original's fixed hold-time
  table, not something introduced by the port - the table's pixel steps
  don't scale with how narrow a given rod's control bar is.
- **Changed:** shaking now presses **Enter** instead of mouse-clicking the
  shake button's on-screen position. No more coordinate calculation needed,
  and every detected shake button gets pressed (the old version skipped a
  button if its position happened to match the previously clicked one).
- **Fixed:** crash when `Settings.ini` has a fractional `Control` value
  (e.g. `Control = 0.23`, which is normal/expected). The fallback loader was
  reading it with `getint()`, which only accepts whole numbers. Now reads it
  as a float, matching the formula it feeds into (`320 * Control + 97`) and
  matching how the original AHK `IniRead` (which doesn't distinguish int vs
  float) treated it.
- **Fixed:** the macro stopping unexpectedly after a few catches with no
  error. It wasn't crashing, the exit hotkey (spacebar) is a **system-wide**
  listener, not scoped to the game window (same as the original AHK
  `$space::exitapp`). Any spacebar press anywhere, alt-tabbing to pause a
  video, scroll a page, etc., silently stopped it, and there was no log
  message explaining why. Now: the default exit key is **F8** instead of
  space (configurable via `--exit-key`), and pressing it prints
  `[Exit key pressed - stopping macro]` so it's unmistakable next time.
- **Fixed:** fish-tracking loss. Two causes:
  1. `hold_formula()` could receive a negative pixel distance when the fish
     crossed to the other side of the bar mid-hold. Python's negative list
     indexing (`data[-1]`) silently wrapped to the *last* table entry
     instead of erroring like AHK would, producing wildly wrong hold times
     right at the moment the fish changed direction. Now negative distances
     return a 0ms hold (stop immediately), matching the original's intent.
  2. Each tracking loop iteration was taking 2-3 separate screenshots (one
     per color checked) instead of one shared screenshot. That added
     avoidable latency on every step. Fish/white/bar checks now reuse a
     single captured frame per iteration.
