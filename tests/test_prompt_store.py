import json
import os
import tempfile
import unittest
from unittest.mock import patch

from interview_tool.prompt_store import PromptStore, PromptStoreError


class PromptStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "prompt-scenes.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_only_one_scene_is_active_and_both_prompts_follow_it(self):
        store = PromptStore(self.path)
        selected = store.select_scene("ai-infra")

        self.assertEqual(selected["id"], "ai-infra")
        self.assertEqual(store.get_active_scene()["id"], "ai-infra")
        self.assertIn("AI Infra", store.compose_voice_prompt("voice-base"))
        self.assertIn("AI Infra", store.compose_vision_prompt("vision-base"))

        store.select_scene("frontend")
        voice = store.compose_voice_prompt("voice-base")
        vision = store.compose_vision_prompt("vision-base")
        self.assertIn("前端", voice)
        self.assertIn("前端", vision)
        self.assertNotIn("AI Infra", voice)
        self.assertNotIn("AI Infra", vision)

    def test_builtin_edits_persist_and_can_be_reset(self):
        store = PromptStore(self.path)
        store.save_scene("frontend", {
            "name": "我的前端",
            "default_language": "TypeScript",
            "voice_prompt": "只回答 Vue 3",
            "vision_prompt": "只生成组合式 API",
        })

        reloaded = PromptStore(self.path)
        self.assertEqual(reloaded.get_active_scene()["name"], "我的前端")
        self.assertIn("只回答 Vue 3", reloaded.compose_voice_prompt("base"))

        reset = reloaded.reset_builtin("frontend")
        self.assertEqual(reset["name"], "前端")
        self.assertNotIn("只回答 Vue 3", reloaded.compose_voice_prompt("base"))

    def test_custom_scene_lifecycle(self):
        store = PromptStore(self.path)
        created = store.create_scene({
            "name": "后端",
            "default_language": "Java",
            "voice_prompt": "关注 JVM",
            "vision_prompt": "关注 Spring",
        })
        self.assertFalse(created["builtin"])
        self.assertEqual(store.get_active_scene()["id"], created["id"])

        active = store.delete_scene(created["id"])
        self.assertEqual(active["id"], "general")
        with self.assertRaises(PromptStoreError):
            store.get_scene(created["id"])

    def test_invalid_file_falls_back_to_general_without_overwriting_it(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{broken")
        store = PromptStore(self.path)
        self.assertEqual(store.get_active_scene()["id"], "general")
        self.assertTrue(store.load_error)
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{broken")

    def test_invalid_active_id_type_falls_back_to_general(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({
                "version": 1,
                "active_scene_id": [],
                "builtin_overrides": {},
                "custom_scenes": {},
            }, f)
        store = PromptStore(self.path)
        self.assertEqual(store.get_active_scene()["id"], "general")

    def test_saved_config_contains_no_parallel_active_scene_list(self):
        store = PromptStore(self.path)
        store.select_scene("ai-infra")
        with open(self.path, encoding="utf-8") as f:
            raw = json.load(f)
        self.assertEqual(raw["active_scene_id"], "ai-infra")
        self.assertNotIn("active_scene_ids", raw)

    def test_failed_persist_rolls_back_memory_state(self):
        store = PromptStore(self.path)
        with patch.object(store, "_persist_locked", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                store.select_scene("ai-infra")
        self.assertEqual(store.get_active_scene()["id"], "general")
        self.assertEqual(store.revision, 0)


if __name__ == "__main__":
    unittest.main()
