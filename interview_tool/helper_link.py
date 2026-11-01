# -*- coding: utf-8 -*-
"""helper_link.py — 局域网互助中继：本机 ThreadingHTTPServer + 房间 token。

面试者这台机器就是服务器，朋友手机浏览器直接打开
    http://<本机局域网IP>:8787/?t=<房间token>
不需要公网、不需要 Telegram、不需要安装任何东西。本模块只依赖标准库。

线程模型：
  * start() 起一个 daemon 线程跑 serve_forever；退出 interview-tool 即停
  * 长轮询用 Condition 等待，publish_* 时 notify_all；每个请求一个线程
  * 所有共享状态（事件、答案、在线表）由 self._lock 保护

安全边界：
  * 只接受 /api/answer 提交的文本，没有任何远程控制/执行接口
  * token 用 secrets.compare_digest 校验；日志不打印完整 token
  * 图片 id 白名单正则，防路径穿越
"""
import json
import os
import re
import secrets
import socket
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import BASE_DIR, _env_get
from .helper_page import PAGE_HTML

STATE_FILE = os.path.join(BASE_DIR, "helper-link.json")
EVENT_FILE = os.path.join(BASE_DIR, "helper-link.jsonl")
INBOX_FILE = os.path.join(BASE_DIR, "helper-inbox.jsonl")
LOCK_FILE = os.path.join(BASE_DIR, "helper-link.lock")
SHOT_DIR = os.path.join(BASE_DIR, "helper-shots")

DEFAULT_PORT = 8787
PORT_TRIES = 12
MAX_EVENT_TEXT = 8000
MAX_ANSWER_TEXT = 8000
MAX_BODY = 64 * 1024
POLL_MAX_SEC = 30
ONLINE_WINDOW_SEC = 35
MEMORY_EVENTS = 200
MEMORY_ANSWERS = 200

_IMAGE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


