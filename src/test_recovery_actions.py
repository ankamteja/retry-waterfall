"""
Tests for the outbound execution path.

The point of most of these is not that the happy path works. It is that the
executor refuses to make a call the payment rails forbid, even when a caller
explicitly asks it to. Those are the tests that would fail if a future
policy change quietly started contacting customers it should not.

No network, no credentials, no arguments: python3 test_recovery_actions.py
"""

import json
import os
import tempfile
import threading
import unittest

from domain_rules import DeclineCode, MAX_ATTEMPTS_PER_CYCLE
from recovery_actions import (
    ActionKind,
    ComplianceViolation,
    ContactLedger,
    DryRunExecutor,
    RecoveryExecutor,
    _paise,
    render_curl,
)


def contacts(ex):
    """The agent's own contacts, without the notice the law adds to each.

    Counting raw actions would make every cap assertion below double when
    the mandated notice started being emitted, which is a fact about the
    law rather than about the cap.
    """
    return [a for a in ex.actions if a.kind is not ActionKind.SEND_PRE_DEBIT_ALERT]


def notified(ex, payment_id, amount_inr, category, decline_code,
             attempt_number, window_hours, channel, rationale="x"):
    """Send the mandated notice, then contact -- the legal order.

    Most of these tests are about the cap, the channel or the payload, not
    about the notice, so they go through this rather than restating the two
    calls every time.
    """
    ex.notify_pre_debit(payment_id, amount_inr, category, decline_code,
                        attempt_number, window_hours)
    return ex.contact(payment_id, amount_inr, category, decline_code,
                      attempt_number, window_hours, channel, rationale)


