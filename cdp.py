"""Herdr 브라우저(terminal-browser, Electron) 화면을 PNG 로 받는다. 보기만 한다(클릭·입력 없음).

terminal-browser 데몬은 127.0.0.1 에서 Chrome DevTools Protocol 을 연다(실측 2026-11-15, 포트는 실행마다 바뀜).
표준 라이브러리만 쓰므로 웹소켓은 필요한 만큼만(텍스트 프레임 한 번 주고받기) 직접 구현했다.
"""
import base64
import json
import os
import re
import socket
import struct
import subprocess
import urllib.request

PORT_CACHE = {}


def _daemon_ports():
    """terminal-browser 데몬들이 듣는 127.0.0.1 포트들."""
    ps = subprocess.run(['ps', '-A', '-o', 'pid=,command='], capture_output=True, text=True).stdout
    pids = [ln.split(None, 1)[0] for ln in ps.splitlines()
            if 'terminal-browser' in ln and '--daemo' in ln and 'Helper' not in ln]
    ports = []
    for pid in pids:
        out = subprocess.run(['lsof', '-nP', '-iTCP', '-sTCP:LISTEN', '-a', '-p', pid], capture_output=True, text=True).stdout
        ports += [int(p) for p in re.findall(r'127\.0\.0\.1:(\d+) \(LISTEN\)', out)]
    return ports


def pages():
    """[(port, target)] — 열려 있는 페이지들."""
    out = []
    for port in _daemon_ports():
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=3) as r:
                out += [(port, t) for t in json.load(r) if t.get('type') == 'page']
        except (OSError, ValueError):
            continue
    return out


def pick(start_url=None):
    """보여 줄 페이지. ponytail: 터미널↔페이지 연결 정보가 없어, 페이지가 여럿이면 처음 연 주소의 호스트가 같은 것을 고른다."""
    ps = pages()
    if not ps:
        return None
    if len(ps) > 1 and start_url:
        host = re.sub(r'^https?://', '', start_url).split('/')[0].removeprefix('www.')
        same = [p for p in ps if host and host in (p[1].get('url') or '')]
        if same:
            return same[0]
    return ps[0]


class Session:
    """한 페이지에 붙은 DevTools 연결. 화면 크기 맞춤(Emulation)은 연결이 살아 있는 동안만 유지되고, 연결이 끊기면
    브라우저가 스스로 원래 크기로 돌아간다. 그래서 보고 있는 동안만 열어 두고 한동안 안 쓰면 닫는다."""

    def __init__(self, ws_url, timeout=10):
        m = re.match(r'ws://([^/:]+):(\d+)(/.*)', ws_url)
        if not m or m.group(1) not in ('127.0.0.1', 'localhost'):
            raise OSError('local devtools only')
        host, port, path = m.group(1), int(m.group(2)), m.group(3)
        self.ws_url, self.next_id, self.buf = ws_url, 0, b''
        self.s = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f'GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                        f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
        head = b''
        while b'\r\n\r\n' not in head:
            chunk = self.s.recv(4096)
            if not chunk:
                raise OSError('handshake failed')
            head += chunk
        if b' 101 ' not in head.split(b'\r\n', 1)[0]:
            raise OSError('handshake refused')
        self.buf = head.split(b'\r\n\r\n', 1)[1]

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass

    def _need(self, k):
        while len(self.buf) < k:
            chunk = self.s.recv(1 << 16)
            if not chunk:
                raise OSError('closed')
            self.buf += chunk

    def _frame(self):
        data = b''
        while True:
            self._need(2)
            b0, ln = self.buf[0], self.buf[1] & 0x7F
            off = 2
            if ln == 126:
                self._need(4)
                ln, off = struct.unpack('>H', self.buf[2:4])[0], 4
            elif ln == 127:
                self._need(10)
                ln, off = struct.unpack('>Q', self.buf[2:10])[0], 10
            if ln > 60_000_000:
                raise OSError('frame too large')
            self._need(off + ln)
            data += self.buf[off:off + ln]
            self.buf = self.buf[off + ln:]
            if b0 & 0x0F == 0x8:
                raise OSError('closed')
            if b0 & 0x80:
                return data

    def call(self, method, params=None, session_id=None):
        self.next_id += 1
        mid = self.next_id
        msg = {'id': mid, 'method': method, 'params': params or {}}
        if session_id:  # flatten 모드: 한 브라우저 연결 위에서 탭별 세션으로 보낸다
            msg['sessionId'] = session_id
        payload = json.dumps(msg).encode()
        mask, n = os.urandom(4), len(payload)
        hdr = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else
                               bytes([0x80 | 126]) + struct.pack('>H', n) if n < 65536 else
                               bytes([0x80 | 127]) + struct.pack('>Q', n))
        self.s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))
        while True:  # 이벤트 알림은 버리고 이 요청의 응답만 기다린다
            msg = json.loads(self._frame() or b'{}')
            if msg.get('id') == mid:
                if msg.get('error'):
                    raise OSError(msg['error'].get('message') or 'devtools error')
                return msg.get('result') or {}


