"""
Integration tests for Edge Cases: Signature Tampering, Stock Out, Budget Breach, and API errors.
"""

import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_edge_case_tampered_signature_token():
    payload = {
        "user_query": "Looking for a 65W GaN fast charger under ₹2,500.",
        "max_budget_inr": 2500.0,
        "tamper_token": True
    }
    response = client.post("/api/commerce/run-flow", json=payload)
    assert response.status_code == 400
    assert "AP2 Mandate Bounding Rule Failure" in response.json()["detail"]

def test_edge_case_budget_breach():
    payload = {
        "user_query": "I need a 65W GaN fast charger under ₹50.",
        "max_budget_inr": 50.0
    }
    response = client.post("/api/commerce/run-flow", json=payload)
    assert response.status_code == 404
