"""Servidor HTTP local (em thread) para os testes de url_safety, sem depender da internet."""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class _Handler(BaseHTTPRequestHandler):
    routes: dict[str, tuple[int, dict[str, str]]] = {}

    def do_HEAD(self):  # noqa: N802 - nome exigido pela biblioteca padrão
        self._respond()

    def do_GET(self):  # noqa: N802
        self._respond()

    def _respond(self):
        status, headers = self.routes.get(self.path, (404, {}))
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()

    def log_message(self, *args):  # silencia o log padrão do http.server nos testes
        pass


class LocalHttpServer:
    """Uso: with LocalHttpServer({"/a": (200, {})}) as server: server.url("/a")."""

    def __init__(self, routes: dict[str, tuple[int, dict[str, str]]]) -> None:
        handler = type("Handler", (_Handler,), {"routes": routes})
        self._httpd = HTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def __enter__(self) -> "LocalHttpServer":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)

    def url(self, path: str = "/") -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}{path}"
