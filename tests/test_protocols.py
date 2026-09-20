"""
Integration tests for the A2A Protocol (real a2a-sdk types) and MCP JSON-RPC 2.0 endpoints.

Tests verify that:
- The A2A AgentCard is served as a real a2a-sdk protobuf serialization
  (camelCase fields: name, description, version, supportedInterfaces, skills, capabilities)
- The A2A /api/a2a/message endpoint handles a real SendMessageRequest
  and returns a real SendMessageResponse with a completed Task
- MCP tools/list returns real mcp.types.ListToolsResult via model_dump()
- MCP tools/call returns real mcp.types.CallToolResult with TextContent
"""

import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_google_a2a_agent_card_endpoint():
    """
    Verifies the A2A Agent Card is a real a2a-sdk protobuf serialization.

    Real a2a.types.AgentCard fields (camelCase):
      - name, description, version
      - supportedInterfaces (list of AgentInterface with .url)
      - capabilities (AgentCapabilities with .streaming)
      - skills (list of AgentSkill with id, name, description, tags)
      - provider (AgentProvider with organization, url)
    """
    response = client.get("/.well-known/agent.json")
    assert response.status_code == 200
    data = response.json()

    # Real A2A SDK fields (camelCase from protobuf MessageToDict)
    assert data["name"] == "TechVerse Merchant Agent"
    assert data["version"] == "1.0.0"
    assert "description" in data
    assert "supportedInterfaces" in data
    assert len(data["supportedInterfaces"]) >= 1
    assert "url" in data["supportedInterfaces"][0]

    # Real AgentCapabilities
    assert data["capabilities"]["streaming"] is True

    # Real AgentSkill list
    assert "skills" in data
    assert len(data["skills"]) >= 5
    skill_ids = [s["id"] for s in data["skills"]]
    assert "product_vector_search" in skill_ids
    assert "dynamic_margin_upsell" in skill_ids
    assert "ap2_bounded_mandate_verification" in skill_ids
    assert "razorpay_test_checkout" in skill_ids
    assert "dynamodb_audit_ledger" in skill_ids

    # Real AgentProvider
    assert data["provider"]["organization"] == "TechVerse Systems"


def test_a2a_message_endpoint():
    """
    Verifies the /api/a2a/message endpoint handles a real SendMessageRequest
    and returns a real SendMessageResponse with Task in TASK_STATE_COMPLETED.
    """
    payload = {
        "message": {
            "role": 2,  # ROLE_AGENT (2) or ROLE_USER (1) per proto enum
            "parts": [{"text": "I need a 65W GaN charger under 2500 INR"}],
        }
    }
    response = client.post("/api/a2a/message", json=payload)
    assert response.status_code == 200
    data = response.json()

    # Real SendMessageResponse with Task
    assert "task" in data
    task = data["task"]
    assert "id" in task
    assert "status" in task
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"

    # Task contains a reply message from the merchant agent
    reply_msg = task["status"]["message"]
    assert reply_msg["role"] == "ROLE_AGENT"
    assert len(reply_msg["parts"]) >= 1
    assert len(reply_msg["parts"][0]["text"]) > 10


def test_mcp_jsonrpc_tools_list():
    """Verifies MCP tools/list returns real mcp.types.ListToolsResult serialized tools."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {},
    }
    response = client.post("/mcp", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["jsonrpc"] == "2.0"
    assert "tools" in data["result"]
    tools = data["result"]["tools"]
    assert len(tools) >= 4

    # Verify real mcp.types.Tool structure (name, description, inputSchema)
    tool_names = [t["name"] for t in tools]
    assert "merchant_search_catalog" in tool_names
    assert "merchant_evaluate_upsell" in tool_names
    assert "merchant_create_cart" in tool_names
    assert "merchant_checkout_with_ap2" in tool_names

    # Each tool has a real inputSchema (not just a hand-crafted dict)
    for tool in tools:
        assert "name" in tool
        assert "description" in tool
        assert "inputSchema" in tool
        assert tool["inputSchema"]["type"] == "object"


def test_mcp_jsonrpc_tools_call_search():
    """Verifies MCP tools/call returns real mcp.types.CallToolResult with TextContent."""
    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "merchant_search_catalog",
            "arguments": {
                "query": "65W GaN fast charger",
                "max_budget_inr": 2500.0,
            },
        },
    }
    response = client.post("/mcp", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["jsonrpc"] == "2.0"
    assert "content" in data["result"]
    assert data["result"]["isError"] is False

    # Real mcp.types.TextContent
    content = data["result"]["content"]
    assert len(content) >= 1
    assert content[0]["type"] == "text"
    assert "Found" in content[0]["text"]


def test_mcp_jsonrpc_tools_call_upsell():
    """Verifies MCP upsell tool returns structured CallToolResult."""
    payload = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "merchant_evaluate_upsell",
            "arguments": {
                "product_id": "prod_charger_65w_gan",
                "current_mandate_limit": 2500.0,
            },
        },
    }
    response = client.post("/mcp", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["result"]["isError"] is False
    text = data["result"]["content"][0]["text"]
    assert "Upsell offer" in text or "No upsell" in text


def test_mcp_jsonrpc_unknown_method():
    """Verifies unknown MCP methods return a proper JSON-RPC error."""
    payload = {
        "jsonrpc": "2.0",
        "id": 99,
        "method": "tools/unknown_method",
        "params": {},
    }
    response = client.post("/mcp", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == -32601  # METHOD_NOT_FOUND
