"""Minimal threaded HTTP layer for the desktop sidecar.

Deliberately dependency-free: the package already ships with a hand-written MCP
protocol implementation for the same reason. What is needed here is narrow --
routing, bearer authentication, JSON and binary bodies, and server-sent events.

Design notes
------------
* ``Response.body`` is either ``bytes`` or an iterator of ``bytes``. Streaming
  responses (SSE, large binaries) use the iterator form so nothing is buffered.
* :class:`OpenRadError` subclasses map to stable HTTP statuses, mirroring the
  CLI exit codes so a caller can reason about failures the same way.
* Only loopback origins are accepted; the token is a capability, checked in
  constant time.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import socket
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Pattern, Tuple, Union
from urllib.parse import parse_qs, unquote, urlparse

from ..errors import (
    EXIT_GEOMETRY,
    EXIT_INPUT,
    EXIT_INTEGRITY,
    EXIT_QUANTITATION,
    EXIT_USAGE,
    EXIT_VALIDATION,
    OpenRadError,
)

log = logging.getLogger("openrad.server")

Body = Union[bytes, Iterator[bytes]]

#: Exit-code -> HTTP status. Keeps the CLI contract legible over the wire.
STATUS_FOR_EXIT: Dict[int, int] = {
    EXIT_USAGE: 400,
    EXIT_INPUT: 404,
    EXIT_GEOMETRY: 422,
    EXIT_VALIDATION: 422,
    EXIT_QUANTITATION: 422,
    EXIT_INTEGRITY: 409,
}

ORIGIN_OK: Tuple[Pattern[str], ...] = (
    re.compile(r"^tauri://localhost$"),
    re.compile(r"^https?://tauri\.localhost$"),
    re.compile(r"^http://localhost(:\d+)?$"),
    re.compile(r"^http://127\.0\.0\.1(:\d+)?$"),
)

MAX_BODY = 64 * 1024 * 1024


class HttpError(Exception):
    """A failure that is about the transport rather than about radiology."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class Request:
    def __init__(self, method: str, path: str, query: Dict[str, List[str]],
                 headers: Dict[str, str], body: bytes, params: Dict[str, str]) -> None:
        self.method = method
        self.path = path
        self.query = query
        self.headers = headers
        self.body = body
        self.params = params

    # -- accessors ---------------------------------------------------------
    def json(self) -> Dict[str, Any]:
        if not self.body:
            return {}
        try:
            data = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise HttpError(400, f"Malformed JSON body: {e}") from e
        if not isinstance(data, dict):
            raise HttpError(400, "JSON body must be an object")
        return data

    def q(self, key: str, default: Optional[str] = None) -> Optional[str]:
        values = self.query.get(key)
        return values[0] if values else default

    def q_int(self, key: str, default: Optional[int] = None) -> Optional[int]:
        raw = self.q(key)
        if raw is None or raw == "":
            return default
        try:
            return int(raw)
        except ValueError as e:
            raise HttpError(400, f"Query parameter {key!r} must be an integer") from e

    def q_float(self, key: str, default: Optional[float] = None) -> Optional[float]:
        raw = self.q(key)
        if raw is None or raw == "":
            return default
        try:
            return float(raw)
        except ValueError as e:
            raise HttpError(400, f"Query parameter {key!r} must be a number") from e

    def q_bool(self, key: str, default: bool = False) -> bool:
        raw = self.q(key)
        if raw is None:
            return default
        return raw.lower() in ("1", "true", "yes", "on")


class Response:
    __slots__ = ("status", "headers", "body")

    def __init__(self, status: int = 200, headers: Optional[Dict[str, str]] = None, body: Body = b"") -> None:
        self.status = status
        self.headers = headers or {}
        self.body = body


def json_response(payload: Any, status: int = 200, headers: Optional[Dict[str, str]] = None) -> Response:
    raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    head = {"Content-Type": "application/json; charset=utf-8"}
    head.update(headers or {})
    return Response(status, head, raw)


def binary_response(data: bytes, content_type: str, headers: Optional[Dict[str, str]] = None) -> Response:
    head = {"Content-Type": content_type}
    head.update(headers or {})
    return Response(200, head, data)


def sse_response(events: Iterable[Tuple[str, Any]]) -> Response:
    """Server-sent events. ``events`` yields ``(event_name, json_payload)``."""

    def stream() -> Iterator[bytes]:
        yield b": open\n\n"
        for name, payload in events:
            chunk = f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
            yield chunk.encode("utf-8")

    return Response(200, {"Content-Type": "text/event-stream; charset=utf-8",
                          "Cache-Control": "no-store",
                          "X-Accel-Buffering": "no"}, stream())


Handler = Callable[[Request], Response]


