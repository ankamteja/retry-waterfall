"""
Generates a single self-contained static HTML page from eval_harness.py's
output: baseline/bandit/oracle side by side, one expanded audit entry, one
hard-decline refused citing the NPCI cap. Reads data/headline_summary.json
and data/audit_bandit_seed0.jsonl -- run eval_harness.py first.

No server, no fetch(), no CORS: data is embedded inline so the file opens
directly in a browser (or via Playwright for screenshots/video).
"""

import json
import os

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUT_PATH = os.path.join(DATA_DIR, "demo.html")


def load_summary() -> dict:
    with open(os.path.join(DATA_DIR, "headline_summary.json")) as f:
        seed_results = json.load(f)
    import statistics
    agg = {}
    for policy_name in ("baseline", "bandit", "oracle"):
        rates = [r[policy_name]["recovery_rate"] for r in seed_results]
        nets = [r[policy_name]["net_recovered_inr"] for r in seed_results]
        cprs = [r[policy_name]["cost_per_recovery_inr"] for r in seed_results if r[policy_name]["cost_per_recovery_inr"] is not None]
        agg[policy_name] = {
            "recovery_rate_mean": round(statistics.mean(rates), 4),
            "net_mean": round(statistics.mean(nets), 2),
            "net_stdev": round(statistics.stdev(nets), 2) if len(nets) > 1 else 0.0,
            "cost_per_recovery_mean": round(statistics.mean(cprs), 2) if cprs else None,
        }
    baseline_net = agg["baseline"]["net_mean"]
    bandit_net = agg["bandit"]["net_mean"]
    oracle_net = agg["oracle"]["net_mean"]
    lift = oracle_net - baseline_net
    agg["lift_captured_pct"] = round((bandit_net - baseline_net) / lift * 100, 1) if lift else None
    agg["n_seeds"] = len(seed_results)
    agg["n_events"] = seed_results[0]["bandit"]["n_events"]
    return agg


def find_examples() -> dict:
    path = os.path.join(DATA_DIR, "audit_bandit_seed0.jsonl")
    recovered_example = None
    hard_decline_example = None
    with open(path) as f:
        for line in f:
            rec = json.loads(line)
            if rec["stopping_rule"] == "hard_decline_no_retry" and hard_decline_example is None:
                hard_decline_example = rec
            if rec["recovered"] and rec["channel"] == "ivr_call" and recovered_example is None:
                recovered_example = rec
            if recovered_example and hard_decline_example:
                break
    return {"recovered": recovered_example, "hard_decline": hard_decline_example}


