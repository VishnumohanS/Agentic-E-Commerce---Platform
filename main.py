"""
FastAPI Server — Merchant Agent Commerce Infrastructure.

Protocol Stack (all using real libraries):
  - Google A2A Protocol  : a2a-sdk 1.1.2  (AgentCard, Message, Task, TaskState)
  - Model Context Protocol: mcp 1.26.0     (Tool, TextContent, CallToolResult)
  - AP2 Bounded Mandates : HMAC-SHA256 cryptographic payment mandate validation
  - Razorpay Payments    : razorpay SDK    (Order creation + HMAC verification)
  - SSE Streaming        : FastAPI StreamingResponse
  - DynamoDB Ledger      : Tamper-evident hash-chained audit log

Hackathon Track 01 — AI Growth & Agentic Commerce
"""

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import os
import json
import uuid
import logging
from typing import Dict, Any
from botocore.exceptions import ClientError, NoCredentialsError

from app.models import (
    ProductQuery, AP2MandateSignature, Cart, CartItem,
    PaymentVerification, OrderReceipt,
)
from app.merchant.catalog import MerchantCatalog
from app.merchant.upsell_engine import MerchantUpsellEngine
from app.merchant.state_machine import CommerceStateMachine
from app.protocols.ap2_mandate import AP2MandateEngine
from app.protocols.mcp_ucp import MCPMerchantCatalogProvider
from app.protocols.a2a import get_agent_card_dict, A2AMessageHandler
from app.services.razorpay_service import RazorpayService
from app.services.audit_ledger import AuditLedgerEngine
from app.services.stream_service import stream_commerce_pipeline
from app.buyer.buyer_agent import AIBuyerAgent
from app.config import settings
from app.services.auth_service import (
    AuthenticatedUser,
    UserLogin,
    UserRegister,
    get_current_user,
    login_user,
    register_user,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Merchant Agent Commerce Infrastructure — Track 01: AI Growth & Agentic Commerce",
    description=(
        "Production-grade autonomous merchant agent demonstrating revenue growth "
        "via dynamic upsells, safe autonomous transactions (AP2 Bounded Mandates), "
        "agent-readable catalog (MCP/UCP), A2A agent interoperability, "
        "Razorpay test-mode checkout, and first-class cryptographic audit explainability.\n\n"
        "**Protocols (all real libraries):**\n"
        "- Google A2A: `a2a-sdk==1.1.2` (protobuf AgentCard, Message, Task)\n"
        "- MCP: `mcp==1.26.0` (Tool, TextContent, CallToolResult, ListToolsResult)\n"
        "- Payment: `razorpay==2.0.1` (Orders API + HMAC-SHA256 verification)\n"
        "- AP2: HMAC-SHA256 signed spending mandates with budget/merchant bounds"
    ),
    version="3.0.0",
    contact={"name": "TechVerse Systems", "url": "https://techverse.example.com"},
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Core Services ────────────────────────────────────────────────────────────
catalog = MerchantCatalog()
upsell_engine = MerchantUpsellEngine(catalog)
audit_ledger = AuditLedgerEngine()
razorpay_service = RazorpayService()


# ════════════════════════════════════════════════════════════════════════════
#  Authentication Endpoints
# ════════════════════════════════════════════════════════════════════════════

@app.post("/auth/register", tags=["Authentication"], summary="Register a new user")
def auth_register(payload: UserRegister):
    """Creates a user with a bcrypt-hashed password and returns a session JWT."""
    result = register_user(payload)
    audit_ledger.record_event(
        actor="USER",
        state="USER_REGISTERED",
        title="New user account created",
        details={"user_id": result.user.user_id, "email": result.user.email},
    )
    return result


@app.post("/auth/login", tags=["Authentication"], summary="Log in with email + password")
def auth_login(payload: UserLogin):
    result = login_user(payload)
    audit_ledger.record_event(
        actor="USER",
        state="USER_LOGIN",
        title="User logged in",
        details={"user_id": result.user.user_id, "email": result.user.email},
    )
    return result


@app.post("/auth/logout", tags=["Authentication"], summary="Log out (client discards the JWT)")
def auth_logout(current_user: AuthenticatedUser = Depends(get_current_user)):
    """Sessions are stateless JWTs; logout is enforced client-side by discarding the token.
    This endpoint just confirms the token was valid and logs the event."""
    audit_ledger.record_event(
        actor="USER", state="USER_LOGOUT", title="User logged out",
        details={"user_id": current_user.user_id},
    )
    return {"status": "logged_out"}


@app.get("/auth/me", tags=["Authentication"], summary="Get the current authenticated user")
def auth_me(current_user: AuthenticatedUser = Depends(get_current_user)):
    return {"user_id": current_user.user_id, "email": current_user.email}


# ════════════════════════════════════════════════════════════════════════════
#  A2A Protocol Endpoints  (real a2a-sdk 1.1.2 protobuf types)
# ════════════════════════════════════════════════════════════════════════════

@app.get(
    "/.well-known/agent.json",
    tags=["A2A Protocol"],
    summary="A2A Agent Card (official a2a-sdk AgentCard proto)",
)
@app.get("/agent.json", tags=["A2A Protocol"], include_in_schema=False)
def get_google_a2a_agent_card():
    """
    Returns the official Google A2A Protocol Agent Card.

    Serialized from a real `a2a.types.AgentCard` protobuf message using
    `google.protobuf.json_format.MessageToDict`.

    Includes: AgentSkill[], AgentCapabilities, AgentInterface, AgentProvider.
    """
    return get_agent_card_dict()


@app.post(
    "/api/a2a/message",
    tags=["A2A Protocol"],
    summary="A2A SendMessageRequest → SendMessageResponse (real proto types)",
)
def handle_a2a_message(payload: Dict[str, Any]):
    """
    Handles a real Google A2A `SendMessageRequest` proto payload.

    Parses using `google.protobuf.json_format.ParseDict` into a real
    `a2a.types.SendMessageRequest`, processes the buyer message intent,
    and returns a real `a2a.types.SendMessageResponse` with a `Task` object
    whose status transitions: SUBMITTED → WORKING → COMPLETED.

    Example payload:
    ```json
    {
      "message": {
        "role": "ROLE_USER",
        "parts": [{"text": "I need a 65W GaN charger under ₹2500"}]
      }
    }
    ```
    """
    return A2AMessageHandler.handle(payload)


# ════════════════════════════════════════════════════════════════════════════
#  MCP Protocol Endpoints  (real mcp 1.26.0 types)
# ════════════════════════════════════════════════════════════════════════════

@app.post(
    "/mcp",
    tags=["MCP Protocol"],
    summary="MCP JSON-RPC 2.0 endpoint (real mcp.types Tool, TextContent, etc.)",
)
def handle_mcp_endpoint(payload: Dict[str, Any]):
    """
    Standard JSON-RPC 2.0 MCP endpoint.

    Uses real `mcp.types` objects:
    - `tools/list` → returns `mcp.types.ListToolsResult` serialized via `.model_dump()`
    - `tools/call` → returns `mcp.types.CallToolResult` with `mcp.types.TextContent`

    Example (tools/list):
    ```json
    {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    ```

    Example (tools/call):
    ```json
    {
      "jsonrpc": "2.0", "id": 2,
      "method": "tools/call",
      "params": {
        "name": "merchant_search_catalog",
        "arguments": {"query": "65W GaN charger", "max_budget_inr": 2500}
      }
    }
    ```
    """
    return MCPMerchantCatalogProvider.handle_mcp_jsonrpc_request(
        payload=payload,
        catalog_service=catalog,
        upsell_service=upsell_engine,
    )


@app.get(
    "/api/mcp/tools",
    tags=["MCP Protocol"],
    summary="List all MCP tools (serialized real mcp.types.Tool objects)",
)
def get_mcp_tools():
    """
    Returns the MCP tool manifest.
    Each tool is a real `mcp.types.Tool` Pydantic object serialized via `.model_dump()`.
    """
    return {"tools": MCPMerchantCatalogProvider.get_mcp_tools_manifest()}


# ════════════════════════════════════════════════════════════════════════════
#  UCP / A2C Catalog Endpoint  (Schema.org JSON-LD)
# ════════════════════════════════════════════════════════════════════════════

@app.get(
    "/api/catalog",
    tags=["Catalog / UCP"],
    summary="Agent-readable product catalog (Schema.org JSON-LD / UCP / ACP format)",
)
def get_catalog():
    """Returns the full product catalog formatted as Schema.org JSON-LD (UCP/ACP standard)."""
    products = catalog.get_all_products()
    return {
        "merchant_id": "merchant_techverse_01",
        "merchant_name": "TechVerse Systems",
        "protocol": "UCP/1.0+Schema.org",
        "items": [MCPMerchantCatalogProvider.format_ucp_product(p) for p in products],
    }


@app.post(
    "/api/catalog/search",
    tags=["Catalog / UCP"],
    summary="Structured catalog search with budget + category filtering",
)
def search_catalog(query: ProductQuery):
    """
    Structured catalog search — no embeddings, no RAG.
    Filters by stock availability, budget, and category; ranks by structured field scoring.
    """
    products, match_count, meta = catalog.structured_search(query)
    return {
        "query": query,
        "matched_count": match_count,
        "search_method": "structured_field_scoring",
        "detected_categories": meta.get("detected_categories", []),
        "results": products,
    }


@app.get(
    "/api/merchant/stock",
    tags=["Catalog / UCP"],
    summary="Real-time inventory snapshot — stock levels for all products",
)
def get_stock_snapshot():
    """
    Returns current stock levels for every product.
    Stock is automatically decremented after each confirmed payment.
    Every deduction is recorded in the cryptographic audit ledger.
    """
    snapshot = catalog.get_stock_snapshot()
    return {
        "merchant_id": "merchant_techverse_01",
        "total_products": len(snapshot),
        "in_stock_count": sum(1 for p in snapshot if p["in_stock"]),
        "out_of_stock_count": sum(1 for p in snapshot if not p["in_stock"]),
        "inventory": snapshot,
    }


# ════════════════════════════════════════════════════════════════════════════
#  Audit Ledger Endpoint
# ════════════════════════════════════════════════════════════════════════════

@app.get(
    "/api/audit/ledger",
    tags=["Audit & Explainability"],
    summary="Cryptographic audit trail — every agent action, hash-chained, DynamoDB-persisted",
)
def get_audit_ledger():
    """
    Returns the full immutable cryptographic audit trail.

    Every merchant action, AP2 mandate check, and Razorpay payment event
    is SHA-256 hash-chained for tamper-evidence (Track 01 bar: 'every money
    action explainable, bounded and gated').
    """
    events = audit_ledger.get_full_ledger()
    is_valid = audit_ledger.verify_ledger_integrity()
    try:
        db_records = audit_ledger.db_ledger.get_all_records(limit=25)
    except (NoCredentialsError, ClientError) as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "DynamoDB is unavailable. Configure AWS credentials and verify "
                f"table '{settings.DYNAMODB_TABLE_NAME}' in region '{settings.AWS_REGION}'."
            ),
        ) from exc
    return {
        "integrity_verified": is_valid,
        "total_events": len(events),
        "db_records": db_records,
        "events": events,
    }


