"""
Eval harness: baseline vs bandit vs oracle, on the same synthetic batches.

Design notes (why it's built this way, not simpler):
- Oracle arm: the bandit's own posteriors target the same probability
  table the simulator samples from, so "bandit beats baseline" is true by
  construction and proves nothing about the bandit's learning. Oracle
  (perfect information) gives a ceiling; reporting "bandit captures N% of
  oracle's lift over baseline" is the honest claim.
- Multi-seed: at n=60 the arm space (3 soft decline codes x 3 retry
  windows x 2 channels = 18 cells) gets only a handful of pulls per cell.
  A single run's result can land either direction by luck. We run N_SEEDS
  independent batches and report mean +/- stdev, not one number.
- Learning-curve run: a single larger batch (LEARNING_CURVE_N) shows the
  bandit's net recovery converging toward oracle as pulls-per-cell grows,
  separate from the n=60 headline (which mirrors the track's "50+ record"
  batch requirement).
"""

import hashlib
import json
import os
import statistics

from domain_rules import RETRY_WINDOWS_HOURS, is_hard_decline, requires_additional_factor_auth
from synthetic_data import generate_batch, recovery_probability
from policies import BaselinePolicy, BanditPolicy, OraclePolicy, CHANNEL_COST_INR
from audit_log import AuditLog, AuditRecord

N_SEEDS = 200
HEADLINE_N = 60
LEARNING_CURVE_N = 800
LEARNING_CURVE_CHECKPOINTS = [50, 100, 200, 400, 800]

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def outcome_draw(seed: int, payment_id: str, window_hours: int, channel: str) -> float:
    """Common random numbers: the uniform draw deciding whether an attempt
    succeeds depends only on (world, payment, window, channel) -- never on
    which policy is asking, or on how many draws that policy has already
    made.

    Without this each policy walks its own RNG stream, so baseline and
    bandit face different luck on the same payment and the comparison
    carries avoidable variance. Coupling the worlds means that when two
    policies make the same choice they get the same outcome, and any
    difference in results is attributable to the decisions themselves.
    """
    h = hashlib.sha256(f"{seed}|{payment_id}|{window_hours}|{channel}".encode()).digest()
    return int.from_bytes(h[:8], "big") / 2**64