class TestGuards(unittest.TestCase):
    """Every one of these is a call the executor must refuse."""

    def setUp(self):
        self.ex = DryRunExecutor()

    def test_hard_decline_cannot_be_contacted(self):
        with self.assertRaises(ComplianceViolation):
            self.ex.contact("pay_1", 500.0, "subscription", DeclineCode.MANDATE_REVOKED,
                            attempt_number=2, window_hours=24, channel="sms", rationale="x")

    def test_over_afa_threshold_cannot_be_silently_retried(self):
        """The bug an earlier review caught, now nailed down at the outbound
        edge: a INR 20,000 subscription is above the RBI general AFA limit,
        so no silent contact-and-retry is permitted for it."""
        with self.assertRaises(ComplianceViolation):
            self.ex.contact("pay_2", 20_000.0, "subscription", DeclineCode.INSUFFICIENT_FUNDS,
                            attempt_number=2, window_hours=24, channel="sms", rationale="x")

    def test_enhanced_category_raises_the_afa_ceiling(self):
        """Same amount, mutual fund: the RBI enhanced limit is INR 1,00,000,
        so this one is permitted. If this test and the previous one ever
        agree, the category carve-out has been lost."""
        action = notified(self.ex, "pay_3", 20_000.0, "mutual_fund", DeclineCode.INSUFFICIENT_FUNDS,
                                 attempt_number=2, window_hours=24, channel="sms", rationale="x")
        self.assertEqual(action.kind, ActionKind.SEND_SMS)

    def test_attempt_beyond_npci_cap_is_refused(self):
        with self.assertRaises(ComplianceViolation):
            self.ex.contact("pay_4", 500.0, "subscription", DeclineCode.INSUFFICIENT_FUNDS,
                            attempt_number=MAX_ATTEMPTS_PER_CYCLE + 1, window_hours=24,
                            channel="sms", rationale="x")

    def test_attempt_below_the_first_retry_is_refused(self):
        """Attempt 1 is the original debit, so a contact cannot belong to it,
        and 0 and -1 are not attempts at all. An unbounded lower end let all
        three through while still reading as 'within the cap'."""
        for attempt in (1, 0, -1):
            with self.subTest(attempt=attempt), self.assertRaises(ComplianceViolation):
                self.ex.contact("pay_low", 500.0, "subscription",
                                DeclineCode.INSUFFICIENT_FUNDS, attempt_number=attempt,
                                window_hours=24, channel="sms", rationale="x")

    def test_cap_binds_across_calls_on_one_payment(self):
        """The cap is a per-payment fact. A guard that only validates the
        attempt number it is handed enforces nothing, because the caller
        picks that number -- this used to allow unlimited contacts."""
        for attempt, window in zip((2, 3, 4), (24, 72, 168)):
            notified(self.ex, "pay_cap", 500.0, "subscription", DeclineCode.INSUFFICIENT_FUNDS,
                            attempt_number=attempt, window_hours=window,
                            channel="sms", rationale="x")
        self.assertEqual(len(contacts(self.ex)), 3)
        with self.assertRaises(ComplianceViolation):
            notified(self.ex, "pay_cap", 500.0, "subscription", DeclineCode.INSUFFICIENT_FUNDS,
                            attempt_number=4, window_hours=168, channel="sms", rationale="x")
        self.assertEqual(len(contacts(self.ex)), 3)

    def test_the_same_attempt_cannot_be_contacted_twice(self):
        """Contacting attempt 2 twice is two contacts against a three-contact
        cap. Same window both times, so this tests the duplicate rule and not
        the attempt-to-window pairing."""
        notified(self.ex, "pay_dup", 500.0, "subscription", DeclineCode.INSUFFICIENT_FUNDS,
                        attempt_number=2, window_hours=24, channel="sms", rationale="x")
        with self.assertRaises(ComplianceViolation):
            notified(self.ex, "pay_dup", 500.0, "subscription", DeclineCode.INSUFFICIENT_FUNDS,
                            attempt_number=2, window_hours=24, channel="sms", rationale="x")

    def test_contact_without_a_pre_debit_notice_is_refused(self):
        """The framework owes the customer notice before the debit. This was
        written in the domain_rules docstring and enforced nowhere, so every
        contact in the system went out without one."""
        with self.assertRaises(ComplianceViolation):
            self.ex.contact("pay_notice", 500.0, "subscription",
                            DeclineCode.INSUFFICIENT_FUNDS, attempt_number=2,
                            window_hours=24, channel="sms", rationale="x")
        self.assertEqual(len(self.ex.actions), 0)

    def test_the_notice_permits_only_its_own_attempt(self):
        """Notice for attempt 2 does not authorise a contact on attempt 3.
        Otherwise one notice would cover a whole cycle of debits."""
        self.ex.notify_pre_debit("pay_n2", 500.0, "subscription",
                                 DeclineCode.INSUFFICIENT_FUNDS, 2, 24)
        self.ex.contact("pay_n2", 500.0, "subscription", DeclineCode.INSUFFICIENT_FUNDS,
                        attempt_number=2, window_hours=24, channel="sms", rationale="x")
        with self.assertRaises(ComplianceViolation):
            self.ex.contact("pay_n2", 500.0, "subscription", DeclineCode.INSUFFICIENT_FUNDS,
                            attempt_number=3, window_hours=72, channel="sms", rationale="x")

    def test_no_notice_is_owed_on_a_hard_decline(self):
        """A notice announces a debit. Sending one where no collection will
        follow tells the customer their account is about to be charged when
        it is not."""
        with self.assertRaises(ComplianceViolation):
            self.ex.notify_pre_debit("pay_n3", 500.0, "subscription",
                                     DeclineCode.MANDATE_REVOKED, 2, 24)

    def test_the_notice_does_not_spend_a_retry(self):
        """Reaching a customer and spending a regulated attempt are not the
        same thing. If the notice counted against the cap, a legal obligation
        would eat a legal retry."""
        for attempt, window in zip((2, 3, 4), (24, 72, 168)):
            notified(self.ex, "pay_n4", 500.0, "subscription",
                     DeclineCode.INSUFFICIENT_FUNDS, attempt, window, "sms")
        self.assertEqual(self.ex.ledger.attempts_for("pay_n4"), [2, 3, 4])
        self.assertEqual(len(contacts(self.ex)), 3)
        self.assertEqual(len(self.ex.actions), 6)

    def test_attempt_must_match_its_mandated_window(self):
        """NPCI fixes which attempt lands in which window, so the pair is one
        fact, not two. Checked separately, a 2nd attempt could claim the T+7d
        window: a retry outside the schedule wearing a legal label."""
        with self.assertRaises(ComplianceViolation):
            self.ex.contact("pay_pair", 500.0, "subscription", DeclineCode.INSUFFICIENT_FUNDS,
                            attempt_number=2, window_hours=168, channel="sms", rationale="x")
        with self.assertRaises(ComplianceViolation):
            self.ex.contact("pay_pair", 500.0, "subscription", DeclineCode.INSUFFICIENT_FUNDS,
                            attempt_number=4, window_hours=24, channel="sms", rationale="x")
        self.assertEqual(len(contacts(self.ex)), 0)

    def test_a_shared_ledger_binds_across_executors(self):
        """The cap is per payment per cycle, but an executor is per request.
        Without a shared ledger the demo path resets the count on every
        request, so the original defect -- unlimited contacts for one
        payment -- is still reachable, just one HTTP call at a time."""
        ledger = ContactLedger()
        emitted = 0
        for attempt, window in ((2, 24), (3, 72), (4, 168), (4, 168)):
            per_request = DryRunExecutor(ledger=ledger)
            try:
                notified(per_request, "pay_shared", 500.0, "subscription",
                                    DeclineCode.INSUFFICIENT_FUNDS, attempt_number=attempt,
                                    window_hours=window, channel="sms", rationale="x")
                emitted += 1
            except ComplianceViolation:
                pass
        self.assertEqual(emitted, 3)
        self.assertEqual(ledger.attempts_for("pay_shared"), [2, 3, 4])

    def test_concurrent_calls_cannot_oversend(self):
        """Check-then-record is not enough under a ThreadingHTTPServer: four
        threads can each read an empty ledger before any of them writes, and
        all four pass a check that was true when they read it."""
        ledger = ContactLedger()
        errors, sent = [], []

        def fire():
            ex = DryRunExecutor(ledger=ledger)
            try:
                notified(ex, "pay_race", 500.0, "subscription", DeclineCode.INSUFFICIENT_FUNDS,
                           attempt_number=2, window_hours=24, channel="sms", rationale="x")
                sent.append(1)
            except ComplianceViolation:
                errors.append(1)

        threads = [threading.Thread(target=fire) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(sent), 1)
        self.assertEqual(len(errors), 7)
        self.assertEqual(ledger.attempts_for("pay_race"), [2])

    def test_a_failed_dispatch_gives_the_attempt_back(self):
        """A reservation taken and then not used would burn a legal retry
        forever: the payment could never be contacted on that attempt again
        because the ledger says it already was."""
        class Failing(DryRunExecutor):
            def _dispatch(self, action):
                raise RuntimeError("comms provider is down")

        ex = Failing()
        with self.assertRaises(RuntimeError):
            notified(ex, "pay_retry", 500.0, "subscription", DeclineCode.INSUFFICIENT_FUNDS,
                       attempt_number=2, window_hours=24, channel="sms", rationale="x")
        self.assertEqual(ex.ledger.attempts_for("pay_retry"), [])

        ok = DryRunExecutor(ledger=ex.ledger)
        notified(ok, "pay_retry", 500.0, "subscription", DeclineCode.INSUFFICIENT_FUNDS,
                   attempt_number=2, window_hours=24, channel="sms", rationale="x")
        self.assertEqual(len(contacts(ok)), 1)

    def test_attempts_do_not_move_backwards(self):
        notified(self.ex, "pay_back", 500.0, "subscription", DeclineCode.INSUFFICIENT_FUNDS,
                        attempt_number=3, window_hours=72, channel="sms", rationale="x")
        with self.assertRaises(ComplianceViolation):
            notified(self.ex, "pay_back", 500.0, "subscription", DeclineCode.INSUFFICIENT_FUNDS,
                            attempt_number=2, window_hours=24, channel="sms", rationale="x")

    def test_a_refused_contact_does_not_consume_an_attempt(self):
        """A blocked call must not spend one of the payment's three slots,
        or a bad channel would silently cost a legitimate retry."""
        with self.assertRaises(ComplianceViolation):
            notified(self.ex, "pay_free", 500.0, "subscription", DeclineCode.INSUFFICIENT_FUNDS,
                            attempt_number=2, window_hours=24, channel="whatsapp", rationale="x")
        for attempt, window in zip((2, 3, 4), (24, 72, 168)):
            notified(self.ex, "pay_free", 500.0, "subscription", DeclineCode.INSUFFICIENT_FUNDS,
                            attempt_number=attempt, window_hours=window,
                            channel="sms", rationale="x")
        self.assertEqual(len(contacts(self.ex)), 3)

    def test_the_cap_is_per_payment_not_global(self):
        for payment in ("pay_a", "pay_b", "pay_c", "pay_d"):
            notified(self.ex, payment, 500.0, "subscription", DeclineCode.INSUFFICIENT_FUNDS,
                            attempt_number=2, window_hours=24, channel="sms", rationale="x")
        self.assertEqual(len(contacts(self.ex)), 4)

    def test_unmandated_retry_window_is_refused(self):
        """T+48h is not one of NPCI's windows. A policy inventing its own
        schedule is exactly what this project claims never to do."""
        with self.assertRaises(ComplianceViolation):
            self.ex.contact("pay_5", 500.0, "subscription", DeclineCode.INSUFFICIENT_FUNDS,
                            attempt_number=2, window_hours=48, channel="sms", rationale="x")

    def test_unknown_channel_is_refused(self):
        with self.assertRaises(ComplianceViolation):
            self.ex.contact("pay_6", 500.0, "subscription", DeclineCode.INSUFFICIENT_FUNDS,
                            attempt_number=2, window_hours=24, channel="whatsapp", rationale="x")

    def test_auth_link_refused_below_threshold(self):
        """An auth link on a payment that does not need one asks the customer
        to re-authenticate for no reason, and skips a retry that was allowed."""
        with self.assertRaises(ComplianceViolation):
            self.ex.escalate_afa("pay_7", 500.0, "subscription",
                                 DeclineCode.INSUFFICIENT_FUNDS, rationale="x")

    def test_auth_link_refused_on_dead_mandate(self):
        with self.assertRaises(ComplianceViolation):
            self.ex.escalate_afa("pay_8", 50_000.0, "subscription",
                                 DeclineCode.MANDATE_REVOKED, rationale="x")

    def test_suppressing_a_soft_decline_is_refused(self):
        """Forfeiting a recoverable payment is a defect in the other
        direction, and the guard catches it too."""
        with self.assertRaises(ComplianceViolation):
            self.ex.suppress_hard_decline("pay_9", 500.0,
                                          DeclineCode.INSUFFICIENT_FUNDS, rationale="x")

    def test_refused_calls_emit_nothing(self):
        for call in (
            lambda: self.ex.contact("p", 500.0, "subscription", DeclineCode.CARD_EXPIRED,
                                    2, 24, "sms", "x"),
            lambda: self.ex.escalate_afa("p", 500.0, "subscription",
                                         DeclineCode.INSUFFICIENT_FUNDS, "x"),
        ):
            with self.assertRaises(ComplianceViolation):
                call()
        self.assertEqual(self.ex.actions, [])


