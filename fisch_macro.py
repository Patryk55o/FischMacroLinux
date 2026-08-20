#!/usr/bin/env python3
"""
Fisch Cream's Macro (Free Edition) - Python / Linux port
Original AutoHotkey script by Cweamya (https://github.com/Cweamy/Fisch-Cream-s-Macro)

This is a straightforward line-for-line port of the AHK logic to Python so it
can run on Debian (or any X11-based Linux). It works purely by looking at
on-screen colors and clicking the mouse - it does not read game memory or
touch the Roblox process in any way.

REQUIREMENTS
------------
System packages (X11 only - this will NOT work under Wayland):
    sudo apt install xdotool

Python packages:
    pip install mss numpy pyautogui pynput

USAGE
-----
1. Put Roblox (or your Linux Roblox client, e.g. Sober) into fullscreen and
   enable Camera Mode for the fishing minigame, same as the original macro.
2. Set --window-title if your window isn't literally titled "Roblox".
3. Make sure your display scale is 100% (xrandr --dpi 96, or whatever your
   DE calls "100% scaling"). Like the original, this macro assumes 1:1
   pixel coordinates and WILL misbehave on fractional scaling.
4. Run:  python3 fisch_macro.py
5. Press SPACE at any time to stop.

This script intentionally keeps the same tuning constants, color
tolerances, and hold-time interpolation table as the original AHK script,
so behavior should match closely. yes
"""

import configparser
import math
import random
import subprocess
import sys
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock
from webhook import send_embed as WebSend
from webhook import WEBHOOK_ENABLED
from datetime import datetime

import numpy as np

try:
    import mss
except ImportError:
    sys.exit("Missing dependency: mss. Install with `pip install mss`")

try:
    import pyautogui
    pyautogui.FAILSAFE = False
    # pyautogui inserts a 0.1s pause after EVERY call by default (click,
    # mouseDown, keyDown, press, etc.) as a built-in safety default. That
    # was silently adding ~100ms to every single steering action regardless
    # of --pulse-ms, which is why lowering --pulse-ms had no visible effect -
    # the hidden pause was the actual floor on cycle speed, not the sleep
    # in the loop.
    pyautogui.PAUSE = 0
except ImportError:
    sys.exit("Missing dependency: pyautogui. Install with `pip install pyautogui`")

try:
    from pynput import keyboard
except ImportError:
    sys.exit("Missing dependency: pynput. Install with `pip install pynput`")


# ============================================================================
# Color targets (same values as the AHK script; 0xRRGGBB -> tolerance)
# ============================================================================
COLOR_UI = {0xffdcac: 10, 0x3d381b: 10, 0xfffde4: 10}
COLOR_FISH = {0x434b5b: 3, 0x4a4a5c: 4, 0x47515d: 4}
COLOR_WHITE = {0xFFFFFF: 15}
COLOR_BAR = {0x848587: 4, 0x787773: 4, 0x7a7873: 4}

# HoldFormula interpolation table: [hold_ms, pixel_distance_at_800px_width]
HOLD_DATA = [
    [0, 0], [16, 0], [132, 1], [217, 5], [365, 29], [450, 54], [534, 91],
    [632, 151], [736, 234], [817, 310], [900, 382], [997, 469], [1081, 541],
    [1164, 613], [1250, 686], [1347, 711], [1448, 721], [1531, 724], [1531, 9999],
]


def hex_to_rgb(h):
    return ((h >> 16) & 0xFF, (h >> 8) & 0xFF, h & 0xFF)


# ============================================================================
# Window handling (X11 via xdotool)
# ============================================================================
class WindowError(Exception):
    pass


