# -*- coding: utf-8 -*-
"""Configurable global hotkeys and their runtime actions."""
from dataclasses import dataclass
import ctypes
import re
import threading
import time

from .config import _env_get
from .log import log_event
from .state import TYPING_STATE, VISION_STATE, push_on


_MODIFIER_CODES = {
    "Ctrl": 0x11,
    "Alt": 0x12,
    "Shift": 0x10,
    "Win": 0x5B,
}
_MODIFIER_ALIASES = {
    "CTRL": "Ctrl", "CONTROL": "Ctrl",
    "ALT": "Alt", "OPTION": "Alt",
    "SHIFT": "Shift",
    "WIN": "Win", "WINDOWS": "Win", "META": "Win",
}
_KEY_CODES = {
    "Esc": 0x1B, "Tab": 0x09, "Enter": 0x0D, "Space": 0x20,
    "Backspace": 0x08, "Insert": 0x2D, "Delete": 0x2E,
    "Home": 0x24, "End": 0x23, "PageUp": 0x21, "PageDown": 0x22,
    "Left": 0x25, "Up": 0x26, "Right": 0x27, "Down": 0x28,
    "Plus": 0xBB, "Minus": 0xBD, "Comma": 0xBC, "Period": 0xBE,
    "Slash": 0xBF, "Semicolon": 0xBA, "Quote": 0xDE,
    "Backtick": 0xC0, "LeftBracket": 0xDB, "Backslash": 0xDC,
    "RightBracket": 0xDD,
}
_KEY_ALIASES = {
    "ESC": "Esc", "ESCAPE": "Esc", "RETURN": "Enter",
    "BACK": "Backspace", "DEL": "Delete", "INS": "Insert",
    "PGUP": "PageUp", "PAGEUP": "PageUp",
    "PGDN": "PageDown", "PAGEDOWN": "PageDown",
    "ARROWLEFT": "Left", "ARROWUP": "Up",
    "ARROWRIGHT": "Right", "ARROWDOWN": "Down",
    "+": "Plus", "-": "Minus", ".": "Period", "/": "Slash",
}
for _i in range(1, 25):
    _KEY_CODES[f"F{_i}"] = 0x6F + _i
for _c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    _KEY_CODES[_c] = ord(_c)
for _c in "0123456789":
    _KEY_CODES[_c] = ord(_c)
for _i in range(10):
    _KEY_CODES[f"Num{_i}"] = 0x60 + _i

_ACTION_SPECS = {
    "record_start": ("HOTKEY_RECORD_START", "F1"),
    "record_stop": ("HOTKEY_RECORD_STOP", "F2"),
    "vision": ("HOTKEY_VISION", {"quiz": "F3|P", "code": "Alt+P"}),
    "type_answer": ("HOTKEY_TYPE_ANSWER", {"quiz": "1", "code": "Alt+1"}),
    "paste_answer": ("HOTKEY_PASTE_ANSWER", "Alt+2"),
    "clear_vision": ("HOTKEY_CLEAR_VISION", "Alt+3"),
    "toggle_window": ("HOTKEY_TOGGLE_WINDOW", "F4"),
    "toggle_push": ("HOTKEY_TOGGLE_PUSH", "F6"),
    "toggle_prompter": ("HOTKEY_TOGGLE_PROMPTER", "F8"),
    "force_mic": ("HOTKEY_FORCE_MIC", "F9"),
    "toggle_mode": ("HOTKEY_TOGGLE_MODE", "F10"),
    "history_prev": ("HOTKEY_HISTORY_PREV", "Up"),
    "history_next": ("HOTKEY_HISTORY_NEXT", "Down"),
    "pause": ("HOTKEY_PAUSE", "Ctrl+Esc"),
    "exit": ("HOTKEY_EXIT", "Esc"),
    "exit_now": ("HOTKEY_EXIT_NOW", "Ctrl+Q"),
}


def _windows_key_down(vk):
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
    except Exception:
        return False


