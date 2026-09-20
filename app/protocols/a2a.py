"""
Google Agent2Agent (A2A) Protocol Handler — powered by the official `a2a-sdk` library.

Uses real protobuf types from `a2a.types` (a2a-sdk>=1.1.2):
  - AgentCard, AgentCapabilities, AgentSkill, AgentInterface, AgentProvider
  - Message, Part, Role, Task, TaskStatus, TaskState
  - SendMessageRequest, SendMessageResponse

Agent Card served at /.well-known/agent.json as per the A2A specification.
A2A message endpoint at /api/a2a/message handles the full task lifecycle:
  TASK_STATE_SUBMITTED → TASK_STATE_WORKING → TASK_STATE_COMPLETED
"""

import uuid
import logging
from typing import Any, Dict

from a2a.types import (
    AgentCard,
    AgentCapabilities,
    AgentSkill,
    AgentInterface,
    AgentProvider,
    Message,
    Part,
    Role,
    Task,
    TaskStatus,
    TaskState,
    SendMessageRequest,
    SendMessageResponse,
)
from google.protobuf import json_format

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Agent Card — serialized once at startup
# ---------------------------------------------------------------------------

def _build_agent_card() -> AgentCard:
    """
    Constructs the official A2A AgentCard using real `a2a.types` protobuf types.

    Conforms to the Google A2A specification:
    https://google.github.io/A2A/specification/
    """
    card = AgentCard()
    card.name = "TechVerse Merchant Agent"
    card.description = (
        "Autonomous merchant agent for agentic commerce. "
        "Supports catalog discovery via MCP, dynamic upsells, "
        "AP2 cryptographic payment mandates, and Razorpay test-mode checkout."
    )
    card.version = "1.0.0"

    # Supported network interface (HTTP JSON endpoint)
    iface = AgentInterface()
    iface.url = "http://localhost:8000/api/a2a/message"
    card.supported_interfaces.append(iface)

    # Capabilities
    cap = AgentCapabilities()
    cap.streaming = True
    card.capabilities.CopyFrom(cap)

    # Agent Skills (atomic capabilities exposed to AI buyers)
    skills_data = [
        {
            "id": "product_vector_search",
            "name": "Vector Catalog Discovery",
            "description": (
                "TF-IDF vector similarity catalog search filtered by "
                "spec requirements and AP2 budget limits."
            ),
            "tags": ["catalog", "search", "mcp", "ucp"],
        },
        {
            "id": "dynamic_margin_upsell",
            "name": "Revenue Maximiser",
            "description": (
                "Calculates the highest-margin upsell/cross-sell product "
                "within the remaining AP2 mandate budget headroom."
            ),
            "tags": ["upsell", "cross-sell", "revenue"],
        },
        {
            "id": "ap2_bounded_mandate_verification",
            "name": "AP2 Mandate Validator",
            "description": (
                "Validates HMAC-SHA256 signed AP2 Bounded Mandates — "
                "enforcing budget caps, merchant binding, category limits, "
                "and expiry checks before any payment action."
            ),
            "tags": ["ap2", "mandate", "authorization", "payment"],
        },
        {
            "id": "razorpay_test_checkout",
            "name": "Razorpay Test Gateway",
            "description": (
                "Creates Razorpay orders via /v1/orders, generates payment "
                "tokens, and verifies HMAC-SHA256 signatures in test mode."
            ),
            "tags": ["razorpay", "payment", "checkout"],
        },
        {
            "id": "dynamodb_audit_ledger",
            "name": "Cryptographic Audit Ledger",
            "description": (
                "Tamper-evident SHA-256 hash-chained audit log of every "
                "agent action, persisted to DynamoDB. Full explainability."
            ),
            "tags": ["audit", "explainability", "ledger"],
        },
    ]

    for s in skills_data:
        skill = AgentSkill()
        skill.id = s["id"]
        skill.name = s["name"]
        skill.description = s["description"]
        for tag in s["tags"]:
            skill.tags.append(tag)
        card.skills.append(skill)

    # Provider info
    provider = AgentProvider()
    provider.organization = "TechVerse Systems"
    provider.url = "https://techverse.example.com"
    card.provider.CopyFrom(provider)

    return card


# Pre-built once, serialized on each request to avoid repeated proto work
_AGENT_CARD: AgentCard = _build_agent_card()


def get_agent_card_dict() -> Dict[str, Any]:
    """
    Returns the A2A AgentCard serialized to a JSON-compatible dict.
    Uses `google.protobuf.json_format.MessageToDict` for spec-compliant output.
    """
    return json_format.MessageToDict(
        _AGENT_CARD,
        preserving_proto_field_name=False,  # camelCase per A2A spec
        always_print_fields_with_no_presence=False,
    )


# ---------------------------------------------------------------------------
# A2A Message Handler — real SendMessageRequest / SendMessageResponse
# ---------------------------------------------------------------------------

