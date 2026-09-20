"""
Server-Sent Events (SSE) Real-Time Streaming Service for Split-Screen Interface.
Streams turn-by-turn AI dialogue into Panel A and real-time execution telemetry into Panel B.
"""

import asyncio
import json
import uuid
import os
from typing import AsyncGenerator, Dict, Any

from app.models import ProductQuery, Cart, CartItem, PaymentVerification, OrderReceipt
from app.merchant.catalog import MerchantCatalog
from app.merchant.upsell_engine import MerchantUpsellEngine
from app.merchant.state_machine import CommerceStateMachine
from app.protocols.ap2_mandate import AP2MandateEngine
from app.services.razorpay_service import RazorpayService
from app.services.audit_ledger import AuditLedgerEngine
from app.buyer.buyer_agent import AIBuyerAgent

async def stream_commerce_pipeline(
    user_query: str,
    max_budget_inr: float,
    catalog: MerchantCatalog,
    upsell_engine: MerchantUpsellEngine,
    audit_ledger: AuditLedgerEngine,
    razorpay_service: RazorpayService,
    options: Dict[str, Any] = None
) -> AsyncGenerator[str, None]:
    """
    Async generator yielding real-time SSE messages for Panel A (Live Chat) and Panel B (Telemetry).
    """
    options = options or {}
    tamper_token = options.get("tamper_token", False)
    simulate_razorpay_error = options.get("simulate_razorpay_error", False)
    simulate_stock_out = options.get("simulate_stock_out", False)
    simulate_counter_offer = options.get("simulate_counter_offer", False)

    session_id = f"#TX-{uuid.uuid4().hex[:4].upper()}"
    state_machine = CommerceStateMachine(session_id)
    buyer = AIBuyerAgent()

    # Step 1: Parse Intent & Budget
    intent, parsed_budget, category = buyer.extract_intent_and_budget(user_query)
    effective_budget = max_budget_inr if max_budget_inr > 0 else parsed_budget

    # Panel A Chat Line 1
    msg1 = {"type": "PANEL_A", "role": "BUYER", "speaker": "Buyer Agent / User", "text": f'"{user_query}"'}
    yield f"data: {json.dumps(msg1)}\n\n"
    await asyncio.sleep(0.3)

    # Panel B Telemetry Header
    t_header = {"type": "PANEL_B", "section": "SESSION_HEADER", "text": f"[STATUS: ACTIVE SESSION {session_id}]"}
    yield f"data: {json.dumps(t_header)}\n\n"
    await asyncio.sleep(0.2)

    t_intent = {"type": "PANEL_B", "section": "INTENT_PARSE", "text": f"> Intent Parsed: {intent}"}
    yield f"data: {json.dumps(t_intent)}\n\n"
    await asyncio.sleep(0.2)

    # Issue AP2 Bounded Mandate
    mandate_sig = buyer.issue_bounded_mandate(max_budget_inr=effective_budget, authorized_merchant_id="merchant_techverse_01")
    if tamper_token:
        mandate_sig.signature = "INVALID_FORGED_SIGNATURE_TOKEN_999"

    audit_ledger.record_event(
        actor="AI_BUYER",
        state="INITIATED",
        title="Issued Signed AP2 Bounded Mandate Token",
        details={
            "session_id": session_id,
            "intent": intent,
            "user_query": user_query,
            "max_budget_inr": effective_budget,
            "buyer_agent_id": buyer.agent_id,
            "signature_token": mandate_sig.signature[:24] + "..."
        },
        session_id=session_id
    )

    # Step 2: Structured Catalog Search
    state_machine.transition_to("DISCOVERED")

    if simulate_stock_out:
        catalog.set_stock_status("prod_charger_65w_gan", False)
        catalog.set_stock_status("prod_laptop_dev_65k", False)

    q = ProductQuery(query_text=user_query, max_budget_inr=effective_budget)
    try:
        results, match_count, search_meta = catalog.search_for_agent(q)
    except RuntimeError:
        yield f"data: {json.dumps({'type': 'ERROR', 'detail': 'Amazon product source is temporarily unavailable.'})}\n\n"
        return
    detected_cats = search_meta.get("detected_categories", [])

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
            "search_method": "structured_field_scoring",
        },
        session_id=session_id,
    )

    t_search = {
        "type": "PANEL_B",
        "section": "CATALOG_SEARCH",
        "text": (
            f"> Catalog Search: {match_count} matches | "
            f"Category: {', '.join(detected_cats) if detected_cats else 'general'} | "
            f"Budget filter: ≤₹{effective_budget:,.2f}"
        ),
    }
    yield f"data: {json.dumps(t_search)}\n\n"
    await asyncio.sleep(0.2)

    t_bud = {"type": "PANEL_B", "section": "BUDGET_CONSTRAINT", "text": f"> Budget Constraint: Max ₹{effective_budget:,.2f}"}
    yield f"data: {json.dumps(t_bud)}\n\n"
    await asyncio.sleep(0.3)

    if not results:
        state_machine.fail("No products found within budget limit")
        audit_ledger.record_event(
            actor="MERCHANT_AGENT",
            state="FAILED",
            title="Catalog Search Returned 0 Matches Under Budget",
            details={"session_id": session_id, "query": user_query, "max_budget_inr": effective_budget},
            session_id=session_id
        )
        t_fail = {"type": "PANEL_B", "section": "BOUNDING_RULE", "text": f"> Bounding Rule: FAILED (No items under ₹{effective_budget:,.2f})"}
        yield f"data: {json.dumps(t_fail)}\n\n"
        err_msg = {"type": "ERROR", "detail": f"Bounding Rule Violation: No catalog items found under budget limit ₹{effective_budget:,.2f}."}
        yield f"data: {json.dumps(err_msg)}\n\n"
        return

    base_product = results[0]

    # Step 3: Dynamic Upsell Calculation
    state_machine.transition_to("RECOMMENDED")
    state_machine.transition_to("UPSELL_OFFERED")
    upsell_offer = upsell_engine.evaluate_best_upsell(
        base_product=base_product,
        max_mandate_budget_inr=mandate_sig.mandate.max_amount_inr
    )

    upsell_accepted = False
    accepted_upsell_product = None

    if upsell_offer:
        is_accepted, decision_reason = buyer.evaluate_upsell_offer(upsell_offer, mandate_sig)
        if is_accepted:
            upsell_accepted = True
            accepted_upsell_product = upsell_offer.product
            state_machine.transition_to("UPSELL_ACCEPTED")

    # Step 4: Finalize Cart
    items = [
        CartItem(
            product_id=base_product.id,
            name=base_product.name,
            category=base_product.category,
            price_inr=base_product.price_inr,
            quantity=1
        )
    ]
    upsell_subtotal = 0.0

    if upsell_accepted and accepted_upsell_product:
        items.append(
            CartItem(
                product_id=accepted_upsell_product.id,
                name=accepted_upsell_product.name,
                category=accepted_upsell_product.category,
                price_inr=accepted_upsell_product.price_inr,
                quantity=1,
                is_upsell=True
            )
        )
        upsell_subtotal = accepted_upsell_product.price_inr

    total_amount = base_product.price_inr + upsell_subtotal
    cart = Cart(
        items=items,
        base_subtotal_inr=base_product.price_inr,
        upsell_subtotal_inr=upsell_subtotal,
        total_amount_inr=total_amount
    )

    state_machine.transition_to("CART_FINALIZED")

    t_off = {"type": "PANEL_B", "section": "DYNAMIC_OFFER", "text": f"> Dynamic Offer Generated: ₹{total_amount:,.2f}"}
    yield f"data: {json.dumps(t_off)}\n\n"
    await asyncio.sleep(0.3)

    # Panel A Chat Line 2: Merchant Offer Pitch
    if upsell_accepted and accepted_upsell_product:
        offer_pitch = f'"I have the {base_product.name} + {accepted_upsell_product.name} bundle for ₹{total_amount:,.2f}. Would you like to authorize payment?"'
    else:
        offer_pitch = f'"I have {base_product.name} for ₹{total_amount:,.2f}. Would you like to authorize payment?"'

    msg2 = {"type": "PANEL_A", "role": "MERCHANT", "speaker": "Merchant Agent", "text": offer_pitch}
    yield f"data: {json.dumps(msg2)}\n\n"
    await asyncio.sleep(0.4)

    # Counter Offer Simulation Option
    if simulate_counter_offer:
        c_msg1 = {"type": "PANEL_A", "role": "BUYER", "speaker": "Buyer Agent", "text": f'"Can we optimize the bundle to fit strictly under ₹{base_product.price_inr:,.2f}?"'}
        yield f"data: {json.dumps(c_msg1)}\n\n"
        await asyncio.sleep(0.3)

        total_amount = base_product.price_inr
        cart.items = [items[0]]
        cart.upsell_subtotal_inr = 0.0
        cart.total_amount_inr = total_amount

        c_msg2 = {"type": "PANEL_A", "role": "MERCHANT", "speaker": "Merchant Agent", "text": f'"Understood. Adjusted order to standalone {base_product.name} for ₹{total_amount:,.2f}."'}
        yield f"data: {json.dumps(c_msg2)}\n\n"
        await asyncio.sleep(0.3)

    # Step 5: AP2 Bounded Mandate Verification
    val_res = AP2MandateEngine.validate_mandate(
        mandate_sig=mandate_sig,
        merchant_id="merchant_techverse_01",
        cart=cart
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
                "valid": False
            },
            session_id=session_id
        )
        t_bound_fail = {"type": "PANEL_B", "section": "BOUNDING_RULE", "text": f"> Bounding Rule: FAILED ({val_res.reason})"}
        yield f"data: {json.dumps(t_bound_fail)}\n\n"
        err_bound = {"type": "ERROR", "detail": f"AP2 Mandate Bounding Rule Failure: {val_res.reason}"}
        yield f"data: {json.dumps(err_bound)}\n\n"
        return

    state_machine.transition_to("MANDATE_VERIFIED")
    t_bound_pass = {"type": "PANEL_B", "section": "BOUNDING_RULE", "text": f"> Bounding Rule: Passed (Within Limit ₹{effective_budget:,.2f})"}
    yield f"data: {json.dumps(t_bound_pass)}\n\n"
    await asyncio.sleep(0.3)

    # Panel A Chat Line 3: Buyer Authorization
    msg3 = {"type": "PANEL_A", "role": "BUYER", "speaker": "Buyer Agent", "text": '"Authorized. Payment token dispatched."'}
    yield f"data: {json.dumps(msg3)}\n\n"
    await asyncio.sleep(0.4)

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
            yield f"data: {json.dumps({'type': 'ERROR', 'detail': reason})}\n\n"
            return

    # Step 6: Create Razorpay Order & Payment Verification
    if simulate_razorpay_error:
        state_machine.fail("Razorpay Gateway Connection Error (Simulated Failure)")
        audit_ledger.record_event(
            actor="RAZORPAY_API",
            state="FAILED",
            title="Razorpay API Gateway Exception",
            details={"session_id": session_id, "error": "Gateway Timeout 504"},
            session_id=session_id
        )
        t_gw_err = {"type": "PANEL_B", "section": "GATEWAY_DISPATCH", "text": "[GATEWAY DISPATCH ERROR: Razorpay Gateway Timeout 504]"}
        yield f"data: {json.dumps(t_gw_err)}\n\n"
        err_gw = {"type": "ERROR", "detail": "Razorpay Gateway Error: 504 Gateway Timeout."}
        yield f"data: {json.dumps(err_gw)}\n\n"
        return

    try:
        order_res = razorpay_service.create_order(cart=cart, buyer_id=buyer.agent_id)
    except RuntimeError as payment_error:
        state_machine.fail(str(payment_error))
        audit_ledger.record_event(
            actor="RAZORPAY_API",
            state="FAILED",
            title="Razorpay Order Creation Failed",
            details={"session_id": session_id, "error": str(payment_error)},
            session_id=session_id,
        )
        yield f"data: {json.dumps({'type': 'ERROR', 'detail': str(payment_error)})}\n\n"
        return
    state_machine.transition_to("PAYMENT_CREATED")

    t_gw = {"type": "PANEL_B", "section": "GATEWAY_DISPATCH", "text": "[GATEWAY DISPATCH — Autonomous Payment]"}
    yield f"data: {json.dumps(t_gw)}\n\n"
    await asyncio.sleep(0.2)

    t_rzp = {"type": "PANEL_B", "section": "RAZORPAY_ORDER", "text": f"> Razorpay Order Created: {order_res.order_id}"}
    yield f"data: {json.dumps(t_rzp)}\n\n"
    await asyncio.sleep(0.2)

    t_tok = {"type": "PANEL_B", "section": "TOKEN_VERIFICATION", "text": "> AP2 Mandate Signature Valid — Dispatching Payment"}
    yield f"data: {json.dumps(t_tok)}\n\n"
    await asyncio.sleep(0.2)

    # Execute real payment via Razorpay API (success@razorpay UPI in test mode)
    # or simulate locally if keys not configured
    if razorpay_service.client:
        checkout = razorpay_service.register_checkout(order_res, cart, session_id)
        audit_ledger.record_event(
            actor="RAZORPAY_API", state="PAYMENT_PENDING", title="Razorpay Standard Checkout Opened",
            details={"session_id": session_id, "order_id": order_res.order_id,
                     "total_amount_inr": cart.total_amount_inr}, session_id=session_id,
        )
        yield f"data: {json.dumps({'type': 'CHECKOUT_REQUIRED', 'order': checkout})}\n\n"
        return

    try:
        payment_id, signature = razorpay_service.execute_payment(order_res.order_id, order_res.amount_paise)
    except RuntimeError as payment_error:
        state_machine.fail(str(payment_error))
        audit_ledger.record_event(
            actor="RAZORPAY_API",
            state="FAILED",
            title="Razorpay Payment Failed",
            details={"session_id": session_id, "order_id": order_res.order_id, "error": str(payment_error)},
            session_id=session_id,
        )
        yield f"data: {json.dumps({'type': 'ERROR', 'detail': str(payment_error)})}\n\n"
        return

    verification = PaymentVerification(
        razorpay_order_id=order_res.order_id,
        razorpay_payment_id=payment_id,
        razorpay_signature=signature
    )

    is_sig_valid = razorpay_service.verify_payment_signature(verification)
    if not is_sig_valid:
        state_machine.fail("Payment HMAC signature verification failed")
        err_sig = {"type": "ERROR", "detail": "Razorpay Payment HMAC Verification Failed."}
        yield f"data: {json.dumps(err_sig)}\n\n"
        return

    state_machine.transition_to("PAYMENT_SUCCESS")
    state_machine.transition_to("ORDER_CONFIRMED")

    order_num = f"#ORD-{uuid.uuid4().hex[:4].upper()}"

    # ── Auto stock deduction — runs immediately after payment captured ────
    for item in cart.items:
        try:
            stock_info = catalog.deduct_stock(item.product_id, item.quantity)
            audit_ledger.record_event(
                actor="MERCHANT_AGENT",
                state="STOCK_DEDUCTED",
                title=f"Stock Deducted: {item.name}",
                details={
                    "session_id": session_id,
                    "order_id": order_res.order_id,
                    "product_id": item.product_id,
                    "quantity_deducted": item.quantity,
                    "stock_before": stock_info["stock_before"],
                    "stock_after": stock_info["stock_after"],
                    "still_in_stock": stock_info["still_in_stock"],
                    "is_upsell_item": item.is_upsell,
                },
                session_id=session_id,
            )
            t_stock = {
                "type": "PANEL_B",
                "section": "STOCK_DEDUCTED",
                "text": (
                    f"> Stock Update: {item.name} | "
                    f"qty={item.quantity} | "
                    f"stock {stock_info['stock_before']} → {stock_info['stock_after']}"
                ),
            }
            yield f"data: {json.dumps(t_stock)}\n\n"
            await asyncio.sleep(0.1)
        except ValueError as se:
            audit_ledger.record_event(
                actor="MERCHANT_AGENT",
                state="STOCK_WARNING",
                title=f"Stock Warning: {item.name}",
                details={"session_id": session_id, "error": str(se)},
                session_id=session_id,
            )

    # Panel A Chat Line 4: Merchant Receipt Confirmation
    msg4 = {"type": "PANEL_A", "role": "MERCHANT", "speaker": "Merchant Agent", "text": f'"Payment of ₹{total_amount:,.2f} captured. Receipt sent. Order ID: {order_num}."'}
    yield f"data: {json.dumps(msg4)}\n\n"
    await asyncio.sleep(0.3)

    t_stat = {"type": "PANEL_B", "section": "TRANSACTION_STATUS", "text": "> Transaction Status: SUCCESS (Captured)"}
    yield f"data: {json.dumps(t_stat)}\n\n"
    await asyncio.sleep(0.2)

    # Step 7: Record Audit Ledger in Database
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
            "matched_count": match_count
        },
        session_id=session_id
    )

    audit_hex_id = audit_rec_final.details.get("audit_record_id", "0x8F4A1C9")

    t_db = {"type": "PANEL_B", "section": "DB_LOGGER", "text": "> State Logged to DynamoDB Ledger"}
    yield f"data: {json.dumps(t_db)}\n\n"
    await asyncio.sleep(0.2)

    t_rec = {"type": "PANEL_B", "section": "AUDIT_RECORD", "text": f"[AUDIT RECORD ID: {audit_hex_id}]"}
    yield f"data: {json.dumps(t_rec)}\n\n"
    await asyncio.sleep(0.2)

    # Final Complete Payload
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
        timestamp=order_res.created_at.__str__(),
        audit_hash_chain=audit_rec_final.current_hash
    )

    complete_payload = {
        "type": "COMPLETE",
        "payload": {
            "status": "SUCCESS",
            "session_id": session_id,
            "audit_record_id": audit_hex_id,
            "receipt": receipt.model_dump(),
            "cart": cart.model_dump()
        }
    }
    yield f"data: {json.dumps(complete_payload)}\n\n"
