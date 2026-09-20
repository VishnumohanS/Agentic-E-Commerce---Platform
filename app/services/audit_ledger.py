"""
First-Class Immutable Cryptographic Audit Trail Ledger.
Maintains a tamper-evident, hash-chained log of all agent interactions,
upsell justifications, AP2 mandate validations, and Razorpay API responses,
persisted into DynamoDB.
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import List, Dict, Any
from app.models import AuditEvent
from app.services.database_ledger import DatabaseLedger

class AuditLedgerEngine:
    def __init__(self):
        self.events: List[AuditEvent] = []
        self.db_ledger = DatabaseLedger()
        self._genesis_event()

    def _genesis_event(self):
        genesis = AuditEvent(
            sequence=0,
            timestamp=datetime.now(timezone.utc).isoformat(),
            state="GENESIS",
            actor="SYSTEM",
            title="Audit Ledger Initialized",
            details={"system": "Merchant Agent Commerce Protocol Ledger v1.0"},
            prev_hash="0000000000000000000000000000000000000000000000000000000000000000",
        )
        genesis.current_hash = self._compute_hash(genesis)
        self.events.append(genesis)

    def _compute_hash(self, event: AuditEvent) -> str:
        payload = f"{event.sequence}:{event.timestamp}:{event.actor}:{event.state}:{event.title}:{json.dumps(event.details, sort_keys=True)}:{event.prev_hash}"
        return hashlib.sha256(payload.encode('utf-8')).hexdigest()

    def record_event(self, actor: str, state: str, title: str, details: Dict[str, Any], session_id: str = "#TX-9042") -> AuditEvent:
        """
        Appends a new event to the ledger with hash chaining and DynamoDB persistence.
        """
        prev = self.events[-1]
        seq = prev.sequence + 1
        ts = datetime.now(timezone.utc).isoformat()

        event_details = dict(details)
        audit_record_id = self.db_ledger.generate_audit_record_id(session_id, seq)
        event_details["audit_record_id"] = audit_record_id

        event = AuditEvent(
            sequence=seq,
            timestamp=ts,
            state=state,
            actor=actor,
            title=title,
            details=event_details,
            prev_hash=prev.current_hash,
            current_hash=""
        )
        event.current_hash = self._compute_hash(event)
        self.events.append(event)

        # Persist to DynamoDB
        audit_rec_id = self.db_ledger.insert_record(
            session_id=session_id,
            seq=seq,
            actor=actor,
            state=state,
            title=title,
            details=event_details,
            prev_hash=prev.current_hash,
            current_hash=event.current_hash,
            audit_record_id=audit_record_id
        )

        return event

    def get_full_ledger(self) -> List[AuditEvent]:
        return self.events

    def verify_ledger_integrity(self) -> bool:
        """
        Verifies the cryptographic integrity of the entire hash chain.
        """
        for i in range(1, len(self.events)):
            curr = self.events[i]
            prev = self.events[i-1]

            if curr.prev_hash != prev.current_hash:
                return False

            if curr.current_hash != self._compute_hash(curr):
                return False

        return True
