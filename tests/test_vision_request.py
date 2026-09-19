import unittest
from unittest.mock import patch

from PIL import Image

from interview_tool.vision import _ask_vision_multi


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class VisionRequestTests(unittest.TestCase):
    @patch("interview_tool.vision._env_get", return_value="disabled")
    @patch("requests.post")
    def test_thinking_setting_is_added_to_chat_request(self, post, _env_get):
        post.return_value = _Response(200, {
            "choices": [{"message": {"content": "ok"}}],
        })

        answer, status = _ask_vision_multi(
            "key", "model", "https://example.invalid/chat",
            [Image.new("RGB", (2, 2), "white")], "prompt", 100,
        )

        self.assertEqual((answer, status), ("ok", 200))
        self.assertEqual(post.call_args.kwargs["json"]["thinking"],
                         {"type": "disabled"})

    @patch("interview_tool.vision._env_get", return_value="disabled")
    @patch("requests.post")
    def test_thinking_setting_is_added_to_responses_fallback(self, post, _env_get):
        post.side_effect = [
            _Response(400, {}),
            _Response(200, {
                "output": [{"type": "message", "content": [{"text": "ok"}]}],
            }),
        ]

        answer, status = _ask_vision_multi(
            "key", "model", "https://example.invalid/chat",
            [Image.new("RGB", (2, 2), "white")], "prompt", 100,
        )

        self.assertEqual((answer, status), ("ok", 200))
        fallback_body = post.call_args_list[1].kwargs["json"]
        self.assertEqual(fallback_body["thinking"], {"type": "disabled"})


if __name__ == "__main__":
    unittest.main()
