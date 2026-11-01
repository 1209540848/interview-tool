# -*- coding: utf-8 -*-
"""ui.py — 答题窗与对话历史属主（模块级单例状态 + Tk 答题窗）。

R2 属主：hist/cur/VIEW/MODE_TXT/PENDING_TXT 会被整体重绑定 → 唯一属主在本模块，
他模块经 import ui 后 ui.cur 等访问（勿 from-import 后 global 赋值，会改错副本）。
    show_answer_window(...) = code 主本搬入，启动几何/缩放手柄两段差异已收敛 →
profiles.ACTIVE.place_window / mount_window_extra（文本唯一副本在 profiles；
engine.main 激活后才被调用，ACTIVE 无空窗）。
GEO_FILE 收敛：原 dirname(abspath(__file__)) 包化后指向包内目录 → 钉 config.BASE_DIR
（包父目录 = repo 根，logs/.env/window-pos.txt 同层）。"""
import json
import os
import queue
import time

from . import appearance        # 外观配置（颜色/透明度）+ 设置面板
from . import profiles          # 窗口差异方法经 ACTIVE 调（差异文本唯一副本在 profiles）
from .config import BASE_DIR, LOG_DIR
from .log import log_event
from .notes import list_markdown_notes, read_markdown_note
from .prompt_settings import open_prompt_settings
from .push import push_answer
from .state import ACRYLIC, CHAMELEON, TYPING_STATE, push_on, shot_hide, stealth
from .winfx import (BG_DARK, WIN_ALPHA, sample_screen_rect, set_acrylic,
                    set_capture_excluded, set_no_activate, set_window_minimized)

GEO_FILE = os.path.join(BASE_DIR, "window-pos.txt")   # 收敛编辑：原 __file__ 相对推导（allow: code 1367）