class TestActions(unittest.TestCase):

    def setUp(self):
        self.ex = DryRunExecutor()

    def test_channel_maps_to_action_kind(self):
        sms = notified(self.ex, "p1", 500.0, "subscription", DeclineCode.INSUFFICIENT_FUNDS,
                              2, 24, "sms", "x")
        ivr = notified(self.ex, "p2", 500.0, "subscription", DeclineCode.INSUFFICIENT_FUNDS,
                              2, 24, "ivr_call", "x")
        self.assertEqual(sms.kind, ActionKind.SEND_SMS)
        self.assertEqual(ivr.kind, ActionKind.PLACE_IVR_CALL)

    def test_contact_payload_carries_no_pii(self):
        """The batch has no phone numbers. Inventing one to make the payload
        look finished would put fake customer data in a committed artifact."""
        action = notified(self.ex, "p1", 500.0, "subscription", DeclineCode.INSUFFICIENT_FUNDS,
                                 2, 24, "sms", "x")
        blob = json.dumps(action.request)
        self.assertNotIn("phone", blob)
        self.assertNotIn("+91", blob)

    def test_auth_link_is_a_razorpay_call_in_paise(self):
        action = self.ex.escalate_afa("p1", 20_000.0, "subscription",
                                      DeclineCode.INSUFFICIENT_FUNDS, "x")
        self.assertEqual(action.provider, "razorpay")
        self.assertEqual(action.request["method"], "POST")
        self.assertTrue(action.request["url"].endswith("/payment_links"))
        self.assertEqual(action.request["body"]["amount"], 2_000_000)
        self.assertEqual(action.request["body"]["currency"], "INR")

    def test_paise_rounds_rather_than_truncates(self):
        """int(8399.99 * 100) is 8399998 / 10 short in float; round is not."""
        self.assertEqual(_paise(8399.99), 839999)
        self.assertEqual(_paise(0.07), 7)

    def test_no_retry_branches_make_no_outbound_call(self):
        suppressed = self.ex.suppress_hard_decline("p1", 500.0, DeclineCode.CARD_EXPIRED, "x")
        manual = self.ex.escalate_manual("p2", 500.0, DeclineCode.INSUFFICIENT_FUNDS, "x")
        self.assertIsNone(suppressed.request)
        self.assertIsNone(manual.request)
        self.assertIsNone(suppressed.provider)

    def test_every_action_names_the_rule_that_authorised_it(self):
        notified(self.ex, "p1", 500.0, "subscription", DeclineCode.INSUFFICIENT_FUNDS, 2, 24, "sms", "x")
        self.ex.escalate_afa("p2", 20_000.0, "subscription", DeclineCode.INSUFFICIENT_FUNDS, "x")
        self.ex.suppress_hard_decline("p3", 500.0, DeclineCode.CARD_EXPIRED, "x")
        self.ex.escalate_manual("p4", 500.0, DeclineCode.INSUFFICIENT_FUNDS, "x")
        for action in self.ex.actions:
            self.assertTrue(action.authorised_by)

    def test_dry_run_is_the_default_and_nothing_dispatches(self):
        action = notified(self.ex, "p1", 500.0, "subscription", DeclineCode.INSUFFICIENT_FUNDS,
                                 2, 24, "sms", "x")
        self.assertTrue(action.dry_run)
        self.assertTrue(self.ex.dry_run)

    def test_base_executor_refuses_to_send(self):
        """The seam is deliberately unimplemented: there is no code path in
        this repository that transmits anything."""
        with self.assertRaises(NotImplementedError):
            RecoveryExecutor()._dispatch(None)


