from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from app.youtube import upload_short


class UploadHandler(BaseHTTPRequestHandler):
    init_headers: dict[str, str] = {}
    uploaded_size = 0

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length", "0"))
        self.rfile.read(length)
        self.__class__.init_headers = {key.lower(): value for key, value in self.headers.items()}
        self.send_response(200)
        self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/session")
        self.end_headers()

    def do_PUT(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        self.__class__.uploaded_size = len(body)
        payload = json.dumps({"id": "abc123"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args) -> None:  # noqa: ANN002
        return


class UploadTests(unittest.TestCase):
    def test_resumable_upload_sends_file(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), UploadHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "short.mp4"
                path.write_bytes(b"x" * 4096)
                with patch(
                    "app.youtube.UPLOAD_URL",
                    f"http://127.0.0.1:{server.server_port}/upload",
                ):
                    video_id = upload_short(
                        path=path,
                        metadata={"snippet": {"title": "Test #Shorts"}},
                        access_token="token",
                        max_bytes=10_000,
                    )
            self.assertEqual(video_id, "abc123")
            self.assertEqual(UploadHandler.uploaded_size, 4096)
            self.assertEqual(UploadHandler.init_headers.get("x-upload-content-length"), "4096")
            self.assertEqual(UploadHandler.init_headers.get("authorization"), "Bearer token")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
