"""Server tests against tests/fake_hc.py. Run: python3 -m unittest discover -s tests"""
import http.client
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
TMP = tempfile.TemporaryDirectory()
T = Path(TMP.name)
(T / 'web').mkdir()
(T / 'web' / 'index.html').write_text('<h1>login</h1>')
(T / 'web' / 'app.js').write_text('// app')
(T / 'web' / 'data.json').write_text('{"private": 1}')
(T / 'secret.txt').write_text('SECRET')
R = T / 'home'           # 결과물 허용 범위(진짜 홈 대신)
D = R / 'docs'           # 문서 칸도 이 범위 안만 연다
D.mkdir(parents=True)
(D / 'ok.md').write_text('# 안녕')
(D / 'script.sh').write_text('echo SECRET')
(T / 'secret.key').write_text('SECRET')
(D / 'link.md').symlink_to(T / 'secret.key')
(D / 'big.md').write_bytes(b'x' * (2 * 1024 * 1024 + 1))
(D / 'swap.md').write_text('swap')
W = R / 'proj'           # t1 의 작업 폴더
for rel, body in {'proj/report.md': '# 보고서', 'proj/page.html': '<script>1</script>', 'proj/pic.png': 'PNG',
                  'proj/run.sh': 'echo', 'proj/api_key.md': 'SECRET', 'proj/.hidden/notes.md': 'SECRET',
                  'proj/node_modules/x.md': 'dep', 'proj/old.md': 'old', 'proj/swap.md': 'swap',
                  'notes/plan.md': '계획', 'other.md': 'SECRET', '.ssh/id.md': 'SECRET'}.items():
    (R / rel).parent.mkdir(parents=True, exist_ok=True)
    (R / rel).write_text(body)
os.utime(R / 'proj/old.md', (0, time.time() - 3 * 86400))
(W / 'link.md').symlink_to(R / '.ssh/id.md')
(T / 'outside.md').write_text('SECRET')
os.environ.update(HC_BIN=str(HERE / 'fake_hc.py'), FAKE_DOC_DIR=str(D), FAKE_CWD=str(W),
                  HERDR_CHAT_FILES_ROOT=str(R), FAKE_HC_LOG=str(T / 'hc.log'), HERDR_CHAT_WEB=str(T / 'web'),
                  HERDR_CHAT_LOG_DIR=str(T / 'logs'), HERDR_CHAT_CONFIG_DIR=str(T / 'cfg'), HERDR_CHAT_TOKEN_FILE=str(T / 'cfg' / 'token'))
sys.path.insert(0, str(HERE.parent))
os.environ['HERDR_CHAT_MACHINE_LABELS'] = 'local=iMac,macbook-pro=MacBook'  # 설정 읽기도 함께 시험
import server  # noqa: E402

