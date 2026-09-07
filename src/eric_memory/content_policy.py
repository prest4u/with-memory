"""Pre-persistence content controls.

The policy deliberately returns only reason codes and a fixed redacted summary.
Callers must never include rejected input in exceptions, logs, audit metadata, or
quarantine rows.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .errors import ValidationError

MAX_CANDIDATE_CHARS = 1200
SENTENCE_END_RE = re.compile(r"[.!?。！？]+(?:[\"'”’）】》])?(?:\s+|$)")

EXPLICIT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----", re.I)),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("github_token", re.compile(r"\bgh(?:p|o|u|s|r)_[A-Za-z0-9_]{30,}\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("cookie_header", re.compile(r"(?:^|\n)\s*(?:set-)?cookie\s*:\s*\S+", re.I)),
    (
        "credential_assignment",
        re.compile(r"\b(?:password|passwd|api[_-]?key|secret[_-]?key|access[_-]?token)\s*[:=]\s*\S+", re.I),
    ),
)

MINOR_PERFORMANCE_RE = re.compile(
    r"(?:未成年|学生|学员|student|pupil|grade\s*[1-9]\b).{0,80}"
    r"(?:成绩|分数|排名|表现|测评|score|rank|midterm|exam)",
    re.I | re.S,
)
RAW_ORIGINAL_RE = re.compile(r"(?:完整原文|整段原文|verbatim transcript|raw transcript)", re.I)
TOKEN_RE = re.compile(r"[A-Za-z0-9_+/=-]{28,}")
SESSION_UUID_RE = re.compile(
    r"(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}(?![0-9a-f])",
    re.I,
)


@dataclass(frozen=True)
class PolicyDecision:
    action: str
    codes: tuple[str, ...]

    @property
    def safe_summary(self) -> str:
        return "Content withheld by local safety policy."


def _entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def inspect_content(content: str) -> PolicyDecision:
    explicit = [code for code, pattern in EXPLICIT_PATTERNS if pattern.search(content)]
    if explicit:
        return PolicyDecision("reject", tuple(sorted(set(explicit))))
    high_entropy = any(_entropy(token) >= 4.0 for token in TOKEN_RE.findall(content))
    suspicious: list[str] = []
    if high_entropy:
        suspicious.append("high_entropy_token")
    if MINOR_PERFORMANCE_RE.search(content):
        suspicious.append("minor_individual_performance")
    if RAW_ORIGINAL_RE.search(content) or len(content.splitlines()) > 12:
        suspicious.append("possible_raw_original")
    if suspicious:
        return PolicyDecision("quarantine", tuple(sorted(set(suspicious))))
    return PolicyDecision("allow", ())


def inspect_metadata(values: list[str], *, source_locator: str = "") -> PolicyDecision:
    """Reject secrets in auxiliary fields that would otherwise bypass body checks."""
    combined = "\n".join(value for value in [*values, source_locator] if value)
    explicit = [code for code, pattern in EXPLICIT_PATTERNS if pattern.search(combined)]
    # UUID session filenames are identifiers, not mixed-alphabet credentials.
    # Normalize only the locator's UUIDs for the entropy heuristic; explicit
    # credential patterns still inspect every original byte above. All other
    # metadata and the persisted locator stay unchanged.
    entropy_input = "\n".join([*values, SESSION_UUID_RE.sub("uuid", source_locator)])
    # A slightly higher entropy threshold avoids treating ordinary long hex
    # filenames as credentials while still catching mixed-alphabet tokens.
    if any(_entropy(token) >= 4.2 for token in TOKEN_RE.findall(entropy_input)):
        explicit.append("high_entropy_token")
    if explicit:
        return PolicyDecision("reject", tuple(sorted(set(explicit))))
    return PolicyDecision("allow", ())


def validate_pointer(content: str, *, candidate: bool = True) -> str:
    value = content.strip()
    if not value:
        raise ValidationError("content must not be empty")
    if candidate and len(value) > MAX_CANDIDATE_CHARS:
        raise ValidationError(f"candidate content must be at most {MAX_CANDIDATE_CHARS} Unicode characters")
    if candidate:
        endings = len(SENTENCE_END_RE.findall(value))
        if endings == 0:
            endings = 1
        if endings > 3:
            raise ValidationError("candidate content must contain 1–3 sentences")
    return value
