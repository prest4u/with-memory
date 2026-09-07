"""Stable public errors shared by the CLI, service layer, and MCP."""

from __future__ import annotations

from typing import Any


class MemoryError(Exception):
    """Base exception with a stable machine-readable error code."""

    code = "MEMORY_ERROR"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class DatabaseNotFoundError(MemoryError, FileNotFoundError):
    code = "DATABASE_NOT_FOUND"


class DatabaseExistsError(MemoryError):
    code = "DATABASE_EXISTS"


class MigrationRequiredError(MemoryError):
    code = "MIGRATION_REQUIRED"


class SchemaTooNewError(MemoryError):
    code = "SCHEMA_TOO_NEW"


class PermissionRequiredError(MemoryError, PermissionError):
    code = "PERMISSION_REQUIRED"


class ValidationError(MemoryError, ValueError):
    code = "VALIDATION_ERROR"


class ContentRejectedError(MemoryError, ValueError):
    code = "CONTENT_REJECTED"


class ContentQuarantinedError(MemoryError, ValueError):
    code = "CONTENT_QUARANTINED"


class ConflictError(MemoryError):
    code = "CONFLICT"


class IntegrityCheckError(MemoryError):
    code = "INTEGRITY_CHECK_FAILED"


class ConfirmationRequiredError(MemoryError):
    code = "CONFIRMATION_REQUIRED"


class TruncatedScanError(MemoryError):
    code = "SCAN_TRUNCATED"


class NotFoundError(MemoryError, KeyError):
    code = "NOT_FOUND"


class UpdateVerificationError(MemoryError):
    code = "UPDATE_VERIFICATION_FAILED"


class UpdateUnavailableError(MemoryError):
    code = "UPDATE_UNAVAILABLE"


def public_error(error: Exception) -> dict[str, Any]:
    """Convert implementation exceptions to a small stable public vocabulary."""
    if isinstance(error, MemoryError):
        return error.to_dict()
    if isinstance(error, FileNotFoundError):
        code = "NOT_FOUND"
    elif isinstance(error, ValueError):
        code = "VALIDATION_ERROR"
    elif isinstance(error, PermissionError):
        code = "PERMISSION_REQUIRED"
    elif isinstance(error, OSError):
        code = "IO_ERROR"
    else:
        code = "INTERNAL_ERROR"
    return {"code": code, "message": str(error), "details": {}}
