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
import json
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
# Color targets (default values matching original script; 0xRRGGBB -> tolerance)
# ============================================================================
DEFAULT_COLOR_UI = {0xffdcac: 10, 0x3d381b: 10, 0xfffde4: 10}
DEFAULT_COLOR_FISH = {0x434b5b: 3, 0x4a4a5c: 4, 0x47515d: 4}
DEFAULT_COLOR_WHITE = {0xFFFFFF: 15}
DEFAULT_COLOR_BAR = {0x848587: 4, 0x787773: 4, 0x7a7873: 4}

COLOR_UI = dict(DEFAULT_COLOR_UI)
COLOR_FISH = dict(DEFAULT_COLOR_FISH)
COLOR_WHITE = dict(DEFAULT_COLOR_WHITE)
COLOR_BAR = dict(DEFAULT_COLOR_BAR)


def parse_color_dict(color_str, default_dict=None):
    """
    Parses a string of hex colors and tolerances into a dict {0xRRGGBB: tolerance}.
    Supports formats like:
      - "0xffdcac:10, 0x3d381b:10, 0xfffde4:10"
      - "#FFDCAC:10, #3D381B:10"
      - "0xffdcac, 0x3d381b" (uses default tolerance from default_dict or 10)
    """
    if not color_str or not isinstance(color_str, str) or not color_str.strip():
        return dict(default_dict) if default_dict is not None else {}

    default_tol = 10
    if default_dict and len(default_dict) > 0:
        default_tol = list(default_dict.values())[0]

    result = {}
    tokens = [t.strip() for t in color_str.split(",") if t.strip()]
    for token in tokens:
        if ":" in token:
            parts = token.split(":", 1)
            hex_str, tol_str = parts[0].strip(), parts[1].strip()
            try:
                tol = int(tol_str)
            except ValueError:
                tol = default_tol
        else:
            hex_str = token
            tol = default_tol

        hex_clean = hex_str.lower().replace("#", "").replace("0x", "")
        try:
            val = int(hex_clean, 16)
            result[val] = tol
        except ValueError:
            continue

    return result if result else (dict(default_dict) if default_dict is not None else {})