def _canonical_key(raw):
    value = raw.strip()
    upper = value.upper()
    if upper in _KEY_ALIASES:
        return _KEY_ALIASES[upper]
    if re.fullmatch(r"F(?:[1-9]|1[0-9]|2[0-4])", upper):
        return upper
    if len(upper) == 1 and upper in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
        return upper
    if upper.startswith("NUM") and upper[3:].isdigit() and 0 <= int(upper[3:]) <= 9:
        return f"Num{int(upper[3:])}"
    for name in _KEY_CODES:
        if name.upper() == upper:
            return name
    raise ValueError(f"未知按键 {raw!r}")


@dataclass(frozen=True)
class KeyChord:
    modifiers: tuple
    key: str

    @property
    def label(self):
        return "+".join(self.modifiers + (self.key,))

    @property
    def signature(self):
        return self.modifiers, self.key

    def is_down(self, reader):
        required = set(self.modifiers)
        for name, vk in _MODIFIER_CODES.items():
            if bool(reader(vk)) != (name in required):
                return False
        return bool(reader(_KEY_CODES[self.key]))

    def any_key_down(self, reader):
        return (bool(reader(_KEY_CODES[self.key]))
                or any(reader(_MODIFIER_CODES[name]) for name in self.modifiers))


@dataclass(frozen=True)
class HotkeyBinding:
    chords: tuple

    @property
    def label(self):
        return " / ".join(chord.label for chord in self.chords) if self.chords else "未绑定"

    def is_down(self, reader):
        return any(chord.is_down(reader) for chord in self.chords)

    def any_key_down(self, reader):
        return any(chord.any_key_down(reader) for chord in self.chords)


@dataclass(frozen=True)
class HotkeyState:
    down: bool
    pressed: bool
    released: bool


def parse_binding(value):
    raw = (value or "").strip()
    if raw.lower() in ("none", "off", "disabled"):
        return HotkeyBinding(())
    if not raw:
        raise ValueError("快捷键不能为空")
    chords = []
    for item in re.split(r"\s*(?:\||,)\s*", raw):
        parts = [part.strip() for part in item.split("+") if part.strip()]
        modifiers = []
        key = None
        for part in parts:
            modifier = _MODIFIER_ALIASES.get(part.upper())
            if modifier:
                if modifier not in modifiers:
                    modifiers.append(modifier)
                continue
            if key is not None:
                raise ValueError(f"组合键 {item!r} 包含多个普通按键")
            key = _canonical_key(part)
        if key is None:
            raise ValueError(f"组合键 {item!r} 缺少普通按键")
        ordered = tuple(name for name in ("Ctrl", "Alt", "Shift", "Win")
                        if name in modifiers)
        chords.append(KeyChord(ordered, key))
    return HotkeyBinding(tuple(chords))


