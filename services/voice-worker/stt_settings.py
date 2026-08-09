from __future__ import annotations

from dataclasses import dataclass
import os
from urllib.parse import urlsplit, urlunsplit


SUPPORTED_STT_PROVIDERS = ("whisper", "qwen", "nemotron")

_PROVIDER_ALIASES = {
    "faster-whisper": "whisper",
    "faster_whisper": "whisper",
    "qwen3": "qwen",
    "qwen3-asr": "qwen",
    "qwen3_asr": "qwen",
    "nemotron-3.5": "nemotron",
    "nemotron3.5": "nemotron",
    "nemotron-3.5-asr": "nemotron",
}

_DEFAULT_MODELS = {
    "whisper": "Systran/faster-whisper-medium.en",
    "qwen": "Qwen/Qwen3-ASR-1.7B",
    "nemotron": "nvidia/nemotron-3.5-asr-streaming-0.6b",
}


def _boolean(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(
        f"{name} must be one of true/false, yes/no, on/off, or 1/0"
    )


def normalize_stt_provider(requested: str) -> str:
    requested = requested.strip().lower()
    provider = _PROVIDER_ALIASES.get(requested, requested)
    if provider not in SUPPORTED_STT_PROVIDERS:
        supported = ", ".join(SUPPORTED_STT_PROVIDERS)
        raise RuntimeError(
            f"Unsupported STT_PROVIDER {requested!r}; expected one of: {supported}"
        )
    return provider


def default_stt_provider() -> str:
    return normalize_stt_provider(os.getenv("STT_PROVIDER", "whisper"))


def available_stt_providers() -> tuple[str, ...]:
    default = default_stt_provider()
    configured = os.getenv("STT_AVAILABLE_PROVIDERS")
    if configured is None:
        return (default,)
    providers: list[str] = []
    for value in configured.split(","):
        if not value.strip():
            continue
        provider = normalize_stt_provider(value)
        if provider not in providers:
            providers.append(provider)
    if default not in providers:
        raise RuntimeError("STT_PROVIDER must be included in STT_AVAILABLE_PROVIDERS")
    return tuple(providers)


def _provider_value(generic: str, specific: str, *, use_generic: bool) -> str | None:
    if use_generic:
        return os.getenv(generic) or os.getenv(specific)
    return os.getenv(specific)


def _normalize_api_base_url(raw_url: str) -> str:
    value = raw_url.strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError(
            "STT_BASE_URL must be an absolute http(s) URL, for example "
            "http://127.0.0.1:8000/v1"
        )
    path = parsed.path.rstrip("/")
    if not path.endswith("/v1"):
        path = f"{path}/v1" if path else "/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _default_health_url(api_base_url: str) -> str:
    parsed = urlsplit(api_base_url)
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3]
    health_path = f"{path}/health" if path else "/health"
    return urlunsplit((parsed.scheme, parsed.netloc, health_path, "", ""))


@dataclass(frozen=True, slots=True)
class STTConfig:
    provider: str
    model: str
    base_url: str
    api_key: str
    language: str
    detect_language: bool
    use_realtime: bool
    health_url: str

    @classmethod
    def from_env(cls, provider: str | None = None) -> "STTConfig":
        default_provider = default_stt_provider()
        provider = normalize_stt_provider(provider) if provider else default_provider
        if provider not in available_stt_providers():
            raise RuntimeError(
                f"STT provider {provider!r} is not enabled in STT_AVAILABLE_PROVIDERS"
            )
        use_generic = provider == default_provider
        legacy_prefix = {
            "whisper": "FASTER_WHISPER",
            "qwen": "QWEN_ASR",
            "nemotron": "NEMOTRON_ASR",
        }[provider]

        base_url = _provider_value(
            "STT_BASE_URL",
            f"{legacy_prefix}_BASE_URL",
            use_generic=use_generic,
        )
        if not base_url and provider == "whisper":
            base_url = "http://127.0.0.1:8000/v1"
        if not base_url:
            raise RuntimeError(
                f"STT_BASE_URL is required when STT_PROVIDER={provider}"
            )
        normalized_base_url = _normalize_api_base_url(base_url)

        model = _provider_value(
            "STT_MODEL",
            f"{legacy_prefix}_MODEL",
            use_generic=use_generic,
        )
        model = (model or _DEFAULT_MODELS[provider]).strip()
        if not model:
            raise RuntimeError("STT_MODEL cannot be empty")

        configured_language = _provider_value(
            "STT_LANGUAGE",
            f"{legacy_prefix}_LANGUAGE",
            use_generic=use_generic,
        )
        if configured_language is not None:
            language = configured_language.strip()
        elif provider == "whisper":
            language = "en"
        elif provider == "nemotron":
            language = "auto"
        else:
            language = ""

        # Qwen and Whisper use an empty language field for auto detection.
        # Nemotron's prompt-conditioned runtime expects the literal value "auto".
        detect_language = provider != "nemotron" and language.lower() in {"", "auto"}
        if detect_language:
            language = ""

        realtime_name = (
            "STT_USE_REALTIME" if use_generic else f"{legacy_prefix}_USE_REALTIME"
        )
        use_realtime = _boolean(realtime_name, provider == "nemotron")
        if use_realtime and provider != "nemotron":
            raise RuntimeError(
                "STT_USE_REALTIME is currently supported only for Nemotron via "
                "NeMo-Speech.cpp's OpenAI-compatible /v1/realtime endpoint"
            )

        api_key = _provider_value(
            "STT_API_KEY",
            f"{legacy_prefix}_API_KEY",
            use_generic=use_generic,
        ) or "local"
        health_url = _provider_value(
            "STT_HEALTH_URL",
            f"{legacy_prefix}_HEALTH_URL",
            use_generic=use_generic,
        ) or _default_health_url(
            normalized_base_url
        )

        return cls(
            provider=provider,
            model=model,
            base_url=normalized_base_url,
            api_key=api_key,
            language=language,
            detect_language=detect_language,
            use_realtime=use_realtime,
            health_url=health_url,
        )