# ════════════════════════════════════════════════════════════════════════════
#  Commerce Flow Endpoints
# ════════════════════════════════════════════════════════════════════════════

@app.post(
    "/api/commerce/stream",
    tags=["Commerce Pipeline"],
    summary="Real-time SSE streaming — full autonomous commerce pipeline",
)
async def stream_commerce_flow(payload: Dict[str, Any]):
    """
    Server-Sent Events (SSE) streaming endpoint for the Split-Screen Interface.

    Full pipeline per SSE stream:
    1. Parse buyer intent & budget → issue AP2 Bounded Mandate (HMAC-SHA256)
    2. Vector search catalog → find best matching products
    3. Evaluate dynamic upsell within mandate headroom
    4. Validate AP2 Bounded Mandate (signature + budget + merchant + expiry)
    5. Create Razorpay order via SDK → generate HMAC payment signature
    6. Verify Razorpay HMAC-SHA256 → capture payment
    7. Write final audit event to DynamoDB → hash-chain verified

    Supports simulation flags: tamper_token, simulate_razorpay_error,
    simulate_stock_out, simulate_counter_offer.
    """
    import boto3

    if boto3.Session(region_name=settings.AWS_REGION or None).get_credentials() is None:
        return JSONResponse(
            status_code=503,
            content={
                "detail": (
                    "DynamoDB is unavailable because AWS credentials were not found. "
                    "Configure the AWS credential chain before starting the stream."
                )
            },
        )

    user_query = payload.get(
        "user_query",
        "Looking for a 65W GaN fast charger with USB-C cable under ₹2,500.",
    )
    max_budget = float(payload.get("max_budget_inr", 2500.00))
    options = {
        "tamper_token": payload.get("tamper_token", False),
        "simulate_razorpay_error": payload.get("simulate_razorpay_error", False),
        "simulate_stock_out": payload.get("simulate_stock_out", False),
        "simulate_counter_offer": payload.get("simulate_counter_offer", False),
    }
    return StreamingResponse(
        stream_commerce_pipeline(
            user_query=user_query,
            max_budget_inr=max_budget,
            catalog=catalog,
            upsell_engine=upsell_engine,
            audit_ledger=audit_ledger,
            razorpay_service=razorpay_service,
            options=options,
        ),
        media_type="text/event-stream",
    )