class A2AMessageHandler:
    """
    Handles inbound A2A SendMessageRequest objects and returns SendMessageResponse.

    Lifecycle:
      1. Parse the incoming payload into a real `SendMessageRequest` proto.
      2. Extract the buyer's text intent from `message.parts[*].text`.
      3. Create a `Task` with status TASK_STATE_WORKING.
      4. Invoke the merchant's commerce skill (catalog search hint in metadata).
      5. Build the `Message` reply from the merchant agent.
      6. Return a `SendMessageResponse` with the completed `Task`.
    """

    @staticmethod
    def handle(raw_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parses a raw JSON dict as `SendMessageRequest`, processes it,
        and returns a `SendMessageResponse` dict.

        Args:
            raw_payload: JSON-decoded request body matching SendMessageRequest proto.

        Returns:
            JSON-compatible dict representing SendMessageResponse.
        """
        # --- 1. Parse inbound message using real SDK proto ---
        try:
            request: SendMessageRequest = json_format.ParseDict(
                raw_payload, SendMessageRequest()
            )
        except Exception as exc:
            logger.warning("A2A: Could not parse SendMessageRequest: %s", exc)
            # Return an error Task
            return A2AMessageHandler._error_response(
                f"Invalid SendMessageRequest payload: {exc}"
            )

        inbound_msg: Message = request.message

        # Extract text intent from message parts (real Part proto)
        intent_text = ""
        for part in inbound_msg.parts:
            if part.HasField("text"):
                intent_text += part.text + " "
        intent_text = intent_text.strip() or "No intent provided"

        task_id = str(uuid.uuid4())
        context_id = inbound_msg.context_id or str(uuid.uuid4())

        # --- 2. Build Task with WORKING status ---
        task = Task()
        task.id = task_id
        task.context_id = context_id

        working_status = TaskStatus()
        working_status.state = TaskState.Value("TASK_STATE_WORKING")
        working_status.timestamp.GetCurrentTime()  # protobuf Timestamp.GetCurrentTime()
        working_msg = Message()
        working_msg.message_id = str(uuid.uuid4())
        working_msg.context_id = context_id
        working_msg.task_id = task_id
        working_msg.role = Role.Value("ROLE_AGENT")
        wp = Part()
        wp.text = f"Merchant agent processing: '{intent_text}'"
        working_msg.parts.append(wp)
        working_status.message.CopyFrom(working_msg)
        task.status.CopyFrom(working_status)

        # --- 3. Build merchant reply message ---
        reply_msg = Message()
        reply_msg.message_id = str(uuid.uuid4())
        reply_msg.context_id = context_id
        reply_msg.task_id = task_id
        reply_msg.role = Role.Value("ROLE_AGENT")

        reply_text = (
            f"TechVerse Merchant Agent received your request: '{intent_text}'. "
            "Use POST /api/commerce/stream for the full autonomous commerce pipeline "
            "(catalog search → upsell offer → AP2 mandate validation → Razorpay checkout). "
            "Use POST /mcp for MCP tool invocations (tools/list, tools/call)."
        )
        rp = Part()
        rp.text = reply_text
        reply_msg.parts.append(rp)

        # --- 4. Finalize Task as COMPLETED ---
        completed_status = TaskStatus()
        completed_status.state = TaskState.Value("TASK_STATE_COMPLETED")
        completed_status.timestamp.GetCurrentTime()  # protobuf Timestamp.GetCurrentTime()
        completed_status.message.CopyFrom(reply_msg)
        task.status.CopyFrom(completed_status)

        # Append message history
        task.history.append(inbound_msg)
        task.history.append(reply_msg)

        # --- 5. Build SendMessageResponse ---
        response = SendMessageResponse()
        response.task.CopyFrom(task)

        return json_format.MessageToDict(
            response,
            preserving_proto_field_name=False,
            always_print_fields_with_no_presence=False,
        )

    @staticmethod
    def _error_response(reason: str) -> Dict[str, Any]:
        task = Task()
        task.id = str(uuid.uuid4())
        task.context_id = str(uuid.uuid4())

        err_status = TaskStatus()
        err_status.state = TaskState.Value("TASK_STATE_FAILED")
        err_status.timestamp.GetCurrentTime()  # protobuf Timestamp.GetCurrentTime()

        err_msg = Message()
        err_msg.message_id = str(uuid.uuid4())
        err_msg.role = Role.Value("ROLE_AGENT")
        ep = Part()
        ep.text = reason
        err_msg.parts.append(ep)
        err_status.message.CopyFrom(err_msg)
        task.status.CopyFrom(err_status)

        response = SendMessageResponse()
        response.task.CopyFrom(task)

        return json_format.MessageToDict(
            response,
            preserving_proto_field_name=False,
            always_print_fields_with_no_presence=False,
        )
