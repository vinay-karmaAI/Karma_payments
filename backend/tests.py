"""
tests.py — Pytest test suite for the reconciliation engine
──────────────────────────────────────────────────────────
Run with: pytest tests.py -v
"""

import pytest
from decimal import Decimal
from data_generator import generate_data
from reconciliation_engine import reconcile


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def sample_data():
    return generate_data(80)


@pytest.fixture(scope="module")
def recon_result(sample_data):
    return reconcile(
        sample_data["platform_transactions"],
        sample_data["bank_settlements"],
    )


# ── Data generator tests ──────────────────────────────────────────────────────

class TestDataGenerator:
    def test_generates_platform_transactions(self, sample_data):
        assert len(sample_data["platform_transactions"]) > 0

    def test_generates_bank_settlements(self, sample_data):
        assert len(sample_data["bank_settlements"]) > 0

    def test_planted_gaps_count(self, sample_data):
        assert len(sample_data["planted_gaps"]) >= 4

    def test_next_month_settlement_planted(self, sample_data):
        types = [g["gap_type"] for g in sample_data["planted_gaps"]]
        assert "NEXT_MONTH_SETTLEMENT" in types

    def test_rounding_difference_planted(self, sample_data):
        types = [g["gap_type"] for g in sample_data["planted_gaps"]]
        assert "ROUNDING_DIFFERENCE" in types

    def test_duplicate_entry_planted(self, sample_data):
        types = [g["gap_type"] for g in sample_data["planted_gaps"]]
        assert "DUPLICATE_ENTRY" in types

    def test_orphan_refund_planted(self, sample_data):
        types = [g["gap_type"] for g in sample_data["planted_gaps"]]
        assert "ORPHAN_REFUND" in types

    def test_meta_fields_present(self, sample_data):
        meta = sample_data["meta"]
        assert "review_month" in meta
        assert "generated_at" in meta


# ── Reconciliation engine tests ───────────────────────────────────────────────

class TestReconciliationEngine:
    def test_returns_required_keys(self, recon_result):
        assert "summary" in recon_result
        assert "gaps" in recon_result
        assert "stats" in recon_result

    def test_summary_keys(self, recon_result):
        s = recon_result["summary"]
        for key in ["total_platform_txns", "total_bank_records", "total_gaps",
                    "platform_total_usd", "bank_total_usd", "total_variance_usd"]:
            assert key in s, f"Missing summary key: {key}"

    def test_detects_gaps(self, recon_result):
        assert recon_result["summary"]["total_gaps"] > 0

    def test_detects_duplicate(self, recon_result):
        types = [g["gap_type"] for g in recon_result["gaps"]]
        assert "DUPLICATE_PLATFORM_TXN" in types, "Duplicate not detected"

    def test_detects_orphan_refund(self, recon_result):
        types = [g["gap_type"] for g in recon_result["gaps"]]
        assert "ORPHAN_REFUND" in types, "Orphan refund not detected"

    def test_detects_next_month_settlement(self, recon_result):
        types = [g["gap_type"] for g in recon_result["gaps"]]
        assert "NEXT_MONTH_SETTLEMENT" in types, "Next-month settlement not detected"

    def test_detects_rounding(self, recon_result):
        types = [g["gap_type"] for g in recon_result["gaps"]]
        assert "AMOUNT_MISMATCH" in types or "ROUNDING_BATCH_DELTA" in types, \
            "Rounding difference not detected"

    def test_gaps_have_required_fields(self, recon_result):
        for gap in recon_result["gaps"]:
            assert "gap_id"   in gap
            assert "gap_type" in gap
            assert "severity" in gap
            assert "title"    in gap

    def test_severity_values_valid(self, recon_result):
        valid = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
        for gap in recon_result["gaps"]:
            assert gap["severity"] in valid

    def test_gaps_sorted_by_severity(self, recon_result):
        order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        severities = [order[g["severity"]] for g in recon_result["gaps"]]
        assert severities == sorted(severities), "Gaps not sorted by severity"

    def test_platform_total_is_positive(self, recon_result):
        val = Decimal(recon_result["summary"]["platform_total_usd"])
        assert val >= 0

    def test_reconciliation_rate_format(self, recon_result):
        rate = recon_result["summary"]["reconciliation_rate"]
        assert rate.endswith("%")


# ── Edge-case tests ───────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_inputs(self):
        result = reconcile([], [])
        assert result["summary"]["total_gaps"] == 0

    def test_perfect_match(self):
        txns = [{"txn_id": "T1", "merchant": "X", "amount": "100.00",
                 "txn_date": "2024-03-15", "type": "CHARGE", "status": "SETTLED"}]
        bank = [{"txn_id": "T1", "merchant": "X", "settled_amount": "100.00",
                 "settlement_date": "2024-03-16", "batch_id": "B1",
                 "settlement_id": "S1"}]
        result = reconcile(txns, bank)
        assert result["summary"]["matched_txns"] == 1
        mismatch_gaps = [g for g in result["gaps"] if g["gap_type"] == "AMOUNT_MISMATCH"]
        assert len(mismatch_gaps) == 0

    def test_detects_unmatched_platform_txn(self):
        txns = [{"txn_id": "GHOST", "merchant": "X", "amount": "50.00",
                 "txn_date": "2024-03-10", "type": "CHARGE", "status": "SETTLED"}]
        result = reconcile(txns, [])
        types = [g["gap_type"] for g in result["gaps"]]
        assert "UNMATCHED_PLATFORM_TXN" in types

    def test_detects_significant_amount_mismatch(self):
        txns = [{"txn_id": "T2", "merchant": "X", "amount": "500.00",
                 "txn_date": "2024-03-10", "type": "CHARGE", "status": "SETTLED"}]
        bank = [{"txn_id": "T2", "merchant": "X", "settled_amount": "400.00",
                 "settlement_date": "2024-03-11", "batch_id": "B2",
                 "settlement_id": "S2"}]
        result = reconcile(txns, bank)
        critical = [g for g in result["gaps"]
                    if g["gap_type"] == "AMOUNT_MISMATCH" and g["severity"] == "CRITICAL"]
        assert len(critical) == 1

    def test_out_of_month_bank_record_ignored(self):
        txns = [{"txn_id": "T3", "merchant": "X", "amount": "200.00",
                 "txn_date": "2024-02-28", "type": "CHARGE", "status": "SETTLED"}]
        bank = [{"txn_id": "T3", "merchant": "X", "settled_amount": "200.00",
                 "settlement_date": "2024-02-29", "batch_id": "B3",
                 "settlement_id": "S3"}]
        result = reconcile(txns, bank)
        # Feb txn should not appear in March totals
        assert Decimal(result["summary"]["platform_total_usd"]) == Decimal("0")
