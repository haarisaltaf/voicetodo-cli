"""HTTP client for the voicetodo-server API.

stdlib only — uses urllib so the CLI works in environments where you can't
easily `pip install requests`. Multipart upload is hand-rolled.
"""

from __future__ import annotations

import json
import mimetypes
import socket
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


class ApiError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class UploadResult:
    note_id: int
    transcript: str
    todos: list[dict]
    duration: Optional[float] = None


class Api:
    def __init__(self, url: str, api_key: str = "", timeout: float = 15.0) -> None:
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    # ------------------------------------------------------------ public ops

    def health(self) -> dict:
        return self._request("GET", "/health", auth=False)

    def list_open(self) -> list[dict]:
        body = self._request("GET", "/todos")
        return list(body.get("todos", []))

    def list_completed(self) -> list[dict]:
        body = self._request("GET", "/todos?include_completed=true")
        # The server's include_completed=true returns BOTH; filter to completed.
        return [t for t in body.get("todos", []) if t.get("completed")]

    def create_todo(
        self,
        text: str,
        *,
        priority: int = 0,
        due_at: Optional[str] = None,
    ) -> dict:
        payload: dict[str, Any] = {"text": text, "priority": priority}
        if due_at is not None:
            payload["due_at"] = due_at
        return self._request("POST", "/todos", json_body=payload)

    def update_todo(
        self,
        todo_id: int,
        *,
        text: Optional[str] = None,
        completed: Optional[bool] = None,
        priority: Optional[int] = None,
        due_at: Optional[str] = None,  # "" to clear, None to leave alone
    ) -> dict:
        payload: dict[str, Any] = {}
        if text is not None:
            payload["text"] = text
        if completed is not None:
            payload["completed"] = bool(completed)
        if priority is not None:
            payload["priority"] = priority
        if due_at is not None:
            payload["due_at"] = due_at
        return self._request("PATCH", f"/todos/{todo_id}", json_body=payload)

    def delete_todo(self, todo_id: int) -> None:
        self._request("DELETE", f"/todos/{todo_id}")

    def upload_audio(
        self, audio_path: Path, *, source: str = "cli"
    ) -> UploadResult:
        body = self._upload_multipart(
            "/audio",
            file_field="audio",
            file_path=audio_path,
            extra_fields={"source": source},
        )
        return UploadResult(
            note_id=int(body.get("note_id", -1)),
            transcript=str(body.get("transcript", "")),
            todos=list(body.get("todos", [])),
            duration=body.get("duration"),
        )

    # ------------------------------------------------------------ internals

    def _headers(self, auth: bool, extra: Optional[dict] = None) -> dict:
        h = {"Accept": "application/json"}
        if auth and self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        if extra:
            h.update(extra)
        return h

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict] = None,
        auth: bool = True,
    ) -> dict:
        if not self.url:
            raise ApiError("Server URL not configured. Run `voicetodo configure`.")
        full = self.url + path
        data: Optional[bytes] = None
        headers = self._headers(auth)
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(full, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            raw = e.read() or b""
            text = raw.decode("utf-8", errors="replace")
            raise ApiError(f"HTTP {e.code}: {text.strip() or e.reason}", e.code)
        except (urllib.error.URLError, socket.timeout) as e:
            raise ApiError(f"Network error: {e}") from e

        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise ApiError(f"Bad JSON from server: {e}") from e

    def _upload_multipart(
        self,
        path: str,
        *,
        file_field: str,
        file_path: Path,
        extra_fields: Optional[dict[str, str]] = None,
    ) -> dict:
        # Build the multipart body in memory. Audio memos are tiny (a 30s
        # clip in AAC is about 200 KB); not worth the streaming complexity.
        boundary = uuid.uuid4().hex
        nl = b"\r\n"
        parts: list[bytes] = []

        for k, v in (extra_fields or {}).items():
            parts.extend([
                f"--{boundary}".encode(),
                f'Content-Disposition: form-data; name="{k}"'.encode(),
                b"",
                str(v).encode("utf-8"),
            ])

        ctype, _ = mimetypes.guess_type(file_path.name)
        if not ctype:
            ctype = "application/octet-stream"
        parts.extend([
            f"--{boundary}".encode(),
            (
                f'Content-Disposition: form-data; name="{file_field}"; '
                f'filename="{file_path.name}"'
            ).encode(),
            f"Content-Type: {ctype}".encode(),
            b"",
            file_path.read_bytes(),
        ])
        parts.append(f"--{boundary}--".encode())
        parts.append(b"")
        body = nl.join(parts)

        headers = self._headers(True, {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        })

        req = urllib.request.Request(
            self.url + path, data=body, method="POST", headers=headers
        )
        # Audio uploads can take a while because the server does Whisper STT
        # synchronously. Allow up to two minutes.
        try:
            with urllib.request.urlopen(req, timeout=max(self.timeout, 120.0)) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            raw = e.read() or b""
            text = raw.decode("utf-8", errors="replace")
            raise ApiError(f"HTTP {e.code}: {text.strip() or e.reason}", e.code)
        except (urllib.error.URLError, socket.timeout) as e:
            raise ApiError(f"Network error: {e}") from e

        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise ApiError(f"Bad JSON from server: {e}") from e