server.mapping = server.transcripts = None  # 짝짓기는 팀원 A 의 테스트가 맡는다. 여기선 화면 대체만 본다.


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = server.make_server(0)
        cls.pushed = []  # 진짜 푸시 서비스로 나가지 않게 바꿔 끼운다
        if cls.srv.RequestHandlerClass.app.pusher:
            cls.srv.RequestHandlerClass.app.pusher.post = lambda e, b, h: cls.pushed.append(e) or 201
        cls.port = cls.srv.server_address[1]
        cls.token = (T / 'cfg' / 'token').read_text().strip()
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.RequestHandlerClass.app.hub.stop()
        cls.srv.shutdown()

    def req(self, method, path, body=None, auth=True, headers=None):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        h = {'Authorization': f'Bearer {self.token}'} if auth else {}
        if body is not None:
            h['Content-Type'] = 'application/json'
        h.update(headers or {})
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
        r = c.getresponse()
        raw = r.read()
        c.close()
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw.decode()

    def test_token_file_private(self):
        self.assertEqual((T / 'cfg' / 'token').stat().st_mode & 0o777, 0o600)

    def test_auth_required(self):
        self.assertEqual(self.req('GET', '/api/terminals', auth=False)[0], 401)
        self.assertEqual(self.req('GET', '/api/terminals', headers={'Authorization': 'Bearer wrong'})[0], 401)
        self.assertEqual(self.req('POST', '/api/terminals/t1/send', {'text': 'x'}, auth=False)[0], 401)
        st, _ = self.req('GET', '/api/terminals', auth=False, headers={'Cookie': f'a=b; hc_token={self.token}'})
        self.assertEqual(st, 200)

    def test_terminals(self):
        st, d = self.req('GET', '/api/terminals')
        self.assertEqual(st, 200)
        by = {t['id']: t for t in d['terminals']}
        self.assertEqual(set(by), {'t1', 't2', 'mac/t3'})
        self.assertEqual(by['t1']['title'], '제목 t1')
        self.assertTrue(by['t1']['writable'])
        self.assertFalse(by['t2']['writable'])
        self.assertEqual(by['mac/t3']['machine'], 'mac')
        self.assertEqual(by['t1']['machine'], 'local')
        self.assertFalse(by['t1']['chat'])

    def test_terminals_space_tab(self):
        by = {t['id']: t for t in self.req('GET', '/api/terminals')[1]['terminals']}
        pick = lambda t: (t['space_id'], t['tab_id'], t['space'], t['tab'])  # noqa: E731
        self.assertEqual(pick(by['t1']), ('sp1', 'tb1', '시험', '클로드'))
        self.assertEqual(pick(by['t2']), (None, None, '시험', '셸 / 하위 · 탭'))  # 첫 ' / ' 에서만
        self.assertEqual(pick(by['mac/t3']), (None, None, '(위치 없음)', None))

    def test_layout_and_docs(self):
        st, d = self.req('GET', '/api/layout')
        self.assertEqual(st, 200)
        local = d['machines'][0]
        self.assertEqual(local['id'], 'local')
        self.assertEqual([s['name'] for s in local['spaces']], ['me', '내 프로젝트'])  # 빈 이름은 폴더 이름
        tab1, tab2 = local['spaces'][0]['tabs']
        self.assertEqual(tab1['items'][0], {'kind': 'terminal', 'id': 't1'})
        self.assertEqual((tab1['items'][1]['kind'], tab1['items'][1]['title']), ('doc', 'ok.md'))
        self.assertEqual(tab1['items'][2], {'kind': 'mod', 'mod': 'web-viewer'})
        self.assertNotIn(str(D), json.dumps(d))  # 경로는 내보내지 않는다
        self.assertEqual(tab2['items'][-1], {'kind': 'mod', 'mod': 'md-viewer'})  # 상대 경로는 문서로 안 연다
        ids = {i['title']: i['doc_id'] for i in tab1['items'] + tab2['items'] if i['kind'] == 'doc'}
        st, doc = self.req('GET', f"/api/docs/{ids['ok.md']}")
        self.assertEqual((st, doc['title'], doc['text']), (200, 'ok.md', '# 안녕'))
        self.assertIsInstance(doc['mtime_ms'], int)
        self.assertEqual(self.req('GET', f"/api/docs/{ids['script.sh']}")[0], 403)   # 확장자
        self.assertEqual(self.req('GET', f"/api/docs/{ids['link.md']}")[0], 403)     # 링크 끝이 .key
        self.assertEqual(self.req('GET', f"/api/docs/{ids['big.md']}")[0], 413)
        self.assertEqual(self.req('GET', f"/api/docs/{ids['gone.md']}")[0], 404)
        # 레이아웃을 읽은 뒤 링크로 바꿔치기해도 다른 파일은 안 열린다
        (D / 'swap.md').unlink()
        (D / 'swap.md').symlink_to(T / 'secret.txt')
        st, body = self.req('GET', f"/api/docs/{ids['swap.md']}")
        self.assertEqual(st, 403)
        self.assertNotIn('SECRET', str(body))

    def test_machines_and_remote_layout(self):
        st, d = self.req('GET', '/api/machines')
        self.assertEqual(st, 200)
        self.assertEqual([(m['id'], m['label'], m['state']) for m in d['machines']],
                         [('local', 'iMac', 'connected'), ('mac', 'mac', 'connected'),
                          ('macbook-pro', 'MacBook', 'unreachable')])
        self.assertEqual(d['machines'][2]['why'], '자는 중')
        by = {m['id']: m for m in self.req('GET', '/api/layout')[1]['machines']}
        self.assertEqual(by['macbook-pro'], {'id': 'macbook-pro', 'spaces': None, 'error': 'unreachable'})
        items = by['mac']['spaces'][0]['tabs'][0]['items']
        self.assertEqual(items[0], {'kind': 'terminal', 'id': 'mac/t3'})  # /api/terminals 와 같은 id
        self.assertEqual(items[1], {'kind': 'mod', 'mod': 'md-viewer', 'title': 'ok.md'})  # 원격 문서는 아직 못 연다
        terms = {t['id']: t for t in self.req('GET', '/api/terminals')[1]['terminals']}
        self.assertEqual((terms['mac/t3']['machine'], terms['mac/t3']['chat']), ('mac', False))

    def test_machine_events(self):
        hub = server.Hub()
        q = hub.subscribe()
        states = [[{'name': 'mb', 'state': 'unreachable'}], [{'name': 'mb', 'state': 'connected'}]]
        orig = server.hc_client.machines
        server.hc_client.machines = lambda: states.pop(0)
        try:
            hub._poll_machines()
            hub._poll_machines()
        finally:
            server.hc_client.machines = orig
        self.assertEqual(q.get_nowait(), b'event: machine\ndata: {"id": "mb", "state": "connected"}\n\n')

    def test_ls_cache(self):
        time.sleep(1.1)
        n = lambda: (T / 'hc.log').read_text().splitlines().count('["ls"]')  # noqa: E731
        before = n()
        for _ in range(3):
            self.req('GET', '/api/terminals/t1/screen')
        self.assertEqual(n() - before, 1)  # 1초 안의 요청은 hc ls 한 번

    def test_attach_name(self):
        n = server.attach_name
        self.assertEqual(n('../../.ssh/사진 (1).JPG'), '사진 (1).JPG')  # basename 만
        self.assertEqual(n('C:\\x\\..\\.env'), 'env.bin')
        self.assertEqual(n('a;rm -rf $HOME|b.txt'), 'a_rm -rf _HOME_b.txt')
        self.assertEqual(n('noext'), 'noext.bin')
        self.assertEqual(n(''), 'attachment.bin')
        self.assertEqual(n(None), 'attachment.bin')
        self.assertEqual(n('...'), 'attachment.bin')
        long = n('가' * 300 + '.pdf')
        self.assertEqual((len(long), long[-4:]), (120, '.pdf'))

    def test_attach(self):
        import base64
        import glob
        import tempfile
        pattern = os.path.join(tempfile.gettempdir(), 'herdr-chat-*')
        before = set(glob.glob(pattern))
        b64 = base64.b64encode('내용'.encode()).decode()
        st, d = self.req('POST', '/api/terminals/mac%2Ft3/attach', {'name': '../보고서 v1.md', 'data': b64})
        self.assertEqual(st, 200)
        self.assertEqual(d, {'ok': True, 'path': '/fake/files/mac/t3/보고서 v1.md', 'name': '보고서 v1.md',
                             'size': len('내용'.encode())})
        calls = [json.loads(x) for x in (T / 'hc.log').read_text().splitlines()]
        self.assertIn(['put', 'mac/t3', '보고서 v1.md', '내용', '0o700'], calls)
        self.assertEqual(set(glob.glob(pattern)), before)  # 임시 폴더가 남지 않는다
        st, d = self.req('POST', '/api/terminals/t2/attach', {'name': 'a.txt', 'data': b64})
        self.assertEqual((st, d['error']), (403, 'shell_blocked'))
        big = base64.b64encode(b'x' * (8 * 1024 * 1024 + 1)).decode()
        st, d = self.req('POST', '/api/terminals/t1/attach', {'name': 'big.bin', 'data': big})
        self.assertEqual((st, d['error']), (413, 'file_too_large'))
        self.assertEqual(self.req('POST', '/api/terminals/t1/attach', {'name': 'a', 'data': '%%%'})[0], 400)
        # 다른 요청은 본문 한도가 그대로(64KB)
        self.assertEqual(self.req('POST', '/api/terminals/t1/send', {'text': 'x' * 70000})[0], 413)
        audit = [json.loads(x) for x in (T / 'logs' / 'audit.jsonl').read_text().splitlines() if '"attach"' in x]
        self.assertIn({'id': 'mac/t3', 'result': 'ok', 'name': '보고서 v1.md', 'bytes': 6},
                      [{k: a[k] for k in ('id', 'result', 'name', 'bytes')} for a in audit])
        self.assertNotIn('내용', (T / 'logs' / 'audit.jsonl').read_text())
        self.assertEqual(set(glob.glob(pattern)), before)

    @unittest.skipIf(server.push is None, 'cryptography 없음')
    def test_push_api(self):
        st, d = self.req('GET', '/api/push/key')
        self.assertEqual(st, 200)
        self.assertEqual(len(server.push.unb64u(d['public_key'])), 65)
        import base64
        sub = {'endpoint': 'https://web.push.apple.com/abc',
               'keys': {'p256dh': d['public_key'], 'auth': base64.urlsafe_b64encode(b'x' * 16).decode().rstrip('=')}}
        st, r = self.req('POST', '/api/push/subscribe', {'subscription': sub, 'prefs': {'blocked': True}})
        self.assertEqual((st, r['prefs']), (200, {'blocked': True, 'done': False}))
        st, r = self.req('POST', '/api/push/subscribe', {'subscription': dict(sub, endpoint='https://evil.com/x')})
        self.assertEqual((st, r['error']), (400, 'endpoint_not_allowed'))
        self.assertEqual(self.req('POST', '/api/push/subscribe', {'subscription': sub}, auth=False)[0], 401)
        # 상태가 blocked 로 바뀌면 백그라운드로 보내고 감사 기록에 결과 코드만 남긴다
        app = self.srv.RequestHandlerClass.app
        app._status_changed('mac/t3', 'blocked', 'working')
        for _ in range(50):
            if sub['endpoint'] in self.pushed:
                break
            time.sleep(0.05)
        self.assertIn(sub['endpoint'], self.pushed)
        time.sleep(0.1)
        audit = [json.loads(x) for x in (T / 'logs' / 'audit.jsonl').read_text().splitlines() if '"push"' in x]
        self.assertIn(('mac/t3', 201), [(a['id'], a['result']) for a in audit])
        st, r = self.req('POST', '/api/push/unsubscribe', {'endpoint': sub['endpoint']})
        self.assertEqual((st, r['removed']), (200, 1))

    def test_server_timing(self):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        c.request('GET', '/api/machines', headers={'Authorization': f'Bearer {self.token}'})
        r = c.getresponse()
        r.read()
        c.close()
        self.assertRegex(r.getheader('Server-Timing') or '', r'^app;dur=\d+(\.\d+)?$')

    def reads(self, *args):
        return [x for x in (T / 'hc.log').read_text().splitlines() if json.loads(x) == ['read', *args]]

    def test_screen_cache_and_blocked_prefetch(self):
        self.req('GET', '/api/terminals/t1/screen?lines=7')
        self.req('GET', '/api/terminals/t1/screen?lines=7')
        self.assertEqual(len(self.reads('--lines', '7', 't1')), 1)  # 2초 캐시
        self.req('POST', '/api/terminals/t1/keys', {'keys': ['enter']})
        self.req('GET', '/api/terminals/t1/screen?lines=7')
        self.assertEqual(len(self.reads('--lines', '7', 't1')), 2)  # 키를 누르면 캐시를 버린다
        # 가짜 events 가 t1 을 working → blocked 로 바꾸면 화면을 미리 읽는다
        self.req('GET', '/api/terminals')
        for _ in range(30):
            if self.reads('--lines', '60', 't1'):
                break
            time.sleep(0.1)
        self.assertTrue(self.reads('--lines', '60', 't1'))

    def test_messages_cache(self):
        calls, mtime = [], [1.0]

        class FakeTranscripts:
            @staticmethod
            def read_messages(kind, path, before=None, limit=50):
                calls.append(before)
                return [{'id': 'm1', 'role': 'user', 'text': 'hi', 'ts_ms': 1}], None

            @staticmethod
            def last_change(path):
                return mtime[0]

        app = self.srv.RequestHandlerClass.app
        orig = server.transcripts, app.hub.resolve
        server.transcripts, app.hub.resolve = FakeTranscripts, lambda a, f: {'kind': 'claude', 'path': '/x.jsonl'}
        try:
            for _ in range(2):
                st, d = self.req('GET', '/api/terminals/t1/messages?limit=5')
                self.assertEqual((st, d['source']), (200, 'transcript'))
            self.assertEqual(len(calls), 1)  # 기록 파일이 그대로면 다시 해석하지 않는다
            mtime[0] = 2.0
            self.req('GET', '/api/terminals/t1/messages?limit=5')
            self.req('GET', '/api/terminals/t1/messages?limit=5&before=c1')
            self.assertEqual(calls, [None, None, 'c1'])
        finally:
            server.transcripts, app.hub.resolve = orig

    def test_files(self):
        said = (f'결과는 {R}/notes/plan.md를 보세요. [키]({R}/.ssh/id.md) 와 {T}/outside.md, '
                f'{W}/run.sh, {W}/report.md:12 도 만들었습니다.')

        class FakeTranscripts:
            @staticmethod
            def read_messages(kind, path, before=None, limit=50):
                return [{'id': 'm1', 'role': 'assistant', 'text': said, 'ts_ms': 1}], None

        app = self.srv.RequestHandlerClass.app
        orig = server.transcripts, app.hub.resolve
        server.transcripts, app.hub.resolve = FakeTranscripts, lambda a, f: {'kind': 'claude', 'path': '/x.jsonl'}
        try:
            st, d = self.req('GET', '/api/terminals/t1/files')
        finally:
            server.transcripts, app.hub.resolve = orig
        self.assertEqual(st, 200)
        by = {f['name']: f for f in d['files']}
        # 숨김·키 이름·허용 안 된 확장자·홈 밖·의존성 폴더·24시간 지난 파일·숨김으로 가는 링크는 빠진다
        self.assertEqual(set(by), {'plan.md', 'report.md', 'page.html', 'pic.png', 'swap.md'})
        self.assertEqual((by['plan.md']['source'], by['plan.md']['rel']), ('mentioned', '~/notes/plan.md'))
        self.assertEqual((by['report.md']['source'], by['report.md']['rel']), ('mentioned', 'report.md'))
        self.assertEqual((by['page.html']['source'], by['page.html']['kind']), ('recent', 'html'))
        self.assertEqual(by['pic.png']['kind'], 'image')

        def get(fid, **kw):
            c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
            c.request('GET', f'/api/files/{fid}', headers=kw.get('headers', {'Authorization': f'Bearer {self.token}'}))
            r = c.getresponse()
            body = r.read()
            c.close()
            return r, body

        r, body = get(by['report.md']['file_id'])
        self.assertEqual((r.status, body.decode()), (200, '# 보고서'))
        self.assertEqual(r.getheader('Content-Type'), 'text/markdown; charset=utf-8')
        r, _ = get(by['page.html']['file_id'], headers={'Cookie': 'hc_token=' + quote(self.token, safe='')})
        self.assertEqual(r.status, 200)  # iframe 처럼 쿠키만으로
        self.assertIn('sandbox allow-scripts', r.getheader('Content-Security-Policy'))
        self.assertEqual(r.getheader('X-Content-Type-Options'), 'nosniff')
        r, _ = get(by['pic.png']['file_id'])
        self.assertEqual((r.getheader('Content-Type'), r.getheader('Content-Security-Policy')), ('image/png', "frame-ancestors 'self'"))
        self.assertEqual(get(by['pic.png']['file_id'], headers={})[0].status, 401)
        # 목록 밖 id, 실제 있는 비밀 파일의 해시를 맞혀도 열리지 않는다
        import hashlib
        for fid in ('0' * 20, hashlib.sha256(os.path.realpath(R / 'other.md').encode()).hexdigest()[:20], '..%2f..'):
            r, body = get(fid)
            self.assertEqual(r.status, 404, fid)
            self.assertNotIn(b'SECRET', body)
        # 목록을 받은 뒤 링크로 바꿔치기하면 막힌다
        (W / 'swap.md').unlink()
        (W / 'swap.md').symlink_to(R / 'other.md')
        r, body = get(by['swap.md']['file_id'])
        self.assertEqual(r.status, 403)
        self.assertNotIn(b'SECRET', body)

    def test_safe_file_rules(self):
        ok = lambda rel: server.safe_file(str(R / rel)) is not None  # noqa: E731
        self.assertTrue(ok('proj/report.md'))
        for rel in ('.ssh/id.md', 'proj/.hidden/notes.md', 'proj/api_key.md', 'proj/run.sh', 'proj/link.md',
                    'proj/missing.md', '../outside.md'):
            self.assertFalse(ok(rel), rel)

    def test_docs_outside_layout(self):
        import hashlib
        guess = hashlib.sha256(str(T / 'secret.txt').encode()).hexdigest()[:20]
        for p in (f'/api/docs/{guess}', '/api/docs/..%2f..%2fsecret.txt', '/api/docs/%2Fetc%2Fpasswd',
                  '/api/docs/' + '0' * 20):
            st, body = self.req('GET', p)
            self.assertEqual(st, 404, p)
            self.assertNotIn('SECRET', str(body))
        self.assertEqual(self.req('GET', '/api/layout', auth=False)[0], 401)
        self.assertEqual(self.req('GET', '/api/docs/' + guess, auth=False)[0], 401)

    def test_messages_screen_fallback(self):
        st, d = self.req('GET', '/api/terminals/mac/t3/messages?limit=10')
        self.assertEqual(st, 200)
        self.assertEqual(d['source'], 'screen')
        self.assertEqual(len(d['messages']), 1)
        self.assertEqual(d['messages'][0]['tool']['name'], 'screen')
        self.assertIn('가짜 화면 mac/t3', d['messages'][0]['text'])
        self.assertIsNone(d['cursor'])
        st, d = self.req('GET', '/api/terminals/t1/screen?lines=5')
        self.assertEqual((st, d['status']), (200, 'working'))

    def test_encoded_id_and_cookie(self):
        st, d = self.req('GET', '/api/terminals/mac%2Ft3/messages')
        self.assertEqual((st, d['source']), (200, 'screen'))
        self.assertEqual(d['messages'][0]['id'], self.req('GET', '/api/terminals/mac%2Ft3/messages')[1]['messages'][0]['id'])
        cookie = {'Cookie': 'hc_token=' + quote(self.token, safe='')}
        self.assertEqual(self.req('POST', '/api/terminals/mac%2Ft3/interrupt', {}, auth=False, headers=cookie)[0], 200)
        self.assertIn(['interrupt', '--timeout', '5', 'mac/t3'],
                      [json.loads(x) for x in (T / 'hc.log').read_text().splitlines()])

    def test_unknown_and_invalid_terminal(self):
        self.assertEqual(self.req('GET', '/api/terminals/t9/messages')[0], 404)
        self.assertEqual(self.req('GET', '/api/terminals/-rf/messages')[0], 400)
        self.assertEqual(self.req('POST', '/api/terminals/a;b/send', {'text': 'x'})[0], 400)

    def test_shell_send_forbidden(self):
        for action, body in (('send', {'text': 'ls'}), ('keys', {'keys': ['enter']}), ('interrupt', {})):
            st, d = self.req('POST', f'/api/terminals/t2/{action}', body)
            self.assertEqual((st, d['error']), (403, 'shell_blocked'))

    def test_keys_allowlist(self):
        st, d = self.req('POST', '/api/terminals/t1/keys', {'keys': ['enter', 'alt+x']})
        self.assertEqual((st, d['error']), (400, 'bad_keys'))
        st, d = self.req('POST', '/api/terminals/t1/keys', {'keys': ['esc', '1', 'ctrl+c']})
        self.assertEqual((st, d), (200, {'ok': True}))

    def test_send_limits_and_json_only(self):
        self.assertEqual(self.req('POST', '/api/terminals/t1/send', {'text': 'x' * 8001})[0], 400)
        st, _ = self.req('POST', '/api/terminals/t1/send', None, headers={'Content-Type': 'text/plain'})
        self.assertEqual(st, 415)

    def test_send_and_audit_has_no_text(self):
        secret = '-비밀 원문 SENTINEL-42'
        st, d = self.req('POST', '/api/terminals/mac/t3/send', {'text': secret})
        self.assertEqual((st, d), (200, {'ok': True}))
        calls = [json.loads(x) for x in (T / 'hc.log').read_text().splitlines()]
        self.assertIn(['send', 'mac/t3', '--', secret], calls)  # '-' 로 시작해도 '--' 뒤 본문
        audit = (T / 'logs' / 'audit.jsonl').read_text()
        self.assertNotIn('SENTINEL', audit)
        last = [json.loads(x) for x in audit.splitlines() if '"send"' in x and 'mac/t3' in x][-1]
        self.assertEqual((last['chars'], last['result']), (len(secret), 'ok'))

    def test_static_and_traversal(self):
        self.assertEqual(self.req('GET', '/', auth=False), (200, '<h1>login</h1>'))
        self.assertEqual(self.req('GET', '/app.js', auth=False), (200, '// app'))
        # 토큰 없이 데이터 파일을 달라면 로그인 화면만
        self.assertEqual(self.req('GET', '/data.json', auth=False), (200, '<h1>login</h1>'))
        self.assertEqual(self.req('GET', '/data.json')[1], {'private': 1})
        for p in ('/../secret.txt', '/%2e%2e/secret.txt', '/..%2fsecret.txt', '/%2e%2e%2f%2e%2e%2fetc/passwd'):
            st, body = self.req('GET', p)
            self.assertNotIn('SECRET', str(body), p)
            self.assertEqual(st, 404, p)

    def test_sse_first_event(self):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        self.assertEqual(self.req('GET', '/api/events', auth=False)[0], 401)
        # EventSource 는 헤더를 못 붙이므로 쿠키만으로 접속한다.
        c.request('GET', '/api/events', headers={'Cookie': 'hc_token=' + quote(self.token, safe='')})
        r = c.getresponse()
        self.assertEqual(r.status, 200)
        self.assertTrue(r.getheader('Content-Type').startswith('text/event-stream'))
        event = data = None
        while data is None:
            line = r.fp.readline().decode().rstrip('\n')
            if line.startswith('event: '):
                event = line[7:]
            elif line.startswith('data: '):
                data = json.loads(line[6:])
        c.close()
        self.assertEqual(event, 'terminal')
        self.assertEqual((data['id'], data['status']), ('t1', 'blocked'))


