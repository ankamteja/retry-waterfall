"""
Optional live server. The dashboard works without it; this makes it a
running service instead of a page.

    python3 src/server.py            # then open http://localhost:8934

What it adds over opening data/dashboard.html directly:

  GET  /                serves the dashboard
  GET  /health          the page polls this once to decide whether to
                        light up its LIVE badge and enable the two
                        network-backed features below
  POST /llm             a real language model writes the explanation for a
                        decision, per decision, as it happens. Proxied
                        rather than called from the browser because the
                        model endpoint sets no CORS headers, and because a
                        proxy is where a key would live if one were needed.
  POST /webhook         accepts a genuine Razorpay subscription webhook
                        body, runs it through razorpay_adapter, the
                        compliance gate, the bandit and the dry-run
                        executor, and returns the whole trace
  GET  /events          server-sent events: every webhook decision is
                        pushed to any connected dashboard live

The offline path is deliberately still first-class. The pitch gets recorded
on unknown wifi, and a demo that needs a process running is a demo that can
die on stage. With the server up you get a live model and real webhook
ingestion; without it you get the in-browser engine and the committed
explanations, and the page says which mode it is in rather than pretending.

Nothing here sends anything to Razorpay. The executor is still the dry-run
one: /webhook returns the outbound call it *would* make.
"""

import json
import os
import queue
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from domain_rules import RETRY_WINDOWS_HOURS, is_hard_decline, requires_additional_factor_auth
from policies import BanditPolicy
from razorpay_adapter import parse_webhook, to_event
from recovery_actions import ComplianceViolation, ContactLedger, DryRunExecutor

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
PORT = int(os.environ.get("PORT", "8934"))

# An OpenAI-compatible endpoint. Defaults to the local OmniRoute gateway,
# which needs no key; override for a hosted provider.
LLM_URL = os.environ.get("LLM_URL", "http://localhost:20128/v1/chat/completions")
LLM_MODEL = os.environ.get("LLM_MODEL", "free-stack")
LLM_KEY = os.environ.get("LLM_API_KEY", "")

# The model writes language and nothing else. It is handed the decision that
# was already made and asked to phrase it. It is never asked what to do,
# never asked for a number, and its output is not parsed for one.
LLM_SYSTEM = (
    "You explain automated payment-recovery decisions to a finance operations "
    "person in India. You are given a decision that has already been made by a "
    "rules engine and a bandit policy. Never second-guess it, never suggest an "
    "alternative, never invent a number that is not in the input. Two or three "
    "short sentences, plain English, no jargon, no bullet points, no em-dashes. "
    "Say what happened, why the rules forced or allowed it, and what happens next."
)

_subscribers: list[queue.Queue] = []
_subscribers_lock = threading.Lock()

# One long-lived bandit, so the server actually learns across webhooks
# instead of resetting per request. Seeded for a reproducible demo.
_policy = BanditPolicy(seed=0)
_policy_lock = threading.Lock()

# One long-lived contact ledger, for the same reason and a stricter one. The
# NPCI cap counts contacts per payment per cycle, and a cycle outlives any
# single request -- so an executor built per request, with a ledger of its
# own, would start every request at zero and the cap would bind nothing.
# Replaying one webhook four times is exactly how a real integration
# oversends. This is the state that refuses the fourth.
_ledger = ContactLedger()


def broadcast(event: dict) -> None:
    payload = f"data: {json.dumps(event)}\n\n".encode()
    with _subscribers_lock:
        targets = list(_subscribers)
    for q in targets:
        try:
            q.put_nowait(payload)
        except queue.Full:
            pass  # a stalled browser must not block the server


