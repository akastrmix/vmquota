from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import AppConfig
from .db import StateDB
from .presentation import build_usage_snapshot, render_usage_brief, render_usage_text


def serve_api(config: AppConfig) -> int:
    handler = make_handler(config)
    server = ThreadingHTTPServer((config.api_bind_host, config.api_bind_port), handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


def make_handler(config: AppConfig) -> type[BaseHTTPRequestHandler]:
    class VmQuotaApiHandler(BaseHTTPRequestHandler):
        server_version = "vmquota-api/0.1"

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/healthz":
                self._send_text(HTTPStatus.OK, "ok\n")
                return
            if parsed.path not in {"/v1/usage", "/v1/usage/text", "/v1/usage/brief"}:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return

            query = parse_qs(parsed.query, keep_blank_values=True)
            uuid_values = query.get("uuid", [])
            if len(uuid_values) != 1:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "missing uuid" if not uuid_values else "duplicate uuid"})
                return
            uuid = uuid_values[0].strip()
            if not uuid:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "missing uuid"})
                return

            snapshot = lookup_snapshot(config, uuid)
            if snapshot is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "vm not found"})
                return

            if parsed.path == "/v1/usage/text":
                body = render_usage_text(snapshot) + "\n"
                self._send_text(HTTPStatus.OK, body)
                return
            if parsed.path == "/v1/usage/brief":
                body = render_usage_brief(snapshot) + "\n"
                self._send_text(HTTPStatus.OK, body)
                return
            self._send_json(HTTPStatus.OK, snapshot)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_text(self, status: HTTPStatus, text: str) -> None:
            body = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return VmQuotaApiHandler


def lookup_snapshot(config: AppConfig, bios_uuid: str) -> dict[str, object] | None:
    with StateDB(config.state_db) as db:
        vm = db.get_vm_by_uuid(bios_uuid)
        if vm is None:
            return None
        return build_usage_snapshot(vm, config.timezone)
