# Architecture

How the pieces fit, and why the seams are where they are. For the domain
background start with `EXPLAINER.md`; this assumes it.

---

## The shape of it

```
  Razorpay webhook                    synthetic batch
        |                                    |
        v                                    v
  razorpay_adapter.py  ------------>  FailedPaymentEvent
        |                                    |
        |                                    v
        |                          +-------------------+
        |                          |  classify         |  domain_rules.py
        |                          |  soft vs hard     |
        |                          +-------------------+
        |                                    |
        |                          +-------------------+
        |                          |  compliance gate  |  domain_rules.py
        |                          |  hard: refuse     |
        |                          |  AFA: escalate    |
        |                          |  cap: stop at 4   |
        |                          +-------------------+
        |                                    |
        |                          +-------------------+
        |                          |  channel policy   |  policies.py
        |                          |  SMS / IVR / skip |
        |                          +-------------------+
        |                                    |
        v                                    v
  recovery_actions.py  <----------------------+
        |
        +--> RecoveryAction (dry run)      audit_log.py -> JSONL
```

Two entry points, one pipeline. A live webhook and a generated test event
become the same `FailedPaymentEvent`, so the policy that runs in the
evaluation is the policy that runs in production. There is no separate demo
path.

---

## Modules

| File | Responsibility |
|---|---|
| `domain_rules.py` | The regulation. Decline taxonomy, NPCI cap, retry windows, RBI AFA thresholds. Sourced facts. |
| `synthetic_data.py` | Batch generator and the recovery-probability model. Labelled assumptions. |
| `policies.py` | Baseline, bandit, oracle. The only learned component in the system. |
| `recovery_actions.py` | The outbound edge. Five action kinds, independent compliance guards, dry run only. |
| `audit_log.py` | Append-only JSONL, one line per decision. |
| `eval_harness.py` | Runs all three policies over many seeds with common random numbers. |
| `pipeline_stats.py` | Rolls the audit trail into per-stage breakdowns, and raises if the cap is ever exceeded. |
| `razorpay_adapter.py` | Real Razorpay webhooks into the engine's own types. |
| `explain_exceptions.py` | Offline LLM explanations, committed so the page needs no key. |
| `dashboard_live.py` | The engine, ported to the browser, with constants generated from the Python. |
| `generate_dashboard.py` | Builds the self-contained HTML. |
| `server.py` | Optional. Serves the page, proxies a model, receives webhooks, streams decisions. |

---

## The seams that matter

### Sourced regulation is a separate file from modelling assumptions

`domain_rules.py` contains numbers you can go and verify against NPCI and
RBI. `synthetic_data.py` contains numbers this project made up to run a
simulation with.

Keeping them apart is not tidiness, it is what makes the evaluation
falsifiable. If they were mixed, no reader could tell which claims are
checkable. Every file header says which kind it is.

### The learned component is deliberately small

`policies.py` chooses a channel. That is all it does.

It does not choose timing, because NPCI fixes timing. It does not choose
whether a payment is eligible, because the compliance gate decides that
before the policy is ever called. It cannot exceed the attempt cap, because
the loop that calls it is bounded by `RETRY_WINDOWS_HOURS`.

The surface area of "what could the AI get wrong" is one decision wide, and
that is the point.

### The outbound edge re-derives its own authorisation

`recovery_actions.py` could have trusted its caller. The policy layer has
already refused hard declines and escalated anything over the AFA threshold,
so the executor's guards should never fire.

They exist anyway. Every emit path re-checks the rules from `domain_rules`
and raises `ComplianceViolation` rather than producing an action. This is
the only step in the system that can touch a customer, so it is the one
place worth checking twice, and a future policy bug should fail loudly
instead of turning into an SMS.

A prior review found `requires_additional_factor_auth` defined and never
called anywhere. That is exactly the failure this seam is placed to catch.

### There is no live send

`RecoveryExecutor._dispatch` raises `NotImplementedError`. `DryRunExecutor`
records and returns. Nothing in the repository transmits.

That means the eval, the demo and the public repo all run with no
credentials and no side effects, and the request bodies stay reviewable as
data. Adding a sending subclass is the deployment step and the only place a
key would ever appear.

### The browser copy of the engine cannot drift from the Python

`dashboard_live.py` ports the gate and the sampling step to JavaScript so
the page can run the real policy client side. Duplicated logic drifts, so
the duplication is confined to control flow: every threshold, cost,
probability and decline weight is read out of `domain_rules`, `policies` and
`synthetic_data` at build time and emitted as JSON.

Change the AFA threshold in the Python and the browser changes with the next
build. There is no magic number in that JavaScript that also exists in
Python.

### The evaluation is paired

Outcomes are drawn from `hash(seed, payment, window, channel)`, never from a
per-policy RNG stream. When two policies make the same choice they get the
same outcome, so any difference in results is attributable to the decisions
themselves rather than to luck.

This is what makes the paired confidence interval legitimate: plus Rs 12,197
per batch, 95 percent CI plus 6,330 to plus 18,065, over 200 seeds.

### The model writes language and nothing else

Explanations are generated from the audit trail. The model receives a
decision that has already been made and is asked to phrase it. Its output is
displayed, never parsed. No number in the interface comes from it.

Offline, explanations are pre-generated and committed so the public repo
ships no API key. With `server.py` running, they are written live per
decision and the page names the model that wrote them.

---

## Running it

```bash
cd src

python3 eval_harness.py            # ~2 min, 200 seeds, writes data/
python3 pipeline_stats.py
python3 explain_exceptions.py      # needs a model endpoint, or --offline
python3 generate_dashboard.py

python3 test_razorpay_adapter.py   # 15 tests, no network
python3 test_recovery_actions.py   # 22 tests, no network
```

Then either open `data/dashboard.html` directly, or:

```bash
python3 src/server.py              # http://localhost:8934
PORT=8935 python3 src/server.py    # if 8934 is taken
```

The page detects which mode it is in and says so in the nav. Without the
server it runs the in-browser engine and pre-generated explanations; with
it, live model output and real webhook ingestion over SSE.

---

## What is deliberately absent

**A database.** State that matters lives in the audit trail, which is a
file. Adding a backend would give the demo a way to fail during a recording
and buys nothing the submission needs.

**A retry-trigger call.** Razorpay schedules mandate retries and NPCI fixes
the windows. This agent influences whether a payment is pursued and how the
customer is reached beforehand. Trying to fire the debit itself would cross
the line the compliance story depends on.

**A CDN.** Fonts, Three.js and the force-graph library are vendored and
inlined. Judges may open the repo offline.
