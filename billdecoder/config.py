"""Runtime configuration, read from environment variables (.env supported)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

SOURCE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(SOURCE_DIR / ".env")

# Only models allowed by the competition rules may ever be called.
ALLOWED_MODELS = {"gemini-3.5-flash-lite", "gemini-3.1-flash-lite"}


@dataclass(frozen=True)
class Config:
    provider: str
    model: str
    fallback_model: str
    api_key: str
    request_timeout_s: int
    min_interval_s: float
    max_retries: int
    cache_dir: Path

    @classmethod
    def from_env(cls) -> "Config":
        cfg = cls(
            provider=os.getenv("MODEL_PROVIDER", "google"),
            model=os.getenv("MODEL_NAME", "gemini-3.5-flash-lite"),
            fallback_model=os.getenv("FALLBACK_MODEL_NAME", "gemini-3.1-flash-lite"),
            api_key=os.getenv("API_KEY", ""),
            request_timeout_s=int(os.getenv("REQUEST_TIMEOUT_S", "120")),
            min_interval_s=float(os.getenv("MIN_REQUEST_INTERVAL_S", "4.2")),
            max_retries=int(os.getenv("MAX_RETRIES", "4")),
            cache_dir=Path(os.getenv("CACHE_DIR", str(SOURCE_DIR / ".cache"))),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.provider != "google":
            raise ValueError("MODEL_PROVIDER must be 'google' (Gemini) for this solution.")
        for m in (self.model, self.fallback_model):
            if m not in ALLOWED_MODELS:
                raise ValueError(f"Model '{m}' is not in the competition allow-list {sorted(ALLOWED_MODELS)}")
        if not self.api_key or "your-api-key" in self.api_key:
            raise ValueError("API_KEY is missing. Copy .env.example to .env and set your Gemini key.")
