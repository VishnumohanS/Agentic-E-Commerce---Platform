"""
Revenue Maximizing Upsell & Cross-Sell Engine for Merchant Agent.
Calculates high-margin upsells dynamically within the AP2 mandate budget headroom.
"""

from typing import Optional
from app.models import Product, UpsellOffer, Cart
from app.merchant.catalog import MerchantCatalog

class MerchantUpsellEngine:
    def __init__(self, catalog: MerchantCatalog):
        self.catalog = catalog

    def evaluate_best_upsell(
        self,
        base_product: Product,
        max_mandate_budget_inr: float
    ) -> Optional[UpsellOffer]:
        """
        Calculates the highest margin, value-added upsell or cross-sell item
        that fits within (max_mandate_budget_inr - base_product.price_inr).
        """
        headroom = max_mandate_budget_inr - base_product.price_inr
        if headroom <= 0:
            return None

        # Check for explicitly preferred upsell ID on base product
        pref_id = base_product.specifications.get("preferred_upsell_id")
        if pref_id:
            pref_product = self.catalog.get_product_by_id(pref_id)
            if pref_product and pref_product.price_inr <= headroom:
                new_total = base_product.price_inr + pref_product.price_inr
                pitch = (
                    f"Would you like to add '{pref_product.name}' for ₹{pref_product.price_inr:,.2f}? "
                    f"This protects your ₹{base_product.price_inr:,.2f} programming setup with zero-cost repairs. "
                    f"Total order price will be ₹{new_total:,.2f}, which comfortably respects your AP2 Mandate limit of ₹{max_mandate_budget_inr:,.2f}."
                )
                return UpsellOffer(
                    product=pref_product,
                    pitch=pitch,
                    additional_cost_inr=pref_product.price_inr,
                    new_cart_total_inr=new_total,
                    within_mandate=True
                )

        # Fallback to general margin maximization
        all_products = self.catalog.get_all_products()
        candidates = []

        for p in all_products:
            if p.id == base_product.id:
                continue

            if p.price_inr <= headroom:
                candidates.append(p)

        if not candidates:
            return None

        candidates.sort(key=lambda x: (x.price_inr * x.merchant_margin_pct), reverse=True)
        best_upsell = candidates[0]
        new_total = base_product.price_inr + best_upsell.price_inr

        pitch = (
            f"Would you like to add '{best_upsell.name}' for ₹{best_upsell.price_inr:,.2f}? "
            f"Total order price will be ₹{new_total:,.2f}, respecting your AP2 Mandate limit of ₹{max_mandate_budget_inr:,.2f}."
        )

        return UpsellOffer(
            product=best_upsell,
            pitch=pitch,
            additional_cost_inr=best_upsell.price_inr,
            new_cart_total_inr=new_total,
            within_mandate=True
        )
