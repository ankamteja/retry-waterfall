# Retry Waterfall, end to end

The single walkthrough: what this system is, how a payment moves through it,
where the AI is and is not, and how the numbers were arrived at. Written for
someone who has never seen the repository.

The other documents go deeper on one thing each. [`EXPLAINER.md`](EXPLAINER.md)
teaches the domain from zero. [`ARCHITECTURE.md`](ARCHITECTURE.md) explains why
the seams are where they are. [`razorpay_integration.md`](razorpay_integration.md)
separates what was verified against Razorpay's docs from what is illustrative.
[`PITCH.md`](PITCH.md) is the spoken script.

---

## 1. What this is

An agent that recovers failed recurring payments in India without breaking the
rules that govern them.

You subscribe to something. You do not type your card in every month; you sign
a mandate once, and after that the merchant asks the payment rails to pull the
money. Sometimes the pull fails. No balance that morning, the bank did not
answer, the card expired last week.

The customer has not cancelled. They still want the service. That revenue was
lost to plumbing rather than to a decision, and recovering it is close to pure
margin, which is why every payments company tries. The industry calls it
dunning.

What makes India different is that you cannot simply try harder. NPCI caps UPI
AutoPay at four attempts per billing cycle and fixes when they may happen. The
RBI requires the customer to re-authenticate above a threshold, so a silent
background retry on a large payment cannot legally clear at all. Every attempt
is both regulated and scarce.

Every public write-up on AI dunning is Stripe, Recurly or Chargebee: US, card,
SaaS. None of them encode an attempt cap, because none applies to them.

### The design decision everything rests on

> **Regulation decides when to retry. The AI only decides how to reach the
> customer.**

Timing is not a choice anyone gets to make, so this system does not make it.
What is left is genuinely a decision and nobody is currently making it: given
four scarce attempts, which payments are worth touching at all, and how do you
reach the customer before each one.

---

## 2. The rules that decide everything

| Rule | Value | What it forces |
|---|---|---|
| NPCI attempt cap | 4 per cycle | One original execution plus three retries. A fifth attempt is not aggressive dunning, it is non-compliant. |
| Retry windows | T+24h, T+72h, T+7d | Fixed. Timing is therefore never a model output. |
| RBI re-authentication | above Rs 15,000 | A silent auto-debit retry cannot clear. Route to an authenticated link instead. |
| Enhanced limit | above Rs 1,00,000 | For mutual funds, insurance and credit-card bills. Same payment, different ceiling. |

These live in `domain_rules.py`, deliberately separate from
`synthetic_data.py`, which holds the modelling assumptions. Keeping them apart
is not tidiness. If they were mixed, no reader could tell which claims are
checkable and which were invented, and the evaluation would stop being
falsifiable.

**Independent corroboration.** Razorpay's own sample webhook payloads show
`auth_attempts: 1` on a pending subscription and `auth_attempts: 4` on a halted
one. That ceiling of four is the same number arrived at here from the NPCI
regulation rather than from Razorpay. Two independent routes to the same
constant is the strongest signal the domain model is right.

---

## 3. How a payment moves

Two entry points, one pipeline. A live Razorpay webhook and a generated test
event become the same object, so the policy that runs in the evaluation is the
policy that would run in production. There is no separate demo path.

```
                                    3 above Rs 15,000
                                  +----------------------+
                                  |                      v
                                  |            +--------------------+
                                  |            | Authenticated link |
                                  |            | budget left intact |
                                  |            +--------------------+
   60          60                 |
 +--------+  +----------+  +--------------+  37   +----------------+
 | Ingest |->| Classify |->| Compliance   |------>| Channel policy |
 +--------+  +----------+  | gate         |       | SMS or IVR     |
                           +--------------+       +----------------+
                                  |                  |          |
                                  |                  | 28       | 9
                                  v                  v          v
                        +------------------+   +-----------+  +-----------+
                        | Refused, 20 hard |   | Recovered |  | Exhausted |
                        +------------------+   +-----------+  +-----------+

 Retry timing is never an output: T+24h, T+72h and T+7d are fixed by NPCI
 for every policy alike. Only the channel is chosen.
```

Volumes are one audited batch of 60 failed payments, seed 0.

The gate runs before the policy is ever called, which is what bounds the
problem:

- **Hard declines never reach the learned component.** A revoked mandate or a
  closed account is permanent for this cycle; retrying is both pointless and
  non-compliant.
