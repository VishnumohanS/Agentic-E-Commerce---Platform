"""
AI Buyer Core — Full Autonomous Purchase Pipeline.

Powered by AWS Bedrock for:
- Natural-language intent parsing (structured JSON output)
- Upsell ROI reasoning
- Negotiation logic

Pipeline steps emitted as SSE events:
  CONFIG_ERROR → MERCHANT_STATUS → INTENT_PARSED → MANDATE_ISSUED
  → MCP_CALL (search) → MCP_RESULT → MCP_CALL (upsell) → MCP_RESULT
  → A2A_SENT → A2A_RECEIVED → COMMERCE_PIPELINE events → COMPLETE
"""

import json
import uuid
import logging
import httpx
from typing import AsyncGenerator

from app.config import settings
from buyer_agent.agent.a2a_client import A2AClient
from buyer_agent.agent.mcp_client import MCPClient
from buyer_agent.agent.ap2_mandate import create_signed_mandate
from app.services.ai_service import ai_service

logger = logging.getLogger(__name__)


class BuyerCore:
    def __init__(self):
        self.merchant_url = settings.MERCHANT_AGENT_URL
        self.a2a = A2AClient(self.merchant_url)
        self.mcp = MCPClient(self.merchant_url)
        if not ai_service.is_available:
            logger.warning("AWS Bedrock is unavailable; using rule-based AI fallback.")

    # ── SSE helper ───────────────────────────────────────────────────────────
    def _sse(self, type_: str, **kwargs) -> str:
        return f"data: {json.dumps({'type': type_, **kwargs})}\n\n"

    # ── Bedrock intent parsing ─────────────────────────────────────────────────
    async def parse_intent(self, query: str, budget: float) -> dict:
        if ai_service.is_available:
            try:
                prompt = (
                    f"Parse this purchase request and return ONLY valid JSON with keys "
                    f"intent, budget_inr, category, constraints. Request: {query!r}. "
                    f"User max budget: {budget}. Category must be one of charging, laptops, "
                    f"peripherals, displays, audio, electronics."
                )
                parsed = json.loads(ai_service.generate(prompt).replace("```json", "").replace("```", "").strip())
                return {
                    "intent": str(parsed.get("intent", query)),
                    "budget_inr": float(parsed.get("budget_inr", budget)),
                    "category": str(parsed.get("category", "electronics")),
                    "constraints": list(parsed.get("constraints", [])),
                }
            except Exception as exc:
                logger.warning("Bedrock intent parse failed: %s", exc)
        # Regex fallback when Bedrock is unavailable or returns invalid output.
        import re
        category = "electronics"
        for kw, cat in [
            ("charger", "charging"), ("cable", "charging"),
            ("laptop", "laptops"), ("mouse", "peripherals"),
            ("keyboard", "peripherals"), ("monitor", "displays"),
            ("headphone", "audio"), ("earphone", "audio"),
        ]:
            if kw in query.lower():
                category = cat
                break
        return {
            "intent": f"Hardware procurement: {query[:80]}",
            "budget_inr": budget,
            "category": category,
            "constraints": [],
        }

    # ── Main Pipeline ─────────────────────────────────────────────────────────
    async def run_pipeline(self, query: str, budget: float) -> AsyncGenerator[str, None]:
        session_id = f"#BUY-{uuid.uuid4().hex[:6].upper()}"
        missing_keys = settings.get_missing_keys()

        # ── Step 0: Config check ──────────────────────────────────────────────
        if missing_keys:
            yield self._sse(
                "CONFIG_ERROR",
                missing_keys=missing_keys,
                message=(
                    f"Missing: {', '.join(missing_keys)}. "
                    "Running with simulated Razorpay + rule-based AI. "
                    "Add keys to .env for full live mode."
                ),
                setup_urls={
                    "AWS_REGION": "https://docs.aws.amazon.com/bedrock/latest/userguide/setting-up.html",
                    "RAZORPAY": "https://dashboard.razorpay.com",
                },
            )

        # ── Step 1: Merchant connectivity ─────────────────────────────────────
        online = await self.a2a.check_merchant_online()
        yield self._sse("MERCHANT_STATUS", online=online, url=self.merchant_url)
        if not online:
            yield self._sse("SYSTEM", message=f"Merchant agent at {self.merchant_url} is not reachable. Start it with: python main.py")
            yield self._sse("ERROR", detail=f"Merchant agent offline at {self.merchant_url}")
            return

        yield self._sse("SYSTEM", message=f"Session {session_id} started. Merchant online ✓")

        # ── Step 2: Parse intent with Bedrock ──────────────────────────────────
        yield self._sse("SYSTEM", message=f"Parsing purchase intent with {settings.ai_provider()}…")
        intent = await self.parse_intent(query, budget)
        effective_budget = intent["budget_inr"]

        yield self._sse(
            "INTENT_PARSED",
            intent=intent["intent"],
            budget_inr=effective_budget,
            category=intent["category"],
            constraints=intent["constraints"],
        )
        yield self._sse("CHAT_MESSAGE", role="BUYER", text=f'"{query}"')
        yield self._sse("SYSTEM", message=f"Intent: {intent['intent']} | Budget: ₹{effective_budget:,.0f} | Category: {intent['category']}")

        # ── Step 3: Issue AP2 Bounded Mandate ─────────────────────────────────
        mandate = create_signed_mandate(
            buyer_agent_id="buyer_agent_alpha_01",
            user_id="user_dev",
            max_amount_inr=effective_budget,
            authorized_merchant_id="merchant_techverse_01",
            secret=settings.AP2_MANDATE_SECRET,
        )
        yield self._sse(
            "MANDATE_ISSUED",
            mandate_id=mandate["mandate"]["mandate_id"],
            max_amount=effective_budget,
            merchant="merchant_techverse_01",
            expires_at=mandate["mandate"]["expires_at"],
            signature_preview=mandate["signature"][:24] + "…",
        )
        yield self._sse("CHAT_MESSAGE", role="BUYER", text=f'Issued AP2 Bounded Mandate [{mandate["mandate"]["mandate_id"]}] — Max ₹{effective_budget:,.2f}, bound to merchant_techverse_01.')

        # ── Step 4: MCP — search catalog ──────────────────────────────────────
        yield self._sse("SYSTEM", message="Calling merchant MCP: merchant_search_catalog…")
        yield self._sse("MCP_CALL", tool="merchant_search_catalog", args={"query": query, "max_budget_inr": effective_budget})
        search_text, search_err = await self.mcp.search_catalog(query, effective_budget)
        yield self._sse("MCP_RESULT", tool="merchant_search_catalog", result=search_text, success=not search_err)
        if not search_err:
            yield self._sse("CHAT_MESSAGE", role="MERCHANT", text=search_text)

        # Extract top product ID from search result for upsell call
        # Catalog uses consistent product IDs — pick first match by category
        product_id_map = {
            "charging":    "prod_charger_65w_gan",
            "laptops":     "prod_laptop_dev_65k",
            "peripherals": "prod_mouse_ergo_3k",
            "displays":    "prod_monitor_4k_35k",
            "audio":       "prod_headphones_anc_12k",
            "electronics": "prod_charger_65w_gan",
        }
        top_product_id = product_id_map.get(intent["category"], "prod_charger_65w_gan")

        # ── Step 5: MCP — evaluate upsell ─────────────────────────────────────
        yield self._sse("SYSTEM", message="Calling merchant MCP: merchant_evaluate_upsell…")
        yield self._sse("MCP_CALL", tool="merchant_evaluate_upsell", args={"product_id": top_product_id, "current_mandate_limit": effective_budget})
        upsell_text, upsell_err = await self.mcp.evaluate_upsell(top_product_id, effective_budget)
        yield self._sse("MCP_RESULT", tool="merchant_evaluate_upsell", result=upsell_text, success=not upsell_err)
        if not upsell_err:
            yield self._sse("CHAT_MESSAGE", role="MERCHANT", text=upsell_text)

        # ── Step 6: A2A message to merchant ───────────────────────────────────
        a2a_text = (
            f"Initiating purchase: {intent['intent']}. "
            f"AP2 Mandate [{mandate['mandate']['mandate_id']}] authorized up to ₹{effective_budget:,.2f}. "
            f"Please proceed with checkout."
        )
        yield self._sse("SYSTEM", message="Sending A2A SendMessageRequest to merchant agent…")
        yield self._sse("A2A_SENT", message=a2a_text, to=f"{self.merchant_url}/api/a2a/message")
        yield self._sse("CHAT_MESSAGE", role="BUYER", text=a2a_text)

        try:
            a2a_resp = await self.a2a.send_message(a2a_text)
            task_state = a2a_resp.get("task", {}).get("status", {}).get("state", "TASK_STATE_COMPLETED")
            reply_parts = a2a_resp.get("task", {}).get("status", {}).get("message", {}).get("parts", [])
            reply_text = reply_parts[0].get("text", "Acknowledged.") if reply_parts else "Acknowledged."
            task_id = a2a_resp.get("task", {}).get("id", uuid.uuid4().hex)

            yield self._sse("A2A_RECEIVED", task_id=task_id, state=task_state, reply=reply_text)
            yield self._sse("CHAT_MESSAGE", role="MERCHANT", text=reply_text)
        except Exception as e:
            logger.warning("A2A send failed: %s", e)
            yield self._sse("A2A_RECEIVED", task_id="err", state="TASK_STATE_FAILED", reply=str(e))

        # ── Step 7: Full commerce pipeline — proxy merchant's LIVE SSE stream ──
        yield self._sse("SYSTEM", message="Connecting to merchant's live commerce stream…")

        stream_url = f"{self.merchant_url}/api/commerce/stream"
        stream_payload = {
            "user_query": query,
            "max_budget_inr": effective_budget,
            "tamper_token": False,
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as http:
                async with http.stream("POST", stream_url, json=stream_payload) as resp:
                    if resp.status_code != 200:
                        body = await resp.aread()
                        yield self._sse("ERROR", detail=f"Merchant stream error {resp.status_code}: {body.decode()[:200]}")
                        return

                    receipt_data = {}
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if not raw:
                            continue
                        try:
                            ev = json.loads(raw)
                        except Exception:
                            continue

                        ev_type = ev.get("type", "")

                        if ev_type == "PANEL_A":
                            # Chat message from merchant's stream
                            yield self._sse(
                                "CHAT_MESSAGE",
                                role=ev.get("role", "MERCHANT"),
                                text=ev.get("text", ""),
                            )

                        elif ev_type == "PANEL_B":
                            # Telemetry → map to commerce pipeline event
                            section = ev.get("section", "SYSTEM")
                            text = ev.get("text", "")
                            yield self._sse(
                                "COMMERCE_PIPELINE",
                                step=section,
                                data={"text": text, "badge": _telemetry_badge(section)},
                            )
                            # Surface payment/stock events separately for UI tabs
                            if "RAZORPAY_ORDER" in section:
                                yield self._sse("COMMERCE_PIPELINE", step="RAZORPAY_ORDER", data={"text": text})
                            elif "STOCK_DEDUCTED" in section:
                                yield self._sse("COMMERCE_PIPELINE", step="STOCK_DEDUCTED", data={"text": text})

                        elif ev_type == "COMPLETE":
                            payload = ev.get("payload", {})
                            receipt_data = payload.get("receipt", {})
                            yield self._sse(
                                "COMPLETE",
                                receipt=receipt_data,
                                session_id=session_id,
                            )

                        elif ev_type == "CHECKOUT_REQUIRED":
                            # Preserve the merchant's user-initiated checkout event;
                            # the buyer UI must not silently replace it with a payment.
                            yield self._sse("CHECKOUT_REQUIRED", order=ev.get("order", {}),
                                            merchant_url=self.merchant_url)

                        elif ev_type == "ERROR":
                            yield self._sse("ERROR", detail=ev.get("detail", "Commerce pipeline error"))
                            return

        except httpx.ConnectError:
            yield self._sse("ERROR", detail=f"Cannot connect to merchant at {self.merchant_url}. Is it running?")
        except Exception as e:
            logger.exception("Commerce stream proxy error: %s", e)
            yield self._sse("ERROR", detail=f"Stream error: {e}")



def _telemetry_badge(section: str) -> str:
    mapping = {
        "RAZORPAY_ORDER": "RAZORPAY",
        "TOKEN_VERIFICATION": "RAZORPAY",
        "PAYMENT_VERIFIED": "RAZORPAY",
        "BOUNDING_RULE": "MANDATE",
        "VECTOR_SEARCH": "MCP",
        "INTENT_PARSE": "INTENT",
        "DB_LOGGER": "SYSTEM",
        "AUDIT_RECORD": "SYSTEM",
        "GATEWAY_DISPATCH": "RAZORPAY",
    }
    return mapping.get(section, "SYSTEM")
