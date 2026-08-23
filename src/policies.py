"""
Retry policies. Retry *timing* always follows the NPCI-mandated schedule
(RETRY_WINDOWS_HOURS) -- that's compliance, not a decision a policy gets to
make. What each policy actually controls: which channel to contact the
customer through at each scheduled retry, and whether it's even worth
attempting (expected net value can be negative for a low-value payment on
the expensive channel).

Three policies, compared head to head in eval_harness.py:
- BaselinePolicy   -- naive blind retry: always SMS, no personalization.
- BanditPolicy     -- Thompson Sampling per (decline_code, window_step)
                      context, cost-aware arm choice.
- OraclePolicy     -- perfect information (knows synthetic_data's true
                      probabilities). Upper bound the bandit is scored against.
"""

import random
from dataclasses import dataclass
from enum import Enum

from domain_rules import DeclineCode
from synthetic_data import recovery_probability

CHANNEL_COST_INR = {
    "sms": 0.15,
    "ivr_call": 8.0,
}


class Channel(str, Enum):
    SMS = "sms"
    IVR_CALL = "ivr_call"


CHANNELS = [Channel.SMS.value, Channel.IVR_CALL.value]


@dataclass
class Decision:
    """What a policy returns for one scheduled retry attempt."""
    should_attempt: bool
    channel: str | None
    expected_net_inr: float | None
    rationale: str


class BaselinePolicy:
    """Naive blind retry: same channel for everyone, every attempt, no
    expected-value check. This is the thing the track's bar text warns
    about ("one cherry-picked match proves nothing") -- it's the strawman
    the bandit has to beat."""

    name = "baseline"

    def choose(self, decline_code: DeclineCode, window_hours: int, amount_inr: float) -> Decision:
        return Decision(
            should_attempt=True,
            channel=Channel.SMS.value,
            expected_net_inr=None,
            rationale="fixed policy: always SMS",
        )

    def update(self, decline_code: DeclineCode, window_hours: int, channel: str, recovered: bool) -> None:
        pass


class BanditPolicy:
    """Thompson Sampling, one Beta(alpha, beta) posterior per
    (decline_code, window_hours, channel) arm. At decision time: sample a
    success probability per channel, convert to expected net INR using the
    payment amount and channel cost, act on whichever channel wins -- and
    skip the attempt entirely if both are expected-negative."""

    name = "bandit"

    def __init__(self, seed: int | None = None):
        self._rng = random.Random(seed)
        self._alpha: dict[tuple, float] = {}
        self._beta: dict[tuple, float] = {}

    def _key(self, decline_code: DeclineCode, window_hours: int, channel: str) -> tuple:
        return (decline_code, window_hours, channel)

    def _sample_p(self, decline_code: DeclineCode, window_hours: int, channel: str) -> float:
        key = self._key(decline_code, window_hours, channel)
        a = self._alpha.get(key, 1.0)  # Beta(1,1) = uniform prior, no bias assumed
        b = self._beta.get(key, 1.0)
        return self._rng.betavariate(a, b)

    def choose(self, decline_code: DeclineCode, window_hours: int, amount_inr: float) -> Decision:
        best_channel = None
        best_net = None
        for channel in CHANNELS:
            p = self._sample_p(decline_code, window_hours, channel)
            net = p * amount_inr - CHANNEL_COST_INR[channel]
            if best_net is None or net > best_net:
                best_net = net
                best_channel = channel

        if best_net <= 0:
            return Decision(
                should_attempt=False,
                channel=None,
                expected_net_inr=best_net,
                rationale=f"skipped: best sampled expected net ({best_net:.2f} INR via {best_channel}) <= 0",
            )
        return Decision(
            should_attempt=True,
            channel=best_channel,
            expected_net_inr=best_net,
            rationale=f"thompson sample picked {best_channel}, expected net {best_net:.2f} INR",
        )

    def update(self, decline_code: DeclineCode, window_hours: int, channel: str, recovered: bool) -> None:
        key = self._key(decline_code, window_hours, channel)
        if recovered:
            self._alpha[key] = self._alpha.get(key, 1.0) + 1.0
        else:
            self._beta[key] = self._beta.get(key, 1.0) + 1.0


class OraclePolicy:
    """Perfect information: reads the true recovery_probability() from
    synthetic_data directly instead of learning it. Not a real deployable
    policy -- it's the upper bound the bandit's learning is scored against,
    so "bandit captures N% of achievable lift" is an honest claim instead
    of a bare win/loss."""

    name = "oracle"

    def choose(self, decline_code: DeclineCode, window_hours: int, amount_inr: float) -> Decision:
        best_channel = None
        best_net = None
        for channel in CHANNELS:
            p = recovery_probability(decline_code, window_hours, channel)
            net = p * amount_inr - CHANNEL_COST_INR[channel]
            if best_net is None or net > best_net:
                best_net = net
                best_channel = channel

        if best_net <= 0:
            return Decision(
                should_attempt=False,
                channel=None,
                expected_net_inr=best_net,
                rationale=f"skipped: true expected net ({best_net:.2f} INR via {best_channel}) <= 0",
            )
        return Decision(
            should_attempt=True,
            channel=best_channel,
            expected_net_inr=best_net,
            rationale=f"true-probability best channel {best_channel}, expected net {best_net:.2f} INR",
        )

    def update(self, decline_code: DeclineCode, window_hours: int, channel: str, recovered: bool) -> None:
        pass
