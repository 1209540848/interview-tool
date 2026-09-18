# -*- coding: utf-8 -*-
"""
interview-tool-api.py — 面试实时辅助工具（API 版：不依赖 Claude Code 会话）

自动模式（默认）链路：全程双轨录音（回环轨=面试官、麦克风轨=自己，外放免耳机）
→ 回环 VAD 断句攒问题 → 你开口（或停顿 2.5s）自动发送 → 云端转写（ISI，热词可配）
→ DeepSeek API 作答（历史 5 轮 + 简历 resume.md + 你的实际回答附注）→ 屏幕小窗显示。
全程 WAV 落盘（logs/<session>/interviewer.wav + me.wav）+ JSONL 日志 → 面试复盘。
打断自动处理：面试官新语音 0.4s 内作废在途答案（历史保留占位）。

与最早原版单体的区别：注入段不再模拟键盘打给 Claude Code 终端，
改为直接 HTTP 调 OpenAI 兼容 API（DeepSeek）。
手撕代码场景走 F3 截屏识图（火山方舟豆包视觉模型，需 .env 的 ARK_API_KEY）。

热键（自动模式默认）:
  F1        手动录问题兜底（点按开始/结束，走转写提问）
  P / F3    截屏识图（测评/手撕代码：题目截图直接出答案。P 为主键：F 键在
            浏览器有默认行为且部分测评页拦截，P 无页面副作用）
  F4        隐藏/显示答案窗（一键藏，再按恢复）
  F6        手机推送开关（启动常驻开）：答案同步发到 Telegram，屏幕失效时看手机
  F8        提词器模式 ↔ 普通窗（贴摄像头下方小窗+大字自动滚动）
  F9        按住强制解除门控（面试官说话期间你补充/纠正，麦克风全收）
  F10       附注你的回答开关（默认开：你的实际回答拼进下一问给模型参考）
  ↑/↓       翻历史（提问+回答同屏）
  Ctrl+Esc  紧急暂停（暂停录音+生成，再按恢复）
  ESC / Ctrl+Q  一键退出（进程+窗口一起没）

用法: python interview-tool-api.py [--api-key KEY] [--model deepseek-chat]
      [--no-inject 只转写不调 API] [--no-window 不显示答案窗（纯转写测试）]
      [--acrylic 磨砂玻璃窗] [--chameleon 变色龙窗（吸背景色）]
      [--manual 手动模式（F1/F2 定界，旧版行为）]
依赖: pyaudiowpatch / websockets / requests / numpy / tkinter（Python 自带）
      Pillow（F3 截屏识图用）
"""
import argparse
import ctypes
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import wave
from collections import deque
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pyaudiowpatch as pyaudio

# ---------- 配置 ----------
SAMPLE_RATE = 16000          # ASR 标准采样率（ISI/DashScope 都是 16k）
BLOCK = 1024                 # 录音块大小（帧）
ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
READ_POLL_SECONDS = 0.5      # （API 版无 JSONL 轮询，保留常量兼容）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------- 自动模式（默认）：门控 / 断句 / 触发参数 ----------
GATE_RMS_THR = 0.003         # 门控快速 RMS 阈值。低于 VAD 阈值 0.008：轻声/回声也要门住
ESCAPE_COOLDOWN = 0.6        # 逃生口放行后回环 VAD 跳过时长（放行=麦克风在收人声→回环不是面试官）
GATE_SILENCE_BLOCKS = 20     # 回环连续低 RMS 块数 → 判定静音（衰减释放，~0.43s@48k）
GATE_ESCAPE_RATIO = 3.0      # 逃生口：mic RMS 超过回环回声水平 N 倍 → 用户开口放行
GATE_ESCAPE_ABS = 0.02       # 逃生口绝对阈值：mic RMS 至少要达到（防噪声放大）
GATE_RECYCLE_SEC = 0.4       # 被门控丢弃的麦克风块保留最近 N 秒（抢答开头回收）
GATE_FAST_ONSET_SEC = 0.4    # 回环快速 onset 持续 N 秒 → epoch++（打断 0.4s 内作废）
GATE_FAST_ONSET_MIN_GAP = 1.0  # 两次快速 onset 最小间隔（防持续说话反复触发）
MIN_UTTERANCE_SEC = 0.8      # utterance 低于此秒数丢弃（backchannel"对对对"）
PENDING_MAX_SEC = 120        # pending 攒句总时长上限，超过强制发送
B_TRIGGER_SEC = 2.5          # 兜底触发：pending 最后一次追加后静置 N 秒 → 发送
MY_ANSWER_MAX_CHARS = 4000   # 附注进 prompt 的截断长度（长回答取最近 4000 字）
MY_ANSWER_TEXT_MAX = 8000    # 你的回答累积文本上限（超出丢最旧，防无限增长）
MY_BATCH_SEC = 15            # 你的回答增量转写批量：攒够 ~15s 音频提交一次转写
RESUME_MAX_CHARS = 1500      # resume.md 注入 system prompt 的截断长度
RESUME_FILE = os.path.join(BASE_DIR, "resume.md")
AUTO_ATTACH_ON = True        # 自动模式默认附注你的回答（F10 切换）
# VAD 攒句参数（自最早原版单体移植）
VAD_RMS_THR = 0.008          # 回环"开口"阈值：静音基线极低(~0.001)，语音明显更高
MIN_SPEECH = 1.2             # 语音持续 ≥1.2s 才开始攒（滤咳嗽/短插话）
END_SILENCE = 0.9            # 停顿 ≥0.9s 视为句子完成
MAX_UTTERANCE = 90           # 单句上限（秒）

# ---------- 面试日志（每场一个 JSONL：问题/答案/作废/模式全记录，复盘用） ----------
LOG_DIR = os.path.join(BASE_DIR, "logs")
LOG_FILENAME = None              # main() 启动时按场次命名
_log_lock = threading.Lock()     # 多线程追加保护