def ask_llm(prompt: str) -> str:
    body = json.dumps({
        "model": LLM_MODEL,
        "messages": [{"role": "system", "content": LLM_SYSTEM},
                     {"role": "user", "content": prompt}],
        "max_tokens": 220,
        "stream": False,   # the gateway streams by default and the JSON parse
                           # then silently fails; this bit us once already
    }).encode()
    req = urllib.request.Request(LLM_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if LLM_KEY:
        req.add_header("Authorization", f"Bearer {LLM_KEY}")
    with urllib.request.urlopen(req, timeout=45) as r:
        data = json.loads(r.read())
    return data["choices"][0]["message"]["content"].strip()


def decide(event, category: str) -> dict:
    """The same pipeline eval_harness runs, for one live payment."""
    executor = DryRunExecutor(ledger=_ledger)
    trace = {"payment_id": event.payment_id, "amount_inr": event.amount_inr,
             "category": category, "decline_code": event.decline_code.value,
             "attempts": [], "actions": []}

    if is_hard_decline(event.decline_code):
        executor.suppress_hard_decline(event.payment_id, event.amount_inr,
                                       event.decline_code, "hard decline: not retryable")
        trace["outcome"] = "refused"
    elif requires_additional_factor_auth(event.amount_inr, category):
        executor.escalate_afa(event.payment_id, event.amount_inr, category,
                              event.decline_code, "above RBI AFA threshold")
        trace["outcome"] = "escalated"
    else:
        with _policy_lock:
            for i, window_hours in enumerate(RETRY_WINDOWS_HOURS):
                decision = _policy.choose(event.decline_code, window_hours, event.amount_inr)
                trace["attempts"].append({
                    "window_hours": window_hours, "attempt_number": i + 2,
                    "channel": decision.channel,
                    "expected_net_inr": decision.expected_net_inr,
                    "rationale": decision.rationale,
                })
                if decision.should_attempt:
                    executor.contact(event.payment_id, event.amount_inr, category,
                                     event.decline_code, i + 2, window_hours,
                                     decision.channel, decision.rationale)
                    break
        trace["outcome"] = "contacted" if executor.actions else "skipped"

    trace["actions"] = [a.to_json_dict() for a in executor.actions]
    return trace


def explain(trace: dict) -> str:
    return ask_llm(json.dumps({
        "amount_inr": trace["amount_inr"],
        "category": trace["category"],
        "decline_code": trace["decline_code"],
        "outcome": trace["outcome"],
        "attempts": trace["attempts"],
        "actions": [{"kind": a["kind"], "authorised_by": a["authorised_by"]}
                    for a in trace["actions"]],
    }, indent=2))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print(f"  {self.address_string()}  {fmt % args}")

    # -- helpers ----------------------------------------------------------

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _read_body(self) -> dict:
        n = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(n) or b"{}")

    # -- routes -----------------------------------------------------------

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/health"):
            return self._json(200, {"live": True, "model": LLM_MODEL})
        if self.path.startswith("/events"):
            return self._sse()
        if self.path in ("/", "/index.html", "/dashboard.html"):
            path = os.path.join(DATA_DIR, "dashboard.html")
            if not os.path.exists(path):
                return self._send(404, b"Run src/generate_dashboard.py first.", "text/plain")
            with open(path, "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        return self._send(404, b"not found", "text/plain")

    def do_POST(self):
        try:
            if self.path.startswith("/llm"):
                body = self._read_body()
                return self._json(200, {"text": ask_llm(body.get("prompt", ""))})

            if self.path.startswith("/webhook"):
                payload = self._read_body()
                signal = parse_webhook(payload)
                if signal is None:
                    return self._json(200, {"ignored": True,
                                            "reason": "not a recovery event"})
                category = payload.get("category", "subscription")
                if not signal.should_recover:
                    result = {"payment_id": signal.payment_id or signal.subscription_id,
                              "outcome": "no_recovery_window",
                              "reason": f"event {signal.event}, "
                                        f"{signal.attempts_remaining} attempts remaining",
                              "actions": [], "attempts": []}
                else:
                    result = decide(to_event(signal, category), category)
                if self.headers.get("X-Explain") == "1":
                    try:
                        result["explanation"] = explain(result)
                        result["explanation_source"] = "llm"
                    except Exception as e:
                        result["explanation_source"] = f"unavailable: {e}"
                broadcast(result)
                return self._json(200, result)

        except ComplianceViolation as e:
            # A refused outbound call is a real answer, not a server error.
            return self._json(200, {"outcome": "blocked", "reason": str(e)})
        except (urllib.error.URLError, TimeoutError) as e:
            return self._json(502, {"error": f"model endpoint unreachable: {e}"})
        except Exception as e:
            return self._json(400, {"error": str(e)})

        return self._send(404, b"not found", "text/plain")

    def _sse(self):
        q: queue.Queue = queue.Queue(maxsize=64)
        with _subscribers_lock:
            _subscribers.append(q)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    self.wfile.write(q.get(timeout=20))
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")   # through proxies
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with _subscribers_lock:
                if q in _subscribers:
                    _subscribers.remove(q)


def main():
    print(f"Retry Waterfall live server on http://localhost:{PORT}")
    print(f"  model endpoint  {LLM_URL}  ({LLM_MODEL})")
    print(f"  dashboard       http://localhost:{PORT}/")
    print(f"  webhook         POST http://localhost:{PORT}/webhook")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
