"""
Tests for the Razorpay webhook adapter, using payloads shaped like the
real ones (auth_attempts on the subscription entity, amounts in paise,
error reason on the payment entity).

Run: python test_razorpay_adapter.py
"""

from domain_rules import DeclineCode
from razorpay_adapter import parse_webhook, to_event, UNMAPPED_DEFAULT


def webhook(event, *, auth_attempts=1, reason=None, amount_paise=149900):
    return {
        "event": event,
        "payload": {
            "subscription": {"entity": {
                "id": "sub_00000000000001",
                "status": event.split(".")[1],
                "auth_attempts": auth_attempts,
                "current_invoice_amount": amount_paise,
            }},
            "payment": {"entity": {
                "id": "pay_00000000000001",
                "amount": amount_paise,
                "error_reason": reason,
            }} if reason is not None else {},
        },
    }


def check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    return cond


def main():
    ok = True
    print("razorpay_adapter")

    s = parse_webhook(webhook("subscription.pending", reason="insufficient_funds"))
    ok &= check("soft decline on pending is recoverable", s.should_recover)
    ok &= check("insufficient_funds maps to soft code",
                s.decline_code is DeclineCode.INSUFFICIENT_FUNDS)
    ok &= check("paise converted to rupees", s.amount_inr == 1499.00)
    ok &= check("3 attempts remain after the 1st", s.attempts_remaining == 3)

    s = parse_webhook(webhook("subscription.pending", reason="mandate_revoked"))
    ok &= check("hard decline is parsed", s.decline_code is DeclineCode.MANDATE_REVOKED)
    ok &= check("hard decline still surfaces as a signal", s is not None)

    # The agent must not stack attempts on top of a cycle Razorpay already
    # exhausted -- that is the compliance-critical case.
    s = parse_webhook(webhook("subscription.halted", auth_attempts=4, reason="insufficient_funds"))
    ok &= check("halted is never recoverable", not s.should_recover)
    ok &= check("no attempts remain at the cap", s.attempts_remaining == 0)

    s = parse_webhook(webhook("subscription.pending", auth_attempts=4, reason="insufficient_funds"))
    ok &= check("cap reached is not recoverable even while pending", not s.should_recover)

    s = parse_webhook(webhook("subscription.charged"))
    ok &= check("charged is recognised but not recoverable", not s.should_recover)

    # Fail-closed: an unrecognised reason is classified hard, so it reaches
    # the policy layer (should_recover=True means "hand it to the policy",
    # not "retry it") and policies.is_hard_decline then refuses the retry.
    s = parse_webhook(webhook("subscription.pending", reason="some_reason_razorpay_added_later"))
    ok &= check("unknown reason fails closed to hard decline",
                s.decline_code is UNMAPPED_DEFAULT)
    from domain_rules import is_hard_decline
    ok &= check("and the policy layer therefore refuses to retry it",
                is_hard_decline(s.decline_code))

    ok &= check("unrelated events are ignored",
                parse_webhook({"event": "payout.processed", "payload": {}}) is None)

    ev = to_event(parse_webhook(webhook("subscription.pending", reason="gateway_timeout")))
    ok &= check("recoverable signal converts to an engine event",
                ev.decline_code is DeclineCode.BANK_SERVER_TIMEOUT and ev.amount_inr == 1499.00)

    try:
        to_event(parse_webhook(webhook("subscription.halted", auth_attempts=4,
                                       reason="insufficient_funds")))
        ok &= check("converting a non-recoverable signal raises", False)
    except ValueError:
        ok &= check("converting a non-recoverable signal raises", True)

    print("OK" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
