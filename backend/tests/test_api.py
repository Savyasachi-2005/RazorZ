from fastapi.testclient import TestClient

from app.main import app
from app.services.reconciliation_service import run_reconciliation, review_exception


client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] in {"ok", "degraded"}


def test_reconciliation_run_endpoint():
    payload = {
        "records": [
            {
                "source": "synthetic",
                "record_type": "order",
                "record_id": "ORD-100",
                "reference": "ORD-100",
                "amount": "125.50",
                "date": "2026-01-05",
                "customer": "CUST-27",
            },
            {
                "source": "synthetic",
                "record_type": "payment",
                "record_id": "PMT-100",
                "reference": "ORD-100",
                "amount": "125.50",
                "date": "2026-01-05",
                "customer": "CUST-27",
            },
        ]
    }

    response = client.post("/reconciliation/run", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["matched"] >= 1
    assert data["results"][0]["status"] == "MATCHED"

    summary = client.get("/reconciliation/summary")
    assert summary.status_code == 200
    assert summary.json()["total_records"] >= 1

    records = client.get("/reconciliation/records")
    assert records.status_code == 200
    assert records.json()["items"]

    audit = client.get("/audit")
    assert audit.status_code == 200
    assert audit.json()["items"]


def test_exception_review_endpoints():
    run_reconciliation(
        [
            {
                "source": "synthetic",
                "record_type": "order",
                "record_id": "ORD-404",
                "reference": "ORD-404",
                "amount": "90.00",
                "date": "2026-04-01",
                "customer": "CUST-9",
            }
        ]
    )
    listed = client.get("/exceptions")
    assert listed.status_code == 200
    exception_id = listed.json()["items"][0]["id"]

    resolved = client.post(
        f"/exceptions/{exception_id}/resolve",
        json={"actor": "finance-ops", "note": "Confirmed missing capture in source."},
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "RESOLVED"
    assert resolved.json()["certainty"] in {"CONFIRMED", "PROBABLE", "UNKNOWN"}