if __name__ == '__main__':
    unittest.main()


class ModeTest(unittest.TestCase):
    """권한 모드: 화면 읽기와 shift+tab 돌리기(실측 화면 줄 그대로)."""
    BOX = '─' * 20 + '\n❯ \n' + '─' * 20 + '\n'

    def test_claude_footer(self):
        self.assertEqual(server.claude_mode_of(self.BOX + '  ⏵⏵ auto mode on (shift+tab to cycle) · ← for agents'), 'auto')
        self.assertEqual(server.claude_mode_of(self.BOX + '  ⏸ manual mode on · ← for agents'), 'manual')
        self.assertEqual(server.claude_mode_of(self.BOX + '  ⏵⏵ accept edits on (shift+tab to cycle)'), 'accept-edits')
        self.assertIsNone(server.claude_mode_of('plan mode on 이라는 말이 본문에\n' + 'x\n' * 5 + self.BOX + '  ? for shortcuts'))

    def test_codex_picker(self):
        screen = ('  Update Model Permissions\n'
                  '  1. Ask for approval     Read and edit workspace files\n'
                  '  2. Approve for me       Only ask for actions detected as potentially unsafe\n'
                  '  3. Full Access          Use with caution\n'
                  '› 4. Read Only (current)  Read workspace files\n  enter select · esc back')
        rows, cur = server.codex_picker_of(screen)
        self.assertEqual((rows['Full Access'], rows['Read Only'], cur), ('3', '4', 'Read Only'))
        self.assertEqual(server.codex_picker_of('› Ask Codex to do anything'), ({}, None))

    def _cycle(self, start, available):
        """가짜 터미널: shift+tab 마다 available 안에서 다음 모드로."""
        texts = {m: t for m, _, t, _ in server.MODES['claude-code']}
        state = {'i': available.index(start), 'presses': 0}
        app = server.App.__new__(server.App)
        app.read_screen_fresh = lambda tid: self.BOX + f'  ⏵⏵ {texts[available[state["i"]]]} on'

        def keys(tid, names):
            state['presses'] += 1
            state['i'] = (state['i'] + 1) % len(available)
        orig = server.hc_client.keys
        server.hc_client.keys = keys
        self.addCleanup(setattr, server.hc_client, 'keys', orig)
        return app, state

    def test_claude_cycle_reaches_target(self):
        app, state = self._cycle('auto', ['auto', 'manual', 'accept-edits', 'plan'])
        self.assertEqual(app._set_claude_mode('t1', 'plan'), 'plan')
        self.assertEqual(state['presses'], 3)

    def test_claude_cycle_missing_mode(self):
        app, state = self._cycle('manual', ['auto', 'manual', 'accept-edits', 'plan'])
        with self.assertRaises(server.HcError) as e:
            app._set_claude_mode('t1', 'bypass')
        self.assertEqual(e.exception.code, 'mode_unavailable')
        self.assertEqual(state['presses'], 4)  # 한 바퀴 돌고 처음 모드로 돌아와 멈춤
