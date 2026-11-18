"""Read Claude Code / Codex transcript jsonl files into API.md messages, newest first from the end of the file."""
from __future__ import annotations  # python3 3.9 (macOS 기본) 에서도 돌게
import json
import os
import re
from datetime import datetime

BLOCK = 1 << 18
SUMMARY_MAX = 80


def last_change(path: str) -> float:
    p = _db_path(path)
    # sqlite writes land in the -wal file first
    return max(os.path.getmtime(x) for x in (p, p + "-wal") if os.path.exists(x)) if os.path.exists(p) else os.path.getmtime(p)


def read_messages(kind: str, path: str, before: str | None = None, limit: int = 50) -> tuple[list[dict], str | None]:
    """Latest `limit` messages before byte offset `before`, oldest first. cursor = offset for the next older page."""
    if kind == "opencode":
        return _opencode_read(path, before, limit)
    if kind == "agy":
        return _agy_read(path, before, limit)
    parse = {"claude": _claude, "codex": _codex}[kind]
    end = int(before) if before is not None else os.path.getsize(path)
    out = []  # newest first, one list per line
    n = 0
    cursor = None
    for start, raw in _lines_backward(path, end):
        msgs = parse(raw, start)
        if not msgs:
            continue
        if n and n + len(msgs) > limit:  # keep a line's messages together; the cursor is a line boundary
            cursor = str(start + len(raw) + 1)
            break
        out.append(msgs)
        n += len(msgs)
        if n >= limit:
            cursor = str(start) if start > 0 else None
            break
    msgs = [m for msgs in reversed(out) for m in msgs]
    if kind == "claude" and before is None:
        msgs += _queued(path, end)
    return msgs, cursor


# ---- Claude input queue: messages typed while the agent works sit in a queue until they are delivered as a
# `user` line (dequeue) or as a `queued_command` attachment (remove, reason absorbed_mid_turn).
_queues = {}  # path -> (inode, scanned offset, [(enqueue offset, record)])


def _queue_state(path, end):
    """Replay queue-operation lines forward; incremental per file so a refresh only reads new bytes."""
    ino = os.stat(path).st_ino
    c = _queues.get(path)
    if not c or c[0] != ino or c[1] > end:
        c = (ino, 0, [])
    _, pos, q = c
    q = list(q)
    with open(path, "rb") as f:
        f.seek(pos)
        for line in f:
            if pos + len(line) > end or not line.endswith(b"\n"):
                break  # line still being written
            if b'"queue-operation"' in line:
                try:
                    o = json.loads(line)
                except ValueError:
                    o = {}
                op, content = o.get("operation"), o.get("content")
                if op == "enqueue":
                    q.append((pos, o))
                elif op == "remove" and content is not None:
                    i = next((i for i, (_, e) in enumerate(q) if e.get("content") == content), None)
                    if i is not None:
                        q.pop(i)
                elif op in ("dequeue", "remove") and q:
                    q.pop(0)
            pos += len(line)
    _queues[path] = (ino, pos, q)
    return q


def _queued(path, end):
    out = []
    for off, e in _queue_state(path, end):
        content = e.get("content")
        if not isinstance(content, str) or content.lstrip().startswith("<agent-message"):
            continue  # peer/subagent hand-backs are meta, never shown
        for m in _claude_user(f"queue-{off}", content, _ts(e.get("timestamp"))):
            if m["role"] == "user":
                m["queued"] = True
                out.append(m)
    return out


def _lines_backward(path, end):
    """Yield (offset, bytes) of each line ending before `end`, last line first."""
    with open(path, "rb") as f:
        pos = end
        tail = []  # chunks of the line being assembled; kept as a list so multi-MB lines stay O(n)
        while pos > 0:
            step = min(BLOCK, pos)
            pos -= step
            f.seek(pos)
            buf = f.read(step)
            i = buf.rfind(b"\n")
            if i < 0:
                tail.append(buf)
                continue
            line = buf[i + 1:] + b"".join(reversed(tail))
            if line.strip():
                yield pos + i + 1, line
            parts = buf[:i].split(b"\n")
            off = pos + i + 1
            for p in reversed(parts[1:]):
                off -= len(p) + 1
                if p.strip():
                    yield off, p
            tail = [parts[0]]
        line = b"".join(reversed(tail))
        if line.strip():
            yield 0, line


