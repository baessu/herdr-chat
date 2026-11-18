"""Web Push tests (needs .venv cryptography). Run: .venv/bin/python -m unittest discover -s tests"""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    import push
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    push = None

d = lambda s: push.unb64u(s)  # noqa: E731


def receive(body, ua_private, auth):
    """수신 브라우저 흉내: RFC 8291 대로 풀어 원문을 돌려준다."""
    salt, rs, idlen = body[:16], int.from_bytes(body[16:20], 'big'), body[20]
    as_public, ct = body[21:21 + idlen], body[21 + idlen:]
    ua_public = ua_private.public_key().public_bytes(serialization.Encoding.X962,
                                                     serialization.PublicFormat.UncompressedPoint)
    ecdh = ua_private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    ikm = push._hkdf(auth, ecdh, b'WebPush: info\x00' + ua_public + as_public, 32)
    cek = push._hkdf(salt, ikm, b'Content-Encoding: aes128gcm\x00', 16)
    nonce = push._hkdf(salt, ikm, b'Content-Encoding: nonce\x00', 12)
    plain = AESGCM(cek).decrypt(nonce, ct, None)
    assert rs == 4096 and plain.endswith(b'\x02')
    return plain[:-1]


def subscription(endpoint='https://web.push.apple.com/QGuQ'):
    ua = ec.generate_private_key(ec.SECP256R1())
    auth = b'0123456789abcdef'
    pub = ua.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return ua, auth, {'endpoint': endpoint, 'keys': {'p256dh': push.b64u(pub), 'auth': push.b64u(auth)}}


@unittest.skipIf(push is None, 'cryptography 없음: .venv/bin/python 으로 실행')
class PushTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sent, self.results, self.code = [], [], 201
        self.p = push.Pusher(self.tmp.name, on_result=lambda t, c: self.results.append((t, c)),
                             post=lambda e, b, h: self.sent.append((e, b, h)) or self.code)

    def tearDown(self):
        self.tmp.cleanup()

    def wait(self, n):
        for _ in range(50):
            if len(self.results) >= n:
                return
            time.sleep(0.02)

    def test_rfc8291_vector(self):
        # RFC 8291 Appendix A 와 바이트 단위로 같아야 한다.
        asp = ec.derive_private_key(int.from_bytes(d('yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw'), 'big'),
                                    ec.SECP256R1())
        out = push.encrypt(b'When I grow up, I want to be a watermelon',
                           d('BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4'),
                           d('BTBZMqHH6r4Tts7J_aSIgg'), asp, d('DGv6ra1nlYgDCS1FRnbzlw'))
        self.assertEqual(push.b64u(out), 'DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6'
                         'e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLV'
                         'WGNWQexSgSxsj_Qulcy4a-fN')

    def test_encrypt_roundtrip(self):
        ua, auth, sub = subscription()
        msg = '{"title": "답을 기다리는 중"}'.encode()
        self.assertEqual(receive(push.encrypt(msg, d(sub['keys']['p256dh']), auth), ua, auth), msg)

    def test_vapid_jwt(self):
        header = self.p.vapid('https://web.push.apple.com/abc/def', now=1000)
        t, k = header[len('vapid t='):].split(', k=')
        self.assertEqual(k, self.p.public_key)
        h, c, sig = t.split('.')
        self.assertEqual(json.loads(d(h)), {'typ': 'JWT', 'alg': 'ES256'})
        self.assertEqual(json.loads(d(c)), {'aud': 'https://web.push.apple.com', 'exp': 1000 + 12 * 3600,
                                            'sub': push.SUB})
        raw = d(sig)
        pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), d(k))
        der = encode_dss_signature(int.from_bytes(raw[:32], 'big'), int.from_bytes(raw[32:], 'big'))
        pub.verify(der, f'{h}.{c}'.encode(), ec.ECDSA(hashes.SHA256()))  # 실패하면 예외

    def test_key_files_private_and_stable(self):
        pem = Path(self.tmp.name) / 'vapid_private.pem'
        self.assertEqual(pem.stat().st_mode & 0o777, 0o600)
        self.assertEqual(push.Pusher(self.tmp.name).public_key, self.p.public_key)

    def test_endpoint_allowlist(self):
        ok = ['https://web.push.apple.com/x', 'https://fcm.googleapis.com/fcm/send/x',
              'https://updates.push.services.mozilla.com/wpush/v2/x']
        bad = ['http://web.push.apple.com/x', 'https://evil.com/x', 'https://push.apple.com.evil.com/x',
               'https://user@web.push.apple.com/x', 'https://web.push.apple.com:8443/x', 'https://127.0.0.1/x',
               'https://fcm.googleapis.com.evil.com/x', None, 'file:///etc/passwd']
        for e in ok:
            self.assertTrue(push.endpoint_ok(e), e)
        for e in bad:
            self.assertFalse(push.endpoint_ok(e), e)
            with self.assertRaises(ValueError):
                self.p.subscribe(dict(subscription()[2], endpoint=e), {})

    def test_send_decrypts_and_410_removes(self):
        ua, auth, sub = subscription()
        self.p.subscribe(sub, {'blocked': True})
        self.assertEqual((Path(self.tmp.name) / 'push.json').stat().st_mode & 0o777, 0o600)
        msg = {'title': '답을 기다리는 중', 'body': 'iMac · 내 프로젝트 / 출시', 'id': 't1', 'tag': 't1'}
        self.assertTrue(self.p.notify('t1', 'blocked', msg))
        self.wait(1)
        endpoint, body, headers = self.sent[0]
        self.assertEqual(json.loads(receive(body, ua, auth)), msg)
        self.assertEqual((headers['Content-Encoding'], headers['Urgency'], headers['TTL']), ('aes128gcm', 'high', '3600'))
        self.assertTrue(headers['Authorization'].startswith('vapid t='))
        self.assertEqual(self.results, [('t1', 201)])
        self.code = 410
        self.assertTrue(self.p.notify('t2', 'blocked', msg))
        self.wait(2)
        time.sleep(0.05)
        self.assertEqual(self.p.subs(), [])  # 410 이면 구독을 지운다

    def test_rate_limit_and_prefs(self):
        _, _, sub = subscription()
        self.p.subscribe(sub, {'blocked': True, 'done': False})
        self.assertTrue(self.p.notify('t1', 'blocked', {}))
        self.assertFalse(self.p.notify('t1', 'blocked', {}))  # 같은 터미널 60초 안
        self.p.last['t1'] -= 61
        self.assertTrue(self.p.notify('t1', 'done', {}))       # done 은 꺼져 있어 보내지 않는다
        time.sleep(0.2)
        self.assertEqual(len(self.sent), 1)


if __name__ == '__main__':
    unittest.main()
