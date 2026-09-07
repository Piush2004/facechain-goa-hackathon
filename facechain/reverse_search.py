"""Reverse-image / social-media search via SerpAPI (Google Lens engine).

This is a genuine search step: we hand SerpAPI the face-scan image URL, it runs
Google Lens, and we get back the pages Google found that contain that image.
We then keep only results that live on social-media domains and return the
best one. Nothing here is hard-coded to a particular person or post.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any

import requests

SOCIAL_DOMAINS = (
    "instagram.com", "twitter.com", "x.com", "facebook.com", "fb.com",
    "linkedin.com", "tiktok.com", "youtube.com", "youtu.be", "threads.net",
    "reddit.com", "pinterest.com", "tumblr.com", "flickr.com", "vk.com",
    "mastodon.social", "bsky.app", "weibo.com",
)


@dataclass
class Match:
    title: str
    link: str
    source: str
    thumbnail: str | None
    is_social: bool
    raw: dict[str, Any]


def _serpapi_key() -> str:
    key = os.getenv("SERPAPI_KEY") or os.getenv("SERPAPI_API_KEY")
    if not key:
        raise RuntimeError(
            "SERPAPI_KEY is not set. Put it in .env (see .env.example) or export it."
        )
    return key


def _domain_of(url: str) -> str:
    from urllib.parse import urlparse

    netloc = urlparse(url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def _is_social(url: str) -> bool:
    d = _domain_of(url)
    return any(d == sd or d.endswith("." + sd) for sd in SOCIAL_DOMAINS)


def google_lens(image_url: str, *, hl: str = "en", country: str = "us", timeout: int = 60) -> list[Match]:
    """Run Google Lens on an image URL and return all visual/exact matches."""
    params = {
        "engine": "google_lens",
        "url": image_url,
        "hl": hl,
        "country": country,
        "api_key": _serpapi_key(),
    }
    resp = requests.get("https://serpapi.com/search.json", params=params, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if data.get("error"):
        raise RuntimeError(f"SerpAPI error: {data['error']}")

    results: list[Match] = []
    buckets = []
    for k in ("exact_matches", "visual_matches", "image_results"):
        if isinstance(data.get(k), list):
            buckets.extend(data[k])
    for item in buckets:
        link = item.get("link") or item.get("source_url") or ""
        if not link:
            continue
        results.append(
            Match(
                title=item.get("title") or item.get("source") or "",
                link=link,
                source=item.get("source") or _domain_of(link),
                thumbnail=item.get("thumbnail"),
                is_social=_is_social(link),
                raw=item,
            )
        )
    # de-dupe by link, keep first occurrence (exact matches come first)
    seen, deduped = set(), []
    for m in results:
        if m.link in seen:
            continue
        seen.add(m.link)
        deduped.append(m)
    return deduped


def best_social_match(matches: list[Match]) -> Match | None:
    social = [m for m in matches if m.is_social]
    return social[0] if social else None


def fetch_thumbnail_sha256(url: str | None, timeout: int = 30) -> str | None:
    if not url:
        return None
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return hashlib.sha256(r.content).hexdigest()
    except requests.RequestException:
        return None


def upload_for_search(path: str, timeout: int = 60) -> str:
    """Best-effort: host a local image so Google Lens can fetch it.

    Uses tmpfiles.org (no key, ephemeral). Prefer passing --image-url when the
    scan already exists online.
    """
    with open(path, "rb") as fh:
        r = requests.post("https://tmpfiles.org/api/v1/upload", files={"file": fh}, timeout=timeout)
    r.raise_for_status()
    page_url = r.json()["data"]["url"]
    # convert the viewer URL to a direct-download URL
    return page_url.replace("tmpfiles.org/", "tmpfiles.org/dl/", 1)
