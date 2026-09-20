"""
AP2 (Agent Payment Protocol) Bounded Mandates Engine.
Provides mandate issuance, cryptographic signature verification, budget boundary enforcement,
and category/merchant restriction checks.
"""

import hmac
import hashlib
import json
from datetime import datetime, timezone
from typing import Tuple
from app.models import AP2Mandate, AP2MandateSignature, MandateValidationResult, Cart
from app.config import settings

SECRET_KEY_FOR_MANDATES = settings.AP2_MANDATE_SECRET

class AP2MandateEngine:
    @staticmethod
    def compute_mandate_signature(mandate: AP2Mandate, secret: str = SECRET_KEY_FOR_MANDATES) -> str:
        """
        Computes an HMAC-SHA256 signature for the bounded mandate to prevent tampering.
        """
        payload_str = f"{mandate.mandate_id}:{mandate.buyer_agent_id}:{mandate.user_id}:{mandate.max_amount_inr}:{mandate.authorized_merchant_id}:{mandate.expires_at}:{mandate.nonce}"
        return hmac.new(secret.encode('utf-8'), payload_str.encode('utf-8'), hashlib.sha256).hexdigest()

    @classmethod
    def create_signed_mandate(
        cls,
        buyer_agent_id: str,
        user_id: str,
        max_amount_inr: float,
        authorized_merchant_id: str,
        allowed_categories: list = None,
        expires_at: str = None
    ) -> AP2MandateSignature:
        """
        Creates a new AP2 Bounded Mandate signed with user secret key.
        """
        if not expires_at:
            # Default 1 hour validity window
            from datetime import timedelta
            expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            
        if allowed_categories is None:
            allowed_categories = [
                "charging", "laptops", "peripherals", "displays", "audio",
                "electronics", "software", "warranty", "services", "accessories",
            ]

        mandate = AP2Mandate(
            buyer_agent_id=buyer_agent_id,
            user_id=user_id,
            max_amount_inr=max_amount_inr,
            authorized_merchant_id=authorized_merchant_id,
            allowed_categories=allowed_categories,
            expires_at=expires_at
        )

        signature = cls.compute_mandate_signature(mandate)
        return AP2MandateSignature(
            mandate=mandate,
            signature=signature,
            public_key_thumbprint=f"thumbprint_{hashlib.sha256(user_id.encode()).hexdigest()[:12]}"
        )

    @classmethod
    def validate_mandate(
        cls,
        mandate_sig: AP2MandateSignature,
        merchant_id: str,
        cart: Cart
    ) -> MandateValidationResult:
        """
        Validates AP2 mandate against the cart total, merchant identity, expiration, and signature integrity.
        """
        mandate = mandate_sig.mandate
        
        # 1. Verify Signature Integrity
        expected_sig = cls.compute_mandate_signature(mandate)
        if not hmac.compare_digest(mandate_sig.signature, expected_sig):
            return MandateValidationResult(
                valid=False,
                reason="Mandate signature verification failed! Tampered or forged AP2 mandate.",
                requested_amount_inr=cart.total_amount_inr,
                max_allowed_inr=mandate.max_amount_inr,
                remaining_headroom_inr=0.0
            )

        # 2. Verify Expiration
        try:
            exp_time = datetime.fromisoformat(mandate.expires_at)
            if datetime.now(timezone.utc) > exp_time:
                return MandateValidationResult(
                    valid=False,
                    reason=f"Mandate expired at {mandate.expires_at}.",
                    requested_amount_inr=cart.total_amount_inr,
                    max_allowed_inr=mandate.max_amount_inr,
                    remaining_headroom_inr=0.0
                )
        except Exception:
            return MandateValidationResult(
                valid=False,
                reason=f"Mandate expiry is invalid: {mandate.expires_at}.",
                requested_amount_inr=cart.total_amount_inr,
                max_allowed_inr=mandate.max_amount_inr,
                remaining_headroom_inr=0.0
            )

        # 3. Verify Authorized Merchant
        if mandate.authorized_merchant_id != "*" and mandate.authorized_merchant_id != merchant_id:
            return MandateValidationResult(
                valid=False,
                reason=f"Mandate restricted to merchant '{mandate.authorized_merchant_id}', cannot transact with '{merchant_id}'.",
                requested_amount_inr=cart.total_amount_inr,
                max_allowed_inr=mandate.max_amount_inr,
                remaining_headroom_inr=0.0
            )

        for item in cart.items:
            if item.category and item.category not in mandate.allowed_categories:
                return MandateValidationResult(
                    valid=False,
                    reason=f"Cart item category '{item.category}' is not allowed by this mandate.",
                    requested_amount_inr=cart.total_amount_inr,
                    max_allowed_inr=mandate.max_amount_inr,
                    remaining_headroom_inr=0.0
                )

        # 4. Verify Amount Limit
        requested_total = cart.total_amount_inr
        max_allowed = mandate.max_amount_inr
        headroom = max_allowed - requested_total

        if requested_total > max_allowed:
            return MandateValidationResult(
                valid=False,
                reason=f"Cart total (₹{requested_total:,.2f}) exceeds AP2 Mandate upper boundary limit (₹{max_allowed:,.2f}) by ₹{abs(headroom):,.2f}.",
                requested_amount_inr=requested_total,
                max_allowed_inr=max_allowed,
                remaining_headroom_inr=headroom
            )

        # Valid mandate
        return MandateValidationResult(
            valid=True,
            reason=f"AP2 Mandate authorized cleanly. Cart Total: ₹{requested_total:,.2f} <= Mandate Limit: ₹{max_allowed:,.2f}. Headroom: ₹{headroom:,.2f}.",
            requested_amount_inr=requested_total,
            max_allowed_inr=max_allowed,
            remaining_headroom_inr=headroom
        )
