# -*- coding: utf-8 -*-
"""log.py — 会话目录内的 events.jsonl 日志（属主：LOG_FILENAME）。

R2 属主规则：LOG_FILENAME 会整体重绑定（每场会话命名一次），只能在本模块赋值——
他模块一律 import interview_tool.log 后经 log.start_session() 注入，不得 from-import 改写。
log_event 与旧版逐字节一致（只读 LOG_FILENAME 模块全局）；LOG_DIR 常量在 config.py。
"""
import json
import os
import threading
import time

from .config import LOG_DIR, _env_bool

LOG_FILENAME = None              # 相对 LOG_DIR 的 session-*/events.jsonl
_log_lock = threading.Lock()     # 多线程追加保护

def log_event(ev):
    """追加一条日志事件（线程安全；失败静默，不影响主链路）"""
    if not LOG_FILENAME:
        return
    ev = dict(ev)
    if not _env_bool("SAVE_TRANSCRIPTS", True):
        for key in ("text", "question", "answer", "partial"):
            if key in ev:
                ev[f"{key}_len"] = len(str(ev.pop(key) or ""))
    ev.setdefault("ts", time.strftime("%Y-%m-%d %H:%M:%S"))
    try:
        with _log_lock:
            with open(os.path.join(LOG_DIR, LOG_FILENAME), "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except Exception:
        pass


def start_session(model, no_inject):
    """R3 注入入口：原 main 首部 global LOG_FILENAME 的 4 行块原样封装
    （引擎 main 启动时调用；本场日志命名：重启提词器 = 新一场）"""
    global LOG_FILENAME
    os.makedirs(LOG_DIR, exist_ok=True)
    from . import storage
    millis = int(time.time_ns() // 1_000_000) % 1000
    requested_id = (f"session-{time.strftime('%Y%m%d-%H%M%S')}-"
                    f"{millis:03d}-p{os.getpid()}")
    session_path = storage.start_session(requested_id)
    session_id = os.path.basename(session_path)
    LOG_FILENAME = os.path.join(session_id, "events.jsonl")
    log_event({"type": "session_start", "model": model, "no_inject": no_inject,
               "session_id": session_id})
