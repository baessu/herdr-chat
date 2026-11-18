"""Map a herdr terminal (`hc get <t> --json` facts) to its agent transcript file. See README (How it works)."""
from __future__ import annotations  # python3 3.9 (macOS 기본) 에서도 돌게
import json
import os
import re
import sqlite3
import subprocess
import time
from pathlib import Path

HOME = Path.home()  # tests patch this
WINDOW_S = 120      # thread must be created within 2 min of process start
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
PROMPT_FLAGS = {"-m", "--model", "-c", "--config", "-s", "--sandbox", "-a", "--ask-for-approval", "-p", "--profile",
                "-C", "--cd", "-i", "--image", "--add-dir", "--local-provider", "--enable", "--disable"}


def resolve(facts: dict) -> dict | None:
    fg = facts.get("foreground") or {}
    pids = [p for p in (fg.get("pid"), facts.get("pid")) if p]
    argv = fg.get("argv") or facts.get("argv") or []
    # `sh -c "$@" sh claude`: the foreground pid is sh and the pid file belongs to its child.
    for pid in pids + [c for p in pids for c in _children(p)]:
        r = _claude(pid)
        if r:
            return r
    cwd = fg.get("cwd") or facts.get("cwd") or ""
    names = [os.path.basename(a) for a in argv]
    if "opencode" in names:
        return _opencode(pids, argv[names.index("opencode") + 1:], cwd)
    if "agy" in names:
        return _agy(pids, argv[names.index("agy") + 1:], cwd)
    i = _codex_index(argv)
    if i is None:
        return None
    return _codex(_codex_pids(pids), argv[i + 1:], cwd)


def _start(pids):
    return next((st for st in (_proc(p)[1] for p in pids) if st), None)


def _arg(args, *flags):
    for f in flags:
        if f in args[:-1]:
            return args[args.index(f) + 1]
    return None


def _same_dir(a, b):
    return bool(a and b) and os.path.realpath(a) == os.path.realpath(b)


# ---- opencode: ~/.local/share/opencode/opencode.db, a session row per conversation (created at start and on /new).
# ponytail: two opencode with the same folder and model started together can swap; then the newest wins for both.
def _opencode(pids, args, cwd):
    import sqlite3
    db = HOME / ".local/share/opencode/opencode.db"
    start = _start(pids)
    if not db.exists() or not start:
        return None
    model = _arg(args, "--model", "-m")
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as c:
            rows = c.execute("select id, directory, model, time_created from session_v2 where parent_id is null "
                             "and time_created>=? order by time_updated desc", (int((start - 10) * 1000),)).fetchall()
    except sqlite3.Error:
        return None

    def same_model(m):
        if not model:
            return True
        try:
            mm = json.loads(m or "{}")
        except ValueError:
            return False
        return model in (mm.get("id"), f"{mm.get('providerID')}/{mm.get('id')}")

    rows = [r for r in rows if same_model(r[2])]
    # 실측(MacBook): 대화는 프로세스가 뜬 그 초에 생긴다. 같은 폴더·모델이 여럿이어도 시작 시각으로 갈린다.
    near = [r for r in rows if abs(r[3] / 1000 - start) <= 10]
    if near:
        sid = min(near, key=lambda r: abs(r[3] / 1000 - start))[0]
        return {"kind": "opencode", "path": f"{db}#{sid}", "session_id": sid}
    # 그 밖에는 실행 폴더(또는 하위 폴더)에서 가장 최근에 쓴 대화. ponytail: 같은 폴더·모델 둘이면 섞일 수 있다.
    base = os.path.realpath(cwd) if cwd else ""
    for sid, directory, _, _ in rows:
        d = os.path.realpath(directory or "")
        if base and (d == base or d.startswith(base + os.sep)):
            return {"kind": "opencode", "path": f"{db}#{sid}", "session_id": sid}
    return None


# ---- Antigravity CLI (agy): ~/.gemini/antigravity-cli/conversations/<id>.db, created at the first prompt.
_agy_heads = {}  # db path -> (mtime, (first prompt epoch, workspace folder, first prompt text))


