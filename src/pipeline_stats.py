"""
Rolls the audit JSONL up into the shape the dashboard needs: per-stage
volumes for the pipeline digital twin, plus the per-decline-code and
per-channel breakdowns the flat summary was hiding.

Pipeline stages (these are the dashboard's nodes):
  ingest -> classify -> compliance_gate -> policy -> outcome
A stage node goes red when it rejects/refuses traffic, which is the
compliance story: the gate lights up on every hard decline.
"""

import json
import os
from collections import defaultdict

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def build(policy: str = "bandit", seed: int = 0) -> dict:
    path = os.path.join(DATA_DIR, f"audit_{policy}_seed{seed}.jsonl")
    records = [json.loads(line) for line in open(path)]

    # A payment can never exceed the regulatory cap. If it does, the audit
    # file has stacked multiple runs (append-mode bug) and every downstream
    # number is wrong -- fail loudly rather than publish it.
    from domain_rules import MAX_ATTEMPTS_PER_CYCLE
    worst = max((sum(1 for r in records if r["payment_id"] == p and r["channel"])
                 for p in {r["payment_id"] for r in records}), default=0)
    if worst > MAX_ATTEMPTS_PER_CYCLE - 1:
        raise ValueError(
            f"{path}: a payment shows {worst} retries, exceeding the NPCI cap of "
            f"{MAX_ATTEMPTS_PER_CYCLE - 1}. Regenerate with eval_harness.py."
        )

    payments = {r["payment_id"] for r in records}
    hard_ids = {r["payment_id"] for r in records if r["stopping_rule"] == "hard_decline_no_retry"}
    attempted = [r for r in records if r["channel"] is not None]
    recovered_ids = {r["payment_id"] for r in records if r["recovered"]}

    afa_ids = {r["payment_id"] for r in records
               if r["stopping_rule"] == "afa_required_escalate_to_auth_link"}
    soft_ids = payments - hard_ids - afa_ids

    # A policy-stage skip is the policy declining a window it was allowed to
    # use. Defining it as "no channel, and not a hard decline" also swept up
    # the AFA escalations, which never reach the policy at all -- so they were
    # reported twice, once as gate escalations and again as policy skips, and
    # the stage showed 3 skips where the policy had made none.
    skipped = [r for r in records
               if r["channel"] is None and r["payment_id"] in soft_ids]
    exhausted_ids = soft_ids - recovered_ids

    by_code = defaultdict(lambda: {"total": 0, "recovered": 0, "gross_inr": 0.0})
    for pid in payments:
        rec = next(r for r in records if r["payment_id"] == pid)
        entry = by_code[rec["decline_code"]]
        entry["total"] += 1
        if pid in recovered_ids:
            entry["recovered"] += 1
            win = next(r for r in records if r["payment_id"] == pid and r["recovered"])
            entry["gross_inr"] += win["amount_inr"]

    by_channel = defaultdict(lambda: {"attempts": 0, "wins": 0, "cost_inr": 0.0, "gross_inr": 0.0})
    from policies import CHANNEL_COST_INR
    for r in attempted:
        entry = by_channel[r["channel"]]
        entry["attempts"] += 1
        entry["cost_inr"] += CHANNEL_COST_INR[r["channel"]]
        if r["recovered"]:
            entry["wins"] += 1
            entry["gross_inr"] += r["amount_inr"]

    by_window = defaultdict(lambda: {"attempts": 0, "wins": 0})
    for r in attempted:
        entry = by_window[r["window_hours"]]
        entry["attempts"] += 1
        if r["recovered"]:
            entry["wins"] += 1

    gross = sum(r["amount_inr"] for r in records if r["recovered"])
    cost = sum(CHANNEL_COST_INR[r["channel"]] for r in attempted)
    at_risk = sum(next(r for r in records if r["payment_id"] == pid)["amount_inr"] for pid in payments)

    return {
        "stages": {
            "ingest": {"in": len(payments), "out": len(payments), "rejected": 0},
            "classify": {"in": len(payments), "soft": len(soft_ids) + len(afa_ids),
                         "hard": len(hard_ids)},
            "compliance_gate": {"in": len(payments), "passed": len(soft_ids),
                                "refused": len(hard_ids), "afa_escalated": len(afa_ids)},
            "policy": {"in": len(soft_ids), "attempts": len(attempted), "skipped": len(skipped)},
            "outcome": {"recovered": len(recovered_ids), "exhausted": len(exhausted_ids)},
        },
        "money": {
            "at_risk_inr": round(at_risk, 2),
            "gross_recovered_inr": round(gross, 2),
            "channel_cost_inr": round(cost, 2),
            "net_recovered_inr": round(gross - cost, 2),
        },
        "by_decline_code": {k: dict(v) for k, v in by_code.items()},
        "by_channel": {k: dict(v) for k, v in by_channel.items()},
        "by_window": {str(k): dict(v) for k, v in by_window.items()},
        "exceptions": sorted(exhausted_ids),
        "hard_declines": sorted(hard_ids),
    }


def main():
    stats = build()
    out = os.path.join(DATA_DIR, "pipeline_stats.json")
    with open(out, "w") as f:
        json.dump(stats, f, indent=2)
    print(f"Wrote {os.path.abspath(out)}")
    s = stats["stages"]
    print(f"  ingest {s['ingest']['in']} -> soft {s['classify']['soft']} / hard {s['classify']['hard']} "
          f"-> attempts {s['policy']['attempts']} -> recovered {s['outcome']['recovered']}")


if __name__ == "__main__":
    main()
