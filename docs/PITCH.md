# Pitch script

Three minutes, spoken. Timings are cumulative. Say the numbers exactly as
written; every one of them is reproducible from `src/eval_harness.py`.

---

## 0:00 to 0:30 · The problem, for someone who has never heard of dunning

You subscribe to something in India. You do not type your card in every
month. You sign a mandate once, and after that the merchant asks the payment
rails to pull the money.

Sometimes that pull fails. No balance in the account that morning. The bank
did not answer. The card expired last week.

The customer has not cancelled. They still want the service. The revenue was
lost to plumbing, not to a decision. Recovering it is nearly pure margin,
which is why every payments company tries.

## 0:30 to 1:00 · Why India is a different problem

Here is what makes this hard, and what almost nobody builds for.

NPCI caps UPI AutoPay at four attempts per billing cycle. One original
execution plus three retries. A fifth attempt is not aggressive dunning, it
is non compliant.

The retry windows are fixed too. T plus 24 hours, T plus 72 hours, T plus 7
days. And above Rs 15,000 the RBI requires the customer to re authenticate,
so a silent background retry cannot legally clear at all. That limit rises
to Rs 1,00,000 for mutual funds, insurance and credit card bills.

Every public writeup on AI dunning is Stripe, Recurly, Chargebee. All US,
all card, all SaaS. None of them encode the NPCI cap, because it does not
apply to them. A generic clone does not work here.

## 1:00 to 1:30 · The insight

So here is the design decision the whole project rests on.

**Regulation decides when. The AI only decides how to reach you.**

Timing is not a choice we get to make, so we do not make it. What is left is
genuinely a decision, and nobody is currently making it: given four scarce
attempts, which payments should be touched at all, and how do you reach the
customer before each one.

A contextual bandit picks the channel, SMS or an IVR call, conditioned on
why the payment failed and which window we are in. An IVR call converts
better and costs about fifty times more. On a Rs 200 subscription it costs
more than the payment is worth. On a Rs 20,000 one it obviously does not.
The bandit will also skip an attempt entirely when the expected recovery
does not cover the message.

Say the strong version out loud: "our AI decides when to retry" invites the
question, what if it decides wrong. "Regulation decides when, the AI only
decides how to reach you" does not.

## 1:30 to 2:15 · Show it, do not describe it

Open the Simulator tab. Pick insufficient funds, Rs 4,200, subscription.
Press run.

Walk the five stages out loud as they appear. Classify, soft. Compliance
gate, cleared, under the authentication limit. Then the policy: for each
permitted window it samples its belief about each channel, converts that to
expected rupees net of cost, and takes the best. Point at the pulls column.
That is how much evidence it has.

Now change the amount to Rs 20,000 and run it again. The gate escalates
instead. Above the limit a silent retry cannot clear, so it issues an
authenticated payment link and leaves the mandate attempt budget untouched.

Now switch the category to mutual fund and run the same Rs 20,000 again. It
clears, because the enhanced limit for mutual funds is Rs 1,00,000.

Then the Live tab. Post a real Razorpay `subscription.pending` webhook body
from a terminal and let it land on the page. Same adapter, same gate, same
bandit, and the outbound call it would make written out in full.

## 2:15 to 2:45 · Does it actually work

The trap most entries fall into: build a bandit, beat a naive baseline in
your own simulator, declare victory. That is circular. The bandit's beliefs
are estimating the exact table the simulator samples from, so it must win
eventually. The claim proves nothing.

So there is a third policy with perfect information. Not deployable. It is
the ceiling.

Over 60 events across 200 independent seeds:

- Baseline, retry everything by SMS: 37.16 percent recovered, Rs 261,660 net
- This project: 39.11 percent, Rs 273,858 net
- Oracle, perfect information: 40.78 percent, Rs 285,540 net

The bandit captures **51.1 percent of the achievable lift**. Paired lift over
baseline is **plus Rs 12,197 per batch**, 95 percent confidence interval plus
6,330 to plus 18,065. It beat the baseline in **126 of 200 batches**.

Not always. Say that part out loud. A number that is visibly not perfect is
more credible than a round win.

## 2:45 to 3:00 · The limitation, said first

One honest limitation before anyone finds it.

The recovery probabilities are simulation assumptions, clearly labelled as
assumptions in `synthetic_data.py`, because real Razorpay outcome data is
not public. The structure is sourced regulation: the soft and hard split,
the four attempt cap, the windows, the authentication thresholds. Those live
in a separate file, `domain_rules.py`, deliberately. Mixing sourced facts
with modelling assumptions would make the whole evaluation unfalsifiable.

Close on this: if a regulator asks a merchant to prove they never exceeded
four attempts, right now they cannot. Razorpay does not disclose the retry
count. With this, it is one file.

---

## If you get questions

**"Is the outbound path real?"** It builds the exact call and does not send
it. There is no code path in the repository that transmits, which is why it
ships with no credentials. The executor also re derives compliance
authorisation itself instead of trusting the policy, and raises rather than
sending anything the rails forbid.

**"Where is the AI?"** Two places, and only two. The bandit picks the
channel. A language model writes the plain English explanation of each
decision, live, and writes language only. It is handed a decision that is
already made. It never chooses and is never parsed for a number.

**"What did you get wrong?"** The best answer available, and it is true: an
adversarial review found that the RBI authentication check was defined and
never called. The project was claiming compliance while payments up to Rs
25,000 went straight through the retry path. Wiring it in correctly dropped
the headline from 63 percent of ceiling to 51.1. Recovering less money
legally is the right outcome.
