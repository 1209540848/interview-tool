# -*- coding: utf-8 -*-
"""ui.py — 答题窗与对话历史属主（模块级单例状态 + Tk 答题窗）。

R2 属主：hist/cur/VIEW/MODE_TXT/PENDING_TXT 会被整体重绑定 → 唯一属主在本模块，
他模块经 import ui 后 ui.cur 等访问（勿 from-import 后 global 赋值，会改错副本）。
show_answer_window(ui_q) = code 主本搬入，启动几何/缩放手柄两段差异已收敛 →
profiles.ACTIVE.place_window / mount_window_extra（文本唯一副本在 profiles；
engine.main 激活后才被调用，ACTIVE 无空窗）。
GEO_FILE 收敛：原 dirname(abspath(__file__)) 包化后指向包内目录 → 钉 config.BASE_DIR
（包父目录 = repo 根，logs/.env/window-pos.txt 同层）。"""
import json
import os
import queue

from . import profiles          # 窗口差异方法经 ACTIVE 调（差异文本唯一副本在 profiles）
from .config import BASE_DIR, LOG_DIR
from .log import log_event
from .push import push_answer
from .state import ACRYLIC, CHAMELEON, TYPING_STATE, push_on, shot_hide, stealth
from .winfx import (BG_DARK, WIN_ALPHA, sample_screen_rect, set_acrylic,
                    set_capture_excluded, set_no_activate)

GEO_FILE = os.path.join(BASE_DIR, "window-pos.txt")   # 收敛编辑：原 __file__ 相对推导（allow: code 1367）

