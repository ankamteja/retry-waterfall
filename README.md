# retry-waterfall

An AI revenue-recovery agent for failed Indian UPI AutoPay and e-mandate subscription debits. Built for the Razorpay AI Buildathon, Track 03.

**New here? Read [`docs/WALKTHROUGH.md`](docs/WALKTHROUGH.md)** -- the whole system end to end in one document: the regulation, how a payment moves through the pipeline, where the AI is and is not, and how the numbers were arrived at.

---

## The problem

When a recurring payment in India fails -- insufficient funds, a bank timeout, an issuer soft decline -- the customer still wants the service. The revenue is lost to plumbing, not intent. This is involuntary churn, and recovering it is nearly pure margin.

But recovery in India is not a free-for-all. NPCI caps UPI AutoPay at 4 total attempts per billing cycle (1 original + 3 retries). The retry windows are fixed: T+24h, T+72h, T+7d. RBI mandates additional factor authentication (AFA) above Rs 15,000. Every attempt is regulated and scarce.

Most public "AI dunning" systems (Stripe, Recurly, Chargebee) are US/SaaS/card-centric. None of them encode NPCI's attempt cap, because it does not apply to them. A generic clone will not do.

## What it does

Every failed payment flows through a five-stage pipeline:

```
ingest -> classify -> compliance gate -> channel policy -> outcome
```

1. **Ingest.** Receive the batch of failed payment events.
2. **Classify.** Split each failure into soft decline (insufficient funds, bank timeout, issuer soft decline) or hard decline (card expired, mandate revoked, account closed, issuer hard decline). Hard declines are permanent for this cycle; retrying them is pointless and non-compliant.
3. **Compliance gate.** Refuse all hard declines -- they never reach the policy. Enforce the NPCI 4-attempt cap. Flag payments above the RBI AFA threshold for escalation to an authenticated payment link instead of a silent auto-debit retry.
4. **Channel policy.** A Thompson Sampling contextual bandit picks the contact channel -- SMS or IVR -- for each scheduled retry, conditioned on (decline reason, retry window). It may skip an attempt entirely if the expected net value is negative for all channels. See the design decision below.
5. **Outcome.** The payment is either recovered or retries are exhausted and it is logged for manual follow-up.

Every terminal branch then produces a concrete outbound action (`src/recovery_actions.py`), so the loop ends in something that would actually reach a customer rather than in a decision nobody acts on. Five kinds: `send_sms`, `place_ivr_call`, `create_auth_payment_link` (a real Razorpay `POST /v1/payment_links`), `suppress_no_retry`, `escalate_to_manual_review`.

Two properties of that edge matter more than the calls themselves:

- **It is dry run only. No live send path exists in the repository.** `RecoveryExecutor._dispatch` raises `NotImplementedError`. Nothing transmits, which is why the repo ships no credentials and why every request body stays reviewable as data.
- **It re-derives its own authorisation rather than trusting the caller.** Every emit re-checks the NPCI and RBI rules from `domain_rules` and raises `ComplianceViolation` instead of producing an action. The policy layer has already refused these cases, so the guard should never fire -- that is the point. It is a second, independent gate on the only step in the system that can touch a customer. See `docs/razorpay_integration.md` for which payloads are verified and which are illustrative.
- **The attempt cap is counted, not asserted.** A `ContactLedger` records the attempts actually contacted per payment and hands them out through an atomic reservation, so the NPCI cap binds even when the caller is wrong about which attempt it is on. A refused or failed contact releases its reservation, and the server holds one long-lived ledger, so replaying the same webhook is refused instead of re-sent.
- **The customer is notified before the debit.** The framework requires a pre-transaction notification at least 24 hours ahead of a collection. The executor emits it as an action and refuses to contact on an attempt with no notice on record. It hangs off the attempt rather than the window, so declining to attempt stays free; it does not count against the NPCI cap, because a legal obligation should not eat a legal retry; and it is deliberately not modelled as changing recovery probabilities, since this project has never measured a world with one and inventing those numbers would make everything downstream of them fiction.

## The key design decision

**The AI never chooses retry timing.** Timing is fixed by NPCI regulation: T+24h, T+72h, T+7d, 4 attempts maximum. Both the baseline and the bandit follow that identical schedule.

The learned component chooses only the contact channel (SMS vs IVR call) and decides whether the attempt is worth making at all, net of cost. SMS costs Rs 0.15; an IVR call costs Rs 8.00.

