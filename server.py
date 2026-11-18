"""herdr-chat server: API.md over http.server, stdlib only. Usage: python3 server.py [--port 5393]"""
import argparse
import base64
import hashlib
import hmac
import json
import mimetypes
import os
import queue
import re
import secrets
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

import hc_client
from hc_client import HcError

try:  # Web Push 는 .venv 의 cryptography 가 있어야 한다. 없으면 푸시만 꺼진다.
    import push
except ImportError:
    push = None
try:  # 팀원 A 의 모듈. 없으면 화면만(chat=false) 으로 동작한다.
    import cdp
    import mapping
    import remote
    import transcripts
except ImportError:
    cdp = mapping = remote = transcripts = None

ROOT = Path(__file__).resolve().parent
WEB = Path(os.environ.get('HERDR_CHAT_WEB', ROOT / 'web')).resolve()
LOG_DIR = Path(os.environ.get('HERDR_CHAT_LOG_DIR', ROOT / 'logs'))
CONFIG_DIR = Path(os.environ.get('HERDR_CHAT_CONFIG_DIR', Path.home() / '.config/herdr-chat'))
FILES_ROOT = os.environ.get('HERDR_CHAT_FILES_ROOT') or str(Path.home())  # 결과물은 이 폴더 아래만 연다
TOKEN_FILE = Path(os.environ.get('HERDR_CHAT_TOKEN_FILE', Path.home() / '.config/herdr-chat/token'))

SHELLS = {'zsh', 'sh', 'bash', 'fish', 'Herdr'}
PANEL_PROGRAMS = {'claude': ['claude'], 'codex': ['codex'], 'shell': None}
# 새 칸에서 프로그램을 로그인 셸로 돌리고, 끝나면 로그인 셸을 남기는 감싸개(직접 작성).
# bash·zsh·sh 가 아니면(fish 등) macOS 기본 zsh 로 돌린다.
RUN_WRAPPER = ('case "${SHELL##*/}" in bash|zsh|sh) s=$SHELL ;; *) s=/bin/zsh ;; esac\n'
               'exec "$s" -lic \'"$@"; exec "$0" -li\' "$s" "$@"')
FOREGROUND_AGENTS = {'claude': 'claude-code', 'codex': 'codex'}
KEYS = {'enter', 'esc', 'up', 'down', 'left', 'right', 'tab', *'123456789', 'y', 'n', 'ctrl+c'}
MAX_TEXT = 8000
MAX_BODY = 64 * 1024
MAX_ATTACH = 8 * 1024 * 1024                 # hc put 한도(terminal.put)
MAX_ATTACH_BODY = MAX_ATTACH * 4 // 3 + 64 * 1024  # base64 로 부푼 크기, attach 요청에만
ATTACH_BAD_CHARS = re.compile(r'[^가-힣ㄱ-ㅎㅏ-ㅣA-Za-z0-9 _\-.()]')
# 로그인 화면을 그리는 데 필요한 정적 자원. 사용자 데이터가 없는 코드·그림뿐이다.
PUBLIC_EXT = {'.html', '.css', '.js', '.png', '.svg', '.ico', '.webmanifest'}
DOC_EXT = {'.md', '.markdown', '.txt'}
MAX_DOC = 2 * 1024 * 1024
LAYOUT_TTL = 10
LAYOUT_ERROR_TTL = 30       # 연결 안 된 기기는 30초 동안 다시 묻지 않는다
REMOTE_LAYOUT_TIMEOUT = 3
SCREEN_TTL = 2
# 결과물 보기: 허용 확장자 → 종류. 그 밖의 확장자는 목록에도 안 넣고 열지도 않는다.
FILE_KINDS = {'md': 'md', 'markdown': 'md', 'txt': 'text', 'log': 'text', 'json': 'text', 'csv': 'csv', 'tsv': 'csv',
              'html': 'html', 'htm': 'html', 'png': 'image', 'jpg': 'image', 'jpeg': 'image', 'gif': 'image',
              'webp': 'image', 'svg': 'image', 'pdf': 'pdf'}
TEXT_EXT = {'md', 'markdown', 'txt', 'log', 'json', 'csv', 'tsv', 'html', 'htm', 'svg'}
MAX_TEXT_FILE = 2 * 1024 * 1024
MAX_BIN_FILE = 30 * 1024 * 1024
SECRET_NAME = re.compile(r'key|secret|token|credential|password|\.pem|\.p12|id_rsa', re.I)
SKIP_DIRS = {'.git', 'node_modules', '.venv', '__pycache__'}
SANDBOX_CSP = ("sandbox allow-scripts; default-src 'none'; img-src data: blob:; style-src 'unsafe-inline'; "
               "script-src 'unsafe-inline'; font-src data:")
# 대화 속 경로: [x](/abs) 링크, 그리고 공백·따옴표·괄호로 끝나는 /abs·~/ 경로
PATH_RE = re.compile(r'\]\((~?/[^)\s]+)\)|(?<![\w/.~:-])(~?/[^\s`\'"<>()\[\]{},;|*]+)')
# 화면에 보일 기기 이름. 예: HERDR_CHAT_MACHINE_LABELS="local=iMac,macbook-pro=MacBook" (local 은 이 서버가 도는 컴퓨터)
MACHINE_LABELS = {'local': '이 컴퓨터', **dict(
    kv.split('=', 1) for kv in os.environ.get('HERDR_CHAT_MACHINE_LABELS', '').split(',') if '=' in kv)}
GONE_TYPES = {'finished', 'removed'}
TERMINAL_TYPES = {'created', 'restarted', 'lost', 'keep', 'status', 'turn', 'title'}
mimetypes.add_type('application/manifest+json', '.webmanifest')


def load_token():
    try:
        token = TOKEN_FILE.read_text().strip()
        if token and TOKEN_FILE.stat().st_mode & 0o077:
            os.chmod(TOKEN_FILE, 0o600)  # 남이 읽을 수 있게 열려 있으면 나만 읽게 되돌린다
        if token:
            return token
    except FileNotFoundError:
        pass
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    token = secrets.token_urlsafe(32)
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(token + '\n')
    return token


BOX_RULE = re.compile(r'^\s*─{20,}')
OPTION_LINE = re.compile(r'^\s*(?:[❯›>]\s*)?(\d)\.\s+(.*\S)\s*$')
RULE_LINE = re.compile(r'^\s*[─╌━┄-]{10,}\s*$')


def prompt_of(screen):
    """화면 맨 아래의 번호 선택 창(실행 승인 등)을 {question, detail, options} 로. 없으면 None.
    실측(2026-11-15): Claude 'Do you want to proceed? ❯ 1. Yes …', Codex 'Would you like to run …? › 1. Yes, proceed (y) …'.
    두 쪽 모두 숫자 키 하나로 고른다. 선택형 질문(AskUserQuestion: Type something 등)은 따로 다루므로 뺀다."""
    lines = [ln.rstrip() for ln in screen.splitlines()]
    end = len(lines)
    while end and not OPTION_LINE.match(lines[end - 1]):  # 아래 안내 줄(Esc to cancel 등)은 건너뜀
        end -= 1
        if len(lines) - end > 6:
            return None
    start = end
    while start and (OPTION_LINE.match(lines[start - 1]) or (lines[start - 1].startswith('     ') and lines[start - 1].strip())):
        start -= 1
    opts = []
    for ln in lines[start:end]:
        m = OPTION_LINE.match(ln)
        if m:
            opts.append({'n': int(m.group(1)), 'label': m.group(2).strip()})
    if len(opts) < 2 or [o['n'] for o in opts] != list(range(1, len(opts) + 1)):
        return None
    if any('Type something' in o['label'] or o['label'].startswith('[') or 'Chat about this' in o['label'] for o in opts):
        return None
    block = []
    for ln in reversed(lines[max(0, start - 18):start]):
        if BOX_RULE.match(ln):
            break
        if ln.strip() and not RULE_LINE.match(ln):
            block.append(ln.strip().strip('│|').strip())
    block.reverse()
    qi = max((i for i, ln in enumerate(block) if ln.endswith('?')), default=None)
    question = block[qi] if qi is not None else (block[-1] if block else '')
    detail = '\n'.join(ln for i, ln in enumerate(block) if i != qi and not ln.startswith('• Running')).strip()
    return {'question': question[:300], 'detail': detail[:2000], 'options': opts}
PROMPT_LINE = re.compile(r'^\s*❯\s?(.*\S)\s*$')


