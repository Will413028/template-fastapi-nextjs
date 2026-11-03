"""RFC 9457 problem responses, with the same `type` and `title` values as the Go version (internal/httpapi/problem.go).

Clients choose messages by type and code (docs/rules/api-design.md), so changing a slug is a breaking change. Step 2b
of docs/plans/2026-11-03-fastapi-template.md adds the other types with the exception handlers that raise them.
"""

from dataclasses import dataclass
from http import HTTPStatus

from fastapi.responses import JSONResponse

PROBLEM_CONTENT_TYPE = "application/problem+json"


@dataclass(frozen=True, slots=True)
class ProblemType:
    status: HTTPStatus
    slug: str
    title: str

    @property
    def uri(self) -> str:
        return f"/problems/{self.slug}"


UNAVAILABLE = ProblemType(HTTPStatus.SERVICE_UNAVAILABLE, "unavailable", "Service unavailable")


def problem_response(problem: ProblemType) -> JSONResponse:
    body = {"type": problem.uri, "title": problem.title, "status": int(problem.status)}
    return JSONResponse(body, status_code=problem.status, media_type=PROBLEM_CONTENT_TYPE)
