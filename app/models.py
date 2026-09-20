"""
Core Pydantic Data Models for Agentic Commerce, AP2 Mandates, and Razorpay Payments.

NOTE: A2A protocol types (AgentCard, Message, Task, TaskStatus, Role, etc.) are
NOT defined here — they come from the real `a2a-sdk` library:
    from a2a.types import AgentCard, Message, Part, Role, Task, TaskStatus, TaskState

MCP protocol types (Tool, TextContent, CallToolResult) are also NOT defined here:
    from mcp.types import Tool, TextContent, CallToolResult, ListToolsResult
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime, timezone
import uuid

# --- Product & Catalog Models (MCP / UCP) ---

class Product(BaseModel):
    id: str
    name: str
    category: str
    price_inr: float
    description: str
    tags: List[str] = []
    merchant_margin_pct: float = Field(default=0.20, description="Merchant profit margin percentage for dynamic upsell calculation")
    in_stock: bool = True
    stock_quantity: int = Field(default=100, description="Actual inventory count. in_stock is derived from stock_quantity > 0.")
    specifications: Dict[str, Any] = Field(default_factory=dict)
    source: str = "merchant_catalog"
    product_url: Optional[str] = None
    image: Optional[str] = None
    rating: Optional[float] = None

    def deduct(self, qty: int = 1) -> None:
        """Decrement inventory. Raises ValueError if insufficient stock."""
        if self.stock_quantity < qty:
            raise ValueError(f"Insufficient stock for '{self.name}': available={self.stock_quantity}, requested={qty}")
        self.stock_quantity -= qty
        self.in_stock = self.stock_quantity > 0


class ProductQuery(BaseModel):
    query_text: str
    max_budget_inr: float
    category: Optional[str] = None
    min_specs: Optional[Dict[str, Any]] = None

# --- AP2 Bounded Mandate Models ---

class AP2Mandate(BaseModel):
    mandate_id: str = Field(default_factory=lambda: f"mandate_{uuid.uuid4().hex[:10]}")
    buyer_agent_id: str
    user_id: str
    max_amount_inr: float
    authorized_merchant_id: str
    allowed_categories: List[str] = Field(default_factory=lambda: [
        "charging", "laptops", "peripherals", "displays", "audio",
        "electronics", "phones", "software", "warranty", "services", "accessories",
    ])
    expires_at: str
    nonce: str = Field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class AP2MandateSignature(BaseModel):
    mandate: AP2Mandate
    signature: str
    public_key_thumbprint: str

class MandateValidationResult(BaseModel):
    valid: bool
    reason: str
    requested_amount_inr: float
    max_allowed_inr: float
    remaining_headroom_inr: float

# --- Cart & Upsell Models ---

class CartItem(BaseModel):
    product_id: str
    name: str
    category: Optional[str] = None
    price_inr: float
    quantity: int = 1
    is_upsell: bool = False
    upsell_reason: Optional[str] = None

class Cart(BaseModel):
    cart_id: str = Field(default_factory=lambda: f"cart_{uuid.uuid4().hex[:8]}")
    items: List[CartItem] = Field(default_factory=list)
    base_subtotal_inr: float = 0.0
    upsell_subtotal_inr: float = 0.0
    total_amount_inr: float = 0.0
    currency: str = "INR"

class UpsellOffer(BaseModel):
    product: Product
    pitch: str
    additional_cost_inr: float
    new_cart_total_inr: float
    within_mandate: bool

# --- A2A Protocol ---
# REMOVED: Fake A2AMessageType and A2AMessage classes.
# Use real `a2a-sdk` types instead:
#   from a2a.types import Message, Part, Role, Task, TaskStatus, TaskState
#   from a2a.types import AgentCard, AgentCapabilities, AgentSkill, AgentInterface
#   from a2a.types import SendMessageRequest, SendMessageResponse

# --- Transaction State Machine & Audit ---

class TransactionState:
    INITIATED = "INITIATED"
    DISCOVERED = "DISCOVERED"
    RECOMMENDED = "RECOMMENDED"
    UPSELL_OFFERED = "UPSELL_OFFERED"
    UPSELL_ACCEPTED = "UPSELL_ACCEPTED"
    CART_FINALIZED = "CART_FINALIZED"
    MANDATE_VERIFIED = "MANDATE_VERIFIED"
    PAYMENT_CREATED = "PAYMENT_CREATED"
    PAYMENT_SUCCESS = "PAYMENT_SUCCESS"
    ORDER_CONFIRMED = "ORDER_CONFIRMED"
    FAILED = "FAILED"

class AuditEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:10]}")
    sequence: int
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    state: str
    actor: str  # "AI_BUYER", "MERCHANT_AGENT", "AP2_MANDATE_ENGINE", "RAZORPAY_API"
    title: str
    details: Dict[str, Any]
    prev_hash: str = ""
    current_hash: str = ""

# --- Razorpay Integration Models ---

class RazorpayOrderResponse(BaseModel):
    order_id: str
    amount_inr: float
    amount_paise: int
    currency: str
    status: str
    receipt: str
    created_at: int

class PaymentVerification(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str

class OrderReceipt(BaseModel):
    order_id: str
    razorpay_payment_id: str
    cart: Cart
    buyer_id: str
    merchant_id: str
    mandate_id: str
    total_paid_inr: float
    merchant_revenue_inr: float
    upsell_revenue_gained_inr: float
    timestamp: str
    audit_hash_chain: str