# 권한 모드. Claude Code 는 입력 상자 아래 줄에 지금 모드를 띄우고 shift+tab 으로 돈다
# (실측 2026-11-17, 2.1.3xx: auto → manual → accept edits → plan → auto. bypass 는 켜 둔 세션에만 끼어든다).
# Codex 는 화면에 모드가 없고 /permissions 고르기 창에서 바꾼다(0.169: 고른 줄에 "(current)").
MODES = {  # (id, 이름, 화면 글, 설명)
    'claude-code': [
        ('auto', '자동', 'auto mode', '위험해 보이는 일만 묻고 나머지는 알아서 합니다.'),
        ('manual', '매번 확인', 'manual mode', '파일 수정·명령 실행마다 묻습니다.'),
        ('accept-edits', '수정 자동 허용', 'accept edits', '파일 수정은 묻지 않고, 명령 실행은 묻습니다.'),
        ('plan', '계획만', 'plan mode', '아무것도 고치지 않고 계획만 세웁니다.'),
        ('bypass', '모든 권한', 'bypass permissions', '아무것도 묻지 않습니다. 켜 두고 시작한 세션에서만 됩니다.')],
    'codex': [
        ('ask', '승인 요청', 'Ask for approval', '작업 폴더 안은 알아서 하고, 인터넷·폴더 밖 수정은 묻습니다.'),
        ('auto', '알아서 승인', 'Approve for me', '위험해 보이는 것만 묻습니다.'),
        ('full', '전체 권한', 'Full Access', '폴더 밖 수정·인터넷도 묻지 않습니다.'),
        ('read-only', '읽기 전용', 'Read Only', '읽기만 하고, 고치거나 인터넷을 쓰려면 묻습니다.')],
}
CLAUDE_MODE_RE = re.compile(r'(auto mode|manual mode|accept edits|plan mode|bypass permissions) on\b')
CODEX_PICK_RE = re.compile(r'^\s*(?:›\s*)?(\d)\.\s+(Ask for approval|Approve for me|Full Access|Read Only)( \(current\))?')


def claude_mode_of(screen):
    """입력 상자 아래 몇 줄에서 지금 모드 id. 대화 본문에 같은 말이 있어도 맨 아래 것을 쓴다."""
    hits = CLAUDE_MODE_RE.findall('\n'.join(screen.splitlines()[-4:]))
    return next((m for m, _, text, _ in MODES['claude-code'] if hits and text == hits[-1]), None)


def codex_picker_of(screen):
    """/permissions 고르기 창: ({label: 번호}, 지금 label). 창이 없으면 ({}, None)."""
    rows, current = {}, None
    for ln in screen.splitlines():
        m = CODEX_PICK_RE.match(ln)
        if m:
            rows[m.group(2)] = m.group(1)
            current = m.group(2) if m.group(3) else current
    return rows, current


def suggestion_of(screen):
    """화면 맨 아래 입력 상자(─ 줄 두 개 사이)의 '❯ …' 글. 상자 안 줄이 하나일 때만(여러 줄이면 사람이 쓰는 중)."""
    lines = screen.splitlines()
    rules = [i for i, ln in enumerate(lines) if BOX_RULE.match(ln)]
    for a, b in zip(rules[-2::-1], rules[:0:-1]):  # 아래쪽 상자부터
        inner = [ln for ln in lines[a + 1:b] if ln.strip()]
        if len(inner) == 1:
            m = PROMPT_LINE.match(inner[0])
            text = m.group(1).strip() if m else ''
            if text and not text.startswith('Try "'):
                return text[:500]
            return None
    return None


def _asking(msgs, before):
    # 최신 페이지의 마지막 메시지가 아직 답하지 않은 선택형 질문이면(Claude AskUserQuestion·Codex request_user_input)
    # 화면이 답할 칸을 띄운다. herdr 가 blocked 를 못 받는 세션(t8d 처럼 셸로 보이는 Claude)도 있다.
    return bool(before is None and msgs and msgs[-1].get('ask'))


def _has_terminal(node, term):
    if node.get('type') == 'block':
        return (node.get('content') or {}).get('terminal') == term
    return any(_has_terminal(c, term) for c in node.get('children') or [])


def audit(tid, kind, chars, result, **extra):
    # 보낸 원문은 남기지 않는다: 시각·id·종류·글자 수(첨부는 이름·바이트)·결과만.
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = json.dumps({'ts_ms': int(time.time() * 1000), 'id': tid, 'kind': kind, 'chars': chars,
                       'result': result, **extra}, ensure_ascii=False)
    with open(LOG_DIR / 'audit.jsonl', 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def pid_of(facts):
    return (facts.get('foreground') or {}).get('pid') or facts.get('pid')


def split_where(where):
    # 탭 이름에 ' · ' 나 ' / ' 가 더 있을 수 있어 첫 ' / ' 에서만 나눈다.
    if not where:
        return '(위치 없음)', None
    space, sep, tab = where.partition(' / ')
    return space, (tab if sep else None)


def safe_file(path):
    """보여 줘도 되는 결과물이면 (실제 경로, 확장자) 아니면 None. 목록을 만들 때와 열 때 둘 다 거친다."""
    home = os.path.realpath(FILES_ROOT)
    real = os.path.realpath(os.path.expanduser(path))
    if not real.startswith(home + os.sep):
        return None
    parts = Path(real).relative_to(home).parts
    # 숨김(.ssh·.config·.claude·.env…)과 비밀 같은 이름은 경로 어느 칸에 있어도 막는다.
    if any(p.startswith('.') or SECRET_NAME.search(p) for p in parts):
        return None
    ext = Path(real).suffix.lower().lstrip('.')
    if ext not in FILE_KINDS or not os.path.isfile(real):
        return None
    return real, ext


URL_RE = re.compile(r'https?://[^\s<>"\'`()\[\]{}]+')


def mentioned_links(msgs, limit=50):
    """에이전트 답(최신이 먼저)에 나온 http(s) 링크. 도구 호출·사용자 글은 빼서 잡음을 줄인다."""
    out, seen = [], set()
    for m in msgs:
        if m.get('role') != 'assistant':
            continue
        for u in URL_RE.findall(m.get('text') or ''):
            u = u.rstrip('.,:;!?*_')
            if u in seen or len(u) > 2000:
                continue
            seen.add(u)
            out.append({'url': u, 'host': urlsplit(u).hostname or '', 'ts_ms': m.get('ts_ms')})
            if len(out) >= limit:
                return out
    return out


def mentioned_paths(texts):
    """메시지 글자들(최신이 먼저)에서 실제 있는 파일 경로를 뽑는다."""
    for text in texts:
        for m in PATH_RE.finditer(text or ''):
            cand = re.sub(r':\d+(:\d+)?$', '', (m.group(1) or m.group(2)).rstrip('.,:;!?)'))
            # 경로 뒤에 붙은 조사('…/a.md를')는 끝 글자를 몇 개 떼어 보며 실제 파일을 찾는다.
            for k in range(4):
                c = cand[:len(cand) - k] if k else cand
                if os.path.isfile(os.path.expanduser(c)):
                    yield c
                    break


def recent_files(cwd, now, limit=50, max_scan=5000, budget=1.0):
    """cwd 아래 24시간 안에 바뀐 파일, 최신순. 숨김·의존성 폴더는 건너뛰고 5000개·1초에서 멈춘다."""
    found, scanned, deadline = [], 0, time.monotonic() + budget
    for root, dirs, files in os.walk(cwd):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]
        for name in files:
            scanned += 1
            if scanned > max_scan or time.monotonic() > deadline:
                break
            p = os.path.join(root, name)
            try:
                m = os.lstat(p).st_mtime
            except OSError:
                continue
            if now - m < 86400 and Path(name).suffix.lower().lstrip('.') in FILE_KINDS:
                found.append((m, p))
        else:
            continue
        break
    return [p for _, p in sorted(found, reverse=True)[:limit]]


def attach_name(name):
    """올린 파일 이름 정리: basename, 앞 '.' 제거, 허용 문자 밖은 _, 120자, 확장자 없으면 .bin."""
    name = re.split(r'[\\/]', name if isinstance(name, str) else '')[-1]
    name = ATTACH_BAD_CHARS.sub('_', name.lstrip('.')).strip()
    if not name:
        return 'attachment.bin'
    stem, ext = os.path.splitext(name)
    if not ext:
        stem, ext = name, '.bin'
    return stem[:max(1, 120 - len(ext))] + ext[:20]


def status_of(facts):
    s = facts.get('status')
    return s.get('state') if isinstance(s, dict) else s


