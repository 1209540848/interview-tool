import json
import tempfile
import unittest
import urllib.error
import urllib.request

from interview_tool.helper_link import HelperLink

TOKEN = "a" * 32


class HelperLinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.answers = []
        self.link = HelperLink(on_answer=self.answers.append, port=0,
                               bind="127.0.0.1", token=TOKEN,
                               base_dir=self.tmp.name)
        self.link.start()
        self.base = self.link.url

    def tearDown(self):
        self.link.stop()
        self.tmp.cleanup()

    def _url(self, path):
        return self.base.replace("/?t=", path + ("&t=" if "?" in path else "?t="))

    def _get_json(self, path):
        with urllib.request.urlopen(self._url(path), timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _post_answer(self, qid, text, client_id):
        req = urllib.request.Request(
            self._url("/api/answer"),
            data=json.dumps({"qid": qid, "text": text,
                             "client_id": client_id}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_page_and_ping(self):
        with urllib.request.urlopen(self.base, timeout=5) as resp:
            html = resp.read()
        self.assertIn("面试互助".encode("utf-8"), html)
        self.assertTrue(self._get_json("/api/ping")["ok"])

    def test_question_history_and_since(self):
        first = self.link.publish_question("第一题")
        second = self.link.publish_question("第二题")
        self.assertEqual((first["seq"], second["seq"]), (1, 2))

        history = self._get_json("/api/history?since=0")
        self.assertEqual([e["text"] for e in history["events"]],
                         ["第一题", "第二题"])
        only_new = self._get_json("/api/history?since=1")
        self.assertEqual([e["text"] for e in only_new["events"]], ["第二题"])

    def test_long_poll_wakes_on_publish(self):
        def publish_later():
            import time
            time.sleep(0.15)
            self.link.publish_question("迟到的问题")

        import threading
        threading.Thread(target=publish_later, daemon=True).start()
        data = self._get_json("/api/poll?since=0&timeout=3")
        self.assertEqual(data["events"][0]["text"], "迟到的问题")
        self.assertEqual(data["now"], 1)

    def test_answer_callback_and_duplicate(self):
        q = self.link.publish_question("题目")
        result = self._post_answer(q["id"], "朋友答案", "cid-1")
        self.assertFalse(result["duplicate"])
        self.assertEqual(len(self.answers), 1)
        self.assertEqual(self.answers[0]["text"], "朋友答案")
        self.assertEqual(self.answers[0]["qseq"], q["seq"])

        again = self._post_answer(q["id"], "朋友答案", "cid-1")
        self.assertTrue(again["duplicate"])
        self.assertEqual(len(self.answers), 1)
        self.assertEqual(self.link.latest_answer()["text"], "朋友答案")

    def test_wrong_token_is_rejected(self):
        bad = self.base.replace(TOKEN, "b" * 32)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(bad, timeout=5)
        self.assertEqual(ctx.exception.code, 403)

    def test_screenshot_image_roundtrip(self):
        event = self.link.publish_screenshot(b"\xff\xd8\xff\xd9", "截图")
        self.assertEqual(event["kind"], "shot")
        with urllib.request.urlopen(
                self._url(event["image"]), timeout=5) as resp:
            self.assertEqual(resp.read(), b"\xff\xd8\xff\xd9")

    def test_persistence_and_seq_continue(self):
        self.link.publish_question("重启前")
        self.link.stop()
        again = HelperLink(port=0, bind="127.0.0.1", token=TOKEN,
                           base_dir=self.tmp.name)
        try:
            again.start()
            self.assertEqual(again.max_seq(), 1)
            event = again.publish_question("重启后")
            self.assertEqual(event["seq"], 2)
        finally:
            again.stop()

    def test_single_instance_lock(self):
        second = HelperLink(port=0, bind="127.0.0.1", token=TOKEN,
                            base_dir=self.tmp.name)
        with self.assertRaises(RuntimeError):
            second.start()


if __name__ == "__main__":
    unittest.main()