def _ts(s):
    try:
        # 3.9·3.10 의 fromisoformat 은 끝의 'Z' 를 못 읽는다
        return int(datetime.fromisoformat(s[:-1] + '+00:00' if s.endswith('Z') else s).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def _one_line(s, n=SUMMARY_MAX):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _msg(id_, role, text, ts, tool=None):
    m = {"id": id_, "role": role, "text": text, "ts_ms": ts}
    if tool:
        m["tool"] = tool
    return m


# ---- shared text cleanup
HC = re.compile(r'^<hc from="([^"]*)">(.*)</hc>\s*$', re.S)
SYSREM = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)


MOUSE = re.compile(r"\x1b?\[<\d+;\d+;\d+[mM]")  # stray terminal mouse reports typed into the prompt


PASTED = re.compile(r'^\s*<pasted_content id="[^"]*">\n?(.*?)\n?</pasted_content id="[^"]*">\s*$', re.S)


def _unwrap_hc(t):
    """`hc send` wraps text as <hc from="t1n">…</hc>; show it as `[t1n] …`.
    Claude Code records a long multi-line send as <pasted_content id=…>…</pasted_content id=…>; drop the tags."""
    t = MOUSE.sub("", t)
    p = PASTED.match(t)
    if p:
        t = p.group(1)
    m = HC.match(t.strip())
    return f"[{m.group(1)}] {m.group(2).strip()}" if m else t


# ---- Claude Code
TOOL_RESULT = re.compile(rb'"toolUseResult"\s*:')  # tool output lines; inside strings the quotes would be escaped
USER_OR_ASSISTANT = re.compile(rb'"type"\s*:\s*"(?:user|assistant)"')
QUEUED_CMD = re.compile(rb'"type"\s*:\s*"queued_command"')


def _claude_absorbed(raw, off):
    """A queued message delivered mid-turn is recorded only as an attachment, never as a `user` line."""
    try:
        o = json.loads(raw)
    except ValueError:
        return []
    a = o.get("attachment") or {}
    if o.get("type") != "attachment" or a.get("type") != "queued_command" or a.get("isMeta") or o.get("isSidechain"):
        return []
    return _claude_user(o.get("uuid") or str(off), a.get("prompt"), _ts(o.get("timestamp") or a.get("timestamp")))


def _claude(raw, off):
    if QUEUED_CMD.search(raw):
        return _claude_absorbed(raw, off)
    if TOOL_RESULT.search(raw) or not USER_OR_ASSISTANT.search(raw):  # cheap skip before json parsing
        return []
    try:
        o = json.loads(raw)
    except ValueError:
        return []
    typ = o.get("type")
    if typ not in ("user", "assistant") or o.get("isSidechain") or o.get("isMeta"):
        return []
    uid = o.get("uuid") or str(off)
    ts = _ts(o.get("timestamp"))
    content = (o.get("message") or {}).get("content")
    if typ == "user":
        if o.get("isCompactSummary"):
            return [_msg(uid, "tool", "", ts, {"name": "compact", "summary": "이전 대화가 요약되었습니다"})]
        return _claude_user(uid, content, ts)
    out = []
    for i, b in enumerate(content if isinstance(content, list) else [{"type": "text", "text": content or ""}]):
        bid = uid if i == 0 else f"{uid}#{i}"
        if b.get("type") == "text" and b.get("text", "").strip():
            out.append(_msg(bid, "assistant", b["text"], ts))
        elif b.get("type") == "tool_use":
            name = b.get("name", "")
            qs = (b.get("input") or {}).get("questions") if name == "AskUserQuestion" else None
            ask = _ask_text(qs)
            if ask:
                out.append(dict(_msg(bid, "assistant", ask, ts), ask=_ask_data(qs, "claude")))
            else:
                out.append(_msg(bid, "tool", "", ts, {"name": name, "summary": _claude_tool(name, b.get("input") or {})}))
    return out


def _ask_data(questions, tool):
    """화면이 고를 수 있는 카드로 그리는 구조. 답은 /answer 로 보낸다."""
    out = []
    for q in questions or []:
        if not isinstance(q, dict):
            continue
        opts = [{"label": str(o.get("label", "")), "description": str(o.get("description") or "")} if isinstance(o, dict)
                else {"label": str(o), "description": ""} for o in q.get("options") or []]
        out.append({"header": str(q.get("header") or q.get("title") or ""),
                    "question": str(q.get("question") or q.get("prompt") or ""),
                    "multi": bool(q.get("multiSelect") or q.get("multi_select")), "options": opts})
    return {"tool": tool, "questions": out}


