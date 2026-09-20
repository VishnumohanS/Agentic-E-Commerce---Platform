import hashlib
import hmac
from uuid import uuid4
from datetime import datetime, timezone, timedelta

def create_signed_mandate(buyer_agent_id: str, user_id: str, max_amount_inr: float, authorized_merchant_id: str, secret: str) -> dict:
    mandate_id = f'mandate_{uuid4().hex[:10]}'
    nonce = uuid4().hex
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    
    payload = f'{mandate_id}:{buyer_agent_id}:{user_id}:{max_amount_inr}:{authorized_merchant_id}:{expires_at}:{nonce}'
    signature = hmac.new(secret.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
    
    mandate = {
        'mandate_id': mandate_id,
        'buyer_agent_id': buyer_agent_id,
        'user_id': user_id,
        'max_amount_inr': max_amount_inr,
        'authorized_merchant_id': authorized_merchant_id,
        'expires_at': expires_at,
        'nonce': nonce,
        'created_at': datetime.now(timezone.utc).isoformat()
    }
    
    return {
        'mandate': mandate,
        'signature': signature,
        'public_key_thumbprint': 'default_thumbprint'
    }
