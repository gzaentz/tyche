"""Preserve a provider response before normalization, without repeating a call."""

import json
import math
import os
from pathlib import Path
import tempfile


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("JSON numbers must be finite")
    return number


def load_json(text):
    return json.loads(text, parse_float=_finite_float, parse_constant=_finite_float)


def response_body(parsed, text):
    """Keep diagnostic text when the provider response could not be parsed."""
    return text if parsed is None else parsed


class ResponseFile:
    def __init__(self, path, redact):
        self.path = Path(path)
        self.redact = redact
        self.response = None
        # Reserve a unique destination and prove it is writable before dispatch.
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        self.identity = self._identity()
        self._write({"receipt_status": "pending"})

    def _identity(self):
        stat = self.path.lstat()
        return stat.st_ino, stat.st_mtime_ns, stat.st_size

    def _write(self, document):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, delete=False) as stream:
                temporary = stream.name
                json.dump(self.redact(document), stream, ensure_ascii=False, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            if self._identity() != self.identity:
                raise OSError("response output changed during the request")
            os.replace(temporary, self.path)
            self.identity = self._identity()
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def capture(self, response):
        self.response = self.redact(response)
        try:
            self._write({"receipt_status": "response_received", "provider_response": self.response})
        except (OSError, TypeError, ValueError):
            # Keep the response in memory for the final save; never retry the provider.
            pass

    def finish(self, body):
        document = dict(body, receipt_status="complete")
        if self.response is not None:
            document["provider_response"] = self.response
        try:
            self._write(document)
        except (OSError, TypeError, ValueError):
            return False
        return True
