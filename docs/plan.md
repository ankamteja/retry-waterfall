# Razorpay AI Buildathon — plan

## The event
Razorpay AI Buildathon. Student-only hiring pipeline for AI Builder Intern
(₹75,000/month, 6 or 12 months, Bangalore in-person). Applications close
**2026-09-05**. No resume screen, no aptitude test — pick a track, build a
working thing, submit public GitHub repo + 5-minute pitch video +
architecture writeup, get a callback if it's good.

"Winning" here = getting a callback/interview, not beating other entrants
on a public scoreboard. There is no published data on applicant counts or
per-track competitiveness — don't treat any "track X is less crowded"
claim as fact.

## The five tracks
1. **AI Growth & Agentic Commerce** — grow merchant revenue / make merchant
   transactable by an AI buyer, on Razorpay test-mode APIs.
2. **AI Risk Manager** — fraud/return/chargeback detector, measured
   precision/recall on held-out data, strictly defense-only (offense-capable
   = disqualified).
3. **AI Revenue Recovery** — detect at-risk revenue, diagnose cause, execute
   bounded recovery workflow (payment failures, checkout abandonment,
   overdue receivables). Bar: measured money recovered, compliant
   escalation, stopping rules, audit trail.
4. **AI Finance Controller** — close a finance-ops loop across 50+ record
   synthetic batch, report match rate and unresolved exceptions.
5. **Open Track** — anything, same bar: real problem, working product, real
   AI use, demonstrated value.

## Track picked: 03 — AI Revenue Recovery
Not picked for "lower competition" (no data supports that claim). Picked
because it's the most demoable given solo build time before 2026-09-05:
- Bar is one clean number (money recovered on a batch) — easy to make
  legible in a 5-minute pitch.
- Direct tie to Razorpay's core payments business — relevance to the panel
  is obvious without extra framing.
- Scope is bounded enough to actually finish end-to-end alone (detect →
  diagnose → bounded action → audit trail).

Runner-up considered: Track 02 (AI Risk Manager) — best overlap with
existing CTF/netsec background, but Track 03 stays the pick for
demo-strength and deadline fit.

## Exact scope chosen
Failed recurring-payment (subscription/mandate) recovery agent:
1. Take a failed payment event.
2. Classify decline reason as soft (temporary, worth retrying — low
   balance, bank timeout) or hard (permanent, retry pointless — expired
   card, cancelled mandate).
3. Pick retry timing + contact channel per decline type, using a
   contextual bandit (an algorithm that tries different actions, tracks
   which ones actually recover money, and shifts toward what works — like
   adaptive A/B testing) instead of one fixed rule.
4. Respect NPCI's real mandate retry cap as a hard stopping rule.
5. Log every decision in a structured, replayable audit trail.

## Real rules being encoded (the actual differentiator)
Most public "AI dunning bot" writeups (Stripe/Recurly/Chargebee) are
US/SaaS-card-centric. This build encodes India-specific payment-rail rules
instead, checked 2026-08-22:

- **NPCI AutoPay/e-mandate retry cap**: max 4 total attempts per billing
  cycle (1 original execution + 3 retries), effective Aug 2025.
- **Retry windows**: T+24h, T+72h, T+7d, then the cycle is marked failed —
  no further retries, no penalty from bank/NPCI.
- **RBI additional-factor-auth (AFA) trigger**: mandatory above ₹15,000 for
  general recurring payments; ₹1,00,000 enhanced limit for mutual funds,
  insurance, and credit-card bill payments.
- **Pre-debit notification**: RBI's recurring-payment framework requires
  the customer be notified in advance of an auto-debit.

These numbers live in `src/domain_rules.py` in this repo, with sources
noted in the file header.

## Why this beats a generic LLM-wrapper entry
The obvious failure mode everyone else will hit: prompt an LLM to "decide
when to retry," demo one happy-path win, ship no real evaluation. The
track's own bar text says "one cherry-picked match proves nothing" — so:

1. **Real policy underneath, not just prompting.** Contextual bandit picks
   retry timing/channel; LLM only handles language (customer message,
   exception explanation, pitch narration).
2. **India-specific domain accuracy** instead of a copied US SaaS-dunning
   playbook (see rules above).
3. **Rigorous eval harness**: synthetic batch with controllable failure-cause
   distribution, policy vs naive-blind-retry baseline, recovery-rate lift +
   cost-per-recovery (channel cost vs $ recovered, net not gross) + honest
   list of unresolved exceptions.
4. **Structured audit trail**: append-only log of decision, confidence,
   rationale, action, outcome, and stopping-rule trigger — not a chat
   transcript.
5. **Explainable diagnosis**: survival-analysis framing (hazard of the
   payment failing again) instead of a black-box classifier.

## Research list (do before/alongside building)
1. Payment decline reasons — soft vs hard, ISO 8583 response codes (~2-3h)
2. UPI AutoPay / e-mandate mechanics + NPCI retry rule (~2-3h)
3. RBI recurring-payment framework — AFA trigger, pre-debit notice (~1-2h)
4. Dunning — general vocabulary only, background not source of truth (~1h)
5. Contextual bandits — concept only (Thompson Sampling, multi-armed
   bandit intro), not the underlying math (~1-2h)
6. Chargebacks / receivables — definitions only (~30min)

## Build plan
1. `src/domain_rules.py` — decline code taxonomy + NPCI/RBI constants
   (done).
2. Synthetic data generator — 50+ fake failed-payment records, controllable
   distribution of decline causes.
3. Baseline policy — naive blind retry (same timing for everyone).
4. Bandit policy — the actual recovery logic, decline-code-aware.
5. Eval harness — policy vs baseline, recovery %, $ recovered, cost per
   recovery, exception list.
6. Audit log — structured JSONL, one line per decision.
7. Pitch assembly — 5-min video: problem → baseline vs policy number →
   one correctly-refused retry (compliance) → architecture diagram.

## Papers / references gathered
- Optimizing debt collections using constrained reinforcement learning —
  https://www.researchgate.net/publication/220272023_Optimizing_debt_collections_using_constrained_reinforcement_learning
- Flexible recommendation for debt collection via deep RL —
  https://www.sciencedirect.com/science/article/abs/pii/S0957417424018189
- When Less Is More? DRL-Based Optimization of Debt Collection (SSRN) —
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4488673
- Benchmarking LLM Agents in Wealth-Management Workflows (arXiv:2512.02230) —
  https://arxiv.org/html/2512.02230
- Can LLM Agents Be CFOs? (arXiv:2603.23638) —
  https://arxiv.org/html/2603.23638
- Customer Churn Prediction using Explainable ML (arXiv:2303.00960) —
  https://arxiv.org/abs/2303.00960
- Explainability/risk modeling for churn analytics (arXiv:2510.11604) —
  https://arxiv.org/html/2510.11604
- Stripe: How we built Smart Retries (industry prior art, baseline to beat) —
  https://stripe.com/blog/how-we-built-it-smart-retries
- GR4VY: Payment retry logic explained —
  https://gr4vy.com/posts/payment-retry-logic-explained-smart-retries-for-failed-transactions-in-2026/

## Open items
- Other hackathon (has a team, not yet detailed here) — timeline overlap
  with this one not yet mapped.
