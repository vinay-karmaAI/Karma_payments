from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List
import uuid
from datetime import date


ROUNDING_TOLERANCE = Decimal("0.05")   # per-txn tolerance (batch sum may differ)


def _to_decimal(val: Any) -> Decimal:
    try:
        return Decimal(str(val))
    except (InvalidOperation, TypeError):
        return Decimal("0")


def _parse_date(val: Any) -> date | None:
    if isinstance(val, date):
        return val
    try:
        return date.fromisoformat(str(val))
    except (ValueError, TypeError):
        return None


def reconcile(
    platform_txns: List[Dict],
    bank_settlements: List[Dict],
    review_year: int = 2024,
    review_month: int = 3,
) -> Dict[str, Any]:
    review_month_start = date(review_year, review_month, 1)
    if review_month == 12:
        review_month_end = date(review_year + 1, 1, 1)
    else:
        review_month_end = date(review_year, review_month + 1, 1)

    def in_review_month(d: date | None) -> bool:
        return d is not None and review_month_start <= d < review_month_end

    gaps: List[Dict] = []

    def add_gap(gap_type: str, severity: str, title: str, **detail):
        gaps.append({
            "gap_id":   str(uuid.uuid4()),
            "gap_type": gap_type,
            "severity": severity,
            "title":    title,
            **detail,
        })

    platform_by_id: Dict[str, List[Dict]] = {}
    for txn in platform_txns:
        tid = txn.get("txn_id", "")
        platform_by_id.setdefault(tid, []).append(txn)

    bank_by_txn_id: Dict[str, List[Dict]] = {}
    for rec in bank_settlements:
        tid = rec.get("txn_id", "")
        bank_by_txn_id.setdefault(tid, []).append(rec)

    all_ids = set(platform_by_id) | set(bank_by_txn_id)

    platform_total   = Decimal("0")
    bank_total       = Decimal("0")
    matched_count    = 0
    merchant_sums: Dict[str, Dict[str, Decimal]] = {}

    for tid, records in platform_by_id.items():
        if len(records) > 1:
            amounts = [_to_decimal(r["amount"]) for r in records]
            add_gap(
                "DUPLICATE_PLATFORM_TXN",
                "CRITICAL",
                f"Duplicate platform entry: {tid}",
                txn_id=tid,
                count=len(records),
                amounts=[str(a) for a in amounts],
                excess_amount=str(sum(amounts[1:])),
                merchant=records[0].get("merchant", "unknown"),
            )

    for tid in all_ids:
        p_records = platform_by_id.get(tid, [])
        b_records = bank_by_txn_id.get(tid, [])

        p_rec   = p_records[0] if p_records else None
        p_amount = _to_decimal(p_rec["amount"]) if p_rec else Decimal("0")
        p_date   = _parse_date(p_rec.get("txn_date")) if p_rec else None
        merchant = (p_rec or b_records[0]).get("merchant", "unknown")

        b_rec     = b_records[0] if b_records else None
        b_amount  = _to_decimal(b_rec["settled_amount"]) if b_rec else Decimal("0")
        b_date    = _parse_date(b_rec.get("settlement_date")) if b_rec else None

        if p_rec and in_review_month(p_date):
            platform_total += abs(p_amount)

        if p_rec and b_rec and p_date and b_date:
            if in_review_month(p_date) and not in_review_month(b_date):
                add_gap(
                    "NEXT_MONTH_SETTLEMENT",
                    "HIGH",
                    f"Settlement fell outside review window for {tid}",
                    txn_id=tid,
                    txn_date=str(p_date),
                    settlement_date=str(b_date),
                    amount=str(p_amount),
                    merchant=merchant,
                )
                continue

        if p_rec and not b_rec:
            if in_review_month(p_date):
                add_gap(
                    "UNMATCHED_PLATFORM_TXN",
                    "HIGH",
                    f"No bank settlement found for platform txn {tid}",
                    txn_id=tid,
                    txn_date=str(p_date),
                    amount=str(p_amount),
                    merchant=merchant,
                    txn_type=p_rec.get("type", "CHARGE"),
                )
            continue

        if b_rec and not p_rec:
            if in_review_month(b_date):
                add_gap(
                    "UNMATCHED_BANK_SETTLEMENT",
                    "HIGH",
                    f"No platform txn found for bank settlement {tid}",
                    txn_id=tid,
                    settlement_date=str(b_date),
                    settled_amount=str(b_amount),
                    merchant=merchant,
                )
                bank_total += abs(b_amount)
            continue

        if in_review_month(b_date):
            bank_total += abs(b_amount)

        delta = abs(p_amount) - abs(b_amount)

        if delta == Decimal("0"):
            matched_count += 1
        elif abs(delta) <= ROUNDING_TOLERANCE:
            m_entry = merchant_sums.setdefault(merchant, {"platform": Decimal("0"), "bank": Decimal("0")})
            m_entry["platform"] += abs(p_amount)
            m_entry["bank"]     += abs(b_amount)

            add_gap(
                "AMOUNT_MISMATCH",
                "MEDIUM",
                f"Amount mismatch (rounding) for {tid}",
                txn_id=tid,
                platform_amount=str(abs(p_amount)),
                bank_amount=str(abs(b_amount)),
                delta=str(delta),
                merchant=merchant,
            )
        else:
            add_gap(
                "AMOUNT_MISMATCH",
                "CRITICAL",
                f"Significant amount mismatch for {tid}",
                txn_id=tid,
                platform_amount=str(abs(p_amount)),
                bank_amount=str(abs(b_amount)),
                delta=str(delta),
                merchant=merchant,
            )

    for merch, sums in merchant_sums.items():
        batch_delta = sums["platform"] - sums["bank"]
        if abs(batch_delta) > Decimal("0"):
            add_gap(
                "ROUNDING_BATCH_DELTA",
                "MEDIUM",
                f"Cumulative rounding delta for merchant: {merch}",
                merchant=merch,
                platform_sum=str(sums["platform"]),
                bank_sum=str(sums["bank"]),
                batch_delta=str(batch_delta),
            )

    platform_txn_id_set = set(platform_by_id.keys())
    for txn in platform_txns:
        if txn.get("type") == "REFUND":
            orig_id = txn.get("original_txn_id")
            if orig_id and orig_id not in platform_txn_id_set:
                add_gap(
                    "ORPHAN_REFUND",
                    "CRITICAL",
                    f"Refund references missing original txn: {orig_id}",
                    refund_txn_id=txn["txn_id"],
                    original_txn_id=orig_id,
                    refund_amount=str(txn.get("amount", 0)),
                    merchant=txn.get("merchant", "unknown"),
                    refund_date=txn.get("txn_date", "unknown"),
                )

    gap_type_counts: Dict[str, int] = {}
    for g in gaps:
        gap_type_counts[g["gap_type"]] = gap_type_counts.get(g["gap_type"], 0) + 1

    total_variance = abs(platform_total - bank_total)

    summary = {
        "total_platform_txns":  len(platform_txns),
        "total_bank_records":   len(bank_settlements),
        "matched_txns":         matched_count,
        "total_gaps":           len(gaps),
        "platform_total_usd":   str(platform_total.quantize(Decimal("0.01"))),
        "bank_total_usd":       str(bank_total.quantize(Decimal("0.01"))),
        "total_variance_usd":   str(total_variance.quantize(Decimal("0.01"))),
        "reconciliation_rate":  (
            f"{matched_count / max(len(platform_txns), 1) * 100:.1f}%"
        ),
        "critical_gaps":        sum(1 for g in gaps if g["severity"] == "CRITICAL"),
        "high_gaps":            sum(1 for g in gaps if g["severity"] == "HIGH"),
        "medium_gaps":          sum(1 for g in gaps if g["severity"] == "MEDIUM"),
    }

    return {
        "summary": summary,
        "gaps":    sorted(gaps, key=lambda g: ["CRITICAL","HIGH","MEDIUM","LOW"].index(g["severity"])),
        "stats":   gap_type_counts,
    }
