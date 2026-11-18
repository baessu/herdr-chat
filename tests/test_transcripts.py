import json
import os
import random
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import transcripts as T  # noqa: E402

FX = os.path.join(os.path.dirname(__file__), "fixtures")


def read_all(kind, path, limit):
    pages, cur = [], None
    while True:
        ms, cur = T.read_messages(kind, path, before=cur, limit=limit)
        pages.insert(0, ms)
        if cur is None:
            return [m for p in pages for m in p]


class Claude(unittest.TestCase):
    def setUp(self):
        self.ms, self.cur = T.read_messages("claude", f"{FX}/claude.jsonl")

    def test_order_and_filtering(self):
        self.assertIsNone(self.cur)
        self.assertEqual([m["id"] for m in self.ms],
                         ["u2", "u4", "a6", "a7", "u11", "u12", "u13", "u16", "a17", "a17#1", "u18"])
        ts = [m["ts_ms"] for m in self.ms]
        self.assertEqual(ts, sorted(ts))

    def test_shapes(self):
        by = {m["id"]: m for m in self.ms}
        self.assertEqual(by["u2"]["text"], "/clear")
        self.assertEqual(by["a7"]["tool"], {"name": "Bash", "summary": "ls ~/Projects && echo done"})
        self.assertNotIn("tool", by["a6"])
        self.assertEqual(by["u11"]["text"], "이 그림 봐\n[이미지]")
        self.assertEqual(by["u12"]["tool"]["name"], "notification")
        self.assertEqual(by["u13"]["text"], "[t8p] [리드] 가짜 지시 문장입니다.")
        self.assertEqual(by["u18"]["text"], "! git status")
        self.assertEqual(len(by["a17#1"]["tool"]["summary"]), 80)
        for m in self.ms:
            self.assertEqual(set(m) - {"tool"}, {"id", "role", "text", "ts_ms"})
            self.assertIn(m["role"], ("user", "assistant", "tool"))

    def test_paging_matches_full_read(self):
        for limit in (1, 2, 3, 5):
            self.assertEqual(read_all("claude", f"{FX}/claude.jsonl", limit), self.ms, limit)

    def test_page_keeps_multi_block_line_together(self):
        ms, cur = T.read_messages("claude", f"{FX}/claude.jsonl", limit=2)
        self.assertEqual([m["id"] for m in ms], ["u18"])  # a17 has 2 messages, would overflow
        ms2, _ = T.read_messages("claude", f"{FX}/claude.jsonl", before=cur, limit=2)
        self.assertEqual([m["id"] for m in ms2], ["a17", "a17#1"])


class Queue(unittest.TestCase):
    """Messages typed while the agent works: queued until delivered as a user line or a queued_command attachment."""

    def setUp(self):
        T._queues.clear()
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "q.jsonl")
        shutil.copy(f"{FX}/claude_queue.jsonl", self.path)

    def test_pending_items_at_end_of_latest_page(self):
        ms, _ = T.read_messages("claude", self.path)
        self.assertEqual([(m["id"][:6], m["text"], m.get("queued")) for m in ms[-3:]],
                         [("a14", "계속 작업 중입니다.", None),
                          ("queue-", "가짜 대기 B", True),
                          ("queue-", "[t8p] 가짜 대기 C", True)])
        by = {m["text"]: m for m in ms}
        self.assertEqual(by["도중에 끼워 넣은 말"]["id"], "at9")  # absorbed mid-turn: shown from the attachment
        self.assertNotIn("queued", by["가짜 대기 A"])  # dequeued: shown once, as the real user line
        self.assertEqual([m["text"] for m in ms].count("가짜 대기 A"), 1)
        self.assertFalse(any("가짜 보고" in m["text"] for m in ms))  # queued agent-message stays hidden

    def test_older_pages_have_no_queue(self):
        ms, cur = T.read_messages("claude", self.path, limit=2)
        older, _ = T.read_messages("claude", self.path, before=cur, limit=50)
        self.assertFalse(any(m.get("queued") for m in older))

    def test_delivered_item_leaves_queue(self):
        T.read_messages("claude", self.path)  # warm the incremental cache
        with open(self.path, "a") as f:
            f.write(json.dumps({"type": "queue-operation", "operation": "dequeue",
                                "timestamp": "2026-11-14T11:00:20.000Z"}) + "\n")
            f.write(json.dumps({"type": "user", "uuid": "u21", "timestamp": "2026-11-14T11:00:21.000Z",
                                "message": {"role": "user", "content": "가짜 대기 B"}}) + "\n")
        ms, _ = T.read_messages("claude", self.path)
        self.assertEqual([(m["id"], m.get("queued")) for m in ms[-2:]], [("u21", None), (ms[-1]["id"], True)])
        self.assertEqual(ms[-1]["text"], "[t8p] 가짜 대기 C")

    def test_partial_last_line_is_ignored(self):
        with open(self.path, "a") as f:
            f.write('{"type":"queue-operation","operation":"dequeue"')  # writer mid-line
        ms, _ = T.read_messages("claude", self.path)
        self.assertEqual(sum(1 for m in ms if m.get("queued")), 2)


