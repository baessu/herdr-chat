#!/usr/bin/env python3
"""Fake `hc` for tests: fixed JSON answers; send/keys/interrupt append argv to $FAKE_HC_LOG."""
import json
import os
import sys
import time

TERMS = [
    {'address': 't1', 'agent': 'claude-code', 'cwd': os.environ.get('FAKE_CWD', '/tmp'), 'state': 'live', 'status': 'working', 'turn': 3,
     'where': '시험 / 클로드'},
    {'address': 't2', 'agent': 'zsh', 'cwd': '/tmp', 'state': 'live', 'turn': 0, 'where': '시험 / 셸 / 하위 · 탭'},
    {'address': 'mac/t3', 'agent': 'codex', 'cwd': '/tmp', 'state': 'live', 'status': 'blocked', 'turn': 1},
]


def facts(t):
    return {'address': t['address'], 'pid': 1000 + len(t['address']), 'title': f"제목 {t['address']}",
            'state': 'live', 'status': {'state': t['status']} if 'status' in t else None,
            'status_ms': 1794656252418, 'turn': t['turn'], 'foreground': None,
            'labels': {'herdr.space': 'sp1', 'herdr.tab': 'tb1'} if t['address'] == 't1' else {}}


def out(data, code=0):
    print(json.dumps(data, ensure_ascii=False), flush=True)
    sys.exit(code)


def find(addr):
    for t in TERMS:
        if t['address'] == addr:
            return t
    out({'error': {'kind': 'not_found', 'message': f'no terminal "{addr}"'}}, 3)


def main(argv):
    cmd, rest = argv[0], [a for a in argv[1:] if a != '--json']
    if cmd == 'ls':
        with open(os.environ['FAKE_HC_LOG'], 'a', encoding='utf-8') as f:
            f.write(json.dumps([cmd]) + '\n')
        out({'terminals': TERMS, 'machines': [], 'hidden_finished': 0})
    if cmd == 'get':
        out(facts(find(rest[-1])))
    if cmd == 'machine':
        out({'machines': [{'name': 'mac', 'state': 'connected'},
                          {'name': 'macbook-pro', 'state': 'unreachable', 'why': '자는 중', 'since_ms': 1}]})
    if cmd == 'read':
        with open(os.environ['FAKE_HC_LOG'], 'a', encoding='utf-8') as f:
            f.write(json.dumps([cmd, *rest], ensure_ascii=False) + '\n')
        out({'complete': True, 'how': 'screen', 'text': f'가짜 화면 {rest[-1]}'})
    if cmd in ('send', 'keys', 'interrupt'):
        with open(os.environ['FAKE_HC_LOG'], 'a', encoding='utf-8') as f:
            f.write(json.dumps([cmd, *rest], ensure_ascii=False) + '\n')
        out({'ok': True})
    if cmd == 'connect':
        d = os.environ.get('FAKE_DOC_DIR', '/nonexistent')
        doc = lambda f: {'type': 'block', 'content': {'mod': 'md-viewer', 'props': {'file': f'{d}/{f}'}}}  # noqa: E731
        layout = {'spaces': [
            {'id': 'sp1', 'name': '', 'folder': '/Users/x/me', 'tabs': [
                {'id': 'tb1', 'name': '첫 탭', 'root': {'type': 'row', 'children': [
                    {'type': 'block', 'content': {'terminal': 't1'}},
                    {'type': 'col', 'children': [doc('ok.md'), {'type': 'block', 'content': {'mod': 'web-viewer'}}]}]}},
                {'id': 'tb2', 'name': '문서들', 'root': {'type': 'row', 'children': [
                    doc('script.sh'), doc('link.md'), doc('big.md'), doc('swap.md'), doc('gone.md'),
                    {'type': 'block', 'content': {'mod': 'md-viewer', 'props': {'file': 'relative.md'}}}]}}]},
            {'id': 'sp2', 'name': '내 프로젝트', 'folder': '/p/my-project', 'tabs': []}]}
        remote = {'spaces': [{'id': 'spm', 'name': '맥', 'folder': '/m', 'tabs': [
            {'id': 'tbm', 'name': '원격 탭', 'root': {'type': 'row', 'children': [
                {'type': 'block', 'content': {'terminal': 't3'}},
                {'type': 'block', 'content': {'mod': 'md-viewer', 'props': {'file': f'{d}/ok.md'}}}]}}]}]}
        for line in sys.stdin:
            req = json.loads(line)
            on = (req.get('params') or {}).get('on')
            if req['method'] == 'doc.get' and on and on != 'mac':
                print(json.dumps({'jsonrpc': '2.0', 'id': req['id'], 'error': {
                    'code': -32000, 'data': {'kind': 'unavailable'}, 'message': f'{on} is unreachable'}}), flush=True)
                continue
            res = {'value': remote if on else layout} if req['method'] == 'doc.get' else {'core': {}}
            print(json.dumps({'jsonrpc': '2.0', 'id': str(req['id']), 'result': res}), flush=True)
        sys.exit(0)
    if cmd == 'put':
        tid, path = rest
        with open(path, 'rb') as src, open(os.environ['FAKE_HC_LOG'], 'a', encoding='utf-8') as f:
            f.write(json.dumps(['put', tid, os.path.basename(path), src.read().decode('utf-8', 'replace'),
                                oct(os.stat(os.path.dirname(path)).st_mode & 0o777)], ensure_ascii=False) + '\n')
        out({'bytes': os.path.getsize(path), 'path': f'/fake/files/{tid}/{os.path.basename(path)}'})
    if cmd == 'events':
        print(json.dumps({'channel': None, 'seq': 1, 'terminals': [facts(t) for t in TERMS]}), flush=True)
        for turn in range(4, 200):  # 접속 시점과 상관없이 받도록 계속 바뀐다
            time.sleep(0.3)
            changed = dict(facts(TERMS[0]), status={'state': 'blocked'}, turn=turn)
            print(json.dumps({'jsonrpc': '2.0', 'method': 'event',
                              'params': {'data': changed, 'seq': turn, 'terminal': 't1', 'type': 'status'}}),
                  flush=True)
        sys.exit(0)
    out({'error': {'kind': 'usage', 'message': 'fake'}}, 2)


if __name__ == '__main__':
    main(sys.argv[1:])
