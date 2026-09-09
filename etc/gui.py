#!/usr/bin/env python3
import os
import sys
import re
import subprocess
import json
import shutil
import threading
import subprocess
import configparser
from pathlib import Path

try:
    import mss
    from PIL import Image, ImageTk
    import customtkinter as ctk
    import tkinter as tk
except ImportError as e:
    sys.exit(f"Missing dependency: {e}. Install dependencies with `pip install customtkinter mss pillow`")

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("green")

SETTINGS_FILE = "Settings.ini"
MACRO_SCRIPT = "fisch_macro.py"
PROFILES_DIR = Path("Profiles")
VERSION_CURRENT = "1.12.2"
VERSION_URL = "https://raw.githubusercontent.com/Patryk55o/FischMacroLinux/refs/heads/main/etc/macroversion.txt"

DEFAULT_COLOR_UI_STR = "0xffdcac:10, 0x3d381b:10, 0xfffde4:10"
DEFAULT_COLOR_FISH_STR = "0x434b5b:3, 0x4a4a5c:4, 0x47515d:4"
DEFAULT_COLOR_BAR_STR = "0x848587:4, 0x787773:4, 0x7a7873:4"

# Regex to strip ANSI escape sequences from macro output
ANSI_ESCAPE_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


def ensure_profiles_dir():
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    profs = list_profiles()
    if not profs:
        create_profile("Default")
        profs = ["Default"]
    return profs


def list_profiles():
    if not PROFILES_DIR.exists():
        return []
    profs = [d.name for d in PROFILES_DIR.iterdir() if d.is_dir()]
    profs.sort()
    return profs


def get_profile_json_path(profile_name):
    return PROFILES_DIR / profile_name / "profile_settings.json"


def load_profile_data(profile_name):
    path = get_profile_json_path(profile_name)
    if not path.exists():
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading profile {profile_name}: {e}")
        return {}


def save_profile_data(profile_name, macro_dict, fisch_dict):
    prof_folder = PROFILES_DIR / profile_name
    prof_folder.mkdir(parents=True, exist_ok=True)
    json_path = prof_folder / "profile_settings.json"

    data = {
        "profile_name": profile_name,
        "Macro": macro_dict,
        "Fisch": fisch_dict
    }
    with open(json_path, "w") as f:
        json.dump(data, f, indent=2)


def create_profile(profile_name, macro_dict=None, fisch_dict=None):
    if not macro_dict:
        macro_dict = {
            "window-title": "",
            "exit-key": "f8",
            "steering": "mouse",
            "pulse-ms": "",
            "hold-scale": "1.0",
            "shake-min-pixels": "12",
            "deadzone-px": "6",
            "color-ui": DEFAULT_COLOR_UI_STR,
            "color-fish": DEFAULT_COLOR_FISH_STR,
            "color-bar": DEFAULT_COLOR_BAR_STR,
        }
    if not fisch_dict:
        fisch_dict = {
            "control": "0"
        }
    save_profile_data(profile_name, macro_dict, fisch_dict)


def delete_profile_dir(profile_name):
    folder = PROFILES_DIR / profile_name
    if folder.exists() and folder.is_dir():
        shutil.rmtree(folder)


def parse_hex_colors(color_str):
    """Extract hex color strings (e.g. #FFDCAC) for UI color preview badges."""
    badges = []
    if not color_str:
        return badges
    tokens = [t.strip() for t in color_str.split(",") if t.strip()]
    for token in tokens:
        raw_hex = token.split(":")[0].strip().replace("#", "").replace("0x", "")
        if len(raw_hex) == 6:
            try:
                int(raw_hex, 16)
                badges.append(f"#{raw_hex.upper()}")
            except ValueError:
                pass
    return badges


# ============================================================================
# Screen Region Selection & Eyedropper Dialogs
# ============================================================================
class RegionSelectorOverlay(tk.Toplevel):
    """Full-screen overlay displaying a pre-captured desktop screenshot for region selection."""

    def __init__(self, parent, full_img, callback, on_cancel=None):
        super().__init__(parent)
        self.full_img = full_img
        self.callback = callback
        self.on_cancel = on_cancel

        self.overrideredirect(True)          # no window decorations/titlebar
        self.attributes("-topmost", True)
        self.configure(cursor="crosshair")

        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{sw}x{sh}+0+0")

        self.tk_full_img = ImageTk.PhotoImage(self.full_img)

        self.canvas = tk.Canvas(self, cursor="crosshair", highlightthickness=0,
                                width=sw, height=sh)
        self.canvas.pack(fill="both", expand=True)

        # Draw frozen desktop image
        self.canvas.create_image(0, 0, anchor="nw", image=self.tk_full_img)

        # Instruction banner at top
        self.canvas.create_rectangle(0, 0, sw, 46, fill="#111111", outline="")
        self.canvas.create_text(
            sw // 2, 23,
            text="Click & Drag to select screen region  •  ESC to cancel",
            fill="#00FF00", font=("Helvetica", 15, "bold")
        )

        self.start_x = None
        self.start_y = None
        self.rect_id = None

        self.bind("<ButtonPress-1>", self.on_press)
        self.bind("<B1-Motion>", self.on_drag)
        self.bind("<ButtonRelease-1>", self.on_release)
        self.bind("<Escape>", lambda e: self.cancel())
        self.focus_force()
        self.lift()

    def cancel(self):
        self.destroy()
        if self.on_cancel:
            self.on_cancel()

    def on_press(self, event):
        self.start_x = event.x
        self.start_y = event.y
        if self.rect_id:
            self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(
            self.start_x, self.start_y, self.start_x, self.start_y,
            outline="#00FF00", width=3, dash=(6, 3)
        )

    def on_drag(self, event):
        if self.rect_id:
            self.canvas.coords(self.rect_id, self.start_x, self.start_y, event.x, event.y)

    def on_release(self, event):
        end_x, end_y = event.x, event.y
        x1, x2 = min(self.start_x, end_x), max(self.start_x, end_x)
        y1, y2 = min(self.start_y, end_y), max(self.start_y, end_y)

        # Hide immediately so the overlay visually disappears before the next dialog opens
        self.withdraw()
        self.update()

        if (x2 - x1) > 5 and (y2 - y1) > 5:
            cropped = self.full_img.crop((x1, y1, x2, y2))
            self.destroy()
            self.callback(cropped)
        else:
            self.destroy()
            if self.on_cancel:
                self.on_cancel()


