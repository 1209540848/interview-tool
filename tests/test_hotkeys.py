import threading
import unittest
from unittest.mock import patch

from interview_tool.hotkeys import (_ACTION_SPECS, HotkeyBindings, HotkeyRuntime,
                                    HotkeyState, parse_binding)


def _env(values):
    return lambda name: values.get(name, "")


class HotkeyParsingTests(unittest.TestCase):
    def test_single_combo_and_alternatives_are_canonicalized(self):
        self.assertEqual(parse_binding("f7").label, "F7")
        self.assertEqual(parse_binding("shift+ctrl+h").label, "Ctrl+Shift+H")
        self.assertEqual(parse_binding("f3 | p").label, "F3 / P")

    def test_none_disables_binding(self):
        binding = parse_binding("none")
        self.assertEqual(binding.label, "未绑定")
        self.assertFalse(binding.chords)

    def test_combo_requires_exact_modifiers(self):
        chord = parse_binding("Ctrl+H").chords[0]

        def reader_for(*codes):
            down = set(codes)
            return lambda code: code in down

        self.assertTrue(chord.is_down(reader_for(0x11, ord("H"))))
        self.assertFalse(chord.is_down(reader_for(0x11, 0x10, ord("H"))))
        self.assertFalse(chord.is_down(reader_for(ord("H"))))

    def test_invalid_binding_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "未知按键"):
            parse_binding("Ctrl+NotAKey")


class HotkeyConfigurationTests(unittest.TestCase):
    def test_profile_defaults_are_preserved(self):
        quiz = HotkeyBindings("quiz", env_get=_env({}), reader=lambda _key: False)
        code = HotkeyBindings("code", env_get=_env({}), reader=lambda _key: False)
        self.assertEqual(quiz.label("vision"), "F3 / P")
        self.assertEqual(quiz.label("type_answer"), "1")
        self.assertEqual(code.label("vision"), "Alt+P")
        self.assertEqual(code.label("type_answer"), "Alt+1")

    def test_profile_value_overrides_general_value(self):
        bindings = HotkeyBindings(
            "code",
            env_get=_env({
                "HOTKEY_TOGGLE_WINDOW": "F7",
                "CODE_HOTKEY_TOGGLE_WINDOW": "Ctrl+Shift+H",
            }),
            reader=lambda _key: False,
        )
        self.assertEqual(bindings.label("toggle_window"), "Ctrl+Shift+H")

    def test_invalid_value_falls_back_and_warns(self):
        bindings = HotkeyBindings(
            "code",
            env_get=_env({"HOTKEY_TOGGLE_WINDOW": "Ctrl+NoSuchKey"}),
            reader=lambda _key: False,
        )
        self.assertEqual(bindings.label("toggle_window"), "F4")
        self.assertTrue(any("已回退 F4" in warning for warning in bindings.warnings))

    def test_conflicts_are_reported(self):
        bindings = HotkeyBindings(
            "code",
            env_get=_env({
                "HOTKEY_TOGGLE_WINDOW": "F7",
                "HOTKEY_TOGGLE_PUSH": "F7",
            }),
            reader=lambda _key: False,
        )
        self.assertTrue(any("快捷键冲突：F7" in warning
                            for warning in bindings.warnings))

    def test_poll_reports_press_hold_and_release_edges(self):
        down = set()
        bindings = HotkeyBindings(
            "code",
            env_get=_env({"HOTKEY_TOGGLE_WINDOW": "F7"}),
            reader=lambda key: key in down,
        )
        self.assertFalse(bindings.poll()["toggle_window"].pressed)
        down.add(0x76)
        state = bindings.poll()["toggle_window"]
        self.assertTrue(state.down)
        self.assertTrue(state.pressed)
        self.assertFalse(bindings.poll()["toggle_window"].pressed)
        down.clear()
        self.assertTrue(bindings.poll()["toggle_window"].released)


class HotkeyRuntimeTests(unittest.TestCase):
    def test_configured_window_key_dispatches_toggle_action(self):
        class StopLoop(Exception):
            pass

        class FakeBindings:
            calls = 0

            def poll(self):
                self.calls += 1
                if self.calls > 1:
                    raise StopLoop
                states = {
                    action: HotkeyState(False, False, False)
                    for action in _ACTION_SPECS
                }
                states["toggle_window"] = HotkeyState(True, True, False)
                return states

            def label(self, action):
                return "F7" if action == "toggle_window" else "unused"

        class FakeRoot:
            _user_hidden = False

            def __init__(self):
                self.withdraw_count = 0

            def withdraw(self):
                self.withdraw_count += 1

            def deiconify(self):
                raise AssertionError("first toggle should hide the window")

        root = FakeRoot()
        runtime = HotkeyRuntime(
            bindings=FakeBindings(), profile_key="code", manual=True,
            root=root, ui=lambda *_args: None, set_status=lambda _text: None,
            state={"mode": "full", "paused": False},
            state_lock=threading.Lock(), epoch={"n": 0},
            recorder_loop=None, recorder_mic=None, event_q=None,
            attach_on=None, prompt_store=None, type_answer=lambda _text: None,
            paste_answer=lambda: None, do_vision=lambda *_args: None,
            reset_vision=lambda _reason: False,
            save_geometry=lambda _geometry: None,
            manual_handler=lambda _buffers: None,
        )

        with patch("interview_tool.hotkeys.time.sleep"), patch("builtins.print"), \
             self.assertRaises(StopLoop):
            runtime.run()

        self.assertTrue(root._user_hidden)
        self.assertEqual(root.withdraw_count, 1)


if __name__ == "__main__":
    unittest.main()