def _agy_head(path):
    import sqlite3
    import transcripts
    m = os.path.getmtime(path)
    hit = _agy_heads.get(path)
    if hit and hit[0] == m:
        return hit[1]
    head = (None, None, "")
    try:
        with transcripts._sqlite(path) as c:
            r = c.execute("select step_payload from steps where step_type=? order by idx limit 1",
                          (transcripts.AGY_USER,)).fetchone()
        f = {}
        for k, v in transcripts._pb(r[0] if r else b""):
            f.setdefault(k, v)
        ws = f.get("/19/12/12")
        text = f.get("/19/2") if isinstance(f.get("/19/2"), str) else ""
        head = (f.get("/5/1/1"), ws[7:] if isinstance(ws, str) and ws.startswith("file://") else None, text)
    except sqlite3.Error:
        pass
    _agy_heads[path] = (m, head)
    return head


def _agy(pids, args, cwd):
    start = _start(pids)
    if not start:
        return None
    prompt = _norm(_arg(args, "--prompt-interactive", "-i", "--prompt", "-p") or "")[:60]
    best = None
    for p in (HOME / ".gemini/antigravity-cli/conversations").glob("*.db"):
        ts, ws, text = _agy_head(str(p))
        if not isinstance(ts, int) or ts < start - 5:
            continue
        first = _norm(re.sub(r"^/[a-z]+\s+", "", text))  # mode commands (`/plan …`) come first
        if not (first.startswith(prompt) if prompt else _same_dir(ws, cwd)):
            continue
        # 실행 직후에 생긴 대화를 고른다(가장 늦은 것이 아니라). 실측(MacBook t10h·t10n): 같은 폴더에서 나중에
        # 띄운 agy 의 대화가 앞 터미널에 붙었다. ponytail: agy 안의 /new 는 따라가지 못한다.
        if best is None or ts < best[0]:
            best = (ts, str(p))
    return {"kind": "agy", "path": best[1], "conversation_id": Path(best[1]).stem} if best else None


# ---- Claude: ~/.claude/sessions/<pid>.json is rewritten on /clear, so read it every time.
def _claude(pid):
    try:
        d = json.loads((HOME / ".claude/sessions" / f"{pid}.json").read_text())
        sid = d["sessionId"]
    except (OSError, ValueError, KeyError):
        return None
    # 실측(MacBook t8d): parkedJobId 가 있으면 대화가 그 id 로 시작하는 새 파일로 이어지고, sessions 파일은
    # 갱신되지 않는다. 두 후보 중 최근에 바뀐 쪽을 쓴다.
    found = [(p, 0) for p in (HOME / ".claude/projects").glob(f"*/{sid}.jsonl")]
    job = d.get("parkedJobId")
    if isinstance(job, str) and re.fullmatch(r"[0-9a-f-]{4,36}", job):
        found += [(p, 1) for p in (HOME / ".claude/projects").glob(f"*/{job}*.jsonl")]
    if not found:
        return None
    # 파일 수정 시각은 못 믿는다(실측: OneDrive 가 여러 기록 파일을 같은 순간에 건드림). 안의 마지막 시각을 본다.
    p = max(found, key=lambda x: (_last_ts(x[0]), x[1]))[0]  # 같으면 job 쪽
    return {"kind": "claude", "path": str(p), "session_id": p.stem}


TS_RE = re.compile(rb'"timestamp"\s*:\s*"([0-9T:.\-]+Z?)"')


def _last_ts(path):
    """기록 파일 끝 64KB 에 나온 마지막 "timestamp" 값(ISO 문자열, 비교용). 못 찾으면 ""."""
    try:
        with open(path, "rb") as f:
            f.seek(max(0, f.seek(0, 2) - 65536))
            hits = TS_RE.findall(f.read())
    except OSError:
        return ""
    return hits[-1].decode() if hits else ""


# ---- Codex
def _codex_index(argv):
    for i, a in enumerate(argv):
        if os.path.basename(a) == "codex":
            return i
    return None


def _codex_pids(pids):
    """The terminal's pid may be a `sh -c` wrapper; the codex process is then its child."""
    for pid in pids:
        if _proc(pid)[0] == "codex":
            return [pid]
    for pid in pids:
        kids = [c for c in _children(pid) if _proc(c)[0] == "codex"]
        if kids:
            return kids
    return []


