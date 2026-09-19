import inspect
import sys
import unittest
from unittest.mock import patch

from interview_tool import engine, profiles
from interview_tool.chat import (ChatAgent, DEEPSEEK_MODEL, DEEPSEEK_URL,
                                 build_system_prompt)
from interview_tool.state import stealth


class _SelectionCaptured(Exception):
    pass


class AnswerBackendSelectionTests(unittest.TestCase):
    def _select(self, deepseek_key):
        captured = {}

        class CapturingAgent:
            def __init__(self, api_key, model, system_prompt, base_url, thinking=None):
                captured.update(api_key=api_key, model=model, base_url=base_url,
                                thinking=thinking)
                raise _SelectionCaptured

        def fake_env_get(name):
            if name == "DEEPSEEK_API_KEY":
                return deepseek_key
            if name == "ANSWER_THINKING":
                return "disabled"
            return ""

        with patch.object(sys, "argv", ["run_code.py", "--no-window"]), \
             patch.object(engine, "_env_get", side_effect=fake_env_get), \
             patch.object(engine, "_vision_providers", return_value=[
                 ("main", "vision-key", "vision-model", "https://vision.example/chat/completions")
             ]), \
             patch.object(engine.log, "start_session"), \
             patch.object(engine, "load_history_from_logs", return_value=[]), \
             patch.object(engine, "build_system_prompt", return_value="system"), \
             patch("builtins.print"), \
             patch.object(engine, "ChatAgent", CapturingAgent):
            with self.assertRaises(_SelectionCaptured):
                engine.main(profiles.CODE)
        return captured

    def test_deepseek_key_has_priority(self):
        selected = self._select("deepseek-key")
        self.assertEqual(selected, {
            "api_key": "deepseek-key",
            "model": DEEPSEEK_MODEL,
            "base_url": DEEPSEEK_URL,
            "thinking": None,
        })

    def test_visual_backend_is_used_without_deepseek_key(self):
        selected = self._select("")
        self.assertEqual(selected, {
            "api_key": "vision-key",
            "model": "vision-model",
            "base_url": "https://vision.example/chat/completions",
            "thinking": "disabled",
        })


class ChatAgentEndpointTests(unittest.TestCase):
    def test_custom_base_url_is_used(self):
        requested = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

            def iter_lines(self):
                return iter([b'data: {"choices":[{"delta":{"content":"ok"}}]}',
                             b"data: [DONE]"])

            def close(self):
                pass

        class FakeSession:
            def post(self, url, **kwargs):
                requested.update(url=url, kwargs=kwargs)
                return FakeResponse()

        agent = ChatAgent("vision-key", model="vision-model", system_prompt="system",
                          base_url="https://vision.example/chat/completions")
        agent.session = FakeSession()
        self.assertEqual(agent.ask_stream("question"), "ok")
        self.assertEqual(requested["url"], "https://vision.example/chat/completions")
        self.assertEqual(requested["kwargs"]["json"]["model"], "vision-model")

    def test_optional_thinking_setting_is_added_to_request(self):
        requested = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

            def iter_lines(self):
                return iter([b'data: {"choices":[{"delta":{"content":"ok"}}]}',
                             b"data: [DONE]"])

            def close(self):
                pass

        class FakeSession:
            def post(self, _url, **kwargs):
                requested.update(kwargs)
                return FakeResponse()

        agent = ChatAgent("key", system_prompt="system", thinking="disabled")
        agent.session = FakeSession()
        self.assertEqual(agent.ask_stream("question"), "ok")
        self.assertEqual(requested["json"]["thinking"], {"type": "disabled"})

    def test_scheduled_prompt_resets_history_on_next_question(self):
        sent_messages = []

        class FakeResponse:
            def raise_for_status(self):
                pass

            def iter_lines(self):
                return iter([b'data: {"choices":[{"delta":{"content":"ok"}}]}',
                             b"data: [DONE]"])

            def close(self):
                pass

        class FakeSession:
            def post(self, _url, **kwargs):
                sent_messages.append(kwargs["json"]["messages"][:])
                return FakeResponse()

        agent = ChatAgent("key", system_prompt="old")
        agent.session = FakeSession()
        agent.ask_stream("old question")
        agent.schedule_system_prompt("new", reset_history=True)
        agent.ask_stream("new question")

        self.assertEqual(sent_messages[1], [
            {"role": "system", "content": "new"},
            {"role": "user", "content": "new question"},
        ])


class VoicePromptStructureTests(unittest.TestCase):
    def test_voice_prompt_requires_summary_before_details(self):
        previous = profiles.ACTIVE
        profiles.ACTIVE = profiles.CODE
        try:
            with patch("interview_tool.chat.RESUME_FILE", "__missing_resume__.md"), \
                 patch("builtins.print"):
                prompt = build_system_prompt()
        finally:
            profiles.ACTIVE = previous

        self.assertIn("回答必须先总后分", prompt)
        self.assertIn("开头先用 1—2 句话概括核心结论和关键点", prompt)
        self.assertIn("随后再按重要性展开", prompt)
        self.assertNotIn("回答必须先总后分", profiles.CODE.vision_prompt)


class PermanentCaptureExclusionTests(unittest.TestCase):
    def test_capture_exclusion_is_always_on_and_f7_is_unused(self):
        self.assertTrue(stealth["on"])
        engine_source = inspect.getsource(engine.main)
        self.assertNotIn("VK_F7", engine_source)
        self.assertNotIn('hk["f7"]', engine_source)
        self.assertNotIn("F7", profiles.QUIZ.banner_auto)
        self.assertNotIn("F7", profiles.CODE.banner_auto)


if __name__ == "__main__":
    unittest.main()
