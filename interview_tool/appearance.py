# -*- coding: utf-8 -*-
"""appearance.py — 答案窗外观（背景色 / 文字色 / 强调色 / 透明度）持久化与设置面板。

配置文件 appearance.json 与 window-pos.txt 同层（repo 根）。show_answer_window 构建完
控件后 load() 并 apply；面板里每次改动即时生效并落盘。

custom=True 表示用户手动定过外观：变色龙吸色暂停（手动优先）。点「恢复默认」会清
custom，把控制权还给变色龙（若以 --chameleon 启动）。
"""

import json
import os
import re

from .config import BASE_DIR

FILE = os.path.join(BASE_DIR, "appearance.json")

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

DEFAULTS = {
    "bg": "#0D111A",        # 窗口底板
    "fg": "#E8ECF2",        # 正文文字
    "muted": "#8D98AA",     # 次要文字（状态行 / 列表）
    "accent": "#82AFFF",    # 强调色（标题 / 回答标签）
    "warm": "#E6B673",      # 提问标签 / 公式
    "code_bg": "#151B26",   # 代码块底色
    "alpha": 0.92,          # 整窗不透明度（越低越透，0.30–1.0）
    "custom": False,        # 用户是否手动定过外观（True = 暂停变色龙）
}

LIGHT_THEME = {
    "bg": "#F2F4F8",
    "fg": "#1E2430",
    "muted": "#5A6577",
    "accent": "#2D5F9A",
    "warm": "#80551E",
    "code_bg": "#E4E8F0",
    "alpha": 0.95,
}


def valid_color(value, fallback="#000000"):
    """校验 #RRGGBB；不合法返回 fallback（不抛异常，外观面板边输边校验靠它）。"""
    value = str(value or "").strip()
    return value.lower() if _COLOR_RE.match(value) else fallback


