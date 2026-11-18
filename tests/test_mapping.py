import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import mapping  # noqa: E402

START = 1_794_600_000  # fake process start (epoch s)
SUB = '{"subagent":{"other":"guardian"}}'


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / ".codex").mkdir()
        (self.home / ".claude/sessions").mkdir(parents=True)
        (self.home / ".claude/projects/-tmp-x").mkdir(parents=True)
        st = sqlite3.connect(self.home / ".codex/state_5.sqlite")
        st.executescript("""
            create table threads (id text primary key, rollout_path text, created_at integer, source text,
                                  cwd text, first_user_message text default '');
            create table thread_spawn_edges (parent_thread_id text, child_thread_id text primary key, status text);""")
        st.commit(); st.close()
        lg = sqlite3.connect(self.home / ".codex/logs_2.sqlite")
        lg.execute("create table logs (id integer primary key, ts integer, thread_id text, process_uuid text)")
        lg.commit(); lg.close()
        self.procs = {}  # pid -> (comm, start)
        self.kids = {}
        mapping._cache.clear()
        p = mock.patch.object(mapping, "HOME", self.home); p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(mapping, "_proc", lambda pid: self.procs.get(pid, (None, None))); p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(mapping, "_children", lambda pid: self.kids.get(pid, [])); p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def thread(self, tid, created, cwd="/w", source="cli", first="", child_of=None):
        path = self.home / f"rollout-{tid}.jsonl"
        path.write_text("{}\n")
        st = sqlite3.connect(self.home / ".codex/state_5.sqlite")
        st.execute("insert into threads values (?,?,?,?,?,?)", (tid, str(path), created, source, cwd, first))
        if child_of:
            st.execute("insert into thread_spawn_edges values (?,?,'open')", (child_of, tid))
        st.commit(); st.close()
        return str(path)

    def log(self, pid, tid, ts):
        lg = sqlite3.connect(self.home / ".codex/logs_2.sqlite")
        lg.execute("insert into logs (ts, thread_id, process_uuid) values (?,?,?)", (ts, tid, f"pid:{pid}:abc"))
        lg.commit(); lg.close()

    def facts(self, pid, argv, cwd="/w", shell_pid=1):
        return {"pid": shell_pid, "cwd": cwd, "argv": ["/bin/zsh"],
                "foreground": {"pid": pid, "argv": argv, "cwd": cwd, "name": os.path.basename(argv[0])}}


class Claude(Base):
    def session(self, pid, sid):
        (self.home / f".claude/sessions/{pid}.json").write_text(json.dumps({"pid": pid, "sessionId": sid}))

    def test_pid_file_to_jsonl(self):
        self.session(10, "s-1")
        p = self.home / ".claude/projects/-tmp-x/s-1.jsonl"
        p.write_text("")
        r = mapping.resolve(self.facts(10, ["claude"]))
        self.assertEqual(r, {"kind": "claude", "path": str(p), "session_id": "s-1"})

    def test_clear_switches_session(self):
        """/clear rewrites sessionId in the pid file; resolve follows it on the next call."""
        for s in ("s-1", "s-2"):
            (self.home / f".claude/projects/-tmp-x/{s}.jsonl").write_text("")
        self.session(10, "s-1")
        self.assertEqual(mapping.resolve(self.facts(10, ["claude"]))["session_id"], "s-1")
        self.session(10, "s-2")
        self.assertEqual(mapping.resolve(self.facts(10, ["claude"]))["session_id"], "s-2")

    def test_missing_transcript(self):
        self.session(10, "nope")
        self.assertIsNone(mapping.resolve(self.facts(10, ["claude"])))

    def test_sh_wrapper_child(self):
        """`sh -c "$@" sh claude` (MacBook t11t): the pid file is under the child claude pid."""
        self.kids[30] = [31]
        self.session(31, "s-1")
        p = self.home / ".claude/projects/-tmp-x/s-1.jsonl"
        p.write_text("")
        argv = ["sh", "-c", '"$@"; exec "${SHELL:-sh}" -i', "sh", "claude"]
        self.assertEqual(mapping.resolve(self.facts(30, argv))["path"], str(p))

    def test_shell_is_none(self):
        self.assertIsNone(mapping.resolve(self.facts(10, ["/bin/zsh"])))


