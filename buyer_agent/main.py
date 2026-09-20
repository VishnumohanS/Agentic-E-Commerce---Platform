"""
Buyer Agent — FastAPI Service (port 8001).

Endpoints:
  GET  /                        → Split-screen buyer UI
  GET  /api/buyer/health        → Service health + API key status
  GET  /api/buyer/merchant/card → Merchant A2A agent card
  GET  /api/buyer/merchant/tools→ Merchant MCP tool list
  GET  /api/buyer/run           → SSE streaming purchase pipeline (query params)
  POST /api/buyer/a2a/send      → Send A2A message to merchant
  POST /api/buyer/mcp/call      → Call merchant MCP tool

Run from project root:
  python buyer_agent/main.py        ← direct
  python -m buyer_agent.main        ← as module
"""

import sys, os
# Ensure project root is on the path when run directly
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

import uvicorn
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.services.ai_service import ai_service
from buyer_agent.agent.buyer_core import BuyerCore
from buyer_agent.agent.a2a_client import A2AClient
from buyer_agent.agent.mcp_client import MCPClient

app = FastAPI(
    title="AI Buyer Agent — Agentic Commerce",
    description=(
        "Autonomous buyer agent that uses real A2A Protocol (a2a-sdk), "
        "real MCP tools (mcp library), AP2 Bounded Mandates, and AWS Bedrock "
        "to discover products, negotiate upsells, and complete Razorpay payments."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="buyer_agent/static"), name="static")


# ── UI ────────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse, tags=["UI"])
async def index():
    with open("buyer_agent/static/index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/api/buyer/health", tags=["Status"])
async def health():
    missing = settings.get_missing_keys()
    if settings.ai_provider() == "bedrock" and not ai_service.is_available:
        missing.append("AWS credentials")
    return {
        "status": "ok",
        "ai_provider": settings.ai_provider(),
        "ai_mode": ai_service.mode,
        "razorpay_mode": settings.razorpay_mode(),
        "merchant_url": settings.MERCHANT_AGENT_URL,
        "missing_keys": missing,
        "all_configured": len(missing) == 0,
    }


# ── Merchant discovery ────────────────────────────────────────────────────────
@app.get("/api/buyer/merchant/card", tags=["Merchant Discovery"])
async def merchant_card():
    """Fetches the merchant's real A2A AgentCard from /.well-known/agent.json"""
    client = A2AClient(settings.MERCHANT_AGENT_URL)
    try:
        return await client.get_agent_card()
    except Exception as e:
        return {"error": str(e), "merchant_url": settings.MERCHANT_AGENT_URL}


@app.get("/api/buyer/merchant/tools", tags=["Merchant Discovery"])
async def merchant_tools():
    """Fetches the merchant's MCP tools list."""
    client = MCPClient(settings.MERCHANT_AGENT_URL)
    return await client.list_tools()


# ── SSE Pipeline — GET with query params (works with EventSource) ─────────────
@app.get("/api/buyer/run", tags=["Pipeline"])
async def run_pipeline_get(user_query: str = "65W GaN charger under 2500", max_budget_inr: float = 2500.0):
    """
    Server-Sent Events streaming endpoint for the autonomous purchase pipeline.
    Used by the buyer UI's EventSource connection.

    Query params:
      user_query      - Natural language purchase request
      max_budget_inr  - Maximum budget in INR
    """
    core = BuyerCore()
    return StreamingResponse(
        core.run_pipeline(user_query, max_budget_inr),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Direct protocol endpoints ─────────────────────────────────────────────────
@app.post("/api/buyer/a2a/send", tags=["A2A Protocol"])
async def a2a_send(body: dict):
    """
    Send a real A2A SendMessageRequest to the merchant agent.
    Body: {"text": "message text", "context_id": "optional"}
    """
    client = A2AClient(settings.MERCHANT_AGENT_URL)
    return await client.send_message(
        text=body.get("text", ""),
        context_id=body.get("context_id"),
    )


@app.post("/api/buyer/mcp/call", tags=["MCP Protocol"])
async def mcp_call(body: dict):
    """
    Call a merchant MCP tool directly.
    Body: {"tool": "merchant_search_catalog", "arguments": {...}}
    """
    client = MCPClient(settings.MERCHANT_AGENT_URL)
    result, is_error = await client.call_tool(
        name=body.get("tool", ""),
        arguments=body.get("arguments", {}),
    )
    return {"result": result, "is_error": is_error}


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(
        "buyer_agent.main:app",
        host="0.0.0.0",
        port=settings.BUYER_AGENT_PORT,
        reload=True,
        log_level="info",
    )
