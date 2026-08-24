"""
The outbound half of the loop.

Everything upstream of this file decides *what should happen* to a failed
payment. Nothing until now actually did anything: the engine parsed
webhooks, classified declines, applied the compliance gate, picked a
channel, and stopped. This module turns each of those terminal decisions
into a concrete, inspectable outbound call.

Two properties matter more than the calls themselves.

**It is dry-run by default and there is no live path in this repo.**
`RecoveryExecutor` defines the seam (`_dispatch`); `DryRunExecutor` records
the exact request it would have sent and sends nothing. A deployment
subclasses it. That means the demo, the eval, and the public repo all run
with zero credentials and zero side effects, and the request bodies stay
reviewable as data.

**It re-derives authorisation instead of trusting the caller.**
Every emit path re-checks the same NPCI/RBI rules from `domain_rules`
before producing an action, and raises `ComplianceViolation` if the caller
asked for something the rules forbid. The policy layer already refuses
these cases, so the guard should never fire -- that is the point. It is a
second, independent gate on the only step that can touch a customer, so a
future policy bug cannot silently turn into an outbound contact. The
`bounded` in "bounded recovery workflow" is enforced here, not assumed.

Five terminal actions, one per branch the engine can reach:

    send_sms                   soft decline, bandit picked SMS
    place_ivr_call             soft decline, bandit picked IVR
    create_auth_payment_link   over the RBI AFA threshold: a silent retry
                               cannot legally succeed, so the customer is
                               sent an authenticated link instead of the
                               mandate spending an attempt
    suppress_no_retry          hard decline: permanent for this cycle, so
                               the correct outbound call is none at all
    escalate_to_manual_review  NPCI attempt cap reached unrecovered

Payload shapes for the Razorpay call are documented, with sources, in
docs/razorpay_integration.md. The comms actions are deliberately
provider-agnostic: SMS and IVR are not Razorpay endpoints, and inventing a
specific vendor's schema here would be fiction.
"""

import json
import threading
from dataclasses import asdict, dataclass
from enum import Enum

from domain_rules import (
    MAX_ATTEMPTS_PER_CYCLE,
    RETRY_WINDOWS_HOURS,
    is_hard_decline,
    requires_additional_factor_auth,
)

RAZORPAY_API_BASE = "https://api.razorpay.com/v1"

# Attempt 1 is the original auto-debit the bank already declined, so the
# first attempt a recovery contact can belong to is 2. The last is the NPCI
# cap itself, which makes MAX_RETRY_CONTACTS_PER_CYCLE contacts in a cycle.
MIN_RETRY_ATTEMPT = 2
MAX_RETRY_CONTACTS_PER_CYCLE = MAX_ATTEMPTS_PER_CYCLE - MIN_RETRY_ATTEMPT + 1

# NPCI fixes the schedule, so an attempt number and a retry window are not
# independent: the 2nd attempt is the T+24h one, the 3rd is T+72h, the 4th is
# T+7d. Deriving the pairing keeps it correct if the windows ever change.
ATTEMPT_TO_WINDOW_HOURS = {
    MIN_RETRY_ATTEMPT + i: hours for i, hours in enumerate(RETRY_WINDOWS_HOURS)
}


class ComplianceViolation(Exception):
    """Raised when a caller asks the executor to contact a customer in a way
    the payment rails do not permit. Never caught internally: an outbound
    call that breaches a mandate rule is a defect, not a condition to
    recover from."""


