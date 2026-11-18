"""다른 기기(MacBook) 터미널의 대화 기록: mapping.py·transcripts.py 를 그 기기의 python3 에서 그대로 돌린다.

`hc exec -i --on <기기> python3 -I -` 로 스크립트를 넘긴다. 인자는 스크립트 안의 JSON 문자열로만 들어가고,
읽기만 한다(기록 파일·DB 를 쓰지 않음). 실측: 왕복 약 0.2초.
"""
import json
import os
import subprocess

import hc_client
from hc_client import HcError

HERE = os.path.dirname(os.path.abspath(__file__))
TIMEOUT_S = 25

RUNNER = '''
import json, sys, types
def _load(name, src):
    m = types.ModuleType(name)
    sys.modules[name] = m
    exec(compile(src, name + ".py", "exec"), m.__dict__)
    return m
mapping = _load("mapping", SRC["mapping"])
transcripts = _load("transcripts", SRC["transcripts"])

def resolve_all(facts_by_addr):
    out = {}
    for addr, facts in facts_by_addr.items():
        try:
            out[addr] = mapping.resolve(facts)
        except Exception:
            out[addr] = None
    return out

def messages(kind, path, before, limit):
    msgs, cursor = transcripts.read_messages(kind, path, before=before, limit=limit)
    return {"messages": msgs, "cursor": cursor, "mtime": transcripts.last_change(path)}

def mtimes(paths):
    out = {}
    for p in paths:
        try:
            out[p] = transcripts.last_change(p)
        except Exception:
            out[p] = None
    return out

OPS = {"resolve_all": resolve_all, "messages": messages, "mtimes": mtimes}
sys.stdout.write(json.dumps(OPS[OP](**ARGS), ensure_ascii=False))
'''


def _source():
    src = {}
    for name in ('mapping', 'transcripts'):
        with open(os.path.join(HERE, name + '.py'), encoding='utf-8') as f:
            src[name] = f.read()
    return src


def call(machine, op, **args):
    if not isinstance(machine, str) or not hc_client.MACHINE.fullmatch(machine):
        raise HcError('invalid_machine', '기기 이름 형식이 올바르지 않습니다.', 400)
    # 값은 모두 JSON 문자열 리터럴로만 넣는다(코드로 해석되지 않음).
    script = (f'SRC = json.loads({json.dumps(json.dumps(_source()))})\n'
              f'OP = {json.dumps(op)}\n'
              f'ARGS = json.loads({json.dumps(json.dumps(args))})\n' + RUNNER)
    script = 'import json\n' + script
    try:
        r = subprocess.run([hc_client.binary(), 'exec', '-i', '--on', machine, '--timeout', str(TIMEOUT_S),
                            'python3', '-I', '-'], input=script.encode(), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=TIMEOUT_S + 5, check=False, env=hc_client._env())
    except subprocess.TimeoutExpired:
        raise HcError('hc_timeout', '다른 기기 응답이 늦습니다.') from None
    except OSError:
        raise HcError('hc_unavailable', 'hc 를 실행할 수 없습니다.') from None
    if r.returncode or len(r.stdout) > hc_client.MAX_OUTPUT:
        raise HcError('remote_failed', '다른 기기에서 기록을 읽지 못했습니다.')
    try:
        return json.loads(r.stdout)
    except ValueError:
        raise HcError('remote_failed', '다른 기기에서 기록을 읽지 못했습니다.') from None
