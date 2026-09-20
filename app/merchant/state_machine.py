"""
Transaction State Machine & Failure Handling Engine.
Tracks order states, enforces allowed state transitions, and manages rollback/failure recovery.
"""

from app.models import TransactionState
from typing import Dict, Any

VALID_TRANSITIONS = {
    TransactionState.INITIATED: [TransactionState.DISCOVERED, TransactionState.FAILED],
    TransactionState.DISCOVERED: [TransactionState.RECOMMENDED, TransactionState.FAILED],
    TransactionState.RECOMMENDED: [TransactionState.UPSELL_OFFERED, TransactionState.CART_FINALIZED, TransactionState.FAILED],
    TransactionState.UPSELL_OFFERED: [TransactionState.UPSELL_ACCEPTED, TransactionState.CART_FINALIZED, TransactionState.FAILED],
    TransactionState.UPSELL_ACCEPTED: [TransactionState.CART_FINALIZED, TransactionState.FAILED],
    TransactionState.CART_FINALIZED: [TransactionState.MANDATE_VERIFIED, TransactionState.FAILED],
    TransactionState.MANDATE_VERIFIED: [TransactionState.PAYMENT_CREATED, TransactionState.FAILED],
    TransactionState.PAYMENT_CREATED: [TransactionState.PAYMENT_SUCCESS, TransactionState.FAILED],
    TransactionState.PAYMENT_SUCCESS: [TransactionState.ORDER_CONFIRMED, TransactionState.FAILED],
    TransactionState.ORDER_CONFIRMED: [],
    TransactionState.FAILED: [TransactionState.INITIATED]
}

class CommerceStateMachine:
    def __init__(self, transaction_id: str):
        self.transaction_id = transaction_id
        self.current_state = TransactionState.INITIATED
        self.history = [TransactionState.INITIATED]

    def transition_to(self, target_state: str, context: str = "") -> bool:
        allowed = VALID_TRANSITIONS.get(self.current_state, [])
        if target_state not in allowed:
            raise ValueError(
                f"Invalid state transition from '{self.current_state}' to '{target_state}'. "
                f"Allowed next states: {allowed}"
            )
        
        self.current_state = target_state
        self.history.append(target_state)
        return True

    def fail(self, error_message: str):
        self.current_state = TransactionState.FAILED
        self.history.append(TransactionState.FAILED)
