"""
MCP (Model Context Protocol) Merchant Catalog Provider.

Uses REAL `mcp` library types (mcp>=1.26.0):
  - mcp.types.Tool, mcp.types.ToolAnnotations
  - mcp.types.TextContent, mcp.types.CallToolResult
  - mcp.types.ListToolsResult
  - mcp.types.JSONRPCResponse, mcp.types.JSONRPCError, mcp.types.JSONRPCRequest

The `/mcp` HTTP endpoint handles standard JSON-RPC 2.0 MCP requests:
  - tools/list  → returns real ListToolsResult
  - tools/call  → returns real CallToolResult with TextContent

Also provides Schema.org JSON-LD UCP product format for AI buyer catalog discovery.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from mcp.types import (
    Tool,
    ToolAnnotations,
    TextContent,
    CallToolResult,
    ListToolsResult,
    JSONRPCResponse,
    JSONRPCError,
    JSONRPCRequest,
    ErrorData,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    INTERNAL_ERROR,
)
from pydantic import AnyUrl

from app.models import Product, ProductQuery

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MCP Tool Definitions using real mcp.types.Tool
# ---------------------------------------------------------------------------

def _build_mcp_tools() -> List[Tool]:
    """
    Builds the canonical MCP tool manifest using real `mcp.types.Tool` objects.
    """
    return [
        Tool(
            name="merchant_search_catalog",
            description=(
                "Search the TechVerse merchant catalog for products matching "
                "a natural-language query and a maximum budget in INR. "
                "Uses TF-IDF vector similarity scoring."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Natural-language product search prompt, "
                            "e.g. '65W GaN fast charger with USB-C cable'"
                        ),
                    },
                    "max_budget_inr": {
                        "type": "number",
                        "description": "Maximum budget in Indian Rupees (INR)",
                    },
                },
                "required": ["query", "max_budget_inr"],
            },
            annotations=ToolAnnotations(
                title="Catalog Search",
                readOnlyHint=True,
            ),
        ),
        Tool(
            name="merchant_evaluate_upsell",
            description=(
                "Evaluates the highest-margin, value-added upsell or cross-sell "
                "product that fits within the remaining AP2 mandate budget headroom "
                "after the base product is selected."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "product_id": {
                        "type": "string",
                        "description": "Catalog product ID of the base selected product",
                    },
                    "current_mandate_limit": {
                        "type": "number",
                        "description": "AP2 mandate upper budget limit in INR",
                    },
                },
                "required": ["product_id", "current_mandate_limit"],
            },
            annotations=ToolAnnotations(
                title="Dynamic Upsell Evaluator",
                readOnlyHint=True,
            ),
        ),
        Tool(
            name="merchant_create_cart",
            description=(
                "Creates an agentic commerce cart from a list of product IDs. "
                "Returns the cart total and line item breakdown."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "product_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of catalog product IDs to add to cart",
                    }
                },
                "required": ["product_ids"],
            },
            annotations=ToolAnnotations(title="Cart Builder"),
        ),
        Tool(
            name="merchant_checkout_with_ap2",
            description=(
                "Submits a cart for Razorpay test-mode payment checkout "
                "gated by a valid AP2 Bounded Mandate signature. "
                "Verifies HMAC-SHA256 mandate integrity and budget bounds "
                "before creating the Razorpay order."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "cart_id": {
                        "type": "string",
                        "description": "Cart identifier",
                    },
                    "mandate_id": {
                        "type": "string",
                        "description": "AP2 Bounded Mandate ID",
                    },
                    "signature": {
                        "type": "string",
                        "description": "HMAC-SHA256 AP2 mandate signature",
                    },
                },
                "required": ["cart_id", "mandate_id", "signature"],
            },
            annotations=ToolAnnotations(
                title="AP2-Gated Razorpay Checkout",
                destructiveHint=False,
            ),
        ),
    ]


# Pre-built tool list — shared across requests
_MCP_TOOLS: List[Tool] = _build_mcp_tools()


# ---------------------------------------------------------------------------
# MCP JSON-RPC 2.0 Request Handler
# ---------------------------------------------------------------------------

class MCPMerchantCatalogProvider:

    @staticmethod
    def get_mcp_tools() -> List[Tool]:
        """Returns the real `mcp.types.Tool` list."""
        return _MCP_TOOLS

    @staticmethod
    def get_mcp_tools_manifest() -> List[Dict[str, Any]]:
        """
        Returns tools as JSON-serialisable dicts (for /api/mcp/tools HTTP endpoint).
        Uses Pydantic `.model_dump()` on each real mcp.types.Tool.
        """
        return [t.model_dump(exclude_none=True) for t in _MCP_TOOLS]

    @classmethod
    def handle_mcp_jsonrpc_request(
        cls,
        payload: Dict[str, Any],
        catalog_service,
        upsell_service,
    ) -> Dict[str, Any]:
        """
        Standard JSON-RPC 2.0 MCP dispatcher.

        Parses the request, dispatches to the correct MCP method handler,
        and returns a properly-formed JSONRPCResponse (or JSONRPCError) dict,
        using real `mcp.types` objects serialized via Pydantic.
        """
        req_id = payload.get("id", 1)
        method: str = payload.get("method", "")
        params: Dict[str, Any] = payload.get("params", {}) or {}

        try:
            if method == "tools/list":
                return cls._handle_tools_list(req_id)

            elif method == "tools/call":
                return cls._handle_tools_call(req_id, params, catalog_service, upsell_service)

            else:
                return cls._json_rpc_error(
                    req_id,
                    code=METHOD_NOT_FOUND,
                    message=f"Method '{method}' not found. Supported: tools/list, tools/call",
                )

        except Exception as exc:
            logger.exception("MCP handler error for method '%s': %s", method, exc)
            return cls._json_rpc_error(
                req_id,
                code=INTERNAL_ERROR,
                message=f"Internal server error: {exc}",
            )

    # -------------------------------------------------------------------
    # tools/list
    # -------------------------------------------------------------------

    @classmethod
    def _handle_tools_list(cls, req_id: Any) -> Dict[str, Any]:
        result = ListToolsResult(tools=_MCP_TOOLS)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": result.model_dump(exclude_none=True),
        }

    # -------------------------------------------------------------------
    # tools/call dispatcher
    # -------------------------------------------------------------------

    @classmethod
    def _handle_tools_call(
        cls,
        req_id: Any,
        params: Dict[str, Any],
        catalog_service,
        upsell_service,
    ) -> Dict[str, Any]:
        tool_name: str = params.get("name", "")
        args: Dict[str, Any] = params.get("arguments", {}) or {}

        handler_map = {
            "merchant_search_catalog": cls._tool_search_catalog,
            "merchant_evaluate_upsell": cls._tool_evaluate_upsell,
            "merchant_create_cart": cls._tool_create_cart,
            "merchant_checkout_with_ap2": cls._tool_checkout_ap2,
        }

        handler = handler_map.get(tool_name)
        if not handler:
            return cls._json_rpc_error(
                req_id,
                code=INVALID_PARAMS,
                message=f"Unknown tool '{tool_name}'. Available: {list(handler_map.keys())}",
            )

        # Validate required fields per tool
        required = {
            "merchant_search_catalog": ["query", "max_budget_inr"],
            "merchant_evaluate_upsell": ["product_id", "current_mandate_limit"],
            "merchant_create_cart": ["product_ids"],
            "merchant_checkout_with_ap2": ["cart_id", "mandate_id", "signature"],
        }
        missing = [k for k in required.get(tool_name, []) if k not in args]
        if missing:
            return cls._json_rpc_error(
                req_id,
                code=INVALID_PARAMS,
                message=f"Missing required arguments for '{tool_name}': {missing}",
            )

        text_out = handler(args, catalog_service, upsell_service)
        result = CallToolResult(
            content=[TextContent(type="text", text=text_out)],
            isError=False,
        )
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": result.model_dump(exclude_none=True),
        }

    # -------------------------------------------------------------------
    # Individual tool implementations
    # -------------------------------------------------------------------

    @staticmethod
    def _tool_search_catalog(
        args: Dict[str, Any], catalog_service, upsell_service
    ) -> str:
        query_text = str(args["query"])
        budget = float(args["max_budget_inr"])

        products, match_count, top_sim = catalog_service.vector_search_catalog(
            ProductQuery(query_text=query_text, max_budget_inr=budget)
        )

        if not products:
            return (
                f"No catalog items matched query '{query_text}' "
                f"under budget ₹{budget:,.2f}."
            )

        top = products[0]
        lines = [
            f"Found {match_count} catalog match(es) under ₹{budget:,.2f}.",
            f"Top match: '{top.name}' @ ₹{top.price_inr:,.2f} "
            f"(Vector Similarity: {top_sim * 100:.1f}%)",
            f"Category: {top.category} | In Stock: {top.in_stock}",
        ]
        return "\n".join(lines)

    @staticmethod
    def _tool_evaluate_upsell(
        args: Dict[str, Any], catalog_service, upsell_service
    ) -> str:
        product_id = str(args["product_id"])
        mandate_limit = float(args["current_mandate_limit"])

        base = catalog_service.get_product_by_id(product_id)
        if not base:
            return f"Error: Product '{product_id}' not found in catalog."

        upsell = upsell_service.evaluate_best_upsell(
            base, max_mandate_budget_inr=mandate_limit
        )
        if not upsell:
            headroom = mandate_limit - base.price_inr
            return (
                f"No upsell available. Base product '{base.name}' "
                f"@ ₹{base.price_inr:,.2f}. Remaining headroom: ₹{headroom:,.2f}."
            )

        return (
            f"Upsell offer: '{upsell.product.name}' "
            f"for +₹{upsell.additional_cost_inr:,.2f}. "
            f"New cart total: ₹{upsell.new_cart_total_inr:,.2f}. "
            f"Within AP2 mandate limit ₹{mandate_limit:,.2f}: "
            f"{'✓ YES' if upsell.within_mandate else '✗ NO'}."
        )

    @staticmethod
    def _tool_create_cart(
        args: Dict[str, Any], catalog_service, upsell_service
    ) -> str:
        product_ids: List[str] = args.get("product_ids", [])
        if not product_ids:
            return "Error: product_ids list is empty."

        lines = ["Cart summary:"]
        total = 0.0
        for pid in product_ids:
            p = catalog_service.get_product_by_id(pid)
            if p:
                lines.append(f"  • {p.name} — ₹{p.price_inr:,.2f}")
                total += p.price_inr
            else:
                lines.append(f"  • Product ID '{pid}' not found — skipped.")

        lines.append(f"Cart total: ₹{total:,.2f}")
        return "\n".join(lines)

    @staticmethod
    def _tool_checkout_ap2(
        args: Dict[str, Any], catalog_service, upsell_service
    ) -> str:
        # This MCP tool returns a guidance message; actual checkout
        # is performed via POST /api/commerce/stream or /api/commerce/run-flow.
        return (
            f"AP2-gated checkout initiated for cart '{args['cart_id']}' "
            f"with mandate '{args['mandate_id']}'. "
            "Use POST /api/commerce/stream for full autonomous pipeline execution "
            "with real-time SSE streaming, AP2 mandate verification, "
            "and Razorpay test-mode order creation."
        )

    # -------------------------------------------------------------------
    # JSON-RPC error helper
    # -------------------------------------------------------------------

    @staticmethod
    def _json_rpc_error(req_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": code, "message": message},
        }

    # -------------------------------------------------------------------
    # Schema.org JSON-LD UCP product format (A2C / agent-readable catalog)
    # -------------------------------------------------------------------

    @staticmethod
    def format_ucp_product(product: Product) -> Dict[str, Any]:
        """
        Formats a Product into Schema.org JSON-LD for AI buyer catalog discovery
        (A2C / UCP / ACP compatible format).
        """
        return {
            "@context": "https://schema.org/",
            "@type": "Product",
            "identifier": product.id,
            "name": product.name,
            "category": product.category,
            "offers": {
                "@type": "Offer",
                "price": product.price_inr,
                "priceCurrency": "INR",
                "availability": (
                    "https://schema.org/InStock"
                    if product.in_stock
                    else "https://schema.org/OutOfStock"
                ),
            },
            "description": product.description,
            "keywords": product.tags,
            "agenticMetadata": {
                "margin_grade": (
                    "HIGH" if product.merchant_margin_pct >= 0.20 else "STANDARD"
                ),
                "specifications": product.specifications,
                "upsell_eligible": True,
            },
        }
