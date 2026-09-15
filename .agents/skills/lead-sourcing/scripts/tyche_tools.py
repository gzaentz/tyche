#!/usr/bin/env python3
"""Small MCP stdio transport for the existing run-bound research functions.

Only tools/list, tools/call, initialize and ping are exposed. There is no
arbitrary shell/file endpoint or independently running service.
"""

import argparse
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from urllib.parse import unquote, urlparse

from research_tools import ResearchTools, TOOLS
from deepline import redact


class SandboxedTools:
    """Relay to one child using the caller's actual Codex sandbox metadata.

    The relay performs no research, file writes or provider dispatch itself.
    A persistent child retains the existing thread locks and three-call limit.
    """
    def __init__(self, run_file, readonly=False):
        if Path(run_file).is_symlink():
            raise ValueError("Bound run file must not be a symlink")
        self.path, self.readonly = Path(run_file).resolve(), readonly
        self.child = None
        self.state = None
        self.lock = threading.Lock()
        self.pending = {}
        self.sequence = 0
        self.children = []
        self.restarts = 0

    def _start(self, state, root):
        command = ["codex", "sandbox", "--sandbox-state-json", json.dumps(state),
                   sys.executable, str(Path(__file__).resolve()), "--worker", "--run-file", str(self.path)]
        if self.readonly:
            command.append("--read-only")
        child = subprocess.Popen(command, cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1)
        self.child, self.state = child, state
        errors = deque(maxlen=8)
        error_reader = threading.Thread(target=self._errors, args=(child, errors), daemon=True)
        reader = threading.Thread(target=self._read, args=(child, errors, error_reader), daemon=True)
        self.children.append((child, reader, error_reader))
        error_reader.start()
        reader.start()

    def call(self, name, arguments, metadata):
        state = metadata.get("codex/sandbox-state-meta") if isinstance(metadata, dict) else None
        if not isinstance(state, dict) or not state.get("permissionProfile"):
            raise ValueError("Codex did not supply sandbox metadata; no research started")
        cwd = urlparse(state.get("sandboxCwd", ""))
        if cwd.scheme != "file" or cwd.netloc not in ("", "localhost"):
            raise ValueError("Sandbox workspace must be a local file URI")
        root = Path(unquote(cwd.path)).resolve()
        if root != Path(__file__).resolve().parents[4]:
            raise ValueError("Sandbox workspace differs from the bound TYCHE checkout")
        old = self.child
        if old is not None and old.poll() is not None:
            next(reader for child, reader, _ in self.children if child is old).join(timeout=1)
        with self.lock:
            if self.state is not None and self.state != state:
                raise ValueError("Sandbox changed; restart the tool connection before more research")
            if self.child is not None and self.child.poll() is not None:
                if next(reader for child, reader, _ in self.children if child is self.child).is_alive():
                    return self._connection_status("Exited child still has open output; preserve pending work", self.child, reconnect=False)
                if self.restarts >= 1:
                    return self._connection_status("Repeated child exit; automatic reconnect limit reached", self.child)
                self.restarts += 1
                self._start(state, root)
            elif self.child is None:
                self._start(state, root)
            child = self.child
            self.sequence += 1
            ident, future = self.sequence, Future()
            self.pending[ident] = (child, future)
            try:
                child.stdin.write(json.dumps({"id": ident, "method": "tools/call", "params": {"name": name, "arguments": arguments}}) + "\n")
                child.stdin.flush()
            except (OSError, ValueError) as exc:
                self.pending.pop(ident, None)
                future.set_exception(ConnectionError(str(exc)))
        try:
            response = future.result(timeout=900)
        except (ConnectionError, FutureTimeoutError) as exc:
            # A lost response never authorizes replay of research or a write.
            # Only pure saved-state inspection can transparently reconnect.
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            read_only = name == "tyche_inspect" and not any(k in arguments for k in ("query", "tool", "recover", "refresh"))
            with self.lock:
                retry_read = read_only and child.poll() is not None and (self.restarts < 1 or self.child is not child)
                failure = None if retry_read else self._connection_status(str(exc), child)
            if retry_read:
                return self.call(name, arguments, metadata)
            return failure
        if response.get("isError"):
            raise ValueError(response["content"][0]["text"])
        return json.loads(response["content"][0]["text"])

    def _connection_status(self, reason, child, reconnect=True):
        can_reconnect = reconnect and (child.poll() is not None and self.restarts < 1 or
                                      self.child is not child and self.child.poll() is None)
        return {"status": "recovery_required" if can_reconnect else "operationally_blocked",
                "delivery_allowed": False, "reason": redact(reason), "child_exit_code": child.poll(),
                "reconnects": self.restarts, "request_outcome": "unknown; no request replayed",
                "next": ("Call tyche_inspect() to reconnect to the same run, then recover saved pending receipts. Never repeat an uncertain paid lookup."
                         if can_reconnect else "Preserve the run and ledger and report this runtime blocker. Repeating research or finalization cannot repair the connection.")}

    @staticmethod
    def _errors(child, errors):
        try:
            for line in child.stderr:
                errors.append(redact(line.strip())[:512])
        except UnicodeError:
            errors.append("Child stderr contains invalid UTF-8")

    def _read(self, child, errors, error_reader):
        try:
            for line in child.stdout:
                response = json.loads(line)
                if not isinstance(response, dict) or not isinstance(response.get("result"), dict):
                    raise ValueError("Expected a JSON-RPC result object")
                result = response["result"]
                with self.lock:
                    pending = self.pending.pop(response.get("id"), None)
                if pending:
                    pending[1].set_result(result)
        except (ValueError, KeyError, TypeError) as exc:
            errors.append("Invalid child response: " + str(exc))
        finally:
            error_reader.join(timeout=1)
            with self.lock:
                # A retiring reader must not fail calls owned by its replacement.
                for ident, (owner, future) in list(self.pending.items()):
                    if owner is child:
                        future.set_exception(ConnectionError("Sandboxed tool connection closed. " + " ".join(errors)))
                        del self.pending[ident]

    def close(self):
        for child, reader, error_reader in self.children:
            try:
                child.stdin.close()
            except (OSError, ValueError):
                pass
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=5)
            reader.join(timeout=1)
            error_reader.join(timeout=1)
            child.stdout.close()
            child.stderr.close()


