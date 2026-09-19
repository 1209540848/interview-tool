# -*- coding: utf-8 -*-
"""按会话保存截图、语音片段和语音转录。"""
import json
import os
import re
import threading
import time
import wave

import numpy as np

from .config import BASE_DIR, LOG_DIR, SAMPLE_RATE, _env_bool


_lock = threading.RLock()
_session_dir = None
_sequence = {}
_request_sequence = 0


def screenshots_enabled():
    return _env_bool("SAVE_SCREENSHOTS", True)


def audio_enabled():
    return _env_bool("SAVE_AUDIO", True)


def transcripts_enabled():
    return _env_bool("SAVE_TRANSCRIPTS", True)


def start_session(session_id, base_dir=None):
    """创建本次启动独占的会话目录；同名时自动追加序号。"""
    global _session_dir, _sequence, _request_sequence
    root = base_dir or LOG_DIR
    with _lock:
        os.makedirs(root, exist_ok=True)
        candidate = os.path.join(root, _safe_label(session_id))
        suffix = 1
        while os.path.exists(candidate):
            suffix += 1
            candidate = os.path.join(root, f"{_safe_label(session_id)}-{suffix:02d}")
        os.makedirs(candidate)
        _session_dir = candidate
        _sequence = {}
        _request_sequence = 0
        return _session_dir


def session_dir():
    with _lock:
        return _session_dir


def ensure_dir(name):
    with _lock:
        if not _session_dir:
            return None
        path = os.path.join(_session_dir, name)
        os.makedirs(path, exist_ok=True)
        return path


def _safe_label(label):
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", str(label or "artifact")).strip("-")
    return value or "artifact"


def _next_path(folder, label, extension):
    with _lock:
        directory = ensure_dir(folder)
        if directory is None:
            return None
        key = (folder, label)
        _sequence[key] = _sequence.get(key, 0) + 1
        stamp = time.strftime("%Y%m%d-%H%M%S")
        millis = int(time.time_ns() // 1_000_000) % 1000
        filename = (f"{_safe_label(label)}-{stamp}-{millis:03d}-"
                    f"{_sequence[key]:03d}.{extension}")
        return os.path.join(directory, filename)


def _request_dir(request_id):
    if not _session_dir or not request_id:
        return None
    safe_id = _safe_label(request_id)
    if safe_id != request_id:
        return None
    path = os.path.join(_session_dir, safe_id)
    os.makedirs(path, exist_ok=True)
    return path


def _write_json(path, data):
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temp_path, path)


def new_request(kind, **metadata):
    """创建当前会话下的 request-NNNN-kind 目录并写入请求索引。"""
    global _request_sequence
    with _lock:
        if not _session_dir:
            return None
        _request_sequence += 1
        request_id = f"request-{_request_sequence:04d}-{_safe_label(kind)}"
        path = _request_dir(request_id)
        record = {
            "id": request_id,
            "kind": str(kind),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        record.update({key: value for key, value in metadata.items() if value is not None})
        _write_json(os.path.join(path, "request.json"), record)
        with open(os.path.join(_session_dir, "requests.jsonl"), "a",
                  encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return request_id


def update_request(request_id, **metadata):
    """向 request.json 合并请求阶段、输入或结果元数据。"""
    if not request_id:
        return None
    with _lock:
        path = _request_dir(request_id)
        if path is None:
            return None
        request_path = os.path.join(path, "request.json")
        try:
            with open(request_path, encoding="utf-8") as handle:
                record = json.load(handle)
        except (OSError, ValueError):
            record = {"id": request_id}
        record.update({key: value for key, value in metadata.items() if value is not None})
        try:
            _write_json(request_path, record)
            return _relative(request_path)
        except OSError:
            return None


def save_response(request_id, text, **metadata):
    """保存模型的最终文本响应，并把响应路径回写 request.json。"""
    if not request_id:
        return None
    with _lock:
        path = _request_dir(request_id)
        if path is None:
            return None
        response_path = os.path.join(path, "response.md")
        try:
            with open(response_path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(str(text or ""))
                if text and not str(text).endswith("\n"):
                    handle.write("\n")
        except OSError:
            return None
        relative = _relative(response_path)
        update_request(request_id, response=relative, **metadata)
        return relative


def _relative(path):
    if not path:
        return None
    try:
        return os.path.relpath(path, BASE_DIR).replace("\\", "/")
    except ValueError:
        return path


def save_screenshot(image, label="screenshot", request_id=None):
    """保存实际送入视觉模型的 RGB 截图，返回相对项目根目录的路径。"""
    if not screenshots_enabled() or image is None:
        return None
    if request_id:
        directory = _request_dir(request_id)
        path = os.path.join(directory, f"{_safe_label(label)}.jpg") if directory else None
    else:
        path = _next_path("screenshots", label, "jpg")
    if path is None:
        return None
    try:
        image.convert("RGB").save(path, "JPEG", quality=92, optimize=True)
        return _relative(path)
    except (OSError, ValueError):
        return None


def save_audio_clip(audio, label="voice", sample_rate=SAMPLE_RATE, request_id=None):
    """把 float 音频保存为 16-bit 单声道 WAV，返回相对路径。"""
    if not audio_enabled() or audio is None:
        return None
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    if not samples.size:
        return None
    samples = np.nan_to_num(samples, nan=0.0, posinf=0.0, neginf=0.0)
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    if request_id:
        directory = _request_dir(request_id)
        path = os.path.join(directory, f"{_safe_label(label)}.wav") if directory else None
    else:
        path = _next_path("audio", label, "wav")
    if path is None:
        return None
    try:
        with wave.open(path, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(int(sample_rate))
            handle.writeframes(pcm.tobytes())
        return _relative(path)
    except (OSError, wave.Error):
        return None


def save_transcript(source, text, request_id=None, **metadata):
    """追加一条可与音频路径关联的转录记录。"""
    if not transcripts_enabled() or not _session_dir:
        return None
    record = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": str(source),
        "text": str(text or ""),
    }
    if request_id:
        record["request_id"] = request_id
    record.update({key: value for key, value in metadata.items() if value is not None})
    path = os.path.join(_session_dir, "transcripts.jsonl")
    try:
        with _lock:
            if request_id:
                request_dir = _request_dir(request_id)
                if request_dir:
                    _write_json(os.path.join(request_dir, "transcript.json"), record)
                    update_request(
                        request_id,
                        transcript=_relative(os.path.join(request_dir, "transcript.json")),
                        stage="transcribed")
            with open(path, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return _relative(path)
    except OSError:
        return None