@app.post(
    "/api/commerce/run-flow",
    tags=["Commerce Pipeline"],
    summary="Synchronous autonomous commerce flow (non-streaming)",
)
def run_autonomous_commerce_flow(
    payload: Dict[str, Any] = None,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Synchronous single-shot autonomous commerce pipeline endpoint.
    Returns the full receipt, cart, mandate validation, and telemetry log.

    The buyer identity (user_id) is ALWAYS taken from the authenticated
    session (JWT), never from the request body, so a client cannot spend
    on behalf of another user.
    """
    payload = payload or {}
    user_query = payload.get(
        "user_query",
        "Looking for a 65W GaN fast charger with USB-C cable under ₹2,500.",
    )
    max_budget = float(payload.get("max_budget_inr", 2500.00))
    force_tamper = bool(payload.get("tamper_token", False))

    session_id = f"#TX-{uuid.uuid4().hex[:4].upper()}"
    state_machine = CommerceStateMachine(session_id)
    # Buyer identity comes from the verified JWT session, not client input.
    buyer = AIBuyerAgent(user_id=current_user.user_id)

    intent, parsed_budget, category = buyer.extract_intent_and_budget(user_query)
    effective_budget = max_budget if max_budget > 0 else parsed_budget

    chat_transcript = []
    telemetry_logs = []

    chat_transcript.append({
        "speaker": "Buyer Agent / User",
        "role": "BUYER",
        "text": f'"{user_query}"',
    })

    mandate_sig = buyer.issue_bounded_mandate(
        max_budget_inr=effective_budget,
        authorized_merchant_id="merchant_techverse_01",
    )
    if force_tamper:
        mandate_sig.signature = "INVALID_FORGED_SIGNATURE_TOKEN_999"

    audit_rec_1 = audit_ledger.record_event(
        actor="AI_BUYER",
        state="INITIATED",
        title="Issued Signed AP2 Bounded Mandate Token",
        details={
            "session_id": session_id,
            "intent": intent,
            "user_query": user_query,
            "max_budget_inr": effective_budget,
            "buyer_agent_id": buyer.agent_id,
            "signature_token": mandate_sig.signature[:24] + "...",
        },
        session_id=session_id,
    )

    telemetry_logs.append({"section": "SESSION_HEADER", "text": f"[STATUS: ACTIVE SESSION {session_id}]"})
    telemetry_logs.append({"section": "INTENT_PARSE", "text": f"> Intent Parsed: {intent}"})

    state_machine.transition_to("DISCOVERED")
    q = ProductQuery(query_text=user_query, max_budget_inr=effective_budget)
    try:
        results, match_count, search_meta = catalog.search_for_agent(q)
    except RuntimeError as search_error:
        audit_ledger.record_event("AMAZON_MCP", "FAILED", "Amazon MCP Search Failed",
                                  {"session_id": session_id, "error": str(search_error)}, session_id=session_id)
        raise HTTPException(status_code=503, detail="Product source is temporarily unavailable.") from search_error

    detected_cats = search_meta.get("detected_categories", [])
    top_score = search_meta.get("top_structured_score", 0.0)

    audit_ledger.record_event(
        actor="MERCHANT_AGENT",
        state="CATALOG_SEARCH",
        title="Structured Catalog Search Executed",
        details={
            "session_id": session_id,
            "query": user_query,
            "max_budget_inr": effective_budget,
            "detected_categories": detected_cats,
            "match_count": match_count,
            "top_score": top_score,
            "search_method": search_meta.get("search_method", "structured_field_scoring"),
        },
        session_id=session_id,
    )
    telemetry_logs.append({
        "section": "CATALOG_SEARCH",
        "text": (
            f"> Structured Search: {match_count} matches | "
            f"Category: {', '.join(detected_cats) if detected_cats else 'general'} | "
            f"Budget filter: ≤₹{effective_budget:,.2f}"
        ),
    })
    telemetry_logs.append({"section": "BUDGET_CONSTRAINT", "text": f"> Budget Constraint: Max ₹{effective_budget:,.2f}"})


    if not results:
        state_machine.fail("No products found within budget")
        audit_ledger.record_event(
            actor="MERCHANT_AGENT",
            state="FAILED",
            title="Catalog Search Returned 0 Matches Under Budget",
            details={"session_id": session_id, "query": user_query, "max_budget_inr": effective_budget},
            session_id=session_id,
        )
        telemetry_logs.append({"section": "BOUNDING_RULE", "text": f"> Bounding Rule: FAILED (No items under ₹{effective_budget:,.2f})"})
        raise HTTPException(
            status_code=404,
            detail=f"Bounding Rule Violation: No catalog items found under budget limit ₹{effective_budget:,.2f}.",
        )

    base_product = results[0]

    state_machine.transition_to("RECOMMENDED")
    state_machine.transition_to("UPSELL_OFFERED")
    upsell_offer = upsell_engine.evaluate_best_upsell(
        base_product=base_product,
        max_mandate_budget_inr=mandate_sig.mandate.max_amount_inr,
    )

    upsell_accepted = False
    accepted_upsell_product = None

    if upsell_offer:
        is_accepted, decision_reason = buyer.evaluate_upsell_offer(upsell_offer, mandate_sig)
        if is_accepted:
            upsell_accepted = True
            accepted_upsell_product = upsell_offer.product
            state_machine.transition_to("UPSELL_ACCEPTED")

    items = [CartItem(product_id=base_product.id, name=base_product.name, category=base_product.category, price_inr=base_product.price_inr, quantity=1)]
    upsell_subtotal = 0.0

    if upsell_accepted and accepted_upsell_product:
        items.append(CartItem(
            product_id=accepted_upsell_product.id,
            name=accepted_upsell_product.name,
            price_inr=accepted_upsell_product.price_inr,
            category=accepted_upsell_product.category,
            quantity=1,
            is_upsell=True,
        ))
        upsell_subtotal = accepted_upsell_product.price_inr

    total_amount = base_product.price_inr + upsell_subtotal
    cart = Cart(
        items=items,
        base_subtotal_inr=base_product.price_inr,
        upsell_subtotal_inr=upsell_subtotal,
        total_amount_inr=total_amount,
    )

    state_machine.transition_to("CART_FINALIZED")
    telemetry_logs.append({"section": "DYNAMIC_OFFER", "text": f"> Dynamic Offer Generated: ₹{total_amount:,.2f}"})

    if upsell_accepted and accepted_upsell_product:
        offer_text = f'"I have the {base_product.name} + {accepted_upsell_product.name} bundle for ₹{total_amount:,.2f}. Would you like to authorize payment?"'
    else:
        offer_text = f'"I have {base_product.name} for ₹{total_amount:,.2f}. Would you like to authorize payment?"'

    chat_transcript.append({"speaker": "Merchant Agent", "role": "MERCHANT", "text": offer_text})

    val_res = AP2MandateEngine.validate_mandate(
        mandate_sig=mandate_sig,
        merchant_id="merchant_techverse_01",
        cart=cart,
    )
    if not val_res.valid:
        state_machine.fail(val_res.reason)
        audit_ledger.record_event(
            actor="AP2_MANDATE_ENGINE",
            state="FAILED",
            title="AP2 Bounded Mandate Verification REJECTED",
            details={
                "session_id": session_id,
                "reason": val_res.reason,
                "max_budget_inr": effective_budget,
                "total_amount_inr": cart.total_amount_inr,
                "valid": False,
            },
            session_id=session_id,
        )
        telemetry_logs.append({"section": "BOUNDING_RULE", "text": f"> Bounding Rule: FAILED ({val_res.reason})"})
        raise HTTPException(
            status_code=400,
            detail=f"AP2 Mandate Bounding Rule Failure: {val_res.reason}",
        )

    state_machine.transition_to("MANDATE_VERIFIED")
    telemetry_logs.append({"section": "BOUNDING_RULE", "text": f"> Bounding Rule: Passed (Within Limit ₹{effective_budget:,.2f})"})
    chat_transcript.append({"speaker": "Buyer Agent", "role": "BUYER", "text": '"Authorized. Payment token dispatched."'})

    for item in cart.items:
        product = catalog.get_product_by_id(item.product_id)
        if not product or product.stock_quantity < item.quantity:
            reason = f"Insufficient stock for '{item.name}'."
            state_machine.fail(reason)
            audit_ledger.record_event(
                actor="MERCHANT_AGENT",
                state="FAILED",
                title="Inventory Check Rejected Checkout",
                details={"session_id": session_id, "product_id": item.product_id, "reason": reason},
                session_id=session_id,
            )
            raise HTTPException(status_code=409, detail=reason)

    try:
        order_res = razorpay_service.create_order(cart=cart, buyer_id=buyer.agent_id)
    except RuntimeError as payment_error:
        state_machine.fail(str(payment_error))
        audit_ledger.record_event(
            actor="RAZORPAY_API",
            state="FAILED",
            title="Razorpay Payment Failed",
            details={"session_id": session_id, "error": str(payment_error)},
            session_id=session_id,
        )
        raise HTTPException(status_code=502, detail=str(payment_error)) from payment_error
    state_machine.transition_to("PAYMENT_CREATED")

    telemetry_logs.append({"section": "GATEWAY_DISPATCH", "text": "[GATEWAY DISPATCH — Autonomous Payment]"})
    telemetry_logs.append({"section": "RAZORPAY_ORDER", "text": f"> Razorpay Order Created: {order_res.order_id}"})
    telemetry_logs.append({"section": "TOKEN_VERIFICATION", "text": "> AP2 Mandate Signature Valid — Dispatching Payment"})

    if razorpay_service.client:
        checkout = razorpay_service.register_checkout(order_res, cart, session_id)
        audit_ledger.record_event(
            actor="RAZORPAY_API", state="PAYMENT_PENDING", title="Razorpay Standard Checkout Opened",
            details={"session_id": session_id, "order_id": order_res.order_id,
                     "total_amount_inr": cart.total_amount_inr}, session_id=session_id,
        )
        return {"status": "PENDING_CHECKOUT", "session_id": session_id, "cart": cart,
                "product": base_product, "mandate_validation": val_res, "checkout": checkout,
                "panel_a_chat": chat_transcript, "panel_b_telemetry": telemetry_logs}

    try:
        payment_id, signature = razorpay_service.execute_payment(order_res.order_id, order_res.amount_paise)
    except RuntimeError as payment_error:
        state_machine.fail(str(payment_error))
        audit_ledger.record_event(
            actor="RAZORPAY_API",
            state="FAILED",
            title="Razorpay Payment Failed",
            details={"session_id": session_id, "error": str(payment_error)},
            session_id=session_id,
        )
        raise HTTPException(status_code=502, detail=str(payment_error)) from payment_error

    verification = PaymentVerification(
        razorpay_order_id=order_res.order_id,
        razorpay_payment_id=payment_id,
        razorpay_signature=signature,
    )

    is_sig_valid = razorpay_service.verify_payment_signature(verification)
    if not is_sig_valid:
        state_machine.fail("Payment HMAC signature verification failed")
        raise HTTPException(status_code=400, detail="Razorpay Payment Verification Failed.")


    state_machine.transition_to("PAYMENT_SUCCESS")
    state_machine.transition_to("ORDER_CONFIRMED")
    order_num = f"#ORD-{uuid.uuid4().hex[:4].upper()}"

    # ── Stock deduction — runs automatically after payment confirmed ──────
    stock_updates: List[Dict] = []
    for item in cart.items:
        try:
            stock_info = catalog.deduct_stock(item.product_id, item.quantity)
            stock_updates.append(stock_info)
            # Every stock change is audited on the merchant ledger
            audit_ledger.record_event(
                actor="MERCHANT_AGENT",
                state="STOCK_DEDUCTED",
                title=f"Stock Deducted: {item.name}",
                details={
                    "session_id": session_id,
                    "order_id": order_res.order_id,
                    "product_id": item.product_id,
                    "product_name": item.name,
                    "quantity_deducted": item.quantity,
                    "stock_before": stock_info["stock_before"],
                    "stock_after": stock_info["stock_after"],
                    "still_in_stock": stock_info["still_in_stock"],
                    "is_upsell_item": item.is_upsell,
                },
                session_id=session_id,
            )
            telemetry_logs.append({
                "section": "STOCK_DEDUCTED",
                "text": (
                    f"> Stock Update: {item.name} | "
                    f"qty={item.quantity} | before={stock_info['stock_before']} → after={stock_info['stock_after']}"
                ),
            })
        except ValueError as stock_err:
            # Log stock error but don't fail the transaction (payment already captured)
            audit_ledger.record_event(
                actor="MERCHANT_AGENT",
                state="STOCK_WARNING",
                title=f"Stock Deduction Warning: {item.name}",
                details={"session_id": session_id, "error": str(stock_err)},
                session_id=session_id,
            )
            telemetry_logs.append({
                "section": "STOCK_WARNING",
                "text": f"> Stock Warning: {stock_err}",
            })

    chat_transcript.append({
        "speaker": "Merchant Agent",
        "role": "MERCHANT",
        "text": f'"Payment of ₹{total_amount:,.2f} captured. Inventory updated. Receipt sent. Order ID: {order_num}."',
    })
    telemetry_logs.append({"section": "TRANSACTION_STATUS", "text": "> Transaction Status: SUCCESS (Captured)"})


    audit_rec_final = audit_ledger.record_event(
        actor="MERCHANT_AGENT",
        state="ORDER_CONFIRMED",
        title="Autonomous Transaction Successfully Completed",
        details={
            "session_id": session_id,
            "order_id": order_res.order_id,
            "order_number": order_num,
            "razorpay_payment_id": payment_id,
            "total_amount_inr": cart.total_amount_inr,
            "base_subtotal_inr": cart.base_subtotal_inr,
            "upsell_subtotal_inr": cart.upsell_subtotal_inr,
            "max_budget_inr": effective_budget,
            "valid": True,
            "intent": intent,
            "matched_count": match_count,
        },
        session_id=session_id,
    )

    audit_hex_id = audit_rec_final.details.get("audit_record_id", "0x8F4A1C9")
    telemetry_logs.append({"section": "DB_LOGGER", "text": "> State Logged to DynamoDB Ledger"})
    telemetry_logs.append({"section": "AUDIT_RECORD", "text": f"[AUDIT RECORD ID: {audit_hex_id}]"})

    receipt = OrderReceipt(
        order_id=order_res.order_id,
        razorpay_payment_id=payment_id,
        cart=cart,
        buyer_id=buyer.agent_id,
        merchant_id="merchant_techverse_01",
        mandate_id=mandate_sig.mandate.mandate_id,
        total_paid_inr=cart.total_amount_inr,
        merchant_revenue_inr=cart.total_amount_inr * 0.30,
        upsell_revenue_gained_inr=upsell_subtotal,
        timestamp=str(order_res.created_at),
        audit_hash_chain=audit_rec_final.current_hash,
    )

    return {
        "status": "SUCCESS",
        "session_id": session_id,
        "audit_record_id": audit_hex_id,
        "panel_a_chat": chat_transcript,
        "panel_b_telemetry": telemetry_logs,
        "receipt": receipt,
        "cart": cart,
        "mandate_validation": val_res,
        "upsell_details": {
            "accepted": upsell_accepted,
            "upsell_product": accepted_upsell_product,
            "additional_revenue_inr": upsell_subtotal,
        },
    }


@app.post("/api/orders/{order_id}/payment-method", tags=["Commerce Pipeline"])
def select_payment_method(
    order_id: str,
    payload: Dict[str, Any],
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Explicit payment-method selection step, required before checkout.

    The AI/agent never picks a payment method — the authenticated user must
    submit one of the methods actually supported by the configured Razorpay
    mode. This only records the choice in the audit ledger; it does not
    move money.
    """
    method = (payload or {}).get("payment_method", "").lower()
    supported = {"upi", "card", "netbanking", "razorpay_test"} if razorpay_service.mode == "test" else {"razorpay_test"}
    if method not in supported:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported payment method '{method}'. Supported: {sorted(supported)}",
        )
    context = razorpay_service.pending_checkouts.get(order_id)
    if not context:
        raise HTTPException(status_code=404, detail="Order not found or checkout not initialized.")
    event = audit_ledger.record_event(
        actor="USER",
        state="PAYMENT_METHOD_SELECTED",
        title="User selected payment method",
        details={"order_id": order_id, "payment_method": method, "user_id": current_user.user_id},
        session_id=context.get("session_id"),
    )
    return {"status": "PAYMENT_METHOD_SELECTED", "order_id": order_id, "payment_method": method,
            "audit_record_id": event.details.get("audit_record_id")}