- **Payments above the authentication threshold never reach it either.** They
  are escalated to an authenticated payment link, which leaves the mandate's
  attempt budget untouched.
- Only what is left is something the agent gets an opinion about.

---

## 4. Follow three payments

The same engine, three amounts. This is the fastest way to see the regulation
doing the deciding.

**Rs 4,200, subscription, insufficient funds.** Classified soft: the account
did not have the money that morning, and that can change. The gate clears it,
below the authentication limit and inside the attempt cap. For each permitted
window the bandit samples its belief about each channel, converts that to
expected rupees net of cost, and takes the best. Recovered on a permitted
attempt.

**Rs 20,000, subscription, insufficient funds.** The gate escalates instead. A
silent retry cannot legally clear above Rs 15,000 however good the policy is,
so the payment is routed out of the retry path to an authenticated link and
the policy never runs.

**Rs 20,000, mutual fund, insufficient funds.** Clears. Identical amount,
different category: the enhanced limit for mutual funds is Rs 1,00,000.

Three runs, one changed field at a time, and the behaviour changes for a
reason you can point at in a published circular.

---

## 5. Where the AI actually is

In exactly two places, and one of them is not allowed to make decisions.

### A contextual bandit picks the channel

Thompson Sampling over Beta posteriors, one arm per `(decline reason, retry
window, channel)`. At decision time it samples a success probability for each
channel, turns that into expected rupees net of what the channel costs, and
takes the winner. An SMS costs Rs 0.15; an IVR call costs Rs 8.00 and converts
better.

Sampling rather than reading a table is the point: a thinly-evidenced arm
still samples wide, so the agent keeps trying a channel until it has reason
not to. The same payment run twice can legitimately pick differently.

**What it is not allowed to decide.** Not timing, which NPCI fixes. Not
eligibility, which the compliance gate settles before the policy is called.
Not how many attempts, because the loop that calls it is bounded by the
mandated windows. The surface area of "what could the AI get wrong" is one
decision wide, and that is deliberate.

### A language model writes the explanation

It is handed a decision that has **already been made** and asked to phrase it.
It never chooses, and its output is never parsed for a number. Every figure in
every explanation comes from the audit trail. Offline the explanations are
pre-generated and committed, so the repository ships no API key.

### Every decision ends in a concrete action

`recovery_actions.py` turns each terminal branch into the exact outbound call
it implies: `send_sms`, `place_ivr_call`, `create_auth_payment_link` (a real
Razorpay `POST /v1/payment_links`), `suppress_no_retry`,
`escalate_to_manual_review`.

It is **dry run only**: `_dispatch` raises `NotImplementedError`, nothing in
the repository transmits, so it ships no credentials and every request body
stays reviewable as data. It also re-derives authorisation from `domain_rules`
on every emit rather than trusting the caller, raising `ComplianceViolation`
instead of producing an action the rails forbid.

---

## 6. Does it work

The trap most entries fall into is beating a naive baseline inside your own
simulator and declaring victory. That is circular: the bandit's posteriors are
estimating the exact table the simulator samples from, so it must win
eventually.

So there is a third policy with perfect information. Not learnable, not
deployable. It is the ceiling, and performance is reported as a fraction of
the lift that is actually achievable.

60 events per batch, 200 independent seeds.

| Policy | Recovery | Net recovered | Cost per win |
|---|---|---|---|
| Baseline, retry everything by SMS | 37.16% | Rs 261,648 | Rs 1.11 |
| **This agent** | **39.11%** | **Rs 273,846** | Rs 15.14 |
| Oracle, perfect information | 40.78% | Rs 285,529 | Rs 25.95 |

- Captures **51.1% of the achievable lift**.
- Paired lift **+Rs 12,198 per batch**, 95% CI **+6,330 to +18,065**.
- Wins **126 of 200 batches**. It loses 74, and the interface says so.

### Three mechanisms make the number honest

- **An oracle ceiling**, so the claim is a fraction of what is achievable
  rather than a bare win.
- **200 seeds**, because at n=60 across 18 arm cells a single run is mostly
  luck.
- **Common random numbers.** Whether an attempt succeeds is a hash of
  `(seed, payment, window, channel)`, never of which policy is asking. When two
  policies make the same choice they get the same outcome, so any difference is
  attributable to the decisions themselves. That is what makes the paired
  confidence interval legitimate.

