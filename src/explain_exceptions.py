"""
Pre-generates plain-language explanations of what happened to each
payment, for the dashboard's "explain this" panel.

Deliberately OFFLINE/pre-generated, not a runtime call: a public repo
can't ship an API key, and a page that calls a local model endpoint is
dead on any machine but the author's. This script does the LLM work once,
writes the text into data/explanations.json, and the dashboard just reads
that. The script itself is the evidence an LLM was used.

Division of labour: the LLM only writes language. Every number and every
decision it describes comes from the audit trail -- the bandit and the
NPCI/RBI rules make the decisions, never the LLM.

Usage:  python explain_exceptions.py            (uses OmniRoute free-stack)
        python explain_exceptions.py --offline  (deterministic templates)
"""

import argparse
import json
import os
import urllib.request

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
ENDPOINT = "http://localhost:20128/v1/chat/completions"
MODEL = "free-stack"

SYSTEM = (
    "You explain payment-recovery decisions to a merchant's finance-ops team. "
    "Two to three sentences, plain English, no jargon, no bullet points, no preamble. "
    "State what happened, why the system did what it did, and what happens next. "
    "Never invent numbers -- use only what you are given."
)


def build_case(payment_id: str, records: list[dict]) -> dict:
    mine = [r for r in records if r["payment_id"] == payment_id]
    first = mine[0]
    attempts = [r for r in mine if r["channel"]]
    win = next((r for r in mine if r["recovered"]), None)
    hard = any(r["stopping_rule"] == "hard_decline_no_retry" for r in mine)
    return {
        "payment_id": payment_id,
        "amount_inr": first["amount_inr"],
        "decline_code": first["decline_code"],
        "outcome": "recovered" if win else ("refused_hard_decline" if hard else "exhausted_retries"),
        "attempt_count": len(attempts),
        "channels_used": sorted({r["channel"] for r in attempts}),
        "windows_used": [r["window_hours"] for r in attempts],
        "recovered_on_window_hours": win["window_hours"] if win else None,
        "stopping_rule": next((r["stopping_rule"] for r in mine if r["stopping_rule"]), None),
    }


def template_explanation(case: dict) -> str:
    amt = f"Rs {case['amount_inr']:,.2f}"
    code = case["decline_code"].replace("_", " ")
    if case["outcome"] == "refused_hard_decline":
        return (
            f"This {amt} payment failed with a {code}, which is a permanent failure -- "
            f"retrying it cannot succeed and would breach mandate rules. "
            f"The system refused to retry and logged the refusal; recovering this needs the "
            f"customer to fix the underlying mandate or card."
        )
    if case["outcome"] == "recovered":
        return (
            f"This {amt} payment failed with a {code}, a temporary problem worth retrying. "
            f"The system retried on the NPCI-permitted schedule and reached the customer by "
            f"{', '.join(case['channels_used'])}; it succeeded {case['recovered_on_window_hours']} hours "
            f"after the original failure. No further action needed."
        )
    return (
        f"This {amt} payment failed with a {code}. The system used all "
        f"{case['attempt_count']} permitted retries across the NPCI schedule "
        f"(24h, 72h, 7d) via {', '.join(case['channels_used']) or 'no channel'}, and none succeeded. "
        f"The retry cap is now exhausted for this cycle, so it needs manual follow-up."
    )


def llm_explanation(case: dict, timeout: int = 90) -> str | None:
    key = os.environ.get("OMNIROUTE_API_KEY")
    if not key:
        return None
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": "Explain this payment recovery case:\n" + json.dumps(case, indent=2)},
        ],
        "max_tokens": 220,
        "temperature": 0.3,
        "stream": False,  # gateway streams by default; SSE would break the JSON parse below
    }
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.load(resp)
        return body["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        print(f"  LLM call failed for {case['payment_id']}: {type(exc).__name__} -- using template")
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", help="skip LLM, use deterministic templates")
    parser.add_argument("--limit", type=int, default=6, help="how many cases to explain")
    args = parser.parse_args()

    records = [json.loads(l) for l in open(os.path.join(DATA_DIR, "audit_bandit_seed0.jsonl"))]
    stats = json.load(open(os.path.join(DATA_DIR, "pipeline_stats.json")))

    # One of each outcome type first -- those are the ones worth showing.
    recovered = [r["payment_id"] for r in records if r["recovered"]]
    picks = []
    if stats["hard_declines"]:
        picks.append(stats["hard_declines"][0])
    if stats["exceptions"]:
        picks.append(stats["exceptions"][0])
    if recovered:
        picks.append(recovered[0])
    for pid in stats["exceptions"] + stats["hard_declines"] + recovered:
        if pid not in picks and len(picks) < args.limit:
            picks.append(pid)

    out = {}
    for pid in picks[: args.limit]:
        case = build_case(pid, records)
        text = None if args.offline else llm_explanation(case)
        source = "llm" if text else "template"
        out[pid] = {
            "case": case,
            "explanation": text or template_explanation(case),
            "source": source,
        }
        print(f"  {pid}: {case['outcome']} ({source})")

    path = os.path.join(DATA_DIR, "explanations.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"Wrote {os.path.abspath(path)}")


if __name__ == "__main__":
    main()
