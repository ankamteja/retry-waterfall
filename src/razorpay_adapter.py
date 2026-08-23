"""
Adapter from Razorpay's real webhook payloads into this engine's domain
objects, so the agent is deployable against the live API rather than only
against the synthetic batch.

Everything referenced here was verified against Razorpay's public docs on
2026-08-23 (see docs/razorpay_integration.md for the citations and the
gap analysis this adapter is built around):

  Subscription lifecycle
    created -> authenticated -> active -> pending -> halted
    A failed auto-charge moves the subscription to `pending`; when retries
    are exhausted it moves to `halted`.

  Webhook events consumed
    subscription.pending   -- a charge failed, recovery window opens
    subscription.halted    -- retries exhausted, escalate to human
    subscription.charged   -- recovered, close the case

  auth_attempts
    Razorpay's own payloads show auth_attempts:1 on `pending` and
    auth_attempts:4 on `halted`. That ceiling of 4 is the same number as
    the NPCI AutoPay cap encoded in domain_rules.MAX_ATTEMPTS_PER_CYCLE,
    so the agent's stopping rule and Razorpay's own lifecycle agree.

  Error object
    Razorpay errors carry code / description / source / step / reason /
    metadata. `reason` is the machine-readable field this maps on.
"""

from dataclasses import dataclass

from domain_rules import DeclineCode, MAX_ATTEMPTS_PER_CYCLE
from synthetic_data import FailedPaymentEvent

RECOVERY_EVENTS = {"subscription.pending", "subscription.halted", "subscription.charged"}

# Razorpay `error.reason` values -> this engine's decline taxonomy.
# Reasons not listed fall back to ISSUER_HARD_DECLINE: refusing to retry an
# unrecognised failure is the safe default, because a wrong soft
# classification spends a regulated attempt that cannot be reclaimed.
REASON_TO_DECLINE = {
    "insufficient_funds": DeclineCode.INSUFFICIENT_FUNDS,
    "payment_failed_due_to_insufficient_funds": DeclineCode.INSUFFICIENT_FUNDS,
    "gateway_timeout": DeclineCode.BANK_SERVER_TIMEOUT,
    "server_error": DeclineCode.BANK_SERVER_TIMEOUT,
    "issuer_down": DeclineCode.BANK_SERVER_TIMEOUT,
    "payment_pending": DeclineCode.BANK_SERVER_TIMEOUT,
    "payment_declined_by_bank": DeclineCode.ISSUER_SOFT_DECLINE,
    "do_not_honour": DeclineCode.ISSUER_SOFT_DECLINE,
    "card_expired": DeclineCode.CARD_EXPIRED,
    "mandate_revoked": DeclineCode.MANDATE_REVOKED,
    "mandate_cancelled": DeclineCode.MANDATE_REVOKED,
    "account_closed": DeclineCode.ACCOUNT_CLOSED,
    "account_blocked": DeclineCode.ACCOUNT_CLOSED,
    "card_lost_or_stolen": DeclineCode.ISSUER_HARD_DECLINE,
    "payment_frozen": DeclineCode.ISSUER_HARD_DECLINE,
}

UNMAPPED_DEFAULT = DeclineCode.ISSUER_HARD_DECLINE


@dataclass(frozen=True)
class RazorpaySignal:
    """What the agent needs from one webhook delivery."""
    event: str
    subscription_id: str
    payment_id: str | None
    amount_inr: float
    auth_attempts: int
    decline_code: DeclineCode | None
    reason_raw: str | None
    attempts_remaining: int
    should_recover: bool


def _amount_inr(paise: int | None) -> float:
    """Razorpay reports money in paise; the engine works in rupees."""
    return round((paise or 0) / 100, 2)


def parse_webhook(payload: dict) -> RazorpaySignal | None:
    """Map one Razorpay webhook body to a RazorpaySignal.

    Returns None for events this agent does not act on, so a caller can
    subscribe to the whole firehose safely.
    """
    event = payload.get("event", "")
    if event not in RECOVERY_EVENTS:
        return None

    entities = payload.get("payload", {})
    sub = entities.get("subscription", {}).get("entity", {}) or {}
    pay = entities.get("payment", {}).get("entity", {}) or {}

    reason = (pay.get("error_reason") or pay.get("error", {}).get("reason")) or None
    decline = REASON_TO_DECLINE.get(reason, UNMAPPED_DEFAULT) if reason else None

    auth_attempts = sub.get("auth_attempts", 0) or 0
    remaining = max(0, MAX_ATTEMPTS_PER_CYCLE - auth_attempts)

    # subscription.charged closes the case; subscription.halted means
    # Razorpay already exhausted the cycle, so the agent must not add
    # attempts on top -- it escalates to a human instead.
    should_recover = (
        event == "subscription.pending"
        and remaining > 0
        and decline is not None
    )

    return RazorpaySignal(
        event=event,
        subscription_id=sub.get("id", ""),
        payment_id=pay.get("id"),
        amount_inr=_amount_inr(pay.get("amount") or sub.get("current_invoice_amount")),
        auth_attempts=auth_attempts,
        decline_code=decline,
        reason_raw=reason,
        attempts_remaining=remaining,
        should_recover=should_recover,
    )


def to_event(signal: RazorpaySignal, category: str = "subscription") -> FailedPaymentEvent:
    """Turn a recoverable signal into the engine's own event type, so the
    same policies that run on the synthetic batch run on live traffic."""
    if not signal.should_recover or signal.decline_code is None:
        raise ValueError(f"{signal.subscription_id}: signal is not recoverable")
    return FailedPaymentEvent(
        payment_id=signal.payment_id or signal.subscription_id,
        amount_inr=signal.amount_inr,
        category=category,
        decline_code=signal.decline_code,
    )
