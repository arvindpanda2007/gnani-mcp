"""Render-deployable streamable-HTTP MCP server wrapping gnani-vachana."""
from __future__ import annotations
import base64
import hmac
import json
import logging
import os
import re
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse, Response
from gnani.stt import GnaniSTTClient
from gnani.tts import AudioConfig, GnaniTTSClient, SpeakerEmbedding
logger = logging.getLogger("gnani_mcp_remote")
DEFAULT_PORT = 8000
MAX_AUDIO_BYTES = 25 * 1024 * 1024
ALLOWED_AUDIO_SUFFIXES = (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac")
PUBLIC_PATHS = frozenset({"/", "/health"})
mcp = FastMCP(
    "gnani-mcp-remote",
    instructions=(
        "Remote MCP server for Gnani AI. Use transcribe for speech-to-text, "
        "synthesize for text-to-speech, and synthesize_cloned when you already "
        "have a Gnani speaker embedding. list_voices and list_stt_languages "
        "show catalogs from the official gnani-vachana SDK."
    ),
    stateless_http=True,
    host="0.0.0.0",
    port=int(os.environ.get("PORT", str(DEFAULT_PORT))),
    streamable_http_path="/mcp",
)
def _require_api_key() -> str:
    key = os.environ.get("GNANI_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "GNANI_API_KEY is missing. Set it in the environment "
            "(Render dashboard or a local .env that is not committed)."
        )
    return key
def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(k): _json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_json_safe(v) for v in value]
        return str(value)
def _guess_filename(name: str | None, url: str | None) -> str:
    candidate = (name or "").strip()
    if not candidate and url:
        candidate = os.path.basename(urlparse(url).path)
    if not candidate:
        return "audio.wav"
    lower = candidate.lower()
    if not any(lower.endswith(ext) for ext in ALLOWED_AUDIO_SUFFIXES):
        return f"{candidate}.wav"
    return candidate
def _decode_base64_audio(audio_base64: str) -> bytes:
    payload = audio_base64.strip()
    if payload.startswith("data:") and "," in payload:
        payload = payload.split(",", 1)[1]
    try:
        data = base64.b64decode(payload, validate=False)
    except Exception as exc:
        raise ValueError(f"audio_base64 is not valid base64: {exc}") from exc
    if not data:
        raise ValueError("audio_base64 decoded to empty bytes")
    if len(data) > MAX_AUDIO_BYTES:
        raise ValueError(f"Audio exceeds {MAX_AUDIO_BYTES} byte limit")
    return data
def _download_audio(url: str) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("audio_url must be an http or https URL")
    request = Request(url, headers={"User-Agent": "gnani-mcp-remote/0.1"})
    with urlopen(request, timeout=60) as response:  # noqa: S310
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_AUDIO_BYTES:
                raise ValueError(f"Audio exceeds {MAX_AUDIO_BYTES} byte limit")
            chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise ValueError("Downloaded audio was empty")
    return data
def _parse_shape(shape: list[int] | str) -> list[int]:
    if isinstance(shape, list):
        return [int(x) for x in shape]
    text = shape.strip()
    if not text:
        return [1, 768]
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [int(x) for x in parsed]
    except json.JSONDecodeError:
        pass
    numbers = [int(x) for x in re.findall(r"-?\d+", text)]
    if not numbers:
        raise ValueError("embedding_shape must be a list of integers, e.g. [1, 768]")
    return numbers
def _audio_config(
    container: str,
    sample_rate: int,
    encoding: str,
    bitrate: str | None,
) -> AudioConfig:
    kwargs: dict[str, Any] = {
        "sample_rate": sample_rate,
        "encoding": encoding,
        "container": container,
    }
    if bitrate:
        kwargs["bitrate"] = bitrate
    return AudioConfig(**kwargs)
class OptionalBearerAuthMiddleware(BaseHTTPMiddleware):
    """If MCP_AUTH_TOKEN is set, require Bearer auth on MCP routes."""
    async def dispatch(self, request: StarletteRequest, call_next) -> Response:
        expected = os.environ.get("MCP_AUTH_TOKEN", "").strip()
        if not expected:
            return await call_next(request)
        if request.url.path in PUBLIC_PATHS and request.method == "GET":
            return await call_next(request)
        header = request.headers.get("authorization", "")
        prefix = "Bearer "
        if not header.startswith(prefix):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        provided = header[len(prefix) :].strip().encode("utf-8")
        expected_bytes = expected.encode("utf-8")
        if len(provided) != len(expected_bytes) or not hmac.compare_digest(
            provided, expected_bytes
        ):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await call_next(request)
