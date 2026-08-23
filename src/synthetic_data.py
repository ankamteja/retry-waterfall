"""
Synthetic failed-payment batch generator + the recovery-probability model
used to simulate outcomes. These probabilities are simulation assumptions
for the eval harness, not sourced regulation (see domain_rules.py for the
actual NPCI/RBI numbers).
"""

import random
from dataclasses import dataclass

from domain_rules import DeclineCode, SOFT_DECLINES, HARD_DECLINES

CATEGORIES = ["subscription", "mutual_fund", "insurance", "credit_card_bill"]

# Base probability a soft decline recovers on a given retry, before
# window/channel effects. Hard declines are never retried (0.0, enforced
# by domain_rules.is_hard_decline, not by this model).
BASE_RECOVERY_PROB = {
    DeclineCode.INSUFFICIENT_FUNDS: 0.25,
    DeclineCode.BANK_SERVER_TIMEOUT: 0.55,
    DeclineCode.ISSUER_SOFT_DECLINE: 0.20,
}

# Multiplier by how many hours since original failure the retry lands.
# insufficient_funds improves the longer you wait (salary/balance cycles);
# bank_server_timeout is a transient blip, doesn't care about window;
# issuer_soft_decline improves moderately with a nudge to act.
WINDOW_MULTIPLIER = {
    DeclineCode.INSUFFICIENT_FUNDS: {24: 0.6, 72: 1.0, 168: 1.4},
    DeclineCode.BANK_SERVER_TIMEOUT: {24: 1.0, 72: 1.0, 168: 0.95},
    DeclineCode.ISSUER_SOFT_DECLINE: {24: 0.8, 72: 1.0, 168: 1.1},
}

# Multiplier by contact channel. Two channels only, kept deliberately small
# so the bandit's (context x window x channel) arm space fits the n=60
# sample budget (see docs/plan.md eval-design note). IVR is the more
# persuasive nudge but the more expensive one (see CHANNEL_COST_INR in
# policies.py) -- that cost/effectiveness tradeoff is exactly what the
# bandit has to learn.
CHANNEL_MULTIPLIER = {
    DeclineCode.INSUFFICIENT_FUNDS: {"sms": 1.0, "ivr_call": 1.15},
    DeclineCode.BANK_SERVER_TIMEOUT: {"sms": 1.0, "ivr_call": 1.05},
    DeclineCode.ISSUER_SOFT_DECLINE: {"sms": 0.9, "ivr_call": 1.3},
}


@dataclass(frozen=True)
class FailedPaymentEvent:
    payment_id: str
    amount_inr: float
    category: str
    decline_code: DeclineCode


def generate_batch(n: int = 60, decline_distribution: dict[DeclineCode, float] | None = None,
                    seed: int | None = None) -> list[FailedPaymentEvent]:
    """
    n: batch size.
    decline_distribution: DeclineCode -> weight. Defaults to a realistic mix
        skewed toward soft declines (most failed recurring payments are
        transient), with hard declines a meaningful minority so the eval
        exercises the compliance no-retry path too.
    """
    rng = random.Random(seed)
    if decline_distribution is None:
        decline_distribution = {
            DeclineCode.INSUFFICIENT_FUNDS: 0.35,
            DeclineCode.BANK_SERVER_TIMEOUT: 0.15,
            DeclineCode.ISSUER_SOFT_DECLINE: 0.15,
            DeclineCode.CARD_EXPIRED: 0.15,
            DeclineCode.MANDATE_REVOKED: 0.10,
            DeclineCode.ACCOUNT_CLOSED: 0.05,
            DeclineCode.ISSUER_HARD_DECLINE: 0.05,
        }
    codes = list(decline_distribution.keys())
    weights = list(decline_distribution.values())

    events = []
    for i in range(n):
        code = rng.choices(codes, weights=weights, k=1)[0]
        category = rng.choice(CATEGORIES)
        amount = round(rng.uniform(200, 25_000), 2)
        events.append(FailedPaymentEvent(
            payment_id=f"pay_{i:04d}",
            amount_inr=amount,
            category=category,
            decline_code=code,
        ))
    return events


def recovery_probability(decline_code: DeclineCode, window_hours: int, channel: str) -> float:
    if decline_code in HARD_DECLINES:
        return 0.0
    base = BASE_RECOVERY_PROB[decline_code]
    window_mult = WINDOW_MULTIPLIER[decline_code][window_hours]
    channel_mult = CHANNEL_MULTIPLIER[decline_code][channel]
    return min(base * window_mult * channel_mult, 0.95)
