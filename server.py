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
def _register_health_routes() -> None:
    custom_route = getattr(mcp, "custom_route", None)
    if custom_route is None:
        return
    try:
        custom_route("/", health, methods=["GET"])
        custom_route("/health", health, methods=["GET"])
    except TypeError:
        custom_route("/", methods=["GET"])(health)
        custom_route("/health", methods=["GET"])(health)
_register_health_routes()
@mcp.tool()
def transcribe(
    language_code: str = "hi-IN",
    audio_url: str | None = None,
    audio_base64: str | None = None,
    filename: str | None = None,
    output_format: str = "verbatim",
    itn_native_numerals: bool = False,
) -> dict[str, Any]:
    """Speech-to-text via Gnani STT REST (GnaniSTTClient.transcribe_bytes).
    Provide exactly one of audio_url (http/https) or audio_base64. Use BCP-47
    language codes such as hi-IN or en-IN. output_format is verbatim (raw)
    or transcribe (ITN, mainly hi-IN/en-IN).
    """
    _require_api_key()
    has_url = bool(audio_url and audio_url.strip())
    has_b64 = bool(audio_base64 and audio_base64.strip())
    if has_url == has_b64:
        raise ValueError("Provide exactly one of audio_url or audio_base64")
    audio_bytes = (
        _download_audio(audio_url.strip()) if has_url else _decode_base64_audio(audio_base64 or "")
    )
    name = _guess_filename(filename, audio_url if has_url else None)
    client = GnaniSTTClient()
    result = client.transcribe_bytes(
        audio_bytes,
        filename=name,
        language_code=language_code,
        format=output_format,
        itn_native_numerals=itn_native_numerals,
    )
    return _json_safe(result)
@mcp.tool()
def synthesize(
    text: str,
    voice: str = "Pranav",
    model: str = "timbre-v2.0",
    language: str | None = None,
    speed: float | None = None,
    container: str = "mp3",
    sample_rate: int = 48000,
    encoding: str = "linear_pcm",
    bitrate: str | None = "128k",
) -> dict[str, Any]:
    """Text-to-speech via Gnani TTS REST (GnaniTTSClient.synthesize).
    Returns base64-encoded audio. For timbre-v2.5, language (e.g. hi-IN) and
    speed (about 0.85–1.15) are supported. voice is required unless using
    synthesize_cloned with a speaker embedding.
    """
    _require_api_key()
    if not text.strip():
        raise ValueError("text is required")
    cfg = _audio_config(container, sample_rate, encoding, bitrate if container == "mp3" else None)
    client = GnaniTTSClient()
    audio = client.synthesize(
        text,
        voice=voice,
        model=model,
        language=language,
        speed=speed,
        audio_config=cfg,
    )
    return {
        "format": container,
        "sample_rate": sample_rate,
        "voice": voice,
        "model": model,
        "audio_base64": base64.b64encode(audio).decode("ascii"),
        "byte_length": len(audio),
    }
@mcp.tool()
def synthesize_cloned(
    text: str,
    embedding: str,
    embedding_shape: list[int] | str = "[1, 768]",
    embedding_dtype: str = "torch.bfloat16",
    model: str = "timbre-v2.0",
    language: str | None = None,
    speed: float | None = None,
    container: str = "mp3",
    sample_rate: int = 48000,
    encoding: str = "linear_pcm",
    bitrate: str | None = "128k",
) -> dict[str, Any]:
    """TTS with a Gnani speaker embedding (SDK voice-clone parameter).
    The gnani-vachana SDK does not create embeddings from a sample clip.
    Pass an embedding string plus tensor shape/dtype from Gnani. This maps
    to GnaniTTSClient.synthesize(..., speaker_embedding=SpeakerEmbedding(...)).
    """
    _require_api_key()
    if not text.strip():
        raise ValueError("text is required")
    if not embedding.strip():
        raise ValueError("embedding is required")
    shape = _parse_shape(embedding_shape)
    cfg = _audio_config(container, sample_rate, encoding, bitrate if container == "mp3" else None)
    client = GnaniTTSClient()
    audio = client.synthesize(
        text,
        voice=None,
        model=model,
        language=language,
        speed=speed,
        audio_config=cfg,
        speaker_embedding=SpeakerEmbedding(
            embedding=embedding.strip(),
            shape=shape,
            dtype=embedding_dtype,
        ),
    )
    return {
        "format": container,
        "sample_rate": sample_rate,
        "model": model,
        "used_speaker_embedding": True,
        "audio_base64": base64.b64encode(audio).decode("ascii"),
        "byte_length": len(audio),
    }
@mcp.tool()
def list_voices(model: str = "timbre-v2.0") -> dict[str, Any]:
    """List TTS voices for a Timbre model from GnaniTTSClient.supported_voices."""
    voices = sorted(GnaniTTSClient.supported_voices(model=model))
    return {"model": model, "voices": voices}
@mcp.tool()
def list_stt_languages() -> dict[str, Any]:
    """List STT language codes from GnaniSTTClient.supported_languages."""
    languages = GnaniSTTClient.supported_languages()
    return {"languages": _json_safe(languages)}
def create_app():
    """ASGI app: streamable HTTP at /mcp plus GET / for Render health checks."""
    app = mcp.streamable_http_app()
    app.add_middleware(OptionalBearerAuthMiddleware)
    return app
def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    _require_api_key()
    port = int(os.environ.get("PORT", str(DEFAULT_PORT)))
    logger.info("Starting gnani-mcp-remote on 0.0.0.0:%s (MCP path /mcp)", port)
    uvicorn.run(create_app(), host="0.0.0.0", port=port, proxy_headers=True, forwarded_allow_ips="*")
if __name__ == "__main__":
    main()
