"""Runtime configuration, read once from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Settings:
    database_url: str
    llm_provider: str
    llm_model: str
    llm_api_key: str
    llm_base_url: str
    llm_tool_mode: str
    llm_max_tokens: int
    llm_stage_models: dict
    llm_fallback_model: str
    llm_reasoning_effort: str
    llm_tpm_budget: int
    app_password: str
    desk_date: str
    max_upload_mb: int
    max_agent_rounds: int

    def today(self) -> date:
        """The desk's 'today'. Pinned by DESK_DATE for demos, else the real date."""
        if self.desk_date:
            return date.fromisoformat(self.desk_date)
        return date.today()


def _db_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        return "sqlite:///./recovery_desk.db"
    # Render and Heroku hand out postgres:// URLs; SQLAlchemy wants a driver name.
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


# Free-tier Groq defaults: Qwen for the heavy stages (intake, build, negotiate), gpt-oss-120b for the rest.
# Groq limits tokens per model, so spreading stages across models spreads the quota.
_GROQ_STAGE_MODELS = "0=qwen/qwen3.8-27b,1=openai/gpt-oss-120b,2=openai/gpt-oss-120b,3=qwen/qwen3.8-27b," \
                     "5=qwen/qwen3.8-27b,6=openai/gpt-oss-120b"


def _stage_models(provider: str) -> dict:
    raw = os.getenv("LLM_STAGE_MODELS")
    if raw is None:
        raw = _GROQ_STAGE_MODELS if provider == "openai_compat" else ""
    out = {}
    for part in raw.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip().isdigit() and v.strip():
                out[int(k)] = v.strip()
    return out


def load() -> Settings:
    provider = os.getenv("LLM_PROVIDER", "openai_compat").strip().lower()
    # Groq is the default endpoint for the default provider; other providers set their own.
    default_base = "https://api.groq.com/openai/v1" if provider == "openai_compat" else ""
    return Settings(
        database_url=_db_url(),
        llm_provider=provider,
        llm_model=os.getenv("LLM_MODEL", "openai/gpt-oss-120b").strip(),
        llm_api_key=os.getenv("LLM_API_KEY", "").strip(),
        llm_base_url=os.getenv("LLM_BASE_URL", default_base).strip(),
        llm_tool_mode=os.getenv("LLM_TOOL_MODE", "native").strip().lower(),
        llm_max_tokens=int(os.getenv("LLM_MAX_TOKENS", "2500")),
        llm_stage_models=_stage_models(provider),
        llm_fallback_model=os.getenv("LLM_FALLBACK_MODEL", "openai/gpt-oss-120b,openai/gpt-oss-20b" if provider == "openai_compat" else "").strip(),
        llm_reasoning_effort=os.getenv("LLM_REASONING_EFFORT", "low").strip().lower(),
        llm_tpm_budget=int(os.getenv("LLM_TPM_BUDGET", "7000")),
        app_password=os.getenv("APP_PASSWORD", "").strip(),
        desk_date=os.getenv("DESK_DATE", "").strip(),
        max_upload_mb=int(os.getenv("MAX_UPLOAD_MB", "25")),
        max_agent_rounds=int(os.getenv("MAX_AGENT_ROUNDS", "8")),
    )


settings = load()