def _codex(pids, args, cwd):
    # -C overrides the shell cwd.
    for flag in ("-C", "--cd"):
        if flag in args[:-1]:
            cwd = args[args.index(flag) + 1]
    with _db("state_5") as st:
        # logs first: it follows /new inside the same process; a stale `resume` id would not.
        for pid in pids:
            tid = _from_logs(st, pid, _proc(pid)[1])
            if tid:
                return _hit(st, tid, "logs")
        if "resume" in args[:-1]:
            # flags may sit between: `codex resume --no-daemon -C ~ <id>`
            tid = next((a for a in args[args.index("resume") + 1:] if UUID.match(a)), None)
            if tid:
                return _hit(st, tid, "resume")
        for pid in pids:
            start = _proc(pid)[1]
            if start:
                tid = _from_cwd_time(st, cwd, start, args)
                if tid:
                    return _hit(st, tid, "cwd_time")
    return None


def _hit(st, tid, how):
    r = st.execute("select rollout_path from threads where id=?", (tid,)).fetchone()
    if r and os.path.exists(r[0]):
        return {"kind": "codex", "path": r[0], "thread_id": tid, "how": how}
    return None


MAIN = "source not like '{\"subagent%' and id not in (select child_thread_id from thread_spawn_edges)"


def _from_logs(st, pid, start):
    for tid, ts in _log_threads().get(pid, []):  # newest first
        if ts < (start or 0):
            break  # an earlier process that had the same pid
        # logs also hold ephemeral helper threads and subagents; skip those
        if st.execute(f"select 1 from threads where id=? and {MAIN}", (tid,)).fetchone():
            return tid
    return None


def _from_cwd_time(st, cwd, start, args):
    exec_mode = "exec" in args
    rows = st.execute(
        f"select id, first_user_message, source from threads where cwd=? and created_at between ? and ? and {MAIN} "
        "order by created_at", (cwd, int(start) - 5, int(start) + WINDOW_S)).fetchall()
    rows = [r for r in rows if (r[2] == "exec") == exec_mode]
    prompt = _prompt(args)
    if prompt is not None:
        rows = [r for r in rows if _norm(r[1]) == prompt]
    return rows[0][0] if len(rows) == 1 else None


def _prompt(args):
    """Positional launch prompt of `codex [opts] <prompt>`, if any."""
    skip = False
    pos = []
    for a in args:
        if skip:
            skip = False
        elif a in PROMPT_FLAGS:
            skip = True
        elif not a.startswith("-"):
            pos.append(a)
    if pos and pos[0] in ("resume", "exec", "e"):
        pos = pos[1:]
    return _norm(pos[-1]) if pos else None


def _norm(s):
    return " ".join((s or "").split())


# ---- process helpers (patched in tests)
TTL_S = 5  # one ps / one logs scan serves every terminal resolved within this window
_cache = {}


def _cached(key, fn):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < TTL_S:
        return hit[1]
    v = fn()
    _cache[key] = (time.monotonic(), v)
    return v


def _log_threads():
    """{pid: [(thread_id, last ts), ...] newest first}, from a single scan of logs_2."""
    def scan():
        out = {}
        try:
            with _db("logs_2") as lg:
                rows = lg.execute("select process_uuid, thread_id, max(ts) from logs where thread_id is not null "
                                  "group by 1, 2 order by 3 desc").fetchall()
        except sqlite3.Error:
            return out
        for pu, tid, ts in rows:
            m = re.match(r"pid:(\d+):", pu or "")
            if m:
                out.setdefault(int(m.group(1)), []).append((tid, ts))
        return out
    return _cached("logs", scan)


def _procs():
    """{pid: (ppid, command basename, start epoch)} from a single `ps` (per-pid calls cost ~50ms each under load)."""
    def scan():
        out = {}
        ps = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,lstart=,comm="], capture_output=True, text=True).stdout
        for line in ps.splitlines():
            p = line.split(None, 7)
            if len(p) < 8:
                continue
            try:
                start = time.mktime(time.strptime(" ".join(p[2:7]), "%a %b %d %H:%M:%S %Y"))
                out[int(p[0])] = (int(p[1]), os.path.basename(p[7].strip()), start)
            except ValueError:
                continue
        return out
    return _cached("ps", scan)


def _proc(pid):
    """(command basename, start epoch)"""
    p = _procs().get(pid)
    return (p[1], p[2]) if p else (None, None)


def _children(pid):
    return [c for c, (pp, _, _) in _procs().items() if pp == pid]


class _db:
    def __init__(self, name):
        self.c = sqlite3.connect(f"file:{HOME}/.codex/{name}.sqlite?mode=ro", uri=True)

    def __enter__(self):
        return self.c

    def __exit__(self, *a):
        self.c.close()
