import os
import tempfile
import unittest
import wave

import numpy as np

from interview_tool.audio import Recorder, WavWriter


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

    def test_capture_raw_false_does_not_accumulate_unconsumed_audio(self):
        device = {"defaultSampleRate": 16000, "maxInputChannels": 1}
        recorder = Recorder(None, 1, device, mode="read", capture_raw=False)
        recorder._sr = 16000
        recorder._ch = 1
        recorder._process(np.array([100, -100], dtype=np.int16).tobytes())

        self.assertEqual(recorder.take_raw(), [])


class WavWriterTests(unittest.TestCase):
    def test_close_flushes_pending_blocks(self):
        class FakeRecorder:
            device = {"defaultSampleRate": 16000}

            def __init__(self):
                self.blocks = [np.array([1, 2, 3], dtype=np.int16)]

            def take_raw(self):
                blocks, self.blocks = self.blocks, []
                return blocks

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "voice.wav")
            writer = WavWriter({"voice": path}, {"voice": FakeRecorder()})
            writer.close()
            with wave.open(path, "rb") as handle:
                self.assertEqual(handle.getnframes(), 3)


if __name__ == "__main__":
    unittest.main()