The comparison that decides it is not the call against the payment -- it is the call against the *extra* recovery it buys over an SMS: Rs 7.85 more, for a few points more probability. That crossover is computed from the same tables the simulator draws from and sits between **Rs 89 and Rs 349** depending on the decline reason and the window (the full table is in `docs/EXPLAINER.md`). Amounts here are drawn uniform(Rs 200, Rs 25,000), so most payments clear most crossovers and the bandit upgrades to a call on the large majority of decisions. The arithmetic is genuinely performed rather than decorative, but in this environment it works as a guard on the small-payment tail, not as a channel the agent often switches away from.

This is the strongest compliance story in the project. "Our AI decides when to retry" invites the question of what happens when it decides wrong. "Regulation decides when; the AI only decides how to reach you" does not.

## How it maps onto Razorpay's real API

The adapter (`src/razorpay_adapter.py`, 18 passing tests) consumes three Razorpay subscription webhooks:

- **`subscription.pending`** -- a charge failed; the recovery window opens.
- **`subscription.halted`** -- retries exhausted; escalate to a human.
- **`subscription.charged`** -- recovered; close the case.

Razorpay's own sample webhook payloads show `auth_attempts: 1` on a pending subscription and `auth_attempts: 4` on a halted one. That ceiling of 4 is the same number as the NPCI AutoPay cap encoded in `domain_rules.MAX_ATTEMPTS_PER_CYCLE`. The agent's stopping rule and Razorpay's own lifecycle agree on the number independently.

The adapter maps Razorpay's `error.reason` field to the engine's decline taxonomy, converts paise to rupees, computes remaining attempts, and refuses to add retries on top of a cycle Razorpay has already exhausted. Unrecognised error reasons fail closed to a hard decline -- refusing to retry an unknown failure is the safe default, because a wrong soft classification spends a regulated attempt that cannot be reclaimed.

## Evaluation methodology

Most hackathon entries run a bandit against a naive baseline in their own simulator and declare victory. That is circular: the bandit's posteriors target the same probability table the simulator samples from, so convergence is guaranteed by construction.

This project fixes that with three mechanisms:

**Oracle ceiling.** A third policy reads the true recovery probabilities directly -- perfect information, not learnable, not deployable. It is the upper bound. Performance is reported as a fraction of the achievable lift, not as a bare win/loss.

**200 seeds.** At n=60 with 18 possible (decline code x retry window x channel) combinations, each cell gets only a handful of pulls. A single run's result is mostly luck. The eval runs 200 independent batches and reports the mean.

**Common random numbers.** The uniform draw deciding whether an attempt succeeds is a deterministic function of (seed, payment_id, window, channel) -- never of which policy is asking. When two policies make the same choice they get the same outcome, so any difference in results is attributable to decisions, not luck.

**Paired confidence interval.** Outcomes are coupled across policies per seed, so per-seed differences are genuinely paired. The standard error of the mean difference is the right uncertainty on the lift.

### Results (60 events per batch, 200 seeds)

| Policy | Recovery rate | Net recovered (Rs) |
|---|---|---|
| Baseline (always SMS) | 37.16% | 261,648 |
| Bandit (this project) | 39.11% | 273,846 |
| Oracle (perfect information) | 40.78% | 285,529 |

The bandit captures **51.1%** of the achievable lift over baseline (oracle = 100%).

Paired lift over baseline: **+Rs 12,198/batch** (95% CI +6,330 to +18,065). The bandit beats the baseline in **126 out of 200 batches**.

## Quickstart

```bash
cd src

python3 eval_harness.py          # run the full eval (~1 min), writes data/
python3 pipeline_stats.py        # roll up the audit trail into stage breakdowns
python3 explain_exceptions.py    # LLM explanations (or --offline for templates)
python3 generate_dashboard.py    # build data/dashboard.html

python3 test_razorpay_adapter.py # 18 tests, no network needed
python3 test_recovery_actions.py # 36 tests, no network needed
```

Then open `data/dashboard.html` in any browser. It is fully self-contained -- fonts, Three.js, and the force-graph library are vendored and inlined. It works with no network.

For the live version -- a model writing each explanation as it happens, and real Razorpay webhook bodies arriving over SSE -- run the optional server instead:

```bash
python3 src/server.py            # http://localhost:8934, or PORT=8935 to move it
```

The page detects which mode it is in and says so. Neither mode is required by the other.

`explain_exceptions.py` is the only script that needs a network (when generating, not when viewing). Its output is committed, so the dashboard never calls a model at runtime. The public repo ships no API key.

## File map