async def health(_: StarletteRequest) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "gnani-mcp-remote"})
"""Render-deployable streamable-HTTP MCP server wrapping gnani-vachana."""
from __future__ import annotations
import base64
import hmac
import json
import logging
import os
import re
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse, Response
from gnani.stt import GnaniSTTClient
from gnani.tts import AudioConfig, GnaniTTSClient, SpeakerEmbedding
logger = logging.getLogger("gnani_mcp_remote")
DEFAULT_PORT = 8000
MAX_AUDIO_BYTES = 25 * 1024 * 1024
ALLOWED_AUDIO_SUFFIXES = (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac")
PUBLIC_PATHS = frozenset({"/", "/health"})
mcp = FastMCP(
    "gnani-mcp-remote",
    instructions=(
        "Remote MCP server for Gnani AI. Use transcribe for speech-to-text, "
        "synthesize for text-to-speech, and synthesize_cloned when you already "
        "have a Gnani speaker embedding. list_voices and list_stt_languages "
        "show catalogs from the official gnani-vachana SDK."
    ),
    stateless_http=True,
    host="0.0.0.0",
    port=int(os.environ.get("PORT", str(DEFAULT_PORT))),
    streamable_http_path="/mcp",
)
def _require_api_key() -> str:
    key = os.environ.get("GNANI_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "GNANI_API_KEY is missing. Set it in the environment "
            "(Render dashboard or a local .env that is not committed)."
        )
    return key
def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(k): _json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_json_safe(v) for v in value]
        return str(value)
def _guess_filename(name: str | None, url: str | None) -> str:
    candidate = (name or "").strip()
    if not candidate and url:
        candidate = os.path.basename(urlparse(url).path)
    if not candidate:
        return "audio.wav"
    lower = candidate.lower()
    if not any(lower.endswith(ext) for ext in ALLOWED_AUDIO_SUFFIXES):
        return f"{candidate}.wav"
    return candidate
def _decode_base64_audio(audio_base64: str) -> bytes:
    payload = audio_base64.strip()
    if payload.startswith("data:") and "," in payload:
        payload = payload.split(",", 1)[1]
    try:
        data = base64.b64decode(payload, validate=False)
    except Exception as exc:
        raise ValueError(f"audio_base64 is not valid base64: {exc}") from exc
    if not data:
        raise ValueError("audio_base64 decoded to empty bytes")
    if len(data) > MAX_AUDIO_BYTES:
        raise ValueError(f"Audio exceeds {MAX_AUDIO_BYTES} byte limit")
    return data
def _download_audio(url: str) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("audio_url must be an http or https URL")
    request = Request(url, headers={"User-Agent": "gnani-mcp-remote/0.1"})
    with urlopen(request, timeout=60) as response:  # noqa: S310
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_AUDIO_BYTES:
                raise ValueError(f"Audio exceeds {MAX_AUDIO_BYTES} byte limit")
            chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise ValueError("Downloaded audio was empty")
    return data
def _parse_shape(shape: list[int] | str) -> list[int]:
    if isinstance(shape, list):
        return [int(x) for x in shape]
    text = shape.strip()
    if not text:
        return [1, 768]
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [int(x) for x in parsed]
    except json.JSONDecodeError:
        pass
    numbers = [int(x) for x in re.findall(r"-?\d+", text)]
    if not numbers:
        raise ValueError("embedding_shape must be a list of integers, e.g. [1, 768]")
    return numbers
def _audio_config(
    container: str,
    sample_rate: int,
    encoding: str,
    bitrate: str | None,
) -> AudioConfig:
    kwargs: dict[str, Any] = {
        "sample_rate": sample_rate,
        "encoding": encoding,
        "container": container,
    }
    if bitrate:
        kwargs["bitrate"] = bitrate
    return AudioConfig(**kwargs)
class OptionalBearerAuthMiddleware(BaseHTTPMiddleware):
    """If MCP_AUTH_TOKEN is set, require Bearer auth on MCP routes."""
    async def dispatch(self, request: StarletteRequest, call_next) -> Response:
        expected = os.environ.get("MCP_AUTH_TOKEN", "").strip()
        if not expected:
            return await call_next(request)
        if request.url.path in PUBLIC_PATHS and request.method == "GET":
            return await call_next(request)
        header = request.headers.get("authorization", "")
        prefix = "Bearer "
        if not header.startswith(prefix):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        provided = header[len(prefix) :].strip().encode("utf-8")
        expected_bytes = expected.encode("utf-8")
        if len(provided) != len(expected_bytes) or not hmac.compare_digest(
            provided, expected_bytes
        ):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await call_next(request)
async def health(_: StarletteRequest) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "gnani-mcp-remote"})