def _ws_call(ws_url, method, params, timeout=10):
    s = Session(ws_url, timeout)
    try:
        return s.call(method, params)
    finally:
        s.close()


def screenshot(start_url=None):
    """(png 바이트, 페이지 주소, 제목) 또는 None."""
    p = pick(start_url)
    if not p:
        return None
    target = p[1]
    res = _ws_call(target['webSocketDebuggerUrl'], 'Page.captureScreenshot', {'format': 'png'})
    return base64.b64decode(res.get('data') or ''), target.get('url') or '', target.get('title') or ''


KEYS = {'Enter': (13, '\r'), 'Backspace': (8, ''), 'Tab': (9, '\t'), 'Escape': (27, ''),
        'ArrowUp': (38, ''), 'ArrowDown': (40, ''), 'ArrowLeft': (37, ''), 'ArrowRight': (39, '')}


class Viewer:
    """보는 중인 브라우저 한 개. 크기를 화면에 맞추고(볼 때만), 탭·글자·키·스크롤을 넣는다."""

    def __init__(self, start_url):
        self.start_url, self.sess, self.size, self.used = start_url, None, None, 0.0

    def _session(self):
        if self.sess is None:
            p = pick(self.start_url)
            if not p:
                raise OSError('no page')
            self.sess, self.url = Session(p[1]['webSocketDebuggerUrl']), p[1].get('url') or ''
            self.size = None
        return self.sess

    def close(self):
        if self.sess:
            self.sess.close()
        self.sess = None

    def _do(self, fn):
        import time
        self.used = time.time()
        try:
            return fn(self._session())
        except OSError:
            self.close()  # 페이지가 바뀌었거나 닫혔으면 한 번 다시 붙는다
            return fn(self._session())

    resize = False  # Herdr 브라우저는 크기를 바꾸지 않는다: 큰 프레임을 Herdr 앱이 그리다 죽었다(2026-11-15 02:40·02:53)

    def fit(self, w, h):
        if not self.resize:
            return
        w, h = max(320, min(int(w), 1600)), max(320, min(int(h), 2400))
        if self.size == (w, h) and self.sess:
            return

        def go(s):
            s.call('Emulation.setDeviceMetricsOverride', {'width': w, 'height': h, 'deviceScaleFactor': 1, 'mobile': False})
            self.size = (w, h)
        self._do(go)

    def shot(self):
        def go(s):
            r = s.call('Page.captureScreenshot', {'format': 'jpeg', 'quality': 70})
            info = s.call('Runtime.evaluate', {'expression': 'JSON.stringify([location.href, document.title, innerWidth, innerHeight])',
                                               'returnByValue': True})
            url, title, w, h = json.loads((info.get('result') or {}).get('value') or '["","",0,0]')
            self.view = (w, h)  # 클릭 좌표 기준(CSS px)
            return base64.b64decode(r.get('data') or ''), url, title
        return self._do(go)

    def click(self, x, y):
        def go(s):
            for t, buttons in (('mouseMoved', 0), ('mousePressed', 1), ('mouseReleased', 0)):
                s.call('Input.dispatchMouseEvent', {'type': t, 'x': x, 'y': y, 'button': 'left',
                                                    'buttons': buttons, 'clickCount': 1, 'pointerType': 'mouse'})
        self._do(go)

    def scroll(self, dy, x=None, y=None):
        # 마우스 휠 이벤트는 백그라운드 탭에서 끝나지 않고 멈출 때가 있어(실측) 페이지 스크립트로 굴린다.
        # 손가락 아래에 안쪽 스크롤 영역(채팅 목록 등)이 있으면 그것을, 없으면 창을 굴린다.
        js = ('(function(x,y,dy){var e=x==null?null:document.elementFromPoint(x,y);'
              'while(e&&e!==document.body&&e!==document.documentElement){var s=getComputedStyle(e);'
              'if(/(auto|scroll|overlay)/.test(s.overflowY)&&e.scrollHeight>e.clientHeight+1){'
              'var b=e.scrollTop;e.scrollBy(0,dy);if(e.scrollTop!==b)return"el";}e=e.parentElement;}'
              'window.scrollBy(0,dy);return"win";})(%s,%s,%s)' % (
                  'null' if x is None else float(x), 'null' if y is None else float(y), float(dy)))
        self._do(lambda s: s.call('Runtime.evaluate', {'expression': js}))

    def type(self, text):
        self._do(lambda s: s.call('Input.insertText', {'text': text}))

    def key(self, name):
        code, text = KEYS[name]

        def go(s):
            down = {'type': 'keyDown', 'key': name, 'code': name, 'windowsVirtualKeyCode': code}
            if text:
                down['text'] = text
            s.call('Input.dispatchKeyEvent', down)
            s.call('Input.dispatchKeyEvent', {'type': 'keyUp', 'key': name, 'code': name, 'windowsVirtualKeyCode': code})
        self._do(go)

    def back(self):
        self._do(lambda s: s.call('Runtime.evaluate', {'expression': 'history.back()'}))