class Router:
    """Pattern router. ``/series/{uid}/slice/{index}`` binds named path params."""

    _SEGMENT = re.compile(r"\{([a-z_]+)(\*?)\}")

    def __init__(self) -> None:
        self._routes: List[Tuple[str, Pattern[str], Handler]] = []

    def add(self, method: str, pattern: str, handler: Handler) -> None:
        self._routes.append((method.upper(), self._compile(pattern), handler))

    def route(self, method: str, pattern: str) -> Callable[[Handler], Handler]:
        def decorate(fn: Handler) -> Handler:
            self.add(method, pattern, fn)
            return fn

        return decorate

    @classmethod
    def _compile(cls, pattern: str) -> Pattern[str]:
        def repl(m: re.Match[str]) -> str:
            name, greedy = m.group(1), m.group(2)
            return f"(?P<{name}>.+)" if greedy else f"(?P<{name}>[^/]+)"

        return re.compile("^" + cls._SEGMENT.sub(repl, pattern) + "$")

    def resolve(self, method: str, path: str) -> Tuple[Optional[Handler], Dict[str, str], bool]:
        """Return ``(handler, params, path_exists_under_other_method)``."""
        seen_path = False
        for verb, rx, handler in self._routes:
            m = rx.match(path)
            if not m:
                continue
            seen_path = True
            if verb == method:
                return handler, {k: unquote(v) for k, v in m.groupdict().items()}, True
        return None, {}, seen_path


