# The operator console

`data/dashboard.html` is one self-contained file. Fonts, Three.js and the
force-graph library are inlined, so it renders with no network at all. Open it
directly, or run `src/server.py` to add live model output and real webhook
ingestion.

It is laid out as a console rather than a document: a fixed viewport with a
control rail on the left, a tabbed stage on the right, and a shared log strip
along the bottom. Nothing scrolls the page. The rail drives whichever pane is
on stage, and only that pane's primary action is filled in, so there is one
obvious next thing to press.

The `?` in the masthead opens a long-form primer for a reader who wants the
domain explained. It is behind a control because a console should not open by
lecturing.

---

## Live or offline

The masthead says which mode it is in and never pretends.

| Indicator | Meaning |
|---|---|
| `offline · in-browser engine` | No server. The compliance gate, Thompson sampling, the race and every table still run, in the page. Explanations are the pre-generated ones. |
| `live · <model>` | `server.py` is behind it. A language model writes each explanation as the decision happens, and real Razorpay webhooks can arrive over SSE. |

The deployed static site is the first of these, by design. The engine is
genuinely running in your browser either way; what the server adds is live
prose and real webhook ingestion.

---

## The five panes

### Decision

One payment, every step it took. Compose a failure on the rail — reason,
category, amount — and run it. The gate, the sampling and the outbound call
all execute in the page, from the posteriors the bandit learned during
evaluation.

Run the same payment twice and it can pick a different channel. That is not a
bug: Thompson Sampling samples a belief rather than reading a table, and
showing that is the point.

**Sample 200×** runs the same payment two hundred times and shows the
distribution of channels chosen, which is the honest way to see a stochastic
policy's behaviour.

### Benchmark

The head-to-head race. Sixty fresh payments, blind retry against the agent.

The outcome of every attempt is fixed by a hash of `(race, payment, window,
channel)` *before either policy runs*, which is the browser port of
`eval_harness.outcome_draw`. Both sides also pass through the identical
compliance gate. So the only thing being compared is channel choice, which is
exactly what the reported lift measures.

Below the race, the Beta posteriors are drawn as they update. The pale band is
the range still considered plausible and the bright line the current best
guess; wide means not enough evidence to commit, so the agent keeps trying
that channel until it has some.

One batch is noisy in both directions, and the verdict says so either way,
quoting the 200-batch mean and interval next to whatever this single race did.

### Stream

Continuous traffic through the pipeline at a chosen rate, with running
counters. This is the pane for showing throughput rather than a single
decision.

### Evidence

The audited run and the aggregate results.

The retry timeline is the signature element: one lane per payment running left
to right across the seven days the regulation allows, a marker at each
permitted attempt, and an amber line at the NPCI wall. Filled teal is the
attempt that recovered it; a dashed lane never got an attempt because it was
refused outright. The regulatory constraint and the recovery moment are
visible in the same glance.

Below it, four tables: by policy, by decline code, by channel and by window,
plus the learning curve showing the bandit converging toward the oracle as
pulls per cell grow.

### Pipeline

The five stages as a 3D graph, with particles along each edge carrying real
volume. The gate is red because it stops payments a naive retry bot would
have illegally retried; the amber branch is the ones above the authentication
threshold, routed to a link instead. Drag to rotate, scroll to zoom.

---

## Driving the demo

The three beats, in order. Each changes exactly one field.

1. **Insufficient funds, subscription, Rs 4,200.** Run it. Walk the stages out
   loud: classify soft, gate cleared, then the policy sampling a belief per
   channel and converting it to expected rupees net of cost. Point at the
   pulls column — that is how much evidence it has.
2. **Change the amount to Rs 20,000.** Run again. The gate escalates instead.
   Above the limit a silent retry cannot clear, so it issues an authenticated
   payment link and leaves the mandate attempt budget untouched.
3. **Change the category to mutual fund, keep Rs 20,000.** Run again. It
   clears, because the enhanced limit for mutual funds is Rs 1,00,000.

Then, with `server.py` running, post a real webhook from a terminal and let it
land on the open page:

```bash
curl -X POST http://localhost:8934/webhook \
  -H 'Content-Type: application/json' \
  -H 'X-Explain: 1' \
  -d '{
    "event": "subscription.pending",
    "payload": {
      "subscription": {"entity": {"id": "sub_demo", "auth_attempts": 1}},
      "payment": {"entity": {"id": "pay_demo", "amount": 420000,
                             "error": {"reason": "insufficient_funds"}}}
    },
    "category": "subscription"
  }'
```

Same adapter, same gate, same bandit, and the outbound call it would make
written out in full. The response comes back on the terminal and the decision
appears on the page over the live stream.

`amount` is in paise, so `420000` is Rs 4,200. `category` rides alongside the
Razorpay body because Razorpay does not carry the mutual-fund / insurance /
credit-card-bill distinction that sets the authentication ceiling.

---

## Rebuilding it

```bash
cd src
python3 eval_harness.py          # ~2 min, 200 seeds
python3 pipeline_stats.py
python3 generate_dashboard.py
```

The page is generated, never hand-edited. Every threshold, cost, probability
and decline weight in its JavaScript is read out of the Python at build time,
so the browser copy cannot drift from `domain_rules`, `policies` or
`synthetic_data`.