def format_color_dict(color_dict):
    """Formats a dict {0xRRGGBB: tolerance} into a string '0xRRGGBB:tol, 0xRRGGBB:tol'."""
    if not color_dict:
        return ""
    items = []
    for h, tol in color_dict.items():
        items.append(f"0x{h:06x}:{tol}")
    return ", ".join(items)


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
    def find_solid_bar_runs(rgb, left, colors: dict, min_col_count=2, max_gap=6, width_range=(15, 600)):
        """
        Find contiguous column runs matching any of the given colors, requiring
        at least min_col_count matching pixels per column to filter out single-pixel noise.
        Returns a list of (min_x, max_x) tuples in absolute screen coordinates.
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
                 deadzone_px=6, color_ui=None, color_fish=None, color_bar=None, color_white=None):
        self.window_title = window_title
        self.settings_path = Path(settings_path)
        self.exit_key = exit_key if exit_key is not None else keyboard.Key.f8
        self.shake_min_pixels = shake_min_pixels
        self.hold_scale = hold_scale
        self.fixed_pulse_ms = fixed_pulse_ms
        self.deadzone_px = deadzone_px
        self.color_ui = color_ui if color_ui is not None else dict(DEFAULT_COLOR_UI)
        self.color_fish = color_fish if color_fish is not None else dict(DEFAULT_COLOR_FISH)
        self.color_bar = color_bar if color_bar is not None else dict(DEFAULT_COLOR_BAR)
        self.color_white = color_white if color_white is not None else dict(DEFAULT_COLOR_WHITE)
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
            found = self.searcher.find_color(*ingame_ui, self.color_ui)
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

    def get_fish_center(self, rgb, left):
        """Locate the exact center X position of the fish icon."""
        centroid = self.searcher.match_centroid_x_in_frame(rgb, left, self.color_fish, min_pixels=2)
        if centroid is not None:
            return centroid
        return self.searcher.match_in_frame(rgb, left, self.color_fish)

    def find_fish_pos(self, region_minigame):
        rgb, left, _ = self.searcher.grab(*region_minigame)
        return self.get_fish_center(rgb, left)

    def wait(self, region_minigame, time_ms):
        """Mirrors AHK Wait(): polls fish position; returns True/False like original."""
        start = time.time()
        while True:
            if self.stop_event.is_set():
                return False
            fish_pos = self.find_fish_pos(region_minigame)
            if fish_pos is None or fish_pos < region_minigame[0] or fish_pos > region_minigame[2]:
                return bool(fish_pos)
            if (time.time() - start) * 1000 > time_ms:
                break
        return False

    def locate_bar(self, rgb, left):
        centroid = self.searcher.match_centroid_x_in_frame(rgb, left, self.color_bar, min_pixels=3)
        if centroid is not None:
            return centroid
        white = self.searcher.match_in_frame(rgb, left, self.color_white)
        if white is not None:
            return white + round(self.control * 0.5)
        return self.searcher.match_in_frame(rgb, left, self.color_bar)

    def hold_formula(self, pixel, roblox_w):
        if self.fixed_pulse_ms is not None:
            hold = self.fixed_pulse_ms
            self.status.set("Hold", f"{hold:.0f}ms (fixed)")
            return hold

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
        result = self.searcher.find_color_xy_count(*shake_region, self.color_white)
        if result is None:
            return None
        x, y, count = result
        if count < self.shake_min_pixels:
            return None
        pyautogui.press("enter")
        time.sleep(0.1)
        return (x, y)

    def estimate_bar_center(self, rgb, left, x_left, x_right):
        # 1. Primary: Check noise-filtered arrow runs (vertical blob detection, min 3 pixels per column)
        runs = self.searcher.find_arrow_runs(rgb, left, self.color_bar)
        if runs:
            if len(runs) >= 2:
                leftmost = min(r[0] for r in runs)
                rightmost = max(r[1] for r in runs)
                width = rightmost - leftmost
                # Lock in self.control from the real measured arrow spacing
                # the first time we get a clean two-arrow reading. This is
                # the reliable case (confirmed against real screenshots) -
                # without this, self.control never gets set here at all for
                # classic arrow-icon rods, since this branch only used to
                # read self.control, never write it. That silently forced
                # every arrow-rod session onto the much coarser Settings.ini
                # fallback formula instead of the actual on-screen width.
                if (not self.control or self.control <= 0) and width > 0:
                    self.control = width
                return (leftmost + rightmost) / 2
            run_min, run_max = runs[0]
            run_center = (run_min + run_max) / 2
            if not self.control or self.control <= 0:
                return run_center
            half = self.control / 2
            if abs(run_center - x_left) < abs(run_center - x_right):
                return run_center + half
            else:
                return run_center - half

        # 2. Secondary: Check noise-filtered solid bar runs for COLOR_BAR or COLOR_WHITE.
        # Used for skins with no fixed gray arrows (e.g. a solid/rainbow bar).
        bar_runs = self.searcher.find_solid_bar_runs(rgb, left, self.color_bar)
        if not bar_runs:
            bar_runs = self.searcher.find_solid_bar_runs(rgb, left, self.color_white)

        if bar_runs:
            widest = max(bar_runs, key=lambda r: r[1] - r[0])
            min_x, max_x = widest
            w = max_x - min_x
            # Only ever set self.control from this tier ONCE (while it's
            # still unset/invalid). Once we have a real reading, freeze it -
            # otherwise a per-rod constant would silently drift every frame
            # based on whatever run happens to be widest that frame (e.g. an
            # animated highlight segment on a rainbow bar), corrupting the
            # dead-zone thresholds and hold-time scaling mid-catch.
            if (not self.control or self.control <= 0) and 15 <= w <= round((x_right - x_left) * 0.85):
                self.control = w
            return (min_x + max_x) / 2

        # 3. Tertiary: Fall back to centroid matching for COLOR_BAR or COLOR_WHITE
        centroid = self.searcher.match_centroid_x_in_frame(rgb, left, self.color_bar, min_pixels=3)
        if centroid is not None:
            return centroid
        centroid_w = self.searcher.match_centroid_x_in_frame(rgb, left, self.color_white, min_pixels=3)
        if centroid_w is not None:
            return centroid_w

        return None

    def load_control_fallback(self):
        cfg = configparser.ConfigParser(strict=False)
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
            fish_found = self.get_fish_center(fish_frame_rgb, fish_frame_left) is not None
            box_found = self.estimate_bar_center(fish_frame_rgb, fish_frame_left, minigame[0], minigame[2]) is not None
            if fish_found and box_found:
                self.play_minigame(minigame, roblox_w, roblox_h)
                self.catch_total += 1
                self.status.set("CatchTotal", self.catch_total)
                time.sleep(3)
                self.reels(roblox_w, roblox_h)
                last_shake_timer = time.time()

        self.mouse.set(False)
        self.key.set(False)
        print("\nStopped.")

    def _exit_key_label(self):
        name = getattr(self.exit_key, "name", None) or str(self.exit_key)
        return name.upper()

    def play_minigame(self, minigame, roblox_w, roblox_h):
        x_left, y_left, x_right, y_right = minigame

        if not self.control or self.control <= 0:
            for _ in range(50):
                rgb, left, top = self.searcher.grab(x_left, y_left, x_right, y_right)
                bar = self.estimate_bar_center(rgb, left, x_left, x_right)
                if self.control and 15 <= self.control <= round((x_right - x_left) * 0.85):
                    break
                time.sleep(0.02)
            if not self.control or self.control <= 0:
                control_raw = self.load_control_fallback()
                self.control = round((roblox_w / 800) * ((320 * control_raw) + 97))

        self.status.set("Task", "Playing Bar Minigame")

        if self.fixed_pulse_ms is not None:
            self._play_minigame_fixed_pulse(minigame)
        else:
            self._play_minigame_formula(minigame, roblox_w)

        self.status.set("Task", "Minigame ended, restarting")

    def _play_minigame_fixed_pulse(self, minigame):
        x_left, y_left, x_right, y_right = minigame
        pulse_s = self.fixed_pulse_ms / 1000.0
        hold_right = None

        while True:
            if self.stop_event.is_set():
                self.steer.set(False)
                return

            cycle_start = time.time()

            rgb, left, top = self.searcher.grab(x_left, y_left, x_right, y_right)
            fish_pos = self.get_fish_center(rgb, left)
            if fish_pos is None:
                break

            bar = self.estimate_bar_center(rgb, left, x_left, x_right)
            capture_ms = (time.time() - cycle_start) * 1000
            if bar is None:
                time.sleep(pulse_s)
                continue

            rng = fish_pos - bar
            if abs(rng) <= self.deadzone_px or hold_right is None:
                if hold_right is None:
                    hold_right = rng >= 0
            else:
                hold_right = rng >= 0
            self.steer.set(hold_right)
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
            fish_pos = self.get_fish_center(rgb, left)
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
                    fish_pos = self.find_fish_pos(minigame)
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
                    fish_pos = self.get_fish_center(rgb, left)
                    if fish_pos is None:
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

# Sentinel so we can tell "user didn't pass this flag" apart from "user
# explicitly passed the same value as the hardcoded default" - needed to
# correctly layer CLI args over Settings.ini values below.
_UNSET = object()


def load_macro_ini_settings(path, profile_name=None):
    """
    Read settings from Settings.ini and optionally merge profile_settings.json.
    """
    cfg = configparser.ConfigParser(strict=False)
    ini_dict = {}
    if Path(path).exists():
        cfg.read(path)
        if cfg.has_section("Macro"):
            ini_dict = {k: v for k, v in cfg.items("Macro")}

    selected_profile = profile_name or ini_dict.get("profile")
    if selected_profile:
        prof_file = Path("Profiles") / selected_profile / "profile_settings.json"
        if prof_file.exists():
            try:
                with open(prof_file, "r") as f:
                    prof_data = json.load(f)
                if "Macro" in prof_data and isinstance(prof_data["Macro"], dict):
                    for k, v in prof_data["Macro"].items():
                        ini_dict[k] = str(v)
            except Exception as e:
                print(f"Warning: Could not load profile {selected_profile}: {e}")

    return ini_dict


def resolve_setting(cli_value, ini_settings, ini_key, caster, hard_default, flag_name):
    """
    Priority: explicit CLI flag > [Macro] value in Settings.ini / profile JSON > hardcoded default.
    """
    if cli_value is not _UNSET:
        raw, source = cli_value, flag_name
    else:
        raw = ini_settings.get(ini_key)
        source = f"Settings/Profile [Macro] {ini_key}"
        if raw is None or raw.strip() == "":
            return hard_default
    try:
        return caster(raw.strip())
    except (ValueError, TypeError):
        sys.exit(f"{source} = '{raw}' is invalid.")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Fisch Cream's Macro - Python/Linux port")
    parser.add_argument("--profile", default=_UNSET,
                         help="Name of profile folder in Profiles/ to load settings from.")
    parser.add_argument("--window-title", default=_UNSET,
                         help="Optional: substring of the game window title to match.")
    parser.add_argument("--settings", default="Settings.ini",
                         help="Path to Settings.ini.")
    parser.add_argument("--exit-key", default=_UNSET,
                         help="Key that stops the macro (default: f8).")
    parser.add_argument("--shake-min-pixels", default=_UNSET,
                         help="Minimum matched white pixels required before a shake-button detection counts (default: 12).")
    parser.add_argument("--hold-scale", default=_UNSET,
                         help="Multiplier applied to every minigame hold duration (default: 1.0).")
    parser.add_argument("--pulse-ms", default=_UNSET,
                         help="Bypass variable formula and hold steering for this many ms.")
    parser.add_argument("--steering", choices=["mouse", "space"], default=_UNSET,
                         help="Input used to steer minigame bar (default: mouse).")
    parser.add_argument("--deadzone-px", default=_UNSET,
                         help="Fixed-pulse mode minimum offset in pixels (default: 6).")
    parser.add_argument("--color-ui", default=_UNSET,
                         help="Custom COLOR_UI targets (e.g. '0xffdcac:10, 0x3d381b:10').")
    parser.add_argument("--color-fish", default=_UNSET,
                         help="Custom COLOR_FISH targets (e.g. '0x434b5b:3, 0x4a4a5c:4').")
    parser.add_argument("--color-bar", default=_UNSET,
                         help="Custom COLOR_BAR targets (e.g. '0x848587:4, 0x787773:4').")
    args = parser.parse_args()

    profile_name = args.profile if args.profile is not _UNSET else None
    ini_settings = load_macro_ini_settings(args.settings, profile_name=profile_name)

    window_title = resolve_setting(args.window_title, ini_settings, "window-title", str, None, "--window-title")
    if window_title == "":
        window_title = None
    exit_key_name = resolve_setting(args.exit_key, ini_settings, "exit-key", str, "f8", "--exit-key")
    shake_min_pixels = resolve_setting(args.shake_min_pixels, ini_settings, "shake-min-pixels", int, 12, "--shake-min-pixels")
    hold_scale = resolve_setting(args.hold_scale, ini_settings, "hold-scale", float, 1.0, "--hold-scale")
    pulse_ms_raw = resolve_setting(args.pulse_ms, ini_settings, "pulse-ms", float, None, "--pulse-ms")
    steering = resolve_setting(args.steering, ini_settings, "steering", str, "mouse", "--steering")
    if steering not in ("mouse", "space"):
        sys.exit(f"Settings.ini [Macro] steering = '{steering}' must be 'mouse' or 'space'.")
    deadzone_px = resolve_setting(args.deadzone_px, ini_settings, "deadzone-px", int, 6, "--deadzone-px")

    color_ui_raw = resolve_setting(args.color_ui, ini_settings, "color-ui", str, None, "--color-ui")
    color_fish_raw = resolve_setting(args.color_fish, ini_settings, "color-fish", str, None, "--color-fish")
    color_bar_raw = resolve_setting(args.color_bar, ini_settings, "color-bar", str, None, "--color-bar")

    color_ui = parse_color_dict(color_ui_raw, DEFAULT_COLOR_UI) if color_ui_raw else dict(DEFAULT_COLOR_UI)
    color_fish = parse_color_dict(color_fish_raw, DEFAULT_COLOR_FISH) if color_fish_raw else dict(DEFAULT_COLOR_FISH)
    color_bar = parse_color_dict(color_bar_raw, DEFAULT_COLOR_BAR) if color_bar_raw else dict(DEFAULT_COLOR_BAR)

    exit_key = resolve_exit_key(exit_key_name)

    print(
        "This is Fisch Cream's Macro (Python/Linux port of the FREE VERSION).\n"
        "Original by Cweamya: https://github.com/Cweamy/Fisch-Cream-s-Macro\n"
        "Make sure Roblox is fullscreen with Camera Mode enabled, and your\n"
        "display scale is set to 100% before starting.\n"
        f"Press '{exit_key_name.upper()}' at any time to stop (this works system-wide,\n"
        "not just while the game is focused).\n"
    )

    macro = FischMacro(window_title=window_title, settings_path=args.settings, exit_key=exit_key,
                        shake_min_pixels=shake_min_pixels, hold_scale=hold_scale,
                        fixed_pulse_ms=pulse_ms_raw, steering_input=steering, deadzone_px=deadzone_px,
                        color_ui=color_ui, color_fish=color_fish, color_bar=color_bar)
    try:
        macro.run()
    except KeyboardInterrupt:
        print("\nInterrupted.")


if __name__ == "__main__":
    main()