class HotkeyBindings:
    def __init__(self, profile_key, env_get=_env_get, reader=_windows_key_down):
        self.profile_key = profile_key
        self.reader = reader
        self.bindings = {}
        self.warnings = []
        self._previous = {}
        for action, (env_name, default_spec) in _ACTION_SPECS.items():
            default = (default_spec.get(profile_key, next(iter(default_spec.values())))
                       if isinstance(default_spec, dict) else default_spec)
            profile_name = f"{profile_key.upper()}_{env_name}"
            profile_value = env_get(profile_name)
            general_value = env_get(env_name)
            configured = profile_value or general_value
            try:
                binding = parse_binding(configured or default)
            except ValueError as exc:
                binding = parse_binding(default)
                self.warnings.append(
                    f"{profile_name if profile_value else env_name}={configured!r} "
                    f"无效：{exc}；已回退 {binding.label}")
            self.bindings[action] = binding
            self._previous[action] = False
        self._check_conflicts()

    def _check_conflicts(self):
        owners = {}
        for action, binding in self.bindings.items():
            for chord in binding.chords:
                other = owners.get(chord.signature)
                if other:
                    self.warnings.append(
                        f"快捷键冲突：{chord.label} 同时绑定 {other} 和 {action}")
                else:
                    owners[chord.signature] = action

    def label(self, action):
        return self.bindings[action].label

    def labels(self):
        return {action: binding.label for action, binding in self.bindings.items()}

    def poll(self):
        result = {}
        for action, binding in self.bindings.items():
            down = binding.is_down(self.reader)
            previous = self._previous[action]
            result[action] = HotkeyState(
                down=down, pressed=down and not previous,
                released=previous and not down,
            )
            self._previous[action] = down
        return result

    def wait_released(self, action):
        binding = self.bindings[action]
        while binding.any_key_down(self.reader):
            time.sleep(0.02)
        time.sleep(0.05)

    def banner(self, manual):
        if manual:
            prefix = (f"✅ 就绪（手动模式）。{self.label('record_start')} 开始录音，"
                      f"{self.label('record_stop')} 结束并生成答案。")
        else:
            prefix = ("✅ 就绪（自动模式）。双轨录音、自动断句与模型回答已启动。"
                      f"{self.label('record_start')} / {self.label('record_stop')} "
                      "可手动录题。")
        items = [
            f"{self.label('vision')} 识图",
            f"{self.label('type_answer')} 输入答案",
        ]
        if self.profile_key == "code":
            items += [f"{self.label('paste_answer')} 粘贴",
                      f"{self.label('clear_vision')} 清题"]
        items += [
            f"{self.label('toggle_window')} 显隐窗口",
            f"{self.label('toggle_push')} 推送",
            f"{self.label('toggle_prompter')} 提词器",
            (f"{self.label('toggle_mode')} "
             f"{'听音模式' if manual else '回答附注'}"),
            f"{self.label('history_prev')} / {self.label('history_next')} 翻历史",
            f"{self.label('pause')} 暂停",
        ]
        if not manual:
            items.append(f"{self.label('force_mic')} 按住强制收音")
        exit_label = self.label("exit")
        if self.profile_key == "code":
            exit_label += " 双按"
        items.append(f"{exit_label} / {self.label('exit_now')} 退出")
        return prefix + " 快捷键：" + "，".join(items)