| File | Purpose |
|---|---|
| `src/domain_rules.py` | The regulation. Decline taxonomy (soft/hard), NPCI 4-attempt cap, retry windows, RBI AFA thresholds. Sourced facts with citations, not assumptions. |
| `src/synthetic_data.py` | Generates test batches and models recovery probabilities. Clearly labelled assumptions, not sourced. |
| `src/policies.py` | Three policies: baseline (always SMS), bandit (Thompson Sampling, cost-aware), oracle (perfect information). Also holds the channel costs and the mandated notice cost. |
| `src/eval_harness.py` | Runs all three policies over 200 seeds, reports paired results. |
| `src/audit_log.py` | Append-only JSONL, one line per decision. |
| `src/pipeline_stats.py` | Rolls audit trail into per-stage, per-channel, per-window breakdowns. |
| `src/explain_exceptions.py` | Uses an LLM to write plain-English explanations of decisions. LLM writes language only; every number comes from the audit trail. |
| `src/razorpay_adapter.py` | Maps real Razorpay webhook payloads into the engine's domain objects. |
| `src/recovery_actions.py` | The outbound edge. Turns each terminal branch into the concrete call it implies, re-derives compliance authorisation itself, holds the `ContactLedger` that makes the attempt cap binding, sends the mandated pre-debit notice, and never sends anything for real. |
| `src/test_razorpay_adapter.py` | 18 tests proving the adapter handles real payload shapes, including the `"error": null` Razorpay actually sends. |
| `src/test_recovery_actions.py` | 36 tests, most of them asserting the executor refuses calls the rails forbid. |
| `src/generate_dashboard.py` | Builds the self-contained HTML dashboard. |
| `src/dashboard_live.py` | The engine ported to the browser, with every threshold and cost generated from the Python so the two cannot drift. |
| `src/dashboard_race.py` | The head-to-head race: blind retry against the agent, same payments, same luck. |
| `src/dashboard_console.py` | The operator console shell -- control rail, tabbed stage, shared log strip. |
| `src/server.py` | Optional. Serves the page, proxies a model, ingests real webhooks, streams decisions over SSE. |
| `src/vendor_fonts.py` | Downloads and inlines fonts for offline operation. |
| `vendor/` | Vendored JS dependencies (Three.js, 3d-force-graph, countUp). |
| `docs/WALKTHROUGH.md` | The whole system end to end, in one document. Start here. |
| `docs/CONSOLE.md` | The operator console: what each pane shows and how to drive the demo. |
| `docs/REFERENCE.md` | Data formats, the HTTP surface, and the decline taxonomy. |
| `docs/EXPLAINER.md` | Full technical explainer: the problem, the architecture, the eval, the bugs. |
| `docs/ARCHITECTURE.md` | How the pieces fit and why the seams are where they are. |
| `docs/razorpay_integration.md` | What was verified against Razorpay's public docs, and what is illustrative. |

## Limitations

Say these before a judge finds them.

1. **The simulator is assumptions, not measured data.** The recovery probabilities in `synthetic_data.py` are plausible, internally consistent, and clearly labelled -- but they are not real Razorpay outcome data, because that is not public. The structure (soft vs hard, the NPCI cap, the windows) is sourced; the probabilities are not.

2. **51.1% of oracle is measured inside that simulator.** If the real world's channel-effectiveness differences are smaller, there is less for the bandit to capture.

3. **Two channels only.** Deliberately trimmed to fit the sample budget at n=60. More channels need more traffic before the bandit learns anything useful.

4. **The adapter is tested, not deployed.** It handles real payload shapes verified from the docs, with 15 passing tests, but it has not run against a live Razorpay test-mode account.

5. **The outbound executor builds the exact call and does not send it.** There is no code path in the repository that transmits. Adding a sending subclass is the deployment step, and the only place a credential would ever appear.

6. **The LLM writes language only.** It never makes a decision. Every number in its explanations comes from the audit trail. This is a design choice, but it means the "AI" in the demo is mostly the bandit, not the language model.

7. **The regulatory constants are sourced, but one is weaker than the others.** The RBI rules -- the AFA thresholds and the 24-hour pre-debit notification -- come from the Digital Payments E-mandate Framework, 2026, notified 21 April 2026, which consolidates the earlier circulars. The NPCI 4-attempt cap is an NPCI operating rule and no circular number is cited for it, so treat it as the weakest-sourced constant here. NPCI and RBI update these periodically, so re-verify before any production use.

## License

MIT. See [LICENSE](LICENSE).