class ColorPickerDialog(ctk.CTkToplevel):
    """Modal dialog for pixel color picking with gradient two-point sampler."""

    STRIP_PX = 4   # edge strip thickness in original pixels

    def __init__(self, parent, cropped_image):
        super().__init__(parent)
        self.gui = parent
        self.cropped_image = cropped_image

        self.title("Eyedropper – Pick / Gradient Color")
        self.geometry("820x720")
        self.attributes("-topmost", True)
        self.resizable(True, True)

        # Scale up small captures so every pixel is easy to click
        orig_w, orig_h = cropped_image.size
        scale = 1
        if orig_w < 350 or orig_h < 350:
            scale = max(2, min(8, 400 // max(orig_w, orig_h)))
        self.scale = scale

        disp_w = orig_w * scale
        disp_h = orig_h * scale
        self.display_image = (
            cropped_image.resize((disp_w, disp_h), Image.Resampling.NEAREST)
            if scale > 1 else cropped_image
        )
        self.tk_image = ImageTk.PhotoImage(self.display_image)

        # Single-pick state
        self.selected_hex = None
        self.selected_rgb = None
        self.highlight_rect = None

        # Gradient two-point state
        self.grad_p1 = None   # (orig_x, orig_y, r, g, b)
        self.grad_p2 = None
        self.grad_markers = []          # canvas item IDs
        self.pick_mode = "single"       # "single" | "grad_p1" | "grad_p2"
        self.var_grad_steps = None      # IntVar – set in build_ui

        self.build_ui(disp_w, disp_h)

    # ------------------------------------------------------------------ helpers
    def _luminance(self, r, g, b):
        return 0.299 * r + 0.587 * g + 0.114 * b

    def _txt_for(self, r, g, b):
        return "#000000" if self._luminance(r, g, b) > 140 else "#FFFFFF"

    def _dominant_color(self, pixels):
        """Median per-channel of a list of (r,g,b) tuples."""
        if not pixels:
            return (128, 128, 128)
        mid = len(pixels) // 2
        return (
            sorted(p[0] for p in pixels)[mid],
            sorted(p[1] for p in pixels)[mid],
            sorted(p[2] for p in pixels)[mid],
        )

    def _edge_pixels(self, edge):
        img, w, h = self.cropped_image, *self.cropped_image.size
        sp = min(self.STRIP_PX, max(1, (h if edge in ("top", "bottom") else w) // 4))
        if edge == "top":    rows = [(x, y) for y in range(sp) for x in range(w)]
        elif edge == "bottom": rows = [(x, y) for y in range(h - sp, h) for x in range(w)]
        elif edge == "left":  rows = [(x, y) for x in range(sp) for y in range(h)]
        else:                 rows = [(x, y) for x in range(w - sp, w) for y in range(h)]
        return [img.getpixel(xy)[:3] for xy in rows]

    def _interpolate(self, rgb1, rgb2, steps):
        """Return `steps` linearly interpolated (r,g,b) tuples from rgb1 to rgb2."""
        if steps < 2:
            return [rgb1]
        result = []
        for i in range(steps):
            t = i / (steps - 1)
            result.append((
                round(rgb1[0] + t * (rgb2[0] - rgb1[0])),
                round(rgb1[1] + t * (rgb2[1] - rgb1[1])),
                round(rgb1[2] + t * (rgb2[2] - rgb1[2])),
            ))
        return result

    # ------------------------------------------------- canvas markers
    def _clear_markers(self):
        for item in self.grad_markers:
            try: self.canvas.delete(item)
            except Exception: pass
        self.grad_markers.clear()

    def _draw_marker(self, orig_x, orig_y, label, color):
        cx = orig_x * self.scale + self.scale // 2
        cy = orig_y * self.scale + self.scale // 2
        r = max(6, self.scale * 2)
        circle = self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                                         outline=color, width=2, fill="")
        cross_h = self.canvas.create_line(cx - r, cy, cx + r, cy, fill=color, width=2)
        cross_v = self.canvas.create_line(cx, cy - r, cx, cy + r, fill=color, width=2)
        text    = self.canvas.create_text(cx + r + 4, cy, text=label,
                                          fill=color, anchor="w",
                                          font=("Helvetica", max(9, self.scale * 2), "bold"))
        self.grad_markers += [circle, cross_h, cross_v, text]

    def _redraw_grad_markers(self):
        self._clear_markers()
        if self.grad_p1:
            x, y, r, g, b = self.grad_p1
            self._draw_marker(x, y, "P1", "#00BFFF")
        if self.grad_p2:
            x, y, r, g, b = self.grad_p2
            self._draw_marker(x, y, "P2", "#FF6600")

    # ------------------------------------------------- apply helpers
    def _apply_single(self, r, g, b, source=""):
        self.selected_rgb = (r, g, b)
        self.selected_hex = f"0x{r:02x}{g:02x}{b:02x}"
        badge = f"#{r:02x}{g:02x}{b:02x}".upper()
        self.color_swatch.configure(
            text=f"{self.selected_hex} ({badge})",
            fg_color=badge, text_color=self._txt_for(r, g, b)
        )
        self.status_lbl.configure(
            text=f"RGB ({r},{g},{b})" + (f"  ← {source}" if source else "")
        )
        for btn in (self.btn_ui, self.btn_fish, self.btn_bar):
            btn.configure(state="normal")

    def _set_var(self, target_var_name, entries, tol, mode):
        """Write one or more hex:tol strings into the target variable."""
        var_map = {
            "color-ui":   self.gui.var_color_ui,
            "color-fish": self.gui.var_color_fish,
            "color-bar":  self.gui.var_color_bar,
        }
        var = var_map.get(target_var_name)
        if not var:
            return
        new_entries = ", ".join(f"0x{r:02x}{g:02x}{b:02x}:{tol}" for r, g, b in entries)
        if mode == "append":
            cur = var.get().strip()
            new_val = f"{cur}, {new_entries}" if cur else new_entries
        else:
            new_val = new_entries
        var.set(new_val)
        self.gui.update_color_badges()
        self.gui.save_settings(quiet=True)
        return new_val

    # ------------------------------------------------------------------ UI
    def build_ui(self, disp_w, disp_h):
        # ── Header ────────────────────────────────────────────────────────
        ctk.CTkLabel(
            self, text="Click a pixel  ·  sample an edge  ·  or use the gradient picker",
            font=ctk.CTkFont(size=13, weight="bold")
        ).pack(padx=15, pady=(10, 4))

        # ── Edge Strip Row ─────────────────────────────────────────────────
        edge_row = ctk.CTkFrame(self, fg_color="transparent")
        edge_row.pack(fill="x", padx=15, pady=(0, 4))
        ctk.CTkLabel(edge_row, text="Edge strip:", text_color="#AAAAAA",
                     font=ctk.CTkFont(size=12)).pack(side="left", padx=(0, 8))
        for lbl, edge, col in [
            ("⬆ Top", "top", "#2196F3"), ("⬇ Bottom", "bottom", "#9C27B0"),
            ("⬅ Left", "left", "#FF9800"), ("➡ Right", "right", "#4CAF50"),
        ]:
            ctk.CTkButton(edge_row, text=lbl, fg_color=col, height=28, width=90,
                          font=ctk.CTkFont(size=12),
                          command=lambda e=edge: self._sample_edge(e)
                          ).pack(side="left", padx=3)

        # ── Gradient Picker Section ────────────────────────────────────────
        grad_box = ctk.CTkFrame(self)
        grad_box.pack(fill="x", padx=15, pady=(2, 6))

        ctk.CTkLabel(grad_box, text="Gradient Picker", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#DDDDDD").grid(row=0, column=0, columnspan=6, padx=8, pady=(6,2), sticky="w")

        # Direction shortcut buttons (auto-set P1/P2 to image edges)
        ctk.CTkLabel(grad_box, text="Direction:", text_color="#AAAAAA",
                     font=ctk.CTkFont(size=11)).grid(row=1, column=0, padx=(8,4), pady=4, sticky="w")
        for col_idx, (lbl, func) in enumerate([
            ("↕ Top→Bot",  lambda: self._grad_direction("top_bottom")),
            ("↔ Left→Rig", lambda: self._grad_direction("left_right")),
            ("↕ Bot→Top",  lambda: self._grad_direction("bottom_top")),
            ("↔ Rig→Left", lambda: self._grad_direction("right_left")),
        ], start=1):
            ctk.CTkButton(grad_box, text=lbl, height=26, width=100,
                          fg_color="#37474F", hover_color="#546E7A",
                          font=ctk.CTkFont(size=11), command=func
                          ).grid(row=1, column=col_idx, padx=3, pady=4, sticky="ew")

        # P1 / P2 pick buttons
        self.btn_pick_p1 = ctk.CTkButton(
            grad_box, text="🎯 Pick P1", height=28, fg_color="#004080", hover_color="#0066CC",
            font=ctk.CTkFont(size=12), command=self._activate_pick_p1
        )
        self.btn_pick_p1.grid(row=2, column=0, columnspan=1, padx=(8,3), pady=4, sticky="ew")

        self.grad_p1_lbl = ctk.CTkLabel(grad_box, text="P1: —", fg_color="#1a1a2e",
                                         corner_radius=6, width=160, height=26,
                                         font=ctk.CTkFont(size=11))
        self.grad_p1_lbl.grid(row=2, column=1, columnspan=2, padx=3, pady=4, sticky="ew")

        self.btn_pick_p2 = ctk.CTkButton(
            grad_box, text="🎯 Pick P2", height=28, fg_color="#5D1A00", hover_color="#CC4400",
            font=ctk.CTkFont(size=12), command=self._activate_pick_p2
        )
        self.btn_pick_p2.grid(row=2, column=3, columnspan=1, padx=(12,3), pady=4, sticky="ew")

        self.grad_p2_lbl = ctk.CTkLabel(grad_box, text="P2: —", fg_color="#1a1a2e",
                                         corner_radius=6, width=160, height=26,
                                         font=ctk.CTkFont(size=11))
        self.grad_p2_lbl.grid(row=2, column=4, columnspan=2, padx=3, pady=4, sticky="ew")

        # Steps + apply row
        steps_row = ctk.CTkFrame(grad_box, fg_color="transparent")
        steps_row.grid(row=3, column=0, columnspan=6, padx=6, pady=(2,6), sticky="ew")
        steps_row.columnconfigure(3, weight=1)
        steps_row.columnconfigure(4, weight=1)
        steps_row.columnconfigure(5, weight=1)

        ctk.CTkLabel(steps_row, text="Steps:", text_color="#AAAAAA",
                     font=ctk.CTkFont(size=12)).grid(row=0, column=0, padx=(2,4), sticky="w")
        self.var_grad_steps = ctk.IntVar(value=5)
        ctk.CTkSlider(steps_row, from_=2, to=12, number_of_steps=10,
                      variable=self.var_grad_steps, width=120
                      ).grid(row=0, column=1, padx=4, sticky="w")
        self.steps_lbl = ctk.CTkLabel(steps_row, text="5", width=24, font=ctk.CTkFont(size=12))
        self.steps_lbl.grid(row=0, column=2, padx=(0, 12), sticky="w")
        self.var_grad_steps.trace_add("write", lambda *_: self.steps_lbl.configure(
            text=str(self.var_grad_steps.get())))

        tol_lbl = ctk.CTkLabel(steps_row, text="Tol:", text_color="#AAAAAA",
                                font=ctk.CTkFont(size=12))
        tol_lbl.grid(row=0, column=3, padx=(0,4), sticky="e")
        self.var_grad_tol = ctk.IntVar(value=8)
        ctk.CTkSlider(steps_row, from_=1, to=30, number_of_steps=29,
                      variable=self.var_grad_tol, width=80
                      ).grid(row=0, column=4, padx=2, sticky="ew")
        self.tol_lbl = ctk.CTkLabel(steps_row, text="8", width=24, font=ctk.CTkFont(size=12))
        self.tol_lbl.grid(row=0, column=5, padx=(0,4), sticky="w")
        self.var_grad_tol.trace_add("write", lambda *_: self.tol_lbl.configure(
            text=str(self.var_grad_tol.get())))

        # Gradient apply buttons
        apply_row = ctk.CTkFrame(grad_box, fg_color="transparent")
        apply_row.grid(row=4, column=0, columnspan=6, padx=6, pady=(0,8), sticky="ew")
        apply_row.columnconfigure((0,1,2), weight=1)

        ctk.CTkButton(apply_row, text="Gradient → COLOR_UI",
                      fg_color="#ffdcac", text_color="#000000", hover_color="#e6c495", height=30,
                      command=lambda: self._apply_gradient("color-ui")
                      ).grid(row=0, column=0, padx=4, sticky="ew")
        ctk.CTkButton(apply_row, text="Gradient → COLOR_FISH",
                      fg_color="#434b5b", text_color="#FFFFFF", hover_color="#373e4b", height=30,
                      command=lambda: self._apply_gradient("color-fish")
                      ).grid(row=0, column=1, padx=4, sticky="ew")
        ctk.CTkButton(apply_row, text="Gradient → COLOR_BAR",
                      fg_color="#848587", text_color="#FFFFFF", hover_color="#6e6f71", height=30,
                      command=lambda: self._apply_gradient("color-bar")
                      ).grid(row=0, column=2, padx=4, sticky="ew")

        # ── Canvas ────────────────────────────────────────────────────────
        canvas_frame = ctk.CTkScrollableFrame(self, width=disp_w + 20, height=min(disp_h + 20, 240))
        canvas_frame.pack(fill="both", expand=True, padx=15, pady=4)

        self.canvas = tk.Canvas(canvas_frame, width=disp_w, height=disp_h,
                                cursor="crosshair", bg="#111111", highlightthickness=0)
        self.canvas.pack(anchor="center", padx=5, pady=5)
        self.canvas.create_image(0, 0, anchor="nw", image=self.tk_image)
        self.canvas.bind("<Button-1>", self.on_canvas_click)

        # ── Single Color Row ──────────────────────────────────────────────
        self.info_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.info_frame.pack(fill="x", padx=15, pady=(4, 2))

        self.color_swatch = ctk.CTkLabel(
            self.info_frame, text="No color selected", fg_color="#333333", text_color="#AAAAAA",
            width=240, height=30, corner_radius=8, font=ctk.CTkFont(weight="bold")
        )
        self.color_swatch.pack(side="left", padx=(0, 10))

        self.status_lbl = ctk.CTkLabel(self.info_frame, text="Click image or sample edge/gradient",
                                        text_color="#AAAAAA")
        self.status_lbl.pack(side="left", padx=5)

        # ── Single-pick assign buttons ─────────────────────────────────────
        btn_grid = ctk.CTkFrame(self)
        btn_grid.pack(fill="x", padx=15, pady=(2, 4))
        btn_grid.columnconfigure((0, 1, 2), weight=1)

        self.btn_ui = ctk.CTkButton(btn_grid, text="➜ Set COLOR_UI", state="disabled",
                                     fg_color="#ffdcac", text_color="#000000", hover_color="#e6c495",
                                     command=lambda: self.assign_color("color-ui", 10))
        self.btn_ui.grid(row=0, column=0, padx=5, pady=4, sticky="ew")

        self.btn_fish = ctk.CTkButton(btn_grid, text="➜ Set COLOR_FISH", state="disabled",
                                       fg_color="#434b5b", text_color="#FFFFFF", hover_color="#373e4b",
                                       command=lambda: self.assign_color("color-fish", 4))
        self.btn_fish.grid(row=0, column=1, padx=5, pady=4, sticky="ew")

        self.btn_bar = ctk.CTkButton(btn_grid, text="➜ Set COLOR_BAR", state="disabled",
                                      fg_color="#848587", text_color="#FFFFFF", hover_color="#6e6f71",
                                      command=lambda: self.assign_color("color-bar", 4))
        self.btn_bar.grid(row=0, column=2, padx=5, pady=4, sticky="ew")

        opt_frame = ctk.CTkFrame(btn_grid, fg_color="transparent")
        opt_frame.grid(row=1, column=0, columnspan=3, padx=5, pady=(0, 4), sticky="w")
        self.var_mode = ctk.StringVar(value="append")
        ctk.CTkRadioButton(opt_frame, text="Append to list", variable=self.var_mode, value="append").pack(side="left", padx=(0, 15))
        ctk.CTkRadioButton(opt_frame, text="Replace list",   variable=self.var_mode, value="replace").pack(side="left")

    # ------------------------------------------------------------------ canvas events
    def on_canvas_click(self, event):
        if self.highlight_rect:
            self.canvas.delete(self.highlight_rect)
            self.highlight_rect = None

        orig_x = int(event.x / self.scale)
        orig_y = int(event.y / self.scale)
        w, h = self.cropped_image.size
        if not (0 <= orig_x < w and 0 <= orig_y < h):
            return

        r, g, b = self.cropped_image.getpixel((orig_x, orig_y))[:3]

        if self.pick_mode == "grad_p1":
            self.grad_p1 = (orig_x, orig_y, r, g, b)
            badge = f"#{r:02x}{g:02x}{b:02x}".upper()
            self.grad_p1_lbl.configure(text=f"P1: ({orig_x},{orig_y})  {badge}",
                                        fg_color=badge, text_color=self._txt_for(r, g, b))
            self.btn_pick_p1.configure(fg_color="#004080")
            self.pick_mode = "single"
            self._redraw_grad_markers()

        elif self.pick_mode == "grad_p2":
            self.grad_p2 = (orig_x, orig_y, r, g, b)
            badge = f"#{r:02x}{g:02x}{b:02x}".upper()
            self.grad_p2_lbl.configure(text=f"P2: ({orig_x},{orig_y})  {badge}",
                                        fg_color=badge, text_color=self._txt_for(r, g, b))
            self.btn_pick_p2.configure(fg_color="#5D1A00")
            self.pick_mode = "single"
            self._redraw_grad_markers()

        else:
            self._apply_single(r, g, b, source=f"pixel ({orig_x},{orig_y})")

    # ------------------------------------------------------------------ gradient actions
    def _activate_pick_p1(self):
        self.pick_mode = "grad_p1"
        self.btn_pick_p1.configure(fg_color="#0099FF")
        self.btn_pick_p2.configure(fg_color="#5D1A00")
        self.status_lbl.configure(text="Click image to set Point 1 ...")

    def _activate_pick_p2(self):
        self.pick_mode = "grad_p2"
        self.btn_pick_p2.configure(fg_color="#FF6600")
        self.btn_pick_p1.configure(fg_color="#004080")
        self.status_lbl.configure(text="Click image to set Point 2 ...")

    def _grad_direction(self, direction):
        """Auto-set P1 and P2 to opposite edges based on direction shortcut."""
        img = self.cropped_image
        w, h = img.size
        mapping = {
            "top_bottom":  ((w // 2, 0),           (w // 2, h - 1)),
            "bottom_top":  ((w // 2, h - 1),        (w // 2, 0)),
            "left_right":  ((0,       h // 2),       (w - 1,  h // 2)),
            "right_left":  ((w - 1,   h // 2),       (0,      h // 2)),
        }
        (x1, y1), (x2, y2) = mapping[direction]
        r1, g1, b1 = img.getpixel((x1, y1))[:3]
        r2, g2, b2 = img.getpixel((x2, y2))[:3]
        self.grad_p1 = (x1, y1, r1, g1, b1)
        self.grad_p2 = (x2, y2, r2, g2, b2)

        b1_hex = f"#{r1:02x}{g1:02x}{b1:02x}".upper()
        b2_hex = f"#{r2:02x}{g2:02x}{b2:02x}".upper()
        self.grad_p1_lbl.configure(text=f"P1: ({x1},{y1})  {b1_hex}",
                                    fg_color=b1_hex, text_color=self._txt_for(r1, g1, b1))
        self.grad_p2_lbl.configure(text=f"P2: ({x2},{y2})  {b2_hex}",
                                    fg_color=b2_hex, text_color=self._txt_for(r2, g2, b2))
        self._redraw_grad_markers()
        self.status_lbl.configure(text=f"Auto-set gradient: {direction.replace('_',' ')}")

    def _apply_gradient(self, target_var_name):
        if not self.grad_p1 or not self.grad_p2:
            self.status_lbl.configure(text="⚠ Set both P1 and P2 first!")
            return
        steps = self.var_grad_steps.get()
        tol   = self.var_grad_tol.get()
        rgb1 = self.grad_p1[2:5]
        rgb2 = self.grad_p2[2:5]
        colors = self._interpolate(rgb1, rgb2, steps)
        self._set_var(target_var_name, colors, tol, self.var_mode.get())
        self.status_lbl.configure(
            text=f"✓ Added {steps}-step gradient → {target_var_name}  (tol :{tol})"
        )

    # ------------------------------------------------------------------ edge strip
    def _sample_edge(self, edge):
        pixels = self._edge_pixels(edge)
        r, g, b = self._dominant_color(pixels)
        # show highlight
        if self.highlight_rect:
            self.canvas.delete(self.highlight_rect)
        dw, dh = self.display_image.size
        sp = self.STRIP_PX * self.scale
        coords = {
            "top":    (0, 0, dw, sp),
            "bottom": (0, dh - sp, dw, dh),
            "left":   (0, 0, sp, dh),
            "right":  (dw - sp, 0, dw, dh),
        }[edge]
        self.highlight_rect = self.canvas.create_rectangle(
            *coords, outline="#00FF00", fill="#00FF00", stipple="gray50"
        )
        self._apply_single(r, g, b, source=f"{edge} edge ({self.STRIP_PX}px median)")

    # ------------------------------------------------------------------ single assign
    def assign_color(self, target_var_name, default_tol):
        if not self.selected_hex:
            return
        r, g, b = self.selected_rgb
        result = self._set_var(target_var_name, [(r, g, b)], default_tol, self.var_mode.get())
        self.status_lbl.configure(text=f"✓ Added {self.selected_hex}:{default_tol} → {target_var_name}")





# ============================================================================
# Main GUI Window
# ============================================================================
class MacroGUI(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("FischTux - Macro Control Panel")
        self.geometry("960x640")
        self.minsize(900, 580)

        # Load Window Icon
        self.app_icon = None
        try:
            self.app_icon = ImageTk.PhotoImage(Image.open("icon.ico"))
            self.iconphoto(False, self.app_icon)
        except Exception as e:
            print(f"Notice: Could not load image icon ({e})")

        # State variables
        self.process = None
        self.is_running = False
        self.active_profile = "Default"

        # Ensure Profiles directory exists and contains at least Default profile
        ensure_profiles_dir()

        # Grid Layout Configuration
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        # Variables for settings
        self.init_variables()

        # Build UI Components
        self.build_sidebar()
        self.build_main_tabs()

        # Setup F9 Hotkey Listener
        self.setup_f9_hotkey()
        self.bind("<F9>", lambda e: self.start_region_color_picker())

        # Load settings
        self.load_settings()
        self.after(1000, self.check_for_update)

    def setup_f9_hotkey(self):
        try:
            from pynput import keyboard
            def on_press(key):
                if key == keyboard.Key.f9:
                    self.after(0, self.start_region_color_picker)

            listener = keyboard.Listener(on_press=on_press)
            listener.daemon = True
            listener.start()
        except Exception as e:
            print(f"Notice: pynput hotkey listener notice ({e})")

    def start_region_color_picker(self):
        # Iconify (minimise) rather than withdraw so the window stays a valid Toplevel parent
        self.iconify()
        # Wait 350ms for the WM to hide the GUI before grabbing the screen
        self.after(350, self._capture_and_launch_overlay)

    def _capture_and_launch_overlay(self):
        """Grab the desktop screenshot FIRST, then open the overlay with the image."""
        try:
            with mss.mss() as sct:
                mon = sct.monitors[1]
                raw = sct.grab(mon)
                full_img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        except Exception as e:
            self.deiconify()
            self.write_log(f"Screen capture failed: {e}")
            return
        RegionSelectorOverlay(self, full_img, self.on_region_selected, on_cancel=self.on_overlay_cancelled)

    def on_overlay_cancelled(self):
        self.deiconify()

    def on_region_selected(self, cropped_image):
        self.deiconify()
        ColorPickerDialog(self, cropped_image)

    def init_variables(self):
        # Profile variable
        self.var_profile = ctk.StringVar(value="Default")

        # [Macro] Section
        self.var_window_title = ctk.StringVar()
        self.var_exit_key = ctk.StringVar(value="f8")
        self.var_steering = ctk.StringVar(value="mouse")
        self.var_pulse_ms = ctk.StringVar()
        self.var_hold_scale = ctk.StringVar(value="1.0")
        self.var_shake_min = ctk.StringVar(value="12")
        self.var_deadzone = ctk.StringVar(value="6")

        # Colors
        self.var_color_ui = ctk.StringVar(value=DEFAULT_COLOR_UI_STR)
        self.var_color_fish = ctk.StringVar(value=DEFAULT_COLOR_FISH_STR)
        self.var_color_bar = ctk.StringVar(value=DEFAULT_COLOR_BAR_STR)

        # Profile Creation
        self.var_new_profile_name = ctk.StringVar()

        # [Fisch] Section
        self.var_control = ctk.StringVar(value="0")

        # [Discord] Section
        self.var_webhook_url = ctk.StringVar()

    def build_sidebar(self):
        self.sidebar_frame = ctk.CTkFrame(self, width=240, corner_radius=0)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(7, weight=1)

        # Logo / Branding
        self.logo_label = ctk.CTkLabel(
            self.sidebar_frame,
            text="Fisch Macro",
            font=ctk.CTkFont(size=24, weight="bold")
        )
        self.logo_label.grid(row=0, column=0, padx=20, pady=(25, 5))

        self.subtitle_label = ctk.CTkLabel(
            self.sidebar_frame,
            text="Linux Control Panel",
            font=ctk.CTkFont(size=13),
            text_color="#888888"
        )
        self.subtitle_label.grid(row=1, column=0, padx=20, pady=(0, 15))

        # Active Profile Selector in Sidebar
        ctk.CTkLabel(
            self.sidebar_frame,
            text="Active Profile:",
            font=ctk.CTkFont(size=12, weight="bold")
        ).grid(row=2, column=0, padx=20, pady=(5, 2), sticky="w")

        self.sidebar_profile_menu = ctk.CTkOptionMenu(
            self.sidebar_frame,
            variable=self.var_profile,
            values=list_profiles(),
            command=self.on_sidebar_profile_change
        )
        self.sidebar_profile_menu.grid(row=3, column=0, padx=20, pady=(0, 15), sticky="ew")

        # Action Buttons
        self.start_btn = ctk.CTkButton(
            self.sidebar_frame, text="▶ Start Macro", fg_color="#28a745", hover_color="#218838",
            font=ctk.CTkFont(size=14, weight="bold"), command=self.start_macro, height=38
        )
        self.start_btn.grid(row=4, column=0, padx=20, pady=5, sticky="ew")

        self.stop_btn = ctk.CTkButton(
            self.sidebar_frame, text="■ Stop Macro", fg_color="#dc3545", hover_color="#c82333",
            font=ctk.CTkFont(size=14, weight="bold"), state="disabled", command=self.stop_macro, height=38
        )
        self.stop_btn.grid(row=5, column=0, padx=20, pady=5, sticky="ew")

        # Status & Log Console Header
        log_header_frame = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        log_header_frame.grid(row=6, column=0, padx=20, pady=(15, 0), sticky="ew")
        log_header_frame.columnconfigure(0, weight=1)

        self.status_title = ctk.CTkLabel(log_header_frame, text="Live Status:", font=ctk.CTkFont(weight="bold"))
        self.status_title.grid(row=0, column=0, sticky="w")

        self.clear_log_btn = ctk.CTkButton(
            log_header_frame, text="Clear", width=50, height=22, font=ctk.CTkFont(size=11),
            fg_color="#444444", hover_color="#555555", command=self.clear_log
        )
        self.clear_log_btn.grid(row=0, column=1, sticky="e")

        self.status_textbox = ctk.CTkTextbox(
            self.sidebar_frame, width=200, height=200, state="disabled", wrap="word", fg_color="#181818"
        )
        self.status_textbox.grid(row=7, column=0, padx=15, pady=10, sticky="nsew")

    def build_main_tabs(self):
        self.tabview = ctk.CTkTabview(self, corner_radius=15)
        self.tabview.grid(row=0, column=1, padx=20, pady=15, sticky="nsew")

        self.tab_general = self.tabview.add("General")
        self.tab_tuning = self.tabview.add("Tuning")
        self.tab_colors = self.tabview.add("Colors")
        self.tab_profiles = self.tabview.add("Profiles")
        self.tab_webhook = self.tabview.add("Webhook")

        self.build_tab_general()
        self.build_tab_tuning()
        self.build_tab_colors()
        self.build_tab_profiles()
        self.build_tab_webhook()

    # --- TAB 1: GENERAL ---
    def build_tab_general(self):
        frame = ctk.CTkScrollableFrame(self.tab_general, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=10, pady=10)
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(frame, text="General Macro Configuration", font=ctk.CTkFont(size=20, weight="bold")).grid(row=0, column=0, columnspan=2, padx=10, pady=(10, 20), sticky="w")

        self.add_field_row(frame, "Window Title Substring:", self.var_window_title, 1, placeholder="e.g. Roblox or Sober (leave empty to select via click)")
        self.add_field_row(frame, "Exit System Hotkey:", self.var_exit_key, 2, placeholder="f8 (or f9, esc, pause)")

        ctk.CTkLabel(frame, text="Steering Input Method:", font=ctk.CTkFont(weight="bold")).grid(row=3, column=0, padx=10, pady=10, sticky="w")
        self.steer_menu = ctk.CTkOptionMenu(frame, variable=self.var_steering, values=["mouse", "space"])
        self.steer_menu.grid(row=3, column=1, padx=10, pady=10, sticky="ew")

        self.add_field_row(frame, "Rod Control Stat (Fallback):", self.var_control, 4, placeholder="e.g. 0.08 (used if control stat is undetected)")

        self.save_btn_gen = ctk.CTkButton(frame, text="💾 Save Configuration", font=ctk.CTkFont(size=15, weight="bold"), command=self.save_settings, height=42)
        self.save_btn_gen.grid(row=5, column=0, columnspan=2, padx=10, pady=30, sticky="ew")

    # --- TAB 2: TUNING ---
    def build_tab_tuning(self):
        frame = ctk.CTkScrollableFrame(self.tab_tuning, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=10, pady=10)
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(frame, text="Tuning & Minigame Thresholds", font=ctk.CTkFont(size=20, weight="bold")).grid(row=0, column=0, columnspan=2, padx=10, pady=(10, 20), sticky="w")

        self.add_field_row(frame, "Fixed Pulse MS:", self.var_pulse_ms, 1, placeholder="e.g. 100 (leave empty to use formula)")
        self.add_field_row(frame, "Hold Scale Factor:", self.var_hold_scale, 2, placeholder="1.0 (multiplier for formula hold times)")
        self.add_field_row(frame, "Shake Min White Pixels:", self.var_shake_min, 3, placeholder="12 (minimum matching pixels for shake button)")
        self.add_field_row(frame, "Deadzone Pixels (Fixed Mode):", self.var_deadzone, 4, placeholder="6 (minimum offset before direction switch)")

        self.save_btn_tun = ctk.CTkButton(frame, text="💾 Save Configuration", font=ctk.CTkFont(size=15, weight="bold"), command=self.save_settings, height=42)
        self.save_btn_tun.grid(row=5, column=0, columnspan=2, padx=10, pady=30, sticky="ew")

    # --- TAB 3: COLORS ---
    def build_tab_colors(self):
        frame = ctk.CTkScrollableFrame(self.tab_colors, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=10, pady=10)
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(frame, text="Custom Minigame Color Targets", font=ctk.CTkFont(size=20, weight="bold")).grid(row=0, column=0, columnspan=2, padx=10, pady=(10, 5), sticky="w")
        ctk.CTkLabel(frame, text="Specify target colors in 0xRRGGBB:tolerance format (comma-separated). Press F9 to pick from screen.", text_color="#aaaaaa").grid(row=1, column=0, columnspan=2, padx=10, pady=(0, 15), sticky="w")

        # Color Eyedropper Button
        picker_btn = ctk.CTkButton(
            frame, text="📸 Screen Region Color Picker (F9)", font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#17a2b8", hover_color="#138496", command=self.start_region_color_picker, height=40
        )
        picker_btn.grid(row=2, column=0, columnspan=2, padx=10, pady=(0, 15), sticky="ew")

        # COLOR_UI
        self.add_field_row(frame, "COLOR_UI Targets:", self.var_color_ui, 3, placeholder=DEFAULT_COLOR_UI_STR)
        self.color_ui_badge_frame = ctk.CTkFrame(frame, fg_color="transparent")
        self.color_ui_badge_frame.grid(row=4, column=1, padx=10, pady=(0, 10), sticky="w")
        self.var_color_ui.trace_add("write", lambda *args: self.update_color_badges())

        # COLOR_FISH
        self.add_field_row(frame, "COLOR_FISH Targets:", self.var_color_fish, 5, placeholder=DEFAULT_COLOR_FISH_STR)
        self.color_fish_badge_frame = ctk.CTkFrame(frame, fg_color="transparent")
        self.color_fish_badge_frame.grid(row=6, column=1, padx=10, pady=(0, 10), sticky="w")
        self.var_color_fish.trace_add("write", lambda *args: self.update_color_badges())

        # COLOR_BAR
        self.add_field_row(frame, "COLOR_BAR Targets:", self.var_color_bar, 7, placeholder=DEFAULT_COLOR_BAR_STR)
        self.color_bar_badge_frame = ctk.CTkFrame(frame, fg_color="transparent")
        self.color_bar_badge_frame.grid(row=8, column=1, padx=10, pady=(0, 10), sticky="w")
        self.var_color_bar.trace_add("write", lambda *args: self.update_color_badges())

        # Action Buttons
        btn_frame = ctk.CTkFrame(frame, fg_color="transparent")
        btn_frame.grid(row=9, column=0, columnspan=2, padx=10, pady=25, sticky="ew")
        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)

        reset_btn = ctk.CTkButton(btn_frame, text="🔄 Reset Default Colors", fg_color="#6c757d", hover_color="#5a6268", command=self.reset_default_colors, height=40)
        reset_btn.grid(row=0, column=0, padx=(0, 10), sticky="ew")

        self.save_btn_col = ctk.CTkButton(btn_frame, text="💾 Save Configuration", font=ctk.CTkFont(size=15, weight="bold"), command=self.save_settings, height=40)
        self.save_btn_col.grid(row=0, column=1, padx=(10, 0), sticky="ew")

        self.update_color_badges()

    def update_color_badges(self):
        def render_badges(container, color_str):
            for child in container.winfo_children():
                child.destroy()
            hex_list = parse_hex_colors(color_str)
            for h in hex_list:
                lbl = ctk.CTkLabel(container, text=h, fg_color=h, text_color="#000000" if self.is_light_color(h) else "#FFFFFF",
                                   corner_radius=6, font=ctk.CTkFont(size=11, weight="bold"), width=75, height=22)
                lbl.pack(side="left", padx=4)

        render_badges(self.color_ui_badge_frame, self.var_color_ui.get())
        render_badges(self.color_fish_badge_frame, self.var_color_fish.get())
        render_badges(self.color_bar_badge_frame, self.var_color_bar.get())

    @staticmethod
    def is_light_color(hex_code):
        try:
            h = hex_code.lstrip("#")
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            luminance = (0.299 * r + 0.587 * g + 0.114 * b)
            return luminance > 140
        except Exception:
            return False

    def reset_default_colors(self):
        self.var_color_ui.set(DEFAULT_COLOR_UI_STR)
        self.var_color_fish.set(DEFAULT_COLOR_FISH_STR)
        self.var_color_bar.set(DEFAULT_COLOR_BAR_STR)
        self.update_color_badges()

    # --- TAB 4: PROFILES ---
    def build_tab_profiles(self):
        frame = ctk.CTkScrollableFrame(self.tab_profiles, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=10, pady=10)
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(frame, text="Profile Management", font=ctk.CTkFont(size=20, weight="bold")).grid(row=0, column=0, columnspan=2, padx=10, pady=(10, 5), sticky="w")
        ctk.CTkLabel(frame, text="Profiles store your macro & color settings under Profiles/<name>/profile_settings.json.", text_color="#aaaaaa").grid(row=1, column=0, columnspan=2, padx=10, pady=(0, 20), sticky="w")

        # Select Profile
        ctk.CTkLabel(frame, text="Active Profile:", font=ctk.CTkFont(weight="bold")).grid(row=2, column=0, padx=10, pady=10, sticky="w")
        self.tab_profile_menu = ctk.CTkOptionMenu(frame, variable=self.var_profile, values=list_profiles(), command=self.on_tab_profile_change)
        self.tab_profile_menu.grid(row=2, column=1, padx=10, pady=10, sticky="ew")

        # Create New Profile
        ctk.CTkLabel(frame, text="Create New Profile:", font=ctk.CTkFont(weight="bold")).grid(row=3, column=0, padx=10, pady=10, sticky="w")
        create_frame = ctk.CTkFrame(frame, fg_color="transparent")
        create_frame.grid(row=3, column=1, padx=10, pady=10, sticky="ew")
        create_frame.columnconfigure(0, weight=1)

        self.new_profile_entry = ctk.CTkEntry(create_frame, textvariable=self.var_new_profile_name, placeholder_text="e.g. ExaMPLE")
        self.new_profile_entry.grid(row=0, column=0, padx=(0, 10), sticky="ew")

        create_btn = ctk.CTkButton(create_frame, text="➕ Create", width=100, command=self.on_create_profile)
        create_btn.grid(row=0, column=1, sticky="e")

        # Info Box
        self.profile_info_label = ctk.CTkLabel(frame, text="", text_color="#888888", justify="left")
        self.profile_info_label.grid(row=4, column=0, columnspan=2, padx=10, pady=15, sticky="w")

        # Delete Profile Button
        self.delete_prof_btn = ctk.CTkButton(frame, text="🗑️ Delete Active Profile", fg_color="#dc3545", hover_color="#c82333", command=self.on_delete_profile, height=38)
        self.delete_prof_btn.grid(row=5, column=0, columnspan=2, padx=10, pady=15, sticky="ew")

        self.update_profile_ui()

    def update_profile_ui(self):
        profs = list_profiles()
        self.sidebar_profile_menu.configure(values=profs)
        self.tab_profile_menu.configure(values=profs)

        cur = self.var_profile.get()
        path = get_profile_json_path(cur)
        self.profile_info_label.configure(text=f"Current Profile: {cur}\nFile Path: {path.resolve()}")

        if len(profs) <= 1:
            self.delete_prof_btn.configure(state="disabled")
        else:
            self.delete_prof_btn.configure(state="normal")

    def on_sidebar_profile_change(self, choice):
        self.switch_to_profile(choice)

    def on_tab_profile_change(self, choice):
        self.switch_to_profile(choice)

    def switch_to_profile(self, profile_name):
        self.active_profile = profile_name
        self.var_profile.set(profile_name)

        # Load profile JSON
        pdata = load_profile_data(profile_name)
        macro_dict = pdata.get("Macro", {})
        fisch_dict = pdata.get("Fisch", {})

        def set_val(var, key, default="", source_dict=macro_dict):
            var.set(source_dict.get(key, default))

        set_val(self.var_window_title, "window-title")
        set_val(self.var_exit_key, "exit-key", "f8")
        set_val(self.var_steering, "steering", "mouse")
        set_val(self.var_pulse_ms, "pulse-ms", "")
        set_val(self.var_hold_scale, "hold-scale", "1.0")
        set_val(self.var_shake_min, "shake-min-pixels", "12")
        set_val(self.var_deadzone, "deadzone-px", "6")
        set_val(self.var_color_ui, "color-ui", DEFAULT_COLOR_UI_STR)
        set_val(self.var_color_fish, "color-fish", DEFAULT_COLOR_FISH_STR)
        set_val(self.var_color_bar, "color-bar", DEFAULT_COLOR_BAR_STR)
        set_val(self.var_control, "control", "0", source_dict=fisch_dict)

        self.update_profile_ui()
        self.update_color_badges()
        self.save_settings(quiet=True)

    def on_create_profile(self):
        name = self.var_new_profile_name.get().strip()
        if not name:
            return
        # Sanitize profile name
        name = re.sub(r'[^\w\-_]', '', name)
        if not name:
            return

        macro_dict = self.gather_macro_dict(profile_name=name)
        fisch_dict = self.gather_fisch_dict()
        create_profile(name, macro_dict, fisch_dict)

        self.var_new_profile_name.set("")
        self.switch_to_profile(name)

    def on_delete_profile(self):
        cur = self.var_profile.get()
        profs = list_profiles()
        if len(profs) <= 1:
            return
        delete_profile_dir(cur)
        remaining = list_profiles()
        next_prof = remaining[0] if remaining else "Default"
        if not remaining:
            create_profile("Default")
            next_prof = "Default"
        self.switch_to_profile(next_prof)

    # --- TAB 5: WEBHOOK ---
    def build_tab_webhook(self):
        frame = ctk.CTkScrollableFrame(self.tab_webhook, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=10, pady=10)
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(frame, text="Discord Webhook Settings", font=ctk.CTkFont(size=20, weight="bold")).grid(row=0, column=0, columnspan=2, padx=10, pady=(10, 5), sticky="w")
        ctk.CTkLabel(frame, text="Discord Webhook settings are global and shared across all profiles.", text_color="#aaaaaa").grid(row=1, column=0, columnspan=2, padx=10, pady=(0, 20), sticky="w")

        self.add_field_row(frame, "Webhook URL:", self.var_webhook_url, 2, placeholder="https://discord.com/api/webhooks/...")

        btn_frame = ctk.CTkFrame(frame, fg_color="transparent")
        btn_frame.grid(row=3, column=0, columnspan=2, padx=10, pady=25, sticky="ew")
        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)

        test_btn = ctk.CTkButton(btn_frame, text="🔔 Test Webhook", fg_color="#17a2b8", hover_color="#138496", command=self.test_webhook, height=40)
        test_btn.grid(row=0, column=0, padx=(0, 10), sticky="ew")

        self.save_btn_web = ctk.CTkButton(btn_frame, text="💾 Save Configuration", font=ctk.CTkFont(size=15, weight="bold"), command=self.save_settings, height=40)
        self.save_btn_web.grid(row=0, column=1, padx=(10, 0), sticky="ew")

    def test_webhook(self):
        url = self.var_webhook_url.get().strip()
        if not url:
            self.write_log("Webhook Test: No URL specified.")
            return
        try:
            import requests
            data = {"embeds": [{"title": "Fisch Macro Webhook Test", "description": "Webhook connection successfully verified!", "color": 0x2BFB42}]}
            resp = requests.post(url, json=data, timeout=5)
            if resp.status_code in (200, 204):
                self.write_log("Webhook Test: SUCCESS!")
            else:
                self.write_log(f"Webhook Test Failed: HTTP {resp.status_code}")
        except Exception as e:
            self.write_log(f"Webhook Test Error: {e}")

    # --- HELPERS & SETTINGS LOAD/SAVE ---
    def add_field_row(self, parent, label_text, variable, row, placeholder=""):
        ctk.CTkLabel(parent, text=label_text, font=ctk.CTkFont(weight="bold")).grid(row=row, column=0, padx=10, pady=8, sticky="w")
        entry = ctk.CTkEntry(parent, textvariable=variable, placeholder_text=placeholder)
        entry.grid(row=row, column=1, padx=10, pady=8, sticky="ew")

    def gather_macro_dict(self, profile_name=None):
        prof = profile_name or self.var_profile.get().strip() or "Default"
        return {
            "profile": prof,
            "window-title": self.var_window_title.get().strip(),
            "exit-key": self.var_exit_key.get().strip(),
            "steering": self.var_steering.get().strip(),
            "pulse-ms": self.var_pulse_ms.get().strip(),
            "hold-scale": self.var_hold_scale.get().strip(),
            "shake-min-pixels": self.var_shake_min.get().strip(),
            "deadzone-px": self.var_deadzone.get().strip(),
            "color-ui": self.var_color_ui.get().strip(),
            "color-fish": self.var_color_fish.get().strip(),
            "color-bar": self.var_color_bar.get().strip(),
        }

    def gather_fisch_dict(self):
        return {
            "control": self.var_control.get().strip()
        }

    def load_settings(self):
        cfg = configparser.ConfigParser(strict=False)
        if Path(SETTINGS_FILE).exists():
            cfg.read(SETTINGS_FILE)

        active_prof = "Default"
        if cfg.has_option("Macro", "profile"):
            active_prof = cfg.get("Macro", "profile").strip() or "Default"

        profs = list_profiles()
        if active_prof not in profs:
            if profs:
                active_prof = profs[0]
            else:
                create_profile("Default")
                active_prof = "Default"

        self.active_profile = active_prof
        self.var_profile.set(active_prof)

        # Load profile JSON
        pdata = load_profile_data(active_prof)
        macro_dict = pdata.get("Macro", {})
        fisch_dict = pdata.get("Fisch", {})

        # If profile JSON had values, use them; otherwise fallback to Settings.ini
        def load_val(var, section, key, default="", source_dict=macro_dict):
            val = source_dict.get(key)
            if val is None and cfg.has_option(section, key):
                val = cfg.get(section, key)
            var.set(val if val is not None else default)

        load_val(self.var_window_title, "Macro", "window-title")
        load_val(self.var_exit_key, "Macro", "exit-key", "f8")
        load_val(self.var_steering, "Macro", "steering", "mouse")
        load_val(self.var_pulse_ms, "Macro", "pulse-ms", "")
        load_val(self.var_hold_scale, "Macro", "hold-scale", "1.0")
        load_val(self.var_shake_min, "Macro", "shake-min-pixels", "12")
        load_val(self.var_deadzone, "Macro", "deadzone-px", "6")
        load_val(self.var_color_ui, "Macro", "color-ui", DEFAULT_COLOR_UI_STR)
        load_val(self.var_color_fish, "Macro", "color-fish", DEFAULT_COLOR_FISH_STR)
        load_val(self.var_color_bar, "Macro", "color-bar", DEFAULT_COLOR_BAR_STR)
        load_val(self.var_control, "Fisch", "control", "0", source_dict=fisch_dict)

        if cfg.has_option("Discord", "url"):
            self.var_webhook_url.set(cfg.get("Discord", "url"))

        self.update_profile_ui()
        self.update_color_badges()

    def save_settings(self, quiet=False):
        prof_name = self.var_profile.get().strip() or "Default"
        macro_dict = self.gather_macro_dict(profile_name=prof_name)
        fisch_dict = self.gather_fisch_dict()

        # 1. Save profile JSON (EXCLUDING Discord)
        save_profile_data(prof_name, macro_dict, fisch_dict)

        # 2. Sync to Settings.ini
        cfg = configparser.ConfigParser(strict=False)
        if Path(SETTINGS_FILE).exists():
            cfg.read(SETTINGS_FILE)

        if not cfg.has_section("Macro"): cfg.add_section("Macro")
        if not cfg.has_section("Fisch"): cfg.add_section("Fisch")
        if not cfg.has_section("Discord"): cfg.add_section("Discord")

        for k, v in macro_dict.items():
            cfg.set("Macro", k, str(v))
        for k, v in fisch_dict.items():
            cfg.set("Fisch", k, str(v))

        cfg.set("Discord", "url", self.var_webhook_url.get().strip())

        with open(SETTINGS_FILE, "w") as f:
            cfg.write(f)

        if not quiet:
            for btn in [self.save_btn_gen, self.save_btn_tun, self.save_btn_col, self.save_btn_web]:
                orig_text = btn.cget("text")
                orig_color = btn.cget("fg_color")
                btn.configure(text="✓ Saved Successfully!", fg_color="#28a745")
                self.after(1500, lambda b=btn, t=orig_text, c=orig_color: b.configure(text=t, fg_color=c))

    def write_log(self, text):
        def update():
            self.status_textbox.configure(state="normal")
            formatted_text = text.replace(" | ", "\n")
            self.status_textbox.insert("end", formatted_text + "\n")
            self.status_textbox.see("end")
            self.status_textbox.configure(state="disabled")
        self.after(0, update)

    def clear_log(self):
        self.status_textbox.configure(state="normal")
        self.status_textbox.delete("1.0", "end")
        self.status_textbox.configure(state="disabled")

    def start_macro(self):
        if not os.path.exists(MACRO_SCRIPT):
            self.write_log(f"ERROR:\nCannot find {MACRO_SCRIPT} in current directory.")
            return

        self.save_settings(quiet=True)

        self.is_running = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        threading.Thread(target=self.run_process_thread, daemon=True).start()

    def run_process_thread(self):
        try:
            cmd = [sys.executable, MACRO_SCRIPT, "--profile", self.var_profile.get().strip()]
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True
            )

            buffer = ""
            while self.is_running and self.process.poll() is None:
                char = self.process.stdout.read(1)
                if not char:
                    break

                sys.stdout.write(char)
                sys.stdout.flush()

                if char in ('\r', '\n'):
                    if buffer:
                        clean_text = ANSI_ESCAPE_RE.sub('', buffer).strip()
                        if clean_text:
                            self.write_log(clean_text)
                        buffer = ""
                else:
                    buffer += char

        except Exception as e:
            self.write_log(f"Process Error:\n{str(e)}")
        finally:
            self.process_ended()

    def stop_macro(self):
        if self.is_running and self.process:
            self.write_log("Stopping macro...")
            self.is_running = False
            self.process.terminate()

    def process_ended(self):
        def reset():
            self.is_running = False
            self.start_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")
            self.write_log("Macro offline.")
        self.after(0, reset)

    def check_for_update(self):
        """Check the server for a newer FischTux version."""
        try:
            import urllib.request

            with urllib.request.urlopen(VERSION_URL, timeout=5) as response:
                version_server = response.read().decode("utf-8").strip()

            if not version_server:
                return

            if version_server != VERSION_CURRENT:
                self.show_update_prompt(VERSION_CURRENT, version_server)

        except Exception as e:
            self.write_log(f"Update check failed: {e}")


    def show_update_prompt(self, version_current, version_server):
        """Show the update confirmation dialog."""
        dialog = ctk.CTkToplevel(self)
        dialog.title("FischTux Update Available")
        dialog.geometry("500x220")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()
        dialog.attributes("-topmost", True)

        # Message
        ctk.CTkLabel(
            dialog,
            text=(
                f"Your version of FischTux ({version_current}) is out of date.\n\n"
                f"The latest version is ({version_server}).\n\n"
                "Do you want to update?"
            ),
            font=ctk.CTkFont(size=15),
            justify="center"
        ).pack(padx=30, pady=(30, 20))

        # Buttons
        button_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        button_frame.pack(fill="x", padx=40, pady=(0, 25))

        def update():
            dialog.destroy()
            self.update_fischtux(version_server)

        ctk.CTkButton(
            button_frame,
            text="Yes",
            fg_color="#28a745",
            hover_color="#218838",
            height=40,
            command=update
        ).pack(side="left", expand=True, fill="x", padx=(0, 10))

        ctk.CTkButton(
            button_frame,
            text="No",
            fg_color="#dc3545",
            hover_color="#c82333",
            height=40,
            command=dialog.destroy
        ).pack(side="left", expand=True, fill="x", padx=(10, 0))


    def update_fischtux(self, version_server):
        """Run the FischTux update process."""
        self.write_log(
            f"Updating FischTux to version {version_server}..."
        )

        subprocess.run(['/bin/bash', '-c', '"git pull"'])


if __name__ == "__main__":
    app = MacroGUI()
    app.mainloop()
