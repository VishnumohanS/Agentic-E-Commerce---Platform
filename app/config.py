"""
Centralised application configuration using pydantic-settings.
All secrets are loaded from the .env file at the project root.

Usage anywhere in the project:
    from app.config import settings
    settings.BEDROCK_MODEL_ID
    settings.is_razorpay_configured()
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ── Razorpay ─────────────────────────────────────────────────────────────
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""
    RAZORPAY_MODE: str = "mock"

    # Optional authorised Amazon-product MCP endpoint.  The local catalog stays
    # available as an offline provider when this is disabled.
    AMAZON_MCP_ENABLED: bool = False
    AMAZON_MCP_URL: str = ""
    AMAZON_MCP_API_KEY: str = ""

    # ── Agent Network ─────────────────────────────────────────────────────────
    MERCHANT_AGENT_URL: str = "http://localhost:8000"
    BUYER_AGENT_URL: str = "http://localhost:8001"
    MERCHANT_AGENT_PORT: int = 8000
    BUYER_AGENT_PORT: int = 8001

    # ── AP2 Mandate Cryptography ──────────────────────────────────────────────
    AP2_MANDATE_SECRET: str = "AP2_MANDATE_SECRET_AUTHORIZATION_KEY_2026"

    # ── Authentication (JWT) ────────────────────────────────────────────────
    JWT_SECRET: str = "change_me_to_a_long_random_secret_in_production"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24h

    # ── Database ─────────────────────────────────────────────────────────────
    DYNAMODB_TABLE_NAME: str = "merchant-agent"
    ENVIRONMENT: str = "development"
    AI_PROVIDER: str = "bedrock"
    AWS_REGION: str = ""
    BEDROCK_MODEL_ID: str = "amazon.nova-lite-v1:0"
    BEDROCK_EMBEDDING_MODEL_ID: str = "amazon.titan-embed-text-v2:0"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def is_razorpay_configured(self) -> bool:
        """True when real (non-placeholder) Razorpay test keys are present."""
        return bool(
            self.RAZORPAY_KEY_ID
            and self.RAZORPAY_KEY_SECRET
            and self.RAZORPAY_KEY_ID.startswith("rzp_test_")
            and not self.RAZORPAY_KEY_ID.startswith("rzp_test_MerchantAgent")
        )

    def get_missing_keys(self) -> list[str]:
        """Returns a list of keys that are not properly configured."""
        missing: list[str] = []
        if self.ai_provider() == "bedrock":
            if not self.AWS_REGION:
                missing.append("AWS_REGION")
            if not self.BEDROCK_MODEL_ID:
                missing.append("BEDROCK_MODEL_ID")
            if not self.DYNAMODB_TABLE_NAME:
                missing.append("DYNAMODB_TABLE_NAME")
        if not self.is_razorpay_configured():
            missing.append("RAZORPAY_KEY_ID + RAZORPAY_KEY_SECRET")
        return missing

    def razorpay_mode(self) -> str:
        if self.RAZORPAY_MODE.lower() == "test" and self.is_razorpay_configured():
            return "test"
        return "mock"

    def ai_provider(self) -> str:
        return self.AI_PROVIDER.strip().lower()

    def ai_mode(self) -> str:
        if self.ai_provider() == "bedrock":
            return "bedrock" if self.AWS_REGION and self.BEDROCK_MODEL_ID else "unavailable"
        return "mock"

    def ap2_secret(self) -> str:
        return self.AP2_MANDATE_SECRET



@lru_cache
def get_settings() -> Settings:
    return Settings()


# Singleton — import this everywhere
settings = get_settings()