def xdotool(*args):
    result = subprocess.run(["xdotool", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise WindowError(result.stderr.strip() or f"xdotool {args} failed")
    return result.stdout.strip()


def find_window(title_substring):
    out = xdotool("search", "--name", title_substring)
    ids = [w for w in out.splitlines() if w.strip()]
    if not ids:
        raise WindowError(f"No window found matching '{title_substring}'")
    return ids[0]


def select_window_interactively():
    """
    Lets the user click on the target window to select it, instead of
    matching by title (which is unreliable since Linux Roblox clients like
    Sober don't necessarily title their window "Roblox").
    """
    print("Click on the game window to select it...")
    result = subprocess.run(["xdotool", "selectwindow"], capture_output=True, text=True)
    win_id = result.stdout.strip()
    if result.returncode != 0 or not win_id:
        raise WindowError(result.stderr.strip() or "selectwindow failed or was cancelled")
    return win_id


def activate_window(win_id):
    xdotool("windowactivate", "--sync", win_id)


def is_window_active(win_id):
    try:
        active = xdotool("getactivewindow")
        return active == win_id
    except WindowError:
        return False


def get_window_geometry(win_id):
    out = xdotool("getwindowgeometry", "--shell", win_id)
    info = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            info[k] = v
    return int(info["WIDTH"]), int(info["HEIGHT"])


def get_screen_size():
    with mss.mss() as sct:
        mon = sct.monitors[1]  # monitor 1 = full virtual screen in mss convention on most setups
        return mon["width"], mon["height"]


# ============================================================================
# Screen / color search helpers
# ============================================================================
_PREPARED_COLOR_CACHE = {}


def _prepared(colors: dict):
    """
    Precompute (target_array, tolerance) pairs for a color dict once and
    cache by identity. These are constant module-level dicts that are never
    mutated, so caching by id() is safe and avoids rebuilding small numpy
    arrays on every single frame in the hot tracking loop.
    """
    key = id(colors)
    cached = _PREPARED_COLOR_CACHE.get(key)
    if cached is None:
        cached = [(np.array(hex_to_rgb(h), dtype=np.int16), tol) for h, tol in colors.items()]
        _PREPARED_COLOR_CACHE[key] = cached
    return cached


class ColorSearcher:
    def __init__(self):
        self._sct = mss.mss()
        self._lock = Lock()

    def grab(self, x1, y1, x2, y2):
        """Grab a region and return an (h, w, 3) int16 RGB numpy array plus its origin."""
        left, top = int(min(x1, x2)), int(min(y1, y2))
        width, height = max(1, int(abs(x2 - x1))), max(1, int(abs(y2 - y1)))
        with self._lock:
            raw = self._sct.grab({"left": left, "top": top, "width": width, "height": height})
        arr = np.array(raw)  # BGRA
        rgb = arr[:, :, [2, 1, 0]].astype(np.int16)
        return rgb, left, top

    @staticmethod
    def match_in_frame(rgb, left, colors: dict):
        """
        Test one color set against an already-captured frame (no new screenshot).
        Mirrors AHK Search()/PixelSearch scan order: top-to-bottom, left-to-right.
        np.nonzero on a C-contiguous array already returns indices in that order,
        so no extra sorting is needed.
        """
        for target, tol in _prepared(colors):
            diff = np.abs(rgb - target)
            mask = np.all(diff <= tol, axis=2)
            if mask.any():
                ys, xs = np.nonzero(mask)
                return int(left + xs[0])
        return None

    @staticmethod
    def match_xy_in_frame(rgb, left, top, colors: dict):
        for target, tol in _prepared(colors):
            diff = np.abs(rgb - target)
            mask = np.all(diff <= tol, axis=2)
            if mask.any():
                ys, xs = np.nonzero(mask)
                return int(left + xs[0]), int(top + ys[0])
        return None

    @staticmethod
    def match_xy_count_in_frame(rgb, left, top, colors: dict):
        """Like match_xy_in_frame, but also returns how many pixels matched -
        lets callers filter out single-pixel false positives (glare, UI
        text, etc.) from a real solid-colored target."""
        for target, tol in _prepared(colors):
            diff = np.abs(rgb - target)
            mask = np.all(diff <= tol, axis=2)
            count = int(mask.sum())
            if count > 0:
                ys, xs = np.nonzero(mask)
                return int(left + xs[0]), int(top + ys[0]), count
        return None

    @staticmethod
    def match_minmax_x_in_frame(rgb, left, colors: dict):
        """
        Returns (min_x, max_x) across ALL pixels matching any of the given
        colors, combined - not just the first match. Used to find the two
        arrow glyphs inside the minigame reticle box: the box's own fill
        color shifts dynamically (observed brown / olive-green / near-white
        depending on distance from target), so matching a fixed fill color
        is unreliable. The arrow glyphs stay a constant gray regardless of
        that fill color, so their combined min/max x gives a reliable box
        center estimate: (min_x + max_x) / 2.

        NOTE: kept for compatibility/simple cases, but prefer
        find_arrow_runs() below - the dark track texture produces scattered
        single-pixel false-positive matches for this color range, which
        corrupt a naive global min/max badly (observed spanning hundreds of
        extra pixels past the real arrows in testing against real
        screenshots).
        """
        xs_all = []
        for target, tol in _prepared(colors):
            diff = np.abs(rgb - target)
            mask = np.all(diff <= tol, axis=2)
            if mask.any():
                xs_all.append(np.nonzero(mask)[1])
        if not xs_all:
            return None
        all_xs = np.concatenate(xs_all)
        return int(left + all_xs.min()), int(left + all_xs.max())

    @staticmethod
    def find_arrow_runs(rgb, left, colors: dict, min_col_count=3, max_gap=5, width_range=(10, 50)):
        """
        Find real arrow-glyph blobs, filtering out scattered single-pixel
        noise matches from the dark track texture. A real arrow glyph has
        real vertical extent (many matching pixels stacked in the same
        column), unlike a stray noise pixel. Columns are required to have
        at least min_col_count matches to count, then grouped into
        contiguous runs (allowing small gaps), and only runs whose width
        falls in a plausible arrow-glyph range are kept.

        Returns a list of (min_x, max_x) tuples in absolute screen
        coordinates, one per detected arrow blob (typically 0, 1, or 2).
        """
        combined = None
        for target, tol in _prepared(colors):
            diff = np.abs(rgb - target)
            mask = np.all(diff <= tol, axis=2)
            combined = mask if combined is None else (combined | mask)
        if combined is None:
            return []
        col_counts = combined.sum(axis=0)
        good = np.nonzero(col_counts >= min_col_count)[0]
        if len(good) == 0:
            return []
        runs = []
        start = good[0]
        prev = good[0]
        for c in good[1:]:
            if c - prev > max_gap:
                runs.append((start, prev))
                start = c
            prev = c
        runs.append((start, prev))
        return [(int(left + r[0]), int(left + r[1])) for r in runs
                if width_range[0] <= (r[1] - r[0]) <= width_range[1]]

    @staticmethod
    def match_centroid_x_in_frame(rgb, left, colors: dict, min_pixels=1):
        """
        Returns the mean x-position of all matching pixels (not just the
        first), across every color in the set combined into one mask. Used
        for anchoring on the reticle's arrow icons, whose gray fill stays
        consistent regardless of the reticle's own background fill color
        (confirmed: same ~(132,132,132) gray whether the reticle renders
        white or olive). If both arrows are visible the centroid lands near
        the reticle's true center; if only one is visible (reticle pinned
        near an edge) it lands near that arrow instead, which is still a
        usable directional signal at the edges.
        """
        combined_mask = None
        for target, tol in _prepared(colors):
            diff = np.abs(rgb - target)
            mask = np.all(diff <= tol, axis=2)
            combined_mask = mask if combined_mask is None else (combined_mask | mask)
        if combined_mask is None or combined_mask.sum() < min_pixels:
            return None
        ys, xs = np.nonzero(combined_mask)
        return left + float(np.mean(xs))

    def find_color(self, x1, y1, x2, y2, colors: dict):
        """Convenience wrapper: grab + match in one call, for single-color-set lookups."""
        rgb, left, top = self.grab(x1, y1, x2, y2)
        return self.match_in_frame(rgb, left, colors)

    def find_color_xy(self, x1, y1, x2, y2, colors: dict):
        rgb, left, top = self.grab(x1, y1, x2, y2)
        return self.match_xy_in_frame(rgb, left, top, colors)

    def find_color_xy_count(self, x1, y1, x2, y2, colors: dict):
        rgb, left, top = self.grab(x1, y1, x2, y2)
        return self.match_xy_count_in_frame(rgb, left, top, colors)


# ============================================================================
# Status line (replacement for AHK's Tooltip stack)
# ============================================================================
class StatusBoard:
    def __init__(self):
        self._fields = {}
        self._lock = Lock()

    def set(self, key, value):
        with self._lock:
            self._fields[key] = value
        self._render()

    def get(self, key, default=None):
        with self._lock:
            return self._fields.get(key, default)

    def clear(self, key):
        with self._lock:
            self._fields.pop(key, None)
        self._render()

    def _render(self):
        with self._lock:
            parts = [f"{k}: {v}" for k, v in self._fields.items()]
        line = " | ".join(parts)
        sys.stdout.write("\r\033[K" + line)
        sys.stdout.flush()


# ============================================================================
# Mouse helpers
# ============================================================================
class MouseController:
    def __init__(self):
        self._down = False

    def set(self, down: bool):
        if down != self._down:
            if down:
                pyautogui.mouseDown()
            else:
                pyautogui.mouseUp()
            self._down = down

    def click(self, x, y):
        pyautogui.click(x, y)

    def move(self, x, y):
        pyautogui.moveTo(x, y)


class KeyHoldController:
    """Holds/releases a single key, mirroring MouseController's down/up API.
    Used for the minigame steering input (spacebar) instead of the mouse -
    key events appear to have lower round-trip latency than simulated mouse
    button events for this game."""

    def __init__(self, key="space"):
        self.key = key
        self._down = False

    def set(self, down: bool):
        if down != self._down:
            if down:
                pyautogui.keyDown(self.key)
            else:
                pyautogui.keyUp(self.key)
            self._down = down


# ============================================================================
# Main macro
# ============================================================================
@dataclass
class Regions:
    minigame: tuple
    shake: tuple


class FischMacro:

    def __init__(self, window_title=None, settings_path="Settings.ini", exit_key=None,
                 shake_min_pixels=12, hold_scale=1.0, fixed_pulse_ms=None, steering_input="mouse",
                 deadzone_px=6):
        self.window_title = window_title
        self.settings_path = Path(settings_path)
        self.exit_key = exit_key if exit_key is not None else keyboard.Key.f8
        self.shake_min_pixels = shake_min_pixels
        self.hold_scale = hold_scale
        self.fixed_pulse_ms = fixed_pulse_ms
        self.deadzone_px = deadzone_px
        self.searcher = ColorSearcher()
        self.mouse = MouseController()
        self.key = KeyHoldController("space")
        # Minigame steering controller. Defaults to mouse: real-world testing
        # with a hardware autoclicker (100ms interval / 100ms hold) confirmed
        # mouse works and spacebar didn't, contrary to the earlier guess that
        # keyboard events would have lower latency. Selectable via
        # --steering in case that's worth revisiting later.
        self.steer = self.key if steering_input == "space" else self.mouse
        self.status = StatusBoard()
        self.stop_event = Event()

        self.click_count = 0
        self.fail_save_count = 0
        self.catch_total = 0
        self.control = 0
        self.run_start = time.time()

    def SWebH(self): # this means: SendWebHook
        while True:
            self.current_time = datetime.now().strftime("%H:%M:%S")
            # Sending Webhook
            WebSend(
                "Fisch Macro Caught Total",
                f"## The Macro has caught: {self.catch_total} fish. \n \n ### The Current Status is: {self.status.get('Task', 0)} \n \n-# The message has been sent at {self.current_time}",
                0x2BFB42
            )
            time.sleep(60)

    # -- setup -------------------------------------------------------------
    def setup_exit_hotkey(self, exit_key):
        def on_press(key):
            if key == exit_key:
                # pynput listens system-wide, not just while the game is
                # focused - make it obvious this was a deliberate keypress
                # and not a crash, since the loop otherwise fails silently.
                print(f"\n[Exit key pressed - stopping macro]")
                self.stop_event.set()
                return False  # stop listener

        listener = keyboard.Listener(on_press=on_press)
        listener.daemon = True
        listener.start()

    def activate_roblox(self):
        try:
            if self.window_title:
                # Explicit title was given - search by that instead of prompting.
                win_id = find_window(self.window_title)
            else:
                win_id = select_window_interactively()
        except WindowError as e:
            sys.exit(f"Could not select the game window: {e}")
        activate_window(win_id)
        time.sleep(1)
        if not is_window_active(win_id):
            sys.exit("Failed to activate the game window.")
        return win_id

    def maybe_fullscreen(self, win_id):
        screen_w, screen_h = get_screen_size()
        w, h = get_window_geometry(win_id)
        if w < screen_w and h < screen_h:
            pyautogui.press("f11")
            time.sleep(1)
        return get_window_geometry(win_id)

    def compute_regions(self, roblox_w, roblox_h):
        ingame_ui = (
            round((roblox_w / 2560) * 2260), round((roblox_h / 1080) * 980),
            round((roblox_w / 2560) * 2560), round((roblox_h / 1080) * 1080),
        )
        minigame = (
            round((roblox_w / 2560) * 763), round((roblox_h / 1080) * 899),
            round((roblox_w / 2560) * 1796), round((roblox_h / 1080) * 939),
        )
        shake = (
            round((roblox_w / 800) * 100), round((roblox_h / 800) * 175),
            round((roblox_w / 800) * 700), round((roblox_h / 800) * 675),
        )
        return ingame_ui, minigame, shake

    def check_camera_mode(self, ingame_ui, roblox_w, roblox_h, screen_w, screen_h):
        for _ in range(5):
            found = self.searcher.find_color(*ingame_ui, COLOR_UI)
            if found is not None:
                self.status.set("Notice", "Please open Camera Mode")
                time.sleep(1)
                x = round((roblox_w / 2560) * 2525)
                y = 35 if (roblox_w >= screen_w and roblox_h >= screen_h) else 65
                self.mouse.click(x, y)
                time.sleep(1)
                break

    # -- helpers mirroring the AHK functions --------------------------------
    def search(self, region, colors):
        return self.searcher.find_color(*region, colors)

    def wait(self, region_minigame, time_ms):
        """Mirrors AHK Wait(): polls fish position; returns True/False like original."""
        start = time.time()
        while True:
            if self.stop_event.is_set():
                return False
            fish_pos = self.search(region_minigame, COLOR_FISH)
            if fish_pos is None or fish_pos < region_minigame[0] or fish_pos > region_minigame[2]:
                return bool(fish_pos)
            if (time.time() - start) * 1000 > time_ms:
                break
        return False

    def locate_bar(self, rgb, left):
        """
        Find the reticle's horizontal position. Prioritizes the arrow icon's
        centroid (COLOR_BAR) since it's confirmed consistent regardless of
        the reticle's own fill color, which shifts across a red->green range
        depending on game state and makes fill-color matching (COLOR_WHITE)
        unreliable most of the time. Falls back to the old white-fill-based
        estimate, then a plain first-match on the arrow color, if the
        centroid approach comes up empty.
        """
        centroid = self.searcher.match_centroid_x_in_frame(rgb, left, COLOR_BAR, min_pixels=3)
        if centroid is not None:
            return centroid
        white = self.searcher.match_in_frame(rgb, left, COLOR_WHITE)
        if white is not None:
            return white + round(self.control * 0.5)
        return self.searcher.match_in_frame(rgb, left, COLOR_BAR)

    def hold_formula(self, pixel, roblox_w):
        if self.fixed_pulse_ms is not None:
            # Fixed-pulse mode: always hold for the same constant duration
            # every cycle, regardless of which side of the bar the fish is
            # on. The crossing-safety clamp below exists to guard the
            # variable interpolation table, which doesn't apply here - on a
            # low-Control rod the fish crosses paths constantly, and that
            # clamp was silently forcing 0ms (i.e. ignoring --pulse-ms)
            # almost every cycle.
            hold = self.fixed_pulse_ms
            self.status.set("Hold", f"{hold:.0f}ms (fixed)")
            return hold

        # Fish crossed past the bar's original snapshot position -> stop holding.
        # (In AHK this indexes data[0-1], which is an out-of-bounds/blank read;
        # in Python that silently wraps to the LAST table entry instead, which
        # produced huge bogus hold times right when the fish changes direction
        # - the exact moment tracking accuracy matters most. Treat it as "done".)
        if pixel <= 0:
            self.status.set("Hold", "0ms")
            return 0

        data = [[a, math.floor(b * (roblox_w / 800))] for a, b in HOLD_DATA]
        lower = upper = None
        for i, pair in enumerate(data):
            if pixel < pair[1]:
                lower = data[max(i - 1, 0)]
                upper = pair
                break
        if lower is None or upper is None:
            lower, upper = data[-2], data[-1]
        if upper[1] == lower[1]:
            hold = lower[0]
        else:
            hold = lower[0] + (pixel - lower[1]) * (upper[0] - lower[0]) / (upper[1] - lower[1])
        hold *= self.hold_scale
        self.status.set("Hold", f"{hold:.0f}ms")
        return hold

    def reels(self, roblox_w, roblox_h):
        self.status.set("Task", "Casting Rod")
        self.mouse.move(80, 400)
        pyautogui.press("m")
        self.mouse.set(True)
        time.sleep(random.uniform(0.6, 1.2))
        self.mouse.set(False)
        self.status.set("Task", "Waiting for bobber")
        time.sleep(random.uniform(1.0, 1.2))
        self.click_count = 0

    def click_shake(self, shake_region):
        result = self.searcher.find_color_xy_count(*shake_region, COLOR_WHITE)
        if result is None:
            return None
        x, y, count = result
        if count < self.shake_min_pixels:
            # Too small to be a real shake button - likely glare, UI text,
            # or another stray white pixel. Ignoring this prevents the
            # fail-safe timer from being reset forever by a false positive,
            # which was causing the macro to sit "stuck" thinking it was
            # mid-shake and never re-cast.
            return None
        pyautogui.press("enter")
        time.sleep(0.1)
        return (x, y)

    def estimate_bar_center(self, rgb, left, x_left, x_right):
        """
        Estimate the reticle box's horizontal center from its two arrow
        glyphs (constant gray, unlike the box's own dynamically-shifting
        fill color). Handles two real-world cases seen in actual gameplay
        screenshots:
          - both arrows visible: center = midpoint between them.
          - only one arrow visible (box pushed against a track edge, so the
            other arrow isn't rendered): estimate the far edge using the
            known box width (self.control) and which track edge the single
            arrow is closer to.
        Returns None if no arrow blob is found at all.
        """
        runs = self.searcher.find_arrow_runs(rgb, left, COLOR_BAR)
        if not runs:
            return None
        if len(runs) >= 2:
            leftmost = min(r[0] for r in runs)
            rightmost = max(r[1] for r in runs)
            return (leftmost + rightmost) / 2
        # Only one arrow blob found.
        run_min, run_max = runs[0]
        run_center = (run_min + run_max) / 2
        if not self.control or self.control <= 0:
            return run_center
        half = self.control / 2
        if abs(run_center - x_left) < abs(run_center - x_right):
            # Closer to the left edge -> this is almost certainly the left
            # arrow, box center is to its right.
            return run_center + half
        else:
            return run_center - half

    def load_control_fallback(self):
        cfg = configparser.ConfigParser()
        if self.settings_path.exists():
            cfg.read(self.settings_path)
        control_raw = 0.0
        if cfg.has_option("Fisch", "Control"):
            try:
                control_raw = cfg.getfloat("Fisch", "Control", fallback=0.0)
            except ValueError:
                raw_str = cfg.get("Fisch", "Control", fallback="0")
                sys.exit(
                    f"Settings.ini has an unreadable Control value: '{raw_str}'. "
                    f"It should be a plain number like 0.23."
                )
        else:
            if not cfg.has_section("Fisch"):
                cfg.add_section("Fisch")
            cfg.set("Fisch", "Control", "0")
            with open(self.settings_path, "w") as f:
                cfg.write(f)
        return control_raw

    # -- main loop -----------------------------------------------------------
    def run(self):
        self.setup_exit_hotkey(self.exit_key)

        win_id = self.activate_roblox()
        roblox_w, roblox_h = self.maybe_fullscreen(win_id)
        screen_w, screen_h = get_screen_size()

        ingame_ui, minigame, shake = self.compute_regions(roblox_w, roblox_h)
        self.check_camera_mode(ingame_ui, roblox_w, roblox_h, screen_w, screen_h)

        print(f"Made by Cweamya (original AHK) - Python port. Press {self._exit_key_label()} to exit.")
        self.reels(roblox_w, roblox_h)
        last_shake_timer = time.time()

        if WEBHOOK_ENABLED:
            threading.Thread(target=self.SWebH, daemon=True).start()

        while not self.stop_event.is_set():
            try:
                activate_window(win_id)
            except WindowError:
                pass

            shake_result = self.click_shake(shake)
            if shake_result:
                x, y = shake_result
                self.click_count += 1
                self.status.set("Task", "Shaking")
                self.status.set("Click", f"({x},{y})")
                self.status.set("ClickCount", self.click_count)
                last_shake_timer = time.time()

            if time.time() - last_shake_timer > 7:
                self.status.set("Task", "FailSafe activated")
                self.fail_save_count += 1
                self.status.set("FailSaveCount", self.fail_save_count)
                pyautogui.press("m")
                time.sleep(0.25)
                self.reels(roblox_w, roblox_h)
                last_shake_timer = time.time()

            fish_frame_rgb, fish_frame_left, _ = self.searcher.grab(*minigame)
            fish_found = self.searcher.match_in_frame(fish_frame_rgb, fish_frame_left, COLOR_FISH) is not None
            # Was COLOR_WHITE - only reliably present when the box happens
            # to be in its near-aligned fill-color state, which usually
            # isn't true right when the minigame starts. The arrow glyphs
            # are present regardless of the box's fill color/state.
            box_found = self.searcher.match_in_frame(fish_frame_rgb, fish_frame_left, COLOR_BAR) is not None
            if fish_found and box_found:
                self.play_minigame(minigame, roblox_w, roblox_h)
                self.catch_total += 1
                self.status.set("CatchTotal", self.catch_total)
                time.sleep(3)
                self.reels(roblox_w, roblox_h)
                last_shake_timer = time.time()

        # Make sure nothing is left held down (mouse button or space key)
        # if the macro stops mid-hold.
        self.mouse.set(False)
        self.key.set(False)
        print("\nStopped.")

    def _exit_key_label(self):
        name = getattr(self.exit_key, "name", None) or str(self.exit_key)
        return name.upper()

    def play_minigame(self, minigame, roblox_w, roblox_h):
        x_left, y_left, x_right, y_right = minigame

        if not self.control:
            for _ in range(50):
                rgb, left, top = self.searcher.grab(x_left, y_left, x_right, y_right)
                # Use the arrow glyphs (constant gray regardless of the
                # box's dynamically-shifting fill color) rather than the
                # old white-color search, which only matched the box's
                # rare near-aligned/near-white state. Require BOTH arrows
                # here specifically, since a single visible arrow (box
                # pushed against a track edge) would give a badly wrong
                # width measurement.
                runs = self.searcher.find_arrow_runs(rgb, left, COLOR_BAR)
                if len(runs) >= 2:
                    leftmost = min(r[0] for r in runs)
                    rightmost = max(r[1] for r in runs)
                    self.control = rightmost - leftmost
                    if self.control > 0:
                        break
                time.sleep(0.02)
            if self.control <= 0:
                control_raw = self.load_control_fallback()
                self.control = round((roblox_w / 800) * ((320 * control_raw) + 97))

        self.status.set("Task", "Playing Bar Minigame")

        if self.fixed_pulse_ms is not None:
            self._play_minigame_fixed_pulse(minigame)
        else:
            self._play_minigame_formula(minigame, roblox_w)

        self.status.set("Task", "Minigame ended, restarting")

    def _play_minigame_fixed_pulse(self, minigame):
        """
        Simple, direction-aware bang-bang controller with an exact fixed
        cadence: check which way the fish is relative to the bar, hold or
        release accordingly, sleep exactly --pulse-ms, repeat.

        This intentionally bypasses hold_formula()/wait() entirely - those
        exist to support the variable-duration formula path and introduce
        their own timing (nested polling loops, a 0.6x post-release sleep,
        etc.) that made the actual on/off timing drift away from the
        requested pulse length. This loop's cadence is just time.sleep(),
        so what you set in --pulse-ms is what you get.

        A small dead-zone (--deadzone-px) prevents direction chattering:
        without it, a naive bang-bang controller flips direction on every
        single pixel of noise right around the setpoint, which looks like
        constant "going left and right" instead of settling near center.
        """
        x_left, y_left, x_right, y_right = minigame
        pulse_s = self.fixed_pulse_ms / 1000.0
        hold_right = None  # unknown yet - first reading always sets it

        while True:
            if self.stop_event.is_set():
                self.steer.set(False)
                return

            cycle_start = time.time()

            rgb, left, top = self.searcher.grab(x_left, y_left, x_right, y_right)
            fish_pos = self.searcher.match_in_frame(rgb, left, COLOR_FISH)
            if fish_pos is None:
                break

            # The box's own fill color shifts dynamically (brown / olive /
            # near-white depending on distance from target), so it can't be
            # used to find the box reliably. estimate_bar_center() uses the
            # two arrow glyphs instead, which stay a constant gray
            # regardless of that fill color.
            bar = self.estimate_bar_center(rgb, left, x_left, x_right)
            capture_ms = (time.time() - cycle_start) * 1000
            if bar is None:
                time.sleep(pulse_s)
                continue

            rng = fish_pos - bar
            if abs(rng) <= self.deadzone_px or hold_right is None:
                # Within the dead-zone (or first reading): keep whatever
                # direction we were already holding instead of flip-flopping
                # on noise. Only a clear, unambiguous offset changes direction.
                if hold_right is None:
                    hold_right = rng >= 0
            else:
                hold_right = rng >= 0
            self.steer.set(hold_right)
            # capture_ms is how long screenshotting+detection itself took, BEFORE
            # the requested sleep. If this is close to (or bigger than) your
            # --pulse-ms value, that's why lowering --pulse-ms has no visible
            # effect: the loop is bottlenecked by screen capture, not the sleep.
            self.status.set(
                "Direction",
                f"{'>' if hold_right else '<'} (pulse {self.fixed_pulse_ms:.0f}ms, capture {capture_ms:.0f}ms)"
            )
            time.sleep(pulse_s)

        self.steer.set(False)

    def _play_minigame_formula(self, minigame, roblox_w):
        x_left, y_left, x_right, y_right = minigame

        while True:
            if self.stop_event.is_set():
                return
            rgb, left, top = self.searcher.grab(x_left, y_left, x_right, y_right)
            fish_pos = self.searcher.match_in_frame(rgb, left, COLOR_FISH)
            if fish_pos is None:
                break

            if fish_pos < x_left + (self.control * 0.8):
                self.steer.set(False)
                self.status.set("Direction", "Max Left")
                continue
            elif fish_pos > x_right - (self.control * 0.8):
                self.steer.set(True)
                self.status.set("Direction", "Max Right")
                continue

            bar = self.estimate_bar_center(rgb, left, x_left, x_right)
            if bar is None:
                continue

            rng = fish_pos - bar

            if rng >= 0:
                self.status.set("Direction", ">")
                self.steer.set(True)
                hold_timer = time.time()
                original_pos = bar
                success = False
                while True:
                    if self.stop_event.is_set():
                        return
                    fish_pos = self.search(minigame, COLOR_FISH)
                    rng = (fish_pos - original_pos) if fish_pos is not None else 0
                    hold = self.hold_formula(rng, roblox_w)
                    elapsed_ms = (time.time() - hold_timer) * 1000
                    if not hold or fish_pos is None or elapsed_ms >= hold or self.wait(minigame, 10):
                        success = elapsed_ms >= hold
                        break
                if success:
                    self.steer.set(False)
                    time.sleep(max(0, (time.time() - hold_timer) * 0.6))
            else:
                self.status.set("Direction", "<")
                hold_timer = time.time()
                self.steer.set(False)
                rng = abs(rng)
                continue_now = False

                if self.wait(minigame, self.hold_formula(rng, roblox_w) * 0.7):
                    continue

                while True:
                    if self.stop_event.is_set():
                        return
                    rgb, left, top = self.searcher.grab(*minigame)
                    fish_pos = self.searcher.match_in_frame(rgb, left, COLOR_FISH)
                    if fish_pos is None or self.searcher.match_in_frame(rgb, left, COLOR_WHITE) is not None:
                        break
                    if self.wait(minigame, 10):
                        continue_now = True
                        break
                    current_position = self.estimate_bar_center(rgb, left, x_left, x_right)
                    if current_position is None:
                        continue
                    if current_position <= fish_pos:
                        break

                if continue_now:
                    continue

                self.steer.set(True)
                elapsed_ms = (time.time() - hold_timer) * 1000
                if self.wait(minigame, elapsed_ms):
                    self.steer.set(False)
                    continue

                self.steer.set(False)


def resolve_exit_key(name):
    """Map a CLI-friendly key name to a pynput Key. Defaults to F8."""
    name = name.strip().lower()
    aliases = {"escape": "esc"}
    name = aliases.get(name, name)
    if hasattr(keyboard.Key, name):
        return getattr(keyboard.Key, name)
    sys.exit(f"Unrecognized --exit-key '{name}'. Try something like: f8, f9, esc, pause.")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Fisch Cream's Macro - Python/Linux port")
    parser.add_argument("--window-title", default=None,
                         help="Optional: substring of the game window title to match. "
                              "If omitted (default), you'll be prompted to click on the "
                              "game window instead (via `xdotool selectwindow`).")
    parser.add_argument("--settings", default="Settings.ini",
                         help="Path to Settings.ini for the Control fallback value")
    parser.add_argument("--exit-key", default="f8",
                         help="Key that stops the macro (default: f8). This is a SYSTEM-WIDE "
                              "hotkey, not scoped to the game window, so avoid keys you press "
                              "often elsewhere (e.g. 'space' is a bad choice - it's a common "
                              "video/browser pause key and can stop the macro by accident).")
    parser.add_argument("--shake-min-pixels", type=int, default=12,
                         help="Minimum matched white pixels required before a shake-button "
                              "detection counts (default: 12). Raise this if the macro gets "
                              "'stuck' thinking it's shaking when it isn't (false positives "
                              "from glare/UI); lower it if real shake buttons are being missed.")
    parser.add_argument("--hold-scale", type=float, default=1.0,
                         help="Multiplier applied to every minigame hold duration (default: 1.0 "
                              "= unchanged). On low-Control rods the bar can overshoot a narrow "
                              "target zone even on the shortest possible pulse; try a smaller "
                              "value like 0.6-0.8 to shorten pulses and reduce overshoot. This is "
                              "experimental and may need tuning per rod. Ignored if --pulse-ms "
                              "is set.")
    parser.add_argument("--pulse-ms", type=float, default=None,
                         help="Bypass the variable hold-time formula entirely and always hold "
                              "the steering input for this many milliseconds (e.g. 100). Useful "
                              "when a rod's Control is so low the fish sits centered and the "
                              "variable formula can't converge - fast, fixed-length pulses can "
                              "spam faster than the formula allows. Overrides --hold-scale. "
                              "Default: unset (use the variable formula).")
    parser.add_argument("--steering", choices=["mouse", "space"], default="mouse",
                         help="Input used to steer the minigame bar (default: mouse). Real-world "
                              "testing with a hardware autoclicker confirmed mouse works better "
                              "than spacebar here, contrary to an earlier guess - this flag is "
                              "kept in case that's worth revisiting.")
    parser.add_argument("--deadzone-px", type=int, default=6,
                         help="Fixed-pulse mode only: minimum fish-to-bar offset (in pixels) "
                              "required before switching direction (default: 6). Prevents "
                              "direction chattering ('going left and right' near center) from a "
                              "naive bang-bang controller flipping on every pixel of noise. "
                              "Raise it if it still chatters, lower it (even to 0) if it feels "
                              "sluggish to correct.")
    args = parser.parse_args()

    exit_key = resolve_exit_key(args.exit_key)

    print(
        "This is Fisch Cream's Macro (Python/Linux port of the FREE VERSION).\n"
        "Original by Cweamya: https://github.com/Cweamy/Fisch-Cream-s-Macro\n"
        "Make sure Roblox is fullscreen with Camera Mode enabled, and your\n"
        "display scale is set to 100% before starting.\n"
        f"Press '{args.exit_key.upper()}' at any time to stop (this works system-wide,\n"
        "not just while the game is focused).\n"
    )

    macro = FischMacro(window_title=args.window_title, settings_path=args.settings, exit_key=exit_key,
                        shake_min_pixels=args.shake_min_pixels, hold_scale=args.hold_scale,
                        fixed_pulse_ms=args.pulse_ms, steering_input=args.steering,
                        deadzone_px=args.deadzone_px)
    try:
        macro.run()
    except KeyboardInterrupt:
        print("\nInterrupted.")


if __name__ == "__main__":
    main()
