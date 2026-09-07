"""Entity names are CJK-only or Latin-only. Mixed names are split, never stored mixed."""

from __future__ import annotations

import re

CJK_RE = re.compile(r"[\u3400-\u9fff]")
LATIN_RE = re.compile(r"[A-Za-z]")
TOKEN_RE = re.compile(r"[\u3400-\u9fff]+|[A-Za-z][A-Za-z0-9._+-]*")
QUOTE_RE = re.compile(r'"([^"]{1,80})"|“([^”]{1,80})”|「([^」]{1,80})」')
HAS_CJK_RE = re.compile(r"[\u3400-\u9fff]")


def has_cjk(text: str) -> bool:
    return bool(HAS_CJK_RE.search(text))


def is_pure_name(name: str) -> bool:
    stripped = name.strip()
    if len(stripped) < 2:
        return False
    cjk = bool(CJK_RE.search(stripped))
    latin = bool(LATIN_RE.search(stripped))
    return not (cjk and latin)


def split_entity_tokens(name: str) -> list[str]:
    """Split a mixed label into pure CJK or Latin tokens."""
    seen: set[str] = set()
    out: list[str] = []
    for token in TOKEN_RE.findall(name):
        if not is_pure_name(token):
            continue
        key = token.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(token)
    return out


def normalize_names(names: list[str] | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in names or []:
        for token in split_entity_tokens(raw) or ([raw.strip()] if is_pure_name(raw) else []):
            key = token.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(token)
    return out


def quoted_names(text: str) -> list[str]:
    found: list[str] = []
    for match in QUOTE_RE.finditer(text):
        value = next((g for g in match.groups() if g), "")
        found.extend(split_entity_tokens(value))
    return found


def mention_hits(content: str, known_names: list[str]) -> list[str]:
    hits: list[str] = []
    seen: set[str] = set()
    for name in known_names:
        if not name or name.casefold() in seen:
            continue
        if has_cjk(name):
            if name in content:
                seen.add(name.casefold())
                hits.append(name)
            continue
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", content):
            seen.add(name.casefold())
            hits.append(name)
    return hits


def collect_entities(content: str, explicit: list[str] | None, known_names: list[str] | None = None) -> list[str]:
    names = normalize_names(explicit)
    names.extend(quoted_names(content))
    if known_names:
        names.extend(mention_hits(content, known_names))
    return normalize_names(names)
