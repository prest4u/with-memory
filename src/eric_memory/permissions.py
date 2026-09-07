"""Principal capabilities used by both CLI services and MCP discovery."""

from __future__ import annotations

STATUS_READ = "status:read"
FACT_SEARCH = "fact:search"
CANDIDATE_SUBMIT = "candidate:submit"
CANDIDATE_READ_OWN = "candidate:read-own"
SOURCE_SCAN = "source:scan"
HISTORY_READ = "history:read"
FACT_WRITE = "fact:write"
SYNC_RUN = "sync:run"
FILE_SEARCH = "file:search"
MANAGE = "manage"

DEFAULT_HARNESS_CAPABILITIES = (
    STATUS_READ,
    FACT_SEARCH,
    CANDIDATE_SUBMIT,
    CANDIDATE_READ_OWN,
    SOURCE_SCAN,
)

LEGACY_CAPABILITIES = (STATUS_READ, FACT_SEARCH)

ALL_CAPABILITIES = frozenset(
    {
        *DEFAULT_HARNESS_CAPABILITIES,
        HISTORY_READ,
        FACT_WRITE,
        SYNC_RUN,
        FILE_SEARCH,
        MANAGE,
    }
)
