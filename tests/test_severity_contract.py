"""Regression: the audit API must not mix severity encodings.

The frontend (`frontend/src/types.ts`) declares::

    export type Severity = "INFO" | "WARNING" | "ERROR" | "CRITICAL";

and groups findings by that string. `summary.by_severity` already uses those
string keys, but `findings[].severity` used to serialise the underlying
``IntEnum`` to a bare integer — so the audit panel rendered the severity counts
while every finding list stayed empty (and severity charts read zero).

These tests pin both representations to the same vocabulary so the two halves of
one response cannot drift apart again.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models.audit import AuditFinding, Severity

SEVERITY_NAMES = {"INFO", "WARNING", "ERROR", "CRITICAL"}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def _audited_batch(client: TestClient) -> dict:
    """Load the synthetic demo batch and return its audit payload."""
    loaded = client.post("/api/demo/load", json={"count": 30})
    assert loaded.status_code == 200, loaded.text
    payload = loaded.json()
    batch_id = payload.get("batch_id") or payload.get("id")
    assert batch_id, f"no batch id in {payload}"

    audit = client.get(f"/api/batches/{batch_id}/audit")
    assert audit.status_code == 200, audit.text
    return audit.json()


class TestSeveritySerialisation:
    def test_finding_severity_is_a_name_not_an_integer(self) -> None:
        """A serialised finding carries the enum NAME, never its ordinal."""
        finding = AuditFinding(rule_id="r", rule_name="R", severity=Severity.ERROR, message="m")
        dumped = finding.model_dump(mode="json")

        assert isinstance(dumped["severity"], str), (
            f"severity serialised as {type(dumped['severity']).__name__} "
            f"({dumped['severity']!r}); the frontend types it as a string union"
        )
        assert dumped["severity"] == "ERROR"

    def test_severity_keeps_its_ordering_semantics(self) -> None:
        """Fixing the wire format must not flatten the ordering the engine sorts by."""
        assert Severity.INFO < Severity.WARNING < Severity.ERROR < Severity.CRITICAL
        findings = [
            AuditFinding(rule_id="a", rule_name="A", severity=Severity.INFO, message="m"),
            AuditFinding(rule_id="b", rule_name="B", severity=Severity.CRITICAL, message="m"),
        ]
        findings.sort(key=lambda f: f.severity, reverse=True)
        assert findings[0].severity is Severity.CRITICAL

    def test_api_findings_and_summary_use_the_same_vocabulary(self, client: TestClient) -> None:
        """`findings[].severity` and `summary.by_severity` keys must match."""
        body = _audited_batch(client)
        findings = body["findings"]
        assert findings, "demo batch should produce audit findings"

        finding_values = {f["severity"] for f in findings}
        assert finding_values <= SEVERITY_NAMES, (
            f"findings carry severities outside the frontend vocabulary: {finding_values}"
        )

        summary_keys = set(body["summary"]["by_severity"])
        assert summary_keys <= SEVERITY_NAMES, summary_keys

        # Every severity that actually occurs must be expressible in the summary,
        # which is what makes the frontend's grouping lookup succeed.
        assert finding_values <= summary_keys, (
            f"findings use {finding_values} but the summary only knows {summary_keys}"
        )

    def test_every_severity_name_round_trips(self) -> None:
        """All four names survive serialisation (guards an over-narrow serializer)."""
        for severity in Severity:
            dumped = AuditFinding(
                rule_id="r", rule_name="R", severity=severity, message="m"
            ).model_dump(mode="json")
            assert dumped["severity"] == severity.name
