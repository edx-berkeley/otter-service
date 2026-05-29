"""LTI 1.3 Assignment & Grade Services (AGS) grade-passback.

Companion to the LTI 1.1 `lis_outcome_service_url` XML/OAuth1 path in
`otter_nb.post_grade()`. When a notebook submission comes in with LTI 1.3
auth metadata (a `lineitem` URL and a platform `token_url`), we sign a
client-credentials JWT with the tool's RS256 private key, exchange it for
an OAuth2 access_token, and POST a Score to `<lineitem>/scores`.

Reference implementation: `tools/lti13-poc/post_score.py` in the
claude-memory-edx-berkeley repo. That script was used to prove the AGS
dance end-to-end against Saltire (HTTP 204 = score accepted, the LTI 1.3
spec response).

The tool's RSA private key is read from the `LTI13_PRIVATE_KEY` env var
(full PEM string) or `LTI13_PRIVATE_KEY_PATH` (path to a PEM file).
`LTI13_CLIENT_ID` and optional `LTI13_KEY_ID` are also env-driven.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from pathlib import Path

import aiohttp
import async_timeout
import jwt

AGS_SCORE_SCOPE = "https://purl.imsglobal.org/spec/lti-ags/scope/score"
DEFAULT_SCOPES = [AGS_SCORE_SCOPE]

# Cache: token_url → (access_token, expires_at_epoch). Avoids signing a
# fresh JWT and round-tripping the token endpoint on every submission.
# Each token typically TTLs ~1h; we refresh at 90% of TTL to be safe.
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}


class AGSError(Exception):
    """Raised when AGS token exchange or score POST fails."""


def _load_private_key_pem() -> str:
    """Return the tool's RSA private key PEM string.

    Prefers `LTI13_PRIVATE_KEY` (full PEM); falls back to
    `LTI13_PRIVATE_KEY_PATH` (path to a PEM file on disk).
    """
    pem = os.environ.get("LTI13_PRIVATE_KEY")
    if pem:
        return pem
    path = os.environ.get("LTI13_PRIVATE_KEY_PATH")
    if path:
        return Path(path).read_text()
    raise AGSError(
        "neither LTI13_PRIVATE_KEY nor LTI13_PRIVATE_KEY_PATH is set; "
        "cannot sign AGS JWT"
    )


def _sign_client_assertion(client_id: str, token_url: str, key_id: str | None) -> str:
    """Build and sign the client-credentials JWT (RS256)."""
    pem = _load_private_key_pem()
    now = int(time.time())
    payload = {
        "iss": client_id,
        "sub": client_id,
        "aud": token_url,
        "iat": now,
        "exp": now + 300,           # 5 min — short window per LTI spec
        "jti": str(uuid.uuid4()),
    }
    headers = {"kid": key_id} if key_id else {}
    return jwt.encode(payload, pem, algorithm="RS256", headers=headers)


async def get_access_token(
    token_url: str,
    *,
    scopes: list[str] | None = None,
    client_id: str | None = None,
    key_id: str | None = None,
) -> str:
    """Exchange a signed client-credentials JWT for an access_token.

    Cached per token_url; refreshes when within 10% of expiry.
    """
    if scopes is None:
        scopes = DEFAULT_SCOPES
    if client_id is None:
        client_id = os.environ.get("LTI13_CLIENT_ID")
        if not client_id:
            raise AGSError("LTI13_CLIENT_ID not set")
    if key_id is None:
        key_id = os.environ.get("LTI13_KEY_ID")  # optional

    # cache check
    cached = _TOKEN_CACHE.get(token_url)
    if cached and cached[1] > time.time() + 30:
        return cached[0]

    assertion = _sign_client_assertion(client_id, token_url, key_id)
    body = {
        "grant_type": "client_credentials",
        "client_assertion_type": (
            "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
        ),
        "client_assertion": assertion,
        "scope": " ".join(scopes),
    }
    async with async_timeout.timeout(15):
        async with aiohttp.ClientSession() as session:
            async with session.post(token_url, data=body) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    raise AGSError(
                        f"token exchange failed: HTTP {resp.status} {text[:300]}"
                    )
                data = await resp.json(content_type=None)

    access_token = data["access_token"]
    # expires_in is in seconds; cache until 90% of TTL has elapsed.
    expires_in = float(data.get("expires_in", 3600))
    _TOKEN_CACHE[token_url] = (access_token, time.time() + expires_in * 0.9)
    return access_token


async def post_score(
    lineitem_url: str,
    user_id: str,
    score: float,
    max_score: float,
    *,
    token_url: str,
    activity_progress: str = "Completed",
    grading_progress: str = "FullyGraded",
    client_id: str | None = None,
    key_id: str | None = None,
) -> None:
    """POST an LTI 1.3 Score JSON to `<lineitem>/scores`.

    Per spec: HTTP 200 / 201 / 204 = success. Anything 4xx/5xx → AGSError.
    """
    access_token = await get_access_token(
        token_url, scopes=[AGS_SCORE_SCOPE], client_id=client_id, key_id=key_id
    )
    url = lineitem_url.rstrip("/") + "/scores"
    body = {
        "userId": str(user_id),
        "scoreGiven": float(score),
        "scoreMaximum": float(max_score),
        "activityProgress": activity_progress,
        "gradingProgress": grading_progress,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/vnd.ims.lis.v1.score+json",
    }
    async with async_timeout.timeout(15):
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=body, headers=headers) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    raise AGSError(
                        f"score POST failed: HTTP {resp.status} {text[:300]}"
                    )


def is_lti13_metadata(metadata: dict) -> bool:
    """True if metadata carries the LTI 1.3 AGS fields needed to score.

    Detection: presence of both `lineitem` URL and `token_url` (the
    platform's OAuth2 token endpoint). These come from the launch JWT's
    `https://purl.imsglobal.org/spec/lti-ags/claim/endpoint` claim,
    captured into JH `auth_state` and forwarded by otter-submit in the
    grade submission body.
    """
    return bool(metadata.get("lti13_lineitem") and metadata.get("lti13_token_url"))


async def post_grade_lti13(metadata: dict, grade: float, max_score: float = 1.0) -> None:
    """LTI 1.3 counterpart to `otter_nb.post_grade()`'s XML/OAuth1 path.

    Expects metadata keys:
        - lti13_lineitem: URL to the lineitem we're scoring
        - lti13_token_url: platform's OAuth2 token endpoint
        - userid: the LTI 1.3 `sub` claim (used as AGS userId)
        - lti13_client_id: optional override; falls back to LTI13_CLIENT_ID env
    """
    await post_score(
        lineitem_url=metadata["lti13_lineitem"],
        user_id=metadata["userid"],
        score=grade,
        max_score=max_score,
        token_url=metadata["lti13_token_url"],
        client_id=metadata.get("lti13_client_id"),
    )
