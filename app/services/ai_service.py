"""Provider-backed text generation for the buyer agents."""

import logging
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


class AIService:
    def __init__(self) -> None:
        self.provider = settings.ai_provider()
        self._bedrock = None
        self._credential_error: str | None = None
        if self.provider == "bedrock":
            try:
                import boto3

                session = boto3.Session(region_name=settings.AWS_REGION or None)
                if session.get_credentials() is None:
                    self._credential_error = "AWS credentials were not found."
                    return
                self._bedrock = boto3.client(
                    "bedrock-runtime",
                    region_name=settings.AWS_REGION or None,
                )
            except Exception as exc:
                logger.error("AWS Bedrock client initialization failed: %s", exc)
                self._credential_error = str(exc)
                self._bedrock = None

    @property
    def is_available(self) -> bool:
        if self.provider == "bedrock":
            return self._bedrock is not None
        return False

    @property
    def mode(self) -> str:
        if self.provider == "bedrock":
            return "bedrock" if self.is_available else "unavailable"
        return "unavailable"

    def generate(self, prompt: str, *, json_output: bool = False) -> str:
        if self.provider != "bedrock":
            raise RuntimeError("AIService is configured for a non-Bedrock provider.")
        if not self._bedrock:
            raise RuntimeError(
                f"AWS Bedrock is unavailable: {self._credential_error or 'client initialization failed'}"
            )

        response = self._bedrock.converse(
            modelId=settings.BEDROCK_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"temperature": 0.2, "maxTokens": 800},
        )
        content: list[dict[str, Any]] = response.get("output", {}).get("message", {}).get("content", [])
        text = next((item.get("text", "") for item in content if item.get("text")), "")
        if not text:
            raise RuntimeError("AWS Bedrock returned an empty response.")
        return text.strip()


ai_service = AIService()