def render(summary: dict, examples: dict) -> str:
    def pct(x):
        return f"{x * 100:.1f}"

    def inr(x):
        return f"{x:,.0f}"

    policy_meta = (
        ("baseline", "Baseline", "Naive blind retry, no personalization"),
        ("bandit", "Bandit", "This project -- Thompson Sampling, cost-aware"),
        ("oracle", "Oracle", "Perfect information -- the ceiling, not deployable"),
    )

    bar_max = max(summary[name]["net_mean"] for name, _, _ in policy_meta)
    bars = ""
    for name, label, note in policy_meta:
        s = summary[name]
        height_pct = round(s["net_mean"] / bar_max * 100, 1) if bar_max else 0
        cpr = f"₹{s['cost_per_recovery_mean']:,.2f}" if s["cost_per_recovery_mean"] is not None else "-"
        bars += f"""
        <div class="bar-col">
          <div class="bar-value">₹{inr(s['net_mean'])}</div>
          <div class="bar-track"><div class="bar-fill bar-{name}" style="--h:{height_pct}%"><div class="bar-cap"></div></div></div>
          <div class="bar-label">{label}</div>
          <div class="bar-note">{note}<br>{pct(s['recovery_rate_mean'])}% recovered &middot; {cpr}/recovery</div>
        </div>"""

    rec = examples["recovered"]
    hard = examples["hard_decline"]

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI Revenue Recovery</title>
<style>
  :root {{
    --bg: #06070c; --panel: rgba(22,26,38,0.55); --panel-solid: #161a26; --border: rgba(255,255,255,0.09);
    --text: #eef1f8; --muted: #8b93a7; --accent: #5b8cff; --accent2: #9d6bff;
    --good: #2fd98a; --bad: #ff5d75; --gold: #ffb454;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ height: 100%; }}
  body {{
    background: var(--bg); color: var(--text); margin: 0; padding: 56px 24px 40px;
    font-family: -apple-system, "Segoe UI", Inter, sans-serif; overflow-x: hidden; position: relative;
  }}
  .aurora {{
    position: fixed; inset: -20%; z-index: 0; pointer-events: none; opacity: 0.55; filter: blur(60px);
    background:
      radial-gradient(38% 32% at 18% 22%, rgba(91,140,255,0.55), transparent 60%),
      radial-gradient(32% 28% at 82% 15%, rgba(157,107,255,0.45), transparent 60%),
      radial-gradient(40% 35% at 60% 85%, rgba(47,217,138,0.28), transparent 60%);
    animation: drift 22s ease-in-out infinite alternate;
  }}
  @keyframes drift {{ from {{ transform: translate3d(0,0,0) rotate(0deg); }} to {{ transform: translate3d(2%,-3%,0) rotate(6deg); }} }}
  .grid-floor {{
    position: fixed; left: 0; right: 0; bottom: 0; height: 40vh; z-index: 0; pointer-events: none;
    background-image: linear-gradient(rgba(91,140,255,0.14) 1px, transparent 1px), linear-gradient(90deg, rgba(91,140,255,0.14) 1px, transparent 1px);
    background-size: 48px 48px; transform: perspective(500px) rotateX(72deg) translateY(20%);
    mask-image: linear-gradient(to top, black, transparent 85%);
  }}
  .wrap {{ max-width: 980px; margin: 0 auto; position: relative; z-index: 1; perspective: 1400px; }}
  h1 {{
    font-size: clamp(1.8rem, 4vw, 2.6rem); margin: 0 0 6px; font-weight: 700; letter-spacing: -0.02em;
    background: linear-gradient(90deg, #fff, var(--accent) 60%, var(--accent2)); -webkit-background-clip: text; background-clip: text; color: transparent;
  }}
  .sub {{ color: var(--muted); margin-bottom: 40px; font-size: 0.95rem; }}
  .glass {{
    background: var(--panel); border: 1px solid var(--border); border-radius: 16px;
    backdrop-filter: blur(18px); -webkit-backdrop-filter: blur(18px);
    box-shadow: 0 20px 60px -20px rgba(0,0,0,0.6), inset 0 1px 0 rgba(255,255,255,0.06);
  }}
  .tilt {{ transform-style: preserve-3d; transition: transform 0.15s ease-out; will-change: transform; }}

  .bars {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 18px; padding: 32px 28px 24px; margin-bottom: 22px; }}
  .bar-col {{ display: flex; flex-direction: column; align-items: center; text-align: center; }}
  .bar-value {{ font-size: 1.1rem; font-weight: 700; margin-bottom: 8px; font-variant-numeric: tabular-nums; }}
  .bar-track {{ width: 100%; height: 180px; display: flex; align-items: flex-end; justify-content: center; perspective: 600px; }}
  .bar-fill {{
    width: 58%; height: var(--h); border-radius: 8px 8px 3px 3px; position: relative;
    transform: rotateX(6deg); transform-origin: bottom;
    animation: rise 1.1s cubic-bezier(.2,.9,.25,1) both;
    box-shadow: 0 -2px 24px -4px currentColor;
  }}
  @keyframes rise {{ from {{ height: 0 !important; }} }}
  .bar-baseline {{ background: linear-gradient(180deg, #4a5570, #2a3245); color: rgba(120,140,180,0.5); }}
  .bar-bandit   {{ background: linear-gradient(180deg, var(--accent2), var(--accent)); color: rgba(91,140,255,0.6); }}
  .bar-oracle   {{ background: linear-gradient(180deg, #ffd98a, var(--gold)); color: rgba(255,180,84,0.5); }}
  .bar-cap {{ position: absolute; top: -3px; left: 0; right: 0; height: 6px; border-radius: 4px; background: rgba(255,255,255,0.55); }}
  .bar-label {{ margin-top: 12px; font-weight: 600; font-size: 0.95rem; }}
  .bar-note {{ color: var(--muted); font-size: 0.78rem; margin-top: 4px; line-height: 1.4; }}

  .lift {{ padding: 18px 26px; margin-bottom: 28px; font-size: 1rem; }}
  .lift b {{ color: var(--accent); font-size: 1.15em; }}

  .cards {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }}
  .card {{ padding: 22px 24px; }}
  .card h3 {{ margin: 0 0 14px; font-size: 0.98rem; display: flex; align-items: center; gap: 8px; }}
  .badge {{ width: 22px; height: 22px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; font-size: 0.8rem; flex-shrink: 0; }}
  .card.good .badge {{ background: rgba(47,217,138,0.18); color: var(--good); }}
  .card.bad .badge {{ background: rgba(255,93,117,0.18); color: var(--bad); }}
  .field {{ display: flex; justify-content: space-between; padding: 5px 0; font-size: 0.88rem; border-bottom: 1px dashed rgba(255,255,255,0.06); }}
  .field span:first-child {{ color: var(--muted); }}
  .field span:last-child {{ font-variant-numeric: tabular-nums; font-weight: 500; }}
  .rationale {{ margin-top: 12px; font-size: 0.83rem; color: var(--muted); font-style: italic; }}

  footer {{ color: var(--muted); font-size: 0.78rem; margin-top: 36px; text-align: center; }}

  @media (max-width: 700px) {{ .bars, .cards {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<div class="aurora"></div>
<div class="grid-floor"></div>
<div class="wrap">
  <h1>AI Revenue Recovery</h1>
  <div class="sub">{summary['n_events']} failed-payment events per batch &middot; {summary['n_seeds']} seeds &middot; mean reported, not a single cherry-picked run</div>

  <div class="glass tilt bars">{bars}
  </div>

  <div class="glass lift">Bandit captures <b>{summary['lift_captured_pct']}%</b> of the achievable lift over baseline &mdash; oracle (perfect information) is the ceiling at 100%.</div>

  <div class="cards">
    <div class="glass tilt card good">
      <h3><span class="badge">&#10003;</span>Recovered &mdash; expensive channel, but it paid off</h3>
      <div class="field"><span>Payment</span><span>{rec['payment_id']}</span></div>
      <div class="field"><span>Decline reason</span><span>{rec['decline_code']}</span></div>
      <div class="field"><span>Attempt</span><span>#{rec['attempt_number']} (T+{rec['window_hours']}h)</span></div>
      <div class="field"><span>Channel</span><span>{rec['channel']}</span></div>
      <div class="field"><span>Amount</span><span>₹{rec['amount_inr']:,.2f}</span></div>
      <div class="field"><span>Expected net</span><span>₹{rec['expected_net_inr']:,.2f}</span></div>
      <div class="rationale">{rec['rationale']}</div>
    </div>
    <div class="glass tilt card bad">
      <h3><span class="badge">&#10007;</span>Refused &mdash; compliance, not a policy choice</h3>
      <div class="field"><span>Payment</span><span>{hard['payment_id']}</span></div>
      <div class="field"><span>Decline reason</span><span>{hard['decline_code']}</span></div>
      <div class="field"><span>Amount</span><span>₹{hard['amount_inr']:,.2f}</span></div>
      <div class="field"><span>Stopping rule</span><span>{hard['stopping_rule']}</span></div>
      <div class="rationale">{hard['rationale']}</div>
    </div>
  </div>

  <footer>Simulator constants are documented assumptions (synthetic_data.py). NPCI/RBI numbers (domain_rules.py) are sourced regulation, checked 2026-08-22.</footer>
</div>
<script>
  document.querySelectorAll('.tilt').forEach(function(el) {{
    el.addEventListener('mousemove', function(e) {{
      var r = el.getBoundingClientRect();
      var px = (e.clientX - r.left) / r.width - 0.5;
      var py = (e.clientY - r.top) / r.height - 0.5;
      el.style.transform = 'rotateX(' + (py * -6) + 'deg) rotateY(' + (px * 8) + 'deg) translateZ(6px)';
    }});
    el.addEventListener('mouseleave', function() {{
      el.style.transform = 'rotateX(0deg) rotateY(0deg) translateZ(0px)';
    }});
  }});
</script>
</body>
</html>
"""


def main():
    summary = load_summary()
    examples = find_examples()
    html = render(summary, examples)
    with open(OUT_PATH, "w") as f:
        f.write(html)
    print(f"Wrote {os.path.abspath(OUT_PATH)}")


if __name__ == "__main__":
    main()
