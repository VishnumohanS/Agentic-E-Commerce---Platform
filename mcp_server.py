"""
Authentic Standalone Model Context Protocol (MCP) Server using official `mcp.server.fastmcp.FastMCP`.
Exposes native tools for searching merchant catalog, evaluating dynamic upsells,
building cart items, and executing AP2-mandated Razorpay test payment checkouts.
"""

from mcp.server.fastmcp import FastMCP
from typing import Dict, Any, List
import json

from app.models import ProductQuery, Cart, CartItem, PaymentVerification
from app.merchant.catalog import MerchantCatalog
from app.merchant.upsell_engine import MerchantUpsellEngine
from app.protocols.ap2_mandate import AP2MandateEngine
from app.services.razorpay_service import RazorpayService
from app.buyer.buyer_agent import AIBuyerAgent

# Initialize FastMCP Server
mcp_server = FastMCP(
    name="TechVerse Merchant Agent MCP Server",
    description="Official MCP JSON-RPC Server providing catalog discovery, dynamic upsells, AP2 mandates, and Razorpay checkout."
)

catalog = MerchantCatalog()
upsell_engine = MerchantUpsellEngine(catalog)
razorpay_service = RazorpayService()

@mcp_server.tool(name="merchant_search_catalog", description="Searches merchant catalog using vector similarity given query prompt and max budget INR.")
def search_catalog_tool(query: str, max_budget_inr: float) -> str:
    products, match_count, top_sim = catalog.vector_search_catalog(ProductQuery(query_text=query, max_budget_inr=max_budget_inr))
    if not products:
        return f"No catalog items matched query '{query}' under budget limit ₹{max_budget_inr:,.2f}."
    
    top_p = products[0]
    return f"Matched {match_count} catalog items. Top Result: '{top_p.name}' @ ₹{top_p.price_inr:,.2f} (Vector Similarity: {top_sim * 100:.1f}%)."

@mcp_server.tool(name="merchant_evaluate_upsell", description="Calculates value-added dynamic margin-maximizing upsell bundle fitting within remaining AP2 budget headroom.")
def evaluate_upsell_tool(product_id: str, max_mandate_budget_inr: float) -> str:
    p = catalog.get_product_by_id(product_id)
    if not p:
        return f"Error: Product '{product_id}' not found."
    
    upsell = upsell_engine.evaluate_best_upsell(p, max_mandate_budget_inr=max_mandate_budget_inr)
    if not upsell:
        return f"No compatible upsell available within remaining headroom ₹{max_mandate_budget_inr - p.price_inr:,.2f}."
    
    return f"Recommended Upsell Offer: '{upsell.product.name}' for +₹{upsell.additional_cost_inr:,.2f}. Projected Cart Total: ₹{upsell.new_cart_total_inr:,.2f}."

@mcp_server.tool(name="merchant_checkout_with_ap2", description="Submits a cart with signed AP2 Bounded Mandate signature to create a Razorpay test order.")
def checkout_tool(product_id: str, upsell_product_id: str = None, max_budget_inr: float = 2500.0) -> str:
    base = catalog.get_product_by_id(product_id)
    if not base:
        return f"Error: Product '{product_id}' not found."
    
    items = [CartItem(product_id=base.id, name=base.name, category=base.category, price_inr=base.price_inr)]
    upsell_subtotal = 0.0

    if upsell_product_id:
        upsell = catalog.get_product_by_id(upsell_product_id)
        if upsell:
            items.append(CartItem(product_id=upsell.id, name=upsell.name, category=upsell.category, price_inr=upsell.price_inr, is_upsell=True))
            upsell_subtotal = upsell.price_inr

    total_amount = base.price_inr + upsell_subtotal
    cart = Cart(items=items, base_subtotal_inr=base.price_inr, upsell_subtotal_inr=upsell_subtotal, total_amount_inr=total_amount)

    buyer = AIBuyerAgent()
    mandate_sig = buyer.issue_bounded_mandate(max_budget_inr=max_budget_inr)

    val_res = AP2MandateEngine.validate_mandate(mandate_sig, "merchant_techverse_01", cart)
    if not val_res.valid:
        return f"AP2 Bounded Mandate Verification FAILED: {val_res.reason}"

    order_res = razorpay_service.create_order(cart, buyer.agent_id)
    if razorpay_service.client:
        return f"PENDING_CHECKOUT: Open Razorpay Standard Checkout with options {json.dumps(razorpay_service.checkout_options(order_res))}. Use success@razorpay for the test UPI flow."
    pay_id, sig = razorpay_service.execute_payment(order_res.order_id, order_res.amount_paise)
    
    return f"SUCCESS: Razorpay Order Created '{order_res.order_id}' | Payment ID '{pay_id}' | Total Settled ₹{cart.total_amount_inr:,.2f}."

if __name__ == "__main__":
    mcp_server.run()
