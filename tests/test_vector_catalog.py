"""
Unit tests for Vector Similarity Search across merchant catalog.
"""

import pytest
from app.models import ProductQuery
from app.merchant.catalog import MerchantCatalog

def test_vector_search_charger_query():
    catalog = MerchantCatalog()
    q = ProductQuery(query_text="65W GaN charger with USB-C cable", max_budget_inr=2500.0)

    matched, count, sim_score = catalog.vector_search_catalog(q)
    assert count >= 1
    assert matched[0].id == "prod_charger_65w_gan"
    assert sim_score > 0.10

def test_vector_search_mouse_query():
    catalog = MerchantCatalog()
    q = ProductQuery(query_text="ergonomic wireless mouse", max_budget_inr=3000.0)

    matched, count, sim_score = catalog.vector_search_catalog(q)
    assert count >= 1
    assert matched[0].id == "prod_mouse_ergo_3k"
