"""The application log (Go version: internal/platform/logx): readable text in dev, one JSON object per line in prod.

Every line logged while a request is served carries its request_id (app.http.request_id), so all the lines of one
request, the access log line included, can be matched with each other and with the X-Request-ID the client got.
Fields passed with `extra=` become their own keys.
"""

import datetime as dt
import json
import logging
import re
import sys
from typing import TYPE_CHECKING, Any, TextIO, override

from app.http.request_id import current_request_id

if TYPE_CHECKING:
    from app.core.config import Mode

# A text value made only of these is written bare; anything else (spaces, quotes, a newline from a %0A in a path) is
# quoted, so a client cannot make one field look like several or forge a line.
_BARE = re.compile(r"[A-Za-z0-9._:/@+-]+")

# The attributes every LogRecord has; anything else on a record came from `extra=`. color_message is uvicorn's own
# copy of msg with terminal colour codes.
_STANDARD = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName", "color_message"}


def configure_logging(mode: Mode, stream: TextIO = sys.stderr, *, level: int = logging.INFO) -> None:
    """Replaces the root logger's handlers with one writing to stream. Uvicorn's loggers propagate to it (`app serve`
    passes log_config=None so uvicorn installs none of its own)."""
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter() if mode == "prod" else TextFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


def _fields(record: logging.LogRecord) -> dict[str, Any]:
    fields = {key: value for key, value in vars(record).items() if key not in _STANDARD}
    if (request_id := current_request_id()) is not None:
        fields["request_id"] = request_id
    return fields


def _time(record: logging.LogRecord) -> str:
    return dt.datetime.fromtimestamp(record.created, tz=dt.UTC).isoformat(timespec="milliseconds")


class JsonFormatter(logging.Formatter):
    @override
    def format(self, record: logging.LogRecord) -> str:
        # the core keys come last, so an extra= of the same name cannot replace them
        entry: dict[str, Any] = {
            **_fields(record),
            "time": _time(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            entry["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    @override
    def format(self, record: logging.LogRecord) -> str:
        pairs = " ".join(f"{key}={_text_value(value)}" for key, value in _fields(record).items())
        # a message may carry request data (uvicorn's warnings do): its line breaks are escaped too
        message = record.getMessage().replace("\r", "\\r").replace("\n", "\\n")
        line = f"{_time(record)} {record.levelname} {record.name} {message}"
        if pairs:
            line = f"{line} {pairs}"
        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line


def _text_value(value: object) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if _BARE.fullmatch(text) else json.dumps(text)