def log_event(ev):
    """追加一条日志事件（线程安全；失败静默，不影响主链路）"""
    if not LOG_FILENAME:
        return
    ev = dict(ev)
    ev.setdefault("ts", time.strftime("%Y-%m-%d %H:%M:%S"))
    try:
        with _log_lock:
            with open(os.path.join(LOG_DIR, LOG_FILENAME), "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except Exception:
        pass

# ---------- DeepSeek（OpenAI 兼容） ----------
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-flash"
HISTORY_TURNS = 5            # 保留最近 N 轮问答（追问承接；10 轮历史太长会带偏新话题）
SYSTEM_PROMPT = (
    "你是实时面试陪练助手：用户正在面试中，会把面试官的问题转写给你。"
    "请直接给出简洁、口语化、可以照着念的答案要点，用中文回答，"
    "不要铺垫，不要反问，不要 Markdown 装饰。"
    "如果题目要求手撕代码/写算法：直接给出完整可运行的代码，"
    "注释只保留关键一行，代码后附一句时间/空间复杂度。"
    "用户消息末尾可能出现【附：你此前的回答】段——那是用户实际口头说出的回答，"
    "供你参考以承接追问、避免重复，它本身不是新问题，不要把它当问题回答。")

def build_system_prompt():
    """SYSTEM_PROMPT + resume.md 简历（≤RESUME_MAX_CHARS；文件缺失/读失败 → 警告跳过不炸）"""
    sp = SYSTEM_PROMPT
    try:
        with open(RESUME_FILE, encoding="utf-8") as f:
            resume = f.read().strip()
        if resume:
            sp += (f"\n\n以下是用户的简历，回答时结合简历给出贴合个人经历的答案要点"
                   f"（不要复述简历本身）：\n{resume[:RESUME_MAX_CHARS]}")
    except OSError:
        print("⚠️ resume.md 不存在（简历注入跳过，可在 voice-interview/resume.md 放简历）",
              flush=True)
    return sp

# ---------- 凭证 / ISI token（照 voice-bridge） ----------
ISI_WS_URL = "wss://nls-gateway.cn-shanghai.aliyuncs.com/ws/v1"
ISI_TOKEN_URL = "https://nls-meta.cn-shanghai.aliyuncs.com/"
ISI_TOKEN_VERSION = "2019-02-28"
DASHSCOPE_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference/"
DASHSCOPE_MODEL = "fun-asr-realtime"
DASHSCOPE_LANGUAGE = "zh"

def _env_get(var):
    val = os.environ.get(var, "").strip()
    if val:
        return val
    try:
        with open(ENV_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(var + "="):
                    raw = line.split("=", 1)[1].strip()
                    if not raw:
                        return ""
                    if raw[0] in ('"', "'"):
                        quote = raw[0]
                        end = raw.find(quote, 1)
                        if end != -1:
                            return raw[1:end].strip()
                        return raw.strip(quote).strip()
                    return raw.split("#", 1)[0].strip()
    except OSError:
        pass
    return ""

def load_isi_creds():
    return (_env_get("ISI_APPKEY"), _env_get("ALIYUN_AK_ID"), _env_get("ALIYUN_AK_SECRET"))

_isi_token_cache = {"id": "", "expire": 0.0}

def _isi_sign(params, secret):
    from urllib.parse import quote
    from urllib.request import Request, urlopen
    import hashlib, hmac, base64
    def pe(s):
        return quote(str(s), safe="~")
    qs = "&".join(f"{pe(k)}={pe(v)}" for k, v in sorted(params.items()))
    string_to_sign = "POST&%2F&" + pe(qs)
    sig = base64.b64encode(
        hmac.new((secret + "&").encode(), string_to_sign.encode(), hashlib.sha1).digest()
    ).decode()
    params["Signature"] = sig
    body = "&".join(f"{pe(k)}={pe(v)}" for k, v in sorted(params.items())).encode()
    req = Request(ISI_TOKEN_URL, data=body)
    with urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())

_isi_token_lock = threading.Lock()

def get_isi_token(ak_id, ak_secret):
    now = time.time()
    if _isi_token_cache["id"] and _isi_token_cache["expire"] - now > 60:
        return _isi_token_cache["id"]
    with _isi_token_lock:   # 并发转写时防多个线程同时换 token
        if _isi_token_cache["id"] and _isi_token_cache["expire"] - now > 60:
            return _isi_token_cache["id"]
        import uuid
        params = {
            "AccessKeyId": ak_id, "Action": "CreateToken", "Format": "JSON",
            "SignatureMethod": "HMAC-SHA1", "SignatureNonce": uuid.uuid4().hex,
            "SignatureVersion": "1.0",
            "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "Version": ISI_TOKEN_VERSION,
        }
        result = _isi_sign(params, ak_secret)
        tok = (result.get("Token") or {}).get("Id") or ""
        if not tok:
            raise RuntimeError(f"ISI 换 token 失败: {result}")
        _isi_token_cache["id"] = tok
        _isi_token_cache["expire"] = now + ((result["Token"].get("ExpireTime") or now + 86400) - now)
        return tok

def isi_transcribe(audio, appkey, ak_id, ak_secret, timeout=60):
    """ISI 实时识别（照 voice-bridge：等 TranscriptionStarted 再发音频，16000B/块）"""
    import asyncio
    import websockets
    import uuid
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
    token = get_isi_token(ak_id, ak_secret)

    async def _run():
        sentences, final = [], ""
        headers = {"X-NLS-Token": token}
        async with websockets.connect(ISI_WS_URL, extra_headers=headers,   # websockets>=12 改叫 extra_headers
                                      max_size=4 * 1024 * 1024) as ws:
            start = {
                "header": {"message_id": uuid.uuid4().hex, "task_id": uuid.uuid4().hex,
                           "namespace": "SpeechTranscriber", "name": "StartTranscription",
                           "appkey": appkey},
                "payload": {"format": "pcm", "sample_rate": SAMPLE_RATE,
                            "enable_intermediate_result": True,
                            "enable_punctuation_prediction": True,
                            "enable_inverse_text_normalization": True},
                "context": {"sdk": {"name": "interview-tool-api", "version": "1.0",
                                    "language": "python"}},
            }
            await ws.send(json.dumps(start, ensure_ascii=False))
            task_id = start["header"]["task_id"]
            sent = False
            while True:
                msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
                if isinstance(msg, (bytes, bytearray)):
                    continue
                ev = json.loads(msg)
                name = ev.get("header", {}).get("name")
                if name == "TranscriptionStarted" and not sent:
                    sent = True
                    for i in range(0, len(pcm), 16000):
                        await ws.send(pcm[i:i + 16000])
                    await ws.send(json.dumps({
                        "header": {"message_id": uuid.uuid4().hex, "task_id": task_id,
                                   "namespace": "SpeechTranscriber",
                                   "name": "StopTranscription", "appkey": appkey},
                        "context": start["context"]}, ensure_ascii=False))
                elif name == "SentenceEnd":
                    t = ((ev.get("payload") or {}).get("result") or "").strip()
                    if t:
                        sentences.append(t)
                elif name == "TranscriptionResultChanged":
                    t = ((ev.get("payload") or {}).get("result") or "").strip()
                    if t:
                        final = t
                elif name == "TranscriptionCompleted":
                    break
                elif name == "TaskFailed":
                    raise RuntimeError((ev.get("header") or {}).get("status_text") or "ISI failed")
        return "".join(sentences) or final

    return asyncio.run(_run()).strip()

def cloud_transcribe(audio, api_key, timeout=60):
    """DashScope fun-asr（备用引擎）"""
    import asyncio
    import websockets
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
    task_id = f"ic-{int(time.time() * 1000)}-{abs(hash(pcm)) % 10000}"

    async def _run():
        sentences, final = [], ""
        headers = {"Authorization": f"bearer {api_key}"}
        async with websockets.connect(DASHSCOPE_WS_URL, additional_headers=headers,
                                      max_size=4 * 1024 * 1024) as ws:
            await ws.send(json.dumps({
                "header": {"action": "run-task", "task_id": task_id, "streaming": "duplex"},
                "payload": {"task_group": "audio", "task": "asr",
                            "function": "recognition", "model": DASHSCOPE_MODEL,
                            "parameters": {"format": "pcm", "sample_rate": SAMPLE_RATE,
                                           "language": DASHSCOPE_LANGUAGE},
                            "input": {}}}, ensure_ascii=False))
            sent = False
            while True:
                msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
                if isinstance(msg, (bytes, bytearray)):
                    continue
                ev = json.loads(msg)
                hdr = ev.get("header", {})
                event = hdr.get("event")
                if event == "task-started" and not sent:
                    await ws.send(pcm)
                    await ws.send(json.dumps({
                        "header": {"action": "finish-task", "task_id": task_id,
                                   "streaming": "duplex"},
                        "payload": {"input": {}}}, ensure_ascii=False))
                    sent = True
                elif event == "result-generated":
                    s = ev.get("payload", {}).get("output", {}).get("sentence", {})
                    t = (s.get("text") or "").strip()
                    if t and s.get("sentence_end"):
                        sentences.append(t)
                    elif t:
                        final = t
                elif event == "task-finished":
                    break
                elif event == "task-failed":
                    raise RuntimeError(hdr.get("error_message") or "ASR failed")
        return "".join(sentences) or final

    return asyncio.run(_run()).strip()

def transcribe(audio, timeout=60):
    """分发：ISI 优先 → DashScope → 抛错。timeout 透传（默认 60s，增量小批不用短超时）"""
    appkey, ak_id, ak_secret = load_isi_creds()
    if appkey and ak_id and ak_secret:
        try:
            return isi_transcribe(audio, appkey, ak_id, ak_secret, timeout=timeout)
        except Exception as e:
            print(f"⚠️ ISI 转写失败: {e}", flush=True)
    key = _env_get("DASHSCOPE_API_KEY")
    if key:
        try:
            return cloud_transcribe(audio, key, timeout=timeout)
        except Exception as e:
            print(f"⚠️ DashScope 转写失败: {e}", flush=True)
    raise RuntimeError("云端转写全失败（检查 .env 凭证）")

# ---------- 转写清洗 + 攒句（照 voice-bridge） ----------
ASR_NOISE_WORDS = {"这", "那", "整体", "方式", "然后", "就是", "嗯", "呃", "啊", "哦",
                   "这个", "那个", "还有", "对对", "好的好的", "嗯嗯", "emm", "诶"}

def clean_asr_text(text):
    t = text.strip()
    t = re.sub(r"(.)\1{2,}", r"\1", t)
    # 保留句末标点：MergeBuffer 靠它立即 flush（无句号要等 0.6s gap）
    if not t:
        return ""
    # 剥确认词前缀：面试官"对，就是让你讲讲XX"→"就是让你讲讲XX"（确认+追问场景）
    while True:
        stripped = False
        for pre in ("对，", "对。", "对的，", "是的，", "没错，", "没错。", "嗯，",
                    "嗯。", "嗯嗯，", "好，", "好的，", "行，", "可以，", "对对，",
                    "对对对，", "是的", "没错", "对的"):
            if t.startswith(pre):
                t = t[len(pre):].lstrip("，,。 ")
                stripped = True
        if not stripped:
            break
    if len(t) <= 2 and t.strip("。！？!?，,；;：: ") in ASR_NOISE_WORDS:
        return ""
    return t

def clean_display_text(text):
    """答案显示清洗：去 Markdown 装饰（水平线/加粗/代码符号）、压缩连续空行为单个。
    （提词器里 '---' 分隔符和成片空行很难看，用户实测反馈）"""
    out = []
    for line in text.split("\n"):
        s = line.strip()
        if re.match(r"^[-=*_]{3,}$", s):       # 水平线
            continue
        if not s:
            if out and out[-1] != "":          # 连续空行压缩成一个
                out.append("")
            continue
        s = re.sub(r"\*\*|__|`|#+|\|", "", s)  # 去加粗/代码/井号/表格线
        out.append(s)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)

# ---------- 问答 agent：OpenAI 兼容 API + 内存对话历史 ----------
class ChatAgent:
    """OpenAI 兼容 API 问答。历史保留最近 HISTORY_TURNS 轮（追问承接）；
    作废轮（新语音打断）通过 drop_last_pair 从历史移除。串行调用，锁保护。"""
    def __init__(self, api_key, model=DEEPSEEK_MODEL, system_prompt=None,
                 base_url=DEEPSEEK_URL):
        import requests
        self.session = requests.Session()
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.messages = [{"role": "system",
                          "content": system_prompt if system_prompt is not None else SYSTEM_PROMPT}]
        self.lock = threading.Lock()

    def ask_stream(self, question, on_chunk=None, should_stop=None):
        """流式问一轮：边生成边回调 on_chunk(当前全文)，返回完整答案文本。
        should_stop() 返回 True 时中断请求（新语音打断，不再白等生成完）；
        中断时返回已生成的部分文本。异常直接抛给调用方。"""
        with self.lock:
            self.messages.append({"role": "user", "content": question})
            resp = self.session.post(
                self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
                json={"model": self.model, "messages": self.messages,
                      "temperature": 0.7, "max_tokens": 4000, "stream": True},
                timeout=(10, 120),
                stream=True,
            )
            try:
                resp.raise_for_status()
                parts = []
                for raw in resp.iter_lines():
                    if should_stop is not None and should_stop():
                        break
                    line = raw.decode("utf-8", "ignore").strip()
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        delta = (json.loads(data)["choices"][0]["delta"]
                                 .get("content")) or ""
                    except Exception:
                        continue
                    if delta:
                        parts.append(delta)
                        if on_chunk is not None:
                            on_chunk("".join(parts))
            finally:
                resp.close()
            answer = "".join(parts).strip()
            self.messages.append({"role": "assistant", "content": answer or "（无内容）"})
            self._trim()
            return answer

    def void_last(self):
        """作废在途答案但保留历史：把最后一个 assistant 换成占位符。
        （旧版删整轮 → 面试官追问时 DeepSeek 不知道上一题是什么；占位保留上下文）"""
        with self.lock:
            if self.messages and self.messages[-1]["role"] == "assistant":
                self.messages[-1] = {"role": "assistant",
                                     "content": "（上一题没来得及回答，面试官已提出新问题）"}

    def _trim(self):
        keep = 2 * HISTORY_TURNS
        if len(self.messages) > keep + 1:
            self.messages = self.messages[:1] + self.messages[-keep:]

