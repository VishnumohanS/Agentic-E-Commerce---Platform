from pydantic import BaseModel
from typing import List

class BuyerIntent(BaseModel):
    query: str
    budget_inr: float
    category: str = 'electronics'
    constraints: List[str] = []

class PurchaseRequest(BaseModel):
    user_query: str
    max_budget_inr: float = 2500.0

class PurchaseSession(BaseModel):
    session_id: str
    query: str
    budget: float
    status: str = 'pending'
    events: List[dict] = []
    created_at: str
