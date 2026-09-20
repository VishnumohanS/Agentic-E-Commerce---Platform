"""
Unit tests for Dynamic AI Buyer Agent intent parsing and prompt evaluation.
"""

import pytest
from app.buyer.buyer_agent import AIBuyerAgent
from app.merchant.catalog import MerchantCatalog
from app.merchant.upsell_engine import MerchantUpsellEngine

def test_extract_intent_and_budget_from_prompt():
    buyer = AIBuyerAgent()

    intent, budget, cat = buyer.extract_intent_and_budget("Looking for a 65W GaN fast charger with USB-C cable under ₹2,500.")
    assert budget == 2500.0
    assert cat == "charging"
    assert "Charging" in intent

    intent2, budget2, cat2 = buyer.extract_intent_and_budget("Need an ergonomic wireless mouse under ₹3,000.")
    assert budget2 == 3000.0
    assert cat2 == "peripherals"

def test_dynamic_upsell_evaluation_accepts_valid_headroom():
    buyer = AIBuyerAgent()
    catalog = MerchantCatalog()
    upsell_engine = MerchantUpsellEngine(catalog)

    mandate_sig = buyer.issue_bounded_mandate(max_budget_inr=2500.0)
    base = catalog.get_product_by_id("prod_charger_65w_gan")
    offer = upsell_engine.evaluate_best_upsell(base, max_mandate_budget_inr=2500.0)

    accepted, reason = buyer.evaluate_upsell_offer(offer, mandate_sig)
    assert accepted is True
    assert "ACCEPTED" in reason
