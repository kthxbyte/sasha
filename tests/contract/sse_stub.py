"""Scriptable OpenAI-compatible SSE stub server — Tier 1 test infrastructure.

Immutable to the implementer (lives under tests/contract/). Used by the
contract tests of Tasks 2.1, 2.3 and 3.3. Stdlib only, offline-safe.

Usage:
    with StubServer([
        {"kind": "text", "text": "Hello!"},
        {"kind": "tool_call", "id": "call_1", "name": "run_command",
         "arguments": {"command": "ls", "description": "list"}, "fragments": 3},
    ]) as stub:
        ...  # point a ChatClient at stub.base_url
    stub.requests  # -> [{"headers": {...lowercase keys...}, "body": {...}}, ...]

Script entry kinds:
    {"kind": "text", "text": str, "chunk_size": int = 5}
        Streams the text as multiple content deltas, then finish/[DONE].
    {"kind": "tool_call", "id": str, "name": str,
     "arguments": dict | str, "fragments": int = 3, "text": str = ""}
        Optional text deltas first, then one tool call whose arguments JSON
        is split across `fragments` SSE chunks. Pass `arguments` as a raw
        string to script malformed-JSON arguments.
    {"kind": "http_error", "status": int}
        Plain HTTP error response, no stream.
    {"kind": "disconnect", "text": str = "partial"}
        Starts streaming, sends one content delta, then closes the
        connection without finish_reason or [DONE].
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class StubServer:
    def __init__(self, script):
        self._script = list(script)
        self._lock = threading.Lock()
        self.requests = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    body = {"_unparsed": raw.decode("utf-8", errors="replace")}
                headers = {k.lower(): v for k, v in self.headers.items()}
                with server._lock:
                    server.requests.append({"headers": headers, "body": body})
                    entry = server._script.pop(0) if server._script else None

                if entry is None:
                    self._plain(500, b'{"error":"stub script exhausted"}')
                    return
                kind = entry["kind"]
                if kind == "http_error":
                    self._plain(entry.get("status", 500), b'{"error":"scripted"}')
                elif kind == "text":
                    self._sse_start()
                    self._stream_text(entry["text"], entry.get("chunk_size", 5))
                    self._sse("{\"choices\":[{\"index\":0,\"delta\":{},"
                              "\"finish_reason\":\"stop\"}]}")
                    self._done()
                elif kind == "tool_call":
                    self._sse_start()
                    if entry.get("text"):
                        self._stream_text(entry["text"], entry.get("chunk_size", 5))
                    args = entry["arguments"]
                    args_str = args if isinstance(args, str) else json.dumps(args)
                    n = max(1, entry.get("fragments", 3))
                    step = max(1, -(-len(args_str) // n))
                    pieces = [args_str[i:i + step]
                              for i in range(0, len(args_str), step)] or [""]
                    first = {"index": 0, "id": entry["id"], "type": "function",
                             "function": {"name": entry["name"],
                                          "arguments": pieces[0]}}
                    self._delta({"tool_calls": [first]})
                    for piece in pieces[1:]:
                        self._delta({"tool_calls": [
                            {"index": 0, "function": {"arguments": piece}}]})
                    self._sse("{\"choices\":[{\"index\":0,\"delta\":{},"
                              "\"finish_reason\":\"tool_calls\"}]}")
                    self._done()
                elif kind == "disconnect":
                    self._sse_start()
                    self._delta({"content": entry.get("text", "partial")})
                    self.wfile.flush()
                    self.connection.close()
                else:
                    self._plain(500, b'{"error":"unknown script kind"}')

            # -- helpers ------------------------------------------------
            def _plain(self, status, payload):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _sse_start(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()

            def _sse(self, data_str):
                self.wfile.write(f"data: {data_str}\n\n".encode("utf-8"))
                self.wfile.flush()

            def _delta(self, delta):
                self._sse(json.dumps(
                    {"choices": [{"index": 0, "delta": delta}]}))

            def _stream_text(self, text, chunk_size):
                for i in range(0, len(text), max(1, chunk_size)):
                    self._delta({"content": text[i:i + max(1, chunk_size)]})

            def _done(self):
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                self.connection.close()

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, daemon=True)

    @property
    def port(self):
        return self._httpd.server_address[1]

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}/v1"

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._httpd.shutdown()
        self._httpd.server_close()
        return False


def refused_base_url():
    """A base_url on a port that is guaranteed closed right now."""
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}/v1"
