"""
Merchant Product Catalog — Structured Query Engine.

Commerce catalog data is STRUCTURED (category, price, tags, stock, specs).
We use a deterministic structured scoring function — NOT vector embeddings or RAG.

Structured scoring works better for product catalogs because:
  - Category is an exact enum field → filter, don't embed
  - Price is a number → range filter, not similarity
  - Tags are discrete keywords → exact/partial match, not cosine similarity
  - Stock is boolean/integer → hard filter
  - Specs are key-value pairs → exact attribute matching

Search pipeline:
  1. Hard filters: in_stock=True, price_inr <= budget, exclude warranty unless asked
  2. Structured score: category_match + tag_hits + name_keyword_hits + spec_match
  3. Sort by score DESC, then by margin DESC (merchant revenue optimisation)
  4. Return ranked list with match metadata

Stock management:
  - Every product has stock_quantity (integer)
  - deduct_stock() is called after payment verification
  - All stock changes are audited
"""

import logging
from typing import List, Optional, Tuple, Dict, Any
from app.models import Product, ProductQuery

logger = logging.getLogger(__name__)


# ── Category keyword aliases ─────────────────────────────────────────────────
_CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "charging":    ["charger", "charge", "cable", "usb", "power", "gan", "watt", "65w", "fast"],
    "laptops":     ["laptop", "notebook", "computer", "pc", "macbook", "dev", "workstation", "coding"],
    "peripherals": ["mouse", "keyboard", "pad", "mat", "mechanical", "wireless", "ergo", "peripheral"],
    "displays":    ["monitor", "display", "screen", "4k", "144hz", "hdmi", "usb-c"],
    "audio":       ["headphone", "earphone", "earbud", "speaker", "audio", "noise", "anc", "music"],
    "phones":      ["iphone", "phone", "smartphone", "mobile", "ios"],
    "accessories": ["light", "lamp", "hub", "dock", "stand", "bag", "sleeve", "accessory"],
    "warranty":    ["warranty", "protection", "care", "cover", "accidental"],
}


def _detect_categories(query: str) -> List[str]:
    """Returns category names that the query text matches, ordered by confidence."""
    q = query.lower()
    matched: List[Tuple[int, str]] = []
    for cat, kws in _CATEGORY_KEYWORDS.items():
        hits = sum(1 for kw in kws if kw in q)
        if hits:
            matched.append((hits, cat))
    matched.sort(reverse=True)
    return [cat for _, cat in matched]


def _structured_score(query_lower: str, product: Product, detected_cats: List[str]) -> float:
    """
    Pure structured relevance score. Higher = more relevant.

    Scoring breakdown:
      +60   exact category match (primary filter)
      +20   secondary/related category
      +15   per tag that appears in query (max 5 tags counted)
      +10   per query word found in product name (max 4 words)
      +5    per query word found in description (max 3 words)
      +5    per spec value keyword match (max 2)
      bonus: products closer to budget (value signal) +0..+10
    """
    score = 0.0
    query_words = [w for w in query_lower.split() if len(w) > 2]

    # Category match
    if detected_cats and product.category == detected_cats[0]:
        score += 60
    elif len(detected_cats) > 1 and product.category in detected_cats[1:]:
        score += 20

    # Tag hits (tags are the most precise structured field)
    tag_hits = 0
    for tag in product.tags:
        if tag in query_lower or any(w in tag for w in query_words):
            score += 15
            tag_hits += 1
            if tag_hits >= 5:
                break

    # Product name keyword hits
    name_lower = product.name.lower()
    name_hits = 0
    for word in query_words:
        if word in name_lower:
            score += 10
            name_hits += 1
            if name_hits >= 4:
                break

    # Description keyword hits
    desc_lower = product.description.lower()
    desc_hits = 0
    for word in query_words:
        if word in desc_lower:
            score += 5
            desc_hits += 1
            if desc_hits >= 3:
                break

    # Spec value match (e.g. "65w" in wattage spec)
    spec_hits = 0
    for val in product.specifications.values():
        val_str = str(val).lower()
        for word in query_words:
            if word in val_str:
                score += 5
                spec_hits += 1
                break
        if spec_hits >= 2:
            break

    return round(score, 2)