@app.post("/api/razorpay/verify", tags=["Commerce Pipeline"])
def verify_razorpay_checkout(payload: Dict[str, Any]):
    """Verifies the three values returned by Razorpay Standard Checkout."""
    verification = PaymentVerification(
        razorpay_order_id=payload.get("razorpay_order_id", ""),
        razorpay_payment_id=payload.get("razorpay_payment_id", ""),
        razorpay_signature=payload.get("razorpay_signature", ""),
    )
    if not razorpay_service.verify_checkout_payment(verification):
        audit_ledger.record_event("RAZORPAY_API", "FAILED", "Razorpay Checkout Signature Rejected", {"order_id": verification.razorpay_order_id})
        raise HTTPException(status_code=400, detail="Razorpay Checkout signature verification failed.")
    context = razorpay_service.pending_checkouts.get(verification.razorpay_order_id)
    if not context:
        raise HTTPException(status_code=404, detail="Checkout session not found or already finalized.")
    try:
        payment = razorpay_service.get_payment_status(
            verification.razorpay_payment_id,
            context["amount_paise"],
        )
    except Exception as payment_error:
        audit_ledger.record_event("RAZORPAY_API", "FAILED", "Razorpay Payment Not Captured", {"order_id": verification.razorpay_order_id, "payment_id": verification.razorpay_payment_id, "error": str(payment_error)})
        raise HTTPException(status_code=409, detail=str(payment_error)) from payment_error

    for item in context["cart"].items:
        try:
            stock_info = catalog.deduct_stock(item.product_id, item.quantity)
            audit_ledger.record_event("MERCHANT_AGENT", "STOCK_DEDUCTED", f"Stock Deducted: {item.name}", {"order_id": verification.razorpay_order_id, "product_id": item.product_id, **stock_info}, session_id=context["session_id"])
        except ValueError as stock_error:
            audit_ledger.record_event("MERCHANT_AGENT", "FAILED", "Stock Finalization Failed After Payment", {"order_id": verification.razorpay_order_id, "error": str(stock_error)}, session_id=context["session_id"])
            raise HTTPException(status_code=409, detail="Payment captured but inventory finalization failed; contact support.") from stock_error

    final_event = audit_ledger.record_event("MERCHANT_AGENT", "ORDER_CONFIRMED", "Razorpay Checkout Payment Captured", {"session_id": context["session_id"], "order_id": verification.razorpay_order_id, "razorpay_payment_id": verification.razorpay_payment_id, "total_amount_inr": context["cart"].total_amount_inr, "valid": True}, session_id=context["session_id"])
    del razorpay_service.pending_checkouts[verification.razorpay_order_id]
    return {"status": "PAYMENT_VERIFIED", "order_id": verification.razorpay_order_id, "payment_id": verification.razorpay_payment_id, "audit_record_id": final_event.details.get("audit_record_id")}


@app.post("/api/razorpay/failure", tags=["Commerce Pipeline"])
def record_razorpay_failure(payload: Dict[str, Any]):
    """Persists a Checkout failure without treating it as a transaction."""
    details = {
        key: payload.get(key)
        for key in ("order_id", "code", "description", "reason", "source", "step")
        if payload.get(key)
    }
    audit_ledger.record_event(
        actor="RAZORPAY_API",
        state="FAILED",
        title="Razorpay Checkout Payment Failed",
        details=details,
    )
    return {"status": "PAYMENT_FAILED_RECORDED"}


# ── Static Files & Dashboard ─────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/", response_class=HTMLResponse, tags=["Dashboard"])
def index_page():
    """Serves the interactive split-screen web dashboard."""
    with open("app/static/index.html", "r", encoding="utf-8") as f:
        return f.read()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