def serve(session, incoming=sys.stdin, outgoing=sys.stdout, *, tools=TOOLS):
    lock = threading.Lock()
    futures = {}

    def send(message):
        with lock:
            outgoing.write(json.dumps(message, ensure_ascii=True, allow_nan=False) + "\n")
            outgoing.flush()

    def call(ident, params):
        try:
            if isinstance(session, SandboxedTools):
                result = session.call(params.get("name"), params.get("arguments", {}), params.get("_meta", {}))
            else:
                result = session.call(params.get("name"), params.get("arguments", {}))
            response = {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=True, allow_nan=False)}], "isError": False}
        except Exception as exc:
            # Always answer the MCP request, including unexpected adapter/export
            # failures. Receipts preserve whether any dispatch occurred.
            response = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        send({"jsonrpc": "2.0", "id": ident, "result": response})

    # Concurrent calls use the same state locks and the session's three-call
    # provider semaphore. Closing stdin lets already dispatched calls preserve
    # their receipts; queued calls are cancelled, never silently redispatched.
    pool = ThreadPoolExecutor(max_workers=3)
    try:
        for line in incoming:
            try:
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("MCP message must be an object")
            except ValueError:
                send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Invalid JSON-RPC request"}})
                continue
            ident, method, params = message.get("id"), message.get("method"), message.get("params", {})
            if ident is not None and type(ident) not in (str, int):
                send({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request ID"}})
                continue
            if not isinstance(params, dict):
                send({"jsonrpc": "2.0", "id": ident, "error": {"code": -32602, "message": "params must be an object"}})
                continue
            if ident is None:
                if method == "notifications/cancelled":
                    future = futures.get(params.get("requestId"))
                    if future:
                        future.cancel()  # Dispatched work retains its receipt.
                continue
            if method == "initialize":
                result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}, "experimental": {"codex/sandbox-state-meta": {}}},
                          "serverInfo": {"name": "tyche", "version": "1.0.0"}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [{"name": name, "description": description, "inputSchema": schema,
                                     "annotations": {"destructiveHint": False, "openWorldHint": True}}
                                    for name, (description, schema) in tools.items()]}
            elif method == "tools/call":
                futures = {key: value for key, value in futures.items() if not value.done()}
                futures[ident] = pool.submit(call, ident, params)
                continue
            else:
                send({"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": "Method not found"}})
                continue
            send({"jsonrpc": "2.0", "id": ident, "result": result})
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        if isinstance(session, SandboxedTools):
            session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-file", type=Path, required=True)
    parser.add_argument("--read-only", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    session = ResearchTools(args.run_file, readonly=args.read_only) if args.worker else SandboxedTools(args.run_file, args.read_only)
    serve(session)
    if args.worker:
        # A clean exit can still interrupt the relay; retain its cause without
        # logging requests, credentials, or research data.
        print(f"TYCHE worker input ended; stdin_blocking={os.get_blocking(sys.stdin.fileno())}", file=sys.stderr)


if __name__ == "__main__":
    main()
