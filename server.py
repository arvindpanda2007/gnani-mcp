"""Render-deployable Streamable-HTTP MCP server wrapping gnani-vachana."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from gnani.stt import GnaniSTTClient
from gnani.tts import AudioConfig, GnaniTTSClient, SpeakerEmbedding


# ============================================================
# CONFIGURATION
# ============================================================

DEFAULT_PORT = 8000

MAX_AUDIO_BYTES = int(
    os.environ.get(
        "MAX_AUDIO_BYTES",
        str(25 * 1024 * 1024),
    )
)

ALLOWED_AUDIO_SUFFIXES = {
    ".wav",
    ".mp3",
    ".m4a",
    ".ogg",
    ".webm",
    ".flac",
    ".aac",
}

logger = logging.getLogger("gnani-mcp-remote")


# ============================================================
# MCP TRANSPORT SECURITY
# ============================================================

_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "gnani-mcp-3.onrender.com")

transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=[_host, f"{_host}:*"],
    allowed_origins=[f"https://{_host}"],
)


# ============================================================
# MCP SERVER
# ============================================================

mcp = FastMCP(
    "gnani-mcp-remote",
    transport_security=transport_security,
)


# ============================================================
# ENVIRONMENT
# ============================================================

def _require_api_key() -> str:
    """Require the Gnani API key."""

    key = os.environ.get(
        "GNANI_API_KEY",
        "",
    ).strip()

    if not key:
        raise RuntimeError(
            "GNANI_API_KEY is missing. "
            "Set it in the Render environment variables."
        )

    return key


# ============================================================
# JSON HELPERS
# ============================================================

def _json_safe(value: Any) -> Any:
    """Convert SDK output into JSON-safe values."""

    try:
        json.dumps(value)
        return value

    except TypeError:

        if isinstance(value, dict):
            return {
                str(k): _json_safe(v)
                for k, v in value.items()
            }

        if isinstance(value, (list, tuple)):
            return [
                _json_safe(v)
                for v in value
            ]

        return str(value)


# ============================================================
# AUDIO HELPERS
# ============================================================

def _guess_filename(
    name: str | None,
    url: str | None,
) -> str:
    """Determine a usable audio filename."""

    candidate = (name or "").strip()

    if not candidate and url:
        candidate = os.path.basename(
            urlparse(url).path
        )

    if not candidate:
        return "audio.wav"

    lower = candidate.lower()

    if not any(
        lower.endswith(ext)
        for ext in ALLOWED_AUDIO_SUFFIXES
    ):
        return f"{candidate}.wav"

    return candidate


def _decode_base64_audio(
    audio_base64: str,
) -> bytes:
    """Decode base64 audio."""

    payload = audio_base64.strip()

    # Support:
    # data:audio/wav;base64,...
    if payload.startswith("data:") and "," in payload:
        payload = payload.split(",", 1)[1]

    try:
        data = base64.b64decode(
            payload,
            validate=False,
        )

    except Exception as exc:
        raise ValueError(
            f"audio_base64 is not valid base64: {exc}"
        ) from exc

    if not data:
        raise ValueError(
            "audio_base64 decoded to empty bytes"
        )

    if len(data) > MAX_AUDIO_BYTES:
        raise ValueError(
            f"Audio exceeds {MAX_AUDIO_BYTES} byte limit"
        )

    return data


def _download_audio(
    url: str,
) -> bytes:
    """Download audio from an HTTP(S) URL."""

    parsed = urlparse(url)

    if parsed.scheme not in (
        "http",
        "https",
    ) or not parsed.netloc:
        raise ValueError(
            "audio_url must be an http or https URL"
        )

    request = Request(
        url,
        headers={
            "User-Agent": "gnani-mcp-remote/0.1",
        },
    )

    with urlopen(
        request,
        timeout=60,
    ) as response:

        chunks: list[bytes] = []
        total = 0

        while True:
            chunk = response.read(64 * 1024)

            if not chunk:
                break

            total += len(chunk)

            if total > MAX_AUDIO_BYTES:
                raise ValueError(
                    f"Audio exceeds {MAX_AUDIO_BYTES} byte limit"
                )

            chunks.append(chunk)

    data = b"".join(chunks)

    if not data:
        raise ValueError(
            "Downloaded audio was empty"
        )

    return data


# ============================================================
# VOICE CLONE HELPERS
# ============================================================

def _parse_shape(
    shape: list[int] | str,
) -> list[int]:
    """Parse speaker embedding tensor shape."""

    if isinstance(shape, list):
        return [
            int(x)
            for x in shape
        ]

    text = shape.strip()

    if not text:
        return [1, 768]

    try:
        parsed = json.loads(text)

        if isinstance(parsed, list):
            return [
                int(x)
                for x in parsed
            ]

    except json.JSONDecodeError:
        pass

    numbers = [
        int(x)
        for x in re.findall(
            r"-?\d+",
            text,
        )
    ]

    if not numbers:
        raise ValueError(
            "embedding_shape must be a list "
            "of integers, e.g. [1, 768]"
        )

    return numbers


# ============================================================
# TTS AUDIO CONFIG
# ============================================================

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


# ============================================================
# TOOL 1 — STT
# ============================================================

@mcp.tool()
def transcribe(
    language_code: str = "hi-IN",
    audio_url: str | None = None,
    audio_base64: str | None = None,
    filename: str | None = None,
    output_format: str = "verbatim",
    itn_native_numerals: bool = False,
) -> dict[str, Any]:
    """
    Convert speech audio to text using Gnani STT.

    Provide exactly one of:
    - audio_url
    - audio_base64

    Example languages:
    - hi-IN
    - en-IN

    output_format:
    - verbatim
    - transcribe
    """

    _require_api_key()

    has_url = bool(
        audio_url
        and audio_url.strip()
    )

    has_base64 = bool(
        audio_base64
        and audio_base64.strip()
    )

    if has_url == has_base64:
        raise ValueError(
            "Provide exactly one of "
            "audio_url or audio_base64"
        )

    if has_url:
        audio_bytes = _download_audio(
            audio_url.strip()
        )
    else:
        audio_bytes = _decode_base64_audio(
            audio_base64 or ""
        )

    audio_filename = _guess_filename(
        filename,
        audio_url if has_url else None,
    )

    client = GnaniSTTClient()

    result = client.transcribe_bytes(
        audio_bytes,
        filename=audio_filename,
        language_code=language_code,
        format=output_format,
        itn_native_numerals=itn_native_numerals,
    )

    return _json_safe(result)


# ============================================================
# TOOL 2 — TTS
# ============================================================

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
    """
    Convert text to speech using Gnani TTS.

    Returns base64-encoded audio.
    """

    _require_api_key()

    if not text.strip():
        raise ValueError(
            "text is required"
        )

    config = _audio_config(
        container=container,
        sample_rate=sample_rate,
        encoding=encoding,
        bitrate=(
            bitrate
            if container == "mp3"
            else None
        ),
    )

    client = GnaniTTSClient()

    audio = client.synthesize(
        text,
        voice=voice,
        model=model,
        language=language,
        speed=speed,
        audio_config=config,
    )

    return {
        "format": container,
        "sample_rate": sample_rate,
        "voice": voice,
        "model": model,
        "audio_base64": base64.b64encode(
            audio
        ).decode("ascii"),
        "byte_length": len(audio),
    }


# ============================================================
# TOOL 3 — CLONED VOICE
# ============================================================

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
    """
    Generate speech using a Gnani speaker embedding.
    """

    _require_api_key()

    if not text.strip():
        raise ValueError(
            "text is required"
        )

    if not embedding.strip():
        raise ValueError(
            "embedding is required"
        )

    shape = _parse_shape(
        embedding_shape
    )

    config = _audio_config(
        container=container,
        sample_rate=sample_rate,
        encoding=encoding,
        bitrate=(
            bitrate
            if container == "mp3"
            else None
        ),
    )

    client = GnaniTTSClient()

    audio = client.synthesize(
        text,
        voice=None,
        model=model,
        language=language,
        speed=speed,
        audio_config=config,
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
        "audio_base64": base64.b64encode(
            audio
        ).decode("ascii"),
        "byte_length": len(audio),
    }


# ============================================================
# TOOL 4 — LIST VOICES
# ============================================================

@mcp.tool()
def list_voices(
    model: str = "timbre-v2.0",
) -> dict[str, Any]:
    """
    List available Gnani TTS voices.
    """

    _require_api_key()

    voices = sorted(
        GnaniTTSClient.supported_voices(
            model=model
        )
    )

    return {
        "model": model,
        "voices": voices,
    }


# ============================================================
# TOOL 5 — LIST STT LANGUAGES
# ============================================================

@mcp.tool()
def list_stt_languages() -> dict[str, Any]:
    """
    List supported Gnani STT languages.
    """

    _require_api_key()

    languages = (
        GnaniSTTClient.supported_languages()
    )

    return {
        "languages": _json_safe(
            languages
        )
    }


# ============================================================
# ASGI APPLICATION
# ============================================================

def create_app():
    """
    Create the Streamable HTTP ASGI application.

    MCP endpoint:
        /mcp
    """

    return mcp.streamable_http_app()


# ============================================================
# RENDER STARTUP
# ============================================================

def main() -> None:
    """Start the Gnani MCP server."""

    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(levelname)s "
            "%(name)s "
            "%(message)s"
        ),
    )

    _require_api_key()

    port = int(
        os.environ.get(
            "PORT",
            str(DEFAULT_PORT),
        )
    )

    logger.info(
        "Starting Gnani MCP server "
        "on 0.0.0.0:%s",
        port,
    )

    logger.info(
        "MCP endpoint: /mcp"
    )

    uvicorn.run(
        create_app(),
        host="0.0.0.0",
        port=port,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