class TestOutputs(unittest.TestCase):

    def test_summary_counts_contacts_separately_from_actions(self):
        ex = DryRunExecutor()
        notified(ex, "p1", 500.0, "subscription", DeclineCode.INSUFFICIENT_FUNDS, 2, 24, "sms", "x")
        ex.escalate_afa("p2", 20_000.0, "subscription", DeclineCode.INSUFFICIENT_FUNDS, "x")
        ex.suppress_hard_decline("p3", 500.0, DeclineCode.CARD_EXPIRED, "x")
        summary = ex.summary()
        # Four: the mandated notice, the SMS it precedes, the auth link and
        # the suppression. The suppression is the only one that reaches
        # nobody, which is what these three counts are separating.
        self.assertEqual(summary["total_actions"], 4)
        self.assertEqual(summary["outbound_calls"], 3)
        self.assertEqual(summary["customer_contacts"], 3)
        self.assertTrue(summary["dry_run"])

    def test_jsonl_is_one_parseable_line_per_action(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "actions.jsonl")
            with DryRunExecutor(path) as ex:
                notified(ex, "p1", 500.0, "subscription", DeclineCode.INSUFFICIENT_FUNDS, 2, 24, "sms", "x")
                ex.suppress_hard_decline("p2", 500.0, DeclineCode.CARD_EXPIRED, "x")
            with open(path) as f:
                rows = [json.loads(line) for line in f]
        self.assertEqual(len(rows), 3)
        # The notice is written before the contact it announces, so the file
        # reads in the order the customer experienced it.
        self.assertEqual(rows[0]["kind"], "send_pre_debit_alert")
        self.assertEqual(rows[1]["kind"], "send_sms")
        self.assertEqual(rows[2]["kind"], "suppress_no_retry")

    def test_render_curl_never_prints_a_real_credential(self):
        ex = DryRunExecutor()
        action = ex.escalate_afa("p1", 20_000.0, "subscription", DeclineCode.INSUFFICIENT_FUNDS, "x")
        rendered = render_curl(action)
        self.assertIn("$RAZORPAY_KEY_ID:$RAZORPAY_KEY_SECRET", rendered)
        self.assertIn("payment_links", rendered)

    def test_render_curl_says_so_when_there_is_no_call(self):
        ex = DryRunExecutor()
        action = ex.suppress_hard_decline("p1", 500.0, DeclineCode.CARD_EXPIRED, "x")
        self.assertIn("no outbound call", render_curl(action))


if __name__ == "__main__":
    unittest.main(verbosity=2)