def show_answer_window(ui_q):
    import tkinter as tk
    root = tk.Tk()
    root.overrideredirect(True)                 # 无边框
    root.attributes("-topmost", True)           # 置顶
    # 启动几何：quiz 560x160 顶中 / code 恢复上次或 760x460（原文在 profiles.place_window）
    profiles.ACTIVE.place_window(root, load_window_geometry)
    # 字体：优先 Inter（若已安装），否则 Segoe UI（观感最接近）；负数尺寸 = 像素
    import tkinter.font as _tkfont
    FAM = "Inter" if "Inter" in set(_tkfont.families(root)) else "Segoe UI"
    # 代码块等宽字体：Cascadia Code（Win 现代终端/VS 自带）→ Consolas（系统必有）兜底。
    # 正文 Segoe/Inter 是非等宽——代码/缩进混在正文里对不齐，面试读起来费劲
    CODE_FAM = ("Cascadia Code" if "Cascadia Code" in set(_tkfont.families(root))
                else "Consolas")
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

    # 防捕获常驻：窗口第一次出现在屏幕上前就挂好 affinity（之前启动时设置过晚，
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
            if root.state() == "normal":        # F12/F4 隐藏（withdrawn）时不 lift，防闪出
                root.lift()
                root.attributes("-topmost", True)
        except Exception:
            return
        root.after(300, _keep_ontop)
    root.after(300, _keep_ontop)

    # ---------- 变色龙模式（--chameleon）：200ms 吸色 + 亮度公式自动深浅文字 ----------
    cm = {"prev": None}     # 上一帧颜色（EMA 平滑用）
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
                    cm["prev"] = rgb
                    lum = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
                    fg = "#555555" if lum > 128 else "#AAAAAA"   # 亮度 >128 亮背景 → 深灰文字
                    bg = _hex(rgb)
                    root.configure(bg=bg)
                    a_text.config(bg=bg, fg=fg)
                    status.config(bg=bg, fg=fg)
                    my_label.config(bg=bg, fg=fg)
            except Exception:
                pass
            root.after(200, chameleon_tick)

        root.after(200, chameleon_tick)

    def on_press(event):
        root._drag = (event.x_root, event.y_root)
        root._geo = root.geometry()

    def on_move(event):
        try:
            x0, y0 = root._drag
            gx, gy = [int(v) for v in root._geo.split("+")[1:]]
            root.geometry(f"+{gx + event.x_root - x0}+{gy + event.y_root - y0}")
        except Exception:
            pass

    root.bind("<ButtonPress-1>", on_press)
    root.bind("<B1-Motion>", on_move)

    a_text = tk.Text(root, bg=BG_DARK, fg="#D4D4D4", wrap="word",
                     font=(FAM, -14), relief="flat", padx=10, pady=8,
                     cursor="arrow", insertwidth=0)      # 无光标闪烁、指针正常
    # 底部小字：对话索引 + 模式（低调灰，不抢眼）
    status = tk.Label(root, text="", bg=BG_DARK, fg="#7a7a7a",
                      font=(FAM, -10), anchor="w")
    status.pack(fill="x", side="bottom", padx=10, pady=(0, 6))
    # 你的回答转写显示行（自动模式：门控录到你的话 → 小字灰显示，供确认录到了什么）
    my_label = tk.Label(root, text="", bg=BG_DARK, fg="#9a9a9a",
                        font=(FAM, -10), anchor="w", wraplength=540)
    my_label.pack(fill="x", side="bottom", padx=10, pady=(0, 2))
    a_text.pack(fill="both", expand=True)
    # 血泪教训(2026-09-13)：✕ 与缩放柄必须最后创建——Tk 堆叠顺序=创建顺序，先建
    # 会被 a_text/status/my_label 压在底下：角落点击落到上层部件、冒泡成整窗拖拽，
    # 缩放柄失效（「没法手动调整窗口大小」根因）。故整块挪到 pack 之后
    # 右上角小关闭按钮（✕ 悬停变红；点击退出进程，返回 "break" 阻止拖拽绑定冒泡）
    close_btn = tk.Label(root, text="✕", bg=BG_DARK, fg="#D4D4D4",
                         font=(FAM, -12), cursor="hand2")
    close_btn.place(relx=1.0, x=-20, y=2)
    def on_close(_e=None):
        log_event({"type": "session_end", "reason": "close-btn"})
        save_window_geometry(root.geometry())      # 记住位置，下次回到这
        os._exit(0)
    close_btn.bind("<Button-1>", lambda e: (on_close(), "break")[1])
    close_btn.bind("<Enter>", lambda e: close_btn.config(fg="#FF6B6B"))
    close_btn.bind("<Leave>", lambda e: close_btn.config(fg="#D4D4D4"))

    # 右下角缩放柄（code 独有；quiz 无手柄）——原文在 profiles.mount_window_extra
    profiles.ACTIVE.mount_window_extra(root, FAM, BG_DARK)
    # 文字 tag：状态行按阶段着色、提问/回答标题区分
    a_text.tag_configure("st_rec", foreground="#ffcc66")    # 录音中黄
    a_text.tag_configure("st_work", foreground="#7fb3ff")   # 转写/生成蓝
    a_text.tag_configure("st_done", foreground="#9dcc9d")   # 完成绿
    a_text.tag_configure("st_idle", foreground="#888888")   # 待命灰
    a_text.tag_configure("q_tag", foreground="#d0b07a")     # 提问标题暗金
    a_text.tag_configure("a_tag", foreground="#7fb3ff")     # 回答标题亮蓝
    # 代码块样式：等宽字体 + 比底板深一档的底色 + 内缩进 → 和正文一眼分层，代码缩进对齐清晰
    a_text.tag_configure("code", font=(CODE_FAM, -13), foreground="#E6EAEF",
                         background="#15181E", lmargin1=10, lmargin2=10,
                         rmargin=10, spacing1=4, spacing3=4)

    def insert_md(tx, text):
        """答案文本插入文字区：把 ```代码围栏``` 渲染成等宽+深底块（见上 code tag），
        其余原样。此前围栏符原样上屏、代码用非等宽正文显示，缩进对不齐糊成一片"""
        import re as _re
        pos = 0
        for m in _re.finditer(r"```[^\n`]*\n(.*?)```", text, _re.S):
            tx.insert("end", text[pos:m.start()], None)         # 围栏前普通文本
            tx.insert("end", m.group(1).rstrip("\n"), "code")   # 块内：吞围栏行，等宽渲染
            pos = m.end()
        tx.insert("end", text[pos:], None)

    def render():
        """按历史索引 + 阶段重绘文字区（提问和回答同屏）"""
        global cur
        a_text.delete("1.0", "end")
        st = VIEW["stage"]
        if st == "rec":
            a_text.insert("end", "🎙️ 录音中…  ·  F2 结束\n", "st_rec")
        elif st == "transcribing":
            a_text.insert("end", "✍️ 转写中…\n", "st_work")
        elif st == "answering":
            a_text.insert("end", "⏳ 生成中…\n", "st_work")
        elif st == "done":
            a_text.insert("end", "✅ 回答完成  ·  F1 录下一题\n", "st_done")
        else:
            a_text.insert("end", "待命中  ·  F1 开始录音\n", "st_idle")
        idx = len(hist) - 1 if cur < 0 else cur
        if 0 <= idx < len(hist):
            item = hist[idx]
            a_text.insert("end", "\n🎙️ 提问：\n", "q_tag")
            insert_md(a_text, item["q"] + "\n\n")
            a_text.insert("end", "💡 回答：\n", "a_tag")
            insert_md(a_text, (item["a"] or "（生成中…）") + "\n")
        a_text.see("1.0")
        n = len(hist)
        pos = f"对话 {min(idx + 1, max(n, 1))}/{n}" if n else "对话 0/0"
        status.config(text=f"{pos} · {MODE_TXT}{PENDING_TXT}")

    def nav(delta):
        """↑↓ 翻历史：在当前索引基础上 ±1，越界夹住"""
        global cur
        if not hist:
            return
        if cur < 0:
            cur = len(hist) - 1
        cur = max(0, min(cur + delta, len(hist) - 1))
        render()
    # 滚轮滚动：Text 默认不响应鼠标滚轮，必须绑定（答案很长时滚着看）。
    # Windows 的 <MouseWheel> 发给有焦点的控件——无边框窗焦点常不在文字区（用户没点过
    # 文字区时滚轮永远不触发），所以 bind_all 整窗响应（提词器/迷你条共用同一个 a_text）
    def on_wheel(event):
        a_text.yview_scroll(int(-event.delta / 120), "units")
    root.bind_all("<MouseWheel>", on_wheel)

    # ---------- 提词器模式（F8）：贴镜头小窗 + 大字 + 自动滚动 ----------
    # 摄像头在屏幕上沿中央，答案窗缩成一条贴在正下方 → 读答案时视线偏移 ~3°，
    # 视频里肉眼不可辨（比 AI 眼神矫正更无痕）。F8 切回普通模式。
    tp = {"on": False, "normal_geo": None, "tick": 0}
    TP_W, TP_H = 640, 150
    TP_SCROLL_TICKS = 25            # 100ms × 25 = 2.5s 滚一行（5s 用户实测太慢）

    def set_teleprompter(on):
        if on and not tp["on"]:
            tp["on"] = True
            tp["normal_geo"] = root.geometry()
            sw = root.winfo_screenwidth()
            root.geometry(f"{TP_W}x{TP_H}+{(sw - TP_W) // 2}+0")
            a_text.config(font=(FAM, -20))
            a_text.tag_configure("code", font=(CODE_FAM, -18))   # 提词器大字：代码块等宽同步放大
            status.config(text="提词器模式（贴镜头）· F8 切回")
            if not a_text.get("1.0", "end").strip():   # 无答案才放占位，保留现有文本
                a_text.insert("1.0", "（答案显示在这里，自动滚动）")
            a_text.see("1.0")
            log_event({"type": "teleprompter", "value": "on"})
            print("📜 提词器模式: 开（贴镜头）", flush=True)
        elif not on and tp["on"]:
            tp["on"] = False
            if tp["normal_geo"]:
                root.geometry(tp["normal_geo"])
            a_text.config(font=(FAM, -14))
            a_text.tag_configure("code", font=(CODE_FAM, -13))   # 恢复正常字号：代码块 tag 同步复位
            log_event({"type": "teleprompter", "value": "off"})
            print("📜 提词器模式: 关", flush=True)

    def poll():
        """主线程消费 UI 事件队列（tkinter 非线程安全，跨线程只能走队列）"""
        global cur, MODE_TXT, PENDING_TXT
        while True:
            try:
                kind, payload = ui_q.get_nowait()
            except queue.Empty:
                break
            try:
                if kind == "rec_on":            # F1：开始录音
                    VIEW["stage"] = "rec"
                    render()
                elif kind == "rec_off":         # F2：录音结束，转写中
                    VIEW["stage"] = "transcribing"
                    render()
                elif kind == "q":               # 转写完成：提问上屏（先显示录到了什么）
                    hist.append({"q": str(payload), "a": ""})
                    cur = -1                    # 跟随最新
                    VIEW["stage"] = "answering"
                    render()
                elif kind == "a":               # 回答（流式）：更新当前对话
                    idx = len(hist) - 1 if cur < 0 else cur
                    if 0 <= idx < len(hist):
                        hist[idx]["a"] = str(payload)
                    VIEW["stage"] = "done"
                    render()
                elif kind == "vision":          # Alt+P 截图识图：独立一轮
                    hist.append({"q": "📸 屏幕截图", "a": str(payload)})
                    cur = -1
                    VIEW["stage"] = "done"
                    render()
                    # 同步推手机（测评场景兜底：窗口藏了/鼠标不出页面也能看答案）
                    if push_on["on"] and str(payload) and not str(payload).startswith("❌"):
                        push_answer("📸 屏幕截图", payload)
                    # 「Alt+1 自动输入」预备：答案就位 → 点进答题框按 Alt+1 模拟真人打字打进页面
                    if str(payload) and not str(payload).startswith("❌") and not TYPING_STATE["busy"]:
                        TYPING_STATE["text"] = str(payload)
                        TYPING_STATE["pos"] = 0          # 新答案:从 0 开始
                        TYPING_STATE["armed"] = True
                elif kind == "idle":            # 转写失败/无内容：回待命
                    VIEW["stage"] = "idle"
                    render()
                elif kind == "nav":             # ↑↓ 翻历史
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
                        root.deiconify()
                elif kind == "tp":
                    set_teleprompter(payload)
                elif kind == "tp_toggle":
                    set_teleprompter(not tp["on"])
                elif kind == "my_answer":       # 自动模式：你的回答转写完成
                    my_label.config(text=f"🗣 你的回答：{str(payload)[:200]}")
                elif kind == "pending":         # 自动模式：攒句段数变化
                    global PENDING_TXT
                    PENDING_TXT = str(payload)
                    render()
            except Exception as e:
                # 单个事件出错不能杀死整个 poll：记日志继续收下一个
                log_event({"type": "ui_error", "kind": kind, "err": str(e)[:200]})
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
VIEW = {"stage": "idle"}   # idle 待命 / rec 录音中 / transcribing 转写中 / answering 生成中 / done 完成
MODE_TXT = ""      # 底部小字右半：当前模式（set_status 维护）
PENDING_TXT = ""   # 攒句段数提示（自动模式编排线程维护）

def load_history_from_logs():
    """启动时从 session 日志重建对话历史（重启不丢）：question 开条目、answer 填上一条"""
    import glob
    items = []
    try:
        files = sorted(glob.glob(os.path.join(LOG_DIR, "session-*.jsonl")))
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
