#!/usr/bin/env python3
import os
import sys
import re
import threading
import subprocess
import configparser
from pathlib import Path
try:
    import customtkinter as ctk
    import tkinter as tk
except ImportError:
    sys.exit("Missing dependency: customtkinter. Install with `pip install customtkinter`")

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("green")

SETTINGS_FILE = "Settings.ini"
MACRO_SCRIPT = "fisch_macro.py"

# Regex to strip ANSI escape sequences (like \033[K) from the macro's output
ANSI_ESCAPE_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

class MacroGUI(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("FischTux")
        self.geometry("900x600")
        self.minsize(850, 550)

        # Load Window Icon (Requires Tk 8.6+ which is default on modern Debian)
        self.app_icon = None
        try:
            from PIL import Image, ImageTk
            # Make sure to use a .png or .ico instead of .svg for tkinter compatibility
            self.app_icon = ImageTk.PhotoImage(Image.open("icon.ico"))
            self.iconphoto(False, self.app_icon)
        except Exception as e:
            print(f"Notice: Could not load image icon ({e})")

        # State variables
        self.process = None
        self.is_running = False

        # Grid Layout Configuration
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        # Variables for settings
        self.init_variables()

        # Build UI Components
        self.build_sidebar()
        self.build_main_view()

        # Load existing settings
        self.load_settings()

    def init_variables(self):
        # [Macro] Section
        self.var_window_title = ctk.StringVar()
        self.var_exit_key = ctk.StringVar(value="f8")
        self.var_steering = ctk.StringVar(value="mouse")
        self.var_pulse_ms = ctk.StringVar()
        self.var_hold_scale = ctk.StringVar(value="1.0")
        self.var_shake_min = ctk.StringVar(value="12")
        self.var_deadzone = ctk.StringVar(value="6")
        # [Fisch] Section
        self.var_control = ctk.StringVar(value="0")
        self.var_webhook_url = ctk.StringVar()

    def build_sidebar(self):
        self.sidebar_frame = ctk.CTkFrame(self, width=220, corner_radius=0)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(4, weight=1)

        # Branding
        self.logo_label = ctk.CTkLabel(
            self.sidebar_frame,
            text="Fisch Macro",
            font=ctk.CTkFont(size=24, weight="bold")
        )
        self.logo_label.grid(row=0, column=0, padx=20, pady=(30, 10))

        self.subtitle_label = ctk.CTkLabel(
            self.sidebar_frame,
            text="Control Panel",
            font=ctk.CTkFont(size=14),
            text_color="gray"
        )
        self.subtitle_label.grid(row=1, column=0, padx=20, pady=(0, 20))

        # Action Buttons
        self.start_btn = ctk.CTkButton(
            self.sidebar_frame, text="▶ Start Macro", fg_color="#28a745", hover_color="#218838",
            font=ctk.CTkFont(size=14, weight="bold"), command=self.start_macro, height=40
        )
        self.start_btn.grid(row=2, column=0, padx=20, pady=10, sticky="ew")

        self.stop_btn = ctk.CTkButton(
            self.sidebar_frame, text="■ Stop Macro", fg_color="#dc3545", hover_color="#c82333",
            font=ctk.CTkFont(size=14, weight="bold"), state="disabled", command=self.stop_macro, height=40
        )
        self.stop_btn.grid(row=3, column=0, padx=20, pady=10, sticky="ew")

        # Log Output Box
        self.status_title = ctk.CTkLabel(self.sidebar_frame, text="Live Status:", font=ctk.CTkFont(weight="bold"))
        self.status_title.grid(row=5, column=0, padx=20, pady=(10, 0), sticky="w")

        self.status_textbox = ctk.CTkTextbox(self.sidebar_frame, width=200, height=200, state="disabled", wrap="word", fg_color="#1e1e1e")
        self.status_textbox.grid(row=6, column=0, padx=15, pady=10, sticky="nsew")

    def build_main_view(self):
        self.main_frame = ctk.CTkScrollableFrame(self, corner_radius=15)
        self.main_frame.grid(row=0, column=1, padx=20, pady=20, sticky="nsew")
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_columnconfigure(1, weight=1)

        title = ctk.CTkLabel(self.main_frame, text="Settings & Configuration", font=ctk.CTkFont(size=24, weight="bold"))
        title.grid(row=0, column=0, columnspan=2, padx=20, pady=(20, 20), sticky="w")

        # General Group
        ctk.CTkLabel(self.main_frame, text="General Settings", font=ctk.CTkFont(size=16, weight="bold")).grid(row=1, column=0, padx=20, pady=10, sticky="w")

        self.add_setting_row("Window Title (Empty for click-select):", self.var_window_title, 2, placeholder="e.g. Roblox or Sober")
        self.add_setting_row("Exit Hotkey:", self.var_exit_key, 3, placeholder="f8")

        ctk.CTkLabel(self.main_frame, text="Steering Input Method:").grid(row=4, column=0, padx=20, pady=5, sticky="w")
        self.steer_menu = ctk.CTkOptionMenu(self.main_frame, variable=self.var_steering, values=["mouse", "space"])
        self.steer_menu.grid(row=4, column=1, padx=20, pady=5, sticky="ew")

        ctk.CTkLabel(self.main_frame, text="Webhook Settings", font=ctk.CTkFont(size=16, weight="bold")).grid(row=11, column=0, padx=20, pady=(20,10), sticky="w")
        self.add_setting_row("Discord Webhook URL:", self.var_webhook_url, 12, placeholder="https://discord.com/api/webhooks/...")


        # Timing & Tuning Group
        ctk.CTkLabel(self.main_frame, text="Tuning & Thresholds", font=ctk.CTkFont(size=16, weight="bold")).grid(row=5, column=0, padx=20, pady=(20,10), sticky="w")

        self.add_setting_row("Fixed Pulse MS (Empty for Formula):", self.var_pulse_ms, 6, placeholder="e.g. 100")
        self.add_setting_row("Hold Scale Factor (Default 1.0):", self.var_hold_scale, 7, placeholder="1.0")
        self.add_setting_row("Shake Min Pixels (Default 12):", self.var_shake_min, 8, placeholder="12")
        self.add_setting_row("Deadzone PX (Default 6):", self.var_deadzone, 9, placeholder="6")
        self.add_setting_row("Rod Control Stat (Fallback if undetected):", self.var_control, 10, placeholder="0")

        # Save Button
        self.save_btn = ctk.CTkButton(
            self.main_frame, text="Save Configuration", font=ctk.CTkFont(size=15, weight="bold"),
            command=self.save_settings, height=45
        )
        self.save_btn.grid(row=13, column=0, columnspan=2, padx=20, pady=40, sticky="ew")

    def add_setting_row(self, label_text, variable, row, placeholder=""):
        ctk.CTkLabel(self.main_frame, text=label_text).grid(row=row, column=0, padx=20, pady=5, sticky="w")
        entry = ctk.CTkEntry(self.main_frame, textvariable=variable, placeholder_text=placeholder)
        entry.grid(row=row, column=1, padx=20, pady=5, sticky="ew")

    def load_settings(self):
        cfg = configparser.ConfigParser()
        if Path(SETTINGS_FILE).exists():
            cfg.read(SETTINGS_FILE)

        def safe_get(section, key, var):
            if cfg.has_option(section, key):
                var.set(cfg.get(section, key))

        safe_get("Macro", "window-title", self.var_window_title)
        safe_get("Macro", "exit-key", self.var_exit_key)
        safe_get("Macro", "steering", self.var_steering)
        safe_get("Macro", "pulse-ms", self.var_pulse_ms)
        safe_get("Macro", "hold-scale", self.var_hold_scale)
        safe_get("Macro", "shake-min-pixels", self.var_shake_min)
        safe_get("Macro", "deadzone-px", self.var_deadzone)
        safe_get("Fisch", "Control", self.var_control)

    def save_settings(self):
        cfg = configparser.ConfigParser()
        if Path(SETTINGS_FILE).exists():
            cfg.read(SETTINGS_FILE)

        if not cfg.has_section("Macro"): cfg.add_section("Macro")
        if not cfg.has_section("Fisch"): cfg.add_section("Fisch")

        cfg.set("Macro", "window-title", self.var_window_title.get().strip())
        cfg.set("Macro", "exit-key", self.var_exit_key.get().strip())
        cfg.set("Macro", "steering", self.var_steering.get().strip())
        cfg.set("Macro", "pulse-ms", self.var_pulse_ms.get().strip())
        cfg.set("Macro", "hold-scale", self.var_hold_scale.get().strip())
        cfg.set("Macro", "shake-min-pixels", self.var_shake_min.get().strip())
        cfg.set("Macro", "deadzone-px", self.var_deadzone.get().strip())
        cfg.set("Fisch", "Control", self.var_control.get().strip())

        with open(SETTINGS_FILE, 'w') as f:
            cfg.write(f)

        # Visual feedback
        original_text = self.save_btn.cget("text")
        original_color = self.save_btn.cget("fg_color")
        self.save_btn.configure(text="✓ Settings Saved!", fg_color="#28a745")
        self.after(1500, lambda: self.save_btn.configure(text=original_text, fg_color=original_color))

    def write_log(self, text):
        """Safely updates the text box from any thread"""
        def update():
            self.status_textbox.configure(state="normal")
            self.status_textbox.delete("1.0", "end")

            # Format the output nicely if it contains the pipe separators
            formatted_text = text.replace(" | ", "\n")
            self.status_textbox.insert("end", formatted_text)
            self.status_textbox.configure(state="disabled")
        self.after(0, update)

    def start_macro(self):
        if not os.path.exists(MACRO_SCRIPT):
            self.write_log(f"ERROR:\nCannot find {MACRO_SCRIPT} in the current directory.")
            return

        self.save_settings() # Auto-save before running

        self.is_running = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        # Start background thread to run and monitor the subprocess
        threading.Thread(target=self.run_process_thread, daemon=True).start()

    def run_process_thread(self):
        try:
            # Launch the macro script
            self.process = subprocess.Popen(
                [sys.executable, MACRO_SCRIPT],
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

                # Mirror the raw output (including \r and ANSI codes) to the terminal
                sys.stdout.write(char)
                sys.stdout.flush()

                # The fisch_macro.py uses '\r' to update the same line in the terminal.
                # We need to detect either '\r' or '\n' to flush the buffer to the GUI.
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

if __name__ == "__main__":
    app = MacroGUI()
    app.mainloop()
