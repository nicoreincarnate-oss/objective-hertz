"""
Central configuration for all Perseus agents.
Loads from .env, provides typed access to all settings.
"""

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

# Load .env from repo root
_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_int(key: str, default: int = 0) -> int:
    return int(os.getenv(key, str(default)))


def _env_float(key: str, default: float = 0.0) -> float:
    return float(os.getenv(key, str(default)))


def _env_bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(int(default))).lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class PostgresConfig:
    user: str = _env("POSTGRES_USER", "perseus")
    password: str = _env("POSTGRES_PASSWORD")
    db: str = _env("POSTGRES_DB", "perseus")
    host: str = _env("POSTGRES_HOST", "localhost")
    port: int = _env_int("POSTGRES_PORT", 5432)

    @property
    def dsn(self) -> str:
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.db}"


@dataclass(frozen=True)
class OllamaConfig:
    host: str = _env("OLLAMA_HOST", "http://localhost:11434")
    model: str = _env("OLLAMA_MODEL", "qwen2.5:14b-instruct-q4_K_M")
    secondary: str = _env("OLLAMA_SECONDARY", "llama3.2:3b")
    embed_model: str = _env("OLLAMA_EMBED", "nomic-embed-text")


@dataclass(frozen=True)
class ClaudeConfig:
    api_key: str = _env("ANTHROPIC_API_KEY")
    primary_model: str = _env("CLAUDE_PRIMARY_MODEL", "claude-sonnet-4-6")
    fast_model: str = _env("CLAUDE_FAST_MODEL", "claude-haiku-4-5-20251001")


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str = _env("TELEGRAM_BOT_TOKEN")
    chat_id: str = _env("TELEGRAM_CHAT_ID")


@dataclass(frozen=True)
class InstantlyConfig:
    api_key: str = _env("INSTANTLY_API_KEY")


@dataclass(frozen=True)
class FirecrawlConfig:
    api_key: str = _env("FIRECRAWL_API_KEY")


@dataclass(frozen=True)
class PaymentConfig:
    wise_api_token: str = _env("WISE_API_TOKEN")
    wise_profile_id: str = _env("WISE_PROFILE_ID")
    stripe_api_key: str = _env("STRIPE_API_KEY")


@dataclass(frozen=True)
class HostingConfig:
    netlify_token: str = _env("NETLIFY_AUTH_TOKEN")


@dataclass(frozen=True)
class MemoryConfig:
    qdrant_host: str = _env("QDRANT_HOST", "http://localhost:6333")
    qdrant_collection: str = _env("QDRANT_COLLECTION", "perseus")
    mem0_host: str = _env("MEM0_HOST", "http://localhost:8888")


@dataclass(frozen=True)
class BudgetConfig:
    monthly_cap: int = _env_int("MONTHLY_BUDGET_CAP", 800)
    alert_threshold: float = _env_float("BUDGET_ALERT_THRESHOLD", 0.80)
    cloud_gpu_cap: int = _env_int("CLOUD_GPU_BUDGET_CAP", 200)


@dataclass(frozen=True)
class PricingConfig:
    """Starting prices — AI adjusts based on market research."""
    website_5page: int = _env_int("PRICE_WEBSITE_5PAGE", 299)
    landing_page: int = _env_int("PRICE_LANDING_PAGE", 149)
    hosting_monthly: int = _env_int("PRICE_HOSTING_MONTHLY", 52)
    receptionist_monthly: int = _env_int("PRICE_RECEPTIONIST_MONTHLY", 398)
    receptionist_vendor_cost: int = _env_int("COST_RECEPTIONIST_VENDOR", 79)


@dataclass(frozen=True)
class PerseusConfig:
    root_dir: Path = _ROOT
    log_level: str = _env("LOG_LEVEL", "INFO")
    log_dir: Path = _ROOT / _env("LOG_DIR", "logs")
    log_format: str = _env("LOG_FORMAT", "text")
    log_to_stdout: bool = _env_bool("LOG_TO_STDOUT", True)
    log_max_bytes: int = _env_int("LOG_MAX_BYTES", 10485760)
    log_backup_count: int = _env_int("LOG_BACKUP_COUNT", 5)
    review_mode: bool = _env_bool("REVIEW_MODE", True)
    sales_before_autonomy: int = _env_int("SALES_BEFORE_AUTONOMY", 10)

    postgres: PostgresConfig = field(default_factory=PostgresConfig)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    claude: ClaudeConfig = field(default_factory=ClaudeConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    instantly: InstantlyConfig = field(default_factory=InstantlyConfig)
    firecrawl: FirecrawlConfig = field(default_factory=FirecrawlConfig)
    payment: PaymentConfig = field(default_factory=PaymentConfig)
    hosting: HostingConfig = field(default_factory=HostingConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    pricing: PricingConfig = field(default_factory=PricingConfig)


# Singleton
config = PerseusConfig()
