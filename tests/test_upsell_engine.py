"""
Unit tests for Merchant Upsell & Revenue Maximization Engine.
"""

import pytest
from app.merchant.catalog import MerchantCatalog
from app.merchant.upsell_engine import MerchantUpsellEngine

def test_upsell_within_budget_headroom():
    catalog = MerchantCatalog()
    upsell_engine = MerchantUpsellEngine(catalog)

    base_product = catalog.get_product_by_id("prod_laptop_dev_65k")
    assert base_product is not None

    # Budget 70k gives 5k headroom -> 2-yr warranty is 2,999 INR (fits headroom!)
    offer = upsell_engine.evaluate_best_upsell(base_product, max_mandate_budget_inr=70000.0)
    assert offer is not None
    assert offer.product.id == "prod_warranty_2yr_2999"
    assert offer.new_cart_total_inr == 67999.0
    assert offer.within_mandate is True

def test_upsell_no_headroom():
    catalog = MerchantCatalog()
    upsell_engine = MerchantUpsellEngine(catalog)

    base_product = catalog.get_product_by_id("prod_laptop_dev_65k")

    # Budget exactly equal to base product (65k) -> 0 headroom
    offer = upsell_engine.evaluate_best_upsell(base_product, max_mandate_budget_inr=65000.0)
    assert offer is None