# ---------- VAD 攒句状态机（自动模式：回环轨断面试官句子 / 麦克风轨断你的话） ----------
# 自最早原版单体移植。回调在 feed 内（持有本对象锁）被调——编排线程单线程喂 feed，
# 回调体只允许发事件（event_q.put），禁止拿其他锁 / 调其他 detector 的方法（非重入锁死锁）。
class SpeechDetector:
    """监听音频块 RMS：开口 ≥min_speech 开始攒，停顿 ≥end_silence 句子完成。
    回调：on_speech_start（检测到新语音，作废旧答案）、on_utterance（句子完成）。"""
    def __init__(self, on_speech_start, on_utterance, rms_thr=VAD_RMS_THR,
                 min_speech=MIN_SPEECH, end_silence=END_SILENCE, max_sec=MAX_UTTERANCE):
        self.on_speech_start = on_speech_start
        self.on_utterance = on_utterance
        self.rms_thr = rms_thr
        self.min_speech = min_speech
        self.end_silence = end_silence
        self.max_sec = max_sec
        self._frames = []
        self._pending = []            # 预检测缓存：确认开口时把开头一起算进句子
        self._speech_run = 0
        self._silence_run = 0
        self._started_at = 0.0
        self._lock = threading.Lock()

    def feed(self, block, block_rate):
        rms = float(np.sqrt(np.mean(block ** 2)))
        with self._lock:
            bps = block_rate / BLOCK
            if not self._frames:
                # 预检测：语音持续 ≥min_speech 才确认开口；期间缓存音频，
                # 确认时把缓存一起算进句子（不丢开头——坑：只判断不攒会吃前 1.2s）
                if rms > self.rms_thr:
                    self._speech_run += 1
                    self._pending.append(block)
                    maxp = int(self.min_speech * bps)
                    if len(self._pending) > maxp:
                        self._pending.pop(0)
                    if self._speech_run >= maxp:
                        self._speech_run = 0
                        self._frames = list(self._pending) + [block]
                        self._pending = []
                        self._silence_run = 0
                        self._started_at = time.time()
                        self.on_speech_start()
                else:
                    self._speech_run = 0
                    self._pending.clear()
                return
            self._frames.append(block)
            if rms > self.rms_thr:
                self._silence_run = 0
            else:
                self._silence_run += 1
            if self._silence_run >= int(self.end_silence * bps):
                self._finish()
            elif time.time() - self._started_at > self.max_sec:
                self._finish()

    def flush_now(self):
        """立即结算 in-progress 句子并返回（抢答场景：面试官话没说完你开口，
        半句也要并入 pending 再发送）；未确认开口的预缓存丢弃；无内容返回 None。
        同步返回不回调——编排线程直接处理，保证"并入 pending"先于"发送"执行。"""
        with self._lock:
            self._pending = []
            self._speech_run = 0
            if not self._frames:
                return None
            buf = np.concatenate(self._frames)
            self._frames = []
            self._silence_run = 0
            return buf

    def _finish(self):
        buf = np.concatenate(self._frames)
        self._frames = []
        self._silence_run = 0
        self.on_utterance(buf)

    def reset(self):
        with self._lock:
            self._frames = []
            self._pending = []
            self._silence_run = 0
            self._speech_run = 0

# ---------- 门控状态：回环响/静音（callback 线程写、编排线程读；GIL 单变量写无需锁） ----------
class GateState:
    """回环快速 RMS 判定。loop_hi 用衰减释放（连续低 RMS 块数），
    门控判定用 loop_recent()（含衰减余量）。快速 onset 供打断作废（epoch++）。"""
    def __init__(self):
        self.loop_rms = 0.0           # 最近一块回环 RMS
        self.loop_hi = False          # 回环正在响（衰减判定）
        self.loop_hi_at = 0.0         # 最近一次"响"的时刻（perf_counter）
        self.silent_blocks = 0        # 连续低 RMS 块数
        self.onset_run = 0.0          # 快速 onset 累计有声秒数
        self.last_onset = 0.0         # 上次快速 onset 时刻

    def feed_loop_rms(self, rms):
        """loop callback 每块调用（轻量：浮点比较 + 计数）"""
        now = time.perf_counter()
        self.loop_rms = rms
        if rms > GATE_RMS_THR:
            self.loop_hi = True
            self.loop_hi_at = now
            self.silent_blocks = 0
            self.onset_run += BLOCK / SAMPLE_RATE
        else:
            self.silent_blocks += 1
            if self.silent_blocks >= GATE_SILENCE_BLOCKS:
                self.loop_hi = False
            if self.onset_run and now - self.loop_hi_at > 0.2:
                self.onset_run = 0.0  # 静音中断快速 onset

    def fast_onset_hit(self):
        """编排线程调用：回环连续有声 ≥GATE_FAST_ONSET_SEC 且距上次足够久 → 返回 True（作废）"""
        now = time.perf_counter()
        if self.onset_run >= GATE_FAST_ONSET_SEC and \
                now - self.last_onset > GATE_FAST_ONSET_MIN_GAP:
            self.last_onset = now
            self.onset_run = 0.0
            return True
        return False

    def loop_recent(self):
        """回环最近是否响过（含 0.4s 衰减余量）——门控判定用"""
        return self.loop_hi or (time.perf_counter() - self.loop_hi_at) < 0.4

    def reset(self):
        self.__init__()

# ---------- 录音：WASAPI 回环 / 麦克风（pyaudiowpatch，统一重采样 16k） ----------
def resample_to_16k(audio, from_rate):
    if from_rate == SAMPLE_RATE:
        return audio
    n = int(len(audio) * SAMPLE_RATE / from_rate)
    return np.interp(np.linspace(0, len(audio) - 1, n),
                     np.arange(len(audio)), audio).astype(np.float32)

def pick_loopback_device(p):
    """按默认输出设备名精确匹配 loopback（蓝牙/音箱切换时仍正确）"""
    wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    out_idx = wasapi["defaultOutputDevice"]
    out_name = p.get_device_info_by_index(out_idx)["name"]
    for i in range(p.get_device_count()):
        dev = p.get_device_info_by_index(i)
        if p.get_host_api_info_by_index(dev["hostApi"])["type"] == pyaudio.paWASAPI \
                and dev.get("isLoopbackDevice"):
            if dev["name"].replace(" [Loopback]", "") == out_name:
                return i, dev
    return None, None

class Recorder:
    """callback 模式录音。统一重采样 16k mono float32 喂 on_block（编排线程/VAD/转写）。
    两条数据流互不干扰：
      - on_block(block)：16k float32 块 → 编排线程（门控仲裁 + VAD）
      - _raw：原生格式 int16 块（设备采样率/声道原样）→ WavWriter 落盘（复盘要真相）
    callback 只做最小工作：不阻塞、不文件IO、不 print、异常全吞不 return paComplete（不杀流）。
    流常开不关；_rec/_bufs 保留 F1 手动兜底（攒 16k float32 整段）。"""
    def __init__(self, p, device_idx, device, on_block=None, mode="cb"):
        self.p = p
        self.device_idx = device_idx
        self.device = device
        self.on_block = on_block
        self.mode = mode
        self._stream = None
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._raw = []                  # 原生 int16 块（落盘；writer 线程 take_raw 取走）
        self._rec = False               # F1 后 True：回调往 _bufs 里攒
        self._bufs = []
        self._fmt = None                # 数据格式探测结果（'f32'/'i16'），首块定
        self._sr = 44100                # 默认值兜底：tcp 模式 start() 跳过不设置，
        self._ch = 2                    # 缺 _sr/_ch 会让 _process 在降混处 AttributeError（全静默丢数据）

    def start(self):
        if self._stream is not None:
            return
        dev = self.device
        if dev is None or self.mode == "tcp":
            return   # tcp 模式：面试官音频由 loop_tcp_thread 直接喂 _process，不开声卡流
        sr = int(dev["defaultSampleRate"])
        ch = int(dev["maxInputChannels"]) or 1
        self._sr, self._ch = sr, ch
        try:
            if self.mode == "read":
                # 坑（VB-Audio 驱动）：pyaudiowpatch callback 模式对 CABLE/Voicemeeter
                # 给 0.0/-NaN 全损数据（read() 模式正常）。回环轨必须 read 线程。
                # read() 数据格式 = 请求格式（请求 paInt16 得 int16），无需探测
                self._stream = self.p.open(
                    format=pyaudio.paInt16, channels=ch, rate=sr,
                    input=True, input_device_index=self.device_idx,
                    frames_per_buffer=BLOCK)
                self._thread = threading.Thread(target=self._read_loop, daemon=True)
                self._thread.start()
            else:
                # 坑：pyaudiowpatch callback 的 in_data 永远是设备混音格式
                # （Float32），format 参数只影响打开校验、不影响数据格式
                self._stream = self.p.open(
                    format=pyaudio.paFloat32, channels=ch, rate=sr,
                    input=True, input_device_index=self.device_idx,
                    frames_per_buffer=BLOCK, stream_callback=self._cb)
        except OSError as e:
            print(f"⚠️ 打开录音流失败 [{self.device_idx}]: {e}", flush=True)
            self._stream = None

    def _read_loop(self):
        """read 模式：节流读块 → 与 callback 相同的处理。
        坑：pyaudiowpatch 对 VB-Audio 设备的 read() 不阻塞——立即返回最新缓冲区，
        疯狂循环会奇偶丢块。必须按采集节奏 sleep 节流（实测 23.7ms/块满速率收全）。
        stop_stream 解阻塞并抛异常退出"""
        interval = BLOCK / self._sr if self._sr else 0.023
        while not self._stop.is_set():
            a = time.perf_counter()
            try:
                in_data = self._stream.read(BLOCK, exception_on_overflow=False)
            except Exception:
                break
            self._process(in_data)
            w = interval - (time.perf_counter() - a)
            if w > 0:
                time.sleep(w)

    def start_rec(self):
        """F1 手动兜底：开始攒（清掉上一轮残留，防误拼）"""
        with self._lock:
            self._bufs = []
            self._rec = True

    def stop_rec(self):
        """F1 手动兜底：停止并取走攒的 16k float32 块（没录到返回空列表）"""
        with self._lock:
            self._rec = False
            bufs, self._bufs = self._bufs, []
        return bufs

    def take_raw(self):
        """WavWriter 取走原生落盘块（已转 int16）"""
        with self._lock:
            out, self._raw = self._raw, []
        return out

    def _process(self, in_data):
        """块处理（callback / read 线程共用）：解析 → 降混 → raw/bufs/on_block。
        read 模式：数据格式 = 请求格式（paInt16），绝不走探测——首块静音会被误判 f32，
        int16 数据按 f32 解析出 2e36 巨值 → clip 满幅 + VAD 疯狂误触发。
        callback 模式：格式 = 设备混音格式（与请求无关），首块全静音无法判断 → 跳过等
        有信号块再定（丢开头 ~23ms 无影响）。"""
        if self._stop.is_set():
            return
        try:
            if self.mode == "read":
                block = np.frombuffer(in_data, dtype=np.int16).astype(np.float32) / 32768.0
            else:
                if self._fmt is None:
                    f32max = float(np.abs(np.frombuffer(in_data[:64], dtype=np.float32)).max())
                    i16max = int(np.abs(np.frombuffer(in_data[:64], dtype=np.int16)).max())
                    # 坑：int16 数据按 f32 解析是随机大值（数亿，字节模式组合出高指数），
                    # 不是恒小值；f32 数据按 f32 解析是真实电平（<1）。
                    # 判定：f32 解析 <1 且 i16 有大值 → 真 float32；f32 解析 >1 → 真 int16。
                    if f32max < 1.0 and i16max > 100:
                        self._fmt = "f32"
                    elif f32max > 1.0:
                        self._fmt = "i16"
                    else:
                        return          # 全静音块：格式无法判断，等下一块
                if self._fmt == "f32":
                    block = np.nan_to_num(np.frombuffer(in_data, dtype=np.float32),
                                          nan=0.0, posinf=0.0, neginf=0.0)   # 引擎瞬态 NaN → 0
                else:
                    block = np.frombuffer(in_data, dtype=np.int16).astype(np.float32) / 32768.0
            if self._ch > 1:
                block = block.reshape(-1, self._ch)[:, 0]   # VAD/转写/落盘统一单声道（ch0）
            i16 = np.clip(block * 32767, -32768, 32767).astype(np.int16)
            r16 = resample_to_16k(block, self._sr)
            with self._lock:
                self._raw.append(i16)
                if self._rec:
                    self._bufs.append(r16)
            if self.on_block is not None:
                self.on_block(r16, float(np.sqrt(np.mean(block ** 2))))
        except Exception:
            pass

    def _cb(self, in_data, frame_count, time_info, status):
        self._process(in_data)
        return (None, pyaudio.paContinue)

    def stop(self):
        self._stop.set()
        with self._lock:
            s, self._stream = self._stream, None
        if s:
            try:
                s.stop_stream()   # read 线程阻塞在 read() 上，stop_stream 解阻塞并抛异常退出
                s.close()
            except Exception:
                pass
        t, self._thread = self._thread, None
        if t is not None and t is not threading.current_thread():
            t.join(timeout=2)

