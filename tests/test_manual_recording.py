import sys
import unittest
from unittest.mock import patch

from interview_tool import engine, profiles


class _MainloopReached(Exception):
    pass


class _FakeRoot:
    def mainloop(self):
        raise _MainloopReached


class _FakePyAudio:
    def get_default_input_device_info(self):
        return {"index": 1}

    def get_device_info_by_index(self, index):
        return {"index": index, "name": "microphone",
                "defaultSampleRate": 44100, "maxInputChannels": 1}


class _FakeThread:
    def __init__(self, **_kwargs):
        pass

    def start(self):
        pass


class ManualRecordingStartupTests(unittest.TestCase):
    def test_manual_mode_starts_loopback_and_microphone_streams(self):
        recorders = []

        class FakeRecorder:
            def __init__(self, _audio, _index, device, **_kwargs):
                self.device = device
                self.start_count = 0
                recorders.append(self)

            def start(self):
                self.start_count += 1

        loop_device = {"name": "loopback", "defaultSampleRate": 48000,
                       "maxInputChannels": 2}
        with patch.object(sys, "argv", ["run_code.py", "--manual", "--no-inject"]), \
             patch.object(engine.pyaudio, "PyAudio", return_value=_FakePyAudio()), \
             patch.object(engine, "pick_loopback_device", return_value=(2, loop_device)), \
             patch.object(engine, "Recorder", FakeRecorder), \
             patch.object(engine.threading, "Thread", _FakeThread), \
             patch.object(engine, "show_answer_window", return_value=_FakeRoot()), \
             patch.object(engine, "set_capture_excluded"), \
             patch.object(engine, "_vision_providers", return_value=[
                 ("main", "", "vision-model", "https://example.invalid")
             ]), \
             patch.object(engine.log, "start_session"), \
             patch.object(engine, "load_history_from_logs", return_value=[]), \
             patch("builtins.print"):
            with self.assertRaises(_MainloopReached):
                engine.main(profiles.CODE)

        self.assertEqual(len(recorders), 2)
        self.assertEqual([recorder.start_count for recorder in recorders], [1, 1])


if __name__ == "__main__":
    unittest.main()
