"""
Integration tests for FastAPI endpoints and complete end-to-end commerce flow.
"""

import pytest
from fastapi.testclient import TestClient
from main import app
from app.config import settings

client = TestClient(app)

def test_mcp_tools_endpoint():
    response = client.get("/api/mcp/tools")
    assert response.status_code == 200
    data = response.json()
    assert "tools" in data
    assert len(data["tools"]) >= 4

def test_ucp_catalog_endpoint():
    response = client.get("/api/catalog")
    assert response.status_code == 200
    data = response.json()
    assert data["merchant_id"] == "merchant_techverse_01"
    assert len(data["items"]) >= 3

def test_run_autonomous_flow_success():
    payload = {
        "user_query": "I need a laptop for programming under Rs 70000.",
        "max_budget_inr": 70000.00,
    }
    response = client.post("/api/commerce/run-flow", json=payload)
    assert response.status_code == 200
    data = response.json()
    # Autonomous flow always returns SUCCESS — no PENDING_CHECKOUT state
    assert data["status"] == "SUCCESS"
    assert data["cart"]["total_amount_inr"] > 0
    assert data["receipt"]["order_id"] is not None
    assert data["receipt"]["order_id"].startswith("order_")


def test_run_autonomous_flow_budget_too_low():
    # Budget of 50 INR, below all catalog items -> 404 No Products Found
    payload = {
        "user_query": "I need a 65W GaN fast charger under ₹50.",
        "max_budget_inr": 50.00
    }
    response = client.post("/api/commerce/run-flow", json=payload)
    assert response.status_code == 404

def test_razorpay_failure_is_recorded_without_success():
    response = client.post("/api/razorpay/failure", json={
        "order_id": "order_test_failure",
        "code": "BAD_REQUEST_ERROR",
        "description": "Authentication failed",
        "reason": "authentication_failed",
    })
    assert response.status_code == 200
    assert response.json()["status"] == "PAYMENT_FAILED_RECORDED"