def loop_tcp_thread(port, rec):
    """面试官音频直连：播放器进程 TCP 发送 int16 2ch 44100Hz 原始流（本机静音方案——
    VB-Audio 虚拟声卡采集端在本机不可用，音频全程不出声卡），按 1024 帧块喂 Recorder。
    播放器以实时节奏发送，断连即线程退出（不影响 mic 轨）。"""
    import socket
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(("127.0.0.1", port))
        srv.listen(1)
        srv.settimeout(1.0)
    except OSError as e:
        print(f"⚠️ loop-tcp 监听 :{port} 失败: {e}", flush=True)
        return
    print(f"🔌 面试官音频 TCP 监听 :{port}（等待播放器连接）…", flush=True)
    CHUNK = 1024 * 2 * 2          # 1024 帧 × 2ch × int16
    while not rec._stop.is_set():
        try:
            conn, _ = srv.accept()
        except socket.timeout:
            continue
        except OSError:
            return
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        print("🔌 播放器已连接，面试官音频直连就绪", flush=True)
        buf = b""
        while not rec._stop.is_set():
            try:
                data = conn.recv(65536)
            except Exception:
                break
            if not data:
                break
            buf += data
            while len(buf) >= CHUNK:
                rec._process(buf[:CHUNK])
                buf = buf[CHUNK:]
        try:
            conn.close()
        except OSError:
            pass
        print("🔌 播放器断开，TCP 面试官轨结束（继续等待重连）", flush=True)

# ---------- 全程录音落盘（复盘）：独立线程每 5s 刷盘 + 修补 WAV header ----------
# 本程序退出全走 os._exit(0)（ESC/Ctrl+Q/关闭按钮），finally/atexit 都不执行——
# 只在退出时修补 header 必然损坏；每 5s 刷盘时同步 seek 重写 RIFF/data-size，文件随时有效。
import struct as _struct
WAV_FLUSH_SEC = 5.0

def _patch_wav_size(path):
    """按当前文件大小重写 RIFF size（偏移4）和 data size（偏移40），不重建流"""
    try:
        size = os.path.getsize(path)
        if size < 44:          # header 还没写（0 字节新文件）：pack 负数会 struct.error
            return
        with open(path, "r+b") as f:
            f.seek(4)
            f.write(_struct.pack("<I", size - 8))
            f.seek(40)
            f.write(_struct.pack("<I", size - 44))
    except OSError:
        pass

class WavWriter:
    """双轨 WAV 持续落盘（原始未门控，复盘要真相）。take_raw 线程安全；每 5s 刷盘+修 header。"""
    def __init__(self, paths, recorders):
        self.paths = paths              # {label: 文件路径}
        self.recorders = recorders      # {label: Recorder}
        self._stop = threading.Event()
        self._w = {}                    # {label: wave.Wave_write}

    def _ensure(self, label, rec):
        if label in self._w or rec.device is None:
            return
        dev = rec.device
        w = wave.open(self.paths[label], "wb")
        w.setnchannels(1)              # 落盘统一单声道（_cb 已降混）
        w.setsampwidth(2)
        w.setframerate(int(dev["defaultSampleRate"]))
        w.writeframes(b"")              # 写 44 字节空 header 占位
        self._w[label] = w

    def run(self):
        while not self._stop.wait(WAV_FLUSH_SEC):
            try:
                for label, rec in self.recorders.items():
                    self._ensure(label, rec)
                    w = self._w.get(label)
                    if w is None:
                        continue
                    blocks = rec.take_raw()
                    if blocks:
                        w.writeframes(np.concatenate(blocks).tobytes())
                    _patch_wav_size(self.paths[label])
            except Exception:
                traceback.print_exc()   # writer 异常别吞：报错可排障，线程继续跑

    def close(self):
        self._stop.set()
        for w in list(self._w.values()):
            try:
                w.close()               # close 时 wave 模块自动修正 header
            except Exception:
                pass
        self._w.clear()

# ---------- 防屏幕捕获：SetWindowDisplayAffinity（WDA_EXCLUDEFROMCAPTURE） ----------
stealth = {"on": True}   # 防捕获固定常驻开启；不提供运行时关闭开关
ACRYLIC = {"on": False}  # --acrylic 启动参数：磨砂玻璃背景（DWM Acrylic）替代灰色实底。模块级同上
CHAMELEON = {"on": False}  # --chameleon 启动参数：吸窗口下方屏幕颜色做底板，文字自动深浅（变色龙）


def sample_screen_rect(x, y, w, h):
    """GDI BitBlt 采样屏幕矩形平均色（返回 (r, g, b)），不依赖 PIL。
    坑：不能采自己窗口的像素（会采到自己的旧颜色 → 反馈循环锁死），
    调用方必须给窗口外侧的矩形。"""
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    user32.GetDC.argtypes = [ctypes.c_void_p]
    user32.GetDC.restype = ctypes.c_void_p
    user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.BitBlt.argtypes = [ctypes.c_void_p] + [ctypes.c_int] * 4 + \
                            [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    gdi32.BitBlt.restype = ctypes.c_int
    gdi32.GetBitmapBits.argtypes = [ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p]
    gdi32.GetBitmapBits.restype = ctypes.c_long
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]

    hdc = user32.GetDC(None)              # NULL = 整个（虚拟）屏幕
    if not hdc:
        return None
    try:
        mem = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
        old = gdi32.SelectObject(mem, bmp)
        gdi32.BitBlt(mem, 0, 0, w, h, hdc, x, y, 0x00CC0020)   # SRCCOPY
        bits = (ctypes.c_ubyte * (w * h * 4))()
        gdi32.GetBitmapBits(bmp, len(bits), ctypes.byref(bits))
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
    finally:
        user32.ReleaseDC(None, hdc)
    n = w * h
    rs = gs = bs = 0
    for i in range(n):        # BGRA 行序：平均色不关心顺序
        rs += bits[4 * i + 2]
        gs += bits[4 * i + 1]
        bs += bits[4 * i]
    return rs // n, gs // n, bs // n


def set_capture_excluded(root, exclude):
    """把答案窗从屏幕捕获中排除：自己照常看，截屏/录屏/共享画面里该区域空白。
    Windows 10 2004+ 官方接口（DRM 播放器同款机制），本机 19045 支持。返回是否成功。"""
    try:
        user32 = ctypes.windll.user32
        user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user32.GetAncestor.restype = ctypes.c_void_p
        user32.SetWindowDisplayAffinity.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        top = user32.GetAncestor(root.winfo_id(), 2) or root.winfo_id()   # GA_ROOT 拿顶层窗口
        WDA_EXCLUDEFROMCAPTURE, WDA_NONE = 0x11, 0x0
        user32.SetWindowDisplayAffinity(top, WDA_EXCLUDEFROMCAPTURE if exclude else WDA_NONE)
        return True
    except Exception as e:
        print(f"⚠️ 防捕获设置失败: {e}", flush=True)
        return False


def set_no_activate(root):
    """给答案窗挂 WS_EX_NOACTIVATE：窗口永不激活、点它也不抢焦点。
    测评/笔试页面监听 blur 记「离开页面」——答案窗每次被激活(点击/lift 瞬间)
    浏览器就失焦一次。tk 的 lift() 在 Windows 上是 SetWindowPos 且不带
    SWP_NOACTIVATE，_keep_ontop 每 300ms 抬一次就可能激活窗口 = 失焦根因。
    挂上此样式后 lift/点击都不再影响浏览器焦点。"""
    try:
        user32 = ctypes.windll.user32
        user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user32.GetAncestor.restype = ctypes.c_void_p
        user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.GetWindowLongPtrW.restype = ctypes.c_void_p   # 64 位指针宽返回值
        user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        top = user32.GetAncestor(root.winfo_id(), 2) or root.winfo_id()   # GA_ROOT 拿顶层窗口
        GWL_EXSTYLE, WS_EX_NOACTIVATE = -20, 0x08000000
        style = user32.GetWindowLongPtrW(top, GWL_EXSTYLE)
        user32.SetWindowLongPtrW(top, GWL_EXSTYLE, style | WS_EX_NOACTIVATE)
        return True
    except Exception as e:
        print(f"⚠️ 设置 WS_EX_NOACTIVATE 失败: {e}", flush=True)
        return False

# ---------- DWM Acrylic 磨砂玻璃背景（--acrylic 启动参数） ----------
class ACCENTPOLICY(ctypes.Structure):
    _fields_ = [("AccentState", ctypes.c_uint), ("AccentFlags", ctypes.c_uint),
                ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_uint)]


class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
    _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p),
                ("SizeOfData", ctypes.c_size_t)]


