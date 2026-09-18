# -*- coding: utf-8 -*-
"""chat.py — OpenAI 兼容聊天协议（ChatAgent）。

系统提示词由 profiles.ACTIVE.system_prompt（quiz/code 运行协议）与 PromptStore（单选技术
场景）组合；模块只读期 ACTIVE 为 None——只在 engine.main 激活后调用。
"""
import json
import threading

from .config import RESUME_FILE, RESUME_MAX_CHARS
from . import profiles          # quiz/code 基础规则取 ACTIVE；技术场景由调用方注入 PromptStore

# ---------- 默认问答后端：DeepSeek（未配 key 时由 engine 改用主视觉后端） ----------
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
HISTORY_TURNS = 5            # 保留最近 N 轮问答（追问承接；10 轮历史太长会带偏新话题）
VOICE_ANSWER_STRUCTURE = (
    "回答必须先总后分：开头先用 1—2 句话概括核心结论和关键点，直接回答问题；"
    "不要从定义、背景或发展过程开始铺垫。随后再按重要性展开原因、步骤、权衡和必要示例。"
    "简单问题在概括后补充 1—3 个要点即可，不为凑结构扩写。"
)

def build_system_prompt(prompt_store=None):
    """运行模式基础规则 + 先总后分回答结构 + 当前唯一技术场景 + resume.md。"""
    sp = f"{profiles.ACTIVE.system_prompt}\n\n{VOICE_ANSWER_STRUCTURE}"
    if prompt_store is not None:
        sp = prompt_store.compose_voice_prompt(sp)
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
                          "content": system_prompt if system_prompt is not None
                          else profiles.ACTIVE.system_prompt}]
        self.lock = threading.Lock()
        # Prompt 切换不等待在途 HTTP：设置线程只写 pending；串行问答线程在下一题开头应用。
        self._prompt_lock = threading.Lock()
        self._pending_prompt = None
        self._pending_reset = True

    def schedule_system_prompt(self, system_prompt, *, reset_history=True):
        """让新 Prompt 从下一次 ask_stream 生效；当前流式回答不被打断。"""
        with self._prompt_lock:
            self._pending_prompt = system_prompt
            self._pending_reset = bool(reset_history)

    def _apply_pending_prompt(self):
        with self._prompt_lock:
            prompt = self._pending_prompt
            reset = self._pending_reset
            self._pending_prompt = None
        if prompt is None:
            return
        if reset:
            self.messages = [{"role": "system", "content": prompt}]
        elif self.messages:
            self.messages[0] = {"role": "system", "content": prompt}
        else:
            self.messages = [{"role": "system", "content": prompt}]

    def ask_stream(self, question, on_chunk=None, should_stop=None):
        """流式问一轮：边生成边回调 on_chunk(当前全文)，返回完整答案文本。
        should_stop() 返回 True 时中断请求（新语音打断，不再白等生成完）；
        中断时返回已生成的部分文本。异常直接抛给调用方。"""
        with self.lock:
            self._apply_pending_prompt()
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