# ---- 평소 쓰는 Google Chrome (로그인 프로필) ----
# Chrome 144+ 는 chrome://inspect/#remote-debugging 을 켜면 프로필 폴더에 DevToolsActivePort(포트, 브라우저 ws 경로)를
# 남긴다. 이 방식은 HTTP 목록(/json)이 없고, 연결마다 Chrome 이 '허용' 창을 띄운다(공식 문서). 그래서 브라우저 연결
# 하나를 계속 붙들고, 사용자가 고른 탭(target id)에만 붙는다. 프로필 복사·쿠키 추출은 하지 않는다.
CHROME_PORT_FILE = os.environ.get('HERDR_CHAT_CHROME_PORT_FILE') or os.path.expanduser(
    '~/Library/Application Support/Google/Chrome/DevToolsActivePort')  # 환경 변수는 격리 시험용


class Chrome:
    def __init__(self):
        self.sess = None

    def _browser(self):
        if self.sess is None:
            try:
                port, path = open(CHROME_PORT_FILE).read().split()[:2]
            except (OSError, ValueError):
                raise OSError('Chrome 원격 디버깅이 꺼져 있습니다(chrome://inspect/#remote-debugging).') from None
            if not port.isdigit() or not path.startswith('/devtools/browser/'):
                raise OSError('Chrome 접속 정보를 읽을 수 없습니다.')
            # 처음 붙을 때 Chrome 에 '허용' 창이 뜬다. 사람이 누를 시간을 준다.
            self.sess = Session(f'ws://127.0.0.1:{int(port)}{path}', timeout=60)
            self.sess.s.settimeout(15)
        return self.sess

    def call(self, method, params=None, session_id=None):
        try:
            return self._browser().call(method, params, session_id)
        except OSError:
            self.close()
            raise

    def close(self):
        if self.sess:
            self.sess.close()
        self.sess = None

    def tabs(self):
        res = self.call('Target.getTargets')
        return [{'id': t['targetId'], 'title': t.get('title') or '', 'url': t.get('url') or ''}
                for t in res.get('targetInfos') or [] if t.get('type') == 'page' and not (t.get('url') or '').startswith('devtools://')]


