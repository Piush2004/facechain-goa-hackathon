"""Canonical fingerprinting of a discovered post.

The fingerprint is a keccak256 over a canonical JSON serialisation of the
fields that identify the post and tie it to the face scan. Canonical =
sorted keys, no insignificant whitespace, UTF-8. Recomputing the fingerprint
from the same payload always yields the same 32-byte value, which is what
makes the on-chain record re-verifiable and tamper-evident.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from eth_utils import keccak


def canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_payload(
    *,
    source_image_url: str,
    face_embedding_sha256: str,
    match_url: str,
    match_source: str,
    match_title: str,
    match_thumbnail_sha256: str | None,
    match_image_face_similarity: float | None,
    discovered_at: str,
) -> dict[str, Any]:
    """Assemble the exact dict that gets hashed. Order-independent (keys sorted at hash time)."""
    return {
        "schema": "facechain/post-fingerprint/v1",
        "source_image_url": source_image_url,
        "face_embedding_sha256": face_embedding_sha256,
        "match_url": match_url,
        "match_source": match_source,
        "match_title": match_title,
        "match_thumbnail_sha256": match_thumbnail_sha256,
        "match_image_face_similarity": (
            None if match_image_face_similarity is None else round(float(match_image_face_similarity), 6)
        ),
        "discovered_at": discovered_at,
    }


def fingerprint(payload: dict[str, Any]) -> bytes:
    """Return the 32-byte keccak256 fingerprint of a payload dict."""
    return keccak(canonical_json(payload))


def fingerprint_hex(payload: dict[str, Any]) -> str:
    return "0x" + fingerprint(payload).hex()
