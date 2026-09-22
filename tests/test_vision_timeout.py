import time
import unittest
from unittest.mock import patch

import requests
from PIL import Image

from interview_tool.vision import (_ask_vision_multi, _ask_vision_retry,
                                   _vision_timeout_seconds)


class _Response:
    def __init__(self, status_code=200, lines=None):
        self.status_code = status_code
        self.lines = lines or [
            b'data: {"choices":[{"delta":{"content":"ok"}}]}',
            b"data: [DONE]",
        ]
        self.closed = False

    def json(self):
        return {"choices": [{"message": {"content": "ok"}}]}

    def iter_lines(self, **_kwargs):
        return iter(self.lines)

    def close(self):
        self.closed = True


class VisionTimeoutTests(unittest.TestCase):
    @patch("interview_tool.vision._env_get", return_value="240")
    def test_timeout_is_loaded_from_env(self, _env_get):
        self.assertEqual(_vision_timeout_seconds(), 240.0)

    @patch("interview_tool.vision._env_get", return_value="not-a-number")
    def test_invalid_timeout_uses_default(self, _env_get):
        self.assertEqual(_vision_timeout_seconds(), 120.0)

    @patch("interview_tool.vision.time.monotonic", return_value=100.0)
    @patch("requests.post", return_value=_Response())
    def test_remaining_budget_is_used_as_read_timeout(self, post, _monotonic):
        answer, status = _ask_vision_multi(
            "key", "model", "https://example.invalid/chat",
            [Image.new("RGB", (2, 2), "white")], "prompt", 100,
            deadline=340.0,
        )

        self.assertEqual((answer, status), ("ok", 200))
        self.assertEqual(post.call_args.kwargs["timeout"], (15.0, 240.0))
        self.assertTrue(post.call_args.kwargs["stream"])

    @patch("interview_tool.vision._env_get")
    @patch("requests.post")
    def test_thinking_is_disabled_and_chat_answer_streams(self, post, env_get):
        env_get.side_effect = lambda name: (
            "disabled" if name == "VISION_THINKING" else "")
        post.return_value = _Response(lines=[
            b'data: {"choices":[{"delta":{"content":"hel"}}]}',
            b'data: {"choices":[{"delta":{"content":"lo"}}]}',
            b"data: [DONE]",
        ])
        chunks = []

        answer, status = _ask_vision_multi(
            "key", "model", "https://example.invalid/chat",
            [Image.new("RGB", (2, 2), "white")], "prompt", 100,
            deadline=time.monotonic() + 240, on_chunk=chunks.append,
        )

        self.assertEqual((answer, status), ("hello", 200))
        self.assertEqual(chunks, ["hel", "hello"])
        self.assertEqual(post.call_args.kwargs["json"]["thinking"],
                         {"type": "disabled"})
        self.assertTrue(post.call_args.kwargs["json"]["stream"])

    @patch("interview_tool.vision._env_get", return_value="disabled")
    @patch("requests.post")
    def test_responses_fallback_stream_is_parsed(self, post, _env_get):
        post.side_effect = [
            _Response(status_code=400),
            _Response(lines=[
                b'data: {"type":"response.output_text.delta","delta":"A"}',
                b'data: {"type":"response.output_text.delta","delta":"B"}',
                b"data: [DONE]",
            ]),
        ]

        answer, status = _ask_vision_multi(
            "key", "model", "https://example.invalid/chat",
            [Image.new("RGB", (2, 2), "white")], "prompt", 100,
            deadline=time.monotonic() + 240,
        )

        self.assertEqual((answer, status), ("AB", 200))
        fallback_body = post.call_args_list[1].kwargs["json"]
        self.assertTrue(fallback_body["stream"])
        self.assertEqual(fallback_body["thinking"], {"type": "disabled"})

    @patch("interview_tool.vision._ask_vision_multi")
    def test_read_timeout_does_not_restart_long_request(self, ask):
        ask.side_effect = requests.exceptions.ReadTimeout("slow model")

        with self.assertRaises(requests.exceptions.ReadTimeout):
            _ask_vision_retry(
                "key", "model", "https://example.invalid/chat",
                [], "prompt", 100, deadline=time.monotonic() + 240,
            )

        self.assertEqual(ask.call_count, 1)

    @patch("interview_tool.vision.log_event")
    @patch("interview_tool.vision._ask_vision_multi")
    def test_connection_error_retries_with_same_deadline(self, ask, log_event):
        ask.side_effect = [requests.exceptions.ConnectionError("reset"),
                           ("ok", 200)]
        deadline = time.monotonic() + 240

        result = _ask_vision_retry(
            "key", "model", "https://example.invalid/chat",
            [], "prompt", 100, deadline=deadline,
        )

        self.assertEqual(result, ("ok", 200))
        self.assertEqual(ask.call_count, 2)
        self.assertTrue(all(call.kwargs["deadline"] == deadline
                            for call in ask.call_args_list))
        log_event.assert_called_once()


if __name__ == "__main__":
    unittest.main()