class ChromeTab(Viewer):
    """Viewer 와 같은 동작을 고른 탭 하나에. 그 탭이 사라지면 실패한다(다른 탭으로 넘어가지 않음)."""

    resize = True  # 평소 Chrome 창은 Herdr 앱과 무관하다. 보는 동안만 폰 화면 크기로 맞춘다(떼면 풀림)

    def __init__(self, chrome, target_id):
        super().__init__(None)
        self.chrome, self.target_id, self.sid = chrome, target_id, None

    def _session(self):
        if self.sid is None:
            if not any(t['id'] == self.target_id for t in self.chrome.tabs()):
                raise OSError('그 탭이 닫혔습니다.')
            self.sid = self.chrome.call('Target.attachToTarget', {'targetId': self.target_id, 'flatten': True})['sessionId']
            self.size = self.natural = None
        chrome, sid = self.chrome, self.sid

        class _S:  # Viewer 가 쓰는 s.call(method, params) 모양
            def call(self, method, params=None):
                return chrome.call(method, params, sid)
        return _S()

    def _do(self, fn):
        import time
        self.used = time.time()
        try:
            return fn(self._session())
        except OSError:
            self.sid = None  # 한 번만 다시 붙어 본다. 탭이 닫혔으면 _session 이 실패를 올린다.
            return fn(self._session())

    def fit(self, w, h):
        """폭은 이 탭의 원래(데스크톱) 폭 그대로 두고, 높이만 보는 칸의 비율에 맞춘다. 화면은 그 프레임 전체를
        칸에 맞춰 줄여 보여 준다. 폭을 칸만큼 줄이면 최소 폭이 있는 사이트(카카오 파트너센터 등)의 오른쪽이
        잘리고 확대된 것처럼 보였다(2026-11-15 iPad 제보)."""
        w, h = float(w), float(h)
        if w < 100 or h < 100:
            return

        def go(s):
            if self.natural is None:
                s.call('Emulation.clearDeviceMetricsOverride')
                r = s.call('Runtime.evaluate', {'expression': 'innerWidth', 'returnByValue': True})
                self.natural = int((r.get('result') or {}).get('value') or 1280)
            width = max(self.natural, 800)
            for _ in range(2):  # 페이지가 그 폭보다 넓으면(최소 폭) 페이지 폭까지 넓혀 한 번 더 맞춘다
                height = int(min(4000, max(400, round(width * h / w))))
                if self.size != (width, height):
                    s.call('Emulation.setDeviceMetricsOverride',
                           {'width': width, 'height': height, 'deviceScaleFactor': 1, 'mobile': False})
                    self.size = (width, height)
                r = s.call('Runtime.evaluate', {'expression': 'document.documentElement.scrollWidth', 'returnByValue': True})
                sw = int((r.get('result') or {}).get('value') or 0)
                if sw <= width + 4:
                    break
                width = min(sw, 2400)
        self._do(go)

    def close(self):
        if self.sid:
            try:  # 떼면 화면 크기 맞춤도 풀려 Mac 에서 원래대로 보인다
                self.chrome.call('Target.detachFromTarget', {'sessionId': self.sid})
            except OSError:
                pass
        self.sid = None

    def back(self):
        self._do(lambda s: s.call('Runtime.evaluate', {'expression': 'history.back()'}))