def run_policy_on_batch(policy, events, seed, audit_log: AuditLog | None = None) -> dict:
    total_gross = 0.0
    total_cost = 0.0
    recovered_count = 0
    exceptions = []  # payment_ids that exhausted retries unresolved
    hard_declines = 0
    afa_escalations = 0

    for event in events:
        if is_hard_decline(event.decline_code):
            hard_declines += 1
            if audit_log:
                audit_log.write(AuditRecord(
                    payment_id=event.payment_id, policy=policy.name,
                    decline_code=event.decline_code.value, attempt_number=1,
                    window_hours=None, channel=None, amount_inr=event.amount_inr,
                    expected_net_inr=None, rationale="hard decline: not retryable, compliance no-retry",
                    recovered=None, stopping_rule="hard_decline_no_retry",
                ))
            continue

        # RBI additional-factor-auth. Above the threshold the customer must
        # re-authenticate, so a silent background retry cannot legally
        # succeed however good the policy is. These are routed to an
        # authenticated payment link instead of consuming a mandate attempt.
        if requires_additional_factor_auth(event.amount_inr, event.category):
            afa_escalations += 1
            if audit_log:
                audit_log.write(AuditRecord(
                    payment_id=event.payment_id, policy=policy.name,
                    decline_code=event.decline_code.value, attempt_number=1,
                    window_hours=None, channel=None, amount_inr=event.amount_inr,
                    expected_net_inr=None,
                    rationale=(f"amount exceeds RBI AFA threshold for category "
                               f"'{event.category}': customer re-authentication required, "
                               f"silent auto-debit retry not permitted"),
                    recovered=None, stopping_rule="afa_required_escalate_to_auth_link",
                ))
            continue

        recovered = False
        for i, window_hours in enumerate(RETRY_WINDOWS_HOURS):
            attempt_number = i + 2  # attempt 1 = the original failed execution
            decision = policy.choose(event.decline_code, window_hours, event.amount_inr)
            is_last_window = (i == len(RETRY_WINDOWS_HOURS) - 1)

            if not decision.should_attempt:
                if audit_log:
                    audit_log.write(AuditRecord(
                        payment_id=event.payment_id, policy=policy.name,
                        decline_code=event.decline_code.value, attempt_number=attempt_number,
                        window_hours=window_hours, channel=None, amount_inr=event.amount_inr,
                        expected_net_inr=decision.expected_net_inr, rationale=decision.rationale,
                        recovered=None,
                        stopping_rule="npci_retry_cap_exhausted" if is_last_window else None,
                    ))
                continue

            true_p = recovery_probability(event.decline_code, window_hours, decision.channel)
            success = outcome_draw(seed, event.payment_id, window_hours, decision.channel) < true_p
            policy.update(event.decline_code, window_hours, decision.channel, success)
            total_cost += CHANNEL_COST_INR[decision.channel]

            if audit_log:
                audit_log.write(AuditRecord(
                    payment_id=event.payment_id, policy=policy.name,
                    decline_code=event.decline_code.value, attempt_number=attempt_number,
                    window_hours=window_hours, channel=decision.channel, amount_inr=event.amount_inr,
                    expected_net_inr=decision.expected_net_inr, rationale=decision.rationale,
                    recovered=success,
                    stopping_rule="npci_retry_cap_exhausted" if (is_last_window and not success) else None,
                ))

            if success:
                recovered = True
                total_gross += event.amount_inr
                recovered_count += 1
                break

        if not recovered:
            exceptions.append(event.payment_id)

    return {
        "policy": policy.name,
        "n_events": len(events),
        "hard_declines": hard_declines,
        "afa_escalations": afa_escalations,
        "recovered_count": recovered_count,
        "recovery_rate": recovered_count / len(events) if events else 0.0,
        "gross_recovered_inr": round(total_gross, 2),
        "total_channel_cost_inr": round(total_cost, 2),
        "net_recovered_inr": round(total_gross - total_cost, 2),
        "cost_per_recovery_inr": round(total_cost / recovered_count, 2) if recovered_count else None,
        "exceptions": exceptions,
    }


def run_one_seed(n: int, seed: int, audit_dir: str | None = None) -> dict:
    events = generate_batch(n=n, seed=seed)
    results = {}
    for policy_cls in (BaselinePolicy, BanditPolicy, OraclePolicy):
        policy = policy_cls(seed=seed) if policy_cls is BanditPolicy else policy_cls()
        audit_log = None
        if audit_dir:
            os.makedirs(audit_dir, exist_ok=True)
            audit_log = AuditLog(os.path.join(audit_dir, f"audit_{policy.name}_seed{seed}.jsonl"))
        try:
            results[policy.name] = run_policy_on_batch(policy, events, seed, audit_log)
        finally:
            if audit_log:
                audit_log.close()
    return results


def summarize(all_seed_results: list[dict], policy_name: str, field: str) -> tuple[float, float]:
    """Rounds to 4dp, not 2: recovery_rate is a 0-1 fraction printed as a
    percentage, so 2dp would quantize every mean and stdev to a whole
    percent and make the +/- values look fabricated."""
    values = [r[policy_name][field] for r in all_seed_results]
    mean = statistics.mean(values)
    stdev = statistics.stdev(values) if len(values) > 1 else 0.0
    return round(mean, 4), round(stdev, 4)