def set_acrylic(root):
    """给答案窗挂 DWM Acrylic 磨砂玻璃（Win10 1803+ 官方 SetWindowCompositionAttribute）。
    先试 ACCENT_ENABLE_ACRYLICBLURBEHIND(4) 带中灰 tint；失败回退 ACCENT_ENABLE_BLURBEHIND(3)
    纯模糊无 tint（更稳）。返回是否成功。"""
    try:
        user32 = ctypes.windll.user32
        user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user32.GetAncestor.restype = ctypes.c_void_p
        user32.SetWindowCompositionAttribute.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        user32.SetWindowCompositionAttribute.restype = ctypes.c_int
        top = user32.GetAncestor(root.winfo_id(), 2) or root.winfo_id()   # GA_ROOT 拿顶层窗口
        # AccentState=4（acrylic blur）+ AccentFlags=2（画全部边框）
        # GradientColor=0xCC8C8C8C：DWORD 是 AABBGGRR 小端，alpha=0xCC、RGB=0x8C8C8C 中灰 tint
        accent = ACCENTPOLICY(4, 2, 0xCC8C8C8C, 0)
        data = WINDOWCOMPOSITIONATTRIBDATA()
        data.Attribute = 19                  # WCA_ACCENT_POLICY
        data.Data = ctypes.cast(ctypes.byref(accent), ctypes.c_void_p)
        data.SizeOfData = ctypes.sizeof(accent)
        if user32.SetWindowCompositionAttribute(top, ctypes.byref(data)):
            return True
        accent.AccentState = 3               # 回退：纯模糊无 tint，兼容性更稳
        accent.GradientColor = 0
        if user32.SetWindowCompositionAttribute(top, ctypes.byref(data)):
            return True
        print("⚠️ Acrylic 设置失败（acrylic/blurbehind 两种 accent 都不支持）", flush=True)
        return False
    except Exception as e:
        print(f"⚠️ Acrylic 设置失败: {e}", flush=True)
        return False

# ---------- 答案小窗（tkinter 置顶，可拖动；UI 线程安全：事件队列投递） ----------
# 窗口视觉：深色底板 + 整窗 85% 不透明（-alpha）。半透底板保证文字在杂色桌面可读，
# 又不完全挡住背后内容。（旧版全透明文字悬浮——杂色桌面上糊成一片，已弃用）
BG_DARK = "#101010"             # 深灰底板（近黑）：够灰够低调，桌面杂色里不起眼
WIN_ALPHA = 0.8                 # 整窗 80% 不透明：半透不挡视线，又保文字清晰
def show_answer_window(ui_q):
    import tkinter as tk
    root = tk.Tk()
    root.overrideredirect(True)                 # 无边框
    root.attributes("-topmost", True)           # 置顶
    # 初始化：水平居中、垂直顶边贴屏幕最上（用户要求）；之后可正常拖动
    _sw = root.winfo_screenwidth()
    root.geometry(f"560x160+{(_sw - 560) // 2}+0")
    # 字体：优先 Inter（若已安装），否则 Segoe UI（观感最接近）；负数尺寸 = 像素
    import tkinter.font as _tkfont
    FAM = "Inter" if "Inter" in set(_tkfont.families(root)) else "Segoe UI"
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
    # 文字 tag：状态行按阶段着色、提问/回答标题区分
    a_text.tag_configure("st_rec", foreground="#ffcc66")    # 录音中黄
    a_text.tag_configure("st_work", foreground="#7fb3ff")   # 转写/生成蓝
    a_text.tag_configure("st_done", foreground="#9dcc9d")   # 完成绿
    a_text.tag_configure("st_idle", foreground="#888888")   # 待命灰
    a_text.tag_configure("q_tag", foreground="#d0b07a")     # 提问标题暗金
    a_text.tag_configure("a_tag", foreground="#7fb3ff")     # 回答标题亮蓝

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
            a_text.insert("end", item["q"] + "\n\n", None)
            a_text.insert("end", "💡 回答：\n", "a_tag")
            a_text.insert("end", (item["a"] or "（生成中…）") + "\n", None)
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
            status.config(text="提词器模式（贴镜头）· F8 切回")
            if not a_text.get("1.0", "end").strip():   # 无答案才放占位，保留现有文本
                a_text.insert("1.0", "（答案显示在这里，自动滚动）")
            a_text.see("1.0")
        elif not on and tp["on"]:
            tp["on"] = False
            if tp["normal_geo"]:
                root.geometry(tp["normal_geo"])
            a_text.config(font=(FAM, -14))

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
                elif kind == "vision":          # F3 截图识图：独立一轮
                    hist.append({"q": "📸 屏幕截图", "a": str(payload)})
                    cur = -1
                    VIEW["stage"] = "done"
                    render()
                    # 同步推手机（测评场景兜底：窗口藏了/鼠标不出页面也能看答案）
                    if push_on["on"] and str(payload) and not str(payload).startswith("❌"):
                        push_answer("📸 屏幕截图", payload)
                elif kind == "idle":            # 转写失败/无内容：回待命
                    VIEW["stage"] = "idle"
                    render()
                elif kind == "nav":             # ↑↓ 翻历史
                    nav(payload)
                elif kind == "status":
                    MODE_TXT = str(payload)
                    render()
                elif kind == "tp":
                    set_teleprompter(payload)
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
                a_text.yview_scroll(1, "lines")
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

# ---------- 窗口位置记忆：退出时存 geometry，下次启动回到用户拖的位置 ----------
# 透明窗口在桌面上本来就难找，重启回到左上角 (40,40) 更是找不到——记住位置是刚需
GEO_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "window-pos.txt")

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

# ---------- F2 截屏识图（手撕代码场景：面试官共享屏幕出题，按 F2 直接出答案） ----------
# 兼容两家 OpenAI 风格接口，用 .env 三件套切换，不用改代码：
#   ARK_API_KEY        = 识图 API key
#   ARK_VISION_MODEL   = 模型名（火山 doubao-1.5-vision-lite-250315 / 百炼 qwen-vl-plus）
#   VISION_BASE_URL    = 接口地址（默认火山 chat/completions；百炼填
#                        https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions）
VISION_MODEL = "doubao-1.5-vision-lite-250315"     # 默认火山豆包轻量视觉（便宜快）
VISION_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
VISION_FALLBACK_URL = "https://ark.cn-beijing.volces.com/api/v3/responses"
VISION_PROMPT = ("这是面试场景的屏幕截图（可能是手撕代码、算法题或面试官共享的题目）。"
                 "请识别其中的代码/题目内容，直接给出：①题目或代码在问什么 ②解题思路 ③关键代码。"
                 "用中文，简洁直接，代码放 markdown 代码块里。")
VISION_STATE = {"busy": False}                     # 一次只截一张，防连按风暴


def _vision_parse(j):
    """解析 chat/completions / responses 两种响应格式，返回文本"""
    try:                                            # chat/completions 格式（主流）
        return j["choices"][0]["message"]["content"]
    except Exception:
        try:                                        # responses 格式（火山新接口）
            out = j["output"]
            return "".join(c.get("text", "") for c in out[0]["content"] if isinstance(c, dict))
        except Exception:
            return None


