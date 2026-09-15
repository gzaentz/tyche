"""Credential-free Arena operation frames; no HTTP or direct-provider fallback."""

import base64
import copy
import json
from pathlib import Path
import socket
import threading
import time

import budget_guard
import deepline


class BrokerError(RuntimeError):
    pass


class BrokerRefusal(BrokerError):
    """A known refusal before provider dispatch, with its original code."""

    def __init__(self, code):
        self.code = code
        super().__init__("Arena refused operation: " + code)


class Broker:
    def __init__(self, socket_path, deadline, *, catalog=None):
        if not Path(socket_path).is_absolute():
            raise ValueError("LAB_ARENA_WORKER_SOCKET must be an absolute path")
        self.socket_path = str(socket_path)
        self.deadline = deadline
        self.catalog = catalog if catalog is not None else json.loads(Path(__file__).with_name("catalog.json").read_text())["tools"]
        self.calls = 0
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.provider_blocked = False

    @staticmethod
    def _receive(connection, size):
        result = bytearray()
        while len(result) < size:
            part = connection.recv(min(size - len(result), 65536))
            if not part:
                raise BrokerError("worker_unavailable: incomplete response; do not retry")
            result.extend(part)
        return bytes(result)

    def request(self, operation, parameters):
        if operation != "deepline.execute":
            raise ValueError("Unsupported Arena operation")
        if operation == "deepline.execute" and parameters.get("tool") not in self.catalog:
            raise ValueError("Tool is absent from the bundled Arena catalog")
        with self.lock:
            remaining = self.deadline - time.monotonic()
            if self.stopped.is_set() or remaining <= 0:
                raise BrokerRefusal("deadline_reached")
            if self.provider_blocked:
                raise BrokerRefusal("provider_blocked_after_uncertain_call")
            if self.calls >= 30:
                raise BrokerRefusal("deepline_quota_exceeded")
            self.calls += 1
        frame = json.dumps({"schema_version": "leadpoet.lab_arena.operation_frame.v1",
            "operation_id": operation, "parameters": parameters,
            "timeout_ms": max(1, min(int(remaining * 1000), 60000))},
            allow_nan=False, separators=(",", ":")).encode()
        if len(frame) > 1048576:
            raise ValueError("Arena request exceeds frame limit")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(remaining)
                connection.connect(self.socket_path)
                connection.sendall(len(frame).to_bytes(4, "big") + frame)
                size = int.from_bytes(self._receive(connection, 4), "big")
                if not 2 <= size <= 4 * 1048576:
                    raise BrokerError("Invalid Arena response size")
                response = json.loads(self._receive(connection, size))
        except (OSError, ValueError) as exc:
            # Dispatch may have billed. Never replay this request.
            raise BrokerError("Arena transport failed; do not retry the call") from exc
        if not isinstance(response, dict):
            raise BrokerError("Invalid Arena response envelope")
        if "error" in response:
            if set(response) == {"error"} and isinstance(response["error"], str) and response["error"] in {
                    "budget_exhausted", "invalid_frame", "frame_too_large", "invalid_request", "invalid_body"}:
                raise BrokerRefusal(response["error"])
            raise BrokerError("Arena refused operation: " + str(response["error"]))
        if set(response) != {"status", "headers", "body_b64"} or type(response["status"]) is not int:
            raise BrokerError("Invalid Arena response envelope")
        try:
            body = json.loads(base64.b64decode(response["body_b64"], validate=True))
        except (ValueError, TypeError) as exc:
            raise BrokerError("Arena returned invalid provider JSON; do not retry") from exc
        return response["status"], response["headers"], body

    def execute(self, request, capture):
        operation = request["operation"]
        if operation in {"search", "describe"}:
            if operation == "describe":
                rows = [self.catalog[request["tool"]]] if request["tool"] in self.catalog else []
            else:
                words = request.get("query", "").casefold().split()
                rows = sorted(self.catalog.values(), key=lambda row: -sum(
                    word in json.dumps(row).casefold() for word in words))[:request.get("limit", 10)]
            return {"provider": "deepline", "operation": operation, "status": "ok" if rows else "no_results",
                    "results": copy.deepcopy(rows)}, 0
        if operation != "execute" or request.get("tool") not in self.catalog:
            raise ValueError("Only catalogued Arena Deepline operations are supported")

        def dispatch():
            try:
                status, headers, payload = self.request("deepline.execute", {
                    "tool": request["tool"], "payload": request["payload"]})
            except BrokerRefusal as exc:
                status = "quota_exceeded" if "quota" in exc.code or exc.code == "budget_exhausted" else "config_error"
                if exc.code in {"invalid_frame", "frame_too_large", "invalid_request", "invalid_body"}:
                    status = "schema_error"
                # A worker refusal is not a provider response or billing receipt.
                # Preserve its code and keep the conservative reservation.
                raw = {"body": {"status": status, "error": {"code": exc.code, "message": str(exc)}},
                       "exit_code": 2, "arena": {"dispatched": False, "error": exc.code}}
                capture(raw)
                return deepline.normalize_response(request, raw)
            except BrokerError as exc:
                # Retain the reservation and block further paid research when
                # the outcome is uncertain. No invented zero-cost receipt.
                self.provider_blocked = True
                raw = {"body": {}, "timed_out": True, "stderr": str(exc)}
                capture(raw)
                return deepline.normalize_response(request, raw)
            raw = {"body": payload, "exit_code": 0 if 200 <= status < 300 else 2,
                   "stderr": "" if 200 <= status < 300 else f"Arena HTTP {status}",
                   "arena": {"status": status, "headers": headers}}
            capture(raw)
            return deepline.normalize_response(request, raw)

        return budget_guard.guarded_call(request, "deepline", dispatch)
