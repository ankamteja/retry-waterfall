# Razorpay integration: what was verified, and what is illustrative

`src/razorpay_adapter.py` and `src/recovery_actions.py` both cite this file.
It exists to make that citation checkable, and to be explicit about the line
between things confirmed from Razorpay's own public documentation and things
this project assumed.

Everything in the **Verified** sections was read from Razorpay's public docs
on 2026-08-23. Everything in **Illustrative** was not, and is marked as such
rather than presented with false confidence.

---

## Verified: the subscription lifecycle

```
created -> authenticated -> active -> pending -> halted
                              |
                              +-> paused / resumed / cancelled / completed
```

Two states matter for recovery:

- **`pending`** means an auto-charge failed. The recovery window is open.
- **`halted`** means retries were exhausted. Invoices keep generating but no
  further auto-charge is attempted, and recovery becomes manual.

## Verified: the webhook events

Razorpay pushes `subscription.authenticated`, `subscription.activated`,
`subscription.charged`, `subscription.completed`, `subscription.updated`,
`subscription.pending`, `subscription.halted`, `subscription.paused`,
`subscription.resumed` and `subscription.cancelled`.

This agent consumes three of them, in `RECOVERY_EVENTS`:

| Event | Meaning here |
|---|---|
| `subscription.pending` | A charge failed. Start recovering. |
| `subscription.halted` | Out of attempts. Escalate to a human. |
| `subscription.charged` | Recovered. Close the case. |

Subscribing to the whole firehose is safe: `parse_webhook` returns `None`
for anything else.

## Verified: auth_attempts, and why it matters

Razorpay's own sample payloads show a subscription in `pending` carrying
`auth_attempts: 1`, and one in `halted` carrying `auth_attempts: 4`.

Four is the same ceiling as the NPCI AutoPay cap already encoded in
`domain_rules.MAX_ATTEMPTS_PER_CYCLE`, arrived at from the regulation rather
than from Razorpay. Razorpay's lifecycle and this agent's stopping rule agree
on the number independently, which is the single strongest signal that the
domain model here is right.

## Verified: what Razorpay's built-in retry already does

- Automatically retries failed payments the following day.
- For e-Mandate and UPI it waits for confirmation or rejection of the last
  payment, which can take longer than 24 hours.
- Bank holidays: if charge day T is a holiday it charges on T-1; if both T
  and T-1 are holidays, T-3.
- The customer receives an email containing a link to update card details.
- Merchants receive `subscription.pending` and `subscription.halted`.
- **The total number of retry attempts is not disclosed** in the docs.

That last point is the reason `audit_log.py` exists. A merchant running its
own dunning on top of Razorpay's automatic retries cannot currently prove it
stayed under the NPCI cap, because it cannot see the total.

## Verified: the error object

Razorpay errors carry `code`, `description`, `source`, `step`, `reason` and
`metadata`. `reason` is the machine-readable field, and it is what
`REASON_TO_DECLINE` maps on.

Unrecognised reasons fall back to `ISSUER_HARD_DECLINE`, which is to say the
adapter fails closed. Wrongly classifying a failure as soft spends one of
four regulated attempts that cannot be reclaimed; wrongly classifying one as
hard costs a single recovery opportunity. The asymmetry is deliberate.

**Sources:**
[Subscription webhook payloads](https://razorpay.com/docs/webhooks/payloads/subscriptions/) ·
[Payment retries](https://razorpay.com/docs/payments/subscriptions/payment-retries/) ·
[About errors](https://razorpay.com/docs/errors/) ·
[e-Mandate errors](https://razorpay.com/docs/payments/recurring-payments/emandate/errors/)

---

## Illustrative: the outbound payloads

`src/recovery_actions.py` builds the outbound call for each terminal branch.
**No request body below has been executed against a live or test-mode
Razorpay account.** They are shaped from the public API reference and are
committed so the intent is reviewable as data, not so they can be pasted
into production untouched.

### `create_auth_payment_link`

The one branch that is a genuine Razorpay call. A payment above the RBI
additional-factor-authentication threshold cannot clear on a silent mandate
retry, so the customer is sent an authenticated link instead and the mandate
attempt budget is left intact.

```
POST https://api.razorpay.com/v1/payment_links
{
  "amount": 2000000,               // paise
  "currency": "INR",
  "accept_partial": false,
  "description": "Authenticated retry of a failed recurring payment",
  "expire_by_hours": 168,          // the outer edge of the NPCI schedule
  "reminder_enable": true,
  "notify": {"sms": true, "email": true},
  "callback_url": "https://merchant.example/recovery/<payment_id>",
  "callback_method": "get",
  "notes": {
    "original_payment_id": "...",
    "decline_code": "...",
    "escalation_reason": "rbi_afa_threshold_exceeded"
  }
}
```

Two things to check before this runs anywhere real: the exact expiry field
name and units, and whether `notify` requires customer contact details to be
supplied in the same call rather than resolved from the subscription.

### `send_sms` and `place_ivr_call`

Deliberately **not** Razorpay calls and deliberately not any named vendor's
schema either. SMS and IVR are comms-provider operations, no provider has
been chosen, and inventing one vendor's request format here would be
fiction dressed as integration.

The payload therefore carries a template id, the variables that template
needs, and `recipient_ref` — the payment id, not a phone number. The batch
holds no phone numbers, and fabricating PII to make a payload look finished
would put fake customer data into a committed artifact.

### `suppress_no_retry` and `escalate_to_manual_review`

No outbound call at all. Both are recorded explicitly anyway, because a
silence you can point at in an audit trail is worth more than an absence.

---

## What is deliberately not attempted

There is no "retry this charge now" call in this project, because Razorpay
drives mandate retry scheduling itself and NPCI fixes the windows. The agent
influences *whether* a payment should be pursued and *how the customer is
reached before the scheduled attempt*. It never tries to trigger the debit.

That boundary is the compliance story, and it is enforced in code: the
executor re-derives authorisation from `domain_rules` on every emit and
raises `ComplianceViolation` rather than trusting the caller.

## What has not been done

The adapter is tested against real payload *shapes* — 15 tests in
`src/test_razorpay_adapter.py`, no network needed — but it has never run
against a live Razorpay test-mode account. Say so before a judge asks.