def do_vision(ui):
    """F2 主流程（后台线程）：全屏截图 → 压缩 → 识图 API → 答案窗显示。失败不打断主链路"""
    try:
        key = _env_get("ARK_API_KEY")
        if not key:
            ui("status", "❌ 缺 ARK_API_KEY：.env 里填识图 API key")
            return
        model = _env_get("ARK_VISION_MODEL") or VISION_MODEL   # 可在 .env 覆盖模型名
        url = _env_get("VISION_BASE_URL") or VISION_BASE_URL   # 可在 .env 换服务商
        import io as _io
        import base64 as _b64
        import requests
        from PIL import ImageGrab
        img = ImageGrab.grab()                      # 全屏
        w, h = img.size
        scale = min(1.0, 1280.0 / max(w, h))        # 最长边压到 1280 → base64 小、API 快
        if scale < 1.0:
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
        buf = _io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=85)
        b64 = _b64.b64encode(buf.getvalue()).decode()
        hdr = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        body = {"model": model, "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            {"type": "text", "text": VISION_PROMPT}]}],
            # 输出上限显式声明：不写就吃服务商默认（智谱 glm-4v-flash 默认仅 1024 token，
            # 长代码答案必截断——历史"输出不完全"根因）。4096 覆盖手撕代码完整输出
            "max_tokens": 4096}
        r = requests.post(url, headers=hdr, json=body, timeout=60)
        j = r.json()
        ans = _vision_parse(j) if r.status_code == 200 else None
        if not ans and r.status_code == 400:        # chat 格式被拒 → 回退 responses 格式
            body2 = {"model": model, "input": [{"role": "user", "content": [
                {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"},
                {"type": "input_text", "text": VISION_PROMPT}]}]}
            r2 = requests.post(VISION_FALLBACK_URL, headers=hdr, json=body2, timeout=60)
            j2 = r2.json()
            ans = _vision_parse(j2) if r2.status_code == 200 else None
            if not ans:
                ans = f"❌ 识图失败({r2.status_code}): {str(j2)[:120]}"
        elif not ans:
            ans = f"❌ 识图失败({r.status_code}): {str(j)[:120]}"
        ui("vision", ans)
        log_event({"type": "vision", "ok": bool(ans and not ans.startswith("❌")),
                   "err": None if (ans and not ans.startswith("❌")) else (ans or "")[:200],
                   "answer": ans[:2000], "ans_len": len(ans or ""),
                   "truncated": bool(ans) and len(ans) > 2000})
    except Exception as e:
        ui("status", f"❌ 识图异常: {e}")
    finally:
        VISION_STATE["busy"] = False

# ---------- 主逻辑 ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-key", default=None, help="DeepSeek API key（默认读 .env DEEPSEEK_API_KEY）")
    ap.add_argument("--model", default=None,
                    help="问答模型名（默认随所选后端：DeepSeek 或主视觉模型）")
    ap.add_argument("--no-inject", action="store_true", help="只转写不调 API（测链路）")
    ap.add_argument("--no-window", action="store_true", help="不显示答案窗（纯转写测试）")
    ap.add_argument("--acrylic", action="store_true", help="磨砂玻璃背景（DWM Acrylic）替代灰色实底")
    ap.add_argument("--chameleon", action="store_true", help="变色龙背景：吸窗口下方屏幕颜色，文字自动深浅")
    ap.add_argument("--manual", action="store_true",
                    help="手动模式（F1 开始/F2 结束定界，旧版行为；默认自动模式双轨全程录音）")
    ap.add_argument("--mic-device", type=int, default=None,
                    help="麦克风设备索引（默认取系统默认输入；装虚拟声卡测试时指定）")
    ap.add_argument("--loop-device", type=int, default=None,
                    help="回环设备索引（默认按默认输出设备匹配；默认输出被虚拟声卡占用时指定）")
    ap.add_argument("--loop-tcp", type=int, default=None,
                    help="面试官音频走 TCP（本机 PORT 监听）：不采声卡回环——VB-Audio 虚拟声卡"
                    "采集端在本机不可用（全损），播放器进程直发面试官音频绕过声卡；"
                    "真麦克风轨照常从设备采集")
    args = ap.parse_args()
    ACRYLIC["on"] = args.acrylic   # 模块级标志：答案窗按此决定磨砂玻璃 / 灰色实底
    CHAMELEON["on"] = args.chameleon

    # 问答后端优先级：显式 --api-key / DEEPSEEK_API_KEY → DeepSeek；否则复用
    # 主识图链路的 key、模型和 URL。视觉模型是多模态模型，也可接收纯文本问题。
    deepseek_key = args.api_key or _env_get("DEEPSEEK_API_KEY")
    if deepseek_key:
        api_key = deepseek_key
        answer_model = args.model or DEEPSEEK_MODEL
        answer_url = DEEPSEEK_URL
        answer_backend = "DeepSeek"
    else:
        api_key = _env_get("ARK_API_KEY")
        answer_model = args.model or _env_get("ARK_VISION_MODEL") or VISION_MODEL
        answer_url = _env_get("VISION_BASE_URL") or VISION_BASE_URL
        answer_backend = "识图链路多模态模型"
    if not args.no_inject and not api_key:
        sys.exit("❌ 缺少问答模型凭证：请填写 DEEPSEEK_API_KEY，或配置识图链路的 ARK_API_KEY")
    print(f"🤖 问答 API: {answer_backend} / {answer_model}", flush=True)

    # 本场日志：logs/session-时间戳.jsonl（重启提词器 = 新一场）
    global LOG_FILENAME
    os.makedirs(LOG_DIR, exist_ok=True)
    LOG_FILENAME = f"session-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    log_event({"type": "session_start", "model": answer_model, "no_inject": args.no_inject})

    # 崩溃兜底：hidden-start 启动无控制台，任何线程异常都落到 logs/interview-crash.log
    def _crash_hook(etype, val, tb):
        try:
            with open(os.path.join(LOG_DIR, "interview-crash.log"), "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {etype.__name__}: {val}\n")
                import traceback as _tb
                _tb.print_exception(etype, val, tb, file=f)
        except Exception:
            pass
    def _thread_crash_hook(args):
        # threading.excepthook 回调签名是单参 ExceptionHookArgs（Py3.8+），
        # 与 sys.excepthook 的三参不同——不能共用一个函数，否则钩子先炸、原始异常丢失
        _crash_hook(args.exc_type, args.exc_value, args.exc_traceback)
    threading.excepthook = _thread_crash_hook
    sys.excepthook = _crash_hook

    # 全局状态 + UI 事件队列
    state = {"mode": "listen", "paused": False, "tp": False}   # listen=只听 / full=全听 / tp=提词器
    epoch = {"n": 0}            # 每检测到新语音 +1；答案回来时序号不符 → 作废
    push_on = {"on": True}      # F6 手机推送（兜底渠道，常驻开）
    st_lock = threading.Lock()
    ui_q = queue.Queue()

    def ui(kind, payload=None):
        # payload 可省略（F1 热键传单参 "mini"，poll 里按当前状态取反）
        ui_q.put((kind, payload))

    def set_status(text):
        ui("status", text)

    # 对话历史：重启从日志重建（↑↓ 回滚查看不丢）
    hist.clear()
    hist.extend(load_history_from_logs())
    if hist:
        print(f"📜 历史恢复: {len(hist)} 个对话（↑↓ 翻看）", flush=True)

    root = None
    if not args.no_window:
        root = show_answer_window(ui_q)
        set_capture_excluded(root, stealth["on"])   # 防捕获常驻开启

    agent = None
    if not args.no_inject:
        agent = ChatAgent(api_key, model=answer_model,
                          system_prompt=build_system_prompt(), base_url=answer_url)

    # Telegram 推送（兜底）：答案同步发到手机；token 用主 bot，目标取白名单第一个
    tg_token = _env_get("BOT_TOKEN")
    tg_ids = [int(x) for x in _env_get("ALLOWED_IDS").split(",") if x.strip()]
    tg_chat_id = tg_ids[0] if tg_ids else 0
    PROXY = _env_get("PROXY")

    def push_answer(question, answer):
        """答案推送手机（独立线程 fire-and-forget；失败只打日志，不影响主链路）"""
        if not tg_token or not tg_chat_id:
            return

        def _run():
            import requests
            try:
                text = f"❓ {question}\n\n💡 {answer}"
                if len(text) > 3900:
                    text = text[:3900]
                resp = requests.post(
                    f"https://api.telegram.org/bot{tg_token}/sendMessage",
                    data={"chat_id": tg_chat_id, "text": text},
                    proxies={"http": PROXY, "https": PROXY} if PROXY else None,
                    timeout=(10, 20))
                if not resp.json().get("ok"):
                    print(f"⚠️ 手机推送失败: {resp.text[:100]}", flush=True)
                else:
                    print("📱 答案已推送到手机", flush=True)
            except Exception as e:
                print(f"⚠️ 手机推送异常: {e}", flush=True)

        threading.Thread(target=_run, daemon=True).start()

    # 问答工作线程（串行调 API；答案作废判定靠 epoch 序号）
    # 警告：严禁并行化！void_last 替换 messages 最后一条 assistant 依赖串行顺序，
    # 并行会在 last 是 user 时静默 no-op，历史留下错误配对的 Q/A。
    answer_q = queue.Queue()

    def answer_worker():
        while True:
            text, seq, trigger = answer_q.get()
            t0 = time.time()      # API 耗时（日志复盘用）
            last_ui = {"t": 0.0}
            # 附注你的回答（自动模式）：取尚未附注过的 last_answer，拼进问题末尾
            q_text = text
            if not args.manual:
                with ans_lock:
                    if attach_on["on"] and last_answer["text"] and not last_answer["attached"]:
                        q_text = text + (f"\n【附：你此前的回答】"
                                         f"{last_answer['text'][:MY_ANSWER_MAX_CHARS]}"
                                         f"（以上是你实际口头回答，仅供参考，不是新问题）")
                        # 已附注部分砍掉，保证每段内容至多附注一次（增量累积下不重复）
                        last_answer["text"] = last_answer["text"][MY_ANSWER_MAX_CHARS:]
                        last_answer["attached"] = True
                if q_text != text:
                    print(f"📎 附注生效：问题带了你此前的回答（+{len(q_text) - len(text)}字）",
                          flush=True)

            def on_chunk(partial):
                # 节流：0.12s 内最多刷一次窗口（流式 delta 频率高，别挤爆 UI 队列）
                now = time.time()
                if now - last_ui["t"] < 0.12:
                    return
                last_ui["t"] = now
                ui("a", partial + "…")

            def should_stop():
                with st_lock:
                    return seq != epoch["n"]

            try:
                answer = agent.ask_stream(q_text, on_chunk=on_chunk,
                                          should_stop=should_stop)
            except Exception as e:
                print(f"❌ API 调用失败: {e}", flush=True)
                ui("a", f"（API 失败：{e}）")
                continue
            with st_lock:
                stale = seq != epoch["n"]
            if stale:
                agent.void_last()   # 不删历史：占位保留，追问才有上下文
                log_event({"type": "void", "question": text,
                           "partial": (answer or "").strip()[:200],
                           "api_sec": round(time.time() - t0, 2)})
                print("🗑️ 答案作废（新语音已到，历史保留）", flush=True)
                continue
            print(f"💡 答案 ({len(answer)}字)", flush=True)
            log_event({"type": "answer", "question": text, "answer": answer,
                       "api_sec": round(time.time() - t0, 2)})
            ui("a", answer)
            if push_on["on"]:
                push_answer(text, answer)

    if agent is not None:
        threading.Thread(target=answer_worker, daemon=True).start()

    # 手动录音链路（F1 开始攒、F2 结束转写 → 提问上屏 → 生成回答）
    def _manual_go(bufs):
        """F2 结束录音后：拼接音频 → 转写 → 提问上屏 → 送 answer_q"""
        try:
            audio = np.concatenate([np.frombuffer(b, dtype=np.float32) for b in bufs])
        except Exception as e:
            print(f"❌ 音频拼接失败: {e}", flush=True)
            ui("idle")
            return
        try:
            text = transcribe(audio)
        except Exception as e:
            print(f"❌ 转写出错: {e}", flush=True)
            ui("status", f"❌ 转写出错: {e}")
            ui("idle")
            return
        text = clean_asr_text(text or "")
        if not text:
            print("🔇 没识别到有效语音（环境声已丢弃）", flush=True)
            ui("status", "没识别到有效语音，可重录")
            ui("idle")
            return
        print(f"✍️ 转写: {text}", flush=True)
        with st_lock:
            seq = epoch["n"]
        log_event({"type": "question", "source": "手动", "text": text, "seq": seq})
        ui("q", text)                       # 提问先上屏（用户先确认录到了什么）
        if args.no_inject:
            print(f"🔇 [no-inject] {text}", flush=True)
            return
        answer_q.put((text, seq, "manual"))

    # ---------- 自动模式链路（默认）：双轨常开 + 门控 + 编排线程 ----------
    # 两个 PortAudio callback 只发事件（event_q），门控仲裁/VAD/双触发/作废全在编排线程串行。
    if not args.manual:
        event_q = queue.Queue()
        gate = GateState()
        recycle = deque()                # 被门控丢弃的 mic 块（回收：抢答开头不丢）
        pending = []                     # 面试官句子（16k float32 列表）
        pending_sec = 0.0                # pending 总音频秒数（上限强制发送）
        pending_since = 0.0              # 最后一次 append 的 perf_counter（B 触发计时）
        my_bufs = []                     # 你的回答 utterances（归属最近一次发送的问题）
        tq = queue.Queue()               # 转写队列：("q", audio, trigger) / ("my", audio)
        ans_lock = threading.Lock()
        last_answer = {"text": "", "attached": True}   # ans_lock 保护（转写 worker 写 / answer_worker 读）
        attach_on = {"on": AUTO_ATTACH_ON}             # F10 切换
        f9_override = {"on": False}                    # F9 按住强制解除门控
        escape_at = 0.0                # 最近一次逃生口放行时刻（期间回环 VAD 跳过）

        def ui_pending():
            ui("pending", f" · 📥 攒句 {len(pending)} 段" if pending else "")

        def on_loop_utterance(buf):
            """面试官句子 → pending（backchannel 短句丢弃）"""
            if len(buf) / SAMPLE_RATE < MIN_UTTERANCE_SEC:
                return
            nonlocal pending_sec, pending_since
            pending.append(buf)
            pending_sec += len(buf) / SAMPLE_RATE
            pending_since = time.perf_counter()
            ui_pending()

        def on_mic_utterance(buf):
            """你的回答句子 → my_bufs。攒够 MY_BATCH_SEC 音频就提交一批转写：
            长回答边讲边进上下文（不攒 10-20 分钟大音频一次转——会超时 + 卡住问题转写）。"""
            if len(buf) / SAMPLE_RATE < MIN_UTTERANCE_SEC:
                return
            my_bufs.append(buf)
            n_sec = sum(len(b) for b in my_bufs) / SAMPLE_RATE
            if n_sec >= MY_BATCH_SEC:
                tq.put(("my", np.concatenate(my_bufs)))
                my_bufs.clear()

        detector_loop = SpeechDetector(
            lambda: None,              # 快速作废走 gate.fast_onset_hit（0.4s），1.2s 确认版不用
            lambda b: event_q.put(("loop_utterance", b)))
        detector_mic = SpeechDetector(
            lambda: event_q.put(("mic_onset", None)),
            lambda b: event_q.put(("mic_utterance", b)))

        def send_question(trigger):
            """合并 pending → 转写队列。先提交 my_bufs 转写（FIFO 在前 → 附注及时），再提交问题。
            只被编排线程调用（串行），pending/my_bufs 无锁竞争。"""
            nonlocal pending, pending_sec, pending_since
            if not pending:
                return
            audio = np.concatenate(pending)
            pending, pending_sec, pending_since = [], 0.0, 0.0
            ui_pending()
            if my_bufs:
                tq.put(("my", np.concatenate(my_bufs)))
                my_bufs.clear()
            tq.put(("q", audio, trigger))

        def orchestrator():
            """单一串行处理线程：门控仲裁、VAD 喂块、双触发、打断作废。callback 只发事件。"""
            nonlocal pending_sec, pending_since, escape_at   # reset/逃生口分支要赋值（不声明会变局部）
            while True:
                try:
                    kind, obj = event_q.get(timeout=0.5)
                except queue.Empty:
                    kind = None
                try:
                    if kind == "loop_audio":
                        block, rms = obj
                        gate.feed_loop_rms(rms)
                        if gate.fast_onset_hit():
                            # 快速作废：回环连续有声 ≥0.4s（与 1.2s 句子确认解耦，打断响应快）
                            with st_lock:
                                epoch["n"] += 1
                            log_event({"type": "void_onset", "epoch": epoch["n"]})
                            print("🧠 面试官新语音…在途答案作废", flush=True)
                            set_status("🧠 面试官新语音…在途答案作废")
                        if time.perf_counter() - escape_at < ESCAPE_COOLDOWN:
                            pass   # 麦克风在收真实人声（逃生口刚放行）→ 回环此刻不是面试官，跳过 VAD
                        else:
                            detector_loop.feed(block, SAMPLE_RATE)
                    elif kind == "mic_audio":
                        block, rms = obj
                        gated = False
                        if not f9_override["on"] and gate.loop_recent():
                            # 回环在响（扬声器有回声）→ 丢弃；逃生口：mic 能量远超回声 → 放行
                            if rms <= max(gate.loop_rms * GATE_ESCAPE_RATIO, GATE_ESCAPE_ABS):
                                gated = True
                        if gated:
                            recycle.append(block)
                            maxr = int(GATE_RECYCLE_SEC * SAMPLE_RATE / BLOCK)
                            while len(recycle) > maxr:
                                recycle.popleft()
                            # 喂零块推进静音计数：面试官说话期间你的 utterance 自然断开
                            detector_mic.feed(np.zeros_like(block), SAMPLE_RATE)
                        else:
                            if recycle:          # 门控刚释放：回收缓冲先喂（抢答开头不丢）
                                for rb in recycle:
                                    detector_mic.feed(rb, SAMPLE_RATE)
                                recycle.clear()
                            detector_mic.feed(block, SAMPLE_RATE)
                            escape_at = time.perf_counter()   # 放行=麦克风在收人声→回环短暂跳过 VAD
                    elif kind == "loop_utterance":
                        on_loop_utterance(obj)
                    elif kind == "mic_onset":
                        # A 触发：你开口 = 问题边界。先 flush 回环半句并入 pending，再发送
                        flush = detector_loop.flush_now()
                        if flush is not None:
                            on_loop_utterance(flush)
                        send_question("mic_onset")
                    elif kind == "mic_utterance":
                        on_mic_utterance(obj)
                    elif kind == "reset":
                        # Ctrl+Esc 恢复：清全部状态，防陈年 pending 被 B 触发
                        detector_loop.reset()
                        detector_mic.reset()
                        pending.clear()
                        pending_sec, pending_since = 0.0, 0.0
                        my_bufs.clear()
                        recycle.clear()
                        escape_at = 0.0
                        gate.reset()
                        ui_pending()
                    elif kind == "f9_on":
                        f9_override["on"] = True
                        print("🎤 F9 按住：强制收录你的声音", flush=True)
                    elif kind == "f9_off":
                        f9_override["on"] = False
                        print("🎤 强制收录结束", flush=True)
                    # B 兜底：pending 静置 ≥B_TRIGGER_SEC（面试官陈述完你没开口）→ 发送
                    if pending and pending_since and \
                            time.perf_counter() - pending_since > B_TRIGGER_SEC:
                        send_question("silence")
                except Exception as e:
                    log_event({"type": "orchestrator_error", "err": str(e)[:200]})
                    traceback.print_exc()   # 定位用（正常无错不打印）

        threading.Thread(target=orchestrator, daemon=True).start()

        def transcribe_worker():
            """串行转写：FIFO（my 先入先转 → 附注及时）。失败不阻塞后续任务。"""
            while True:
                job = tq.get()
                kind = job[0]
                try:
                    if kind == "q":
                        text = transcribe(job[1])
                    else:
                        text = transcribe(job[1])   # 增量小批（≤MY_BATCH_SEC 音频），无需短超时
                except Exception as e:
                    log_event({"type": "transcribe_fail", "kind": kind, "err": str(e)[:200]})
                    if kind == "q":
                        print(f"❌ 问题转写出错: {e}", flush=True)
                        ui("status", f"❌ 转写出错: {e}")
                    continue
                text = clean_asr_text(text or "")
                if not text:
                    if kind == "q":
                        print("🔇 没识别到有效语音", flush=True)
                        ui("status", "没识别到有效语音")
                    continue
                if kind == "q":
                    with st_lock:
                        seq = epoch["n"]   # 采样点：转写完成、入队前（触发时取会误作废新问题）
                    log_event({"type": "question_auto", "trigger": job[2],
                               "text": text[:300], "seq": seq,
                               "audio_sec": round(len(job[1]) / SAMPLE_RATE, 1)})
                    print(f"✍️ 问题（{job[2]}）: {text}", flush=True)
                    ui("q", text)
                    answer_q.put((text, seq, job[2]))
                else:
                    with ans_lock:
                        # 增量累积：边讲边进上下文；保留最近 MY_ANSWER_TEXT_MAX 字（超出丢最旧）
                        last_answer["text"] = (last_answer["text"] + " " + text).strip()
                        if len(last_answer["text"]) > MY_ANSWER_TEXT_MAX:
                            last_answer["text"] = last_answer["text"][-MY_ANSWER_TEXT_MAX:]
                        last_answer["attached"] = False
                    log_event({"type": "my_answer", "text": text[:300]})
                    print(f"🗣 你的回答: {text[:60]}{'…' if len(text) > 60 else ''}", flush=True)
                    ui("my_answer", text)

        threading.Thread(target=transcribe_worker, daemon=True).start()

    # 音频设备（同一 PortAudio 实例：loopback + 麦克风）
    p = pyaudio.PyAudio()
    loop_idx, loop_dev = None, None
    if args.loop_tcp:
        # 面试官音频直连模式：不走声卡（VB-Audio 采集端不可用），假设备信息仅用于 WAV 落盘
        loop_dev = {"name": "面试官(TCP)", "defaultSampleRate": 44100, "maxInputChannels": 2}
    elif args.loop_device is not None:
        try:
            loop_idx = args.loop_device
            loop_dev = p.get_device_info_by_index(loop_idx)
        except OSError:
            print(f"⚠️ --loop-device {args.loop_device} 无效", flush=True)
    if not loop_dev:
        loop_idx, loop_dev = pick_loopback_device(p)
    if not loop_dev:
        sys.exit("❌ 找不到回环设备（检查默认输出设备）")
    print(f"🎧 回环: {loop_dev['name']}"
          + (f" (TCP :{args.loop_tcp})" if args.loop_tcp else f" ({int(loop_dev['defaultSampleRate'])}Hz)"),
          flush=True)

    mic_idx = mic_dev = None
    if args.mic_device is not None:
        try:
            mic_idx = args.mic_device
            mic_dev = p.get_device_info_by_index(mic_idx)
        except OSError:
            print(f"⚠️ --mic-device {args.mic_device} 无效", flush=True)
    if mic_dev is None:
        try:
            mic_idx = p.get_default_input_device_info()["index"]
            mic_dev = p.get_device_info_by_index(mic_idx)
        except OSError:
            print("⚠️ 无默认输入设备（全听模式不可用）", flush=True)
    if mic_dev:
        print(f"🎙 麦克风: {mic_dev['name']}", flush=True)

    # 自动模式：回环轨 read 线程（VB-Audio 驱动 callback 全损 0/NaN，read() 才正常）、
    # 或 TCP 直连（面试官音频不走声卡）、麦克风轨 callback（真麦克风正常）发事件给编排线程
    recorder_loop = Recorder(p, loop_idx, loop_dev, mode="tcp" if args.loop_tcp else "read",
                             on_block=(lambda b, r: event_q.put(("loop_audio", (b, r))))
                             if not args.manual else None)
    recorder_mic = Recorder(p, mic_idx, mic_dev,
                            on_block=(lambda b, r: event_q.put(("mic_audio", (b, r))))
                            if not args.manual else None)
    if args.loop_tcp:
        threading.Thread(target=loop_tcp_thread, args=(args.loop_tcp, recorder_loop),
                         daemon=True).start()

    # 全程录音落盘（复盘）：双轨原始 WAV，独立线程每 5s 刷盘 + 修补 header
    wav_writer = None
    if not args.manual:
        wav_dir = os.path.join(LOG_DIR, LOG_FILENAME[:-6] if LOG_FILENAME.endswith(".jsonl") else "rec")
        os.makedirs(wav_dir, exist_ok=True)
        wav_writer = WavWriter(
            {"interviewer": os.path.join(wav_dir, "interviewer.wav"),
             "me": os.path.join(wav_dir, "me.wav")},
            {"interviewer": recorder_loop, "me": recorder_mic})
        threading.Thread(target=wav_writer.run, daemon=True).start()
        print(f"🎙 全程录音落盘: {wav_dir}", flush=True)

    # 热键轮询（GetAsyncKeyState：F10 切模式 / F9 临时麦克风 /
    # F12 隐藏窗口 / F8 提词器模式 / F6 手机推送 / Ctrl+Esc 暂停）
    VK_F1, VK_F2 = 0x70, 0x71
    VK_F3, VK_F4 = 0x72, 0x73
    VK_F6, VK_F8, VK_F9, VK_F10 = 0x75, 0x77, 0x78, 0x79
    VK_ESC, VK_CTRL, VK_Q = 0x1B, 0x11, 0x51
    VK_UP, VK_DOWN = 0x26, 0x28
    hk = {"f10": False, "esc": False, "f4": False, "f8": False,
          "f6": False, "f1": False, "f2": False, "f3": False,
          "p": False, "f9": False, "up": False, "down": False, "esc_alone": False}
    hidden_state = {"v": False}      # F4 窗口隐藏状态

    def key_down(vk):
        try:
            return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
        except Exception:
            return False

    def set_mode(m):
        with st_lock:
            state["mode"] = m
        log_event({"type": "mode", "value": m})
        print(f"🔀 模式: {'只听（只转写面试官）' if m == 'listen' else '全听（你的话也注入）'}", flush=True)
        set_status("只听模式 · F10切换" if m == "listen" else "全听模式 · F10切换")

    def hotkey_loop():
        while True:
            time.sleep(0.08)
            f10 = key_down(VK_F10)
            if f10 and not hk["f10"]:
                if args.manual:
                    with st_lock:
                        m = state["mode"]
                    set_mode("full" if m == "listen" else "listen")
                    if recorder_mic:
                        recorder_mic.start() if m == "listen" else recorder_mic.stop()
                else:
                    # 自动模式：F10 = 你的回答是否附注给模型（默认开）
                    attach_on["on"] = not attach_on["on"]
                    print(f"🔗 附注你的回答: {'开' if attach_on['on'] else '关'}", flush=True)
                    set_status(("🔗 附注开" if attach_on["on"] else "🔗 附注关") + " · F10切换")
            hk["f10"] = f10
            esc = key_down(VK_ESC)
            if esc and not hk["esc"] and key_down(VK_CTRL):
                with st_lock:
                    state["paused"] = not state["paused"]
                    paused = state["paused"]
                if paused:
                    recorder_loop.stop()
                    if recorder_mic:
                        recorder_mic.stop()
                    if not args.manual:
                        event_q.put(("reset", None))   # 暂停也清状态（防 B 触发陈年 pending）
                    print("⏸️ 已暂停（录音+生成全停）", flush=True)
                    set_status("⏸️ 已暂停 · Ctrl+Esc恢复")
                else:
                    recorder_loop.start()
                    if recorder_mic and not args.manual:
                        recorder_mic.start()   # 自动模式：麦克风轨常开（me.wav 全程落盘）
                    if not args.manual:
                        event_q.put(("reset", None))   # 清 pending/detector/门控（防陈年触发）
                    print("▶️ 已恢复", flush=True)
                    set_status("只听模式 · F10切换" if state["mode"] == "listen"
                               else "全听模式 · F10切换")
            hk["esc"] = esc
            # ESC 单独按：一键退出进程（Ctrl+Esc 仍是暂停，上面分支处理）
            esc_alone = esc and not key_down(VK_CTRL)
            if esc_alone and not hk["esc_alone"]:
                log_event({"type": "session_end", "reason": "ESC"})
                if root is not None:
                    save_window_geometry(root.geometry())      # 记住位置，下次回到这
                print("👋 ESC 退出", flush=True)
                os._exit(0)
            hk["esc_alone"] = esc_alone
            # F1 开始录音（手动定界：录多久自己定，杜绝 VAD 误判半截问题）
            f1 = key_down(VK_F1)
            if f1 and not hk["f1"]:
                with st_lock:
                    epoch["n"] += 1          # 新一轮：在途旧答案作废
                # 手动录音两轨都攒（不区分 listen/full）：面试官问题 + 你的话都能录
                if recorder_loop:
                    recorder_loop.start_rec()
                if recorder_mic:
                    recorder_mic.start_rec()
                ui("rec_on")
                print("🎙️ 开始录音（F2 结束）", flush=True)
            hk["f1"] = f1
            # F2 结束录音：攒的音频 → 转写 → 提问上屏 → 生成回答
            f2 = key_down(VK_F2)
            if f2 and not hk["f2"]:
                bufs = []
                if recorder_loop:
                    bufs += recorder_loop.stop_rec()
                if recorder_mic:
                    bufs += recorder_mic.stop_rec()
                if not bufs:
                    ui("rec_off")
                    ui("status", "没录到内容（先按 F1 开始录音）")
                    ui("idle")
                    print("⚠️ F2 无录音内容", flush=True)
                else:
                    ui("rec_off")            # 转写中标志
                    threading.Thread(target=_manual_go, args=(bufs,), daemon=True).start()
            hk["f2"] = f2
            # F3 / P 截屏识图（面试官共享屏幕/测评题目截图，按一下直接出答案）
            # P 是测评场景主键：F 键在浏览器有默认行为（F3=查找栏）且部分测评页
            # 拦截 F 键；P 是普通字母键，答题不聚焦输入框时页面收不到任何副作用
            f3 = key_down(VK_F3)
            pkey = key_down(0x50)                       # VK_P
            if not VISION_STATE["busy"] and ((f3 and not hk["f3"]) or (pkey and not hk["p"])):
                VISION_STATE["busy"] = True
                set_status("📸 识图中…")
                threading.Thread(target=do_vision, args=(ui,), daemon=True).start()
            hk["f3"] = f3
            hk["p"] = pkey
            # F4 隐藏/显示窗口（面试官靠近/共享屏幕时一键藏）
            f4 = key_down(VK_F4)
            if f4 and not hk["f4"] and root is not None:
                hidden_state["v"] = not hidden_state["v"]
                if hidden_state["v"]:
                    root.withdraw()
                    print("🙈 窗口已隐藏（再按 F4 显示）", flush=True)
                else:
                    root.deiconify()
                    print("👁️ 窗口已显示", flush=True)
            hk["f4"] = f4
            # ↑/↓ 翻历史（上一对话/下一对话；提问+回答同屏）
            up = key_down(VK_UP)
            if up and not hk["up"]:
                ui("nav", -1)
            hk["up"] = up
            down = key_down(VK_DOWN)
            if down and not hk["down"]:
                ui("nav", 1)
            hk["down"] = down
            # F8 提词器模式开关（贴镜头小窗+大字滚动；切回普通窗）
            f8 = key_down(VK_F8)
            if f8 and not hk["f8"]:
                with st_lock:
                    state["tp"] = not state.get("tp", False)
                    tp_on = state["tp"]
                ui("tp", tp_on)
                print(f"📜 提词器模式: {'开（贴镜头）' if tp_on else '关'}", flush=True)
            hk["f8"] = f8
            # F9 按住：自动模式强制解除门控（面试官说话期间你补充/纠正，麦克风全收）
            f9 = key_down(VK_F9)
            if f9 and not hk["f9"] and not args.manual:
                with st_lock:
                    paused = state["paused"]
                if not paused:
                    event_q.put(("f9_on", None))
            elif not f9 and hk["f9"] and not args.manual:
                event_q.put(("f9_off", None))
            hk["f9"] = f9
            # F6 手机推送开关（兜底渠道，启动常驻开）
            f6 = key_down(VK_F6)
            if f6 and not hk["f6"]:
                push_on["on"] = not push_on["on"]
                print(f"📱 手机推送: {'开' if push_on['on'] else '关'}", flush=True)
                set_status(("📱 推送开" if push_on["on"] else "推送关") + " · F6切换")
            hk["f6"] = f6
            # Ctrl+Q 一键退出（进程+窗口一起没）
            if key_down(VK_CTRL) and key_down(VK_Q):
                log_event({"type": "session_end", "reason": "Ctrl+Q"})
                if root is not None:
                    save_window_geometry(root.geometry())      # 记住位置，下次回到这
                print("👋 Ctrl+Q 退出", flush=True)
                os._exit(0)

    threading.Thread(target=hotkey_loop, daemon=True).start()

    # 启动：录音流常开（F1/F2 控制攒与不攒）；防捕获与手机推送常驻开
    recorder_loop.start()
    if not args.manual:
        recorder_mic.start()   # 自动模式：麦克风轨常开（门控由编排线程仲裁）
        print("✅ 就绪（自动模式）。双轨全程录音：面试官说话自动断句攒问题，"
              "你开口（或停顿 2.5s）自动发送 → DeepSeek 作答。全程 WAV 落盘可复盘。"
              "🕶️ 防捕获常驻开，📱 手机推送常驻开（F6 可关）。"
              "F1 手动录问题兜底，F3 识图，F4 隐藏，F8 提词器，"
              "F9 按住强制收录你的话，F10 附注你的回答开关，↑↓ 翻历史，"
              "ESC/Ctrl+Q 退出，Ctrl+Esc 暂停", flush=True)
        set_status("🕶️ 防捕获开 · 📱 推送开 · 自动模式")
    else:
        print("✅ 就绪（手动模式）。F1 开始录音 → 面试官提问 → F2 结束 → 转写提问上屏 → DeepSeek 作答。"
              "🕶️ 防捕获常驻开，📱 手机推送常驻开（F6 可关）。"
              "F3 识图，F4 隐藏窗口，F8 提词器，F10 全听，↑↓ 翻历史，"
              "ESC/Ctrl+Q 退出，Ctrl+Esc 暂停", flush=True)
        set_status("🕶️ 防捕获开 · 📱 推送开 · 手动模式")
    print("=" * 50, flush=True)

    if root is not None:
        root.mainloop()
    else:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    log_event({"type": "session_end", "reason": "正常退出"})
    recorder_loop.stop()
    if recorder_mic:
        recorder_mic.stop()

if __name__ == "__main__":
    main()