def _ask_text(questions):
    """Claude AskUserQuestion · Codex request_user_input 의 질문을 읽을 수 있는 글로. 답은 화면에서 고른다."""
    if not isinstance(questions, list) or not questions:
        return None
    parts = ["**질문이 왔습니다.** 아래 화면에서 번호를 골라 답해 주세요."]
    for q in questions:
        if not isinstance(q, dict):
            continue
        head = q.get("header") or q.get("title") or ""
        text = q.get("question") or q.get("prompt") or ""
        multi = " (여러 개 선택 가능)" if (q.get("multiSelect") or q.get("multi_select")) and "여러" not in text else ""
        parts.append(f"**{head}** {text}{multi}".strip() if head else f"{text}{multi}")
        for i, o in enumerate(q.get("options") or [], 1):
            if isinstance(o, dict):
                desc = o.get("description") or ""
                parts.append(f"{i}. **{o.get('label', '')}**" + (f" — {desc}" if desc else ""))
            else:
                parts.append(f"{i}. {o}")
    return "\n\n".join(parts[:1]) + "\n\n" + "\n".join(parts[1:])


TAG = re.compile(r"<([a-z-]+)>(.*?)</\1>", re.S)


def _claude_user(uid, content, ts):
    if isinstance(content, str):
        blocks = [{"type": "text", "text": content}]
    else:
        blocks = content or []
    texts, images = [], 0
    for b in blocks:
        if b.get("type") == "text":
            texts.append(b["text"])
        elif b.get("type") == "image":
            images += 1
    if not texts and not images:
        return []  # tool_result only
    t = SYSREM.sub("", "\n".join(texts)).strip()
    tags = dict((k, v.strip()) for k, v in TAG.findall(t))
    if "command-name" in tags:
        t = f"{tags['command-name']} {tags.get('command-args', '')}".strip()
    elif "task-notification" in tags:
        inner = dict((k, v.strip()) for k, v in TAG.findall(tags["task-notification"]))
        return [_msg(uid, "tool", "", ts, {"name": "notification",
                                           "summary": _one_line(inner.get("summary") or inner.get("status", ""))})]
    elif "local-command-stdout" in tags:
        if not tags["local-command-stdout"]:
            return []
        return [_msg(uid, "tool", "", ts, {"name": "output", "summary": _one_line(tags["local-command-stdout"])})]
    elif "bash-input" in tags:
        t = "! " + tags["bash-input"]
    elif t.startswith(("<bash-stdout>", "<bash-stderr>", "<local-command-caveat>")):
        return []
    t = _unwrap_hc(t)
    if images:
        t = (t + "\n" if t else "") + " ".join(["[이미지]"] * images)
    return [_msg(uid, "user", t, ts)] if t else []


CLAUDE_KEYS = {"Bash": "command", "Read": "file_path", "Write": "file_path", "Edit": "file_path",
               "NotebookEdit": "notebook_path", "Grep": "pattern", "Glob": "pattern", "WebFetch": "url",
               "WebSearch": "query", "Agent": "description", "Task": "description", "Skill": "skill"}


def _claude_tool(name, inp):
    k = CLAUDE_KEYS.get(name)
    if k and inp.get(k):
        return _one_line(inp[k])
    if name == "SendMessage":
        return _one_line(f"→ {inp.get('to', '')} {inp.get('summary') or inp.get('message', '')}")
    return _first_str(inp)


def _first_str(d):
    for v in d.values():
        if isinstance(v, str) and v.strip() and not v.startswith("gAAAA"):  # skip encrypted payloads
            return _one_line(v)
    return ""


# ---- Codex
PTYPE = re.compile(rb'"type":\s*"response_item",\s*"payload":\s*\{"type":\s*"([a-z_]+)"')
CODEX_KEEP = {"message", "function_call", "custom_tool_call", "local_shell_call", "web_search_call"}
CODEX_AUTO = ("# AGENTS.md instructions", "<environment_context>", "<INSTRUCTIONS>", "<user_instructions>",
              "<permissions instructions>", "Host notice:", "<skill>")


