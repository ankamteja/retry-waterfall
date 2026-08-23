"""
Real payment-rail rules this project encodes, so the stopping/escalation
logic is grounded in actual regulation, not invented.

Sources (checked 2026-08-22):
- NPCI AutoPay/e-mandate retry cap: max 4 total attempts per cycle
  (1 original execution + 3 retries), effective Aug 2025.
- Retry windows: T+24h, T+72h, T+7d (then cycle marked failed, no penalty
  from bank/NPCI).
- RBI additional-factor-auth (AFA) trigger: mandatory above INR 15,000
  general recurring payments; INR 1,00,000 enhanced limit carve-out for
  mutual funds / insurance / credit-card bill payments.
- Standard e-mandate pre-debit notification: customer must be notified
  ahead of the debit (RBI's 2019 recurring-payment framework requires
  advance notice before an auto-debit executes).

These are the numbers a generic "AI dunning bot" clone won't bother
encoding — most public dunning writeups (Stripe/Recurly/Chargebee) are
US/SaaS-card-centric and don't reflect India's NPCI mandate caps.
"""

from dataclasses import dataclass
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


def requires_additional_factor_auth(amount_inr: float, category: str) -> bool:
    limit = AFA_TRIGGER_ENHANCED_INR if category in AFA_ENHANCED_CATEGORIES else AFA_TRIGGER_GENERAL_INR
    return amount_inr > limit


def is_hard_decline(code: DeclineCode) -> bool:
    return code in HARD_DECLINES


@dataclass(frozen=True)
class MandateContext:
    amount_inr: float
    category: str  # e.g. "subscription", "mutual_fund", "insurance", "credit_card_bill"
    attempt_number: int  # 1..MAX_ATTEMPTS_PER_CYCLE

    def attempts_exhausted(self) -> bool:
        return self.attempt_number >= MAX_ATTEMPTS_PER_CYCLE
