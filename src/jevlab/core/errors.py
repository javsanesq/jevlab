"""Safe, actionable errors shared by both interfaces."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast


@dataclass
class JevError(Exception):
    code: str
    message: str
    fix: str
    exit_code: int = 2
    retryable: bool = False
    request_id: str | None = None
    run_id: str | None = None
    http_status: int | None = None
    provider_code: str | None = None
    details: dict[str, object] | None = None

    def __str__(self) -> str:
        return f"{self.message} {self.fix}"

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "code": self.code,
            "message": self.message,
            "fix": self.fix,
            "retryable": self.retryable,
            "request_id": self.request_id,
            "run_id": self.run_id,
        }

        for name in ("http_status", "provider_code", "details"):
            value = getattr(self, name)
            if value is not None:
                result[name] = value
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, object], *, exit_code: int = 2) -> "JevError":
        """Restore diagnostics from history without dropping optional metadata."""

        def optional_text(name: str) -> str | None:
            value = data.get(name)
            return value if isinstance(value, str) else None

        status = data.get("http_status")
        details = data.get("details")
        return cls(
            code=str(data.get("code", "unknown_error")),
            message=str(data.get("message", "The saved error has no explanation.")),
            fix=str(data.get("fix", "Open the saved technical details before retrying.")),
            exit_code=exit_code,
            retryable=data.get("retryable") is True,
            request_id=optional_text("request_id"),
            run_id=optional_text("run_id"),
            http_status=status
            if isinstance(status, int) and not isinstance(status, bool)
            else None,
            provider_code=optional_text("provider_code"),
            details=cast(dict[str, object], details) if isinstance(details, dict) else None,
        )