def print_headline_report(all_seed_results: list[dict]) -> None:
    print(f"\n=== Headline: n={HEADLINE_N} events, {N_SEEDS} seeds (mean +/- stdev) ===")
    print(f"{'policy':<10} {'recovery_rate':<16} {'net_recovered_inr':<20} {'cost_per_recovery_inr'}")
    for policy_name in ("baseline", "bandit", "oracle"):
        rate_m, rate_s = summarize(all_seed_results, policy_name, "recovery_rate")
        net_m, net_s = summarize(all_seed_results, policy_name, "net_recovered_inr")
        cprs = [r[policy_name]["cost_per_recovery_inr"] for r in all_seed_results if r[policy_name]["cost_per_recovery_inr"] is not None]
        cpr_m = round(statistics.mean(cprs), 2) if cprs else None
        print(f"{policy_name:<10} {rate_m:.2%} +/- {rate_s:.2%}   {net_m:>10.2f} +/- {net_s:<8.2f} {cpr_m}")

    baseline_net, _ = summarize(all_seed_results, "baseline", "net_recovered_inr")
    bandit_net, _ = summarize(all_seed_results, "bandit", "net_recovered_inr")
    oracle_net, _ = summarize(all_seed_results, "oracle", "net_recovered_inr")
    achievable_lift = oracle_net - baseline_net
    captured = (bandit_net - baseline_net) / achievable_lift * 100 if achievable_lift else None
    print(f"\nBandit captures {captured:.1f}% of achievable lift over baseline (oracle = 100%)" if captured is not None
          else "\nNo achievable lift over baseline at this batch size.")

    # Paired test. Outcomes are coupled across policies (see outcome_draw),
    # so per-seed differences are genuinely paired and the standard error of
    # the mean difference is the right uncertainty on the lift -- far tighter
    # than comparing two independent means.
    diffs = [r["bandit"]["net_recovered_inr"] - r["baseline"]["net_recovered_inr"]
             for r in all_seed_results]
    n = len(diffs)
    mean_d = statistics.mean(diffs)
    se_d = statistics.stdev(diffs) / (n ** 0.5)
    wins = sum(1 for d in diffs if d > 0)
    print(f"Paired lift over baseline: {mean_d:+,.0f} INR/batch "
          f"(95% CI {mean_d - 1.96*se_d:+,.0f} to {mean_d + 1.96*se_d:+,.0f}, n={n} seeds)")
    print(f"Bandit beat baseline in {wins}/{n} batches ({wins/n:.0%})")


def run_learning_curve(seeds: list[int] = (0, 1, 2)) -> dict:
    """Same policies, larger batch, checkpointed net recovery -- shows the
    bandit converging toward oracle as pulls-per-arm grows."""
    curve = {name: {cp: [] for cp in LEARNING_CURVE_CHECKPOINTS} for name in ("baseline", "bandit", "oracle")}

    for seed in seeds:
        events = generate_batch(n=LEARNING_CURVE_N, seed=seed)
        for policy_cls in (BaselinePolicy, BanditPolicy, OraclePolicy):
            policy = policy_cls(seed=seed) if policy_cls is BanditPolicy else policy_cls()
            cumulative_net = 0.0
            checkpoint_idx = 0
            for i, event in enumerate(events, start=1):
                result = run_policy_on_batch(policy, [event], seed)
                cumulative_net += result["net_recovered_inr"]
                while checkpoint_idx < len(LEARNING_CURVE_CHECKPOINTS) and i == LEARNING_CURVE_CHECKPOINTS[checkpoint_idx]:
                    curve[policy.name][LEARNING_CURVE_CHECKPOINTS[checkpoint_idx]].append(cumulative_net)
                    checkpoint_idx += 1

    averaged = {
        name: {cp: round(statistics.mean(vals), 2) for cp, vals in cps.items()}
        for name, cps in curve.items()
    }
    return averaged


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    all_seed_results = [run_one_seed(HEADLINE_N, seed) for seed in range(N_SEEDS)]
    print_headline_report(all_seed_results)

    # Representative audit trail for the demo UI (task #5): one seed, all
    # three policies, full per-decision JSONL.
    run_one_seed(HEADLINE_N, seed=0, audit_dir=DATA_DIR)

    with open(os.path.join(DATA_DIR, "headline_summary.json"), "w") as f:
        json.dump(all_seed_results, f, indent=2)

    print(f"\n=== Learning curve: n={LEARNING_CURVE_N} events, 3 seeds averaged ===")
    curve = run_learning_curve()
    print(f"{'events':<10}" + "".join(f"{name:<12}" for name in ("baseline", "bandit", "oracle")))
    for cp in LEARNING_CURVE_CHECKPOINTS:
        row = f"{cp:<10}"
        for name in ("baseline", "bandit", "oracle"):
            row += f"{curve[name][cp]:<12.2f}"
        print(row)

    with open(os.path.join(DATA_DIR, "learning_curve.json"), "w") as f:
        json.dump(curve, f, indent=2)

    print(f"\nAudit JSONL + summaries written to {os.path.abspath(DATA_DIR)}")


if __name__ == "__main__":
    main()
