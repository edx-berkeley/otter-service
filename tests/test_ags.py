"""Unit tests for otter_service.ags (LTI 1.3 AGS grade-passback).

Coverage:
- is_lti13_metadata: detection heuristic
- _load_private_key_pem: env var path resolution + error cases
- _sign_client_assertion: produces a JWT verifiable with the matching public key

Network-level tests (token exchange, score POST) are deliberately out of
scope here — those need a real platform endpoint and live as integration
tests against Saltire (see tools/lti13-poc/post_score.py in the memory repo).
"""
from __future__ import annotations

import time
from pathlib import Path

import jwt
import pytest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend

import otter_service.ags as ags


# ---------------------- fixtures ----------------------


@pytest.fixture
def rsa_keypair():
    """Generate a throwaway RSA-2048 keypair as PEM strings (priv, pub)."""
    key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )
    priv_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    pub_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return priv_pem, pub_pem


@pytest.fixture
def private_key_pem_file(tmp_path, rsa_keypair):
    """Write the private key to a temp file and return its path."""
    priv_pem, _ = rsa_keypair
    p = tmp_path / "tool_private.pem"
    p.write_text(priv_pem)
    return p


# ---------------------- is_lti13_metadata ----------------------


def test_is_lti13_metadata_true_when_both_fields_present():
    md = {
        "userid": "29123",
        "lti13_lineitem": "https://platform.example/lineitem/1",
        "lti13_token_url": "https://platform.example/token",
    }
    assert ags.is_lti13_metadata(md) is True


def test_is_lti13_metadata_false_lti11_shape():
    md = {"userid": "abc", "course": "88e", "section": "1", "assignment": "lab01"}
    assert ags.is_lti13_metadata(md) is False


def test_is_lti13_metadata_false_when_lineitem_missing():
    md = {"lti13_token_url": "https://platform.example/token"}
    assert ags.is_lti13_metadata(md) is False


def test_is_lti13_metadata_false_when_token_url_missing():
    md = {"lti13_lineitem": "https://platform.example/lineitem/1"}
    assert ags.is_lti13_metadata(md) is False


def test_is_lti13_metadata_false_when_empty_strings():
    md = {"lti13_lineitem": "", "lti13_token_url": ""}
    assert ags.is_lti13_metadata(md) is False


# ---------------------- _load_private_key_pem ----------------------


def test_load_private_key_pem_prefers_env_var(monkeypatch, rsa_keypair, private_key_pem_file):
    priv_pem, _ = rsa_keypair
    # Both env vars set — env var wins, file path is ignored.
    monkeypatch.setenv("LTI13_PRIVATE_KEY", priv_pem)
    monkeypatch.setenv("LTI13_PRIVATE_KEY_PATH", str(private_key_pem_file))
    out = ags._load_private_key_pem()
    assert out == priv_pem


def test_load_private_key_pem_falls_back_to_path(monkeypatch, rsa_keypair, private_key_pem_file):
    priv_pem, _ = rsa_keypair
    monkeypatch.delenv("LTI13_PRIVATE_KEY", raising=False)
    monkeypatch.setenv("LTI13_PRIVATE_KEY_PATH", str(private_key_pem_file))
    out = ags._load_private_key_pem()
    assert out.strip() == priv_pem.strip()


def test_load_private_key_pem_raises_when_neither_set(monkeypatch):
    monkeypatch.delenv("LTI13_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("LTI13_PRIVATE_KEY_PATH", raising=False)
    with pytest.raises(ags.AGSError, match="LTI13_PRIVATE_KEY"):
        ags._load_private_key_pem()


# ---------------------- _sign_client_assertion ----------------------


def test_sign_client_assertion_produces_jwt_verifiable_by_public_key(
    monkeypatch, rsa_keypair
):
    priv_pem, pub_pem = rsa_keypair
    monkeypatch.setenv("LTI13_PRIVATE_KEY", priv_pem)

    token = ags._sign_client_assertion(
        client_id="my-tool", token_url="https://platform.example/token", key_id="kid-1"
    )

    # Decode using the matching public key; should succeed.
    decoded = jwt.decode(
        token,
        pub_pem,
        algorithms=["RS256"],
        audience="https://platform.example/token",
    )
    assert decoded["iss"] == "my-tool"
    assert decoded["sub"] == "my-tool"
    assert decoded["aud"] == "https://platform.example/token"
    assert "iat" in decoded and "exp" in decoded
    assert decoded["exp"] - decoded["iat"] == 300  # 5 min window
    assert "jti" in decoded

    # Header should carry the kid we passed.
    headers = jwt.get_unverified_header(token)
    assert headers["kid"] == "kid-1"
    assert headers["alg"] == "RS256"


def test_sign_client_assertion_omits_kid_header_when_none(monkeypatch, rsa_keypair):
    priv_pem, _ = rsa_keypair
    monkeypatch.setenv("LTI13_PRIVATE_KEY", priv_pem)

    token = ags._sign_client_assertion(
        client_id="my-tool", token_url="https://platform.example/token", key_id=None
    )
    headers = jwt.get_unverified_header(token)
    assert "kid" not in headers


def test_sign_client_assertion_rejected_by_wrong_public_key(monkeypatch, rsa_keypair):
    """Signed with key A → fails verification against key B's public part."""
    priv_pem, _ = rsa_keypair
    monkeypatch.setenv("LTI13_PRIVATE_KEY", priv_pem)

    # Generate a different keypair; its public key shouldn't verify.
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_pub = other.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()

    token = ags._sign_client_assertion(
        client_id="my-tool", token_url="https://platform.example/token", key_id=None
    )
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(
            token,
            other_pub,
            algorithms=["RS256"],
            audience="https://platform.example/token",
        )


# ---------------------- token cache ----------------------


def test_token_cache_is_per_token_url(monkeypatch):
    # Pure-Python cache check: same dict, different keys don't collide.
    ags._TOKEN_CACHE.clear()
    now = time.time()
    ags._TOKEN_CACHE["https://a.example/token"] = ("token-A", now + 3600)
    ags._TOKEN_CACHE["https://b.example/token"] = ("token-B", now + 3600)
    assert ags._TOKEN_CACHE["https://a.example/token"][0] == "token-A"
    assert ags._TOKEN_CACHE["https://b.example/token"][0] == "token-B"
    ags._TOKEN_CACHE.clear()