_MATH_SYMBOLS = {
    "equiv": "≡", "cdots": "⋯", "ldots": "…", "dots": "…",
    "times": "×", "cdot": "·", "div": "÷", "pm": "±", "mp": "∓",
    "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "ne": "≠",
    "neq": "≠", "approx": "≈", "sim": "∼", "propto": "∝",
    "to": "→", "rightarrow": "→", "leftarrow": "←", "Rightarrow": "⇒",
    "Leftarrow": "⇐", "leftrightarrow": "↔", "Leftrightarrow": "⇔",
    "in": "∈", "notin": "∉", "subset": "⊂", "subseteq": "⊆",
    "supset": "⊃", "supseteq": "⊇", "cup": "∪", "cap": "∩",
    "land": "∧", "lor": "∨", "neg": "¬", "oplus": "⊕", "otimes": "⊗",
    "forall": "∀", "exists": "∃", "nexists": "∄", "infty": "∞",
    "sum": "∑", "prod": "∏", "int": "∫", "partial": "∂", "nabla": "∇",
    "sqrt": "√", "lfloor": "⌊", "rfloor": "⌋", "lceil": "⌈", "rceil": "⌉",
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "theta": "θ", "lambda": "λ", "mu": "μ", "pi": "π", "rho": "ρ",
    "sigma": "σ", "tau": "τ", "phi": "φ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ",
    "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Omega": "Ω",
    "quad": " ", "qquad": "  ", "left": "", "right": "",
    "bmod": " mod ", "mod": " mod ", "pmod": " mod ",
}
_SUPERSCRIPT = str.maketrans("0123456789+-=()ni", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ")
_SUBSCRIPT = str.maketrans(
    "0123456789+-=()aehijklmnoprstuvx",
    "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ",
)


def _math_to_unicode(source):
    """将常见 LaTeX 数学命令转为适合 Tk 文本窗口阅读的 Unicode 表达式。"""
    import re

    def group(value, start):
        if start >= len(value) or value[start] != "{":
            return None, start
        depth = 0
        for index in range(start, len(value)):
            if value[index] == "{":
                depth += 1
            elif value[index] == "}":
                depth -= 1
                if depth == 0:
                    return value[start + 1:index], index + 1
        return None, start

    def replace_one(value, command, render):
        token = "\\" + command
        pos = 0
        while True:
            index = value.find(token, pos)
            if index < 0:
                return value
            start = index + len(token)
            while start < len(value) and value[start].isspace():
                start += 1
            content, end = group(value, start)
            if content is None:
                pos = start
                continue
            replacement = render(convert(content))
            value = value[:index] + replacement + value[end:]
            pos = index + len(replacement)

    def replace_frac(value):
        token = "\\frac"
        pos = 0
        while True:
            index = value.find(token, pos)
            if index < 0:
                return value
            start = index + len(token)
            while start < len(value) and value[start].isspace():
                start += 1
            numerator, middle = group(value, start)
            if numerator is None:
                pos = start
                continue
            while middle < len(value) and value[middle].isspace():
                middle += 1
            denominator, end = group(value, middle)
            if denominator is None:
                pos = middle
                continue
            replacement = f"({convert(numerator)})/({convert(denominator)})"
            value = value[:index] + replacement + value[end:]
            pos = index + len(replacement)

    def script(value, marker, table, fallback):
        def braced(match):
            content = convert(match.group(1))
            if all(ord(char) in table for char in content):
                return content.translate(table)
            return fallback(content)

        def single(match):
            content = match.group(1)
            if ord(content) in table:
                return content.translate(table)
            return fallback(content)

        value = re.sub(re.escape(marker) + r"\{([^{}]+)\}", braced, value)
        return re.sub(re.escape(marker) + r"([A-Za-z0-9])", single, value)

    def convert(value):
        value = re.sub(r"\\begin\{(?:aligned|align\*?|gathered|cases)\}", "", value)
        value = re.sub(r"\\end\{(?:aligned|align\*?|gathered|cases)\}", "", value)
        value = replace_frac(value)
        for command in ("text", "mathrm", "operatorname", "mathbf", "mathit"):
            value = replace_one(value, command, lambda content: content)
        value = replace_one(value, "sqrt", lambda content: f"√({content})")
        value = replace_one(value, "pmod", lambda content: f"(mod {content})")
        value = replace_one(value, "hat", lambda content: content + "̂")
        value = replace_one(value, "bar", lambda content: content + "̄")
        value = replace_one(value, "overline", lambda content: content + "̄")
        value = replace_one(value, "vec", lambda content: content + "⃗")
        value = re.sub(r"\\([{}_%#$&])", r"\1", value)
        value = re.sub(r"\\[,;:!]", "", value)
        value = re.sub(r"\\([A-Za-z]+)",
                       lambda match: _MATH_SYMBOLS.get(match.group(1), match.group(1)),
                       value)
        value = value.replace("\\\\", "\n").replace("&", "").replace("~", " ")
        value = script(value, "^", _SUPERSCRIPT, lambda content: f"^({content})")
        value = script(value, "_", _SUBSCRIPT, lambda content: f"[{content}]")
        value = value.replace("*", " × ")
        for operator in ("≡", "≤", "≥", "≠", "≈", "∼", "∝", "→", "←", "⇒",
                         "⇐", "↔", "⇔", "∈", "∉", "⊂", "⊆", "⊃", "⊇", "×", "÷"):
            value = re.sub(rf"\s*{re.escape(operator)}\s*", f" {operator} ", value)
        value = re.sub(r"\s*=\s*", " = ", value)
        value = re.sub(r"\s*\+\s*", " + ", value)
        return "\n".join(re.sub(r"[ \t]+", " ", line).strip()
                         for line in value.splitlines()).strip()

    return convert(str(source or ""))


def _cancel_window_drag(root):
    """结束一次无边框窗口拖动，避免下次点击控件沿用旧起点。"""
    root._drag = None


def _begin_window_drag(root, event):
    """只允许普通背景区域启动拖动；按钮、正文等交互控件不参与。"""
    if getattr(event.widget, "_no_window_drag", False):
        _cancel_window_drag(root)
        return "break"
    root._drag = (event.x_root, event.y_root, root.winfo_x(), root.winfo_y())


def _move_window_drag(root, event):
    """按起点移动窗口；运动仍落在交互控件上时立即取消陈旧拖动。"""
    if getattr(event.widget, "_no_window_drag", False):
        _cancel_window_drag(root)
        return "break"
    drag = getattr(root, "_drag", None)
    if not drag:
        return "break"
    x0, y0, gx, gy = drag
    x = gx + event.x_root - x0
    y = gy + event.y_root - y0
    root.geometry(f"{x:+d}{y:+d}")
    return "break"


def show_answer_window(ui_q, prompt_store=None, on_prompt_apply=None,
                       hotkey_labels=None, helper=None, on_helper_shot=None):
    import tkinter as tk
    hotkey_labels = hotkey_labels or {}

    def hotkey(action, fallback):
        return hotkey_labels.get(action, fallback)

    root = tk.Tk()
    root._user_hidden = False                 # 隐藏按钮与全局显隐快捷键共用状态
    root._settings_open = False
    root._prompt_settings_window = None
    root.overrideredirect(True)                 # 无边框
    root.attributes("-topmost", True)           # 置顶
    # 启动几何：quiz 560x160 顶中 / code 恢复上次或 760x460（原文在 profiles.place_window）
    profiles.ACTIVE.place_window(root, load_window_geometry)
    # 中文优先字体：避免 Inter/Segoe UI 遇到中文时逐字 fallback，造成字形和基线不统一。
    # 负数尺寸 = 像素，在 Windows 不同 DPI 下比 point size 更可控。
    import tkinter.font as _tkfont
    _families = set(_tkfont.families(root))
    FAM = next((name for name in (
        "Noto Sans SC", "Microsoft YaHei UI", "Microsoft YaHei",
        "Segoe UI Variable Text", "Segoe UI",
    ) if name in _families), "TkDefaultFont")
    # 代码块等宽字体：Cascadia Code（Win 现代终端/VS 自带）→ Consolas（系统必有）兜底。
    # 正文 Segoe/Inter 是非等宽——代码/缩进混在正文里对不齐，面试读起来费劲
    CODE_FAM = ("Cascadia Code" if "Cascadia Code" in _families
                else "Consolas")
    COLORS = {
        "text": "#E8ECF2",
        "muted": "#8D98AA",
        "subtle": "#667085",
        "accent": "#82AFFF",
        "warm": "#E6B673",
        "success": "#9DD6A4",
        "warning": "#F0C674",
        "code_bg": "#151B26",
        "selection": "#263B5F",
    }
    notes_enabled = profiles.ACTIVE.key == "code"
    ui_theme = {"bg": BG_DARK, "fg": COLORS["text"],
                "muted": COLORS["muted"], "accent": COLORS["accent"]}
    # 用户自定义外观（appearance.json）；custom=True 时启动直接用，且暂停变色龙吸色。
    APPEAR = appearance.load()
    VIEW["panel"] = "answer"
    # 默认：深灰半透明底板（-alpha 整窗统一半透）。
    # 全透明键色方案在这台机器上首帧空白 + 125% DPI 下文字几乎不可见 + 窗口难找，
    # 已弃用（代码注释历史里也记过：旧全透明"杂色桌面上糊成一片"）。
    # 深灰半透：文字清晰可读，整窗低调不起眼。
    root.configure(bg=BG_DARK)
    root.wm_attributes("-alpha", WIN_ALPHA)
    if ACRYLIC["on"]:
        # 磨砂玻璃模式（--acrylic）：DWM accent 模糊做背景
        pass
    elif CHAMELEON["on"]:
        # 变色龙模式（--chameleon）：吸色需要实底（半透明会混合底色）
        root.wm_attributes("-alpha", 1.0)
        root.configure(bg="#8c8c8c")            # 初始底板，200ms 内被吸色替换

    # 防捕获常驻：窗口第一次出现前设置；窗口重新显示时仍需恢复 affinity。
    # 窗口 map 后 affinity 丢失，共享画面会露）。三层保险：
    #   1) withdraw 状态下先设一次，deiconify 后第一帧就带 affinity
    #   2) <Map> 每次窗口显示（含 F12 藏了再显示）都重设，防 map 重置
    #   3) 500ms 兜底再设一次
    root.withdraw()
    set_capture_excluded(root, stealth["on"])
    set_no_activate(root)                    # 永不抢焦点：测评页 blur 检测记「离开页面」的根因修复
    if ACRYLIC["on"]:
        set_acrylic(root)                    # 磨砂玻璃也走三层保险（此处 = withdraw 时）
    root.bind("<Map>", lambda _e: set_capture_excluded(root, stealth["on"]))
    root.bind("<Map>", lambda _e: set_no_activate(root), add="+")   # map 会重置 exstyle，每次显示重挂
    if ACRYLIC["on"]:
        root.bind("<Map>", lambda _e: set_acrylic(root), add="+")   # F12 藏了再显示 accent 仍在
    root.deiconify()
    root.after(200, lambda: set_no_activate(root))   # deiconify 首帧后兜底重挂（同 affinity 三层保险）
    # 透明键色已知坑：首帧合成可能不带键色 → 整窗空白（无边框窗一旦空白无法点击触发重绘）。
    # map 后 100ms 重设一次键色强制重绘，保证文字/状态栏一定可见
    root.after(100, root.update_idletasks)      # 首帧重绘兜底（防任何空窗情况）
    root.after(500, lambda: set_capture_excluded(root, stealth["on"]))
    if ACRYLIC["on"]:
        root.after(500, lambda: set_acrylic(root))

    # 置顶保活：周期 lift() + 重设 topmost。原因：PPT 放映 / 浏览器视频全屏 /
    # 视频会议"全屏"大多是无边框 topmost 窗口（伪全屏），创建晚会盖过早创建的置顶窗，
    # 周期 lift 能抢回最前。真·独占全屏（DX 模式切换，老游戏/个别播放器）DWM 停合成，
    # 任何窗口都浮不上来——那类场景靠副屏/别把答案窗放同一块屏，代码无解。
    def _keep_ontop():
        try:
            if (root.state() == "normal" and not root._settings_open
                    and not mini["on"]):
                root.lift()
                root.attributes("-topmost", True)
        except Exception:
            return
        root.after(300, _keep_ontop)
    root.after(300, _keep_ontop)

    # ---------- 变色龙模式（--chameleon）：200ms 吸色 + 亮度公式自动深浅文字 ----------
    cm = {"prev": None, "shown": None, "applied": None,
          "enabled": not APPEAR["custom"]}
    # prev=上一帧采样（EMA 平滑用）；shown=已应用的量化色（死区判定用）；
    # applied=上次真正下发到控件的 (bg,fg,...) 元组（相同则整轮跳过，防 5Hz 空重绘）；
    # enabled=用户手动定过外观时为 False（手动优先，吸色暂停）
    if CHAMELEON["on"]:
        def _sample_band():
            """采窗口外缘外侧一条像素带的平均色（避开自己窗口，防反馈循环）"""
            gx, gy = root.winfo_rootx(), root.winfo_rooty()
            w = root.winfo_width() or 200
            h = root.winfo_height() or 100
            sw = root.winfo_screenwidth()
            sh = root.winfo_screenheight()
            band_y = gy + h + 2            # 默认：窗口下边缘下方 2px
            if band_y + 4 > sh:            # 贴屏幕底 → 改采上边缘上方
                band_y = max(gy - 6, 0)
            x0 = max(gx, 0)
            x1 = min(gx + w, sw - 1)
            bw = max(min(x1 - x0, 200), 16)
            return sample_screen_rect(x0, band_y, bw, 4)

        def _hex(rgb):
            return "#%02x%02x%02x" % rgb

        def chameleon_tick():
            if not cm["enabled"]:
                # 用户手动定过外观 → 吸色让位；面板恢复默认后才重新接管
                root.after(200, chameleon_tick)
                return
            try:
                rgb = _sample_band()
                if rgb:
                    prev = cm["prev"]
                    if prev is not None:
                        # EMA 平滑 + 变化阈值 + 量化：背景动画/视频时不闪、不狂刷
                        rgb = tuple(int(prev[i] + 0.5 * (rgb[i] - prev[i])) for i in range(3))
                        if all(abs(rgb[i] - prev[i]) < 6 for i in range(3)):
                            rgb = prev
                        else:
                            rgb = tuple((rgb[i] // 8) * 8 for i in range(3))
                            # 死区（2026-09-23 实测修复）：采样均值在量化台阶边界附近摆动
                            # 时，量化结果会在相邻两色（如 192↔200）之间每 200ms 翻一次，
                            # 整窗 62% 像素以 5Hz 同闪——这就是「笔记/回答一直抖动」的真根因。
                            # 与「已应用的色」相差不足 2 个台阶（16）就不切色。
                            shown = cm["shown"]
                            if shown and max(abs(rgb[i] - shown[i]) for i in range(3)) < 16:
                                rgb = shown
                            else:
                                cm["shown"] = rgb
                    cm["prev"] = rgb
                    lum = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
                    light = lum > 145
                    fg = "#202630" if light else COLORS["text"]
                    muted = "#596273" if light else COLORS["muted"]
                    accent = "#2D5F9A" if light else COLORS["accent"]
                    warm = "#80551E" if light else COLORS["warm"]
                    code_rgb = tuple(max(0, c - 18) if light else min(255, c + 12)
                                     for c in rgb)
                    code_bg = _hex(code_rgb)
                    bg = _hex(rgb)
                    # 颜色没变 → 本轮一个控件、一个 tag 都不碰（config/tag_configure 即使
                    # 传相同值 Tk 也会重排重绘：200ms × 十余个控件 = 整窗持续刷，肉眼即抖）
                    target = (bg, fg, muted, accent, warm, code_bg)
                    if cm["applied"] == target:
                        root.after(200, chameleon_tick)
                        return
                    ui_theme.update(bg=bg, fg=fg, muted=muted, accent=accent)
                    root.configure(bg=bg)
                    a_text.config(bg=bg, fg=fg)
                    status.config(bg=bg, fg=muted)
                    my_label.config(bg=bg, fg=muted)
                    hide_btn.config(bg=bg, fg=muted)
                    if scene_btn is not None:
                        scene_btn.config(bg=bg, fg=accent)
                    if helper_panel is not None:
                        helper_panel.config(bg=bg)
                        helper_header.config(bg=bg)
                        helper_title.config(bg=bg, fg=accent)
                        helper_state.config(bg=bg, fg=muted)
                        helper_shot_btn.config(bg=bg, fg=accent)
                        helper_link_btn.config(bg=bg, fg=accent)
                        helper_copy_btn.config(bg=bg, fg=muted)
                        helper_fold_btn.config(bg=bg, fg=muted)
                        helper_body.config(bg=bg, fg=fg, selectforeground=fg)
                        helper_body.tag_configure("helper_meta", foreground=warm)
                        helper_body.tag_configure("helper_text", foreground=fg)
                        helper_body.tag_configure("helper_code", foreground=fg,
                                                  background=code_bg)
                    hide_btn.config(bg=bg, fg=muted)
                    a_text.tag_configure("q_tag", foreground=warm)
                    a_text.tag_configure("a_tag", foreground=accent)
                    a_text.tag_configure("code", foreground=fg, background=code_bg)
                    if notes_enabled:
                        content_host.config(bg=bg)
                        answer_frame.config(bg=bg)
                        notes_frame.config(bg=bg)
                        notes_text.config(bg=bg, fg=fg)
                        notes_text.tag_configure("code", foreground=fg, background=code_bg)
                        note_list.config(bg=bg, fg=muted, selectbackground=code_bg,
                                         selectforeground=accent)
                        _style_tabs(bg, fg, accent, muted, warm)
                    cm["applied"] = target      # 全套下发成功才记账；中途异常则下次重试
            except Exception:
                pass
            root.after(200, chameleon_tick)

        root.after(200, chameleon_tick)

    root.bind("<ButtonPress-1>", lambda event: _begin_window_drag(root, event))
    root.bind("<B1-Motion>", lambda event: _move_window_drag(root, event))
    root.bind("<ButtonRelease-1>", lambda _event: _cancel_window_drag(root))

    # code 版在同一个不抢焦点窗口内提供「回答 / 笔记 / 朋友」三页；quiz 保持原布局。
    tab_bar = answer_tab = notes_tab = friend_tab = None
    if notes_enabled:
        tab_bar = tk.Frame(root, bg=BG_DARK, height=30)
        tab_bar.pack(fill="x", side="top", padx=(12, 34), pady=(6, 2))
        answer_tab = tk.Label(tab_bar, text="回答", bg=BG_DARK, fg=COLORS["accent"],
                              font=(FAM, -12, "bold"), cursor="hand2", padx=7, pady=2)
        notes_tab = tk.Label(tab_bar, text="笔记", bg=BG_DARK, fg=COLORS["muted"],
                             font=(FAM, -12, "bold"), cursor="hand2", padx=7, pady=2)
        answer_tab.pack(side="left")
        notes_tab.pack(side="left", padx=(2, 0))
        answer_tab._no_window_drag = True
        notes_tab._no_window_drag = True

    # 内容宿主先创建、最后再 pack：朋友页（tab 模式）要作为它的子页，
    # pack 顺序仍保持「标签栏 → 状态行 → 内容区」的原有视觉层级。
    content_host = tk.Frame(root, bg=BG_DARK)
    answer_frame = tk.Frame(content_host, bg=BG_DARK)
    a_text = tk.Text(answer_frame, bg=BG_DARK, fg=COLORS["text"], wrap="word",
                     font=(FAM, -15), relief="flat", padx=14, pady=10,
                     spacing2=2, borderwidth=0, highlightthickness=0,
                     selectbackground=COLORS["selection"], selectforeground=COLORS["text"],
                     cursor="arrow", insertwidth=0, takefocus=False)  # 无光标闪烁、不抢焦点

    # 底部小字：对话索引 + 模式（低调灰，不抢眼）
    status = tk.Label(root, text="", bg=BG_DARK, fg=COLORS["subtle"],
                      font=(FAM, -10), anchor="w")
    status.pack(fill="x", side="bottom", padx=14, pady=(2, 8))
    # 你的回答转写显示行（自动模式：门控录到你的话 → 小字灰显示，供确认录到了什么）
    my_label = tk.Label(root, text="", bg=BG_DARK, fg=COLORS["muted"],
                        font=(FAM, -10), anchor="w", wraplength=540)
    my_label.pack(fill="x", side="bottom", padx=14, pady=(0, 3))

    # ---------- 朋友回复独立区域（局域网互助） ----------
    # 与 AI 答案分开：code 版把朋友页作为顶部「👤 朋友」标签，和「回答 / 笔记」并列，
    # 占满整个内容区（正文可拖选复制，代码块等宽渲染）；quiz 没有标签栏，保留底部
    # 小面板与折叠逻辑。
    helper_panel = helper_header = helper_title = helper_state = None
    helper_body = helper_shot_btn = helper_copy_btn = helper_fold_btn = None
    helper_link_btn = None
    helper_ui = {"open": True, "unread": 0, "answers": 0, "latest": "",
                 "compact": None, "ultra": None, "tabbed": False}
    helper_menu = None

    if helper is not None:
        helper_tabbed = bool(notes_enabled)
        helper_ui["tabbed"] = helper_tabbed
        if helper_tabbed:
            helper_panel = tk.Frame(content_host, bg=BG_DARK)
            friend_tab = tk.Label(tab_bar, text="👤 朋友", bg=BG_DARK,
                                  fg=COLORS["muted"], font=(FAM, -12, "bold"),
                                  cursor="hand2", padx=7, pady=2)
            friend_tab.pack(side="left", padx=(2, 0))
            friend_tab._no_window_drag = True
        else:
            helper_panel = tk.Frame(root, bg=BG_DARK)
            helper_panel.pack(fill="x", side="bottom", padx=10, pady=(2, 2))
        helper_header = tk.Frame(helper_panel, bg=BG_DARK)
        helper_header.pack(fill="x")
        helper_title = tk.Label(helper_header, text="👤 朋友", bg=BG_DARK,
                                fg=COLORS["accent"], font=(FAM, -11, "bold"))
        helper_title.pack(side="left")
        helper_state = tk.Label(helper_header, text="", bg=BG_DARK,
                                fg=COLORS["muted"], font=(FAM, -10))
        helper_state.pack(side="left", padx=(8, 0))
        helper_shot_btn = tk.Label(
            helper_header, text=f"📸 截图 {hotkey('helper_shot', 'Alt+7')}",
            bg=BG_DARK, fg=COLORS["accent"], font=(FAM, -10), cursor="hand2",
            padx=4, pady=1)
        helper_shot_btn.pack(side="right")
        helper_fold_btn = tk.Label(helper_header, text="收起", bg=BG_DARK,
                                   fg=COLORS["muted"], font=(FAM, -10),
                                   cursor="hand2", padx=4, pady=1)
        if not helper_tabbed:            # 标签页里没有折叠概念，整页常驻
            helper_fold_btn.pack(side="right", padx=(0, 8))
        helper_copy_btn = tk.Label(
            helper_header, text=f"复制 {hotkey('helper_copy', 'Alt+4')}",
            bg=BG_DARK, fg=COLORS["muted"], font=(FAM, -10), cursor="hand2",
            padx=4, pady=1)
        helper_copy_btn.pack(side="right", padx=(0, 8))
        helper_link_btn = tk.Label(
            helper_header, text=f"链接 {hotkey('helper_link', 'Alt+6')}",
            bg=BG_DARK, fg=COLORS["accent"], font=(FAM, -10), cursor="hand2",
            padx=4, pady=1)
        helper_link_btn.pack(side="right", padx=(0, 8))
        for _btn in (helper_shot_btn, helper_copy_btn, helper_fold_btn,
                     helper_link_btn):
            _btn._no_window_drag = True

        helper_body = tk.Text(
            helper_panel, height=3, bg=BG_DARK, fg=COLORS["text"], wrap="word",
            font=(FAM, -12), relief="flat", padx=8, pady=4, spacing2=1,
            borderwidth=0, highlightthickness=0,
            selectbackground=COLORS["selection"], selectforeground=COLORS["text"],
            cursor="xterm" if helper_tabbed else "arrow", insertwidth=0,
            takefocus=bool(helper_tabbed), state="disabled")
        if helper_tabbed:
            # 朋友页占满内容区：正文越高越好，长代码方便拖选
            helper_body.pack(fill="both", expand=True, padx=12, pady=(6, 8))
        else:
            helper_body.pack(fill="x", pady=(4, 0))
        helper_body.tag_configure("helper_meta", foreground=COLORS["warm"],
                                  font=(FAM, -10, "bold"))
        helper_body.tag_configure("helper_text", foreground=COLORS["text"])
        helper_body.tag_configure("helper_code", foreground=COLORS["text"],
                                  background=COLORS["code_bg"],
                                  font=(CODE_FAM, -11), lmargin1=10,
                                  lmargin2=10, rmargin=10, spacing1=5,
                                  spacing2=1, spacing3=5)
        helper_body._no_window_drag = True

        def helper_refresh_status():
            if helper_state is None:
                return
            ultra = bool(helper_ui.get("ultra"))
            if not helper.running:
                helper_state.config(text="· 关" if ultra else "· 已关闭")
                return
            online = helper.online_count()
            if ultra:
                helper_state.config(text=f"· {online}在线" if online else "· 待连接")
            else:
                helper_state.config(text=f"· {online} 台在线" if online
                                    else "· 等待朋友")

        def helper_flash(text):
            if helper_title is None:
                return
            prefix = "👤" if helper_ui.get("ultra") else "👤 朋友"
            helper_title.config(text=f"{prefix} · {text}")
            root.after(1800, lambda: helper_title.config(
                text="👤" if helper_ui.get("ultra") else "👤 朋友"))

        def helper_fold_text():
            if helper_ui["open"]:
                return "收起"
            if not helper_ui["unread"]:
                return "展开"
            return (f"展开·{helper_ui['unread']}" if helper_ui.get("compact")
                    else f"展开 · {helper_ui['unread']} 条新")

        def helper_tab_text():
            unread = helper_ui["unread"]
            return f"👤 朋友·{unread}" if unread else "👤 朋友"

        def helper_update_tab():
            """朋友标签的文字/高亮随未读数刷新（颜色由 _style_tabs 统一管）"""
            if friend_tab is None:
                return
            friend_tab.config(text=helper_tab_text())
            _style_tabs()

        def helper_friend_status():
            return (f"朋友 · 共 {helper_ui['answers']} 条回复 · "
                    f"拖选代码后按「复制」或 {hotkey('helper_copy', 'Alt+4')}")

        def helper_selection():
            if helper_body is None:
                return ""
            try:
                return helper_body.get("sel.first", "sel.last")
            except tk.TclError:
                return ""

        def helper_insert_rich(text):
            """朋友回复正文：围栏代码块等宽渲染；拖选复制到的就是纯代码（不带 ```）。"""
            import re as _re
            pos = 0
            for m in _re.finditer(r"```[^\n`]*\n(.*?)```", text, _re.S):
                helper_body.insert("end", text[pos:m.start()], "helper_text")
                helper_body.insert("end", m.group(1).rstrip("\n"), "helper_code")
                pos = m.end()
            rest = text[pos:]
            m_open = _re.search(r"```[^\n`]*\n", rest)  # 流式未闭合：先按代码块显示
            if m_open:
                helper_body.insert("end", rest[:m_open.start()], "helper_text")
                helper_body.insert("end", rest[m_open.end():].rstrip("\n"),
                                   "helper_code")
            else:
                helper_body.insert("end", rest, "helper_text")

        def helper_append_answer(rec):
            if helper_body is None:
                return
            if not isinstance(rec, dict):
                rec = {"text": str(rec)}
            text = str(rec.get("text") or "").strip()
            if not text:
                return
            qseq = rec.get("qseq")
            when = str(rec.get("ts") or "")
            meta = (when[11:16] + "  " if len(when) >= 16 else "") + "👤 朋友"
            if qseq:
                meta += f" · Q#{qseq}"
            helper_body.config(state="normal")
            if helper_ui["answers"]:
                helper_body.insert("end", "\n")
            helper_body.insert("end", meta + "\n", "helper_meta")
            helper_insert_rich(text + "\n")
            helper_body.config(state="disabled")
            helper_ui["latest"] = text
            helper_ui["answers"] += 1
            if helper_ui["tabbed"]:
                if VIEW["panel"] == "friend":
                    try:
                        at_bottom = helper_body.yview()[1] >= 0.999
                    except Exception:
                        at_bottom = True
                    if at_bottom:            # 用户往回翻旧回复时不抢滚动位置
                        helper_body.see("end")
                    helper_ui["unread"] = 0
                    helper_flash("新回复")
                    status.config(text=helper_friend_status())
                else:
                    helper_ui["unread"] += 1
                    helper_update_tab()
                return
            helper_body.see("end")
            if not helper_ui["open"]:
                if helper_ui["answers"] == 1:
                    helper_toggle(True)          # 第一条回复：自动展开
                else:
                    helper_ui["unread"] += 1
                    helper_fold_btn.config(text=helper_fold_text())
            else:
                helper_flash("新回复")

        def helper_toggle(force=None):
            if helper_body is None or helper_ui.get("tabbed"):
                return                              # 标签页模式整页常驻，无折叠

            want = (not helper_ui["open"]) if force is None else bool(force)
            helper_ui["open"] = want
            if want:
                helper_body.pack(fill="x", pady=(4, 0))
                helper_ui["unread"] = 0
                helper_body.see("end")
            else:
                helper_body.pack_forget()
            helper_fold_btn.config(text=helper_fold_text())

        def _helper_window_size():
            try:
                _w, _h = root.winfo_width(), root.winfo_height()
                if _w > 1 and _h > 1:
                    return _w, _h
            except Exception:
                pass
            try:
                _wh = root.geometry().split("+")[0]
                _w, _h = _wh.split("x")
                return int(_w), int(_h)
            except Exception:
                return 0, 0

        def helper_apply_layout(_event=None):
            # 窄窗只留图标/短标签，避免底部按钮挤出窗口；宽窗再补快捷键提示。
            if _event is not None and getattr(_event, "widget", None) is not root:
                return
            width, _height = _helper_window_size()
            if width <= 1:
                return
            compact = width < 470
            ultra = width < 390
            if (compact == helper_ui["compact"]
                    and ultra == helper_ui["ultra"]):
                return
            helper_ui["compact"] = compact
            helper_ui["ultra"] = ultra
            if compact:
                helper_shot_btn.config(text="📸")
                helper_link_btn.config(text="链接")
            else:
                helper_shot_btn.config(
                    text=f"📸 截图 {hotkey('helper_shot', 'Alt+7')}")
                helper_link_btn.config(
                    text=f"链接 {hotkey('helper_link', 'Alt+6')}")
            helper_update_copy_btn()
            helper_title.config(text="👤" if ultra else "👤 朋友")
            helper_fold_btn.config(text=helper_fold_text())
            helper_refresh_status()

        def helper_set_clipboard(text):
            root.clipboard_clear()
            root.clipboard_append(text)
            root.update_idletasks()

        def helper_update_copy_btn():
            """有选中时按钮变为「复制选中」——代码只挑一段时不用整条回复都复制。"""
            if helper_copy_btn is None:
                return
            sel = bool(helper_selection())
            if helper_ui.get("compact"):
                want = "复制选中" if sel else "复制"
            else:
                want = ("复制选中" if sel
                        else f"复制 {hotkey('helper_copy', 'Alt+4')}")
            if helper_copy_btn.cget("text") != want:   # 选中事件高频，同值不重配
                helper_copy_btn.config(text=want)

        def helper_copy_selection(_event=None):
            _cancel_window_drag(root)
            text = helper_selection()
            if not text:
                return "break"
            helper_set_clipboard(text)
            helper_flash("已复制选中")
            return "break"

        def helper_copy_all(_event=None):
            _cancel_window_drag(root)
            if helper_body is None:
                return "break"
            text = helper_body.get("1.0", "end-1c").strip()
            if not text:
                helper_flash("暂无回复")
                return "break"
            helper_set_clipboard(text)
            helper_flash("已复制全部")
            return "break"

        def helper_copy_latest(_event=None):
            _cancel_window_drag(root)
            sel = helper_selection()
            if sel:                       # 朋友页里拖选了一段（比如代码）→ 只复制选中
                helper_set_clipboard(sel)
                helper_flash("已复制选中")
                return "break"
            text = helper_ui["latest"]
            if not text:
                helper_flash("暂无回复")
                return "break"
            helper_set_clipboard(text)
            helper_flash("已复制")
            return "break"

        def helper_show_menu(event):
            _cancel_window_drag(root)
            if helper_menu is None:
                return "break"
            try:
                helper_menu.entryconfigure(
                    0, state="normal" if helper_selection() else "disabled")
                helper_menu.tk_popup(event.x_root, event.y_root)
            finally:
                try:
                    helper_menu.grab_release()
                except Exception:
                    pass
            return "break"

        def helper_copy_link(_event=None):
            _cancel_window_drag(root)
            url = helper.url if helper.running else ""
            if not url:
                helper_flash("互助已关闭")
                return "break"
            root.clipboard_clear()
            root.clipboard_append(url)
            root.update_idletasks()
            helper_flash("链接已复制")
            return "break"

        def helper_send_shot(_event=None):
            _cancel_window_drag(root)
            if not helper.running:
                helper_flash("互助已关闭")
                return "break"
            if on_helper_shot is None:
                return "break"
            try:
                on_helper_shot()
            except Exception as exc:
                helper_flash("截图失败")
                print(f"⚠️ 互助截图失败: {exc}", flush=True)
            return "break"

        def helper_toggle_link(_event=None):
            _cancel_window_drag(root)
            try:
                on = helper.toggle()
            except Exception as exc:
                helper_state.config(text="· 开启失败")
                print(f"⚠️ 互助开启失败: {exc}", flush=True)
                return "break"
            if on:
                print(f"👥 互助已开启: {helper.url}", flush=True)
            else:
                print("👥 互助已关闭", flush=True)
            helper_refresh_status()
            return "break"

        helper_copy_btn.bind("<Button-1>", helper_copy_latest)
        helper_link_btn.bind("<Button-1>", helper_copy_link)
        helper_shot_btn.bind("<Button-1>", helper_send_shot)
        helper_fold_btn.bind("<Button-1>",
                             lambda e: (helper_toggle(), "break")[1])
        helper_body.bind("<Button-3>", helper_show_menu)
        helper_body.bind("<Control-c>", helper_copy_selection)
        helper_body.bind("<<Selection>>", lambda _e: helper_update_copy_btn())

        # 右键菜单：无边框窗是不抢焦点窗口（WS_EX_NOACTIVATE），Ctrl+C 不一定
        # 落到这里；右键菜单用鼠标即可，专门解决「代码选中了却复制不走」。
        helper_menu = tk.Menu(root, tearoff=0)
        helper_menu.add_command(label="复制选中", command=helper_copy_selection)
        helper_menu.add_command(label="复制最近一条", command=helper_copy_latest)
        helper_menu.add_command(label="复制全部", command=helper_copy_all)
        helper_menu.add_separator()
        helper_menu.add_command(
            label=f"📸 截图给朋友 {hotkey('helper_shot', 'Alt+7')}",
            command=helper_send_shot)
        for _btn, _base in ((helper_shot_btn, "accent"),
                            (helper_link_btn, "accent"),
                            (helper_copy_btn, "muted"),
                            (helper_fold_btn, "muted")):
            _btn.bind("<Enter>", lambda e, b=_btn: b.config(
                fg=ui_theme["fg"]))
            _btn.bind("<Leave>", lambda e, b=_btn, k=_base: b.config(
                fg=ui_theme[k]))

        def helper_tick():
            try:
                helper_refresh_status()
            finally:
                root.after(2000, helper_tick)

        # 矮窗（例如上次保存 300x245）先收起正文，只留标题行，避免答案区被挤没；
        # 第一条朋友回复会自动展开。标签页模式没有折叠，正文常驻。
        _w0, _h0 = _helper_window_size()
        if not helper_tabbed and _h0 and _h0 < 300:
            helper_toggle(False)
        helper_apply_layout()
        root.bind("<Configure>", helper_apply_layout, add="+")
        helper_refresh_status()
        root.after(500, helper_tick)
        root.after(80, helper_apply_layout)

    content_host.pack(fill="both", expand=True)
    answer_frame.pack(fill="both", expand=True)
    a_text.pack(fill="both", expand=True)      # 答案正文（创建提前后别忘了 pack）

    notes_frame = note_list = notes_text = None
    note_state = {"docs": [], "selected": None}
    if notes_enabled:
        notes_frame = tk.Frame(content_host, bg=BG_DARK)
        note_list = tk.Listbox(notes_frame, width=22, bg=BG_DARK, fg=COLORS["muted"],
                               selectbackground=COLORS["code_bg"],
                               selectforeground=COLORS["accent"],
                               font=(FAM, -11), relief="flat", borderwidth=0,
                               highlightthickness=0, activestyle="none",
                               exportselection=False, takefocus=False)
        note_list.pack(side="left", fill="y", padx=(12, 4), pady=(8, 8))
        notes_text = tk.Text(notes_frame, bg=BG_DARK, fg=COLORS["text"], wrap="word",
                             font=(FAM, -15), relief="flat", padx=14, pady=10,
                             spacing2=2, borderwidth=0, highlightthickness=0,
                             selectbackground=COLORS["selection"],
                             selectforeground=COLORS["text"], cursor="arrow",
                             insertwidth=0, state="disabled", takefocus=False)
        notes_text.pack(side="left", fill="both", expand=True)
        note_list._no_window_drag = True
        notes_text._no_window_drag = True

    # 右上角隐藏按钮：只隐藏窗口而不退出进程；全局显隐快捷键可随时恢复。
    hide_btn = tk.Label(root, text="—", bg=BG_DARK, fg=COLORS["muted"],
                        font=(FAM, -13, "bold"), cursor="hand2", padx=5, pady=1)
    hide_btn.place(relx=1.0, x=-10, y=6, anchor="ne")
    hide_btn._no_window_drag = True

    def on_hide(_event=None):
        _cancel_window_drag(root)
        # 迷你/提词器状态下保存"正常形态"的几何，重启回到可用的常规大小
        save_window_geometry(getattr(root, "_geom_for_save", root.geometry)())
        root._user_hidden = True
        root.withdraw()
        log_event({"type": "window_hide", "reason": "hide-btn"})
        print(f"窗口已隐藏（按 {hotkey('toggle_window', 'F4')} 显示）", flush=True)

    hide_btn.bind("<Button-1>", lambda _e: (on_hide(), "break")[1])
    hide_btn.bind("<Enter>", lambda _e: hide_btn.config(fg=ui_theme["accent"]))
    hide_btn.bind("<Leave>", lambda _e: hide_btn.config(fg=ui_theme["muted"]))

    # 右上角 🎨：外观设置（背景色 / 文字颜色 / 透明度），即时生效并落盘。
    appearance_btn = tk.Label(root, text="🎨", bg=BG_DARK, fg=COLORS["accent"],
                              font=(FAM, -12), cursor="hand2", padx=3, pady=1)
    appearance_btn.place(relx=1.0, x=-38, y=7, anchor="ne")
    appearance_btn._no_window_drag = True

    def on_appearance_change(cfg):
        cfg["custom"] = True                 # 手动定过 → 变色龙暂停（apply_theme 里关门）
        appearance.save(cfg)
        apply_theme(cfg)

    def on_appearance_reset():
        cfg = APPEAR
        cfg.update(appearance.DEFAULTS)
        cfg["custom"] = False                # 恢复默认 = 把颜色控制权交还变色龙
        appearance.save(cfg)
        apply_theme(cfg)
        if CHAMELEON["on"]:
            cm["prev"] = cm["shown"] = cm["applied"] = None
            try:
                root.wm_attributes("-alpha", 1.0)   # 吸色需要实底
            except Exception:
                pass

    def open_appearance(_event=None):
        _cancel_window_drag(root)
        appearance.open_appearance_panel(
            root, APPEAR, on_appearance_change, on_appearance_reset,
            CHAMELEON["on"])
        return "break"

    appearance_btn.bind("<Button-1>", open_appearance)
    appearance_btn.bind("<Enter>", lambda _e: appearance_btn.config(fg=ui_theme["fg"]))
    appearance_btn.bind("<Leave>", lambda _e: appearance_btn.config(fg=ui_theme["accent"]))

    # 场景是单选全局状态；点击当前名称打开可输入的独立窗口，悬浮答案窗自身仍不抢焦点。
    scene_btn = None
    if prompt_store is not None:
        def scene_text(scene):
            name = scene["name"]
            return f"{name[:16]}{'…' if len(name) > 16 else ''}  ⚙"

        scene_btn = tk.Label(root, text=scene_text(prompt_store.get_active_scene()),
                             bg=BG_DARK, fg=COLORS["accent"], font=(FAM, -10),
                             cursor="hand2", padx=4, pady=2)
        scene_btn.place(relx=1.0, x=-62, y=7, anchor="ne")   # 让位给右侧 🎨 / —
        scene_btn._no_window_drag = True

        def prompt_applied(scene):
            scene_btn.config(text=scene_text(scene))
            if on_prompt_apply is not None:
                on_prompt_apply(scene)

        def open_settings(_event=None):
            _cancel_window_drag(root)
            open_prompt_settings(root, prompt_store, prompt_applied)
            return "break"

        scene_btn.bind("<Button-1>", open_settings)
        scene_btn.bind("<Enter>", lambda _e: scene_btn.config(fg=ui_theme["fg"]))
        scene_btn.bind("<Leave>", lambda _e: scene_btn.config(fg=ui_theme["accent"]))

    # 右下角缩放柄最后创建，避免被正文控件盖住（code 独有；quiz 无手柄）。
    profiles.ACTIVE.mount_window_extra(root, FAM, BG_DARK)
    # 文字 tag：状态行按阶段着色、提问/回答标题区分
    _status_font = (FAM, -11, "bold")
    _section_font = (FAM, -12, "bold")
    a_text.tag_configure("st_rec", foreground=COLORS["warning"], font=_status_font,
                         spacing3=5)
    a_text.tag_configure("st_work", foreground=COLORS["accent"], font=_status_font,
                         spacing3=5)
    a_text.tag_configure("st_done", foreground=COLORS["success"], font=_status_font,
                         spacing3=5)
    a_text.tag_configure("st_idle", foreground=COLORS["muted"], font=_status_font,
                         spacing3=5)
    a_text.tag_configure("q_tag", foreground=COLORS["warm"], font=_section_font,
                         spacing1=8, spacing3=3)
    a_text.tag_configure("a_tag", foreground=COLORS["accent"], font=_section_font,
                         spacing1=8, spacing3=3)
    # 代码块样式：等宽字体 + 比底板深一档的底色 + 内缩进 → 和正文一眼分层，代码缩进对齐清晰
    a_text.tag_configure("code", font=(CODE_FAM, -14), foreground=COLORS["text"],
                         background=COLORS["code_bg"], lmargin1=12, lmargin2=12,
                         rmargin=12, spacing1=6, spacing2=1, spacing3=6)

    def configure_markdown_tags(tx):
        tx.tag_configure("md_h1", font=(FAM, -19, "bold"), foreground=COLORS["accent"],
                         spacing1=10, spacing3=5)
        tx.tag_configure("md_h2", font=(FAM, -17, "bold"), foreground=COLORS["accent"],
                         spacing1=9, spacing3=4)
        tx.tag_configure("md_h3", font=(FAM, -15, "bold"), foreground=COLORS["warm"],
                         spacing1=7, spacing3=3)
        tx.tag_configure("md_bold", font=(FAM, -15, "bold"))
        tx.tag_configure("md_inline", font=(CODE_FAM, -14),
                         background=COLORS["code_bg"], foreground=COLORS["text"])
        tx.tag_configure("md_math_inline", font=(CODE_FAM, -14),
                         foreground=COLORS["warm"])
        tx.tag_configure("md_math", font=(CODE_FAM, -15), foreground=COLORS["warm"],
                         background=COLORS["code_bg"], lmargin1=18, lmargin2=18,
                         rmargin=12, spacing1=7, spacing2=2, spacing3=7)
        tx.tag_configure("md_quote", foreground=COLORS["muted"],
                         lmargin1=12, lmargin2=12)

    configure_markdown_tags(a_text)
    if notes_enabled:
        notes_text.tag_configure("code", font=(CODE_FAM, -14), foreground=COLORS["text"],
                                  background=COLORS["code_bg"], lmargin1=12, lmargin2=12,
                                  rmargin=12, spacing1=6, spacing2=1, spacing3=6)
        configure_markdown_tags(notes_text)

    # ---------- 视图跟随策略（流式可读性的关键） ----------
    # follow=True：新内容到达滚到尾部（打字机）；False：用户手动滚上去过 → 锁住他的位置。
    # 贴底自动恢复跟随（滚回底部就是要看最新）；滚轮是唯一手动滚动入口（无边框窗焦点常
    # 不在文字区，键盘滚动基本不触发），所以只需在滚轮回调里重判。
    SCROLL = {"follow": True}
    A_WATCH = {"ts": 0.0}        # 最后一次流式帧时刻：静默兜底判定"其实已生成完"用
    SILENT_DONE_SEC = 2.0        # 末帧后静默多久算生成结束（只兜底真答案以省略号结尾的罕见情况）

    def _view_top_line():
        """视图顶部行号（重绘前存、重绘后恢复）：行号在流式增长时稳定，恢复精确"""
        try:
            return int(a_text.index("@0,0").split(".")[0])
        except Exception:
            return 1

    def _sync_follow():
        """滚轮/拖动后重判跟随态：贴底 → 恢复跟随，否则锁定"""
        try:
            SCROLL["follow"] = a_text.yview()[1] >= 0.999
        except Exception:
            pass

    def insert_md(tx, text):
        """轻量渲染标题、列表、粗体、行内代码、公式、引用和围栏代码块。

        流式未闭合容忍：生成中代码块的闭围栏还没到（只有开围栏）时，旧正则配不上对
        → 围栏行和代码原文裸上屏，等闭围栏到了才"啪"地跳变成块（code 笔试全程可见）。
        现在把「行首开围栏 → 文末」这段直接按代码块渲染并吞掉围栏行：全程无裸露，
        闭围栏到达后走正常配对分支，两条路径渲染结果逐字一致（都 rstrip 掉尾部空行）。"""
        import re as _re

        def insert_inline(value, base_tag=None):
            pos = 0
            pattern = _re.compile(
                r"\*\*([^*\n]+)\*\*|`([^`\n]+)`|\$(?!\$)([^$\n]+)\$")
            for match in pattern.finditer(value):
                tags = (base_tag,) if base_tag else ()
                tx.insert("end", value[pos:match.start()], tags)
                if match.group(1) is not None:
                    bold_tags = tuple(t for t in (base_tag, "md_bold") if t)
                    tx.insert("end", match.group(1), bold_tags)
                elif match.group(2) is not None:
                    inline_tags = tuple(t for t in (base_tag, "md_inline") if t)
                    tx.insert("end", match.group(2), inline_tags)
                else:
                    math_tags = tuple(t for t in (base_tag, "md_math_inline") if t)
                    tx.insert("end", _math_to_unicode(match.group(3)), math_tags)
                pos = match.end()
            tags = (base_tag,) if base_tag else ()
            tx.insert("end", value[pos:], tags)

        def insert_plain_lines(value):
            for raw in value.splitlines(keepends=True):
                line = raw.rstrip("\r\n")
                newline = raw[len(line):]
                heading = _re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
                if heading:
                    level = min(len(heading.group(1)), 3)
                    insert_inline(heading.group(2), f"md_h{level}")
                elif _re.match(r"^\s*>\s?", line):
                    content = _re.sub(r"^\s*>\s?", "", line)
                    insert_inline(content, "md_quote")
                elif _re.match(r"^\s*[-+*]\s+", line):
                    indent = line[:len(line) - len(line.lstrip())]
                    content = _re.sub(r"^\s*[-+*]\s+", "", line)
                    tx.insert("end", indent + "• ")
                    insert_inline(content)
                elif _re.fullmatch(r"\s*(?:---+|___+|\*\*\*+)\s*", line):
                    tx.insert("end", "────────────────", "md_quote")
                else:
                    insert_inline(line)
                tx.insert("end", newline)

        def insert_plain(value):
            pos = 0
            for match in _re.finditer(r"\$\$(.*?)\$\$", value, _re.S):
                insert_plain_lines(value[pos:match.start()])
                tx.insert("end", _math_to_unicode(match.group(1)), "md_math")
                pos = match.end()
            rest = value[pos:]
            open_math = _re.search(r"\$\$(.*)$", rest, _re.S)
            if open_math:
                insert_plain_lines(rest[:open_math.start()])
                tx.insert("end", _math_to_unicode(open_math.group(1)), "md_math")
            else:
                insert_plain_lines(rest)

        pos = 0
        for m in _re.finditer(r"```[^\n`]*\n(.*?)```", text, _re.S):
            insert_plain(text[pos:m.start()])                    # 围栏前 Markdown 正文
            tx.insert("end", m.group(1).rstrip("\n"), "code")   # 块内：吞围栏行，等宽渲染
            pos = m.end()
        rest = text[pos:]
        m_open = _re.search(r"```[^\n`]*\n", rest)              # 未闭合的开围栏行（含换行才算）
        if m_open:
            insert_plain(rest[:m_open.start()])
            tx.insert("end", rest[m_open.end():].rstrip("\n"), "code")
        else:
            insert_plain(rest)

    def _style_tabs(bg=None, fg=None, accent=None, muted=None, warm=None):
        if not notes_enabled:
            return
        bg = ui_theme["bg"] if bg is None else bg
        fg = ui_theme["fg"] if fg is None else fg
        accent = ui_theme["accent"] if accent is None else accent
        muted = ui_theme["muted"] if muted is None else muted
        warm = COLORS["warm"] if warm is None else warm
        tab_bar.config(bg=bg)
        answer_tab.config(bg=bg,
                          fg=accent if VIEW["panel"] == "answer" else muted,
                          font=(FAM, -12, "bold"))
        notes_tab.config(bg=bg,
                         fg=accent if VIEW["panel"] == "notes" else muted,
                         font=(FAM, -12, "bold"))
        if friend_tab is not None:
            if VIEW["panel"] == "friend":
                fg_friend = accent
            elif helper_ui["unread"]:
                fg_friend = warm       # 没在朋友页时用暖色 + 数字提示新回复
            else:
                fg_friend = muted
            friend_tab.config(bg=bg, fg=fg_friend,
                              text=helper_tab_text(),
                              font=(FAM, -12, "bold"))

    def apply_theme(cfg):
        """把外观配置铺到整窗（控件底色 / 文字色 / 文字 tag / 整窗透明度）。

        外观面板每次改动都走这里；custom=True 时变色龙吸色暂停（手动优先）。
        """
        bg = cfg["bg"]
        fg = cfg["fg"]
        muted = cfg["muted"]
        accent = cfg["accent"]
        warm = cfg["warm"]
        code_bg = cfg["code_bg"]
        COLORS.update(text=fg, muted=muted, accent=accent, warm=warm,
                      code_bg=code_bg)
        ui_theme.update(bg=bg, fg=fg, muted=muted, accent=accent)
        root.configure(bg=bg)
        try:
            root.wm_attributes("-alpha", cfg["alpha"])
        except Exception:
            pass
        a_text.config(bg=bg, fg=fg, selectforeground=fg)
        status.config(bg=bg, fg=muted)
        my_label.config(bg=bg, fg=muted)
        hide_btn.config(bg=bg, fg=muted)
        appearance_btn.config(bg=bg, fg=accent)
        if scene_btn is not None:
            scene_btn.config(bg=bg, fg=accent)
        if helper_panel is not None:
            helper_panel.config(bg=bg)
            helper_header.config(bg=bg)
            helper_title.config(bg=bg, fg=accent)
            helper_state.config(bg=bg, fg=muted)
            helper_shot_btn.config(bg=bg, fg=accent)
            helper_link_btn.config(bg=bg, fg=accent)
            helper_copy_btn.config(bg=bg, fg=muted)
            helper_fold_btn.config(bg=bg, fg=muted)
            helper_body.config(bg=bg, fg=fg, selectforeground=fg,
                               selectbackground=COLORS["selection"])
            helper_body.tag_configure("helper_meta", foreground=warm)
            helper_body.tag_configure("helper_text", foreground=fg)
            helper_body.tag_configure("helper_code", foreground=fg,
                                      background=code_bg)
        a_text.tag_configure("st_rec", foreground=COLORS["warning"])
        a_text.tag_configure("st_work", foreground=accent)
        a_text.tag_configure("st_done", foreground=COLORS["success"])
        a_text.tag_configure("st_idle", foreground=muted)
        a_text.tag_configure("q_tag", foreground=warm)
        a_text.tag_configure("a_tag", foreground=accent)
        a_text.tag_configure("code", foreground=fg, background=code_bg)
        configure_markdown_tags(a_text)      # 标题/加粗/行内代码等 tag 按新配色重配
        if notes_enabled:
            content_host.config(bg=bg)
            answer_frame.config(bg=bg)
            notes_frame.config(bg=bg)
            notes_text.config(bg=bg, fg=fg, selectforeground=fg)
            notes_text.tag_configure("code", foreground=fg, background=code_bg)
            configure_markdown_tags(notes_text)
            note_list.config(bg=bg, fg=muted, selectbackground=code_bg,
                             selectforeground=accent)
            _style_tabs(bg, fg, accent, muted, warm)
        cm["enabled"] = not cfg["custom"]    # 手动定过外观 → 吸色让位
        if cm["enabled"]:
            cm["applied"] = None             # 下次 tick 必定重刷

    if APPEAR["custom"]:
        apply_theme(APPEAR)                  # 上次手动定过外观：启动即用

    def _render_note(doc):
        if not notes_enabled:
            return
        notes_text.config(state="normal")
        notes_text.delete("1.0", "end")
        try:
            content = read_markdown_note(doc["path"])
            insert_md(notes_text, content or "（空笔记）")
        except (OSError, ValueError) as exc:
            notes_text.insert("end", f"笔记读取失败：{exc}")
        notes_text.config(state="disabled")
        notes_text.see("1.0")
        note_state["selected"] = doc["label"]
        status.config(text=f"笔记 · {doc['label']} · 共 {len(note_state['docs'])} 篇")

    def _reload_notes():
        if not notes_enabled:
            return
        selected = note_state.get("selected")
        docs = list_markdown_notes()
        note_state["docs"] = docs
        note_list.delete(0, "end")
        for doc in docs:
            note_list.insert("end", doc["label"])
        if not docs:
            notes_text.config(state="normal")
            notes_text.delete("1.0", "end")
            notes_text.insert("end", "notes 目录中还没有 Markdown 文档。")
            notes_text.config(state="disabled")
            status.config(text="笔记 · 0 篇")
            note_state["selected"] = None
            return
        index = next((i for i, doc in enumerate(docs) if doc["label"] == selected), 0)
        note_list.selection_clear(0, "end")
        note_list.selection_set(index)
        note_list.see(index)
        _render_note(docs[index])

    def _on_note_select(_event=None):
        if not notes_enabled:
            return "break"
        selected = note_list.curselection()
        if selected:
            index = selected[0]
            if 0 <= index < len(note_state["docs"]):
                _render_note(note_state["docs"][index])
        return "break"

    def _step_note(delta):
        """笔记页 ↑/↓ 切上一篇/下一篇；到首尾后停住，不循环跳转。"""
        if not notes_enabled:
            return
        if not note_state["docs"]:
            _reload_notes()
            return
        selected = note_list.curselection()
        current = selected[0] if selected else 0
        target = max(0, min(current + delta, len(note_state["docs"]) - 1))
        note_list.selection_clear(0, "end")
        note_list.selection_set(target)
        note_list.see(target)
        _render_note(note_state["docs"][target])

    def render(anchor="keep"):
        """按历史索引 + 阶段重绘文字区（提问和回答同屏）。

        anchor 决定重绘后视图停哪（原实现无条件 see("1.0")，流式时每帧都被拽回顶部，
        答案一超一屏就永远看不到正在生成的尾部——这里是「流式看着像卡住」的真根因）：
          "tail" → 滚到底部，跟住正在生成的答案尾部（打字机效果）
          "top"  → 滚回顶部，从提问读起（新问题 / 翻历史）
          "keep" → 原位不动（阶段提示这类无关重绘，不打扰正在读的位置）
        anchor="tail" 在用户手动滚上去过（follow=False）时自动降级为 "keep"：
        否则用户往回翻的每一帧都会被拽回底部，等于不让读。"""
        global cur
        _anchor = "keep" if (anchor == "tail" and not SCROLL["follow"]) else anchor
        if _anchor == "top":
            SCROLL["follow"] = True          # 回顶部 = 回到跟随态（否则后续流式停在锁定位不跟）
        _top = _view_top_line() if _anchor == "keep" else 1
        a_text.delete("1.0", "end")
        blank = VIEW.get("blank", False)
        st = VIEW["stage"]
        if blank:
            a_text.insert("end", f"●  新题待命  ·  {hotkey('vision', profiles.ACTIVE.vision_retry)} 截图\n",
                          "st_idle")
        elif st == "rec":
            a_text.insert("end", f"●  录音中  ·  {hotkey('record_stop', 'Alt+F2')} 结束\n",
                          "st_rec")
        elif st == "transcribing":
            a_text.insert("end", "◌  正在转写\n", "st_work")
        elif st == "answering":
            a_text.insert("end", "◌  正在生成\n", "st_work")
        elif st == "done":
            a_text.insert("end", f"✓  回答完成  ·  {hotkey('record_start', 'Alt+F1')} 录下一题\n",
                          "st_done")
        else:
            a_text.insert("end", f"●  待命  ·  {hotkey('record_start', 'Alt+F1')} 开始录音\n",
                          "st_idle")
        idx = len(hist) - 1 if cur < 0 else cur
        if not blank and 0 <= idx < len(hist):
            item = hist[idx]
            a_text.insert("end", "\n提问\n", "q_tag")
            insert_md(a_text, item["q"] + "\n\n")
            a_text.insert("end", "回答\n", "a_tag")
            insert_md(a_text, (item["a"] or "（生成中…）") + "\n")
        if _anchor == "tail":
            a_text.see("end")
        elif _anchor == "top":
            a_text.yview_moveto(0.0)
        else:
            # 行号复位：流式期间前文（提问 + 已生成的答案）不变、行号稳定，
            # 按行号恢复比重绘前记 yview 比例精确（比例会随总行数增长而漂移）
            try:
                a_text.yview(f"{_top}.0")
            except Exception:
                pass
        n = len(hist)
        pos = ("新题待命" if blank else
               (f"对话 {min(idx + 1, max(n, 1))}/{n}" if n else "对话 0/0"))
        if not notes_enabled or VIEW["panel"] == "answer":
            status.config(text=f"{pos} · {MODE_TXT}{PENDING_TXT}")

    def switch_panel(panel, answer_anchor="keep"):
        """通过窗口顶部按钮切页；问答历史、识图和待输入答案完全不动。"""
        if not notes_enabled:
            return
        allowed = ("answer", "notes")
        if friend_tab is not None:
            allowed = ("answer", "notes", "friend")
        if panel not in allowed:
            return
        if panel != "answer" and tp["on"]:
            set_teleprompter(False)
        VIEW["panel"] = panel
        answer_frame.pack_forget()
        if notes_frame is not None:
            notes_frame.pack_forget()
        if helper_ui.get("tabbed") and helper_panel is not None:
            helper_panel.pack_forget()
        if panel == "notes":
            notes_frame.pack(fill="both", expand=True)
            _reload_notes()              # 每次打开都重扫，外部增删改无需重启
        elif panel == "friend":
            helper_panel.pack(fill="both", expand=True)
            helper_ui["unread"] = 0
            if helper_body is not None:
                helper_body.see("end")
            status.config(text=helper_friend_status())
        else:
            answer_frame.pack(fill="both", expand=True)
            render(answer_anchor)
        _style_tabs()

    if notes_enabled:
        def _switch_panel_click(panel):
            _cancel_window_drag(root)
            switch_panel(panel)
            return "break"

        answer_tab.bind("<ButtonPress-1>", lambda _e: _switch_panel_click("answer"))
        notes_tab.bind("<ButtonPress-1>", lambda _e: _switch_panel_click("notes"))
        if friend_tab is not None:
            friend_tab.bind("<ButtonPress-1>",
                            lambda _e: _switch_panel_click("friend"))
        note_list.bind("<<ListboxSelect>>", _on_note_select)

    def nav(delta):
        """↑↓ 翻历史：在当前索引基础上 ±1，越界夹住"""
        global cur
        if not hist:
            return
        if VIEW.get("blank"):
            VIEW["blank"] = False
            cur = len(hist) - 1              # 清屏后的第一次导航先恢复刚才那一题
            render("top")
            return
        if cur < 0:
            cur = len(hist) - 1
        cur = max(0, min(cur + delta, len(hist) - 1))
        render("top")            # 翻到的历史条目：从提问读起
    # 滚轮滚动：Text 默认不响应鼠标滚轮，必须绑定（答案很长时滚着看）。
    # Windows 的 <MouseWheel> 发给有焦点的控件——无边框窗焦点常不在文字区（用户没点过
    # 文字区时滚轮永远不触发），所以 bind_all 整窗响应（提词器/迷你条共用同一个 a_text）
    def on_wheel(event):
        if notes_enabled and VIEW["panel"] == "notes":
            target = notes_text
        elif VIEW["panel"] == "friend" and helper_body is not None:
            target = helper_body
        else:
            target = a_text
        target.yview_scroll(int(-event.delta / 120), "units")
        if target is a_text:
            _sync_follow()  # 滚上去 → 锁定位置（流式不再拽回）；滚回底部 → 恢复跟随
    root.bind_all("<MouseWheel>", on_wheel)

    # ---------- 提词器模式：贴镜头小窗 + 大字 + 自动滚动 ----------
    # 摄像头在屏幕上沿中央，答案窗缩成一条贴在正下方 → 读答案时视线偏移 ~3°，
    # 视频里肉眼不可辨（比 AI 眼神矫正更无痕）。F8 切回普通模式。
    tp = {"on": False, "normal_geo": None, "tick": 0}
    TP_W, TP_H = 640, 150
    TP_SCROLL_TICKS = 25            # 100ms × 25 = 2.5s 滚一行（5s 用户实测太慢）

    def set_teleprompter(on):
        if on and not tp["on"]:
            if notes_enabled:
                switch_panel("answer")
            tp["on"] = True
            tp["normal_geo"] = root.geometry()
            sw = root.winfo_screenwidth()
            root.geometry(f"{TP_W}x{TP_H}+{(sw - TP_W) // 2}+0")
            a_text.config(font=(FAM, -21))
            a_text.tag_configure("code", font=(CODE_FAM, -19))   # 提词器大字：代码块等宽同步放大
            status.config(
                text=f"提词器模式（贴镜头）· {hotkey('toggle_prompter', 'F8')} 切回")
            if not a_text.get("1.0", "end").strip():   # 无答案才放占位，保留现有文本
                a_text.insert("1.0", "（答案显示在这里，自动滚动）")
            a_text.see("1.0")
            log_event({"type": "teleprompter", "value": "on"})
            print("📜 提词器模式: 开（贴镜头）", flush=True)
        elif not on and tp["on"]:
            tp["on"] = False
            if tp["normal_geo"]:
                root.geometry(tp["normal_geo"])
            a_text.config(font=(FAM, -15))
            a_text.tag_configure("code", font=(CODE_FAM, -14))   # 恢复正常字号：代码块 tag 同步复位
            log_event({"type": "teleprompter", "value": "off"})
            print("📜 提词器模式: 关", flush=True)

    # ---------- F4 最小化：整窗缩到系统最小化态（完全离开屏幕），再按 F4 出现 ----------
    # overrideredirect 窗口不能用 root.iconify()（TclError: override-redirect flag is set），
    # 改走 Win32 ShowWindow；最小化后 Tk state() 为 iconic，_keep_ontop 自动停摆。
    mini = {"on": False, "ts": 0.0}

    def set_minimized(on):
        if on and not mini["on"]:
            if tp["on"]:
                set_teleprompter(False)      # 提词器/最小化互斥：同一窗口两种形态
            if getattr(root, "_user_hidden", False):
                root._user_hidden = False
                root.deiconify()
            mini["on"] = True
            mini["ts"] = time.time()
            if not set_window_minimized(root, True):
                mini["on"] = False
                return
            log_event({"type": "window_minimize", "value": "on"})
            print(f"🔽 已最小化（按 {hotkey('toggle_window', 'F4')} 出现）", flush=True)
        elif not on and mini["on"]:
            mini["on"] = False
            set_window_minimized(root, False)
            # 恢复后补挂三层保险（最小化/恢复会重置部分样式）
            root.attributes("-topmost", True)
            root.lift()
            set_capture_excluded(root, stealth["on"])
            set_no_activate(root)
            log_event({"type": "window_minimize", "value": "off"})
            print("🔼 已恢复显示", flush=True)

    def _geom_for_save():
        """退出/隐藏时该记的"正常形态"几何：提词器状态不写进 window-pos.txt"""
        if tp["on"] and tp["normal_geo"]:
            return tp["normal_geo"]
        return root.geometry()

    root._geom_for_save = _geom_for_save

    vision_stream = {"index": None}

    def poll():
        """主线程消费 UI 事件队列（tkinter 非线程安全，跨线程只能走队列）"""
        global cur, MODE_TXT, PENDING_TXT
        # 从任务栏/系统方式恢复时同步最小化标志，并补挂防捕获样式。
        # ShowWindow 刚下发的瞬间 Tk state 可能还是 normal，用 0.6s 去抖避免误判。
        if mini["on"] and root.state() == "normal" and time.time() - mini["ts"] > 0.6:
            mini["on"] = False
            set_capture_excluded(root, stealth["on"])
            set_no_activate(root)
        while True:
            try:
                kind, payload = ui_q.get_nowait()
            except queue.Empty:
                break
            try:
                if kind == "rec_on":            # Alt+F1：开始录音
                    VIEW["blank"] = False
                    VIEW["stage"] = "rec"
                    if notes_enabled:
                        switch_panel("answer")
                    else:
                        render()
                elif kind == "rec_off":         # Alt+F2：录音结束，转写中
                    VIEW["stage"] = "transcribing"
                    render()
                elif kind == "q":               # 转写完成：提问上屏（先显示录到了什么）
                    hist.append({"q": str(payload), "a": ""})
                    cur = -1                    # 跟随最新
                    VIEW["blank"] = False
                    VIEW["stage"] = "answering"
                    A_WATCH["ts"] = 0.0
                    render("top")
                elif kind == "a":               # 回答（流式）：更新当前对话
                    VIEW["blank"] = False
                    idx = len(hist) - 1 if cur < 0 else cur
                    if 0 <= idx < len(hist):
                        hist[idx]["a"] = str(payload)
                    # engine 约定：中间帧 = 部分答案 + "…"，最终帧 = 完整答案（无省略号）。
                    # 原来无条件置 done —— 边生成边显示"✅ 回答完成 · Alt+F1 录下一题"，误导。
                    if str(payload).endswith("…"):
                        VIEW["stage"] = "answering"
                        A_WATCH["ts"] = time.time()
                    else:
                        VIEW["stage"] = "done"
                        A_WATCH["ts"] = 0.0
                    render("tail")
                elif kind == "vision_stream":  # 截图答案流式片段：原位更新同一条历史
                    idx = vision_stream["index"]
                    if idx is None or not (0 <= idx < len(hist)):
                        hist.append({"q": "📸 屏幕截图", "a": ""})
                        idx = len(hist) - 1
                        vision_stream["index"] = idx
                        cur = -1
                        VIEW["blank"] = False
                        if notes_enabled:
                            switch_panel("answer", "top")
                    hist[idx]["a"] = str(payload)
                    VIEW["stage"] = "answering"
                    A_WATCH["ts"] = time.time()
                    if cur < 0:
                        render("tail")
                elif kind == "vision":          # Alt+P 截图识图：最终完整答案
                    idx = vision_stream["index"]
                    streamed = idx is not None and 0 <= idx < len(hist)
                    if streamed:
                        hist[idx]["a"] = str(payload)
                    else:
                        hist.append({"q": "📸 屏幕截图", "a": str(payload)})
                    vision_stream["index"] = None
                    cur = -1
                    VIEW["blank"] = False
                    VIEW["stage"] = "done"
                    A_WATCH["ts"] = 0.0
                    if notes_enabled:
                        switch_panel("answer", "top")
                    else:
                        render("tail" if streamed else "top")
                    # 同步推手机（测评场景兜底：窗口藏了/鼠标不出页面也能看答案）
                    if push_on["on"] and str(payload) and not str(payload).startswith("❌"):
                        push_answer("📸 屏幕截图", payload)
                    # 「Alt+1 自动输入」预备：答案就位 → 点进答题框按 Alt+1 模拟真人打字打进页面
                    if str(payload) and not str(payload).startswith("❌") and not TYPING_STATE["busy"]:
                        TYPING_STATE["text"] = str(payload)
                        TYPING_STATE["pos"] = 0          # 新答案:从 0 开始
                        TYPING_STATE["armed"] = True
                elif kind == "vision_abort":
                    idx = vision_stream["index"]
                    if idx is not None and 0 <= idx < len(hist) and payload:
                        hist[idx]["a"] += f"\n\n{payload}"
                    vision_stream["index"] = None
                    VIEW["stage"] = "idle"
                    A_WATCH["ts"] = 0.0
                    render("tail")
                elif kind == "vision_reset":    # Alt+3 换新题：历史保留，但当前答案区清屏
                    vision_stream["index"] = None
                    clear_vision_view_state()
                    A_WATCH["ts"] = 0.0
                    my_label.config(text="")
                    if notes_enabled:
                        switch_panel("answer", "top")
                    else:
                        render("top")
                elif kind == "idle":            # 转写失败/无内容：回待命
                    VIEW["stage"] = "idle"
                    render()
                elif kind == "nav":             # ↑↓ 翻历史
                    if notes_enabled and VIEW["panel"] == "notes":
                        _step_note(int(payload))
                    else:
                        nav(payload)
                elif kind == "status":
                    MODE_TXT = str(payload)
                    render()
                elif kind == "ov_ctl":          # 截图前窗口离场：藏起来不让自己进截图；restore 只恢复本次藏的
                    if payload == "hide" and root.state() == "normal":
                        shot_hide["on"] = True
                        root.withdraw()
                    elif payload == "restore" and shot_hide.get("on"):
                        shot_hide["on"] = False
                        if not getattr(root, "_user_hidden", False):
                            root.deiconify()
                elif kind == "tp":
                    set_teleprompter(payload)
                elif kind == "tp_toggle":
                    set_teleprompter(not tp["on"])
                elif kind == "mini_toggle":     # F4：最小化/出现；藏了时先显示
                    if getattr(root, "_user_hidden", False):
                        root._user_hidden = False
                        root.deiconify()
                        root.attributes("-topmost", True)
                        root.lift()
                        print("👁️ 窗口已显示", flush=True)
                    elif mini["on"]:
                        set_minimized(False)
                    else:
                        set_minimized(True)
                elif kind == "my_answer":       # 自动模式：你的回答转写完成
                    my_label.config(text=f"你的回答  ·  {str(payload)[:200]}")
                elif kind == "pending":         # 自动模式：攒句段数变化
                    global PENDING_TXT
                    PENDING_TXT = str(payload)
                    render()
                elif kind == "helper_answer":   # 朋友回复（helper_link 回调线程 → 队列）
                    if helper_panel is not None:
                        helper_append_answer(payload)
                elif kind == "helper_toggle":   # F7：开/关互助链路
                    if helper_panel is not None:
                        helper_toggle_link()
                elif kind == "helper_copy":     # Alt+4：复制最近一条朋友回复
                    if helper_panel is not None:
                        helper_copy_latest()
                elif kind == "helper_link":     # Alt+6：复制互助链接
                    if helper_panel is not None:
                        helper_copy_link()
                elif kind == "helper_shot":     # Alt+7：截图发给朋友
                    if helper_panel is not None:
                        helper_send_shot()
                elif kind == "quit":
                    root.quit()
            except Exception as e:
                # 单个事件出错不能杀死整个 poll：记日志继续收下一个
                log_event({"type": "ui_error", "kind": kind, "err": str(e)[:200]})
        # 静默兜底：真答案以省略号结尾时（模型常用"…"收尾）末帧判不出"完成"，
        # 状态行会一直停在"生成中"——超过 SILENT_DONE_SEC 没有新帧即视为生成结束。
        # 只兜"已经在流式"的情况（A_WATCH 有值），不影响"q 之后 API 还在首字等待"。
        if (VIEW["stage"] == "answering" and A_WATCH["ts"]
                and time.time() - A_WATCH["ts"] > SILENT_DONE_SEC):
            A_WATCH["ts"] = 0.0
            VIEW["stage"] = "done"
            render("keep")       # 保持当前位置：用户可能正读到一半
        # 提词器自动滚动（到底部自动停，滚轮可手动覆盖）
        if tp["on"]:
            tp["tick"] += 1
            if tp["tick"] >= TP_SCROLL_TICKS:
                tp["tick"] = 0
                a_text.yview_scroll(1, "units")
        root.after(100, poll)

    root.after(100, poll)
    return root

# ---------- 对话历史（↑↓ 回滚查看；重启从日志重建） ----------
hist = []          # [{"q": 转写文本, "a": 回答}, ...]，最新在末尾
cur = -1           # 当前查看索引；-1 = 跟随最新
VIEW = {"stage": "idle", "panel": "answer", "blank": False}  # blank=Alt+3 后新题待命
MODE_TXT = ""      # 底部小字右半：当前模式（set_status 维护）
PENDING_TXT = ""   # 攒句段数提示（自动模式编排线程维护）


def clear_vision_view_state():
    """Alt+3 只清当前展示和待输入答案；hist 保留，方向键仍可回看。"""
    global cur
    VIEW["stage"] = "idle"
    VIEW["blank"] = True
    cur = -1
    if TYPING_STATE["busy"]:
        TYPING_STATE["stop"] = True
    TYPING_STATE["armed"] = False
    TYPING_STATE["paused"] = False
    TYPING_STATE["text"] = ""
    TYPING_STATE["pos"] = 0

def load_history_from_logs():
    """启动时从 session 日志重建对话历史（重启不丢）：question 开条目、answer 填上一条"""
    import glob
    items = []
    try:
        # 新格式：每次启动一个独立目录；同时兼容升级前散落在 logs/ 根目录的日志。
        files = sorted(
            glob.glob(os.path.join(LOG_DIR, "session-*", "events.jsonl")) +
            glob.glob(os.path.join(LOG_DIR, "session-*.jsonl")))
    except Exception:
        return items
    for fn in files[-10:]:          # 最近 10 场
        try:
            with open(fn, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    ev = json.loads(line)
                    t = ev.get("type")
                    if t in ("question", "question_auto"):
                        items.append({"q": ev.get("text", ""), "a": ""})
                    elif t == "answer" and items and not items[-1]["a"]:
                        items[-1]["a"] = ev.get("answer", "")
                    elif t == "vision":
                        items.append({"q": "📸 屏幕截图", "a": ev.get("answer", "")})
        except Exception:
            continue
    return [it for it in items if it["q"].strip()]


def save_window_geometry(geo):
    try:
        with open(GEO_FILE, "w", encoding="utf-8") as f:
            f.write(geo)
    except Exception:
        pass

def load_window_geometry():
    try:
        with open(GEO_FILE, "r", encoding="utf-8") as f:
            s = f.read().strip()
        if s and "x" in s and "+" in s:
            return s
    except Exception:
        pass
    return None
