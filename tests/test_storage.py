import json
import os
import tempfile
import unittest
import wave
from unittest.mock import patch

import numpy as np
from PIL import Image

from interview_tool import log, storage, ui
from interview_tool.config import BASE_DIR
from interview_tool.engine import _mix_manual_tracks


class SessionStorageTests(unittest.TestCase):
    def test_saves_screenshot_audio_and_linked_transcript(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(storage, "_env_bool", return_value=True):
            root = storage.start_session("session-test", base_dir=tmp)
            request_id = storage.new_request("vision", model="vision-model")
            screenshot = storage.save_screenshot(
                Image.new("RGB", (32, 24), "white"), request_id=request_id)
            audio = storage.save_audio_clip(np.array([0.0, 0.5, -0.5], dtype=np.float32),
                                            "input", request_id=request_id)
            transcript = storage.save_transcript(
                "interviewer", "测试问题", request_id=request_id, audio=audio)

            self.assertTrue(os.path.isfile(os.path.normpath(os.path.join(BASE_DIR, screenshot))))
            audio_path = os.path.normpath(os.path.join(BASE_DIR, audio))
            self.assertTrue(os.path.isfile(audio_path))
            with wave.open(audio_path, "rb") as handle:
                self.assertEqual(handle.getframerate(), 16000)
                self.assertEqual(handle.getnchannels(), 1)
                self.assertEqual(handle.getnframes(), 3)
            self.assertEqual(os.path.normpath(os.path.join(BASE_DIR, transcript)),
                             os.path.join(root, "transcripts.jsonl"))
            self.assertTrue(os.path.isfile(
                os.path.join(root, request_id, "request.json")))
            self.assertTrue(os.path.isfile(
                os.path.join(root, request_id, "transcript.json")))
            with open(os.path.join(root, "transcripts.jsonl"), encoding="utf-8") as handle:
                record = json.loads(handle.readline())
            self.assertEqual(record["text"], "测试问题")
            self.assertEqual(record["audio"], audio)
            self.assertEqual(record["request_id"], request_id)

    def test_each_start_gets_a_unique_session_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = storage.start_session("session-same", base_dir=tmp)
            second = storage.start_session("session-same", base_dir=tmp)

            self.assertNotEqual(first, second)
            self.assertTrue(os.path.isdir(first))
            self.assertTrue(os.path.isdir(second))

    def test_event_log_lives_inside_session_and_history_can_reload_it(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(log, "LOG_DIR", tmp), \
             patch.object(storage, "LOG_DIR", tmp), \
             patch.object(ui, "LOG_DIR", tmp), \
             patch.object(log, "_env_bool", return_value=True):
            log.start_session("test-model", False)
            log.log_event({"type": "question", "text": "问题"})
            log.log_event({"type": "answer", "answer": "回答"})

            event_path = os.path.join(tmp, log.LOG_FILENAME)
            self.assertEqual(os.path.basename(event_path), "events.jsonl")
            self.assertTrue(os.path.isfile(event_path))
            self.assertEqual(ui.load_history_from_logs(), [{"q": "问题", "a": "回答"}])

    def test_manual_tracks_are_aligned_instead_of_concatenated(self):
        loop = [np.ones(4, dtype=np.float32)]
        mic = [np.zeros(2, dtype=np.float32)]

        mixed, loop_audio, mic_audio = _mix_manual_tracks(loop, mic)

        np.testing.assert_allclose(mixed, [0.5, 0.5, 1.0, 1.0])
        self.assertEqual(len(mixed), 4)
        self.assertEqual(len(loop_audio), 4)
        self.assertEqual(len(mic_audio), 2)


if __name__ == "__main__":
    unittest.main()
