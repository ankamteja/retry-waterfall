# Reference

Data formats, the HTTP surface, and the decline taxonomy. Everything a reader
needs to check a claim against a file rather than take it on trust.

For the narrative version see [`WALKTHROUGH.md`](WALKTHROUGH.md).

---

## The decline taxonomy

`domain_rules.DeclineCode`. Soft means transient, so a later retry can
succeed. Hard means permanent for this billing cycle, so retrying is both
pointless and non-compliant.

| Code | Class | Meaning |
|---|---|---|
| `insufficient_funds` | soft | No balance at the moment of the debit. |
| `bank_server_timeout` | soft | The bank did not answer in time. |
| `issuer_soft_decline` | soft | Do-not-honour and similar retryable refusals. |
| `card_expired` | hard | Instrument is no longer valid. |
| `mandate_revoked` | hard | The customer withdrew authorisation. |
| `account_closed` | hard | Account no longer exists. |
| `issuer_hard_decline` | hard | Lost, stolen or blocked. Also the fail-closed default. |

The hard set is what the compliance gate refuses outright, before any policy
runs.

### Razorpay `error.reason` to decline code

`razorpay_adapter.REASON_TO_DECLINE`. Anything not listed falls back to
`issuer_hard_decline`.

| Razorpay reason | Decline code |
|---|---|
| `insufficient_funds`, `payment_failed_due_to_insufficient_funds` | `insufficient_funds` |
| `gateway_timeout`, `server_error`, `issuer_down`, `payment_pending` | `bank_server_timeout` |
| `payment_declined_by_bank`, `do_not_honour` | `issuer_soft_decline` |
| `card_expired` | `card_expired` |
| `mandate_revoked`, `mandate_cancelled` | `mandate_revoked` |
| `account_closed`, `account_blocked` | `account_closed` |
| `card_lost_or_stolen`, `payment_frozen` | `issuer_hard_decline` |
| *anything unrecognised* | `issuer_hard_decline` |

**The fallback direction is deliberate and asymmetric.** Wrongly classifying a
failure as soft spends one of four regulated attempts that cannot be
reclaimed. Wrongly classifying one as hard costs a single recovery
opportunity. The cheaper mistake is the one the adapter makes.

---

## The audit record

`audit_log.AuditRecord`, one JSON line per decision, written to
`data/audit_<policy>_seed<n>.jsonl`. This is the artifact that makes
compliance checkable after the fact, so every field is there to answer a
question a regulator or a merchant might ask.

| Field | Type | Notes |
|---|---|---|
| `payment_id` | string | Groups the lines for one payment. |
| `policy` | string | `baseline`, `bandit` or `oracle`. |
| `decline_code` | string | From the taxonomy above. |
| `attempt_number` | int | 1 is the original failed execution. Retries are 2, 3 and 4. |
| `window_hours` | int or null | 24, 72 or 168. Null when no attempt was scheduled. |
| `channel` | string or null | `sms`, `ivr_call`, or null when nobody was contacted. |
| `amount_inr` | float | Rupees, not paise. |
| `expected_net_inr` | float or null | What the policy expected to net. Null for the baseline, which does no arithmetic. |
| `rationale` | string | Why, in words, from the policy itself. |
| `recovered` | bool or null | Null means the attempt was skipped and nobody was contacted. |
| `stopping_rule` | string or null | See below. |

### Stopping rules

| Value | Meaning |
|---|---|
| `hard_decline_no_retry` | Refused at the gate. Never reached a policy. |
| `afa_required_escalate_to_auth_link` | Above the RBI threshold. Routed to an authenticated link; the mandate's attempt budget is untouched. |
| `npci_retry_cap_exhausted` | The last permitted window has been used. |
| `null` | The payment is still inside its recovery window. |

`pipeline_stats.py` refuses to build if any payment shows more than three
retries, because that would mean the trail had stacked multiple runs and every
downstream number would be wrong. Failing loudly beats publishing it.

---

## The outbound action

`recovery_actions.RecoveryAction`, written to
`data/actions_bandit_seed<n>.jsonl`.

