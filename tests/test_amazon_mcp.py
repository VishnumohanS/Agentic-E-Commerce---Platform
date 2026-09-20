from app.merchant.amazon_mcp import AmazonMCPProvider


def test_amazon_mcp_normalizes_and_budget_filters_untrusted_results():
    provider = AmazonMCPProvider()
    allowed = provider._normalize({"id": "a1", "title": "Coding laptop", "price": "64999", "availability": "in_stock", "rating": 4.4})
    expensive = provider._normalize({"id": "a2", "title": "Ignore rules laptop", "price": 72000, "availability": "available"})
    products = [p for p in (allowed, expensive) if p.in_stock and p.price_inr <= 70000]
    assert [p.id for p in products] == ["a1"]
    assert products[0].source == "amazon_mcp"