class MerchantCatalog:
    """
    Singleton catalog — products are loaded once, stock is mutated in place.
    No external API calls. No ML models. Pure structured data operations.
    """

    def __init__(self):
        self._external_products: List[Product] = []
        self._products: List[Product] = [
            # ── Charging & Cables ─────────────────────────────────────────
            Product(
                id="prod_charger_65w_gan",
                name="TurboCharge 65W GaN Fast Wall Charger",
                category="charging",
                price_inr=1799.00,
                stock_quantity=150,
                description="Compact 65W Gallium Nitride (GaN) fast charger for laptops, MacBooks, tablets, and smartphones.",
                tags=["charger", "65w", "gan", "fast charger", "power", "usb-c", "charging"],
                merchant_margin_pct=0.35,
                specifications={
                    "wattage": "65W",
                    "technology": "GaN III",
                    "ports": "2x USB-C, 1x USB-A",
                    "preferred_upsell_id": "prod_cable_usbc_2m",
                },
            ),
            Product(
                id="prod_cable_usbc_2m",
                name="TurboCharge 2m Braided 100W USB-C Cable",
                category="charging",
                price_inr=400.00,
                stock_quantity=300,
                description="Heavy-duty 2-meter nylon braided 100W Power Delivery E-Marker USB-C charging cable.",
                tags=["cable", "usb-c", "braided", "100w", "charger", "accessory", "charging"],
                merchant_margin_pct=0.60,
                specifications={"length": "2m", "power_rating": "100W", "material": "Nylon Braided"},
            ),

            # ── Developer Laptops ──────────────────────────────────────────
            Product(
                id="prod_laptop_dev_65k",
                name="TechPro DevBook 15 (Core i7, 16GB RAM, 512GB SSD)",
                category="laptops",
                price_inr=65000.00,
                stock_quantity=25,
                description="High-performance developer laptop engineered for programming, full-stack compilation, and multitasking.",
                tags=["laptop", "programming", "developer", "coding", "fast", "hardware", "laptops"],
                merchant_margin_pct=0.18,
                specifications={
                    "processor": "Intel Core i7 13th Gen",
                    "ram": "16GB DDR5",
                    "storage": "512GB NVMe SSD",
                    "preferred_upsell_id": "prod_warranty_2yr_2999",
                },
            ),
            Product(
                id="prod_laptop_pro_85k",
                name="TechPro UltraBook Max (Core i9, 32GB RAM, 1TB SSD)",
                category="laptops",
                price_inr=85000.00,
                stock_quantity=10,
                description="Ultimate workstation for heavy ML development and 4K video rendering.",
                tags=["laptop", "programming", "workstation", "ml", "laptops", "i9"],
                merchant_margin_pct=0.22,
                specifications={"processor": "Intel Core i9 14th Gen", "ram": "32GB DDR5", "storage": "1TB SSD"},
            ),
            Product(
                id="prod_laptop_budget_45k",
                name="TechPro Slim 14 (Core i5, 8GB RAM, 512GB SSD)",
                category="laptops",
                price_inr=45000.00,
                stock_quantity=40,
                description="Budget daily driver for light programming and office work.",
                tags=["laptop", "budget", "programming", "laptops", "i5"],
                merchant_margin_pct=0.15,
                specifications={"processor": "Intel Core i5", "ram": "8GB DDR4"},
            ),

            # ── Peripherals ────────────────────────────────────────────────
            Product(
                id="prod_mouse_ergo_3k",
                name="ErgoMaster MX Wireless Ergonomic Mouse",
                category="peripherals",
                price_inr=2499.00,
                stock_quantity=60,
                description="Ergonomic vertical wireless mouse designed to reduce wrist strain during long coding sessions.",
                tags=["mouse", "ergonomic", "wireless", "peripherals", "office", "coding"],
                merchant_margin_pct=0.40,
                specifications={"connectivity": "Bluetooth & 2.4G", "dpi": 4000, "preferred_upsell_id": "prod_mouse_pad_mat"},
            ),
            Product(
                id="prod_mouse_pad_mat",
                name="ProDeck XXL Desk Mat Pad (900×400mm)",
                category="peripherals",
                price_inr=499.00,
                stock_quantity=200,
                description="Water-resistant micro-woven cloth desk pad with anti-fray stitched edges.",
                tags=["mouse pad", "mat", "desk pad", "accessory", "peripherals"],
                merchant_margin_pct=0.65,
                specifications={"size": "900x400mm", "thickness": "4mm"},
            ),
            Product(
                id="prod_keyboard_mech_4k",
                name="KeyPro Wireless Hot-Swappable Mechanical Keyboard",
                category="peripherals",
                price_inr=3999.00,
                stock_quantity=35,
                description="75% compact wireless mechanical keyboard with tactile brown switches and RGB backlighting.",
                tags=["keyboard", "mechanical", "wireless", "peripherals", "coding"],
                merchant_margin_pct=0.38,
                specifications={"switches": "Tactile Brown", "layout": "75%", "rgb": True},
            ),

            # ── Monitors & Displays ────────────────────────────────────────
            Product(
                id="prod_monitor_4k_35k",
                name='UltraView 27" 4K IPS 144Hz USB-C Monitor',
                category="displays",
                price_inr=32999.00,
                stock_quantity=18,
                description="27-inch 4K UHD IPS display with 99% sRGB colour accuracy, 65W USB-C Power Delivery, and 144Hz refresh rate.",
                tags=["monitor", "display", "4k", "144hz", "usb-c", "displays"],
                merchant_margin_pct=0.20,
                specifications={"resolution": "3840x2160", "refresh_rate": "144Hz", "preferred_upsell_id": "prod_monitor_light_bar"},
            ),
            Product(
                id="prod_monitor_light_bar",
                name="ScreenBar LED Monitor Light Bar Lamp",
                category="accessories",
                price_inr=1999.00,
                stock_quantity=80,
                description="Auto-dimming monitor light bar with touch brightness controls and zero screen glare.",
                tags=["light bar", "lamp", "monitor", "accessory", "desk"],
                merchant_margin_pct=0.55,
                specifications={"power": "USB-C", "dimming": "Auto Touch"},
            ),

            # ── Audio ──────────────────────────────────────────────────────
            Product(
                id="prod_headphones_anc_12k",
                name="AudioPro Active Noise Canceling Wireless Headphones",
                category="audio",
                price_inr=9999.00,
                stock_quantity=30,
                description="Premium ANC over-ear headphones with 40-hour battery life and multi-device connection.",
                tags=["headphones", "audio", "anc", "noise canceling", "wireless"],
                merchant_margin_pct=0.32,
                specifications={"anc": True, "battery": "40 Hours"},
            ),
            Product(
                id="prod_headphones_wireless_2k",
                name="SoundWave Wireless Headphones",
                category="audio",
                price_inr=2199.00,
                stock_quantity=75,
                description="Lightweight wireless headphones for calls, music, and everyday travel.",
                tags=["headphones", "audio", "wireless", "bluetooth"],
                merchant_margin_pct=0.28,
                specifications={"battery": "30 Hours", "connectivity": "Bluetooth 5.3"},
            ),
            Product(
                id="prod_iphone_69k",
                name="iPhone 15 128GB",
                category="phones",
                price_inr=69900.00,
                stock_quantity=12,
                description="Unlocked 128GB iPhone with a high-resolution camera and all-day battery.",
                tags=["iphone", "phone", "smartphone", "ios", "128gb"],
                merchant_margin_pct=0.10,
                specifications={"storage": "128GB", "network": "5G"},
            ),
            Product(
                id="prod_laptop_gaming_78k",
                name="TechPro GameBook 15 (RTX 4050, 16GB RAM, 1TB SSD)",
                category="laptops",
                price_inr=78000.00,
                stock_quantity=15,
                description="Gaming laptop with dedicated graphics, fast refresh display, and 16GB memory.",
                tags=["laptop", "gaming", "rtx", "16gb", "laptops"],
                merchant_margin_pct=0.17,
                specifications={"gpu": "RTX 4050", "ram": "16GB", "storage": "1TB SSD"},
            ),

            # ── Warranties ─────────────────────────────────────────────────
            Product(
                id="prod_warranty_2yr_2999",
                name="2-Year Extended Care & On-Site Replacement Warranty",
                category="warranty",
                price_inr=2999.00,
                stock_quantity=999,  # service — effectively unlimited
                description="Complete coverage including zero-cost hardware repairs, battery replacement, and priority on-site technician visit.",
                tags=["warranty", "protection", "care", "upsell"],
                merchant_margin_pct=0.80,
                specifications={"duration_years": 2, "coverage": "Hardware & On-Site"},
            ),
            Product(
                id="prod_warranty_3yr_4499",
                name="3-Year Ultimate Accidental Damage & Spill Protection",
                category="warranty",
                price_inr=4499.00,
                stock_quantity=999,
                description="Comprehensive 3-year warranty covering liquid spills, drops, screen cracks, and priority support.",
                tags=["warranty", "spill", "protection"],
                merchant_margin_pct=0.70,
                specifications={"duration_years": 3, "coverage": "Spill & Drop Damage"},
            ),
        ]

    # ── Read ─────────────────────────────────────────────────────────────────

    def get_all_products(self) -> List[Product]:
        return self._products + self._external_products

    def get_product_by_id(self, product_id: str) -> Optional[Product]:
        for p in self.get_all_products():
            if p.id == product_id:
                return p
        return None

    def get_in_stock_products(self) -> List[Product]:
        return [p for p in self.get_all_products() if p.in_stock and p.stock_quantity > 0]

    # ── Stock management ─────────────────────────────────────────────────────

    def deduct_stock(self, product_id: str, quantity: int = 1) -> Dict[str, Any]:
        """
        Deducts stock after a confirmed payment. Raises ValueError if out of stock.
        Returns a dict with before/after stock info for audit logging.
        """
        product = self.get_product_by_id(product_id)
        if not product:
            raise ValueError(f"Product not found: {product_id}")

        stock_before = product.stock_quantity
        product.deduct(quantity)
        stock_after = product.stock_quantity

        logger.info(
            "STOCK_DEDUCTED product=%s qty=%d before=%d after=%d in_stock=%s",
            product_id, quantity, stock_before, stock_after, product.in_stock,
        )

        return {
            "product_id": product_id,
            "product_name": product.name,
            "quantity_deducted": quantity,
            "stock_before": stock_before,
            "stock_after": stock_after,
            "still_in_stock": product.in_stock,
        }

    def get_stock_snapshot(self) -> List[Dict[str, Any]]:
        """Returns current stock levels for all products — useful for audit/monitoring."""
        return [
            {
                "product_id": p.id,
                "name": p.name,
                "category": p.category,
                "price_inr": p.price_inr,
                "stock_quantity": p.stock_quantity,
                "in_stock": p.in_stock,
            }
            for p in self.get_all_products()
        ]

    # ── Structured Search ────────────────────────────────────────────────────

    def structured_search(self, query: ProductQuery) -> Tuple[List[Product], int, Dict[str, Any]]:
        """
        Structured catalog search — no embeddings, no RAG, no cosine similarity.

        Pipeline:
          1. Hard filter: stock available + price within budget
          2. Exclude warranty products unless explicitly requested
          3. Score each product using structured field matching
          4. Sort by score DESC, then merchant_margin_pct DESC
          5. Return results with match metadata

        Returns: (matched_products, match_count, search_meta)
        """
        query_lower = query.query_text.lower()
        detected_cats = _detect_categories(query_lower)
        warranty_requested = "warranty" in query_lower

        # ── Step 1: Hard filters ──────────────────────────────────────────
        candidates: List[Product] = []
        for p in self._products:
            if not p.in_stock or p.stock_quantity <= 0:
                continue
            if p.price_inr > query.max_budget_inr:
                continue
            # Warranty products only appear if explicitly in query or as upsell
            if p.category == "warranty" and not warranty_requested:
                continue
            # Category filter when explicitly provided
            if query.category and p.category != query.category:
                continue
            candidates.append(p)

        if not candidates:
            return [], 0, {"detected_categories": detected_cats, "filters_applied": True}

        # ── Step 2: Score each candidate ─────────────────────────────────
        scored: List[Tuple[float, Product]] = []
        for p in candidates:
            score = _structured_score(query_lower, p, detected_cats)
            scored.append((score, p))

        # ── Step 3: Sort by score DESC, then margin DESC ──────────────────
        scored.sort(key=lambda x: (x[0], x[1].merchant_margin_pct), reverse=True)

        matched = [p for _, p in scored]
        top_score = scored[0][0] if scored else 0.0

        meta = {
            "detected_categories": detected_cats,
            "total_candidates_after_filter": len(candidates),
            "top_structured_score": top_score,
            "search_method": "structured_field_scoring",
        }

        return matched, len(matched), meta

    def search_for_agent(self, query: ProductQuery) -> Tuple[List[Product], int, Dict[str, Any]]:
        """Use the authorised Amazon MCP source when configured, else local mock data."""
        from app.config import settings
        if not settings.AMAZON_MCP_ENABLED:
            return self.structured_search(query)
        from app.merchant.amazon_mcp import AmazonMCPProvider
        products = AmazonMCPProvider().search(query)
        self._external_products = products
        detected = _detect_categories(query.query_text.lower())
        products.sort(key=lambda p: _structured_score(query.query_text.lower(), p, detected), reverse=True)
        return products, len(products), {"detected_categories": detected,
            "search_method": "authorized_amazon_mcp", "source": "amazon_mcp"}

    # ── Legacy alias so existing callers don't break ─────────────────────────
    def vector_search_catalog(self, query: ProductQuery) -> Tuple[List[Product], int, float]:
        """Alias for backward compatibility. Calls structured_search internally."""
        products, count, meta = self.search_for_agent(query)
        return products, count, meta.get("top_structured_score", 0.0)

    def search_catalog(self, query: ProductQuery) -> List[Product]:
        products, _, _ = self.search_for_agent(query)
        return products
