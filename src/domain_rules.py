"""
Real payment-rail rules this project encodes, so the stopping/escalation
logic is grounded in actual regulation, not invented.

Primary source for the RBI rules below: the **Digital Payments -- E-mandate
Framework, 2026**, notified 21 April 2026, which consolidates the earlier
e-mandate circulars into one set of directions. Naming the framework rather
than the date this file was last read is deliberate: a date says only when
someone looked, and a reader cannot check it.

- NPCI AutoPay/e-mandate retry cap: max 4 total attempts per cycle
  (1 original execution + 3 retries), effective Aug 2025. This one is an
  NPCI operating rule rather than an RBI direction, and the circular number
  is not cited here because it has not been located -- treat it as the
  weakest-sourced constant in this file.
- Retry windows: T+24h, T+72h, T+7d (then cycle marked failed, no penalty
  from bank/NPCI).
- RBI additional-factor-auth (AFA) trigger: mandatory above INR 15,000
  general recurring payments; INR 1,00,000 enhanced limit carve-out for
  insurance premiums, mutual fund subscriptions and credit-card bill
  payments -- those three categories only.
- Pre-debit notification: the framework requires a pre-transaction
  notification at least 24 hours before the debit, carrying the merchant
  name, amount, date and time, the mandate reference and the reason, plus a
  post-transaction notification afterwards. PRE_DEBIT_NOTICE_HOURS encodes
  the 24 hours, and recovery_actions refuses to contact on an attempt with
  no notice on record.

These are the numbers a generic "AI dunning bot" clone won't bother
encoding — most public dunning writeups (Stripe/Recurly/Chargebee) are
US/SaaS-card-centric and don't reflect India's NPCI mandate caps.
"""

from enum import Enum


class DeclineCode(str, Enum):
    """
    Soft = transient, retrying later can succeed.
    Hard = permanent for this cycle, retrying is pointless / non-compliant.
    """
    INSUFFICIENT_FUNDS = "insufficient_funds"      # soft
    BANK_SERVER_TIMEOUT = "bank_server_timeout"     # soft
    ISSUER_SOFT_DECLINE = "issuer_soft_decline"      # soft (do-not-honor, retry-able)
    CARD_EXPIRED = "card_expired"                    # hard
    MANDATE_REVOKED = "mandate_revoked"               # hard
    ACCOUNT_CLOSED = "account_closed"                 # hard
    ISSUER_HARD_DECLINE = "issuer_hard_decline"        # hard (lost/stolen, blocked)


SOFT_DECLINES = {
    DeclineCode.INSUFFICIENT_FUNDS,
    DeclineCode.BANK_SERVER_TIMEOUT,
    DeclineCode.ISSUER_SOFT_DECLINE,
}

HARD_DECLINES = {
    DeclineCode.CARD_EXPIRED,
    DeclineCode.MANDATE_REVOKED,
    DeclineCode.ACCOUNT_CLOSED,
    DeclineCode.ISSUER_HARD_DECLINE,
}

MAX_ATTEMPTS_PER_CYCLE = 4          # 1 original + 3 retries (NPCI AutoPay cap)
RETRY_WINDOWS_HOURS = [24, 72, 168]  # T+24h, T+72h, T+7d

AFA_TRIGGER_GENERAL_INR = 15_000
AFA_TRIGGER_ENHANCED_INR = 1_00_000
AFA_ENHANCED_CATEGORIES = {"mutual_fund", "insurance", "credit_card_bill"}

# The pre-debit notice is owed before a collection *attempt*, not before every
# window the schedule permits. A window the agent declines to use produces no
# debit, so it owes no notice -- which keeps declining to attempt a free
# action, as it should be.
PRE_DEBIT_NOTICE_HOURS = 24


def requires_additional_factor_auth(amount_inr: float, category: str) -> bool:
    limit = AFA_TRIGGER_ENHANCED_INR if category in AFA_ENHANCED_CATEGORIES else AFA_TRIGGER_GENERAL_INR
    return amount_inr > limit


def is_hard_decline(code: DeclineCode) -> bool:
    return code in HARD_DECLINES