class Hub:
    """One `hc events` per server: keeps terminal facts, fans events out to SSE clients, polls transcript mtimes."""

    def __init__(self):
        self.lock = threading.Lock()
        self.facts = {}          # address -> `hc get` 형태의 dict
        self.resolved = {}       # address -> (pid, result|None, checked_at)
        self.clients = set()
        self.last_sent = {}      # address -> 마지막으로 보낸 terminal payload
        self.mtimes = {}
        self.started = False
        self.ready = threading.Event()
        self.proc = None
        self.stopping = False
        self.on_status = None    # 상태가 바뀌면 (id, 새 상태, 이전 상태) 로 부른다(화면 미리 읽기·푸시)
        self.machine_states = None
        self.remote = {}         # 다른 기기 -> (확인 시각, {address: 짝짓기 결과|None})
        self.remote_lock = threading.Lock()

    def start(self):
        with self.lock:
            if self.started:
                return
            self.started = True
        threading.Thread(target=self._run_events, daemon=True).start()
        threading.Thread(target=self._poll_messages, daemon=True).start()
        self.ready.wait(3)  # 첫 스냅샷을 한 번만 기다린다. 늦으면 hc get 으로 채운다.

    def stop(self):
        self.stopping = True
        if self.proc:
            self.proc.kill()

    # --- hc events ---
    def _run_events(self):
        while not self.stopping:
            try:
                self.proc = hc_client.events()
                for line in self.proc.stdout:
                    self._handle(line)
            except Exception:  # noqa: BLE001 - 끊겨도 서버는 계속 돈다
                pass
            finally:
                if self.proc:
                    self.proc.kill()
            if not self.stopping:
                time.sleep(5)

    def _handle(self, line):
        try:
            msg = json.loads(line)
        except ValueError:
            return
        if 'terminals' in msg:  # 첫 스냅샷, 또는 뒤처진 뒤 다시 온 스냅샷(missed)
            fresh = {t['address']: t for t in msg['terminals'] if t.get('address') and t.get('state') == 'live'}
            with self.lock:
                old = set(self.facts)
                self.facts = fresh
            for addr in old - set(fresh):
                self._gone(addr)
            for t in fresh.values():
                self._terminal(t, quiet=not old)
            self.ready.set()
            return
        if msg.get('method') != 'event':
            return
        p = msg.get('params') or {}
        data, kind = p.get('data'), p.get('type')
        addr = p.get('terminal') or (data or {}).get('address')
        if not addr:
            return
        if kind in GONE_TYPES:
            self._gone(addr)
        elif kind in TERMINAL_TYPES and isinstance(data, dict):
            # 실측: created 만 전체 정보이고 status·turn 등은 바뀐 칸만 온다({"status": {...}}). 합쳐 둔다.
            with self.lock:
                facts = {**self.facts.get(addr, {}), **data, 'address': addr}
                self.facts[addr] = facts
            self._terminal(facts)

    def _terminal(self, facts, quiet=False):
        payload = {'id': facts['address'], 'status': status_of(facts), 'turn': facts.get('turn'),
                   'status_ms': facts.get('status_ms')}
        prev = self.last_sent.get(payload['id'])
        if prev == payload:
            return
        self.last_sent[payload['id']] = payload
        if not quiet:
            self.broadcast('terminal', payload)
            old = (prev or {}).get('status')
            if payload['status'] != old and self.on_status:
                self.on_status(payload['id'], payload['status'], old)

    def _gone(self, addr):
        with self.lock:
            self.facts.pop(addr, None)
            self.resolved.pop(addr, None)
            self.last_sent.pop(addr, None)
            self.mtimes.pop(addr, None)
        self.broadcast('gone', {'id': addr})

    # --- 기록 파일 mtime ---
    def _poll_messages(self):
        tick = 0
        while not self.stopping:
            time.sleep(2)
            tick += 1
            if not self.clients:
                continue
            if tick % 5 == 0:  # hc events 에 기기 이벤트가 없어 10초마다 기기 상태를 본다
                self._poll_machines()
            if tick % 2 == 0:  # 다른 기기 기록은 4초마다(왕복 약 0.2초)
                self._poll_remote_messages()
            if transcripts is None:
                continue
            with self.lock:
                paths = {a: r[1]['path'] for a, r in self.resolved.items() if r[1] and r[1].get('path')}
            for addr, path in paths.items():
                try:
                    m = transcripts.last_change(path)
                except Exception:  # noqa: BLE001
                    continue
                if addr in self.mtimes and self.mtimes[addr] != m:
                    self.broadcast('message', {'id': addr})
                self.mtimes[addr] = m

    def _poll_remote_messages(self):
        # 다른 기기 기록 파일은 4초마다 한 번에 mtime 을 묻는다(왕복 약 0.2초).
        with self.lock:
            jobs = {m: {a: r['path'] for a, r in v[1].items() if r} for m, v in self.remote.items()}
        for machine, paths in jobs.items():
            if not paths:
                continue
            try:
                got = remote.call(machine, 'mtimes', paths=sorted(set(paths.values())))
            except HcError:
                continue
            for addr, path in paths.items():
                m = got.get(path)
                if addr in self.mtimes and self.mtimes[addr] != m:
                    self.broadcast('message', {'id': addr})
                self.mtimes[addr] = m

    def resolve_remote(self, machine, facts_by_addr, max_age=30):
        """다른 기기 터미널들을 그 기기에서 한 번에 짝짓는다(약 1초). 결과는 30초 재사용, 실패도 30초."""
        if remote is None:
            return {}
        with self.remote_lock:  # 같은 기기에 동시에 여러 번 묻지 않게
            with self.lock:
                hit = self.remote.get(machine)
            if hit and time.time() - hit[0] < max_age and set(facts_by_addr) <= set(hit[1]):
                return hit[1]
            try:
                res = remote.call(machine, 'resolve_all', facts_by_addr=facts_by_addr)
            except HcError:
                res = {}
            with self.lock:
                self.remote[machine] = (time.time(), res)
            return res

    def _poll_machines(self):
        try:
            now = {m.get('name'): m.get('state') for m in hc_client.machines()}
        except HcError:
            return
        old, self.machine_states = self.machine_states, now
        if old is None:
            return
        for name, state in now.items():
            if old.get(name) != state:
                self.broadcast('machine', {'id': name, 'state': state})

    # --- SSE 접속자 ---
    def subscribe(self):
        q = queue.Queue(maxsize=200)
        with self.lock:
            self.clients.add(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.clients.discard(q)

    def broadcast(self, event, data):
        frame = f'event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'.encode()
        with self.lock:
            clients = list(self.clients)
        for q in clients:
            try:
                q.put_nowait(frame)
            except queue.Full:  # 못 따라오는 접속자는 끊는다(다시 접속하면 목록부터 새로 받는다)
                self.unsubscribe(q)
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
                q.put_nowait(None)

    # --- 사실·짝짓기 ---
    def facts_for(self, addr):
        self.start()
        with self.lock:
            f = self.facts.get(addr)
        if f is None:
            f = hc_client.get(addr)
            with self.lock:
                self.facts[addr] = f
        return f

    def resolve(self, addr, facts):
        if mapping is None:
            return None
        pid = pid_of(facts)
        with self.lock:
            hit = self.resolved.get(addr)
        # Codex 는 찾는 데 0.1~0.5초(ps)라 같은 pid 면 계속 재사용. Claude 는 1ms 이고 /clear 때
        # 같은 pid 로 새 기록 파일을 쓰므로 매번 다시 찾는다. 실패는 30초 뒤 다시(기록 파일이 늦게 생긴다).
        # opencode·Antigravity 는 /new 가 새 대화(행·파일)를 만들어 30초마다 다시 찾는다.
        fresh = hit and hit[0] == pid and (hit[1].get('kind') == 'codex' or
                                           (hit[1].get('kind') != 'claude' and time.time() - hit[2] < 30)
                                           if hit[1] else time.time() - hit[2] < 30)
        if fresh:
            return hit[1]
        try:
            result = mapping.resolve(facts)
        except Exception:  # noqa: BLE001 - 짝짓기 실패는 화면 대체로 처리
            result = None
        with self.lock:
            self.resolved[addr] = (pid, result, time.time())
        return result


def build_layout(doc, machine='local'):
    """herdr 문서 → API 형태 + 열어도 되는 문서 목록(doc_id -> (실제 경로, 제목)).
    다른 기기의 터미널 id 는 /api/terminals 와 맞게 'macbook-pro/t37' 로 붙인다."""
    docs = {}
    prefix = '' if machine == 'local' else machine + '/'

    def items(node, out):
        c = node.get('content') or {}
        if isinstance(c.get('terminal'), str):
            out.append({'kind': 'terminal', 'id': prefix + c['terminal']})
        elif c.get('mod'):
            f = (c.get('props') or {}).get('file')
            if c['mod'] == 'md-viewer' and isinstance(f, str) and prefix:
                # ponytail: 다른 기기 문서는 아직 못 연다(이 컴퓨터의 같은 경로를 읽으면 안 된다).
                # 다음 단계에서 hc file read --on 으로 연다.
                out.append({'kind': 'mod', 'mod': 'md-viewer', 'title': os.path.basename(f)})
            elif c['mod'] == 'md-viewer' and isinstance(f, str) and os.path.isabs(f):
                real = os.path.realpath(f)
                doc_id = hashlib.sha256(real.encode('utf-8', 'replace')).hexdigest()[:20]
                docs[doc_id] = (real, os.path.basename(f))
                out.append({'kind': 'doc', 'doc_id': doc_id, 'title': os.path.basename(f), 'mod': 'md-viewer'})
            else:
                out.append({'kind': 'mod', 'mod': str(c['mod'])})
        for child in node.get('children') or []:
            items(child, out)
        return out

    spaces = []
    for sp in doc.get('spaces') or []:
        name = sp.get('name') or os.path.basename((sp.get('folder') or '').rstrip('/')) or '(이름 없음)'
        spaces.append({'id': sp.get('id'), 'name': name, 'tabs': [
            {'id': tb.get('id'), 'name': tb.get('name'), 'items': items(tb.get('root') or {}, [])}
            for tb in sp.get('tabs') or []]})
    return spaces, docs


class App:
    def __init__(self):
        self.token = load_token()
        self.hub = Hub()
        self._layouts = {}       # 기기 -> (만료 시각, 기기 항목, docs)
        self._layout_lock = threading.Lock()
        self._msg_cache = {}     # (id, 기록 파일, mtime, before, limit) -> 응답
        self._screens = {}       # (id, lines) -> (읽은 시각, 글자)
        self._codex_modes = {}   # id -> 이 서버가 마지막으로 바꾼 Codex 모드 id
        self._prefetch = threading.BoundedSemaphore(2)
        self.hub.on_status = self._status_changed
        self.pusher = push.Pusher(CONFIG_DIR, on_result=lambda tid, code: audit(tid, 'push', 0, code)) if push else None
        self._ls = (0.0, [])
        self._ls_lock = threading.Lock()
        self._files = {}         # file_id -> 실제 경로. /files 목록이 발급한 것만 연다.
        self._viewers = {}       # 브라우저 터미널 id -> cdp.Viewer (보는 동안만)
        self._viewers_lock = threading.Lock()
        self._chrome_conn = None  # 평소 Chrome 과의 연결 하나(승인은 연결마다라 계속 붙들어 둔다)
        threading.Thread(target=self._reap_viewers, daemon=True).start()

    def ls(self):
        # 1초 캐시: 요청마다 hc ls(약 20ms)를 부르지 않는다. 셸 여부·존재 확인은 1초 늦어도 된다.
        with self._ls_lock:
            if time.time() - self._ls[0] >= 1:
                self._ls = (time.time(), [self._fix_agent(t) for t in hc_client.ls()])
            return self._ls[1]

    def _fix_agent(self, t):
        # 실측: Claude 가 프로세스 이름을 버전('2.1.314')으로 바꾸면 herdr 가 셸(zsh)로 본다.
        # 셸로 보고된 터미널은 앞에서 도는 프로그램 이름으로 다시 판단한다(셸 차단은 그대로 유지).
        if t.get('agent') not in SHELLS:
            return t
        with self.hub.lock:
            fg = (self.hub.facts.get(t['address']) or {}).get('foreground') or {}
        prog = os.path.basename((fg.get('argv') or [''])[0])
        agent = FOREGROUND_AGENTS.get(prog)
        return {**t, 'agent': agent} if agent else t

    def machines(self):
        out = [{'id': 'local', 'label': MACHINE_LABELS['local'], 'state': 'connected'}]
        for m in hc_client.machines():
            name = m.get('name')
            out.append({'id': name, 'label': MACHINE_LABELS.get(name, name), 'state': m.get('state'),
                        'why': m.get('why'), 'since_ms': m.get('since_ms')})
        return {'machines': out}

    def _machine_layout(self, machine, state):
        with self._layout_lock:
            hit = self._layouts.get(machine)
        if hit and time.time() < hit[0]:
            return hit[1], hit[2]
        try:
            if machine == 'local':
                spaces, docs = build_layout(hc_client.layout())
            elif state == 'unreachable':
                raise HcError('unreachable', '기기에 연결되지 않았습니다.')
            else:
                spaces, docs = build_layout(hc_client.layout(machine, REMOTE_LAYOUT_TIMEOUT), machine)
            entry, ttl = {'id': machine, 'spaces': spaces}, LAYOUT_TTL
        except HcError as e:
            entry, docs, ttl = {'id': machine, 'spaces': None, 'error': e.code}, {}, LAYOUT_ERROR_TTL
        with self._layout_lock:
            self._layouts[machine] = (time.time() + ttl, entry, docs)
        return entry, docs

    def layout(self):
        try:
            remote = hc_client.machines()
        except HcError:
            remote = []
        out = [self._machine_layout('local', 'connected')[0]]
        out += [self._machine_layout(m['name'], m.get('state'))[0] for m in remote if m.get('name')]
        return {'machines': out}

    def local_docs(self):
        return self._machine_layout('local', 'connected')[1]

    def files(self, tid):
        t = self.find(tid)
        if '/' in tid:  # ponytail: 다른 기기 파일은 아직 없음(hc file read --on 로 확장). 링크만 준다.
            try:
                resp = self._remote_messages(tid, None, 500) if t.get('agent') not in SHELLS and remote else None
            except HcError:
                resp = None
            return {'files': [], 'links': mentioned_links(list(reversed((resp or {}).get('messages') or [])))}
        facts = self.hub.facts_for(tid)
        cwd = (facts.get('foreground') or {}).get('cwd') or facts.get('cwd') or t.get('cwd')
        picked, msgs = [], []
        r = self.hub.resolve(tid, facts) if t.get('agent') not in SHELLS and transcripts else None
        if r:
            try:
                msgs, _ = transcripts.read_messages(r['kind'], r['path'], limit=500)
            except Exception:  # noqa: BLE001 - 기록을 못 읽으면 recent 만
                msgs = []
            texts = [m.get('text', '') + ' ' + ((m.get('tool') or {}).get('summary') or '') for m in reversed(msgs)]
            picked += [(p, 'mentioned') for p in mentioned_paths(texts)]
        if cwd and os.path.isdir(cwd):
            picked += [(p, 'recent') for p in recent_files(cwd, time.time())]
        out, seen = [], set()
        for p, source in picked:
            ok = safe_file(p)
            if not ok or ok[0] in seen or len(out) >= 150:
                continue
            real, ext = ok
            seen.add(real)
            st = os.stat(real)
            file_id = hashlib.sha256(real.encode('utf-8', 'replace')).hexdigest()[:20]
            if len(self._files) > 5000:
                self._files.clear()
            self._files[file_id] = real
            base = os.path.realpath(cwd) if cwd else ''
            rel = os.path.relpath(real, base) if base and real.startswith(base + os.sep) \
                else '~/' + os.path.relpath(real, os.path.realpath(FILES_ROOT))
            out.append({'file_id': file_id, 'name': os.path.basename(real), 'rel': rel, 'kind': FILE_KINDS[ext],
                        'size': st.st_size, 'mtime_ms': int(st.st_mtime * 1000), 'source': source})
        return {'files': out, 'links': mentioned_links(list(reversed(msgs)))}

    def file(self, file_id):
        real = self._files.get(file_id) if re.fullmatch(r'[0-9a-f]{20}', file_id) else None
        if not real:
            raise HcError('file_not_found', '그 파일이 없습니다. 목록을 다시 받아 주세요.', 404)
        ok = safe_file(real)
        # 목록을 만든 뒤 링크로 바꿔치기했으면 실제 경로가 달라진다.
        if not ok or ok[0] != real:
            raise HcError('file_not_allowed', '열 수 없는 파일입니다.', 403)
        ext = ok[1]
        limit = MAX_TEXT_FILE if ext in TEXT_EXT else MAX_BIN_FILE
        try:
            fd = os.open(real, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            raise HcError('file_not_found', '파일이 없습니다.', 404) from None
        except OSError:
            raise HcError('file_not_allowed', '열 수 없는 파일입니다.', 403) from None
        with os.fdopen(fd, 'rb') as f:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode):
                raise HcError('file_not_allowed', '열 수 없는 파일입니다.', 403)
            data = f.read(limit + 1)
        if len(data) > limit:
            raise HcError('file_too_large', f'{limit // (1024 * 1024)}MB 보다 큰 파일은 열 수 없습니다.', 413)
        return data, ext

    def attach(self, tid, body):
        name, size = attach_name(body.get('name')), 0
        try:
            t = self.find(tid)
            if t.get('agent') in SHELLS:
                raise HcError('shell_blocked', '셸·Herdr 터미널에는 보낼 수 없습니다.', 403)
            data = body.get('data')
            if not isinstance(data, str):
                raise HcError('bad_data', '파일 내용이 없습니다.', 400)
            if len(data) > MAX_ATTACH_BODY:
                raise HcError('file_too_large', '8MB 까지 올릴 수 있습니다.', 413)
            try:
                raw = base64.b64decode(data, validate=True)
            except ValueError:
                raise HcError('bad_data', '파일 내용(base64)이 올바르지 않습니다.', 400) from None
            size = len(raw)
            if size > MAX_ATTACH:
                raise HcError('file_too_large', '8MB 까지 올릴 수 있습니다.', 413)
            tmp = tempfile.mkdtemp(prefix='herdr-chat-')  # 0700
            try:
                path = os.path.join(tmp, name)
                with open(path, 'xb') as f:
                    f.write(raw)
                landed = hc_client.put(tid, path).get('path')
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            if not landed:
                raise HcError('hc_invalid_response', 'herdr 가 놓인 경로를 알려 주지 않았습니다.')
        except HcError as e:
            audit(tid, 'attach', 0, e.code, name=name, bytes=size)
            raise
        audit(tid, 'attach', 0, 'ok', name=name, bytes=size)
        # hc 가 이름을 한 번 더 바꿀 수 있어(실측: 한글·공백·괄호 → _) 실제로 놓인 이름을 돌려준다.
        return {'ok': True, 'path': landed, 'name': os.path.basename(landed), 'size': size}

    def read_screen(self, tid, lines=60):
        key = (tid, lines)
        hit = self._screens.get(key)
        if hit and time.time() - hit[0] < SCREEN_TTL:
            return hit[1]
        text = hc_client.read(tid, lines).get('text', '')
        self._screens[key] = (time.time(), text)
        return text

    def _status_changed(self, tid, status, old):
        if status == 'blocked':
            self._prefetch_screen(tid)
        if self.pusher and (status == 'blocked' or (status == 'done' and old == 'working')):
            threading.Thread(target=self._push, args=(tid, status), daemon=True).start()

    def _push(self, tid, kind):
        try:
            t = next((x for x in self.ls() if x['address'] == tid), {})
            facts = self.hub.facts.get(tid) or {}
        except HcError:
            t, facts = {}, {}
        machine = tid.rpartition('/')[0] or 'local'
        space, tab = split_where(t.get('where'))
        where = ' / '.join(x for x in (space, tab) if x)
        # 대화 원문은 넣지 않는다: 기기·위치·제목만.
        body = ' · '.join(x for x in (MACHINE_LABELS.get(machine, machine), where, facts.get('title')) if x)
        title = '답을 기다리는 중' if kind == 'blocked' else '작업을 마쳤습니다'
        self.pusher.notify(tid, kind, {'title': title, 'body': body[:200], 'id': tid, 'tag': tid})

    def push_api(self, action, body):
        if not self.pusher:
            raise HcError('push_unavailable', '푸시를 쓸 수 없습니다(cryptography 없음).', 503)
        if action == 'key':
            return {'public_key': self.pusher.public_key}
        try:
            if action == 'subscribe':
                return {'ok': True, 'prefs': self.pusher.subscribe(body.get('subscription'), body.get('prefs'))}
            return {'ok': True, 'removed': self.pusher.unsubscribe(body.get('endpoint'))}
        except ValueError as e:
            msg = {'endpoint_not_allowed': '허용되지 않은 푸시 주소입니다.', 'bad_keys': '구독 키가 올바르지 않습니다.',
                   'too_many': '구독이 너무 많습니다.'}.get(str(e), '구독 정보가 올바르지 않습니다.')
            raise HcError(str(e), msg, 400) from None

    def _prefetch_screen(self, tid):
        def run():
            with self._prefetch:  # 동시에 최대 2개
                self._forget_screen(tid)
                try:
                    self.read_screen(tid)
                except HcError:
                    pass
        threading.Thread(target=run, daemon=True).start()

    def _forget_screen(self, tid):
        for key in [k for k in self._screens if k[0] == tid]:
            self._screens.pop(key, None)

    def doc(self, doc_id):
        # 클라이언트는 경로를 보내지 않는다. 지금 레이아웃에 있는 md-viewer 파일만 연다.
        if not re.fullmatch(r'[0-9a-f]{20}', doc_id):
            raise HcError('doc_not_found', '그 문서가 없습니다.', 404)
        entry = self.local_docs().get(doc_id)
        if not entry:
            raise HcError('doc_not_found', '그 문서가 없습니다.', 404)
        real, title = entry
        if not real.startswith(os.path.realpath(FILES_ROOT) + os.sep):
            raise HcError('doc_not_allowed', '열 수 없는 문서입니다.', 403)
        # 레이아웃을 읽은 뒤 링크를 바꿔치기해도 다른 파일을 열지 않게, 지금 실제 경로가 같은지 다시 본다.
        if os.path.realpath(real) != real or Path(title).suffix.lower() not in DOC_EXT \
                or Path(real).suffix.lower() not in DOC_EXT:
            raise HcError('doc_not_allowed', '열 수 없는 문서입니다.', 403)
        try:
            fd = os.open(real, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            raise HcError('doc_not_found', '문서 파일이 없습니다.', 404) from None
        except OSError:
            raise HcError('doc_not_allowed', '열 수 없는 문서입니다.', 403) from None
        with os.fdopen(fd, 'rb') as f:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode):
                raise HcError('doc_not_allowed', '열 수 없는 문서입니다.', 403)
            if st.st_size > MAX_DOC:
                raise HcError('doc_too_large', '2MB 보다 큰 문서는 열 수 없습니다.', 413)
            data = f.read(MAX_DOC + 1)
        if len(data) > MAX_DOC:
            raise HcError('doc_too_large', '2MB 보다 큰 문서는 열 수 없습니다.', 413)
        return {'title': title, 'text': data.decode('utf-8', 'replace'), 'mtime_ms': int(st.st_mtime * 1000)}

    # --- 새 공간·탭·칸 (herdr 의 herdr-app 문서를 고친다. Herdr 앱이 바로 반영한다: 2026-11-15 실측) ---
    def create(self, kind, body):
        machine = body.get('machine') or 'local'
        on = None if machine == 'local' else hc_client._machine(machine)
        program = body.get('program') or 'claude'
        if program not in PANEL_PROGRAMS:
            raise HcError('bad_program', '띄울 프로그램은 claude·codex·shell 중 하나입니다.', 400)
        name = ' '.join(str(body.get('name') or '').split())[:60]
        if kind in ('space', 'tab') and not name:
            raise HcError('bad_name', '이름을 적어 주세요.', 400)
        sp_id, tb_id, p_id = ('sp' + secrets.token_hex(16), 'tb' + secrets.token_hex(16), 'p' + secrets.token_hex(16))
        doc = hc_client.doc_get(on)
        spaces = doc['value'].get('spaces')
        if not isinstance(spaces, list):
            raise HcError('hc_layout_unavailable', 'herdr 공간·탭 구성을 읽을 수 없습니다.')
        # 새 칸을 띄울 공간·탭과 폴더를 정한다.
        if kind == 'space':
            folder = self._folder(body.get('folder'), on)
        else:
            space, tab = self._find_tab(spaces, body.get('space_id'), body.get('tab_id'), kind)
            sp_id, folder = space['id'], space.get('folder') or self._folder(None, on)
            if kind == 'panel':
                tb_id = tab['id']
        argv = ['/bin/zsh', '-l'] if program == 'shell' else ['sh', '-c', RUN_WRAPPER, 'sh', *PANEL_PROGRAMS[program]]
        term = hc_client.run(argv, folder, [('herdr.space', sp_id), ('herdr.tab', tb_id)], on)
        addr = term.get('address')
        block = {'content': {'terminal': addr.rpartition('/')[2]}, 'id': p_id, 'type': 'block'}
        try:
            for attempt in range(3):  # 그사이 Herdr 앱이 문서를 고쳤으면 다시 읽어 다시 고친다
                if attempt:
                    doc = hc_client.doc_get(on)
                value = doc['value']
                self._backup(value, doc.get('version'), machine)
                spaces = value['spaces']
                # 실측(2026-11-15): Herdr 앱은 새 터미널의 herdr.space·herdr.tab 라벨을 보고 그 공간·탭을 먼저
                # 만들어 둘 수 있다(이름 없음). 같은 id 가 이미 있으면 새로 붙이지 않고 그것을 채운다.
                same = [x for x in spaces if x.get('id') == sp_id]
                for dup in same[1:]:
                    spaces.remove(dup)
                space = same[0] if same else None
                if kind == 'space':
                    if space is None:
                        space = {'folder': folder, 'id': sp_id, 'name': name, 'tabs': []}
                        spaces.append(space)
                    space.update(folder=space.get('folder') or folder, name=name)
                elif space is None:
                    raise HcError('layout_not_found', '그 공간이 없습니다. 목록을 새로 고쳐 주세요.', 404)
                tab = next((t for t in space.setdefault('tabs', []) if t.get('id') == tb_id), None)
                if tab is None:
                    if kind == 'panel':
                        raise HcError('layout_not_found', '그 탭이 없습니다. 목록을 새로 고쳐 주세요.', 404)
                    tab = {'id': tb_id, 'name': '', 'root': {'children': [], 'id': tb_id, 'type': 'row'}}
                    space['tabs'].append(tab)
                if kind != 'panel':
                    tab['name'] = '1' if kind == 'space' else name
                root = tab['root']
                if root.get('type') != 'row' or not isinstance(root.get('children'), list):
                    old = dict(root, id='p' + secrets.token_hex(16))  # 다른 모양이면 한 칸으로 감싸 옆에 붙인다
                    tab['root'] = root = {'children': [old], 'id': tab['id'], 'type': 'row'}
                if not _has_terminal(root, block['content']['terminal']):
                    kids = root['children']
                    if kind == 'panel':  # 칸 너비를 똑같이 나눈다
                        for k in kids:
                            k['size'] = {'weight': 1 / (len(kids) + 1)}
                        block['size'] = {'weight': 1 / (len(kids) + 1)}
                    kids.append(block)
                try:
                    hc_client.doc_set(value, doc['version'], on)
                    break
                except HcError as e:
                    if e.status != 409 or attempt == 2:
                        raise
        except Exception:
            subprocess.run([hc_client.binary(), 'kill', addr], env=hc_client._env(), capture_output=True, timeout=15)
            raise
        audit(addr, 'create_' + kind, 0, 'ok', program=program)
        with self._layout_lock:
            self._layouts.pop(machine, None)
        self._ls = (0.0, [])
        return {'ok': True, 'id': addr, 'space_id': sp_id, 'tab_id': tb_id}

    @staticmethod
    def _find_tab(spaces, space_id, tab_id, kind):
        for s in spaces:
            if s.get('id') == space_id:
                if kind == 'tab':
                    return s, None
                for t in s.get('tabs') or []:
                    if t.get('id') == tab_id:
                        return s, t
        raise HcError('layout_not_found', '그 공간이나 탭이 없습니다. 목록을 새로 고쳐 주세요.', 404)

    @staticmethod
    def _folder(folder, on):
        if on is None:
            folder = os.path.expanduser(folder or '~')
            if not os.path.isabs(folder) or not os.path.isdir(folder):
                raise HcError('bad_folder', '그 폴더가 없습니다.', 400)
            return os.path.realpath(folder)
        code, home = hc_client.exec_(['printenv', 'HOME'], on)
        home = home.strip()
        folder = (folder or '~').strip()
        if folder.startswith('~'):
            folder = home + folder[1:]
        if not folder.startswith('/') or hc_client.exec_(['test', '-d', folder], on)[0] != 0:
            raise HcError('bad_folder', '그 기기에 그 폴더가 없습니다.', 400)
        return folder

    @staticmethod
    def _backup(value, version, machine):
        d = LOG_DIR / 'layout-backups'
        d.mkdir(parents=True, exist_ok=True)
        (d / f'herdr-app-{machine}-v{version}.json').write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        for old in sorted(d.glob('*.json'), key=os.path.getmtime)[:-50]:
            old.unlink()

    def terminals(self):
        out = []
        ls = self.ls()
        remote_facts = {}        # 기기 -> {address: facts}
        for t in ls:
            machine, _, _ = t['address'].rpartition('/')
            if machine and t.get('agent') not in SHELLS:
                try:
                    remote_facts.setdefault(machine, {})[t['address']] = self.hub.facts_for(t['address'])
                except HcError:
                    pass
        paired = {}
        for machine, facts in remote_facts.items():
            paired.update(self.hub.resolve_remote(machine, facts))
        for t in ls:
            addr = t['address']
            try:
                facts = self.hub.facts_for(addr)
            except HcError:
                facts = {}
            agent = t.get('agent')
            machine, _, _ = addr.rpartition('/')
            if machine:
                chat = bool(paired.get(addr))
            else:
                chat = bool(agent not in SHELLS and facts and self.hub.resolve(addr, facts))
            labels = facts.get('labels') or {}
            space, tab = split_where(t.get('where'))
            out.append({'id': addr, 'machine': machine or 'local', 'agent': agent, 'where': t.get('where'),
                        'space_id': labels.get('herdr.space'), 'tab_id': labels.get('herdr.tab'),
                        'space': space, 'tab': tab,
                        'title': facts.get('title') or None, 'status': t.get('status') or status_of(facts),
                        'turn': t.get('turn'), 'status_ms': facts.get('status_ms'), 'chat': chat,
                        'writable': agent not in SHELLS})
        return {'terminals': out}

    def find(self, tid):
        hc_client.terminal_id(tid)
        for t in self.ls():
            if t['address'] == tid:
                return t
        raise HcError('not_found', '그 터미널이 없습니다.', 404)

    def _remote_messages(self, tid, before, limit):
        machine = tid.rpartition('/')[0]
        r = self.hub.resolve_remote(machine, {tid: self.hub.facts_for(tid)}).get(tid)
        if not r:
            return None
        # mtime 을 따로 묻지 않고 짧게만 재사용한다. 더 오래된 쪽 페이지는 잘 바뀌지 않아 1분.
        key = ('remote', tid, r['path'], before, limit)
        hit = self._msg_cache.get(key)
        if hit and time.time() - hit[0] < (60 if before else 3):
            return hit[1]
        got = remote.call(machine, 'messages', kind=r['kind'], path=r['path'], before=before, limit=limit)
        msgs = got.get('messages') or []
        resp = {'source': 'transcript', 'messages': msgs, 'cursor': got.get('cursor'), 'asking': _asking(msgs, before)}
        if len(self._msg_cache) > 300:
            self._msg_cache.clear()
        self._msg_cache[key] = (time.time(), resp)
        return resp

    def _browser_url(self, tid):
        """Herdr 브라우저(terminal-browser) 터미널이면 처음 연 주소(없으면 ''), 아니면 None."""
        if '/' in tid:  # ponytail: 다른 기기 브라우저는 그 기기 포트라 아직 못 봄
            return None
        argv = (self.hub.facts_for(tid).get('foreground') or {}).get('argv') or []
        if not argv or os.path.basename(argv[0]) != 'terminal-browser':
            return None
        return next((a for a in argv[2:] if re.match(r'https?://', a)), '')

    def _viewer(self, tid):
        self.find(tid)
        start = self._browser_url(tid)
        if start is None or cdp is None:
            raise HcError('not_browser', '브라우저 터미널이 아닙니다.', 404)
        with self._viewers_lock:
            v = self._viewers.get(tid)
            if v is None:
                v = self._viewers[tid] = cdp.Viewer(start)
                v.lock = threading.Lock()
        return v

    def _reap_viewers(self):
        # 45초 동안 아무도 안 보면 연결을 닫는다 → 브라우저가 Mac 화면 크기로 돌아간다.
        while True:
            time.sleep(10)
            with self._viewers_lock:
                idle = [k for k, v in self._viewers.items() if time.time() - v.used > 45]
                for k in idle:
                    with self._viewers[k].lock:
                        self._viewers.pop(k).close()

    # --- 평소 쓰는 Chrome 의 탭 (사용자가 고른 탭 하나만) ---
    def _chrome(self):
        if cdp is None:
            raise HcError('browser_unavailable', 'Chrome 연결을 쓸 수 없습니다.', 503)
        with self._viewers_lock:
            if self._chrome_conn is None:
                self._chrome_conn = cdp.Chrome()
                self._chrome_conn.lock = threading.Lock()
        return self._chrome_conn

    def chrome_tabs(self):
        c = self._chrome()
        try:
            with c.lock:
                return {'tabs': c.tabs()}
        except (OSError, TimeoutError) as e:
            raise HcError('chrome_unavailable', str(e) if 'Chrome' in str(e) else
                          'Chrome 에 연결하지 못했습니다. iMac Chrome 에 뜬 원격 디버깅 \'허용\' 창을 눌러 주세요.', 503) from None

    def _chrome_viewer(self, target):
        if not re.fullmatch(r'[0-9A-F]{32}', target or ''):
            raise HcError('bad_target', '탭 주소 형식이 올바르지 않습니다.', 400)
        c = self._chrome()
        key = 'chrome:' + target
        with self._viewers_lock:
            v = self._viewers.get(key)
            if v is None:
                v = self._viewers[key] = cdp.ChromeTab(c, target)
                v.lock = c.lock  # 한 브라우저 연결을 같이 쓰므로 잠금도 같이
        return v

    def chrome_png(self, target, w=None, h=None):
        return self._shot(self._chrome_viewer(target), w, h)

    def chrome_act(self, target, body):
        return self._act(self._chrome_viewer(target), 'chrome:' + target, body)

    def browser_png(self, tid, w=None, h=None):
        return self._shot(self._viewer(tid), w, h)

    def _shot(self, v, w, h):
        try:
            with v.lock:
                if w and h:
                    v.fit(w, h)
                img, url, title = v.shot()
                return img, url, title, tuple(int(x) for x in (getattr(v, 'view', None) or (w or 0, h or 0)))
        except (OSError, ValueError, TimeoutError) as e:
            msg = str(e) if '탭' in str(e) or 'Chrome' in str(e) else '브라우저 화면을 받을 수 없습니다.'
            raise HcError('browser_unavailable', msg, 503) from None

    def browser_act(self, tid, body):
        return self._act(self._viewer(tid), tid, body)

    def _act(self, v, tid, body):
        """탭(x·y 는 맞춘 화면 기준 CSS px)·글자·키·스크롤·뒤로. 이 사람의 브라우저를 대신 조작하므로 감사 기록을 남긴다."""
        act = body.get('action')
        try:
            with v.lock:
                if act == 'click':
                    v.click(float(body['x']), float(body['y']))
                elif act == 'type':
                    text = body.get('text')
                    if not isinstance(text, str) or not text or len(text) > MAX_TEXT:
                        raise HcError('bad_text', '입력할 글이 없습니다.', 400)
                    v.type(text)
                elif act == 'key' and body.get('key') in cdp.KEYS:
                    v.key(body['key'])
                elif act == 'scroll':
                    x, y = body.get('x'), body.get('y')
                    v.scroll(max(-3000, min(3000, float(body.get('dy') or 0))),
                             None if x is None else float(x), None if y is None else float(y))
                elif act == 'back':
                    v.back()
                else:
                    raise HcError('bad_action', '알 수 없는 동작입니다.', 400)
        except (OSError, KeyError, TypeError, ValueError, TimeoutError):
            raise HcError('browser_unavailable', '브라우저에 보내지 못했습니다.', 503) from None
        audit(tid, 'browser_' + act, len(body.get('text') or ''), 'ok')
        return {'ok': True}

    def messages(self, tid, before, limit):
        t = self.find(tid)
        if before is None and self._browser_url(tid) is not None:
            # 브라우저는 글이 아니라 그림이다. 화면이 /browser 이미지를 주기적으로 받아 그린다.
            return {'source': 'browser', 'cursor': None, 'messages': []}
        if '/' in tid and t.get('agent') not in SHELLS and remote is not None:
            try:
                resp = self._remote_messages(tid, before, limit)
                if resp:
                    return resp
            except HcError:
                pass  # 기기에 닿지 않으면 화면으로 대체
        if '/' not in tid and t.get('agent') not in SHELLS and transcripts is not None:
            facts = self.hub.facts_for(tid)
            r = self.hub.resolve(tid, facts)
            if r:
                try:
                    # 기록 파일이 그대로면 다시 해석하지 않고 이전 응답을 준다.
                    key = (tid, r['path'], transcripts.last_change(r['path']), before, limit)
                    if key not in self._msg_cache:
                        msgs, cursor = transcripts.read_messages(r['kind'], r['path'], before=before, limit=limit)
                        if len(self._msg_cache) > 300:
                            self._msg_cache.clear()
                        self._msg_cache[key] = {'source': 'transcript', 'messages': msgs, 'cursor': cursor,
                                                'asking': _asking(msgs, before)}
                    return self._msg_cache[key]
                except Exception:  # noqa: BLE001 - 기록 해석이 실패하면 화면으로 대체
                    pass
        text = self.read_screen(tid, 60)
        # 같은 화면이면 같은 id: 화면이 id 로 중복을 걸러 낸다. 화면이 바뀌면 새 id.
        sid = 'screen-' + hashlib.sha1(text.encode('utf-8', 'replace')).hexdigest()[:12]
        return {'source': 'screen', 'cursor': None, 'messages': [{
            'id': sid, 'role': 'tool', 'text': text,
            'ts_ms': int(time.time() * 1000), 'tool': {'name': 'screen', 'summary': '화면'}}]}

    def prompt(self, tid):
        t = self.find(tid)
        if t.get('agent') in SHELLS:
            return {'prompt': None}
        return {'prompt': prompt_of(hc_client.read(tid, 40).get('text', ''))}

    def suggest(self, tid):
        """Claude Code 가 입력칸에 흐리게 띄우는 '다음에 보낼 말' 추천. 화면 글자에서 읽는다(색 정보가 없어 사람이
        터미널에 직접 치다 만 글과 구별되지 않는다. 앱으로 쓰는 세션에서는 거의 없는 경우라 그대로 둔다)."""
        t = self.find(tid)
        if t.get('agent') != 'claude-code' or (t.get('status') or '') in ('working', 'blocked'):
            return {'text': None}
        return {'text': suggestion_of(self.read_screen(tid, 12))}

    def mode(self, tid):
        """{"modes": [{"id", "label"}], "current": id|null}. Codex 는 화면에 없어 이 서버가 마지막으로 바꾼 값만 안다."""
        t = self.find(tid)
        table = MODES.get(t.get('agent'))
        if not table:
            return {'modes': [], 'current': None}
        if t.get('agent') == 'claude-code':
            current = claude_mode_of(self.read_screen_fresh(tid))
        else:
            current = self._codex_modes.get(tid)  # ponytail: 서버 재시작·터미널에서 직접 바꾸면 모름(null)
        return {'modes': [{'id': m, 'label': label, 'desc': desc} for m, label, _, desc in table], 'current': current}

    def set_mode(self, tid, body):
        t = self.find(tid)
        agent, want = t.get('agent'), body.get('mode')
        table = {m: text for m, _, text, _ in MODES.get(agent, [])}
        if want not in table:
            raise HcError('bad_mode', '이 터미널에서 고를 수 없는 모드입니다.', 400)
        if t.get('status') == 'blocked' or (agent == 'codex' and t.get('status') == 'working'):
            raise HcError('busy', '답을 기다리는 중이라 지금은 바꿀 수 없습니다.' if t.get('status') == 'blocked'
                          else 'Codex 가 일하는 중이라 끝난 뒤에 바꿀 수 있습니다.', 409)
        try:
            got = self._set_claude_mode(tid, want) if agent == 'claude-code' else self._set_codex_mode(tid, want, table)
        except HcError as e:
            audit(tid, 'mode', 0, e.code, mode=want)
            raise
        finally:
            self._forget_screen(tid)
        audit(tid, 'mode', 0, 'ok', mode=want)
        return {'ok': True, 'current': got}

    def _set_claude_mode(self, tid, want):
        # 한 바퀴(모드 5개) 안에 못 만나면 이 세션엔 없는 모드다(bypass 를 안 켠 세션 등).
        seen = []
        for _ in range(6):
            cur = claude_mode_of(self.read_screen_fresh(tid))
            if cur == want:
                return cur
            if cur and cur in seen:
                break
            seen.append(cur)
            hc_client.keys(tid, ['shift+tab'])
            time.sleep(0.3)
        raise HcError('mode_unavailable', '이 세션에서는 그 모드로 바꿀 수 없습니다.', 409)

    def _set_codex_mode(self, tid, want, table):
        label = table[want]
        hc_client.send(tid, '/permissions')
        time.sleep(1.5)
        rows, _ = codex_picker_of(self.read_screen_fresh(tid))
        if label not in rows:
            hc_client.keys(tid, ['esc'])
            raise HcError('mode_failed', '권한 고르기 창이 열리지 않았습니다. 화면을 확인해 주세요.', 502)
        hc_client.keys(tid, [rows[label]])
        time.sleep(1.2)
        if 'Yes, continue anyway' in self.read_screen_fresh(tid):  # Full Access 는 한 번 더 묻는다(앱에서 이미 확인받음)
            hc_client.keys(tid, ['1'])
            time.sleep(1.0)
        hc_client.send(tid, '/permissions')  # 다시 열어 "(current)" 로 확인하고 닫는다
        time.sleep(1.5)
        _, current = codex_picker_of(self.read_screen_fresh(tid))
        hc_client.keys(tid, ['esc'])
        if current != label:
            raise HcError('mode_failed', '모드가 바뀌었는지 확인하지 못했습니다. 화면을 확인해 주세요.', 502)
        self._codex_modes[tid] = want
        return want

    def screen(self, tid, lines):
        t = self.find(tid)
        return {'text': self.read_screen(tid, lines), 'status': t.get('status')}

    def answer(self, tid, body):
        """떠 있는 선택형 질문(Claude AskUserQuestion)에 답한다. 실측한 키 순서(2026-11-15, Claude Code 2.1.325):
        한 개 고르기는 숫자 하나로 답하고 다음 질문으로 간다. 여러 개 고르기는 숫자로 체크한 뒤 → 로 넘어간다.
        '직접 입력'은 (선택지 수+1) 번을 고른 뒤 글을 넣고 Enter. 끝에 검토 화면이 뜨면 1(Submit answers)."""
        t = self.find(tid)
        if t.get('agent') in SHELLS:
            raise HcError('shell_blocked', '셸·Herdr 터미널에는 보낼 수 없습니다.', 403)
        latest = self.messages(tid, None, 20)
        msgs = latest.get('messages') or []
        ask = msgs[-1].get('ask') if latest.get('asking') and msgs else None
        if not isinstance(ask, dict) or ask.get('tool') != 'claude':
            raise HcError('no_question', '지금 답할 질문이 없습니다. 화면을 새로 고쳐 주세요.', 409)
        qs, answers = ask['questions'], body.get('answers')
        if not isinstance(answers, list) or len(answers) != len(qs):
            raise HcError('bad_answer', '모든 질문에 답해 주세요.', 400)
        plan = []  # ('keys', [..]) | ('text', str)
        for q, a in zip(qs, answers):
            n = len(q['options'])
            text = a.get('text') if isinstance(a, dict) else None
            picks = a.get('choices') if isinstance(a, dict) else None
            if isinstance(text, str) and text.strip():
                if len(text) > 2000 or n + 1 > 9:
                    raise HcError('bad_answer', '직접 입력은 2000자까지입니다.', 400)
                plan += [('keys', [str(n + 1)]), ('text', text.strip())]
                continue
            if not isinstance(picks, list) or not picks or not all(isinstance(i, int) and 1 <= i <= min(n, 9) for i in picks):
                raise HcError('bad_answer', '선택지를 골라 주세요.', 400)
            if not q['multi'] and len(picks) != 1:
                raise HcError('bad_answer', '하나만 고를 수 있는 질문입니다.', 400)
            plan += [('keys', [str(i)]) for i in dict.fromkeys(picks)]
            if q['multi']:
                plan.append(('keys', ['right']))
        for kind, val in plan:
            if kind == 'keys':
                hc_client.keys(tid, val)
            else:
                hc_client.send(tid, val, force=True)
            time.sleep(0.4)
        time.sleep(0.6)
        if 'Submit answers' in self.read_screen_fresh(tid):
            hc_client.keys(tid, ['1'])
        audit(tid, 'answer', sum(len(v) for k, v in plan if k == 'text'), 'ok', questions=len(qs))
        self._screens.clear()
        return {'ok': True}

    def read_screen_fresh(self, tid):
        return hc_client.read(tid, 30).get('text', '')

    def act(self, tid, kind, body):
        chars = 0
        try:
            t = self.find(tid)
            if t.get('agent') in SHELLS:
                raise HcError('shell_blocked', '셸·Herdr 터미널에는 보낼 수 없습니다.', 403)
            if kind == 'send':
                text = body.get('text')
                if not isinstance(text, str) or not text.strip():
                    raise HcError('bad_text', '보낼 글이 비어 있습니다.', 400)
                chars = len(text)
                if chars > MAX_TEXT:
                    raise HcError('text_too_long', f'{MAX_TEXT}자까지 보낼 수 있습니다.', 400)
                hc_client.send(tid, text)
            elif kind == 'keys':
                keys = body.get('keys')
                if not isinstance(keys, list) or not 0 < len(keys) <= 20 or not all(k in KEYS for k in keys):
                    raise HcError('bad_keys', '허용되지 않은 키입니다.', 400)
                chars = len(keys)
                hc_client.keys(tid, keys)
            else:
                hc_client.interrupt(tid)
        except HcError as e:
            audit(tid, kind, chars, e.code)
            raise
        finally:
            self._forget_screen(tid)  # 누른 뒤에는 2초 캐시 대신 새 화면을 보여 준다
        audit(tid, kind, chars, 'ok')
        return {'ok': True}


class Handler(BaseHTTPRequestHandler):
    app = None  # make_server 가 채운다
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):  # 주소창 쿼리는 남기되 본문은 남기지 않는다
        pass

    # --- 응답 도우미 ---
    def _send(self, status, body, ctype='application/json; charset=utf-8', extra=None):
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        if 'Content-Security-Policy' not in (extra or {}):
            self.send_header('Content-Security-Policy', "frame-ancestors 'self'")  # 다른 사이트가 이 앱을 끼워 넣지 못하게
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self._timing()
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _timing(self):
        if self.path.startswith('/api/') and hasattr(self, '_t0'):
            self.send_header('Server-Timing', f'app;dur={(time.perf_counter() - self._t0) * 1000:.1f}')

    def _json(self, status, data):
        self._send(status, json.dumps(data, ensure_ascii=False).encode())

    def _error(self, status, code, message):
        self._json(status, {'error': code, 'message': message})

    def _authed(self):
        token = self.app.token.encode()
        auth = self.headers.get('Authorization', '')
        if auth.startswith('Bearer ') and hmac.compare_digest(auth[7:].strip().encode(), token):
            return True
        for part in self.headers.get('Cookie', '').split(';'):
            k, _, v = part.strip().partition('=')
            if k == 'hc_token' and hmac.compare_digest(unquote(v).encode(), token):
                return True
        return False

    # --- 라우팅 ---
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self._t0 = time.perf_counter()
        url = urlsplit(self.path)
        path = unquote(url.path)
        if not path.startswith('/api/'):
            return self._static(path)
        if not self._authed():
            return self._error(401, 'unauthorized', '토큰이 필요합니다.')
        q = {k: v[-1] for k, v in parse_qs(url.query).items()}
        self._route('GET', path, q, None)

    def do_POST(self):
        self._t0 = time.perf_counter()
        path = unquote(urlsplit(self.path).path)
        self.close_connection = True  # 본문을 안 읽고 거절하는 경우가 있어 연결을 재사용하지 않는다
        if not self._authed():
            return self._error(401, 'unauthorized', '토큰이 필요합니다.')
        # JSON 만 받는다: 다른 사이트의 폼 전송(CSRF)은 이 형식을 만들 수 없다.
        if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
            return self._error(415, 'json_required', 'Content-Type 은 application/json 이어야 합니다.')
        try:
            n = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            n = -1
        if not 0 <= n <= (MAX_ATTACH_BODY if path.endswith('/attach') else MAX_BODY):
            return self._error(413, 'body_too_large', '요청이 너무 큽니다.')
        try:
            body = json.loads(self.rfile.read(n) or b'{}')
        except ValueError:
            return self._error(400, 'bad_json', 'JSON 형식이 올바르지 않습니다.')
        if not isinstance(body, dict):
            return self._error(400, 'bad_json', 'JSON 객체여야 합니다.')
        self._route('POST', path, {}, body)

    def _route(self, method, path, q, body):
        app = self.app
        try:
            if method == 'GET' and path.startswith('/api/files/'):
                return self._file(path[len('/api/files/'):])
            if method == 'GET' and path == '/api/push/key':
                return self._json(200, app.push_api('key', None))
            if method == 'POST' and path in ('/api/push/subscribe', '/api/push/unsubscribe'):
                return self._json(200, app.push_api(path.rsplit('/', 1)[1], body))
            if method == 'GET' and path == '/api/machines':
                return self._json(200, app.machines())
            if method == 'GET' and path == '/api/layout':
                return self._json(200, app.layout())
            if method == 'GET' and path.startswith('/api/docs/'):
                return self._json(200, app.doc(path[len('/api/docs/'):]))
            if method == 'GET' and path == '/api/terminals':
                return self._json(200, app.terminals())
            if method == 'GET' and path == '/api/chrome/tabs':
                return self._json(200, app.chrome_tabs())
            m = re.fullmatch(r'/api/chrome/([^/]+)/browser', path)
            if m and method == 'GET':
                img, url, title, view = app.chrome_png(m.group(1), q.get('w') and float(q['w']), q.get('h') and float(q['h']))
                return self._send(200, img, 'image/jpeg', {'X-Page-Url': quote(url, safe=':/?&=#%'),
                                                          'X-Page-Title': quote(title), 'X-View-Size': '%d,%d' % view})
            if m and method == 'POST':
                return self._json(200, app.chrome_act(m.group(1), body))
            if method == 'POST' and path in ('/api/layout/space', '/api/layout/tab', '/api/layout/panel'):
                return self._json(200, app.create(path.rsplit('/', 1)[1], body))
            if method == 'GET' and path == '/api/events':
                return self._events()
            prefix = '/api/terminals/'
            tid, _, action = path[len(prefix):].rpartition('/') if path.startswith(prefix) else ('', '', '')
            if not tid:
                return self._error(404, 'not_found', '없는 주소입니다.')
            if method == 'GET' and action == 'messages':
                limit = max(1, min(200, int(q.get('limit') or 50)))
                return self._json(200, app.messages(tid, q.get('before') or None, limit))
            if method == 'GET' and action == 'browser':
                img, url, title, view = app.browser_png(tid, q.get('w') and float(q['w']), q.get('h') and float(q['h']))
                return self._send(200, img, 'image/jpeg', {'X-Page-Url': quote(url, safe=':/?&=#%'),
                                                          'X-Page-Title': quote(title), 'X-View-Size': '%d,%d' % view})
            if method == 'POST' and action == 'browser':
                return self._json(200, app.browser_act(tid, body))
            if method == 'GET' and action == 'prompt':
                return self._json(200, app.prompt(tid))
            if method == 'GET' and action == 'suggest':
                return self._json(200, app.suggest(tid))
            if method == 'GET' and action == 'mode':
                return self._json(200, app.mode(tid))
            if method == 'POST' and action == 'mode':
                return self._json(200, app.set_mode(tid, body))
            if method == 'GET' and action == 'files':
                return self._json(200, app.files(tid))
            if method == 'GET' and action == 'screen':
                lines = max(1, min(500, int(q.get('lines') or 60)))
                return self._json(200, app.screen(tid, lines))
            if method == 'POST' and action == 'attach':
                return self._json(200, app.attach(tid, body))
            if method == 'POST' and action == 'answer':
                return self._json(200, app.answer(tid, body))
            if method == 'POST' and action in ('send', 'keys', 'interrupt'):
                return self._json(200, app.act(tid, action, body))
            return self._error(404, 'not_found', '없는 주소입니다.')
        except HcError as e:
            return self._error(e.status, e.code, e.message)
        except ValueError:
            return self._error(400, 'bad_request', '요청 값이 올바르지 않습니다.')
        except Exception:  # noqa: BLE001
            return self._error(500, 'server_error', '서버 내부 오류입니다.')

    def _file(self, file_id):
        data, ext = self.app.file(file_id)
        ctype = {'md': 'text/markdown', 'markdown': 'text/markdown', 'log': 'text/plain', 'tsv': 'text/tab-separated-values',
                 'json': 'application/json'}.get(ext) or mimetypes.guess_type('x.' + ext)[0] or 'application/octet-stream'
        if ext in TEXT_EXT:
            ctype += '; charset=utf-8'
        # html·svg 는 고유 출처 샌드박스로: 앱 토큰·쿠키·API 에 닿지 못한다.
        extra = {'Content-Security-Policy': SANDBOX_CSP} if ext in ('html', 'htm', 'svg') else None
        self._send(200, data, ctype, extra)

    def _events(self):
        hub = self.app.hub
        hub.start()
        q = hub.subscribe()
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Accel-Buffering', 'no')
            self._timing()
            self.end_headers()
            self.wfile.write(b': connected\n\n')
            self.wfile.flush()
            while True:
                try:
                    frame = q.get(timeout=15)
                except queue.Empty:
                    frame = b': keep-alive\n\n'
                if frame is None:
                    break
                self.wfile.write(frame)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            hub.unsubscribe(q)
            self.close_connection = True

    def _static(self, path):
        rel = path.lstrip('/') or 'index.html'
        target = (WEB / rel).resolve()
        if target != WEB and WEB not in target.parents:
            return self._error(404, 'not_found', '없는 파일입니다.')
        if target.is_dir():
            target = target / 'index.html'
        if not self._authed() and target.suffix not in PUBLIC_EXT:
            target = WEB / 'index.html'  # 토큰이 없으면 로그인 화면만
        if not target.is_file():
            return self._error(404, 'not_found', '없는 파일입니다.')
        ctype = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
        if ctype.startswith('text/') or ctype.endswith(('javascript', 'json')):
            ctype += '; charset=utf-8'
        self._send(200, target.read_bytes(), ctype)


def make_server(port=5393):
    handler = type('BoundHandler', (Handler,), {'app': App()})
    server = ThreadingHTTPServer(('127.0.0.1', port), handler)
    server.daemon_threads = True
    return server


def _raise_interrupt(*_):
    raise KeyboardInterrupt


def main():
    ap = argparse.ArgumentParser(description='herdr-chat server')
    ap.add_argument('--port', type=int, default=5393)
    args = ap.parse_args()
    server = make_server(args.port)
    # launchd·kill 의 SIGTERM 에도 hc events 자식을 정리한다(안 하면 고아 프로세스로 남는다).
    signal.signal(signal.SIGTERM, _raise_interrupt)
    print(f'herdr-chat: http://127.0.0.1:{args.port}  (토큰: {TOKEN_FILE})', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.RequestHandlerClass.app.hub.stop()


if __name__ == '__main__':
    main()
