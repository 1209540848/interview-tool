# -*- coding: utf-8 -*-
"""chat.py — DeepSeek 聊天协议（ChatAgent）。

系统提示词收敛为 profiles.ACTIVE.system_prompt（两版差异文本收进 profiles.py）；
模块只读期 ACTIVE 为 None——本模块只在 engine.main 激活后才会被调用，无空窗。
SYSTEM_PROMPT 模块常量已随收敛移除：差异文本唯 1 副本 = profiles 字段，杜绝双抄漂移。
"""
import json
import threading

from .config import RESUME_FILE, RESUME_MAX_CHARS
from . import profiles          # 提示词取 ACTIVE.system_prompt（差异文本在 profiles，本模块零场景判断）

# ---------- DeepSeek（OpenAI 兼容） ----------
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
HISTORY_TURNS = 5            # 保留最近 N 轮问答（追问承接；10 轮历史太长会带偏新话题）

def build_system_prompt():
    """ACTIVE.system_prompt + resume.md 简历（≤RESUME_MAX_CHARS；文件缺失/读失败 → 警告跳过不炸）"""
    sp = profiles.ACTIVE.system_prompt       # 收敛编辑：原 SYSTEM_PROMPT 模块常量 → profiles 字段（差异收容）
    try:
        with open(RESUME_FILE, encoding="utf-8") as f:
            resume = f.read().strip()
        if resume:
            sp += (f"\n\n以下是用户的简历，回答时结合简历给出贴合个人经历的答案要点"
                   f"（不要复述简历本身）：\n{resume[:RESUME_MAX_CHARS]}")
    except OSError:
        print("⚠️ resume.md 不存在（简历注入跳过，可在工具根目录放 resume.md）",
              flush=True)
    return sp


# ---------- 问答 agent：DeepSeek API + 内存对话历史 ----------
class ChatAgent:
    """OpenAI 兼容 API 问答。历史保留最近 HISTORY_TURNS 轮（追问承接）；
    作废轮（新语音打断）通过 drop_last_pair 从历史移除。串行调用，锁保护。"""
    def __init__(self, api_key, model=DEEPSEEK_MODEL, system_prompt=None):
        import requests
        self.session = requests.Session()
        self.api_key = api_key
        self.model = model
        self.messages = [{"role": "system",
                          "content": system_prompt if system_prompt is not None
                          else profiles.ACTIVE.system_prompt}]
        self.lock = threading.Lock()

    def ask_stream(self, question, on_chunk=None, should_stop=None):
        """流式问一轮：边生成边回调 on_chunk(当前全文)，返回完整答案文本。
        should_stop() 返回 True 时中断请求（新语音打断，不再白等生成完）；
        中断时返回已生成的部分文本。异常直接抛给调用方。"""
        with self.lock:
            self.messages.append({"role": "user", "content": question})
            resp = self.session.post(
                DEEPSEEK_URL,
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
