import unittest

from PIL import Image

from interview_tool import ui
from interview_tool.state import TYPING_STATE, VIS_MEM, VIS_MEM_LOCK
from interview_tool.vision import _vis_mem_note, _vis_mem_reset, _vis_mem_snapshot


class VisionResetTests(unittest.TestCase):
    def setUp(self):
        self.hist = [dict(item) for item in ui.hist]
        self.cur = ui.cur
        self.view = dict(ui.VIEW)
        self.typing = dict(TYPING_STATE)
        with VIS_MEM_LOCK:
            self.vis_mem = {
                "imgs": list(VIS_MEM["imgs"]),
                "ans": list(VIS_MEM["ans"]),
                "generation": VIS_MEM["generation"],
            }

    def tearDown(self):
        ui.hist[:] = self.hist
        ui.cur = self.cur
        ui.VIEW.clear()
        ui.VIEW.update(self.view)
        TYPING_STATE.clear()
        TYPING_STATE.update(self.typing)
        with VIS_MEM_LOCK:
            VIS_MEM["imgs"] = self.vis_mem["imgs"]
            VIS_MEM["ans"] = self.vis_mem["ans"]
            VIS_MEM["generation"] = self.vis_mem["generation"]

    def test_alt3_clears_current_view_but_keeps_history(self):
        old_history = [{"q": "旧题", "a": "旧答案"}]
        ui.hist[:] = old_history
        ui.cur = 0
        ui.VIEW.update({"stage": "done", "panel": "notes", "blank": False})
        TYPING_STATE.update({
            "armed": True,
            "busy": True,
            "stop": False,
            "paused": True,
            "text": "旧答案",
            "pos": 3,
        })

        ui.clear_vision_view_state()

        self.assertEqual(ui.hist, old_history)
        self.assertEqual(ui.cur, -1)
        self.assertEqual(ui.VIEW["stage"], "idle")
        self.assertTrue(ui.VIEW["blank"])
        self.assertFalse(TYPING_STATE["armed"])
        self.assertFalse(TYPING_STATE["paused"])
        self.assertTrue(TYPING_STATE["stop"])
        self.assertEqual(TYPING_STATE["text"], "")
        self.assertEqual(TYPING_STATE["pos"], 0)

    def test_reset_invalidates_inflight_memory_write(self):
        with VIS_MEM_LOCK:
            VIS_MEM["imgs"] = ["old-image"]
            VIS_MEM["ans"] = ["old-answer"]
            VIS_MEM["generation"] = 8

        generation, parts, suffix, count = _vis_mem_snapshot()
        self.assertEqual((generation, count), (8, 1))
        self.assertEqual(parts, [{"b64": "old-image"}])
        self.assertIn("old-answer", suffix)

        self.assertTrue(_vis_mem_reset("test"))
        accepted = _vis_mem_note(Image.new("RGB", (2, 2), "white"),
                                 "late answer", generation)

        self.assertFalse(accepted)
        with VIS_MEM_LOCK:
            self.assertEqual(VIS_MEM["generation"], 9)
            self.assertEqual(VIS_MEM["imgs"], [])
            self.assertEqual(VIS_MEM["ans"], [])


if __name__ == "__main__":
    unittest.main()
