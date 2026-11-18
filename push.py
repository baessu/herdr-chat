"""Web Push without pywebpush: RFC 8291 (aes128gcm) payload encryption + RFC 8292 VAPID, on `cryptography`."""
import base64
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# VAPID 연락처(푸시 서비스가 문제 시 연락할 주소). 자기 주소로 바꿔 두는 것을 권한다.
SUB = os.environ.get('HERDR_CHAT_PUSH_CONTACT', 'mailto:noreply@example.com')
RATE_S = 60
MAX_SUBS = 50
# SSRF 방지: 알려진 푸시 서비스로만 보낸다.
ALLOWED_HOSTS = ('.push.apple.com', '.push.services.mozilla.com')
ALLOWED_EXACT = ('fcm.googleapis.com',)


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def unb64u(text):
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def _hkdf(salt, ikm, info, length):
    # 길이가 32 이하라 HKDF-Expand 는 한 블록이면 된다.
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b'\x01', hashlib.sha256).digest()[:length]


def _raw_public(key):
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def encrypt(plaintext, ua_public, auth_secret, as_private=None, salt=None):
    """RFC 8291 §3·§4: 한 레코드짜리 aes128gcm 본문. as_private·salt 는 시험 벡터용."""
    as_private = as_private or ec.generate_private_key(ec.SECP256R1())
    salt = salt or os.urandom(16)
    as_public = _raw_public(as_private)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    ecdh = as_private.exchange(ec.ECDH(), ua_key)
    ikm = _hkdf(auth_secret, ecdh, b'WebPush: info\x00' + ua_public + as_public, 32)
    cek = _hkdf(salt, ikm, b'Content-Encoding: aes128gcm\x00', 16)
    nonce = _hkdf(salt, ikm, b'Content-Encoding: nonce\x00', 12)
    body = AESGCM(cek).encrypt(nonce, plaintext + b'\x02', None)  # 0x02: 마지막 레코드
    return salt + (4096).to_bytes(4, 'big') + bytes([len(as_public)]) + as_public + body


def endpoint_ok(endpoint):
    if not isinstance(endpoint, str) or len(endpoint) > 1000:
        return False
    u = urlsplit(endpoint)
    host = (u.hostname or '').lower()
    return (u.scheme == 'https' and not u.username and not u.password and u.port in (None, 443)
            and (host in ALLOWED_EXACT or host.endswith(ALLOWED_HOSTS)))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # 리다이렉트로 다른 곳에 보내지 않는다
        return None


_opener = urllib.request.build_opener(_NoRedirect)


class Pusher:
    def __init__(self, config_dir, on_result=None, post=None):
        self.dir = Path(config_dir)
        self.file = self.dir / 'push.json'
        self.lock = threading.Lock()
        self.on_result = on_result or (lambda tid, code: None)
        self.post = post or self._post  # 시험에서 바꿔 끼운다
        self.last = {}  # 터미널 id -> 마지막 보낸 시각
        self.key = self._load_key()
        self.public_key = b64u(_raw_public(self.key))

    # --- 파일 ---
    def _write_private(self, path, data):
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = path.with_suffix('.tmp')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        os.replace(tmp, path)

    def _load_key(self):
        path = self.dir / 'vapid_private.pem'
        if path.exists():
            return serialization.load_pem_private_key(path.read_bytes(), None)
        key = ec.generate_private_key(ec.SECP256R1())
        self._write_private(path, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                    serialization.NoEncryption()))
        return key

    def subs(self):
        try:
            return json.loads(self.file.read_text())
        except (FileNotFoundError, ValueError):
            return []

    def _save(self, subs):
        self._write_private(self.file, json.dumps(subs, ensure_ascii=False).encode())

    # --- 구독 ---
    def subscribe(self, sub, prefs):
        endpoint = (sub or {}).get('endpoint')
        keys = (sub or {}).get('keys') or {}
        if not endpoint_ok(endpoint):
            raise ValueError('endpoint_not_allowed')
        try:
            p256dh, auth = unb64u(keys['p256dh']), unb64u(keys['auth'])
        except (KeyError, TypeError, ValueError):
            raise ValueError('bad_keys') from None
        if len(p256dh) != 65 or p256dh[0] != 4 or len(auth) != 16:
            raise ValueError('bad_keys')
        prefs = {'blocked': bool((prefs or {}).get('blocked', True)), 'done': bool((prefs or {}).get('done', False))}
        entry = {'endpoint': endpoint, 'keys': {'p256dh': keys['p256dh'], 'auth': keys['auth']}, 'prefs': prefs,
                 'created_ms': int(time.time() * 1000)}
        with self.lock:
            subs = [s for s in self.subs() if s['endpoint'] != endpoint]
            if len(subs) >= MAX_SUBS:
                raise ValueError('too_many')
            self._save(subs + [entry])
        return prefs

    def unsubscribe(self, endpoint):
        with self.lock:
            subs = self.subs()
            left = [s for s in subs if s['endpoint'] != endpoint]
            if len(left) != len(subs):
                self._save(left)
            return len(subs) - len(left)

    # --- 보내기 ---
    def vapid(self, endpoint, now=None):
        u = urlsplit(endpoint)
        claims = {'aud': f'{u.scheme}://{u.netloc}', 'exp': int(now or time.time()) + 12 * 3600, 'sub': SUB}
        signing = (b64u(json.dumps({'typ': 'JWT', 'alg': 'ES256'}).encode()) + '.'
                   + b64u(json.dumps(claims).encode()))
        r, s = decode_dss_signature(self.key.sign(signing.encode(), ec.ECDSA(hashes.SHA256())))
        jwt = signing + '.' + b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))
        return f'vapid t={jwt}, k={self.public_key}'

    def _post(self, endpoint, body, headers):
        req = urllib.request.Request(endpoint, data=body, headers=headers, method='POST')
        try:
            with _opener.open(req, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def notify(self, tid, kind, message):
        """kind 는 'blocked' 또는 'done'. 같은 터미널은 60초에 한 번. 실제 전송은 백그라운드."""
        now = time.time()
        with self.lock:
            if now - self.last.get(tid, 0) < RATE_S:
                return False
            self.last[tid] = now
        threading.Thread(target=self._send_all, args=(tid, kind, message), daemon=True).start()
        return True

    def _send_all(self, tid, kind, message):
        payload = json.dumps(message, ensure_ascii=False).encode()[:3000]
        gone = []
        for s in self.subs():
            if not s.get('prefs', {}).get(kind) or not endpoint_ok(s.get('endpoint')):
                continue
            try:
                body = encrypt(payload, unb64u(s['keys']['p256dh']), unb64u(s['keys']['auth']))
                code = self.post(s['endpoint'], body, {
                    'Content-Encoding': 'aes128gcm', 'Content-Type': 'application/octet-stream',
                    'TTL': '3600', 'Urgency': 'high', 'Authorization': self.vapid(s['endpoint'])})
            except Exception:  # noqa: BLE001 - 보내기 실패가 서버를 멈추지 않게
                code = 0
            self.on_result(tid, code)
            if code in (404, 410):  # 구독이 끝났다
                gone.append(s['endpoint'])
        for endpoint in gone:
            self.unsubscribe(endpoint)