class HotkeyRuntime:
    def __init__(self, *, bindings, profile_key, manual, root, ui, set_status,
                 state, state_lock, epoch, recorder_loop, recorder_mic,
                 event_q, attach_on, prompt_store, type_answer,
                 paste_answer, do_vision, reset_vision, save_geometry,
                 manual_handler, shutdown_event=None):
        self.bindings = bindings
        self.profile_key = profile_key
        self.manual = manual
        self.root = root
        self.ui = ui
        self.set_status = set_status
        self.state = state
        self.state_lock = state_lock
        self.epoch = epoch
        self.recorder_loop = recorder_loop
        self.recorder_mic = recorder_mic
        self.event_q = event_q
        self.attach_on = attach_on
        self.prompt_store = prompt_store
        self.type_answer = type_answer
        self.paste_answer = paste_answer
        self.do_vision = do_vision
        self.reset_vision = reset_vision
        self.save_geometry = save_geometry
        self.manual_handler = manual_handler
        self.shutdown_event = shutdown_event or threading.Event()
        self.hidden = False
        self.exit_previous = 0.0
        self.prompter_last = 0.0

    def _set_mode(self, mode):
        with self.state_lock:
            self.state["mode"] = mode
        log_event({"type": "mode", "value": mode})
        key = self.bindings.label("toggle_mode")
        print(f"🔀 模式: {'只听（只转写面试官）' if mode == 'listen' else '全听（你的话也注入）'}",
              flush=True)
        self.set_status(f"{'只听' if mode == 'listen' else '全听'}模式 · {key}切换")

    def _exit(self, reason, message):
        log_event({"type": "session_end", "reason": reason})
        if self.root is not None:
            self.save_geometry(self.root.geometry())
        print(message, flush=True)
        self.shutdown_event.set()
        if self.root is not None:
            self.ui("quit")

    def _after_release(self, action, callback, *args):
        self.bindings.wait_released(action)
        callback(*args)

    def _start_vision(self):
        if VISION_STATE["busy"]:
            log_event({"type": "vision_hotkey_ignored", "why": "vision_busy"})
            self.set_status("⏳ 上一张还在识别中，稍候…")
            return
        VISION_STATE["busy"] = True
        status = ("📝 测评识图中…" if self.profile_key == "quiz"
                  else "💻 笔试识图中…（同题续截自动带上下文）")
        self.set_status(status)
        threading.Thread(target=self.do_vision,
                         args=(self.ui, self.prompt_store), daemon=True).start()

    def run(self):
        while not self.shutdown_event.is_set():
            time.sleep(0.08)
            keys = self.bindings.poll()

            if keys["toggle_mode"].pressed:
                if self.manual:
                    with self.state_lock:
                        mode = self.state["mode"]
                    self._set_mode("full" if mode == "listen" else "listen")
                    if self.recorder_mic:
                        self.recorder_mic.start() if mode == "listen" else self.recorder_mic.stop()
                else:
                    self.attach_on["on"] = not self.attach_on["on"]
                    value = "开" if self.attach_on["on"] else "关"
                    print(f"🔗 附注你的回答: {value}", flush=True)
                    self.set_status(f"🔗 附注{value} · {self.bindings.label('toggle_mode')}切换")

            if keys["pause"].pressed:
                with self.state_lock:
                    self.state["paused"] = not self.state["paused"]
                    paused = self.state["paused"]
                if paused:
                    self.recorder_loop.stop()
                    if self.recorder_mic:
                        self.recorder_mic.stop()
                    if not self.manual:
                        self.event_q.put(("reset", None))
                    print("⏸️ 已暂停（录音+生成全停）", flush=True)
                    self.set_status(f"⏸️ 已暂停 · {self.bindings.label('pause')}恢复")
                else:
                    self.recorder_loop.start()
                    with self.state_lock:
                        mic_should_run = not self.manual or self.state["mode"] == "full"
                    if self.recorder_mic and mic_should_run:
                        self.recorder_mic.start()
                    if not self.manual:
                        self.event_q.put(("reset", None))
                    print("▶️ 已恢复", flush=True)
                    mode = self.state["mode"]
                    self.set_status(
                        f"{'只听' if mode == 'listen' else '全听'}模式 · "
                        f"{self.bindings.label('toggle_mode')}切换")

            if keys["exit"].pressed:
                label = self.bindings.label("exit")
                if self.profile_key == "quiz":
                    self._exit(label, f"👋 {label} 退出")
                now = time.time()
                if now - self.exit_previous < 0.8:
                    self._exit(f"{label}双按", f"👋 {label} 双按退出")
                self.exit_previous = now

            if keys["record_start"].pressed:
                with self.state_lock:
                    self.epoch["n"] += 1
                if self.recorder_loop:
                    self.recorder_loop.start_rec()
                if self.recorder_mic:
                    self.recorder_mic.start_rec()
                self.ui("rec_on")
                print(f"🎙️ 开始录音（{self.bindings.label('record_stop')} 结束）", flush=True)

            if keys["record_stop"].pressed:
                loop_bufs = []
                mic_bufs = []
                if self.recorder_loop:
                    loop_bufs = self.recorder_loop.stop_rec()
                if self.recorder_mic:
                    mic_bufs = self.recorder_mic.stop_rec()
                if not loop_bufs and not mic_bufs:
                    self.ui("rec_off")
                    self.ui("status", f"没录到内容（先按 {self.bindings.label('record_start')} 开始录音）")
                    self.ui("idle")
                    print(f"⚠️ {self.bindings.label('record_stop')} 无录音内容", flush=True)
                else:
                    self.ui("rec_off")
                    threading.Thread(target=self.manual_handler,
                                     args=(loop_bufs, mic_bufs), daemon=True).start()

            if keys["vision"].pressed:
                self._start_vision()

            if keys["type_answer"].pressed:
                log_event({"type": "type_answer_press", "armed": TYPING_STATE["armed"],
                           "busy": TYPING_STATE["busy"],
                           "text_len": len(TYPING_STATE["text"] or "")})
                if TYPING_STATE["busy"]:
                    if self.profile_key == "quiz" or time.time() - TYPING_STATE["start_ts"] > 1.0:
                        TYPING_STATE["stop"] = True
                elif TYPING_STATE["armed"] and TYPING_STATE["text"]:
                    TYPING_STATE["armed"] = False
                    print(f"⌨️ 自动输入开始…（再按 {self.bindings.label('type_answer')} 停止）",
                          flush=True)
                    target = (self.type_answer if self.profile_key == "quiz"
                              else self._after_release)
                    args = ((TYPING_STATE["text"],) if self.profile_key == "quiz" else
                            ("type_answer", self.type_answer, TYPING_STATE["text"]))
                    threading.Thread(target=target, args=args, daemon=True).start()

            if self.profile_key == "code" and keys["paste_answer"].pressed:
                log_event({"type": "paste_answer_press", "armed": TYPING_STATE["armed"],
                           "busy": TYPING_STATE["busy"],
                           "text_len": len(TYPING_STATE["text"] or "")})
                if not TYPING_STATE["busy"] and TYPING_STATE["armed"] and TYPING_STATE["text"]:
                    TYPING_STATE["armed"] = False
                    print("📋 粘贴模式…（松开快捷键后粘贴到焦点框）", flush=True)
                    threading.Thread(target=self._after_release,
                                     args=("paste_answer", self.paste_answer), daemon=True).start()

            if self.profile_key == "code" and keys["clear_vision"].pressed:
                label = self.bindings.label("clear_vision")
                dropped = self.reset_vision(f"{label} 手动清空")
                self.ui("vision_reset")
                if dropped:
                    print("🧹 识图多轮记忆已清空（截图+旧解答全丢）", flush=True)
                    self.ui("status", f"🧹 识图记忆已清空（{label}）")
                else:
                    self.ui("status", "🧹 本就无识图记忆")

            if keys["toggle_window"].pressed and self.root is not None:
                self.hidden = not bool(getattr(self.root, "_user_hidden", self.hidden))
                self.root._user_hidden = self.hidden
                if self.hidden:
                    self.root.withdraw()
                    print(f"🙈 窗口已隐藏（再按 {self.bindings.label('toggle_window')} 显示）",
                          flush=True)
                else:
                    self.root.deiconify()
                    print("👁️ 窗口已显示", flush=True)

            if keys["history_prev"].pressed:
                self.ui("nav", -1)
            if keys["history_next"].pressed:
                self.ui("nav", 1)

            if keys["toggle_prompter"].released:
                now = time.time()
                if now - self.prompter_last > 0.6:
                    self.prompter_last = now
                    self.ui("tp_toggle")

            if keys["force_mic"].pressed and not self.manual:
                with self.state_lock:
                    paused = self.state["paused"]
                if not paused:
                    self.event_q.put(("f9_on", None))
            elif keys["force_mic"].released and not self.manual:
                self.event_q.put(("f9_off", None))

            if keys["toggle_push"].pressed:
                push_on["on"] = not push_on["on"]
                value = "开" if push_on["on"] else "关"
                print(f"📱 手机推送: {value}", flush=True)
                self.set_status(
                    f"{'📱 推送开' if push_on['on'] else '推送关'} · "
                    f"{self.bindings.label('toggle_push')}切换")

            if keys["exit_now"].pressed:
                label = self.bindings.label("exit_now")
                self._exit(label, f"👋 {label} 退出")
