"""DynamoDB-backed persistent audit ledger."""

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.config import settings


class DatabaseLedger:
    """Persists audit events in a DynamoDB single table."""

    def __init__(self, table_name: str | None = None):
        import boto3

        self.table_name = table_name or settings.DYNAMODB_TABLE_NAME
        if not self.table_name:
            raise RuntimeError("DYNAMODB_TABLE_NAME must be configured.")
        self._table = boto3.resource(
            "dynamodb", region_name=settings.AWS_REGION or None
        ).Table(self.table_name)

    def generate_audit_record_id(self, session_id: str, seq: int) -> str:
        raw = f"{session_id}:{seq}:{datetime.now(timezone.utc).timestamp()}"
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:7].upper()
        return f"0x{digest}"

    @staticmethod
    def _decimal(value: Any) -> Decimal:
        return Decimal(str(value))

    def insert_record(
        self,
        session_id: str,
        seq: int,
        actor: str,
        state: str,
        title: str,
        details: Dict[str, Any],
        prev_hash: str = "",
        current_hash: str = "",
        audit_record_id: Optional[str] = None,
    ) -> str:
        audit_rec_id = audit_record_id or self.generate_audit_record_id(session_id, seq)
        item = {
            "pk": f"SESSION#{session_id}",
            "sk": f"EVENT#{seq:012d}",
            "audit_record_id": audit_rec_id,
            "session_id": session_id,
            "sequence": seq,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "actor": actor,
            "state": state,
            "title": title,
            "intent": str(details.get("intent", details.get("user_query", "Hardware Procurement"))),
            "vector_matches_count": int(details.get("matched_count", details.get("vector_matches_count", 0))),
            "budget_max_inr": self._decimal(
                details.get("max_budget_inr", details.get("authorized_max_inr", 0.0))
            ),
            "offered_amount_inr": self._decimal(
                details.get("total_amount_inr", details.get("price_inr", 0.0))
            ),
            "bounding_rule_status": "FAILED" if state == "FAILED" or details.get("valid") is False else "PASSED",
            "razorpay_order_id": str(details.get("razorpay_order_id", details.get("order_id", ""))),
            "razorpay_payment_id": str(details.get("razorpay_payment_id", details.get("payment_id", ""))),
            "ap2_signature_status": "INVALID" if details.get("valid") is False else "VALID",
            "details_json": json.dumps(details, sort_keys=True),
            "prev_hash": prev_hash,
            "current_hash": current_hash,
        }
        self._table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(pk) AND attribute_not_exists(sk)",
        )
        return audit_rec_id

    @staticmethod
    def _decode(item: Dict[str, Any]) -> Dict[str, Any]:
        decoded = dict(item)
        decoded["id"] = decoded.get("sk", "").removeprefix("EVENT#")
        if "details_json" in decoded:
            decoded["details"] = json.loads(decoded.pop("details_json"))
        return decoded

    def get_records_for_session(self, session_id: str) -> List[Dict[str, Any]]:
        response = self._table.query(
            KeyConditionExpression="pk = :pk",
            ExpressionAttributeValues={":pk": f"SESSION#{session_id}"},
            ScanIndexForward=True,
        )
        return [self._decode(item) for item in response.get("Items", [])]

    def get_all_records(self, limit: int = 50) -> List[Dict[str, Any]]:
        response = self._table.scan(
            FilterExpression="begins_with(sk, :prefix)",
            ExpressionAttributeValues={":prefix": "EVENT#"},
            Limit=max(1, limit),
        )
        records = [self._decode(item) for item in response.get("Items", [])]
        records.sort(key=lambda item: item.get("timestamp", ""), reverse=True)
        return records[:limit]
