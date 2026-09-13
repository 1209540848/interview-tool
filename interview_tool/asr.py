# -*- coding: utf-8 -*-
"""asr.py — 云端转写（ISI 昆仑万维 + DashScope 备用降级）+ ASR 文本清洗。"""
import json
import re
import threading
import time

import numpy as np

from .config import SAMPLE_RATE, _env_get
from .log import log_event

# ---------- 凭证 / ISI token（照 voice-bridge） ----------
ISI_WS_URL = "wss://nls-gateway.cn-shanghai.aliyuncs.com/ws/v1"
ISI_TOKEN_URL = "https://nls-meta.cn-shanghai.aliyuncs.com/"
ISI_TOKEN_VERSION = "2019-02-28"
DASHSCOPE_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference/"
DASHSCOPE_MODEL = "fun-asr-realtime"
DASHSCOPE_LANGUAGE = "zh"


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

async def _connect_with_fallback(url, kw1, kw2):
    """websockets 头部参数名历史漂移（≤13 additional_headers → 14/15 extra_headers
    → 16+ 改回 additional_headers）：错名会被 connect 的 **kwargs 吞掉、转发给
    loop.create_connection → TypeError（2026-09-13 用户报错根因，环境 17.1）。
    运行时试错兜底，任何版本都能连上"""
    import websockets
    try:
        return await websockets.connect(url, **kw1)
    except TypeError:
        return await websockets.connect(url, **kw2)

def _ws_connect(url, headers, **kwargs):
    return _connect_with_fallback(url, dict(additional_headers=headers, **kwargs),
                                  dict(extra_headers=headers, **kwargs))

def isi_transcribe(audio, appkey, ak_id, ak_secret, timeout=60):
    """ISI 实时识别（照 voice-bridge：等 TranscriptionStarted 再发音频，16000B/块）"""
    import asyncio
    import uuid
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
    token = get_isi_token(ak_id, ak_secret)

    async def _run():
        sentences, final = [], ""
        headers = {"X-NLS-Token": token}
        ws = await _ws_connect(ISI_WS_URL, headers, max_size=4 * 1024 * 1024)
        async with ws:
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
        ws = await _ws_connect(DASHSCOPE_WS_URL, headers, max_size=4 * 1024 * 1024)
        async with ws:
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
            # 静默启动无控制台：提供方失败原因必须落盘，否则「转写全失败」无法定位
            log_event({"type": "asr_provider_fail", "provider": "isi",
                       "err": f"{type(e).__name__}: {e}"[:200]})
    key = _env_get("DASHSCOPE_API_KEY")
    if key:
        try:
            return cloud_transcribe(audio, key, timeout=timeout)
        except Exception as e:
            print(f"⚠️ DashScope 转写失败: {e}", flush=True)
            log_event({"type": "asr_provider_fail", "provider": "dashscope",
                       "err": f"{type(e).__name__}: {e}"[:200]})
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