class Codex(Base):
    def setUp(self):
        super().setUp()
        self.procs[20] = ("codex", START)

    def test_logs_latest_main_thread(self):
        self.thread("old", START + 3)
        p = self.thread("new", START + 600)  # after /new in the same process
        self.thread("guard", START + 700, source=SUB)
        self.thread("kid", START + 800, child_of="new")
        self.log(20, "old", START + 10)
        self.log(20, "new", START + 650)
        self.log(20, "guard", START + 900)
        self.log(20, "kid", START + 900)
        self.log(20, "ephemeral", START + 950)  # not in threads
        self.log(20, "reused-pid", START - 50)  # earlier process with the same pid
        r = mapping.resolve(self.facts(20, ["codex", "--no-daemon"]))
        self.assertEqual(r, {"kind": "codex", "path": p, "thread_id": "new", "how": "logs"})

    def test_resume_arg(self):
        tid = "01a19ce7-9f1a-7090-a7df-af8f6bd8475b"
        p = self.thread(tid, START - 99999)
        r = mapping.resolve(self.facts(20, ["/usr/local/bin/codex", "-p", "x", "resume", tid]))
        self.assertEqual((r["thread_id"], r["how"], r["path"]), (tid, "resume", p))
        # 실측(MacBook t2p): resume 과 id 사이에 옵션이 낀다
        r = mapping.resolve(self.facts(20, ["codex", "resume", "--no-daemon", "-C", "/x", tid]))
        self.assertEqual((r["thread_id"], r["how"]), (tid, "resume"))

    def test_logs_beat_stale_resume(self):
        tid = "01a19ce7-9f1a-7090-a7df-af8f6bd8475b"
        self.thread(tid, START - 99999)
        self.thread("fresh", START + 500)
        self.log(20, "fresh", START + 501)
        r = mapping.resolve(self.facts(20, ["codex", "resume", tid]))
        self.assertEqual((r["thread_id"], r["how"]), ("fresh", "logs"))

    def test_cwd_time_with_prompt_and_C_flag(self):
        self.thread("other", START + 4, cwd="/proj", first="다른 작업")
        p = self.thread("mine", START + 5, cwd="/proj", first="브리프를 읽고 수행해")
        self.thread("late", START + 500, cwd="/proj", first="브리프를 읽고 수행해")  # outside 2 min window
        argv = ["codex", "--no-daemon", "-C", "/proj", "-m", "gpt", "브리프를  읽고\n수행해"]
        r = mapping.resolve(self.facts(20, argv, cwd="/elsewhere"))
        self.assertEqual((r["thread_id"], r["how"], r["path"]), ("mine", "cwd_time", p))

    def test_cwd_time_skips_exec_and_subagents(self):
        self.thread("exec1", START + 2, source="exec")
        self.thread("guard", START + 3, source=SUB)
        self.thread("kid", START + 3, child_of="x")
        self.thread("tui", START + 4, source="vscode")
        r = mapping.resolve(self.facts(20, ["codex", "--yolo"]))
        self.assertEqual(r["thread_id"], "tui")

    def test_two_promptless_same_cwd_is_ambiguous(self):
        self.thread("a", START + 4)
        self.thread("b", START + 6)
        self.assertIsNone(mapping.resolve(self.facts(20, ["codex", "--no-daemon"])))

    def test_sh_wrapper_child(self):
        """`sh -c "$@" sh codex …`: the foreground pid is sh; codex is its child."""
        self.procs[30] = ("sh", START)
        self.kids[30] = [31]
        self.procs[31] = ("codex", START)
        self.thread("t", START + 1, first="조사하라")
        self.log(31, "t", START + 2)
        argv = ["sh", "-c", '"$@"; exec "${SHELL:-sh}" -i', "sh", "codex", "--search", "조사하라"]
        r = mapping.resolve(self.facts(30, argv))
        self.assertEqual((r["thread_id"], r["how"]), ("t", "logs"))

    def test_rollout_file_missing(self):
        self.thread("gone", START + 1)
        os.remove(self.home / "rollout-gone.jsonl")
        self.assertIsNone(mapping.resolve(self.facts(20, ["codex"])))


class Ps(unittest.TestCase):
    def test_ps_parsing(self):
        mapping._cache.clear()
        out = (" 83179 33026 Mon Nov  2 17:25:50 2026     sh\n"
               " 83180 83179 Mon Nov  2 17:25:51 2026     /usr/local/bin/codex\n"
               "   1 0 Sat Oct 31 13:48:04 2026     /Applications/A B.app/x\n")
        with mock.patch.object(mapping.subprocess, "run", return_value=mock.Mock(stdout=out)):
            self.assertEqual(mapping._proc(83180)[0], "codex")
            self.assertEqual(mapping._proc(83180)[1] - mapping._proc(83179)[1], 1)
            self.assertEqual(mapping._children(83179), [83180])
            self.assertEqual(mapping._proc(1)[0], "x")
        mapping._cache.clear()


class Prompt(unittest.TestCase):
    def test_prompt_parsing(self):
        self.assertEqual(mapping._prompt(["--no-daemon", "-s", "read-only", "-c", "a=b", "hi  there"]), "hi there")
        self.assertIsNone(mapping._prompt(["--yolo"]))
        self.assertIsNone(mapping._prompt(["-p", "prof"]))


if __name__ == "__main__":
    unittest.main()
