import unittest

from interview_tool.ui import (_begin_window_drag, _cancel_window_drag,
                               _move_window_drag)


class _Widget:
    def __init__(self, no_drag=False):
        self._no_window_drag = no_drag


class _Event:
    def __init__(self, widget, x_root=0, y_root=0):
        self.widget = widget
        self.x_root = x_root
        self.y_root = y_root


class _Root:
    def __init__(self, x=30, y=40):
        self._x = x
        self._y = y
        self._drag = None
        self.geometry_calls = []

    def winfo_x(self):
        return self._x

    def winfo_y(self):
        return self._y

    def geometry(self, value):
        self.geometry_calls.append(value)


class WindowDragTests(unittest.TestCase):
    def test_regular_background_can_move_window(self):
        root = _Root()
        widget = _Widget()

        _begin_window_drag(root, _Event(widget, 100, 100))
        result = _move_window_drag(root, _Event(widget, 115, 108))

        self.assertEqual(result, "break")
        self.assertEqual(root.geometry_calls, ["+45+48"])

    def test_control_motion_cancels_stale_drag_without_moving(self):
        root = _Root()
        root._drag = (10, 10, 30, 40)  # 上一次拖动遗留的起点
        tab = _Widget(no_drag=True)

        result = _move_window_drag(root, _Event(tab, 200, 200))

        self.assertEqual(result, "break")
        self.assertIsNone(root._drag)
        self.assertEqual(root.geometry_calls, [])

    def test_release_always_clears_drag_origin(self):
        root = _Root()
        root._drag = (10, 10, 30, 40)

        _cancel_window_drag(root)

        self.assertIsNone(root._drag)


if __name__ == "__main__":
    unittest.main()
