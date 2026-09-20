"""Adapter for an authorised Amazon product MCP server.

This module deliberately performs MCP JSON-RPC calls only; it does not scrape
Amazon pages. Product text is treated as data and deterministic code applies
availability and budget constraints after normalization.
"""

from typing import Any, Dict, List
import uuid
import requests

from app.config import settings
from app.models import Product, ProductQuery


class AmazonMCPError(RuntimeError):
    pass


class AmazonMCPProvider:
    def search(self, query: ProductQuery) -> List[Product]:
        if not settings.AMAZON_MCP_ENABLED:
            return []
        if not settings.AMAZON_MCP_URL:
            raise AmazonMCPError("Amazon MCP is enabled but AMAZON_MCP_URL is not configured.")
        headers = {"Content-Type": "application/json"}
        if settings.AMAZON_MCP_API_KEY:
            headers["Authorization"] = f"Bearer {settings.AMAZON_MCP_API_KEY}"
        payload = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "tools/call",
                   "params": {"name": "search_amazon", "arguments": {
                       "query": query.query_text, "max_budget_inr": query.max_budget_inr, "currency": "INR"}}}
        try:
            response = requests.post(settings.AMAZON_MCP_URL, json=payload, headers=headers, timeout=15)
            response.raise_for_status()
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise AmazonMCPError("Amazon MCP search is unavailable.") from exc
        if body.get("error"):
            raise AmazonMCPError(f"Amazon MCP search failed: {body['error'].get('message', 'unknown error')}")
        result = body.get("result", {})
        raw = result.get("products") or result.get("items") or result.get("structuredContent", {}).get("products")
        if raw is None:
            raise AmazonMCPError("Amazon MCP returned no structured product list.")
        products = [self._normalize(item) for item in raw if isinstance(item, dict)]
        return [p for p in products if p.in_stock and p.stock_quantity > 0 and p.price_inr <= query.max_budget_inr]

    @staticmethod
    def _normalize(item: Dict[str, Any]) -> Product:
        price = item.get("price", item.get("price_inr"))
        if isinstance(price, dict):
            price = price.get("amount") or price.get("value")
        try:
            price = float(price)
        except (TypeError, ValueError) as exc:
            raise AmazonMCPError("Amazon MCP returned a product without a valid INR price.") from exc
        available = item.get("availability", item.get("in_stock", True))
        if isinstance(available, str):
            available = available.lower() in {"in_stock", "available", "true"}
        return Product(id=str(item.get("product_id") or item.get("id") or uuid.uuid4().hex),
            name=str(item.get("title") or item.get("name") or "Untitled product"),
            category=str(item.get("category") or "electronics"), price_inr=price,
            description=str(item.get("description") or ""), tags=list(item.get("tags") or []),
            in_stock=bool(available), stock_quantity=1 if available else 0,
            specifications=dict(item.get("specifications") or {}), source="amazon_mcp",
            product_url=item.get("product_url") or item.get("url"), image=item.get("image"), rating=item.get("rating"))
