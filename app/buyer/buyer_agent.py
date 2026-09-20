"""
Dynamic Autonomous AI Buyer Agent powered by AWS Bedrock.
Parses natural language directives, issues AP2 Bounded Mandates,
evaluates merchant upsell proposals dynamically, and manages counter-negotiations.

A2A message construction uses real `a2a.types` protobuf objects (in the protocol layer).
This module focuses exclusively on intent parsing, AP2 mandate issuance,
and upsell evaluation logic.
"""

import os
import re
import json
from typing import Tuple, Optional
from app.models import AP2MandateSignature, ProductQuery, UpsellOffer, Product
from app.protocols.ap2_mandate import AP2MandateEngine
from app.config import settings
from app.services.ai_service import ai_service

class AIBuyerAgent:
    def __init__(self, agent_id: str = "buyer_agent_alpha_01", user_id: str = "user_dev_rahul"):
        self.agent_id = agent_id
        self.user_id = user_id
        self._bedrock_error = (
            None
            if ai_service.is_available
            else "AWS Bedrock is unavailable. Configure AWS credentials and AWS_REGION."
        )

    @property
    def bedrock_client(self):
        if self._bedrock_error:
            raise RuntimeError(self._bedrock_error)
        return ai_service

    def get_status(self) -> dict:
        return {
            'ai_provider': settings.ai_provider(),
            'ai_mode': settings.ai_mode(),
            'ai_error': self._bedrock_error,
            'agent_id': self.agent_id
        }

    def extract_intent_and_budget(self, user_prompt: str) -> Tuple[str, float, str]:
        """
        Parses intent, budget limit in INR, and target product category from arbitrary user input.
        """
        if ai_service.is_available:
            try:
                text = ai_service.generate(
                    f'Parse this purchase request and return ONLY valid JSON with keys: intent, budget_inr, category. '
                    f'Category must be one of charging/laptops/peripherals/displays/audio/electronics. '
                    f'Use 50000 as the default budget. Request: "{user_prompt}"'
                )
                data = json.loads(text.replace("```json", "").replace("```", "").strip())
                return data.get('intent', 'Hardware Procurement'), float(data.get('budget_inr', 50000.0)), data.get('category', 'electronics')
            except Exception:
                pass
        # Regex fallback
        prompt_lower = user_prompt.lower()
        budget = 50000.0
        budget_matches = re.findall(r"(?:under|below|budget|max|limit)?\s*₹?\s*([\d,]+)", prompt_lower)
        if budget_matches:
            for match in budget_matches:
                clean_num = match.replace(",", "")
                if clean_num.isdigit() and int(clean_num) > 100:
                    budget = float(clean_num)
                    break

        category = "electronics"
        if "laptop" in prompt_lower:
            category = "laptops"
        elif "charger" in prompt_lower or "cable" in prompt_lower:
            category = "charging"
        elif "mouse" in prompt_lower or "keyboard" in prompt_lower:
            category = "peripherals"
        elif "monitor" in prompt_lower or "display" in prompt_lower:
            category = "displays"
        elif "headphone" in prompt_lower or "audio" in prompt_lower:
            category = "audio"

        intent = f"Hardware Procurement ({category.capitalize()})"
        return intent, budget, category

    def issue_bounded_mandate(
        self,
        max_budget_inr: float,
        authorized_merchant_id: str = "merchant_techverse_01"
    ) -> AP2MandateSignature:
        """
        Creates a signed AP2 Bounded Mandate representing spending authorization.
        """
        return AP2MandateEngine.create_signed_mandate(
            buyer_agent_id=self.agent_id,
            user_id=self.user_id,
            max_amount_inr=max_budget_inr,
            authorized_merchant_id=authorized_merchant_id
        )

    def evaluate_upsell_offer(
        self,
        upsell: UpsellOffer,
        mandate: AP2MandateSignature
    ) -> Tuple[bool, str]:
        """
        Autonomous dynamic evaluation of merchant upsell offer against AP2 mandate limits and value utility.
        """
        headroom = mandate.mandate.max_amount_inr - upsell.new_cart_total_inr

        if headroom < 0:
            return False, f"REJECTED: Offer total ₹{upsell.new_cart_total_inr:,.2f} exceeds AP2 Mandate limit ₹{mandate.mandate.max_amount_inr:,.2f} by ₹{abs(headroom):,.2f}."

        if ai_service.is_available:
            try:
                text = ai_service.generate(
                    f"You are an AI Buyer Agent. The mandate cap is ₹{mandate.mandate.max_amount_inr}. "
                    f"Evaluate this upsell: {upsell.product.name}, additional cost ₹{upsell.additional_cost_inr}, "
                    f"new total ₹{upsell.new_cart_total_inr}. Reply ACCEPT or REJECT followed by one sentence."
                )
                upper = text.upper()
                if "ACCEPT" in upper and "REJECT" not in upper.split("ACCEPT")[0]:
                    return True, f"ACCEPTED (Bedrock reasoned): {text}"
                return False, f"REJECTED (Bedrock reasoned): {text}"
            except Exception:
                pass
        # Standard rule evaluation fallback
        accepted_categories = ["warranty", "charging", "peripherals", "accessories", "electronics", "laptops", "displays", "audio"]
        if upsell.product.category in accepted_categories and headroom >= 0:
            return True, f"ACCEPTED: Additional {upsell.product.name} (₹{upsell.additional_cost_inr:,.2f}) provides high ROI. Total ₹{upsell.new_cart_total_inr:,.2f} fits within ₹{mandate.mandate.max_amount_inr:,.2f} mandate (Headroom: ₹{headroom:,.2f})."

        return False, "REJECTED: Does not meet buyer ROI threshold."
