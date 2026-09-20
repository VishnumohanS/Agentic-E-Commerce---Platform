"""
Real A2A Protocol Client using a2a-sdk 1.1.2 (Pydantic types).

a2a-sdk 1.1.2 uses Pydantic models, NOT protobuf.
Types: SendMessageRequest, Message, Part, Role (Pydantic enums).
"""

import logging
import httpx
from typing import Optional

logger = logging.getLogger(__name__)


class A2AClient:
    def __init__(self, merchant_url: str):
        self.merchant_url = merchant_url.rstrip("/")

    async def get_agent_card(self) -> dict:
        """GET /.well-known/agent.json — returns the merchant's A2A AgentCard."""
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{self.merchant_url}/.well-known/agent.json")
            resp.raise_for_status()
            return resp.json()

    async def send_message(self, text: str, context_id: Optional[str] = None) -> dict:
        """
        Sends an A2A message to the merchant agent.

        Builds the request as a plain JSON dict matching the A2A SendMessageRequest
        schema. This avoids SDK version compatibility issues with protobuf vs Pydantic
        type system differences across SDK versions.

        Returns the raw JSON response dict.
        """
        # Build A2A SendMessageRequest as JSON directly
        # Compatible with both a2a-sdk Pydantic and protobuf variants
        req_dict = {
            "message": {
                "role": "ROLE_USER",
                "parts": [{"text": text}],
            }
        }
        if context_id:
            req_dict["message"]["contextId"] = context_id

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{self.merchant_url}/api/a2a/message",
                json=req_dict,
            )
            resp.raise_for_status()
            return resp.json()

    async def check_merchant_online(self) -> bool:
        """Returns True if merchant agent is reachable via A2A AgentCard endpoint."""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self.merchant_url}/.well-known/agent.json")
                return resp.status_code == 200
        except Exception:
            return False
