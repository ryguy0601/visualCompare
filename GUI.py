import os
import queue
import re
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from tkinter import font as tkfont

DEFAULT_SCRIPT_NAME = "main.py"

# --- COLOR PALETTE (Modern Slate Dark Theme) ---
PALETTE = {
    "bg_app": "#0b0f19",          # Deep slate background
    "bg_card": "#162032",         # Card panel background
    "bg_card_inner": "#0f172a",   # Inner input background
    "border": "#27354a",          # Subtle borders
    "border_focus": "#38bdf8",    # Sky blue accent
    "text_main": "#f8fafc",       # High-contrast white
    "text_muted": "#94a3b8",      # Secondary gray
    "accent_blue": "#0284c7",     # Primary action button
    "accent_blue_hover": "#0369a1",
    "accent_red": "#e11d48",      # Abort / Danger
    "accent_red_hover": "#be123c",
    "accent_green": "#10b981",    # Success / Ready
    "accent_yellow": "#f59e0b",   # In-progress / Busy
    "pill_active_bg": "#38bdf8",
    "pill_active_fg": "#0b0f19",
    "pill_inactive_bg": "#101726",
    "pill_inactive_fg": "#94a3b8",
    "terminal_bg": "#070a12",     # Rich black terminal
    "terminal_fg": "#e2e8f0",
}


def draw_rounded_rectangle(canvas, x1, y1, x2, y2, radius=12, **kwargs):
    """Draws a smooth polygon with rounded corners onto a Tkinter canvas."""
    points = [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]
    return canvas.create_polygon(points, **kwargs, smooth=True)


class RoundedBox(tk.Canvas):
    """A canvas container that renders a rounded background card with borders."""
    def __init__(self, parent, bg_color, border_color, radius=16, pad=0, **kwargs):
        super().__init__(parent, bg=kwargs.pop("canvas_bg", PALETTE["bg_app"]), highlightthickness=0, **kwargs)
        self.bg_color = bg_color
        self.border_color = border_color
        self.radius = radius
        self.pad = pad
        self.bind("<Configure>", self._redraw)

    def _redraw(self, event=None):
        self.delete("bg_shape")
        w, h = self.winfo_width(), self.winfo_height()
        if w < self.radius * 2 or h < self.radius * 2:
            return
        p = self.pad + 1
        draw_rounded_rectangle(
            self, p, p, w - p, h - p,
            radius=self.radius,
            fill=self.bg_color,
            outline=self.border_color,
            width=1,
            tags="bg_shape"
        )
        self.tag_lower("bg_shape")


class RoundedButton(tk.Canvas):
    """Modern button rendered as a rounded capsule with hover/active transitions."""
    def __init__(self, parent, text, command=None, bg_color="#0284c7", hover_color="#0369a1",
                 fg_color="#ffffff", radius=12, width=150, height=36, font=("Segoe UI", 9, "bold"),
                 canvas_bg=PALETTE["bg_app"]):
        super().__init__(parent, width=width, height=height, bg=canvas_bg, highlightthickness=0)
        self.text = text
        self.command = command
        self.normal_bg = bg_color
        self.hover_bg = hover_color
        self.curr_bg = bg_color
        self.fg_color = fg_color
        self.radius = radius
        self.btn_font = font
        self.canvas_bg = canvas_bg
        self.state = "normal"

        self.bind("<Configure>", self._draw)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    def _draw(self, event=None):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        draw_rounded_rectangle(
            self, 2, 2, w - 2, h - 2,
            radius=self.radius,
            fill=self.curr_bg,
            outline="",
            tags="btn_bg"
        )
        self.create_text(
            w / 2, h / 2,
            text=self.text,
            fill=self.fg_color if self.state == "normal" else PALETTE["text_muted"],
            font=self.btn_font,
            tags="btn_text"
        )
        self.config(cursor="hand2" if self.state == "normal" else "arrow")

    def _on_enter(self, e):
        if self.state == "normal":
            self.curr_bg = self.hover_bg
            self._draw()

    def _on_leave(self, e):
        if self.state == "normal":
            self.curr_bg = self.normal_bg
            self._draw()

    def _on_click(self, e):
        if self.state == "normal" and self.command:
            self.command()

    def set_state(self, state):
        self.state = state
        self.curr_bg = self.normal_bg if state == "normal" else PALETTE["border"]
        self._draw()

    def set_colors(self, bg_color, hover_color=None, fg_color=None):
        self.normal_bg = bg_color
        self.hover_bg = hover_color or bg_color
        self.curr_bg = bg_color
        if fg_color:
            self.fg_color = fg_color
        self._draw()


class ModernVisualDiffGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Visual Regression Diff Studio")
        self.geometry("1040x950")
        self.minsize(720, 560)
        self.configure(bg=PALETTE["bg_app"])

        self.proc = None
        self.log_queue = queue.Queue()
        self.is_running = False

        self._configure_ttk_styles()
        self._build_ui()
        self._poll_log_queue()

    def _configure_ttk_styles(self):
        self.style = ttk.Style(self)
        self.style.theme_use("clam")
        self.style.configure(".", background=PALETTE["bg_app"], foreground=PALETTE["text_main"], font=("Segoe UI", 9))
        self.style.configure(
            "Dark.Vertical.TScrollbar",
            background=PALETTE["border"],
            troughcolor=PALETTE["bg_app"],
            bordercolor=PALETTE["bg_app"],
            arrowcolor=PALETTE["text_muted"]
        )

    def _create_rounded_card(self, parent, title: str, subtitle: str = ""):
        box = RoundedBox(parent, bg_color=PALETTE["bg_card"], border_color=PALETTE["border"], radius=18, height=10)
        box.pack(fill=tk.X, pady=(0, 10))

        inner = tk.Frame(box, bg=PALETTE["bg_card"], padx=16, pady=10)
        inner.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        header_frame = tk.Frame(inner, bg=PALETTE["bg_card"])
        header_frame.pack(fill=tk.X, pady=(0, 6))

        tk.Label(
            header_frame,
            text=title,
            font=("Segoe UI", 10, "bold"),
            fg=PALETTE["text_main"],
            bg=PALETTE["bg_card"]
        ).pack(side=tk.LEFT)

        if subtitle:
            tk.Label(
                header_frame,
                text=subtitle,
                font=("Segoe UI", 8),
                fg=PALETTE["text_muted"],
                bg=PALETTE["bg_card"]
            ).pack(side=tk.LEFT, padx=(8, 0), pady=(1, 0))

        body = tk.Frame(inner, bg=PALETTE["bg_card"])
        body.pack(fill=tk.X)
        return body

    def _create_rounded_entry(self, parent, textvar, width=None):
        h = 32
        pill = RoundedBox(
            parent,
            bg_color=PALETTE["bg_card_inner"],
            border_color=PALETTE["border"],
            canvas_bg=PALETTE["bg_card"],
            radius=12,
            height=h
        )
        pill.pack_propagate(False)

        ent = tk.Entry(
            pill,
            textvariable=textvar,
            bg=PALETTE["bg_card_inner"],
            fg=PALETTE["text_main"],
            insertbackground=PALETTE["border_focus"],
            relief=tk.FLAT,
            font=("Segoe UI", 9),
            width=width
        )
        ent.pack(fill=tk.BOTH, expand=True, padx=12, pady=5)
        return pill, ent

    def _build_ui(self):
        container = tk.Frame(self, bg=PALETTE["bg_app"], padx=16, pady=12)
        container.pack(fill=tk.BOTH, expand=True)

        # ----------------------------------------------------
        # TOP HEADER
        # ----------------------------------------------------
        header_box = tk.Frame(container, bg=PALETTE["bg_app"])
        header_box.pack(fill=tk.X, pady=(0, 10))

        title_col = tk.Frame(header_box, bg=PALETTE["bg_app"])
        title_col.pack(side=tk.LEFT)
        tk.Label(
            title_col,
            text="Visual Regression Studio",
            font=("Segoe UI", 14, "bold"),
            fg=PALETTE["text_main"],
            bg=PALETTE["bg_app"]
        ).pack(anchor=tk.W)
        tk.Label(
            title_col,
            text="Automated DOM alignment & pixel-difference engine",
            font=("Segoe UI", 8),
            fg=PALETTE["text_muted"],
            bg=PALETTE["bg_app"]
        ).pack(anchor=tk.W)

        status_pill = RoundedBox(header_box, bg_color=PALETTE["bg_card"], border_color=PALETTE["border"], radius=14, height=30)
        status_pill.pack(side=tk.RIGHT)

        status_inner = tk.Frame(status_pill, bg=PALETTE["bg_card"], padx=10, pady=4)
        status_inner.pack(fill=tk.BOTH, expand=True)

        self.canvas_status = tk.Canvas(status_inner, width=8, height=8, bg=PALETTE["bg_card"], highlightthickness=0)
        self.canvas_status.pack(side=tk.LEFT, padx=(0, 6))
        self.status_dot = self.canvas_status.create_oval(0, 0, 8, 8, fill=PALETTE["accent_green"], outline="")

        self.lbl_status = tk.Label(
            status_inner,
            text="Ready",
            font=("Segoe UI", 8, "bold"),
            fg=PALETTE["text_main"],
            bg=PALETTE["bg_card"]
        )
        self.lbl_status.pack(side=tk.LEFT)

        # ----------------------------------------------------
        # SINGLE UNIFIED SCROLLING CONTAINER
        # ----------------------------------------------------
        scroll_wrapper = tk.Frame(container, bg=PALETTE["bg_app"])
        scroll_wrapper.pack(fill=tk.BOTH, expand=True)

        self.main_canvas = tk.Canvas(scroll_wrapper, bg=PALETTE["bg_app"], highlightthickness=0)
        self.main_scrollbar = ttk.Scrollbar(scroll_wrapper, orient=tk.VERTICAL, command=self.main_canvas.yview, style="Dark.Vertical.TScrollbar")
        self.scrollable_content = tk.Frame(self.main_canvas, bg=PALETTE["bg_app"])

        self.canvas_window_id = self.main_canvas.create_window((0, 0), window=self.scrollable_content, anchor="nw")

        self.main_canvas.configure(yscrollcommand=self.main_scrollbar.set)
        self.main_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.main_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.scrollable_content.bind("<Configure>", lambda e: self.main_canvas.configure(scrollregion=self.main_canvas.bbox("all")))
        self.main_canvas.bind("<Configure>", lambda e: self.main_canvas.itemconfig(self.canvas_window_id, width=e.width))

        self.main_canvas.bind("<Enter>", lambda _: self.main_canvas.bind_all("<MouseWheel>", self._on_main_mousewheel))
        self.main_canvas.bind("<Leave>", lambda _: self.main_canvas.unbind_all("<MouseWheel>"))

        # ----------------------------------------------------
        # CARD 1: TARGET ENVIRONMENTS
        # ----------------------------------------------------
        card_env = self._create_rounded_card(self.scrollable_content, "Target Environments", "Specify baseline and staging hosts")
        card_env.columnconfigure(1, weight=1)

        tk.Label(card_env, text="Production:", font=("Segoe UI", 9, "bold"), fg=PALETTE["text_muted"], bg=PALETTE["bg_card"]).grid(row=0, column=0, sticky=tk.W, pady=3)
        self.var_prod_url = tk.StringVar(value="")
        b_prod, _ = self._create_rounded_entry(card_env, self.var_prod_url)
        b_prod.grid(row=0, column=1, sticky=tk.EW, padx=(10, 0), pady=3)

        tk.Label(card_env, text="Staging:", font=("Segoe UI", 9, "bold"), fg=PALETTE["text_muted"], bg=PALETTE["bg_card"]).grid(row=1, column=0, sticky=tk.W, pady=3)
        self.var_stg_url = tk.StringVar(value="")
        b_stg, _ = self._create_rounded_entry(card_env, self.var_stg_url)
        b_stg.grid(row=1, column=1, sticky=tk.EW, padx=(10, 0), pady=3)

        # ----------------------------------------------------
        # CARD 2: PRE-CAPTURE ACTIONS (INTERACTION CLICKS)
        # ----------------------------------------------------
        card_clicks = self._create_rounded_card(
            self.scrollable_content,
            "Pre-Capture Click Actions",
            "Dismiss cookie banners, popups, or trigger state changes prior to snapshots"
        )
        card_clicks.columnconfigure(1, weight=1)

        tk.Label(card_clicks, text="Click Targets:", font=("Segoe UI", 9, "bold"), fg=PALETTE["text_muted"], bg=PALETTE["bg_card"]).grid(row=0, column=0, sticky=tk.W, pady=3)
        self.var_click_selectors = tk.StringVar(value="#onetrust-accept-btn-handler, button.hcp-confirm")
        b_clicks, _ = self._create_rounded_entry(card_clicks, self.var_click_selectors)
        b_clicks.grid(row=0, column=1, sticky=tk.EW, padx=(10, 0), pady=3)

        tk.Label(
            card_clicks,
            text="Comma or pipe-separated CSS selectors (e.g., #onetrust-accept-btn-handler, .btn-close, #isi-expand)",
            font=("Segoe UI", 7),
            fg=PALETTE["text_muted"],
            bg=PALETTE["bg_card"]
        ).grid(row=1, column=1, sticky=tk.W, padx=(10, 0), pady=(0, 2))

        # ----------------------------------------------------
        # CARD 3: ROUTE SELECTION
        # ----------------------------------------------------
        card_routes = self._create_rounded_card(self.scrollable_content, "Route Scope", "Select dynamic link crawler or defined paths")
        card_routes.columnconfigure(1, weight=1)

        self.var_mode = tk.StringVar(value="crawl")

        mode_btn_frame = tk.Frame(card_routes, bg=PALETTE["bg_card"])
        mode_btn_frame.grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 6))

        self.btn_mode_crawl = RoundedButton(
            mode_btn_frame,
            text="Auto-Crawl Routes",
            radius=12,
            width=140,
            height=28,
            bg_color=PALETTE["pill_active_bg"],
            fg_color=PALETTE["pill_active_fg"],
            canvas_bg=PALETTE["bg_card"],
            command=lambda: self._set_mode("crawl")
        )
        self.btn_mode_crawl.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_mode_manual = RoundedButton(
            mode_btn_frame,
            text="Specific Pages Only",
            radius=12,
            width=140,
            height=28,
            bg_color=PALETTE["pill_inactive_bg"],
            fg_color=PALETTE["pill_inactive_fg"],
            canvas_bg=PALETTE["bg_card"],
            command=lambda: self._set_mode("manual")
        )
        self.btn_mode_manual.pack(side=tk.LEFT)

        # Crawl limit row
        self.row_crawl = tk.Frame(card_routes, bg=PALETTE["bg_card"])
        self.row_crawl.grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=2)
        tk.Label(self.row_crawl, text="Max Depth / Pages:", fg=PALETTE["text_muted"], bg=PALETTE["bg_card"]).pack(side=tk.LEFT)
        self.var_max_pages = tk.StringVar(value="15")
        b_pages, _ = self._create_rounded_entry(self.row_crawl, self.var_max_pages, width=6)
        b_pages.config(width=80)
        b_pages.pack(side=tk.LEFT, padx=10)

        # Manual entry row
        self.row_manual = tk.Frame(card_routes, bg=PALETTE["bg_card"])
        self.row_manual.grid(row=2, column=0, columnspan=2, sticky=tk.EW, pady=2)
        self.row_manual.columnconfigure(1, weight=1)
        tk.Label(self.row_manual, text="Paths (Space/Comma-separated):", fg=PALETTE["text_muted"], bg=PALETTE["bg_card"]).grid(row=0, column=0, sticky=tk.W)
        self.var_specific_pages = tk.StringVar(value="/ /about-us /isi")
        b_spec, _ = self._create_rounded_entry(self.row_manual, self.var_specific_pages)
        b_spec.grid(row=0, column=1, sticky=tk.EW, padx=(10, 0))

        # ----------------------------------------------------
        # CARD 4: PARAMETERS & THRESHOLDS
        # ----------------------------------------------------
        card_params = self._create_rounded_card(self.scrollable_content, "Execution Parameters", "Viewports, concurrency and sensitivity")
        card_params.columnconfigure(1, weight=1)
        card_params.columnconfigure(3, weight=1)

        # Target device
        tk.Label(card_params, text="Device Target:", fg=PALETTE["text_muted"], bg=PALETTE["bg_card"]).grid(row=0, column=0, sticky=tk.W, pady=3)
        dev_frame = tk.Frame(card_params, bg=PALETTE["bg_card"])
        dev_frame.grid(row=0, column=1, columnspan=3, sticky=tk.W, pady=3)

        self.var_device = tk.StringVar(value="both")
        for dev_val, dev_label in [("both", "Both"), ("desktop", "Desktop Only"), ("mobile", "Mobile Only")]:
            rb = tk.Radiobutton(
                dev_frame,
                text=dev_label,
                value=dev_val,
                variable=self.var_device,
                bg=PALETTE["bg_card"],
                fg=PALETTE["text_main"],
                selectcolor=PALETTE["bg_app"],
                activebackground=PALETTE["bg_card"],
                activeforeground=PALETTE["text_main"],
                font=("Segoe UI", 9)
            )
            rb.pack(side=tk.LEFT, padx=(0, 14))

        # Resolutions
        tk.Label(card_params, text="Desktop (WxH):", fg=PALETTE["text_muted"], bg=PALETTE["bg_card"]).grid(row=1, column=0, sticky=tk.W, pady=3)
        self.var_desk_res = tk.StringVar(value="1920x1080")
        b_desk, _ = self._create_rounded_entry(card_params, self.var_desk_res)
        b_desk.grid(row=1, column=1, sticky=tk.EW, padx=(10, 10), pady=3)

        tk.Label(card_params, text="Mobile (WxH):", fg=PALETTE["text_muted"], bg=PALETTE["bg_card"]).grid(row=1, column=2, sticky=tk.W, pady=3)
        self.var_mob_res = tk.StringVar(value="440x956")
        b_mob, _ = self._create_rounded_entry(card_params, self.var_mob_res)
        b_mob.grid(row=1, column=3, sticky=tk.EW, padx=(10, 0), pady=3)

        # Worker Threads & Threshold
        tk.Label(card_params, text="Workers (Threads):", fg=PALETTE["text_muted"], bg=PALETTE["bg_card"]).grid(row=2, column=0, sticky=tk.W, pady=3)
        self.var_workers = tk.StringVar(value="3")
        b_work, _ = self._create_rounded_entry(card_params, self.var_workers)
        b_work.grid(row=2, column=1, sticky=tk.EW, padx=(10, 10), pady=3)

        tk.Label(card_params, text="Pixelmatch Threshold:", fg=PALETTE["text_muted"], bg=PALETTE["bg_card"]).grid(row=2, column=2, sticky=tk.W, pady=3)
        self.var_threshold = tk.StringVar(value="0.1")
        b_thresh, _ = self._create_rounded_entry(card_params, self.var_threshold)
        b_thresh.grid(row=2, column=3, sticky=tk.EW, padx=(10, 0), pady=3)

        # Max Mismatch tolerance %
        tk.Label(card_params, text="Max Mismatch %:", fg=PALETTE["text_muted"], bg=PALETTE["bg_card"]).grid(row=3, column=0, sticky=tk.W, pady=3)
        self.var_max_mismatch = tk.StringVar(value="0.5")
        b_mismatch, _ = self._create_rounded_entry(card_params, self.var_max_mismatch)
        b_mismatch.grid(row=3, column=1, sticky=tk.EW, padx=(10, 10), pady=3)

        # Chrome/Edge Executable override
        tk.Label(card_params, text="Browser Executable:", fg=PALETTE["text_muted"], bg=PALETTE["bg_card"]).grid(row=3, column=2, sticky=tk.W, pady=3)
        self.var_chrome_path = tk.StringVar(value="C:\Program Files\Google\Chrome\Application\chrome.exe")
        b_chrome, _ = self._create_rounded_entry(card_params, self.var_chrome_path)
        b_chrome.grid(row=3, column=3, sticky=tk.EW, padx=(10, 0), pady=3)

        # Output Folder + Rounded Browse button
        tk.Label(card_params, text="Output Directory:", fg=PALETTE["text_muted"], bg=PALETTE["bg_card"]).grid(row=4, column=0, sticky=tk.W, pady=3)
        self.var_output_dir = tk.StringVar(value="test-results")

        dir_box = tk.Frame(card_params, bg=PALETTE["bg_card"])
        dir_box.grid(row=4, column=1, columnspan=3, sticky=tk.EW, pady=3)
        dir_box.columnconfigure(0, weight=1)

        b_dir, _ = self._create_rounded_entry(dir_box, self.var_output_dir)
        b_dir.grid(row=0, column=0, sticky=tk.EW)

        btn_browse = RoundedButton(
            dir_box,
            text="Browse",
            radius=12,
            width=76,
            height=32,
            bg_color=PALETTE["border"],
            hover_color=PALETTE["border_focus"],
            fg_color=PALETTE["text_main"],
            canvas_bg=PALETTE["bg_card"],
            command=self._browse_dir
        )
        btn_browse.grid(row=0, column=1, padx=(8, 0))

        # Checkbox
        self.var_auto_open = tk.BooleanVar(value=True)
        cb_open = tk.Checkbutton(
            card_params,
            text="Automatically open HTML report in browser on completion",
            variable=self.var_auto_open,
            bg=PALETTE["bg_card"],
            fg=PALETTE["text_main"],
            selectcolor=PALETTE["bg_app"],
            activebackground=PALETTE["bg_card"],
            activeforeground=PALETTE["text_main"],
            font=("Segoe UI", 8)
        )
        cb_open.grid(row=5, column=0, columnspan=4, sticky=tk.W, pady=(6, 0))

        # ----------------------------------------------------
        # ACTION BUTTONS TOOLBAR
        # ----------------------------------------------------
        toolbar = tk.Frame(self.scrollable_content, bg=PALETTE["bg_app"])
        toolbar.pack(fill=tk.X, pady=(2, 10))

        self.btn_run = RoundedButton(
            toolbar,
            text="▶  Execute Regression Test",
            radius=14,
            width=210,
            height=38,
            bg_color=PALETTE["accent_blue"],
            hover_color=PALETTE["accent_blue_hover"],
            fg_color="#ffffff",
            font=("Segoe UI", 9, "bold"),
            command=self._start_run
        )
        self.btn_run.pack(side=tk.LEFT, padx=(0, 10))

        self.btn_stop = RoundedButton(
            toolbar,
            text="■  Cancel",
            radius=14,
            width=100,
            height=38,
            bg_color=PALETTE["border"],
            hover_color=PALETTE["accent_red"],
            fg_color=PALETTE["text_muted"],
            font=("Segoe UI", 9, "bold"),
            command=self._abort_run
        )
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 10))
        self.btn_stop.set_state("disabled")

        self.btn_open_report = RoundedButton(
            toolbar,
            text="Open Last Report",
            radius=14,
            width=140,
            height=38,
            bg_color=PALETTE["bg_card"],
            hover_color=PALETTE["border"],
            fg_color=PALETTE["text_main"],
            font=("Segoe UI", 9),
            command=self._open_report_manually
        )
        self.btn_open_report.pack(side=tk.RIGHT)

        # ----------------------------------------------------
        # COMPACT TERMINAL CONSOLE (Part of main scroll)
        # ----------------------------------------------------
        log_box = RoundedBox(self.scrollable_content, bg_color=PALETTE["terminal_bg"], border_color=PALETTE["border"], radius=18)
        log_box.pack(fill=tk.X, pady=(0, 16))

        log_inner = tk.Frame(log_box, bg=PALETTE["terminal_bg"], padx=10, pady=8)
        log_inner.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        available_fonts = tkfont.families(self)
        chosen_font = ("Cascadia Code", 8) if "Cascadia Code" in available_fonts else ("Consolas", 8)

        # Compact height=11 rows (~180px)
        self.txt_log = tk.Text(
            log_inner,
            bg=PALETTE["terminal_bg"],
            fg=PALETTE["terminal_fg"],
            insertbackground=PALETTE["border_focus"],
            font=chosen_font,
            relief=tk.FLAT,
            wrap=tk.WORD,
            height=11
        )
        self.txt_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        log_scroll = ttk.Scrollbar(log_inner, orient=tk.VERTICAL, command=self.txt_log.yview, style="Dark.Vertical.TScrollbar")
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.txt_log.config(yscrollcommand=log_scroll.set)

        # Local wheel scrolling inside text box only when cursor is inside it
        self.txt_log.bind("<Enter>", lambda _: self.main_canvas.unbind_all("<MouseWheel>"))
        self.txt_log.bind("<Leave>", lambda _: self.main_canvas.bind_all("<MouseWheel>", self._on_main_mousewheel))

        self._update_mode_ui()

    def _on_main_mousewheel(self, event):
        self.main_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _set_mode(self, mode: str):
        self.var_mode.set(mode)
        self._update_mode_ui()

    def _update_mode_ui(self):
        is_crawl = self.var_mode.get() == "crawl"
        if is_crawl:
            self.btn_mode_crawl.set_colors(PALETTE["pill_active_bg"], PALETTE["pill_active_bg"], PALETTE["pill_active_fg"])
            self.btn_mode_manual.set_colors(PALETTE["pill_inactive_bg"], PALETTE["border"], PALETTE["pill_inactive_fg"])
            self.row_crawl.grid()
            self.row_manual.grid_remove()
        else:
            self.btn_mode_crawl.set_colors(PALETTE["pill_inactive_bg"], PALETTE["border"], PALETTE["pill_inactive_fg"])
            self.btn_mode_manual.set_colors(PALETTE["pill_active_bg"], PALETTE["pill_active_bg"], PALETTE["pill_active_fg"])
            self.row_crawl.grid_remove()
            self.row_manual.grid()

    def _set_status(self, text: str, state: str):
        color_map = {
            "ready": PALETTE["accent_green"],
            "running": PALETTE["accent_yellow"],
            "error": PALETTE["accent_red"],
            "aborted": PALETTE["accent_red"],
        }
        fill_color = color_map.get(state, PALETTE["text_muted"])
        self.canvas_status.itemconfig(self.status_dot, fill=fill_color)
        self.lbl_status.config(text=text)

    def _browse_dir(self):
        path = filedialog.askdirectory(title="Select Output Directory", initialdir=self.var_output_dir.get())
        if path:
            self.var_output_dir.set(path)

    def _open_report_manually(self):
        rep_file = Path(self.var_output_dir.get()) / "report.html"
        if rep_file.exists():
            webbrowser.open(rep_file.resolve().as_uri())
        else:
            messagebox.showwarning("Report Missing", f"Could not find an existing report at:\n{rep_file.resolve()}")

    def _log(self, text: str):
        self.txt_log.insert(tk.END, text)
        self.txt_log.see(tk.END)

    def _assemble_cli_args(self) -> list[str]:
        script_path = Path(__file__).resolve().parent / DEFAULT_SCRIPT_NAME
        if not script_path.exists():
            f = filedialog.askopenfilename(
                title=f"Locate {DEFAULT_SCRIPT_NAME}",
                filetypes=[("Python Files", "*.py")]
            )
            if not f:
                raise FileNotFoundError("Diff runner engine script was not found.")
            script_path = Path(f)

        prod = self.var_prod_url.get().strip()
        stg = self.var_stg_url.get().strip()

        if not prod or not stg:
            raise ValueError("Both Production and Staging URLs must be provided.")

        args = [
            sys.executable,
            "-u",
            str(script_path),
            "--prod-url", prod,
            "--stg-url", stg,
            "--device", self.var_device.get(),
            "--desktop-res", self.var_desk_res.get().strip(),
            "--mobile-res", self.var_mob_res.get().strip(),
            "--workers", str(self.var_workers.get().strip()),
            "--threshold", str(self.var_threshold.get().strip()),
            "--max-mismatch", str(self.var_max_mismatch.get().strip()),
            "--output-dir", self.var_output_dir.get().strip(),
        ]

        chrome_exec = self.var_chrome_path.get().strip()
        if chrome_exec:
            args.extend(["--chrome-path", chrome_exec])

        if not self.var_auto_open.get():
            args.append("--no-open")

        raw_clicks = self.var_click_selectors.get().strip()
        if raw_clicks:
            selectors = [s.strip() for s in re.split(r"[,|]", raw_clicks) if s.strip()]
            if selectors:
                args.extend(["--click-selectors", ",".join(selectors)])

        if self.var_mode.get() == "crawl":
            args.extend(["--crawl", "--max-pages", str(self.var_max_pages.get().strip())])
        else:
            raw_pages = self.var_specific_pages.get().strip()
            pages = [p.strip() for p in re.split(r"[\s,]+", raw_pages) if p.strip()]
            if not pages:
                raise ValueError("Manual routes selected, but no paths were provided.")
            args.extend(["--pages", ",".join(pages)])

        return args

    def _start_run(self):
        if self.is_running:
            return

        try:
            cmd = self._assemble_cli_args()
        except Exception as e:
            messagebox.showerror("Configuration Error", str(e))
            return

        self.txt_log.delete("1.0", tk.END)
        self._log(f"[EXEC] {' '.join(cmd)}\n\n")

        self.btn_run.set_state("disabled")
        self.btn_stop.set_state("normal")
        self.btn_stop.set_colors(PALETTE["accent_red"], PALETTE["accent_red_hover"], "#ffffff")
        self._set_status("Running...", "running")
        self.is_running = True

        threading.Thread(target=self._process_worker_thread, args=(cmd,), daemon=True).start()

    def _process_worker_thread(self, cmd):
        try:
            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            )

            for line in self.proc.stdout:
                self.log_queue.put(line)

            self.proc.wait()
            rc = self.proc.returncode
            self.log_queue.put(f"\n[DONE] Process terminated with return code {rc}\n")
        except Exception as err:
            self.log_queue.put(f"\n[ERROR] Worker thread error: {err}\n")
        finally:
            self.log_queue.put(None)

    def _abort_run(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass
            self._log("\n[ABORT] Cancellation signal dispatched to worker.\n")
            self._set_status("Aborted", "aborted")

    def _poll_log_queue(self):
        try:
            while True:
                line = self.log_queue.get_nowait()
                if line is None:
                    self.is_running = False
                    self.btn_run.set_state("normal")
                    self.btn_run.set_colors(PALETTE["accent_blue"], PALETTE["accent_blue_hover"], "#ffffff")
                    self.btn_stop.set_state("disabled")
                    self.btn_stop.set_colors(PALETTE["border"], PALETTE["border"], PALETTE["text_muted"])
                    if self.lbl_status.cget("text") != "Aborted":
                        self._set_status("Completed", "ready")
                else:
                    self._log(line)
        except queue.Empty:
            pass
        finally:
            self.after(80, self._poll_log_queue)


if __name__ == "__main__":
    app = ModernVisualDiffGUI()
    app.mainloop()