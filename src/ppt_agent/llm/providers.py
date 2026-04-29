from __future__ import annotations

from pydantic import BaseModel


class ProviderSpec(BaseModel):
    base_url: str
    models: list[str]
    legacy_models: list[str] = []


PROVIDER_SPECS: dict[str, ProviderSpec] = {
    "deepseek": ProviderSpec(
        base_url="https://api.deepseek.com",
        models=[
            "deepseek-v4-flash",
            "deepseek-v4-pro",
            "deepseek-chat",
            "deepseek-reasoner",
        ],
        legacy_models=[
            "deepseek-chat",
            "deepseek-reasoner",
        ],
    ),
    "kimi": ProviderSpec(
        base_url="https://api.moonshot.cn/v1",
        models=[
            "kimi-k2.6",
            "kimi-k2.5",
            "kimi-k2-thinking",
            "kimi-k2-thinking-turbo",
            "kimi-k2-turbo-preview",
            "moonshot-v1-8k",
            "moonshot-v1-32k",
            "moonshot-v1-128k",
        ],
    ),
}


def validate_provider(provider: str) -> str:
    if provider not in PROVIDER_SPECS:
        raise ValueError(f"unsupported provider: {provider}")
    return provider


def validate_model(provider: str, model: str) -> str:
    validate_provider(provider)
    if model not in PROVIDER_SPECS[provider].models:
        raise ValueError(f"unsupported model for {provider}: {model}")
    return model


def is_legacy_model(provider: str, model: str) -> bool:
    validate_provider(provider)
    return model in PROVIDER_SPECS[provider].legacy_models
