import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.rules.engine import evaluate
from app.rules.schemas import RuleRequest

EXAMPLES = Path(__file__).resolve().parents[1] / "config" / "rule_examples"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("normal_session", []),
        ("money_before_id", ["R03"]),
        ("returned_after_counter", ["R04"]),
        ("second_customer", ["R02"]),
        ("cashier_left", ["R01"]),
    ],
)
def test_documented_examples(name, expected):
    report = evaluate(RuleRequest.model_validate_json((EXAMPLES / f"{name}.json").read_text()))
    assert [v.rule_id for v in report.violations] == expected
    assert report.verdict == ("violation" if expected else "no_violation")
    assert report.input_sources == ["synthetic_example"]


def test_rules_api_auth_validation_and_result(tmp_path):
    settings = Settings(_env_file=None, api_key="rules-test-key", storage_dir=tmp_path)
    payload = json.loads((EXAMPLES / "returned_after_counter.json").read_text())
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/v1/rules/evaluate", json=payload).status_code == 401
        headers = {"X-API-Key": "rules-test-key"}
        response = client.post("/api/v1/rules/evaluate", json=payload, headers=headers)
        assert response.status_code == 200
        assert response.json()["violations"][0]["rule_id"] == "R04"
        assert response.json()["input_sources"] == ["synthetic_example"]
        payload["events"][0]["sample_count"] = 1
        assert (
            client.post("/api/v1/rules/evaluate", json=payload, headers=headers).status_code == 422
        )
        schema = client.get("/openapi.json").json()
        assert "/api/v1/rules/evaluate" in schema["paths"]