class Server:
    """Loopback HTTP server with a bearer token and an origin allow-list."""

    def __init__(self, router: Router, token: str, host: str = "127.0.0.1", port: int = 0,
                 public: Iterable[str] = ("/health",)) -> None:
        self.router = router
        self.token = token
        self.public = set(public)
        self._httpd = ThreadingHTTPServer((host, port), self._make_handler())
        self._httpd.daemon_threads = True
        self._httpd.timeout = 1.0
        self.host, self.port = self._httpd.server_address[0], self._httpd.server_address[1]
        self._thread: Optional[threading.Thread] = None

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._httpd.serve_forever, kwargs={"poll_interval": 0.2},
                                        name="openrad-http", daemon=True)
        self._thread.start()

    def serve_forever(self) -> None:
        self._httpd.serve_forever(poll_interval=0.2)

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    # -- request handling --------------------------------------------------
    def _authorised(self, headers: Dict[str, str], path: str) -> bool:
        if path in self.public:
            return True
        supplied = headers.get("authorization", "")
        prefix = "bearer "
        if not supplied.lower().startswith(prefix):
            supplied = headers.get("x-openrad-token", "")
        else:
            supplied = supplied[len(prefix):]
        return bool(supplied) and secrets.compare_digest(supplied, self.token)

    @staticmethod
    def _origin_ok(origin: str) -> bool:
        return not origin or any(rx.match(origin) for rx in ORIGIN_OK)

    def _dispatch(self, method: str, raw_path: str, headers: Dict[str, str], body: bytes) -> Response:
        parsed = urlparse(raw_path)
        path = unquote(parsed.path)
        if not self._origin_ok(headers.get("origin", "")):
            return json_response({"error": "Origin not allowed", "kind": "Forbidden"}, 403)
        if not self._authorised(headers, path):
            return json_response({"error": "Missing or invalid bearer token", "kind": "Unauthorized"}, 401)
        handler, params, path_exists = self.router.resolve(method, path)
        if handler is None:
            status, message = (405, "Method not allowed") if path_exists else (404, f"No route for {path}")
            return json_response({"error": message, "kind": "NotFound"}, status)
        request = Request(method, path, parse_qs(parsed.query, keep_blank_values=True), headers, body, params)
        try:
            return handler(request)
        except HttpError as e:
            return json_response({"error": e.message, "kind": "HttpError"}, e.status)
        except OpenRadError as e:
            status = STATUS_FOR_EXIT.get(e.exit_code, 500)
            return json_response({"error": e.message, "kind": type(e).__name__, "exit_code": e.exit_code}, status)
        except Exception as e:  # pragma: no cover - unexpected bug, keep the trace
            log.error("unhandled error on %s %s: %s\n%s", method, path, e, traceback.format_exc())
            return json_response({"error": str(e), "kind": "InternalError"}, 500)

    def _make_handler(self) -> type:
        server = self

        class RequestHandler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "openrad-server"
            sys_version = ""

            # Headers and body leave in one write, with Nagle off.
            #
            # The default handler is unbuffered, so every response is at least
            # two `sendall` calls: a few hundred bytes of header, then the body.
            # That is the write-write-read pattern that Nagle and delayed
            # acknowledgement combine to punish -- the body waits for an ACK of
            # the header that the peer is in no hurry to send. On a machine
            # where that timer is long it cost about two hundred milliseconds
            # per response, which is most of a second on every image. Buffering
            # the response into a single segment removes the interaction
            # entirely.
            wbufsize = 64 * 1024
            disable_nagle_algorithm = True

            def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
                log.debug("%s - %s", self.address_string(), fmt % args)

            # -- helpers ---------------------------------------------------
            def _headers(self) -> Dict[str, str]:
                return {k.lower(): v for k, v in self.headers.items()}

            def _read_body(self) -> bytes:
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    raise HttpError(400, "Invalid Content-Length") from None
                if length > MAX_BODY:
                    raise HttpError(413, "Request body too large")
                return self.rfile.read(length) if length else b""

            def _cors(self, origin: str) -> Dict[str, str]:
                if not origin or not server._origin_ok(origin):
                    return {}
                return {"Access-Control-Allow-Origin": origin,
                        "Access-Control-Allow-Headers": "Authorization, Content-Type, Range, X-Openrad-Token",
                        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
                        "Access-Control-Expose-Headers": ("Accept-Ranges, Content-Range, "
                                                          "X-Sop-Uid, X-Z-Mm, X-Mm-Per-Px, X-Shape, X-Dtype, "
                                                          "X-Instance, X-Index, X-Series-Uid, X-Study-Uid, X-Plane"),
                        "Access-Control-Max-Age": "600"}

            def _byte_range(self, total: int) -> Optional[Tuple[int, int]]:
                """Parse a single ``Range: bytes=a-b``, or ``None`` to send it whole.

                Only the one-range form is honoured, which is all a viewer asks
                for. Anything malformed, unsatisfiable or multi-range falls back
                to the full body: a partial response nobody asked for is worse
                than a large one.
                """
                raw = (self.headers.get("Range") or "").strip()
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", raw)
                if not match or total <= 0:
                    return None
                first, last = match.group(1), match.group(2)
                if not first and not last:
                    return None
                if not first:  # a suffix range: the final N bytes
                    length = int(last)
                    if length <= 0:
                        return None
                    return max(0, total - length), total - 1
                start = int(first)
                if start >= total:
                    return None
                end = min(int(last), total - 1) if last else total - 1
                return (start, end) if end >= start else None

            def _emit(self, response: Response, origin: str, head_only: bool = False) -> None:
                cors = self._cors(origin)
                body = response.body
                streaming = not isinstance(body, bytes)
                status = response.status
                extra: Dict[str, str] = {}

                # Byte ranges on binary bodies.
                #
                # This exists for a measured reason rather than for
                # completeness. On some machines a loopback write larger than
                # one 16 KiB segment stalls for roughly 230 ms per block, so a
                # half-megabyte slice takes nearly four seconds while the same
                # bytes moved in fourteen-kilobyte request/response turns take
                # under two milliseconds. Serving ranges lets a viewer ask for a
                # slice in pieces small enough to stay under that cliff, and
                # costs nothing on a machine that does not have it.
                if not streaming and status == 200:
                    extra["Accept-Ranges"] = "bytes"
                    span = self._byte_range(len(body))
                    if span is not None:
                        start, end = span
                        extra["Content-Range"] = f"bytes {start}-{end}/{len(body)}"
                        body = body[start : end + 1]
                        status = 206

                self.send_response(status)
                for key, value in list(response.headers.items()) + list(cors.items()) + list(extra.items()):
                    self.send_header(key, value)
                if streaming:
                    self.send_header("Transfer-Encoding", "chunked")
                else:
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if head_only:
                    return
                try:
                    if streaming:
                        for chunk in body:  # type: ignore[union-attr]
                            self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                            self.wfile.flush()
                        self.wfile.write(b"0\r\n\r\n")
                    else:
                        self.wfile.write(body)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, socket.timeout):
                    log.debug("client disconnected during response")

            def _serve(self, method: str) -> None:
                origin = self.headers.get("Origin", "")
                try:
                    body = self._read_body()
                except HttpError as e:
                    self._emit(json_response({"error": e.message, "kind": "HttpError"}, e.status), origin)
                    return
                response = server._dispatch(method, self.path, self._headers(), body)
                self._emit(response, origin, head_only=method == "HEAD")

            # -- verbs -----------------------------------------------------
            def do_GET(self) -> None:  # noqa: N802
                self._serve("GET")

            def do_HEAD(self) -> None:  # noqa: N802
                self._serve("HEAD")

            def do_POST(self) -> None:  # noqa: N802
                self._serve("POST")

            def do_PUT(self) -> None:  # noqa: N802
                self._serve("PUT")

            def do_DELETE(self) -> None:  # noqa: N802
                self._serve("DELETE")

            def do_OPTIONS(self) -> None:  # noqa: N802
                origin = self.headers.get("Origin", "")
                self._emit(Response(204, {}, b""), origin)

        return RequestHandler