def _codex(raw, off):
    m = PTYPE.search(raw, 0, 300)
    if not m or m.group(1).decode() not in CODEX_KEEP:  # skip outputs/reasoning/events without json parsing
        return []
    try:
        o = json.loads(raw)
    except ValueError:
        return []
    p = o["payload"]
    pid = p.get("id") or str(off)
    ts = _ts(o.get("timestamp"))
    typ = p["type"]
    if typ == "message":
        role = p.get("role")
        if role == "assistant":
            t = "".join(b.get("text", "") for b in p.get("content", []) if b.get("type") == "output_text")
            return [_msg(pid, "assistant", t, ts)] if t.strip() else []
        if role == "user":
            texts, images = [], 0
            for b in p.get("content", []):
                if b.get("type") == "input_text":
                    s = b.get("text", "")
                    if not s.lstrip().startswith(CODEX_AUTO) and s.strip() not in ("<image>", "</image>"):
                        texts.append(s)
                elif b.get("type") == "input_image":
                    images += 1
            t = _unwrap_hc("\n".join(texts).strip())
            if t.startswith("<turn_aborted>"):
                t = "(중단됨)"
            if images:
                t = (t + "\n" if t else "") + " ".join(["[이미지]"] * images)
            return [_msg(pid, "user", t, ts)] if t else []
        return []  # developer/system prompts
    if typ == "function_call" and p.get("name") == "request_user_input":
        try:
            qs = json.loads(p.get("arguments") or "{}").get("questions")
        except (ValueError, AttributeError):
            qs = None
        ask = _ask_text(qs)
        if ask:
            return [dict(_msg(pid, "assistant", ask, ts), ask=_ask_data(qs, "codex"))]
    return [_msg(pid, "tool", "", ts, _codex_tool(p))]


EXEC_CMD = re.compile(r"""\bcmd\s*:\s*(["'`])((?:\\.|(?!\1).)*)\1""", re.S)


def _js_str(q, body):
    """Decode a JS string literal body (JS escapes are close enough to JSON's)."""
    body = body.replace("\\'", "'").replace("\\`", "`")  # escapes JSON lacks
    if q != '"':
        body = re.sub(r'(?<!\\)"', '\\"', body)
    try:
        return json.loads('"' + body + '"')
    except ValueError:
        return body


def _codex_tool(p):
    typ = p["type"]
    if typ == "custom_tool_call":
        inp = p.get("input") or ""
        m = EXEC_CMD.search(inp)
        if m:
            s = _js_str(m.group(1), m.group(2))
        else:
            s = next((ln for ln in inp.splitlines() if ln.strip()), "")
        return {"name": p.get("name", "custom"), "summary": _one_line(s)}
    if typ == "function_call":
        try:
            args = json.loads(p.get("arguments") or "{}")
        except ValueError:
            args = {}
        if not isinstance(args, dict):
            args = {}
        s = ""
        for k in ("cmd", "command", "title", "query", "url", "task_name", "target", "code"):
            v = args.get(k)
            if isinstance(v, list):
                v = " ".join(map(str, v))
            if isinstance(v, str) and v.strip() and not v.startswith("gAAAA"):
                s = v
                break
        if not s and isinstance(args.get("questions"), list) and args["questions"]:
            s = (args["questions"][0] or {}).get("title", "")
        return {"name": p.get("name", "function"), "summary": _one_line(s) if s else _first_str(args)}
    if typ == "local_shell_call":
        cmd = ((p.get("action") or {}).get("command")) or []
        return {"name": "shell", "summary": _one_line(" ".join(cmd) if isinstance(cmd, list) else cmd)}
    q = (p.get("action") or {}).get("query", "")
    return {"name": "web_search", "summary": _one_line(q)}


# ---- sqlite-backed agents: path is "<db>" (Antigravity, one db per conversation) or "<db>#<session>" (opencode).
# cursor = row number (seq/idx) of the oldest message on the page.
def _sqlite(path):
    import sqlite3
    from urllib.parse import quote
    try:
        c = sqlite3.connect(f"file:{quote(path)}?mode=ro", uri=True)
        c.execute("select 1 from sqlite_master limit 1")
        return c
    except sqlite3.Error:  # WAL db in a folder we may not write -shm into
        return sqlite3.connect(f"file:{quote(path)}?mode=ro&immutable=1", uri=True)


def _db_path(path):
    return path.split("#", 1)[0]


def _sql_messages(rows, parse, limit):
    """rows newest first: (n, ...). Keep a row's messages together, like the jsonl readers."""
    out, n, cursor = [], 0, None
    for row in rows:
        msgs = parse(row)
        if not msgs:
            continue
        if n and n + len(msgs) > limit:
            cursor = str(row[0] + 1)
            break
        out.append(msgs)
        n += len(msgs)
        if n >= limit:
            cursor = str(row[0])
            break
    return [m for msgs in reversed(out) for m in msgs], cursor


