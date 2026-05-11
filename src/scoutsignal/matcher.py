from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlparse

from scoutsignal.config_loader import ChatEntry, DefaultsConfig


_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


@dataclass
class MatchResult:
    matched: bool
    fingerprint: str
    text: str
    urls: List[str]


def _normalize(text: str) -> str:
    s = " ".join((text or "").split()).strip().lower()
    return unicodedata.normalize("NFC", s)


def _fingerprint(chat_key: str, text: str) -> str:
    raw = f"{chat_key}\n{text.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def extract_urls(text: str) -> List[str]:
    return _URL_RE.findall(text or "")


def match_message(
    chat_key: str,
    text: str,
    defaults: DefaultsConfig,
    chat: ChatEntry,
) -> MatchResult:
    urls = extract_urls(text)
    fp = _fingerprint(chat_key, text)

    inc = chat.include_keywords if chat.include_keywords is not None else defaults.include_keywords
    exc = chat.exclude_keywords if chat.exclude_keywords is not None else defaults.exclude_keywords
    req_url = chat.require_url if chat.require_url is not None else defaults.require_url

    t = text or ""
    if len(t.strip()) < defaults.min_text_length:
        return MatchResult(False, fp, t, urls)

    if req_url and not urls:
        return MatchResult(False, fp, t, urls)

    norm = _normalize(t)
    if not norm:
        return MatchResult(False, fp, t, urls)

    for ex in exc:
        if ex and _normalize(ex) in norm:
            return MatchResult(False, fp, t, urls)

    if not inc:
        # No include keywords: match anything that passes exclusions + length (+ url if required).
        return MatchResult(True, fp, t, urls)

    for kw in inc:
        if kw and _normalize(kw) in norm:
            return MatchResult(True, fp, t, urls)

    return MatchResult(False, fp, t, urls)


def effective_include_keywords(defaults: DefaultsConfig, chat: ChatEntry) -> List[str]:
    inc = chat.include_keywords if chat.include_keywords is not None else defaults.include_keywords
    return [str(k).strip() for k in inc if k and str(k).strip()]


def _matches_prelude_for_keyword_count(text: str, defaults: DefaultsConfig, chat: ChatEntry) -> bool:
    """Same gates as match_message before the include-keyword OR check."""
    t = text or ""
    if len(t.strip()) < defaults.min_text_length:
        return False
    exc = chat.exclude_keywords if chat.exclude_keywords is not None else defaults.exclude_keywords
    req_url = chat.require_url if chat.require_url is not None else defaults.require_url
    urls = extract_urls(t)
    if req_url and not urls:
        return False
    norm = _normalize(t)
    if not norm:
        return False
    for ex in exc:
        if ex and _normalize(ex) in norm:
            return False
    return True


def count_keyword_hits_in_messages(
    message_texts: List[str],
    chat: ChatEntry,
    defaults: DefaultsConfig,
) -> dict[str, int]:
    """
    Per-keyword counts: number of scraped messages (after prelude filters) whose text
    contains that keyword as a substring (NFC-normalized). Multiple keywords can match one message.
    """
    inc = effective_include_keywords(defaults, chat)
    counts: dict[str, int] = {k: 0 for k in inc}
    if not inc:
        return {}
    for raw in message_texts:
        if not _matches_prelude_for_keyword_count(raw, defaults, chat):
            continue
        norm = _normalize(raw)
        for kw in inc:
            if _normalize(kw) in norm:
                counts[kw] += 1
    return counts


def first_http_url(text: str) -> Optional[str]:
    for u in extract_urls(text):
        try:
            p = urlparse(u)
            if p.scheme in ("http", "https") and p.netloc:
                return u
        except ValueError:
            continue
    return None