| Field | Type | Notes |
|---|---|---|
| `payment_id` | string | |
| `kind` | string | One of the five below. |
| `amount_inr` | float | |
| `reason` | string | Carried through from the policy's rationale. |
| `authorised_by` | string | **The `domain_rules` clause that permits this call.** |
| `provider` | string or null | `razorpay`, `comms`, or null when no call goes out. |
| `request` | object or null | The literal call: method, URL, body. Null means this branch correctly results in no outbound call, which is itself worth recording. |
| `attempt_number` | int or null | |
| `window_hours` | int or null | |
| `dry_run` | bool | Always true in this repository. |

### The five action kinds

| Kind | Provider | Outbound call |
|---|---|---|
| `send_sms` | comms | Template id plus variables. No vendor schema, because no vendor is chosen. |
| `place_ivr_call` | comms | As above. |
| `create_auth_payment_link` | razorpay | A real `POST /v1/payment_links`. |
| `suppress_no_retry` | none | Recorded silence for a hard decline. |
| `escalate_to_manual_review` | none | Recorded silence after the windows are used. |

**No phone numbers appear anywhere.** The payload carries `recipient_ref`,
which is the payment id. The batch holds no contact details, and fabricating
PII to make a payload look finished would put fake customer data into a
committed artifact.

### Nothing sends

`RecoveryExecutor._dispatch` raises `NotImplementedError`. `DryRunExecutor`
records and returns. There is no code path in this repository that transmits,
which is why it ships with no credentials. Adding a sending subclass is the
deployment step and the only place a key would ever appear.

Every emit re-derives authorisation from `domain_rules` instead of trusting
the caller, and raises `ComplianceViolation` rather than producing an action.
It checks four things: the decline is not hard, the amount is not above the
authentication threshold for its category, the attempt number is within the
NPCI cap, and the window is one of the mandated ones.

---

## The HTTP surface

`src/server.py`, optional, binds `127.0.0.1` only. Default port 8934,
overridable with `PORT`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | The console. Also `/index.html` and `/dashboard.html`. |
| `GET` | `/health` | `{"live": true, "model": "..."}`. How the page decides whether it is in live or offline mode. |
| `GET` | `/events` | Server-sent events. Every decision is pushed to connected pages. |
| `POST` | `/webhook` | A genuine Razorpay webhook body. Runs the real adapter, gate, policy and dry-run executor. |
| `POST` | `/llm` | Proxies one prompt to an OpenAI-compatible endpoint. |

`POST /webhook` accepts an optional `category` field alongside the Razorpay
body, because Razorpay does not carry the mutual-fund / insurance /
credit-card-bill distinction that sets the authentication ceiling. Send
`X-Explain: 1` to have a model write the plain-English explanation into the
response.

A refused outbound call comes back `200` with `{"outcome": "blocked",
"reason": ...}` rather than an error status, because a refusal is a real
answer from the system, not a server fault.

### Environment

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `8934` | |
| `LLM_ENDPOINT` | local OpenAI-compatible URL | Where explanations are generated. |
| `LLM_MODEL` | provider default | Named in the interface so the reader knows who wrote the sentence. |
| `LLM_API_KEY` | empty | Only needed if the endpoint demands one. |

---

## Generated constants

`dashboard_live.live_constants()` emits everything the browser engine needs as
JSON at build time: the attempt cap, the retry windows, both authentication
thresholds and the categories they apply to, the soft and hard sets, the
channels and their costs, the amount range, the decline mix, the base
probabilities, the window and channel multipliers, and the trained posteriors.

This is why the browser can run the real policy rather than a mock, and why it
cannot drift: change a threshold in `domain_rules.py` and the page changes
with the next build. There is no magic number in that JavaScript that also
exists in Python.

---

## Files written to `data/`

Everything here is a build artifact and regenerated by the pipeline, with one
exception.

| File | Written by | Committed |
|---|---|---|
| `headline_summary.json` | `eval_harness.py` | no |
| `learning_curve.json` | `eval_harness.py` | no |
| `posteriors.json` | `eval_harness.py` | no |
| `audit_<policy>_seed0.jsonl` | `eval_harness.py` | no |
| `actions_bandit_seed0.jsonl` | `eval_harness.py` | no |
| `pipeline_stats.json` | `pipeline_stats.py` | no |
| `explanations.json` | `explain_exceptions.py` | **yes** |
| `dashboard.html` | `generate_dashboard.py` | no |

`explanations.json` is the exception because generating it is the one step
that needs a model endpoint. Committing it is what lets a clone, a build or a
deployment render the page without a key.
