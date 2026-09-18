import unittest

from interview_tool.audio import Recorder


class _FakeStream:
    def stop_stream(self):
        pass

    def close(self):
        pass


class _FakePyAudio:
    def __init__(self):
        self.open_count = 0

    def open(self, **_kwargs):
        self.open_count += 1
        return _FakeStream()


class RecorderRestartTests(unittest.TestCase):
    def test_start_clears_stop_event_when_reopening_microphone(self):
        audio = _FakePyAudio()
        device = {"defaultSampleRate": 16000, "maxInputChannels": 1}
        recorder = Recorder(audio, 1, device)

        recorder.start()
        recorder.stop()
        self.assertTrue(recorder._stop.is_set())

        recorder.start()

        self.assertFalse(recorder._stop.is_set())
        self.assertEqual(audio.open_count, 2)
        self.assertIsNotNone(recorder._stream)


if __name__ == "__main__":
    unittest.main()
