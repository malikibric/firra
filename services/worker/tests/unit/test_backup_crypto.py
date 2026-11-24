"""Backup envelope: round trip, wrong phrase, tamper, parameter bounds."""

import base64
import json

import pytest

from firra.backup.crypto import BackupDecryptError, decrypt, encrypt, new_recovery_phrase


def test_round_trip_and_phrase_shape() -> None:
    phrase = new_recovery_phrase()
    assert len(phrase.replace("-", "")) == 24 and phrase == phrase.upper()
    blob = encrypt({"clients": [{"name": "Ünïcode"}]}, phrase)
    assert decrypt(blob, phrase) == {"clients": [{"name": "Ünïcode"}]}
    assert decrypt(blob, phrase.lower().replace("-", " ")) == {"clients": [{"name": "Ünïcode"}]}
    assert b"Unicode" not in blob and "Ünïcode".encode() not in blob


def test_wrong_phrase_and_tamper_are_refused() -> None:
    blob = encrypt({"a": 1}, "AAAA-BBBB")
    with pytest.raises(BackupDecryptError):
        decrypt(blob, "AAAA-BBBC")
    envelope = json.loads(blob)
    envelope["n"] = 2**14  # a changed parameter is part of the authenticated header
    with pytest.raises(BackupDecryptError):
        decrypt(json.dumps(envelope).encode(), "AAAA-BBBB")
    envelope = json.loads(blob)
    envelope["ct"] = base64.b64encode(b"x" * 40).decode()
    with pytest.raises(BackupDecryptError):
        decrypt(json.dumps(envelope).encode(), "AAAA-BBBB")


@pytest.mark.parametrize(
    "garbage", [b"", b"not json", b"[]", b'{"v": 2}', b'{"v":1,"kdf":"scrypt"}']
)
def test_garbage_is_refused(garbage: bytes) -> None:
    with pytest.raises(BackupDecryptError):
        decrypt(garbage, "AAAA")


def test_work_factor_is_bounded() -> None:
    envelope = json.loads(encrypt({}, "AAAA"))
    envelope["n"] = 2**24
    with pytest.raises(BackupDecryptError):
        decrypt(json.dumps(envelope).encode(), "AAAA")
