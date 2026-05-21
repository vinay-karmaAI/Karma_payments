import random
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Dict, Any

random.seed(42)   

MONTH_START = date(2024, 3, 1)
MONTH_END   = date(2024, 3, 31)
NEXT_MONTH  = date(2024, 4, 1)

MERCHANTS = [
    "Acme Corp", "Bright Spark Ltd", "Coral Reef Inc",
    "Delta Payments", "Echo Systems", "Falcon Goods",
    "Granite Co", "Harbor Bridge LLC",
]

PAYMENT_METHODS = ["CARD", "BANK_TRANSFER", "WALLET", "UPI"]
STATUSES        = ["SETTLED", "SETTLED", "SETTLED", "PENDING"]   


def _rand_amount(lo: float = 1.0, hi: float = 5000.0) -> Decimal:
    raw = random.uniform(lo, hi)
    if random.random() < 0.20:
        return Decimal(str(round(raw, 4)))
    return Decimal(str(round(raw, 2)))


def _business_days_later(d: date, days: int) -> date:
    current = d
    added   = 0
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5:   # Mon–Fri
            added += 1
    return current


def _rand_date_in_march() -> date:
    delta = (MONTH_END - MONTH_START).days
    return MONTH_START + timedelta(days=random.randint(0, delta))


def generate_data(n_transactions: int = 80) -> Dict[str, Any]:
    """
    Returns a dict with keys:
      platform_transactions : List[dict]
      bank_settlements      : List[dict]
      planted_gaps          : List[dict]   — ground-truth for test verification
    """

    platform_txns: List[dict] = []
    bank_records:  List[dict] = []
    planted_gaps:  List[dict] = []

    for _ in range(n_transactions):
        txn_id   = str(uuid.uuid4())
        txn_date = _rand_date_in_march()
        amount   = _rand_amount()
        merchant = random.choice(MERCHANTS)
        method   = random.choice(PAYMENT_METHODS)

        platform_txns.append({
            "txn_id":         txn_id,
            "merchant":       merchant,
            "amount":         str(amount),
            "currency":       "USD",
            "payment_method": method,
            "txn_date":       str(txn_date),
            "status":         "SETTLED",
            "type":           "CHARGE",
        })

        settle_days = random.choice([1, 2])
        settle_date = _business_days_later(txn_date, settle_days)

        # Keep in-month settlements only (gaps planted separately below)
        if settle_date <= MONTH_END:
            bank_records.append({
                "settlement_id":  str(uuid.uuid4()),
                "txn_id":         txn_id,
                "merchant":       merchant,
                "settled_amount": str(Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                "currency":       "USD",
                "settlement_date": str(settle_date),
                "batch_id":       f"BATCH-{settle_date.strftime('%Y%m%d')}",
            })

    gap1_txn_id = str(uuid.uuid4())
    gap1_amount = _rand_amount(200, 800)
    platform_txns.append({
        "txn_id":         gap1_txn_id,
        "merchant":       "Falcon Goods",
        "amount":         str(gap1_amount),
        "currency":       "USD",
        "payment_method": "CARD",
        "txn_date":       "2024-03-31",
        "status":         "SETTLED",
        "type":           "CHARGE",
        "_gap":           "NEXT_MONTH_SETTLEMENT",
    })
    bank_records.append({
        "settlement_id":  str(uuid.uuid4()),
        "txn_id":         gap1_txn_id,
        "merchant":       "Falcon Goods",
        "settled_amount": str(gap1_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "currency":       "USD",
        "settlement_date": "2024-04-01",   # ← next month
        "batch_id":       "BATCH-20240401",
    })
    planted_gaps.append({
        "gap_type":    "NEXT_MONTH_SETTLEMENT",
        "txn_id":      gap1_txn_id,
        "description": "Transaction on 2024-03-31 settled on 2024-04-01 (outside review window)",
        "amount":      str(gap1_amount),
    })

    rounding_txn_ids = []
    platform_sum     = Decimal("0")
    bank_sum         = Decimal("0")
    for i in range(5):
        txn_id      = str(uuid.uuid4())
        raw         = Decimal(str(round(random.uniform(100, 500), 4)))  # 4 dp
        rounded     = raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        txn_date    = _rand_date_in_march()
        settle_date = _business_days_later(txn_date, 1)
        if settle_date > MONTH_END:
            settle_date = MONTH_END

        platform_txns.append({
            "txn_id":         txn_id,
            "merchant":       "Delta Payments",
            "amount":         str(raw),
            "currency":       "USD",
            "payment_method": "BANK_TRANSFER",
            "txn_date":       str(txn_date),
            "status":         "SETTLED",
            "type":           "CHARGE",
            "_gap":           "ROUNDING_SOURCE",
        })
        bank_records.append({
            "settlement_id":  str(uuid.uuid4()),
            "txn_id":         txn_id,
            "merchant":       "Delta Payments",
            "settled_amount": str(rounded),
            "currency":       "USD",
            "settlement_date": str(settle_date),
            "batch_id":       f"BATCH-{settle_date.strftime('%Y%m%d')}",
        })
        rounding_txn_ids.append(txn_id)
        platform_sum += raw
        bank_sum     += rounded

    planted_gaps.append({
        "gap_type":    "ROUNDING_DIFFERENCE",
        "txn_ids":     rounding_txn_ids,
        "description": (
            f"5 sub-cent transactions; platform sum={platform_sum}, "
            f"bank sum={bank_sum}, delta={platform_sum - bank_sum}"
        ),
        "delta":       str(platform_sum - bank_sum),
    })

    # ─────────────────────────────────────────────────────────────────────────
    # GAP 3 — Duplicate entry on the PLATFORM side
    # One legitimate transaction is posted twice with the same txn_id.
    # ─────────────────────────────────────────────────────────────────────────
    dup_source       = random.choice(platform_txns[:20])
    dup_txn_id       = dup_source["txn_id"]
    duplicate_entry  = dict(dup_source)
    duplicate_entry["_gap"] = "DUPLICATE_PLATFORM"
    platform_txns.append(duplicate_entry)

    planted_gaps.append({
        "gap_type":    "DUPLICATE_ENTRY",
        "txn_id":      dup_txn_id,
        "description": "Same txn_id appears twice on the platform ledger; bank settled once",
        "amount":      dup_source["amount"],
    })

    # ─────────────────────────────────────────────────────────────────────────
    # GAP 4 — Refund with no matching original transaction
    # A refund record references a txn_id that doesn't exist on the platform.
    # ─────────────────────────────────────────────────────────────────────────
    orphan_refund_id = str(uuid.uuid4())
    orphan_orig_id   = str(uuid.uuid4())   # ← this ID never appears in platform_txns
    orphan_amount    = _rand_amount(50, 300)
    refund_date      = _rand_date_in_march()
    settle_date      = _business_days_later(refund_date, 1)
    if settle_date > MONTH_END:
        settle_date = MONTH_END

    platform_txns.append({
        "txn_id":           orphan_refund_id,
        "merchant":         "Acme Corp",
        "amount":           str(-orphan_amount),     # negative = refund
        "currency":         "USD",
        "payment_method":   "CARD",
        "txn_date":         str(refund_date),
        "status":           "SETTLED",
        "type":             "REFUND",
        "original_txn_id":  orphan_orig_id,          # ← dangling reference
        "_gap":             "ORPHAN_REFUND",
    })
    bank_records.append({
        "settlement_id":  str(uuid.uuid4()),
        "txn_id":         orphan_refund_id,
        "merchant":       "Acme Corp",
        "settled_amount": str((-orphan_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "currency":       "USD",
        "settlement_date": str(settle_date),
        "batch_id":       f"BATCH-{settle_date.strftime('%Y%m%d')}",
    })
    planted_gaps.append({
        "gap_type":      "ORPHAN_REFUND",
        "txn_id":        orphan_refund_id,
        "original_txn_id": orphan_orig_id,
        "description":   "Refund references an original txn_id that has no platform record",
        "amount":        str(-orphan_amount),
    })

    # ── Strip internal _gap markers from the output datasets ─────────────────
    def _clean(records):
        return [{k: v for k, v in r.items() if k != "_gap"} for r in records]

    return {
        "platform_transactions": _clean(platform_txns),
        "bank_settlements":      _clean(bank_records),
        "planted_gaps":          planted_gaps,          
        "meta": {
            "review_month":       "2024-03",
            "total_platform_txns": len(platform_txns),
            "total_bank_records":  len(bank_records),
            "generated_at":       datetime.now(timezone.utc).isoformat(),
        },
    }
