"""Allowlisted `hc` calls as list-argv subprocesses, never shell=True. HC_BIN swaps the binary for tests."""
import json
import os
import re
import subprocess

TERMINAL_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]*(/[A-Za-z0-9][A-Za-z0-9_-]*)?')
ALLOWED = {'ls', 'get', 'read', 'send', 'keys', 'interrupt', 'events', 'connect', 'machine', 'put', 'run', 'exec'}
MACHINE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,60}')
MAX_OUTPUT = 4_000_000


class HcError(Exception):
    def __init__(self, code, message, status=503):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def binary():
    return os.environ.get('HC_BIN', 'hc')


def _env():
    # 서버를 herdr 터미널 안에서 켜도 그 터미널 이름으로 보내지 않게 한다(<hc from=…> 감싸기 방지).
    return {k: v for k, v in os.environ.items() if k not in ('HC_TERMINAL_ID', 'HC_TERMINAL_SOCKET')}


def terminal_id(value):
    # 첫 글자를 영숫자로 묶어 '-x' 같은 id 가 hc 옵션으로 읽히지 않게 한다.
    if not isinstance(value, str) or len(value) > 120 or not TERMINAL_ID.fullmatch(value):
        raise HcError('invalid_terminal', '터미널 주소 형식이 올바르지 않습니다.', 400)
    return value


def _call(args, timeout=10):
    if args[0] not in ALLOWED:
        raise HcError('hc_not_allowed', '허용되지 않은 hc 명령입니다.', 500)
    try:
        result = subprocess.run([binary(), *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout, check=False, env=_env())
    except subprocess.TimeoutExpired:
        raise HcError('hc_timeout', 'herdr 응답이 늦습니다.') from None
    except OSError:
        raise HcError('hc_unavailable', 'hc 를 실행할 수 없습니다.') from None
    if len(result.stdout) > MAX_OUTPUT:
        raise HcError('hc_response_too_large', 'herdr 응답이 너무 큽니다.')
    try:
        data = json.loads(result.stdout or b'null')
    except (ValueError, UnicodeError):
        raise HcError('hc_invalid_response', 'herdr 응답을 읽을 수 없습니다.') from None
    if result.returncode or (isinstance(data, dict) and isinstance(data.get('error'), dict)):
        err = (data or {}).get('error') if isinstance(data, dict) else None
        kind = (err or {}).get('kind') or 'hc_failed'
        status = 404 if kind == 'not_found' else 409
        raise HcError(f'hc_{kind}', (err or {}).get('message') or 'herdr 명령이 실패했습니다.', status)
    return data


def ls():
    return _call(['ls', '--json']).get('terminals') or []


def get(tid):
    return _call(['get', '--json', terminal_id(tid)])


def read(tid, lines=60):
    # 실측: hc read 는 2~3초 걸린다.
    return _call(['read', '--json', '--lines', str(int(lines)), terminal_id(tid)], timeout=20)


def send(tid, text, force=False):
    # '--' 뒤라서 '-' 로 시작하는 글도 옵션이 아닌 본문으로 들어간다(실측 확인).
    # force: 질문 창이 떠 있어도(blocked) 입력한다. 질문의 '직접 입력' 칸에 답할 때만 쓴다.
    return _call(['send', '--json', *(['--force'] if force else []), terminal_id(tid), '--', text], timeout=40)


def keys(tid, names):
    return _call(['keys', '--json', terminal_id(tid), *names], timeout=15)


def put(tid, path):
    # 실측 출력: {"bytes": 3, "path": "~/.local/state/herdr-core/default/files/t94/____1_.jpg"}
    return _call(['put', '--json', terminal_id(tid), path], timeout=60)


def interrupt(tid):
    return _call(['interrupt', '--json', '--timeout', '5', terminal_id(tid)], timeout=20)


def machines():
    return _call(['machine', 'ls', '--json']).get('machines') or []


def _machine(on):
    if on is not None and (not isinstance(on, str) or not MACHINE.fullmatch(on)):
        raise HcError('invalid_machine', '기기 이름 형식이 올바르지 않습니다.', 400)
    return on


def rpc(method, params, timeout=10):
    """한 번의 `hc connect` 로 hello 뒤 method 하나를 부른다. params 는 JSON 으로만 넣는다."""
    req = ('{"jsonrpc":"2.0","id":1,"method":"hello","params":{}}\n'
           + json.dumps({'jsonrpc': '2.0', 'id': 2, 'method': method, 'params': params}) + '\n')
    try:
        result = subprocess.run([binary(), 'connect', '--json'], input=req.encode(), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout, check=False, env=_env())
    except subprocess.TimeoutExpired:
        raise HcError('hc_timeout', 'herdr 응답이 늦습니다.') from None
    except OSError:
        raise HcError('hc_unavailable', 'hc 를 실행할 수 없습니다.') from None
    if len(result.stdout) > MAX_OUTPUT:
        raise HcError('hc_response_too_large', 'herdr 응답이 너무 큽니다.')
    for line in result.stdout.splitlines():
        try:
            msg = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if str(msg.get('id')) == '2':  # 실측: 성공은 "2", 오류는 2 로 돌아온다
            if isinstance(msg.get('error'), dict):
                kind = (msg['error'].get('data') or {}).get('kind') or 'failed'
                raise HcError(kind, msg['error'].get('message') or 'herdr 명령이 실패했습니다.', 409)
            return msg.get('result')
    raise HcError('hc_invalid_response', 'herdr 응답을 읽을 수 없습니다.')


def doc_get(on=None, timeout=10):
    """herdr 의 공간·탭 문서(doc.get herdr-app): {'value': {...}, 'version': n}."""
    params = {'name': 'herdr-app'}
    if _machine(on) is not None:
        params['on'] = on
    res = rpc('doc.get', params, timeout) or {}
    if not isinstance(res.get('value'), dict):
        raise HcError('hc_layout_unavailable', 'herdr 공간·탭 구성을 읽을 수 없습니다.')
    return res


def doc_set(value, version, on=None):
    """버전이 그대로일 때만 쓴다. 다르면 HcError(409)."""
    params = {'name': 'herdr-app', 'value': value, 'version': version}
    if _machine(on) is not None:
        params['on'] = on
    return rpc('doc.set', params, 15)


def layout(on=None, timeout=10):
    return doc_get(on, timeout)['value']


def exec_(argv, on, timeout=15):
    """다른 기기에서 프로그램 하나(셸 없이). (종료 코드, 표준 출력 글자)."""
    import base64
    r = _call(['exec', '--json', '--on', _machine(on), '--timeout', str(timeout), '--', *argv], timeout=timeout + 5)
    return r.get('code'), base64.b64decode(r.get('stdout') or '').decode('utf-8', 'replace')


def run(argv, cwd=None, labels=(), on=None):
    """새 터미널에서 프로그램을 띄우고 곧바로 돌아온다(--bg). 부모 없이(이 서버가 끝나도 남게)."""
    args = ['run', '--json', '--bg', '--no-parent']
    if _machine(on) is not None:
        args += ['--on', on]
    if cwd:
        args += ['--cwd', cwd]
    for k, v in labels:
        args += ['--label', f'{k}={v}']
    return _call(args + ['--', *argv], timeout=30)


def events():
    """Long-lived `hc events --json`; the caller reads stdout lines and must kill it."""
    return subprocess.Popen([binary(), 'events', '--json'], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace', env=_env())