### Read the ceiling correctly

39.11% sounds low until you notice the denominator. Of 60 failed payments, 20
are hard declines that are unrecoverable by law and 3 are escalated off the
retry path, so the structural maximum is **61.7%**, not 100%. The agent is
scored including payments it is legally forbidden to pursue.

---

## 7. Real versus simulated

| Component | Status |
|---|---|
| NPCI cap, retry windows, RBI thresholds | **Sourced regulation**, verifiable against published circulars. |
| Razorpay webhook shapes, lifecycle, error taxonomy | **Verified** against Razorpay's public docs. 18 tests against real payload shapes. |
| Recovery probabilities | **Assumptions.** Real Razorpay outcome data is not public. Labelled as assumptions in their own file. |
| The 51.1% of oracle | **Measured inside that simulator.** If real channel differences are smaller, there is less to capture. |
| The adapter | **Tested, not deployed.** Never run against a live Razorpay test-mode account. |
| The outbound executor | **Builds the exact call and does not send it.** No code path in the repository transmits. |
| Payment-link conversion | **Not modelled.** Escalated payments count as non-recoveries, which is conservative. |

The RBI constants in `domain_rules.py` -- the AFA thresholds and the 24-hour
pre-debit notification -- come from the **RBI Digital Payments -- E-mandate Framework, 2026** (notified 21 April 2026), which consolidates the earlier e-mandate circulars into one set of
directions. The NPCI 4-attempt cap is an NPCI operating rule and no circular
number is cited for it, which makes it the weakest-sourced constant in the
file. NPCI and RBI update these periodically, so re-verify before any
production use.

---

## 8. The code

Eighteen Python modules, standard library only, nothing to install.

| File | Responsibility |
|---|---|
| `domain_rules.py` | The regulation. Decline taxonomy, attempt cap, retry windows, authentication thresholds. Sourced facts. |
| `synthetic_data.py` | Batch generator and the recovery-probability model. Labelled assumptions. |
| `policies.py` | Baseline, bandit, oracle. The only learned component. |
| `recovery_actions.py` | The outbound edge. Five action kinds, independent guards, dry run only. |
| `audit_log.py` | Append-only JSONL, one line per decision. |
| `eval_harness.py` | All three policies over 200 seeds with common random numbers. |
| `pipeline_stats.py` | Rolls the trail into per-stage breakdowns; raises if the cap is ever exceeded. |
| `razorpay_adapter.py` | Real Razorpay webhooks into the engine's own types. |
| `explain_exceptions.py` | Offline LLM explanations, committed so the page needs no key. |
| `dashboard_live.py` | The engine ported to the browser, constants generated from the Python. |
| `dashboard_race.py` | Head-to-head race against blind retry, same payments and same luck. |
| `dashboard_console.py` | The operator console shell: control rail, tabbed stage, log strip. |
| `generate_dashboard.py` | Builds the self-contained HTML. |
| `server.py` | Optional. Live model output and real webhook ingestion over SSE. |

### The seam worth understanding

The browser runs the real policy, not a mock. Duplicated logic drifts, so the
duplication is confined to control flow: every threshold, cost, probability and
decline weight is read out of the Python at build time and emitted as JSON.
Change the authentication threshold in `domain_rules.py` and the browser
changes with the next build. There is no magic number in that JavaScript that
also exists in Python.

---

## 9. Run and deploy it

```bash
cd src
python3 eval_harness.py          # ~2 min, 200 seeds
python3 pipeline_stats.py
python3 generate_dashboard.py    # builds data/dashboard.html

python3 test_razorpay_adapter.py # 18 tests, no network
python3 test_recovery_actions.py # 36 tests, no network
```

Open `data/dashboard.html` in any browser. Fonts, Three.js and the force-graph
library are inlined, so it works with no network at all, which matters when the
pitch is recorded on unknown wifi and judges may open the repo offline.

For the live version, a model writing each explanation as it happens and real
Razorpay webhook bodies arriving over SSE:

```bash
python3 src/server.py            # PORT=8935 to move it
```

The page detects which mode it is in and says so rather than faking it.

**Deployed.** The static console builds on Vercel via `scripts/build-static.sh`,
which runs the whole evaluation at deploy time so every number on the live page
is computed from the committed code rather than served from an artifact that
can drift. The build refuses to publish a page that is implausibly small or
missing its own panes.