def _stamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _lan_ip():
    """取本机出口内网 IP：UDP connect 不发包，不依赖网卡枚举。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        sock.close()


def _env_port(default=DEFAULT_PORT):
    raw = _env_get("HELPER_LAN_PORT")
    if not raw:
        return default
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return default
    return port if 1 <= port <= 65535 else default


def _acquire_lock(path):
    """进程级单实例锁：另一个 interview-tool 已开互助时直接失败。"""
    handle = open(path, "a+b")
    try:
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def _release_lock(handle):
    if handle is None:
        return
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        handle.close()
    except OSError:
        pass


class HelperLink:
    """局域网互助中继。on_answer(record) 会在朋友提交答案时被调用（任意线程）。"""

    def __init__(self, on_answer=None, *, port=None, bind=None, token=None,
                 base_dir=None):
        self.on_answer = on_answer
        self.base_dir = base_dir or BASE_DIR
        self.state_file = os.path.join(self.base_dir, "helper-link.json")
        self.event_file = os.path.join(self.base_dir, "helper-link.jsonl")
        self.inbox_file = os.path.join(self.base_dir, "helper-inbox.jsonl")
        self.lock_file = os.path.join(self.base_dir, "helper-link.lock")
        self.shot_dir = os.path.join(self.base_dir, "helper-shots")
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._events = []
        self._answers = []
        self._seen = set()
        self._qseq = {}
        self._images = {}
        self._clients = {}
        self._seq = 0
        self._server = None
        self._thread = None
        self._lock_handle = None
        self._running = False

        self.bind = bind if bind is not None else (
            _env_get("HELPER_LAN_BIND") or "0.0.0.0")
        self.port = _env_port() if port is None else int(port)
        state = self._load_state()
        env_token = _env_get("HELPER_ROOM_TOKEN")
        self.token = token or env_token or state.get("token") or ""
        if not re.fullmatch(r"[0-9a-fA-F]{16,64}", self.token or ""):
            self.token = state.get("token") or secrets.token_hex(16)
        if not state.get("token"):
            self._save_state()

        self._load_events()
        self._load_inbox()

    # ---------- 持久化 ----------
    def _load_state(self):
        try:
            with open(self.state_file, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self):
        try:
            os.makedirs(self.base_dir, exist_ok=True)
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump({"token": self.token, "port": self.port,
                           "updated": _stamp()}, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def _append_jsonl(self, path, record):
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _load_events(self):
        try:
            with open(self.event_file, encoding="utf-8") as f:
                for line in f:
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(ev, dict) or "seq" not in ev:
                        continue
                    self._events.append(ev)
                    self._seq = max(self._seq, int(ev.get("seq") or 0))
                    if ev.get("kind") == "question" and ev.get("id"):
                        self._qseq[ev["id"]] = ev["seq"]
                    if ev.get("image") and ev.get("id"):
                        self._qseq[ev["id"]] = ev["seq"]
        except OSError:
            return
        self._events = self._events[-MEMORY_EVENTS:]

    def _load_inbox(self):
        try:
            with open(self.inbox_file, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(rec, dict) or not rec.get("text"):
                        continue
                    key = rec.get("client_id") or ""
                    if key:
                        self._seen.add(key)
                    self._answers.append(rec)
        except OSError:
            return
        self._answers = self._answers[-MEMORY_ANSWERS:]

    # ---------- 生命周期 ----------
    @property
    def running(self):
        return self._running

    def start(self):
        if self._running:
            return self
        self._lock_handle = _acquire_lock(self.lock_file)
        if self._lock_handle is None:
            raise RuntimeError("互助链路已被另一个 interview-tool 实例占用")
        last_error = None
        for offset in range(PORT_TRIES):
            port = self.port + offset
            if port > 65535:
                break
            try:
                server = _HelperServer((self.bind, port), _HelperHandler, self)
            except OSError as exc:
                last_error = exc
                continue
            self._server = server
            self.port = server.server_address[1]   # port=0 时取系统分配的随机端口
            self._running = True
            self._thread = threading.Thread(
                target=server.serve_forever, kwargs={"poll_interval": 0.2},
                name="helper-link-http", daemon=True)
            self._thread.start()
            self._save_state()
            return self
        _release_lock(self._lock_handle)
        self._lock_handle = None
        raise RuntimeError(f"互助端口 {self.port} 起 {PORT_TRIES} 个都被占用: {last_error}")

    def stop(self):
        if not self._running:
            return
        self._running = False
        server = self._server
        self._server = None
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
        with self._cond:
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        _release_lock(self._lock_handle)
        self._lock_handle = None

    def toggle(self):
        if self._running:
            self.stop()
            return False
        self.start()
        return True

    @property
    def url(self):
        if not self._running:
            return ""
        host = self.bind if self.bind not in ("", "0.0.0.0", "::") else _lan_ip()
        return f"http://{host}:{self.port}/?t={self.token}"

    def masked_url(self):
        if not self._running:
            return ""
        return self.url.replace(self.token, self.token[:6] + "…")

    # ---------- 事件 ----------
    def _new_event(self, kind, event_id, text, image=None):
        return {"seq": self._seq, "id": event_id, "kind": kind,
                "text": (text or "")[:MAX_EVENT_TEXT],
                "image": image, "ts": _stamp()}

    def publish_question(self, text):
        text = (text or "").strip()
        if not text:
            return None
        with self._cond:
            self._seq += 1
            event_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{self._seq}"
            ev = self._new_event("question", event_id, text)
            self._qseq[event_id] = ev["seq"]
            self._events.append(ev)
            self._events = self._events[-MEMORY_EVENTS:]
            self._append_jsonl(self.event_file, ev)
            self._cond.notify_all()
            return ev

    def publish_screenshot(self, data, caption="📸 本机截图"):
        if not data:
            return None
        with self._cond:
            self._seq += 1
            event_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{self._seq}"
            self._images[event_id] = bytes(data)
            self._qseq[event_id] = self._seq
            try:
                os.makedirs(self.shot_dir, exist_ok=True)
                with open(os.path.join(self.shot_dir, event_id + ".jpg"), "wb") as f:
                    f.write(data)
            except OSError:
                pass
            ev = self._new_event("shot", event_id, caption,
                                 image=f"/api/image/{event_id}")
            self._events.append(ev)
            self._events = self._events[-MEMORY_EVENTS:]
            self._append_jsonl(self.event_file, ev)
            self._cond.notify_all()
            return ev

    def _snapshot(self, since, limit=60):
        with self._lock:
            events = [ev for ev in self._events if int(ev.get("seq") or 0) > since]
        return events[-limit:]

    def wait_events(self, since, timeout, client=None):
        """长轮询：直到 seq > since 或有客户端变动，最多 timeout 秒。"""
        deadline = time.time() + max(0, timeout)
        with self._cond:
            while self._running:
                if self._seq > since:
                    break
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                self._cond.wait(remaining)
        return self._snapshot(since)

    def max_seq(self):
        with self._lock:
            return self._seq

    # ---------- 答案 ----------
    def accept_answer(self, qid, text, client_id=""):
        text = (text or "").strip()
        if not text:
            raise ValueError("答案不能为空")
        if len(text) > MAX_ANSWER_TEXT:
            text = text[:MAX_ANSWER_TEXT]
        qid = (qid or "")[:120]
        client_id = (client_id or "").strip()[:200]
        if not client_id:
            client_id = "anon-" + secrets.token_hex(8)
        with self._cond:
            if client_id in self._seen:
                return False, None
            self._seen.add(client_id)
            rec = {"qid": qid, "qseq": self._qseq.get(qid),
                   "text": text, "client_id": client_id, "ts": _stamp()}
            self._answers.append(rec)
            self._answers = self._answers[-MEMORY_ANSWERS:]
            self._append_jsonl(self.inbox_file, rec)
            self._cond.notify_all()
        if self.on_answer is not None:
            try:
                self.on_answer(rec)
            except Exception:
                pass
        return True, rec

    def latest_answer(self):
        with self._lock:
            return dict(self._answers[-1]) if self._answers else None

    # ---------- 在线 ----------
    def touch(self, client, ip=""):
        key = (client or "").strip()[:200] or ip or "unknown"
        with self._lock:
            self._clients[key] = time.time()

    def online_count(self):
        cutoff = time.time() - ONLINE_WINDOW_SEC
        with self._lock:
            stale = [k for k, ts in self._clients.items() if ts < cutoff]
            for key in stale:
                self._clients.pop(key, None)
            return len(self._clients)

    # ---------- 图片 ----------
    def get_image(self, image_id):
        if not _IMAGE_ID_RE.fullmatch(image_id or ""):
            return None
        with self._lock:
            data = self._images.get(image_id)
        if data:
            return data
        path = os.path.join(self.shot_dir, image_id + ".jpg")
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            return None
        with self._lock:
            self._images[image_id] = data
        return data


class _HelperServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, helper):
        self.helper = helper
        super().__init__(addr, handler)

    def handle_error(self, request, client_address):
        # 手机锁屏/切后台、页面刷新会掐掉在途长轮询，属于正常断线，不刷 traceback。
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionAbortedError, ConnectionResetError,
                            BrokenPipeError)):
            return
        super().handle_error(request, client_address)


class _HelperHandler(BaseHTTPRequestHandler):
    server_version = "InterviewHelper/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, _format, *_args):
        pass

    # ---------- 基础响应 ----------
    def _send(self, status, data, content_type):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _json(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(status, data, "application/json; charset=utf-8")

    def _text(self, status, text):
        self._send(status, text.encode("utf-8"), "text/plain; charset=utf-8")

    def _query(self):
        parsed = urllib.parse.urlparse(self.path)
        return parsed.path, urllib.parse.parse_qs(parsed.query)

    def _authorized(self, query):
        provided = (query.get("t") or [""])[0]
        return bool(provided) and secrets.compare_digest(provided, self.server.helper.token)

    def _client(self, query):
        return ((query.get("client") or [""])[0] or "")[:200]

    # ---------- 路由 ----------
    def do_GET(self):
        path, query = self._query()
        helper = self.server.helper
        if path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return
        if not self._authorized(query):
            self._text(403, "房间链接无效")
            return
        client = self._client(query)
        helper.touch(client, self.client_address[0] if self.client_address else "")

        if path in ("/", "/index.html"):
            self._send(200, PAGE_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/api/ping":
            self._json({"ok": True, "now": helper.max_seq(),
                        "online": helper.online_count()})
            return
        if path in ("/api/history", "/api/poll"):
            try:
                since = int((query.get("since") or ["0"])[0])
            except ValueError:
                since = 0
            try:
                timeout = int((query.get("timeout") or ["0"])[0])
            except ValueError:
                timeout = 0
            timeout = max(0, min(timeout, POLL_MAX_SEC))
            if path == "/api/poll" and timeout:
                events = helper.wait_events(since, timeout, client)
            else:
                events = helper._snapshot(since)
            self._json({"ok": True, "now": helper.max_seq(),
                        "online": helper.online_count(), "events": events})
            return
        if path.startswith("/api/image/") or path.startswith("/api/shot/"):
            image_id = path.rsplit("/", 1)[-1]
            data = helper.get_image(image_id)
            if not data:
                self._text(404, "图片不存在")
                return
            self._send(200, data, "image/jpeg")
            return
        self._text(404, "Not Found")

    def do_POST(self):
        path, query = self._query()
        if not self._authorized(query):
            self._text(403, "房间链接无效")
            return
        if path != "/api/answer":
            self._text(404, "Not Found")
            return
        helper = self.server.helper
        client = self._client(query)
        helper.touch(client, self.client_address[0] if self.client_address else "")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            self._json({"ok": False, "error": "请求体为空或过大"}, 413)
            return
        try:
            body = self.rfile.read(length)
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._json({"ok": False, "error": "JSON 解析失败"}, 400)
            return
        if not isinstance(payload, dict):
            self._json({"ok": False, "error": "格式错误"}, 400)
            return
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            self._json({"ok": False, "error": "答案不能为空"}, 400)
            return
        qid = payload.get("qid") if isinstance(payload.get("qid"), str) else ""
        cid = payload.get("client_id") if isinstance(payload.get("client_id"), str) else ""
        try:
            accepted, rec = helper.accept_answer(qid, text, cid or client)
        except ValueError as exc:
            self._json({"ok": False, "error": str(exc)}, 400)
            return
        self._json({"ok": True, "duplicate": not accepted,
                    "ts": (rec or {}).get("ts")})
