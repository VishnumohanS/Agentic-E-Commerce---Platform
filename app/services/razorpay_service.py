"""Razorpay order creation and server-side Standard Checkout verification.

Razorpay test mode never creates a payment on a user's behalf. It creates an
order and waits for a browser Checkout callback. Mock mode is the explicit,
offline-only alternative used by the local demo and automated tests.
"""

import hashlib
import hmac
import logging
import time
import uuid
from typing import Dict, Tuple

import razorpay

from app.config import settings
from app.models import Cart, PaymentVerification, RazorpayOrderResponse

logger = logging.getLogger(__name__)


class RazorpayService:
    def __init__(self, key_id: str | None = None, key_secret: str | None = None):
        self._explicit_credentials = key_id is not None or key_secret is not None
        self.key_id = key_id if key_id is not None else settings.RAZORPAY_KEY_ID
        self.key_secret = key_secret if key_secret is not None else settings.RAZORPAY_KEY_SECRET
        self.client = None
        self.pending_checkouts: Dict[str, dict] = {}
        if self.key_id and self.key_secret and (self._explicit_credentials or settings.razorpay_mode() == "test"):
            try:
                self.client = razorpay.Client(auth=(self.key_id, self.key_secret))
            except Exception as exc:
                logger.warning("Razorpay client could not be initialised: %s", exc)

    @property
    def mode(self) -> str:
        return "test" if self.client else "mock"

    def create_order(self, cart: Cart, buyer_id: str) -> RazorpayOrderResponse:
        """Create an order using the backend-calculated cart total only."""
        amount_paise = int(round(cart.total_amount_inr * 100))
        if amount_paise <= 0:
            raise RuntimeError("Cannot create a payment order with a non-positive amount.")
        receipt = f"rcpt_{cart.cart_id}"
        if self.client:
            try:
                result = self.client.order.create(data={
                    "amount": amount_paise, "currency": "INR", "receipt": receipt,
                    "payment_capture": 1,
                    "notes": {"buyer_id": buyer_id, "cart_id": cart.cart_id, "protocol": "A2A+AP2+MCP"},
                })
            except Exception as exc:
                raise RuntimeError(f"Razorpay order creation failed: {exc}") from exc
            return RazorpayOrderResponse(order_id=result["id"], amount_inr=cart.total_amount_inr,
                amount_paise=int(result["amount"]), currency=result.get("currency", "INR"),
                status=result.get("status", "created"), receipt=result.get("receipt", receipt),
                created_at=result.get("created_at", int(time.time())))
        return RazorpayOrderResponse(order_id=f"order_sim_{uuid.uuid4().hex[:14]}", amount_inr=cart.total_amount_inr,
            amount_paise=amount_paise, currency="INR", status="created", receipt=receipt, created_at=int(time.time()))

    def checkout_options(self, order: RazorpayOrderResponse) -> dict:
        """Payload for checkout.js; no caller-supplied amount is accepted."""
        return {"key": self.key_id, "order_id": order.order_id, "amount": order.amount_paise,
                "currency": order.currency, "name": "Agentic Commerce", "description": "Approved product order"}

    def register_checkout(self, order: RazorpayOrderResponse, cart: Cart, session_id: str) -> dict:
        self.pending_checkouts[order.order_id] = {"amount_paise": order.amount_paise, "cart": cart, "session_id": session_id}
        return self.checkout_options(order)

    def execute_payment(self, order_id: str, amount_paise: int) -> Tuple[str, str]:
        """Compatibility helper. Only mock orders may be settled by the server."""
        if self.client and not order_id.startswith("order_sim_"):
            raise RuntimeError("Razorpay test payments require user completion in Standard Checkout.")
        return self._simulated_payment(order_id)

    def generate_simulated_payment(self, order_id: str) -> Tuple[str, str]:
        return self._simulated_payment(order_id)

    def _generate_hmac(self, order_id: str, payment_id: str) -> str:
        return hmac.new((self.key_secret or "mock_razorpay_secret").encode(),
                        f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()

    def _simulated_payment(self, order_id: str) -> Tuple[str, str]:
        payment_id = f"pay_mock_{uuid.uuid4().hex[:14]}"
        return payment_id, self._generate_hmac(order_id, payment_id)

    def verify_payment_signature(self, verification: PaymentVerification) -> bool:
        return hmac.compare_digest(verification.razorpay_signature,
                                   self._generate_hmac(verification.razorpay_order_id, verification.razorpay_payment_id))

    def verify_checkout_payment(self, verification: PaymentVerification) -> bool:
        if not self.client:
            return self.verify_payment_signature(verification)
        try:
            self.client.utility.verify_payment_signature({"razorpay_order_id": verification.razorpay_order_id,
                "razorpay_payment_id": verification.razorpay_payment_id, "razorpay_signature": verification.razorpay_signature})
            return True
        except Exception:
            return False

    def get_payment_status(self, payment_id: str, expected_amount_paise: int) -> dict:
        if not self.client:
            return {"id": payment_id, "status": "captured", "amount": expected_amount_paise}
        payment = self.client.payment.fetch(payment_id)
        if payment.get("status") != "captured" or int(payment.get("amount", -1)) != expected_amount_paise:
            raise RuntimeError("Razorpay payment was not captured for the trusted order amount.")
        return payment
