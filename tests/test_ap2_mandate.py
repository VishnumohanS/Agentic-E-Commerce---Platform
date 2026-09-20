"""
Unit tests for AP2 Bounded Mandate Engine.
Tests signature verification, budget boundary enforcement, and merchant restrictions.
"""

import pytest
from app.models import Cart, CartItem
from app.protocols.ap2_mandate import AP2MandateEngine

def test_valid_mandate():
    mandate_sig = AP2MandateEngine.create_signed_mandate(
        buyer_agent_id="buyer_01",
        user_id="user_01",
        max_amount_inr=70000.0,
        authorized_merchant_id="merchant_techverse_01"
    )

    cart = Cart(
        items=[CartItem(product_id="laptop", name="Laptop", price_inr=65000.0)],
        base_subtotal_inr=65000.0,
        total_amount_inr=65000.0
    )

    res = AP2MandateEngine.validate_mandate(mandate_sig, "merchant_techverse_01", cart)
    assert res.valid is True
    assert res.remaining_headroom_inr == 5000.0

def test_mandate_budget_breach():
    mandate_sig = AP2MandateEngine.create_signed_mandate(
        buyer_agent_id="buyer_01",
        user_id="user_01",
        max_amount_inr=60000.0, # Budget lower than 65k product
        authorized_merchant_id="merchant_techverse_01"
    )

    cart = Cart(
        items=[CartItem(product_id="laptop", name="Laptop", price_inr=65000.0)],
        base_subtotal_inr=65000.0,
        total_amount_inr=65000.0
    )

    res = AP2MandateEngine.validate_mandate(mandate_sig, "merchant_techverse_01", cart)
    assert res.valid is False
    assert "exceeds AP2 Mandate upper boundary limit" in res.reason

def test_tampered_mandate_signature():
    mandate_sig = AP2MandateEngine.create_signed_mandate(
        buyer_agent_id="buyer_01",
        user_id="user_01",
        max_amount_inr=70000.0,
        authorized_merchant_id="merchant_techverse_01"
    )

    # Tamper with mandate signature
    mandate_sig.signature = "invalid_tampered_signature_123"

    cart = Cart(
        items=[CartItem(product_id="laptop", name="Laptop", price_inr=65000.0)],
        total_amount_inr=65000.0
    )

    res = AP2MandateEngine.validate_mandate(mandate_sig, "merchant_techverse_01", cart)
    assert res.valid is False
    assert "signature verification failed" in res.reason

def test_malformed_expiry_is_rejected():
    mandate_sig = AP2MandateEngine.create_signed_mandate(
        buyer_agent_id="buyer_01", user_id="user_01", max_amount_inr=1000.0,
        authorized_merchant_id="merchant_techverse_01"
    )
    mandate_sig.mandate.expires_at = "not-a-timestamp"
    mandate_sig.signature = AP2MandateEngine.compute_mandate_signature(mandate_sig.mandate)
    cart = Cart(items=[CartItem(product_id="p", name="Item", price_inr=10.0)], total_amount_inr=10.0)
    res = AP2MandateEngine.validate_mandate(mandate_sig, "merchant_techverse_01", cart)
    assert res.valid is False
    assert "expiry is invalid" in res.reason

def test_disallowed_category_is_rejected():
    mandate_sig = AP2MandateEngine.create_signed_mandate(
        buyer_agent_id="buyer_01", user_id="user_01", max_amount_inr=1000.0,
        authorized_merchant_id="merchant_techverse_01", allowed_categories=["charging"]
    )
    cart = Cart(
        items=[CartItem(product_id="p", name="Item", category="audio", price_inr=10.0)],
        total_amount_inr=10.0,
    )
    res = AP2MandateEngine.validate_mandate(mandate_sig, "merchant_techverse_01", cart)
    assert res.valid is False
    assert "not allowed" in res.reason