class Codex(unittest.TestCase):
    def setUp(self):
        self.ms, self.cur = T.read_messages("codex", f"{FX}/codex.jsonl")

    def test_order_and_filtering(self):
        self.assertIsNone(self.cur)
        self.assertEqual([m["id"] for m in self.ms],
                         ["msg_u5", "msg_a8", "ctc_9", "fc_11", "fc_14", "msg_u16", "msg_u17", "msg_u18", "msg_a20"])

    def test_shapes(self):
        by = {m["id"]: m for m in self.ms}
        self.assertEqual(by["ctc_9"]["tool"], {"name": "exec", "summary": "rg -n 'foo' src | head wc -l x"})
        self.assertEqual(by["fc_11"]["tool"]["summary"], "브라우저 열기")
        self.assertNotIn("gAAAA", by["fc_14"]["tool"]["summary"])
        self.assertEqual(by["msg_u16"]["text"], "[t8p] [리드] 가짜 요청")
        self.assertEqual(by["msg_u17"]["text"], "(중단됨)")
        self.assertEqual(by["msg_u18"]["text"], "그림 첨부\n[이미지]")

    def test_paging_matches_full_read(self):
        for limit in (1, 2, 4):
            self.assertEqual(read_all("codex", f"{FX}/codex.jsonl", limit), self.ms)


class Backward(unittest.TestCase):
    def test_offsets_match_forward_split(self):
        rnd = random.Random(1)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(T, "BLOCK", 3):
            for _ in range(200):
                data = b"\n".join(bytes(rnd.choices(b"ab ", k=rnd.randint(0, 9))) for _ in range(rnd.randint(0, 8)))
                data += b"\n" * rnd.randint(0, 1)
                p = os.path.join(d, "f")
                open(p, "wb").write(data)
                want, off = [], 0
                for line in data.split(b"\n"):
                    if line.strip():
                        want.append((off, line))
                    off += len(line) + 1
                self.assertEqual(list(T._lines_backward(p, len(data)))[::-1], want)


class Big(unittest.TestCase):
    """Thousands of lines, tens of MB, mostly bulky tool output: the tail read must stay fast."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls.dir.name, "big.jsonl")
        blob = "x" * 20000
        with open(cls.path, "w") as f:
            for i in range(3000):
                f.write(json.dumps({"type": "assistant", "uuid": f"a{i}", "timestamp": "2026-11-14T10:00:00Z",
                                    "message": {"content": [{"type": "text", "text": f"답 {i}"}]}}) + "\n")
                f.write(json.dumps({"type": "user", "uuid": f"r{i}", "toolUseResult": {"o": blob},
                                    "message": {"content": [{"type": "tool_result", "content": blob}]}}) + "\n")

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def test_tail_is_fast(self):
        self.assertGreater(os.path.getsize(self.path), 50_000_000)
        t = time.perf_counter()
        ms, cur = T.read_messages("claude", self.path, limit=50)
        dt = time.perf_counter() - t
        self.assertEqual(ms[-1]["text"], "답 2999")
        self.assertEqual(len(ms), 50)
        self.assertIsNotNone(cur)
        self.assertLess(dt, 0.2)  # target 50 ms; loose bound for a busy machine


if __name__ == "__main__":
    unittest.main()


class SqliteAgentsTest(unittest.TestCase):
    def test_pb_nested_strings(self):
        # field 19 { field 2: "hi" }, field 5 { field 1 { field 1: 300 } }
        b = bytes([0x9a, 0x01, 4, 0x12, 2]) + b"hi" + bytes([0x2a, 5, 0x0a, 3, 0x08, 0xac, 0x02])
        f = dict(T._pb(b))
        self.assertEqual((f["/19/2"], f["/5/1/1"]), ("hi", 300))

    def test_opencode_rows(self):
        import json as j
        user = (1, "m1", "user", 5, j.dumps({"text": "<hc from=\"t1\">하이</hc>"}))
        asst = (2, "m2", "assistant", 6, j.dumps({"content": [
            {"type": "reasoning", "text": "x"}, {"type": "tool", "name": "read", "state": {"input": {"path": "/a"}}},
            {"type": "text", "text": "답"}]}))
        self.assertEqual(T._opencode_row(user)[0]["text"], "[t1] 하이")
        roles = [(m["role"], m["text"] or m["tool"]["summary"]) for m in T._opencode_row(asst)]
        self.assertEqual(roles, [("tool", "/a"), ("assistant", "답")])