def load():
    """读 appearance.json；文件缺失/损坏/字段非法一律回退默认值。"""
    cfg = dict(DEFAULTS)
    try:
        with open(FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return cfg
    if not isinstance(data, dict):
        return cfg
    for key in ("bg", "fg", "muted", "accent", "warm", "code_bg"):
        cfg[key] = valid_color(data.get(key), DEFAULTS[key])
    try:
        cfg["alpha"] = max(0.30, min(1.0, float(data.get("alpha", DEFAULTS["alpha"]))))
    except (TypeError, ValueError):
        cfg["alpha"] = DEFAULTS["alpha"]
    cfg["custom"] = bool(data.get("custom", False))
    return cfg


def save(cfg):
    """写回 appearance.json；写失败不影响使用（下次启动回默认）。"""
    try:
        with open(FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def open_appearance_panel(parent, cfg, on_change, on_reset=None,
                          chameleon_active=False):
    """打开唯一外观设置窗；所有控件改动即时回调 on_change(cfg)（面板不负责落盘）。

    cfg 与调用方共用同一个 dict：面板改的是它，on_change 里 apply + save。
    on_reset：恢复默认并（若变色龙开启）把颜色控制权交还吸色循环。
    """
    import tkinter as tk
    from tkinter import colorchooser

    existing = getattr(parent, "_appearance_window", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.deiconify()
                existing.lift()
                existing.focus_force()
                return existing
        except Exception:
            pass

    win = tk.Toplevel(parent)
    parent._appearance_window = win
    parent._settings_open = True          # 暂停主窗 300ms 置顶循环，免得盖住设置窗
    win.title("外观设置")
    win.geometry("440x430")
    win.resizable(False, False)
    win.attributes("-topmost", True)
    win.configure(bg="#111827")

    colors = {
        "bg": "#111827", "panel": "#1A2432", "input": "#0D141F",
        "text": "#E8ECF2", "muted": "#8D98AA", "accent": "#82AFFF",
        "border": "#263244", "danger": "#FF7B7B",
    }
    font = ("Microsoft YaHei UI", -11)
    small = ("Microsoft YaHei UI", -10)

    header = tk.Frame(win, bg=colors["bg"])
    header.pack(fill="x", padx=18, pady=(16, 4))
    tk.Label(header, text="外观设置", bg=colors["bg"], fg=colors["text"],
             font=(font[0], -17, "bold")).pack(side="left")

    tk.Label(win, text="调整即时生效并保存；手动改色会暂停变色龙吸色。",
             bg=colors["bg"], fg=colors["muted"], font=small,
             anchor="w").pack(fill="x", padx=18, pady=(0, 6))

    body = tk.Frame(win, bg=colors["bg"])
    body.pack(fill="both", expand=True, padx=18)

    swatches = {}
    entries = {}

    def commit():
        on_change(cfg)
        status.config(text="已应用", fg=colors["accent"])

    def set_color(key, value):
        value = valid_color(value, cfg[key])
        cfg[key] = value
        swatches[key].config(bg=value)
        entry = entries[key]
        entry.delete(0, "end")
        entry.insert(0, value)
        commit()

    def color_row(parent_frame, key, label_text):
        row = tk.Frame(parent_frame, bg=colors["bg"])
        row.pack(fill="x", pady=(7, 0))
        tk.Label(row, text=label_text, width=9, anchor="w", bg=colors["bg"],
                 fg=colors["muted"], font=small).pack(side="left")
        swatch = tk.Label(row, width=4, bg=cfg[key], relief="flat", bd=0,
                          cursor="hand2", font=small)
        swatch.pack(side="left", padx=(0, 8), ipady=5)
        entry = tk.Entry(row, bg=colors["input"], fg=colors["text"],
                         insertbackground=colors["text"], relief="flat", font=font)
        entry.pack(side="left", fill="x", expand=True, ipady=4)
        entries[key] = entry
        swatches[key] = swatch
        entry.insert(0, cfg[key])

        def pick(_event=None):
            _rgb, hexval = colorchooser.askcolor(color=cfg[key], parent=win,
                                                 title="选择颜色")
            if hexval:
                set_color(key, hexval)

        def typed(_event=None):
            value = entry.get().strip()
            if _COLOR_RE.match(value):
                set_color(key, value)
            else:
                status.config(text="颜色格式： #RRGGBB", fg=colors["danger"])

        swatch.bind("<Button-1>", pick)
        entry.bind("<KeyRelease>", typed)
        entry.bind("<Return>", typed)
        return row

    color_row(body, "bg", "背景颜色")
    color_row(body, "fg", "文字颜色")
    color_row(body, "accent", "强调颜色")

    # 模糊度 = 整窗不透明度：越低越透（桌面越"透"）；数值高 = 实底清晰。
    tk.Label(body, text="模糊度（透明度）", bg=colors["bg"], fg=colors["muted"],
             font=small, anchor="w").pack(fill="x", pady=(12, 0))
    alpha_row = tk.Frame(body, bg=colors["bg"])
    alpha_row.pack(fill="x")
    alpha_label = tk.Label(alpha_row, text="", width=5, anchor="e",
                           bg=colors["bg"], fg=colors["accent"], font=small)
    alpha_label.pack(side="right")

    updating = {"on": False}

    def on_scale(value):
        if updating["on"]:
            return
        percent = int(float(value))
        cfg["alpha"] = percent / 100.0
        alpha_label.config(text=f"{percent}%")
        commit()

    # command 先不挂：构造时 scale.set() 会触发回调，而底部状态标签还没创建。
    scale = tk.Scale(alpha_row, from_=30, to=100, orient="horizontal",
                     showvalue=False, bg=colors["bg"], fg=colors["text"],
                     troughcolor=colors["input"], highlightthickness=0,
                     borderwidth=0, sliderrelief="flat", activebackground=colors["accent"])
    scale.pack(side="left", fill="x", expand=True)
    scale.set(int(round(cfg["alpha"] * 100)))
    alpha_label.config(text=f"{int(round(cfg['alpha'] * 100))}%")
    tk.Label(body, text="越低越透；30% 以下文字会糊，不建议。",
             bg=colors["bg"], fg=colors["muted"], font=small,
             anchor="w").pack(fill="x", pady=(0, 6))

    # ---------- 预设 ----------
    preset_row = tk.Frame(body, bg=colors["bg"])
    preset_row.pack(fill="x", pady=(4, 0))

    def apply_preset(values):
        for key, value in values.items():
            cfg[key] = value
        refresh_controls()
        commit()

    def preset_button(text, values):
        btn = tk.Button(preset_row, text=text, relief="flat",
                        bg="#253247", fg=colors["text"], activebackground="#31435F",
                        activeforeground=colors["text"], font=small, padx=9, pady=3,
                        cursor="hand2", command=lambda: apply_preset(values))
        btn.pack(side="left", padx=(0, 6))
        return btn

    preset_button("深色", {k: DEFAULTS[k] for k in
                           ("bg", "fg", "muted", "accent", "warm", "code_bg", "alpha")})
    preset_button("浅色", dict(LIGHT_THEME))

    def do_reset():
        if on_reset is not None:
            on_reset()
        refresh_controls()
        status.config(text="已恢复默认", fg=colors["accent"])
        if chameleon_active:
            status.config(text="已恢复默认 · 变色龙重新吸色", fg=colors["accent"])

    def refresh_controls():
        updating["on"] = True
        try:
            for key in ("bg", "fg", "accent"):
                swatches[key].config(bg=cfg[key])
                entries[key].delete(0, "end")
                entries[key].insert(0, cfg[key])
            percent = int(round(cfg["alpha"] * 100))
            scale.set(percent)
            alpha_label.config(text=f"{percent}%")
        finally:
            updating["on"] = False

    def close():
        parent._settings_open = False
        parent._appearance_window = None
        win.destroy()

    footer = tk.Frame(win, bg=colors["bg"])
    footer.pack(fill="x", padx=18, pady=(10, 14))
    status = tk.Label(footer, text="", bg=colors["bg"], fg=colors["muted"],
                      font=small, anchor="w")
    status.pack(side="left", fill="x", expand=True)
    scale.config(command=on_scale)      # 状态标签就位后再挂回调

    def footer_button(text, command):
        widget = tk.Button(footer, text=text, command=command, relief="flat",
                           bg="#253247", fg=colors["text"], activebackground="#31435F",
                           activeforeground=colors["text"], font=small, padx=11, pady=5,
                           cursor="hand2")
        widget.pack(side="right", padx=(6, 0))
        return widget

    footer_button("关闭", close)
    footer_button("恢复默认", do_reset)

    win.protocol("WM_DELETE_WINDOW", close)
    win.update_idletasks()
    try:
        from .winfx import set_capture_excluded
        set_capture_excluded(win, True)
        win.bind("<Map>", lambda _e: set_capture_excluded(win, True))
    except Exception:
        pass
    win.after(50, lambda: (win.lift(), win.focus_force()))
    return win