def _opencode_read(path, before, limit):
    db, sid = path.split("#", 1)
    with _sqlite(db) as c:
        cur = c.execute("select seq, id, type, time_created, data from session_message where session_id=? and seq<? "
                        "and type in ('user','assistant') order by seq desc", (sid, int(before) if before else 1 << 62))
        msgs, cursor = _sql_messages(cur, _opencode_row, limit)
    if cursor and int(cursor) <= 0:
        cursor = None
    return msgs, cursor


def _opencode_row(row):
    seq, mid, typ, ts, data = row
    try:
        d = json.loads(data)
    except ValueError:
        return []
    if typ == "user":
        t = _unwrap_hc(SYSREM.sub("", d.get("text") or "")).strip()
        return [_msg(f"oc-{seq}", "user", t, ts)] if t else []
    out = []
    for i, part in enumerate(d.get("content") or []):
        kind = part.get("type")
        if kind == "text" and (part.get("text") or "").strip():
            out.append(_msg(f"oc-{seq}-{i}", "assistant", part["text"], ts))
        elif kind == "tool":
            inp = (part.get("state") or {}).get("input") or {}
            out.append(_msg(f"oc-{seq}-{i}", "tool", "", ts, {"name": part.get("name") or "tool",
                                                               "summary": _first_str(inp) if isinstance(inp, dict) else ""}))
    return out


def _pb(b):
    """Schema-less protobuf: [(field path, int | str | bytes)]. Antigravity stores steps as protobuf blobs."""
    out = []

    def varint(i):
        r = s = 0
        while True:
            c = b[i]
            i += 1
            r |= (c & 0x7F) << s
            s += 7
            if c < 0x80:
                return r, i

    def walk(lo, hi, path):
        i = lo
        while i < hi:
            k, i = varint(i)
            f, t = k >> 3, k & 7
            p = f"{path}/{f}"
            if t == 0:
                v, i = varint(i)
                out.append((p, v))
            elif t == 1:
                i += 8
            elif t == 5:
                i += 4
            elif t == 2:
                n, i = varint(i)
                if i + n > hi:
                    raise ValueError
                sub = b[i:i + n]
                try:
                    s = sub.decode("utf-8")
                    ok = s.isprintable() or "\n" in s
                except UnicodeDecodeError:
                    ok = False
                if ok:
                    out.append((p, s))
                else:
                    mark = len(out)
                    try:
                        walk(i, i + n, p)
                    except (ValueError, IndexError):
                        del out[mark:]
                        out.append((p, sub))
                i += n
            else:
                raise ValueError
    try:
        walk(0, len(b), "")
    except (ValueError, IndexError):
        pass
    return out


AGY_USER, AGY_MODEL, AGY_TOOL, AGY_ERROR = 14, 15, 132, 17  # step_type values seen in agy 1.x conversation dbs


def _agy_read(path, before, limit):
    with _sqlite(path) as c:
        cur = c.execute("select idx, step_type, step_payload from steps where idx<? and step_type in (?,?,?,?) "
                        "order by idx desc", (int(before) if before else 1 << 62, AGY_USER, AGY_MODEL, AGY_TOOL, AGY_ERROR))
        msgs, cursor = _sql_messages(cur, _agy_row, limit)
    if cursor and int(cursor) <= 0:
        cursor = None
    return msgs, cursor


def _agy_row(row):
    idx, typ, payload = row
    f = {}
    for k, v in _pb(payload or b""):
        f.setdefault(k, v)
    ts = f.get("/5/1/1")
    ts = ts * 1000 if isinstance(ts, int) else None
    if typ == AGY_USER:
        t = f.get("/19/2")
        t = _unwrap_hc(re.sub(r"^/plan\s+", "", t)).strip() if isinstance(t, str) else ""
        return [_msg(f"agy-{idx}", "user", t, ts)] if t else []
    if typ == AGY_MODEL:
        t = f.get("/20/1")
        return [_msg(f"agy-{idx}", "assistant", t, ts)] if isinstance(t, str) and t.strip() else []
    if typ == AGY_ERROR:  # e.g. quota exhausted: shown on screen, so show it here too
        t = f.get("/24/3/1")
        return [_msg(f"agy-{idx}", "tool", t, ts, {"name": "오류", "summary": _one_line(t)})] if isinstance(t, str) else []
    name = f.get("/5/4/2")
    if not isinstance(name, str):
        return []
    try:
        args = json.loads(f.get("/5/4/3") or "{}")
    except ValueError:
        args = {}
    return [_msg(f"agy-{idx}", "tool", "", ts, {"name": name, "summary": _first_str(args) if isinstance(args, dict) else ""})]
