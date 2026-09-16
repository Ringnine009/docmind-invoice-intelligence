"""Rule-level isolation: one broken rule must not take the audit down with it.

Before this, an exception raised inside a single rule propagated out of
``AuditEngine.run`` and discarded *every* finding from *every* rule — and, at
the HTTP layer, failed the whole batch. A typo in one rule therefore silently
removed the fraud signal produced by the other seven, which is strictly worse
than the rule not existing: the batch still reported ``status=done`` with an
empty findings list.

Stub rules here use an empty ``rule_id`` at class level so the base class's
auto-registration hook leaves the global registry alone (`AuditRule.
__init_subclass__` only registers subclasses with a non-empty ``rule_id``).
"""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models.audit import Severity
from app.services.audit.base import AuditRule
from app.services.audit.engine import AuditEngine
from app.services.audit.rules import ArithmeticTotalRule, DuplicateInvoiceNumberRule
from app.services.extraction.mock_extractor import MockExtractor

from conftest import make_invoice


def stub_rule(rule_id: str, *, raises: bool = False, severity=Severity.WARNING) -> AuditRule:
    """A rule that either finds one thing per invoice or blows up."""
    # Bound outside the class body: assigning `rule_id` inside it would shadow
    # the closure variable, so `name` must not read `rule_id` from there.
    label = f"stub {rule_id}"

    class _Stub(AuditRule):
        rule_id = ""  # empty → not auto-registered into the global registry
        name = label
        description = "test stub"
        default_severity = severity

        def evaluate(self, batch):
            if raises:
                raise RuntimeError(f"{self.rule_id} exploded")
            return [self.finding(f"stub finding from {self.rule_id}")] if batch else []

    rule = _Stub()
    rule.rule_id = rule_id  # instance-level id, read by the engine
    return rule


def duplicate_batch():
    return [
        make_invoice(number="11111111111111111111"),
        make_invoice(number="11111111111111111111"),
    ]


class TestEngineIsolation:
    def test_other_rules_still_produce_their_findings(self):
        engine = AuditEngine(
            rules=[stub_rule("exploding", raises=True), DuplicateInvoiceNumberRule()]
        )
        findings = engine.run(duplicate_batch())
        assert [f.rule_id for f in findings] == ["dup_invoice_number"]
        assert findings[0].severity == Severity.CRITICAL

    def test_run_does_not_raise(self):
        engine = AuditEngine(rules=[stub_rule("exploding", raises=True)])
        assert engine.run(duplicate_batch()) == []

    def test_error_is_recorded_with_the_rule_name(self):
        engine = AuditEngine(rules=[stub_rule("exploding", raises=True)])
        engine.run(duplicate_batch())
        assert len(engine.errors) == 1
        error = engine.errors[0]
        assert error.rule_id == "exploding"
        assert error.rule_name == "stub exploding"
        assert "RuntimeError" in error.error
        assert "exploded" in error.error
        # and it survives the trip to JSON (the API serialises it this way)
        assert error.to_dict() == {
            "rule_id": "exploding",
            "rule_name": "stub exploding",
            "error": "RuntimeError: exploding exploded",
        }

    def test_findings_from_the_remaining_rules_are_ordered_by_severity(self):
        engine = AuditEngine(
            rules=[
                stub_rule("exploding", raises=True),
                DuplicateInvoiceNumberRule(),
                stub_rule("noisy", severity=Severity.INFO),
            ]
        )
        severities = [f.severity for f in engine.run(duplicate_batch())]
        assert severities == [Severity.CRITICAL, Severity.INFO]

    def test_every_rule_failing_still_returns_cleanly(self):
        engine = AuditEngine(rules=[stub_rule("boom_a", raises=True),
                                    stub_rule("boom_b", raises=True)])
        assert engine.run(duplicate_batch()) == []
        assert {e.rule_id for e in engine.errors} == {"boom_a", "boom_b"}

    def test_a_real_registered_rule_can_be_isolated(self, monkeypatch):
        def boom(self, batch):
            raise ValueError("bad numeric conversion")

        monkeypatch.setattr(DuplicateInvoiceNumberRule, "evaluate", boom)
        engine = AuditEngine(
            rules=[DuplicateInvoiceNumberRule(), stub_rule("survivor")]
        )
        findings = engine.run(duplicate_batch())
        assert [f.rule_id for f in findings] == ["survivor"]
        assert engine.errors[0].rule_id == "dup_invoice_number"
        assert "ValueError" in engine.errors[0].error

    def test_errors_do_not_accumulate_across_runs(self):
        engine = AuditEngine(rules=[stub_rule("exploding", raises=True)])
        engine.run(duplicate_batch())
        engine.run(duplicate_batch())
        assert len(engine.errors) == 1

    def test_healthy_engine_reports_no_errors(self):
        engine = AuditEngine(rules=[DuplicateInvoiceNumberRule()])
        engine.run(duplicate_batch())
        assert engine.errors == []

    def test_disabled_rule_is_not_run_and_not_reported_as_broken(self):
        engine = AuditEngine(
            rules=[stub_rule("exploding", raises=True)],
            disabled_rules={"exploding"},
        )
        assert engine.run(duplicate_batch()) == []
        assert engine.errors == []


class TestBrokenRuleSurfacesThroughTheApi:
    @pytest.fixture()
    def broken_client(self, monkeypatch):
        def boom(self, batch):
            raise RuntimeError("simulated rule crash")

        monkeypatch.setattr(ArithmeticTotalRule, "evaluate", boom)
        app = create_app(extractor=MockExtractor())
        with TestClient(app) as client:
            yield client

    def test_batch_still_completes_with_the_other_findings(self, broken_client):
        r = broken_client.post("/api/demo/load", json={"count": 30})
        assert r.status_code == 200
        batch = broken_client.get(f"/api/batches/{r.json()['batch_id']}").json()
        assert batch["status"] == "done"
        by_rule = {f["rule_id"] for f in batch["findings"]}
        assert "arithmetic_total" not in by_rule
        # the other six rules still did their job on the same batch
        assert {"dup_invoice_number", "tax_rate", "party_info",
                "invoice_date", "qr_crosscheck"} <= by_rule

    def test_batch_exposes_the_rule_error(self, broken_client):
        r = broken_client.post("/api/demo/load", json={"count": 30})
        batch = broken_client.get(f"/api/batches/{r.json()['batch_id']}").json()
        assert len(batch["rule_errors"]) == 1
        error = batch["rule_errors"][0]
        assert error["rule_id"] == "arithmetic_total"
        assert error["rule_name"] == "Total arithmetic consistency"
        assert "simulated rule crash" in error["error"]

    def test_audit_endpoint_reports_the_rule_error_and_is_inconclusive(self, broken_client):
        r = broken_client.post("/api/demo/load", json={"count": 30})
        audit = broken_client.get(
            f"/api/batches/{r.json()['batch_id']}/audit"
        ).json()
        assert audit["rule_errors"][0]["rule_id"] == "arithmetic_total"
        assert audit["audit_conclusive"] is False
        assert audit["findings"], "the surviving findings must still be returned"

    def test_healthy_batch_has_no_rule_errors(self):
        app = create_app(extractor=MockExtractor())
        with TestClient(app) as client:
            r = client.post("/api/demo/load", json={"count": 30})
            audit = client.get(f"/api/batches/{r.json()['batch_id']}/audit").json()
        assert audit["rule_errors"] == []
        assert audit["audit_conclusive"] is True