class ContactLedger:
    """What has actually been said to whom, this cycle.

    The NPCI cap is a fact about a payment, not about a call, so a guard
    that only inspects the arguments in front of it cannot enforce one --
    the caller picks those arguments. This is the memory that makes the cap
    binding, and `reserve` is the only way to consume an attempt.

    Reserving is atomic. The demo server is a ThreadingHTTPServer, so a
    check that read the ledger and appended to it in two steps would let
    concurrent requests for one payment each pass a check that was true
    when they read it and false by the time they wrote.

    One instance covers one cycle for every executor that shares it. It is
    in-memory, so the cap still stops binding when the process ends; a
    deployment has to back this with a durable store. That is the honest
    boundary of the guarantee.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._attempts: dict[str, list[int]] = {}

    def attempts_for(self, payment_id: str) -> list[int]:
        with self._lock:
            return list(self._attempts.get(payment_id, []))

    def reserve(self, payment_id: str, attempt_number: int) -> None:
        """Consume one attempt for this payment, or raise and consume none."""
        with self._lock:
            already = self._attempts.get(payment_id, [])
            if len(already) >= MAX_RETRY_CONTACTS_PER_CYCLE:
                raise ComplianceViolation(
                    f"{payment_id} has already been contacted {len(already)} times this "
                    f"cycle (attempts {already}): the NPCI cap of {MAX_ATTEMPTS_PER_CYCLE} "
                    f"allows {MAX_RETRY_CONTACTS_PER_CYCLE} recovery contacts"
                )
            if attempt_number in already:
                raise ComplianceViolation(
                    f"{payment_id} was already contacted on attempt {attempt_number}: "
                    f"one contact per attempt"
                )
            if already and attempt_number < already[-1]:
                raise ComplianceViolation(
                    f"{payment_id} is on attempt {attempt_number} after attempt "
                    f"{already[-1]}: attempts within a cycle only move forward"
                )
            self._attempts.setdefault(payment_id, []).append(attempt_number)

    def release(self, payment_id: str, attempt_number: int) -> None:
        """Give a reserved attempt back, for a contact that was never sent.

        Without this, a dispatch that fails after the reservation burns one
        of the payment's three attempts and the retry can never be made
        again -- a delivery failure would quietly cost a legal retry.
        """
        with self._lock:
            attempts = self._attempts.get(payment_id)
            if attempts and attempt_number in attempts:
                attempts.remove(attempt_number)


# How long an authenticated recovery link stays valid. Chosen to match the
# outer edge of the NPCI retry schedule (T+7d) so the link and the mandate
# cycle expire together rather than leaving a live payment URL behind after
# the cycle has already been written off.
AUTH_LINK_TTL_HOURS = RETRY_WINDOWS_HOURS[-1]


class ActionKind(str, Enum):
    SEND_SMS = "send_sms"
    PLACE_IVR_CALL = "place_ivr_call"
    CREATE_AUTH_PAYMENT_LINK = "create_auth_payment_link"
    SUPPRESS_NO_RETRY = "suppress_no_retry"
    ESCALATE_TO_MANUAL_REVIEW = "escalate_to_manual_review"


CHANNEL_TO_ACTION = {
    "sms": ActionKind.SEND_SMS,
    "ivr_call": ActionKind.PLACE_IVR_CALL,
}

# Which actions put something in front of a customer. Only these consume a
# contact budget and only these are guarded on the mandate rules; the other
# two are internal bookkeeping.
CONTACT_ACTIONS = {
    ActionKind.SEND_SMS,
    ActionKind.PLACE_IVR_CALL,
    ActionKind.CREATE_AUTH_PAYMENT_LINK,
}


@dataclass(frozen=True)
class RecoveryAction:
    """One outbound decision, fully specified.

    `request` is the literal call that would go out: method, URL, and body.
    None means this branch correctly results in no outbound call at all,
    which is itself a decision worth recording.
    """
    payment_id: str
    kind: ActionKind
    amount_inr: float
    reason: str
    authorised_by: str          # the domain_rules clause that permits this
    provider: str | None = None  # "razorpay", "comms", or None
    request: dict | None = None
    attempt_number: int | None = None
    window_hours: int | None = None
    dry_run: bool = True

    def to_json_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d


def _paise(amount_inr: float) -> int:
    """Razorpay takes money in paise. Round rather than truncate: the batch
    carries 2dp rupee amounts, and int() on a float like 8399.99 * 100
    lands a paise short."""
    return int(round(amount_inr * 100))


class RecoveryExecutor:
    """Base class. Subclasses implement `_dispatch` to actually send.

    Nothing in this repository implements a sending `_dispatch`. Adding one
    is the deployment step, and it is the only place credentials would ever
    appear.
    """

    dry_run = True

    def __init__(self, path: str | None = None,
                 callback_base_url: str = "https://merchant.example/recovery",
                 ledger: ContactLedger | None = None):
        self._path = path
        self._file = open(path, "w", encoding="utf-8") if path else None
        self._callback_base_url = callback_base_url
        self.actions: list[RecoveryAction] = []
        # A caller that creates one executor per request needs to pass a
        # shared ledger, or the cap is re-set to empty on every request and
        # binds nothing. The default is a private one, which is right for
        # the eval: one executor per seed is exactly one cycle.
        self.ledger = ledger if ledger is not None else ContactLedger()

    # -- the seam ---------------------------------------------------------

    def _dispatch(self, action: RecoveryAction) -> None:
        raise NotImplementedError

    def _emit(self, action: RecoveryAction) -> RecoveryAction:
        self.actions.append(action)
        self._dispatch(action)
        if self._file:
            self._file.write(json.dumps(action.to_json_dict()) + "\n")
            self._file.flush()
        return action

    # -- guards -----------------------------------------------------------

    def _guard_contactable(self, payment_id: str, decline_code, amount_inr: float,
                           category: str, attempt_number: int, window_hours: int) -> None:
        """Re-derive, from domain_rules alone, that contacting this customer
        on this attempt is permitted. Deliberately does not consult the
        policy layer's opinion."""
        if is_hard_decline(decline_code):
            raise ComplianceViolation(
                f"{decline_code.value} is a hard decline: no retry contact is permitted this cycle"
            )
        if requires_additional_factor_auth(amount_inr, category):
            raise ComplianceViolation(
                f"INR {amount_inr:,.2f} in category '{category}' is above the RBI AFA threshold: "
                f"requires an authenticated link, not a silent retry"
            )
        # Bounded on both sides. An unbounded lower end let attempt 0 and
        # attempt -1 through, and both read as "within the cap" to every
        # check that only compares against the ceiling.
        if not MIN_RETRY_ATTEMPT <= attempt_number <= MAX_ATTEMPTS_PER_CYCLE:
            raise ComplianceViolation(
                f"attempt {attempt_number} is outside the retry range "
                f"{MIN_RETRY_ATTEMPT}..{MAX_ATTEMPTS_PER_CYCLE}: attempt 1 is the "
                f"original debit and the NPCI cap is {MAX_ATTEMPTS_PER_CYCLE} per cycle"
            )

        # The attempt number and the window are not independent: NPCI fixes
        # the schedule, so the 2nd attempt *is* the T+24h one. Checking each
        # separately accepted a 2nd attempt claiming the T+7d window, which
        # is a retry outside the mandated schedule wearing a legal label.
        #
        # This subsumes the old "is this one of the mandated windows" check:
        # every value it can accept is drawn from RETRY_WINDOWS_HOURS, so a
        # separate membership test after it could never fire.
        expected = ATTEMPT_TO_WINDOW_HOURS[attempt_number]
        if window_hours != expected:
            raise ComplianceViolation(
                f"attempt {attempt_number} is the T+{expected}h retry, not T+{window_hours}h. "
                f"The mandated windows are {RETRY_WINDOWS_HOURS} and NPCI fixes which "
                f"attempt lands in which -- the agent chooses the channel, never the timing"
            )

    # -- the five branches ------------------------------------------------

    def contact(self, payment_id: str, amount_inr: float, category: str, decline_code,
                attempt_number: int, window_hours: int, channel: str, rationale: str) -> RecoveryAction:
        """A soft decline inside the mandate window: nudge the customer on
        the channel the bandit chose, so the next scheduled auto-debit finds
        a funded account.

        The nudge does not itself move money. NPCI fixes when the retry
        fires; this only changes whether the customer is ready for it. That
        separation is why the channel is a learned decision and the timing
        never is.
        """
        self._guard_contactable(payment_id, decline_code, amount_inr, category,
                                attempt_number, window_hours)
        # Checked before the attempt is reserved: an unknown channel is the
        # caller's mistake, and it must not cost the payment a legal retry.
        kind = CHANNEL_TO_ACTION.get(channel)
        if kind is None:
            raise ComplianceViolation(f"unknown contact channel '{channel}'")

        # Reserving is the atomic step that consumes the attempt. Anything
        # that fails after it hands the attempt back, so a dispatch error
        # cannot quietly spend one of the payment's three retries.
        self.ledger.reserve(payment_id, attempt_number)
        try:
            return self._emit(RecoveryAction(
                payment_id=payment_id,
                kind=kind,
                amount_inr=amount_inr,
                reason=rationale,
                authorised_by=f"soft_decline_within_npci_cap (attempt {attempt_number}/{MAX_ATTEMPTS_PER_CYCLE})",
                provider="comms",
                request={
                    # Provider-agnostic on purpose: SMS and IVR are not Razorpay
                    # endpoints, and no vendor is chosen yet.
                    "channel": channel,
                    "template": f"recovery_{decline_code.value}_t{window_hours}h",
                    "variables": {
                        "amount_inr": round(amount_inr, 2),
                        "retry_window_hours": window_hours,
                        "attempt_number": attempt_number,
                    },
                    # No phone number: the batch carries none, and fabricating
                    # PII to make a payload look complete would be worse than an
                    # honest reference.
                    "recipient_ref": payment_id,
                },
                attempt_number=attempt_number,
                window_hours=window_hours,
                dry_run=self.dry_run,
            ))
        except Exception:
            # Never emitted, so the attempt was never used.
            self.ledger.release(payment_id, attempt_number)
            raise

    def escalate_afa(self, payment_id: str, amount_inr: float, category: str, decline_code,
                     rationale: str) -> RecoveryAction:
        """Above the RBI AFA threshold. A background retry on the mandate
        cannot legally clear without the customer re-authenticating, so
        spending a mandate attempt on it burns one of four irreplaceable
        tries on a guaranteed failure. Send an authenticated payment link
        instead and leave the attempt budget intact.
        """
        if not requires_additional_factor_auth(amount_inr, category):
            raise ComplianceViolation(
                f"INR {amount_inr:,.2f} in category '{category}' is below the AFA threshold: "
                f"an auth link is not the correct action here"
            )
        if is_hard_decline(decline_code):
            raise ComplianceViolation(
                f"{decline_code.value} is a hard decline: the mandate itself is dead, "
                f"an auth link on the same instrument will not clear"
            )

        return self._emit(RecoveryAction(
            payment_id=payment_id,
            kind=ActionKind.CREATE_AUTH_PAYMENT_LINK,
            amount_inr=amount_inr,
            reason=rationale,
            authorised_by=f"rbi_afa_threshold_exceeded (category '{category}')",
            provider="razorpay",
            request={
                "method": "POST",
                "url": f"{RAZORPAY_API_BASE}/payment_links",
                "body": {
                    "amount": _paise(amount_inr),
                    "currency": "INR",
                    "accept_partial": False,
                    "description": "Authenticated retry of a failed recurring payment",
                    "expire_by_hours": AUTH_LINK_TTL_HOURS,
                    "reminder_enable": True,
                    "notify": {"sms": True, "email": True},
                    "callback_url": f"{self._callback_base_url}/{payment_id}",
                    "callback_method": "get",
                    "notes": {
                        "original_payment_id": payment_id,
                        "decline_code": decline_code.value,
                        "escalation_reason": "rbi_afa_threshold_exceeded",
                    },
                },
            },
            dry_run=self.dry_run,
        ))

    def suppress_hard_decline(self, payment_id: str, amount_inr: float, decline_code,
                              rationale: str) -> RecoveryAction:
        """Card expired, mandate revoked, account closed, issuer hard
        decline. The instrument is gone for this cycle. The right outbound
        call is none, and recording that explicitly is what makes the
        no-contact auditable rather than merely absent.
        """
        if not is_hard_decline(decline_code):
            raise ComplianceViolation(
                f"{decline_code.value} is a soft decline: suppressing it forfeits a recoverable payment"
            )
        return self._emit(RecoveryAction(
            payment_id=payment_id,
            kind=ActionKind.SUPPRESS_NO_RETRY,
            amount_inr=amount_inr,
            reason=rationale,
            authorised_by="hard_decline_no_retry",
            provider=None,
            request=None,
            dry_run=self.dry_run,
        ))

    def escalate_manual(self, payment_id: str, amount_inr: float, decline_code,
                        rationale: str) -> RecoveryAction:
        """Retries exhausted without recovery. Nothing further is permitted
        on this mandate until the next cycle, so the case leaves the
        automated loop and goes to a human queue.
        """
        return self._emit(RecoveryAction(
            payment_id=payment_id,
            kind=ActionKind.ESCALATE_TO_MANUAL_REVIEW,
            amount_inr=amount_inr,
            reason=rationale,
            authorised_by="npci_retry_cap_exhausted",
            provider=None,
            request=None,
            dry_run=self.dry_run,
        ))

    # -- reporting --------------------------------------------------------

    def summary(self) -> dict:
        counts: dict[str, int] = {}
        value: dict[str, float] = {}
        for action in self.actions:
            counts[action.kind.value] = counts.get(action.kind.value, 0) + 1
            value[action.kind.value] = round(value.get(action.kind.value, 0.0) + action.amount_inr, 2)
        return {
            "total_actions": len(self.actions),
            "outbound_calls": sum(1 for a in self.actions if a.request is not None),
            "customer_contacts": sum(1 for a in self.actions if a.kind in CONTACT_ACTIONS),
            "by_kind": counts,
            "amount_inr_by_kind": value,
            "dry_run": self.dry_run,
        }

    def close(self) -> None:
        if self._file:
            self._file.close()
            self._file = None

    def __enter__(self) -> "RecoveryExecutor":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class DryRunExecutor(RecoveryExecutor):
    """Records the call, sends nothing. The only executor in this repo."""

    dry_run = True

    def _dispatch(self, action: RecoveryAction) -> None:
        return None


def render_curl(action: RecoveryAction) -> str:
    """Human-readable form of an HTTP action, for the demo and the docs.

    Shows `-u $RAZORPAY_KEY_ID:$RAZORPAY_KEY_SECRET` unexpanded on purpose:
    the printed command is meant to be readable in a screenshot and in a
    committed artifact, and a real key must never end up in either.
    """
    if action.request is None or "url" not in action.request:
        return f"# {action.kind.value}: no outbound call ({action.authorised_by})"
    body = json.dumps(action.request["body"], indent=2)
    return (
        f"curl -X {action.request['method']} {action.request['url']} \\\n"
        f"  -u $RAZORPAY_KEY_ID:$RAZORPAY_KEY_SECRET \\\n"
        f"  -H 'Content-Type: application/json' \\\n"
        f"  -d '{body}'"
    )
