"""
Builds the dashboard: one self-contained HTML file, no network at runtime.

Design direction (researched, then applied deliberately -- see docs/plan.md):
  Palette   deep indigo-black base, slate-blue surfaces, muted teal for
            recovered, brick red for refused, amber for regulatory
            attention. Deliberately NOT the #22C55E/#EF4444 pairing every
            AI-generated dashboard uses.
  Type      Space Grotesk (display -- payment-SDK/terminal feel),
            Inter (body -- what Indian fintech UI actually uses),
            JetBrains Mono (data -- mandate refs stay legible when dense).
  Signature The retry timeline: every soft-decline payment is a lane, each
            NPCI-permitted attempt a marker on it, so you can see the
            regulatory cap and the recovery moment at once. This is the
            thing the page is remembered by, so everything else stays quiet.

Everything is vendored: fonts base64-inlined, force-graph and countUp read
off disk. The pitch gets recorded on unknown wifi and judges may open the
repo offline; a blank page in either case is unacceptable.
"""

import json
import os

from dashboard_live import (ENGINE_JS, LIVE_CSS, LIVE_HTML, LIVE_UI_JS, NETWORK_CSS,
                            NETWORK_HTML, NETWORK_JS, live_constants)

SRC_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(SRC_DIR, "..", "data")
VENDOR_DIR = os.path.join(SRC_DIR, "..", "vendor")
OUT_PATH = os.path.join(DATA_DIR, "dashboard.html")


def load(name):
    with open(os.path.join(DATA_DIR, name)) as f:
        return json.load(f)


def vendor(name):
    with open(os.path.join(VENDOR_DIR, name)) as f:
        return f.read()


def headline(seed_results):
    import statistics
    agg = {}
    for p in ("baseline", "bandit", "oracle"):
        rates = [r[p]["recovery_rate"] for r in seed_results]
        nets = [r[p]["net_recovered_inr"] for r in seed_results]
        cprs = [r[p]["cost_per_recovery_inr"] for r in seed_results if r[p]["cost_per_recovery_inr"]]
        agg[p] = {
            "rate": statistics.mean(rates), "rate_sd": statistics.stdev(rates),
            "net": statistics.mean(nets), "net_sd": statistics.stdev(nets),
            "cpr": statistics.mean(cprs) if cprs else 0,
        }
    lift = agg["oracle"]["net"] - agg["baseline"]["net"]
    agg["captured"] = (agg["bandit"]["net"] - agg["baseline"]["net"]) / lift * 100 if lift else 0
    agg["n_seeds"] = len(seed_results)
    agg["n_events"] = seed_results[0]["bandit"]["n_events"]
    return agg


def build_timeline(records, limit=16):
    """The signature element's data: one lane per payment, markers at each
    NPCI retry window, so the cap and the recovery moment are both visible."""
    by_payment = {}
    for r in records:
        by_payment.setdefault(r["payment_id"], []).append(r)

    lanes = []
    for pid, rs in by_payment.items():
        first = rs[0]
        hard = any(r["stopping_rule"] == "hard_decline_no_retry" for r in rs)
        attempts = [
            {"w": r["window_hours"], "ch": r["channel"], "won": bool(r["recovered"])}
            for r in rs if r["channel"]
        ]
        won = any(a["won"] for a in attempts)
        lanes.append({
            "id": pid, "amount": first["amount_inr"], "code": first["decline_code"],
            "hard": hard, "won": won, "attempts": attempts,
            "outcome": "refused" if hard else ("recovered" if won else "exhausted"),
        })
    # Show a representative mix, biggest amounts first within each outcome.
    order = {"recovered": 0, "exhausted": 1, "refused": 2}
    lanes.sort(key=lambda l: (order[l["outcome"]], -l["amount"]))
    picked, seen = [], {"recovered": 0, "exhausted": 0, "refused": 0}
    for l in lanes:
        if seen[l["outcome"]] < limit // 3 + 2:
            picked.append(l)
            seen[l["outcome"]] += 1
    return picked[:limit]


def main():
    seed_results = load("headline_summary.json")
    stats = load("pipeline_stats.json")
    curve = load("learning_curve.json")
    try:
        explanations = load("explanations.json")
    except FileNotFoundError:
        explanations = {}
    records = [json.loads(l) for l in open(os.path.join(DATA_DIR, "audit_bandit_seed0.jsonl"))]
    posteriors = load("posteriors.json")

    h = headline(seed_results)
    st = stats["stages"]
    timeline = build_timeline(records)

    payload = json.dumps({
        "headline": h, "stats": stats, "curve": curve,
        "explanations": explanations, "timeline": timeline,
    })

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Retry Waterfall Recovery Control Room</title>
<style>
{vendor('fonts.css')}

:root {{
  /* Palette: warmer than true black; slate-blue reads regulatory, not gaming. */
  --base:#0A0E1A; --surface:#141B2A; --surface-2:#1B2838; --raised:#22314A;
  --line:rgba(255,255,255,.075); --line-2:rgba(255,255,255,.13);
  --text:#E6EAF4; --muted:#8A97B0; --dim:#5C6880;
  --recovered:#3B9B7D;  /* muted teal -- money moving, not lawn green */
  --refused:#C54D4D;    /* brick -- serious loss, not alarm */
  --attention:#E8B86D;  /* amber -- mandate/regulatory warnings */
  --accent:#5E86C7;
  --shadow-1:0 1px 2px rgba(0,0,0,.5), 0 0 0 1px rgba(255,255,255,.05);
  --shadow-2:0 6px 20px rgba(0,0,0,.55), 0 0 0 1px rgba(255,255,255,.07);
}}
*{{box-sizing:border-box;}}
html{{-webkit-font-smoothing:antialiased;}}
body{{margin:0;background:var(--base);color:var(--text);
  font-family:Inter,-apple-system,"Segoe UI",sans-serif;font-size:15px;line-height:1.6;
  padding:0 0 72px;overflow-x:hidden;}}
.noise{{position:fixed;inset:0;pointer-events:none;opacity:.02;z-index:9999;
  background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='3'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");}}
.num{{font-variant-numeric:tabular-nums lining-nums;font-feature-settings:"tnum" 1,"lnum" 1;}}
.mono{{font-family:"JetBrains Mono",ui-monospace,monospace;}}

.wrap{{max-width:1200px;margin:0 auto;padding:0 26px;}}

/* ---- masthead ---- */
.top{{border-bottom:1px solid var(--line);background:linear-gradient(180deg,rgba(27,40,56,.5),transparent);}}
.top .wrap{{padding-top:34px;padding-bottom:38px;}}
.brandrow{{display:flex;align-items:center;gap:11px;margin-bottom:22px;}}
.mark{{width:26px;height:26px;border-radius:7px;background:linear-gradient(145deg,var(--recovered),var(--accent));
  display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;color:#0A0E1A;
  font-family:"Space Grotesk",sans-serif;}}
.brand{{font-family:"Space Grotesk",sans-serif;font-weight:700;letter-spacing:-.01em;font-size:1rem;}}
.brand span{{color:var(--dim);font-weight:500;}}
h1{{font-family:"Space Grotesk",sans-serif;font-size:clamp(1.9rem,3.6vw,2.55rem);line-height:1.14;
  font-weight:700;letter-spacing:-.028em;margin:0 0 16px;max-width:19ch;}}
.lede{{color:var(--muted);max-width:70ch;margin:0 0 12px;}}
.lede b{{color:var(--text);font-weight:600;}}
.rule{{color:var(--attention);font-weight:600;}}

/* ---- kpi ---- */
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--line);
  border:1px solid var(--line);border-radius:12px;overflow:hidden;margin-top:30px;}}
.kpi{{background:var(--surface);padding:17px 19px;}}
.kpi .l{{color:var(--dim);font-size:.7rem;text-transform:uppercase;letter-spacing:.09em;font-weight:600;}}
.kpi .v{{font-family:"Space Grotesk",sans-serif;font-size:1.6rem;font-weight:700;margin-top:8px;letter-spacing:-.02em;}}
.kpi .s{{color:var(--dim);font-size:.76rem;margin-top:3px;}}
.kpi.tealv .v{{color:var(--recovered);}} .kpi.redv .v{{color:var(--refused);}}
.kpi.amberv .v{{color:var(--attention);}}

/* ---- sections ---- */
section{{margin-top:52px;}}
.shead{{display:flex;align-items:baseline;gap:12px;margin-bottom:6px;}}
.snum{{font-family:"JetBrains Mono",monospace;font-size:.74rem;color:var(--dim);letter-spacing:.06em;}}
h2{{font-family:"Space Grotesk",sans-serif;font-size:1.22rem;font-weight:700;margin:0;letter-spacing:-.015em;}}
.note{{color:var(--muted);font-size:.885rem;max-width:82ch;margin:0 0 20px;}}
.note b{{color:var(--text);font-weight:600;}}
.card{{background:var(--surface);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow-1);}}
.pad{{padding:20px 22px;}}

/* ---- signature: retry timeline ---- */
.tl-head{{display:grid;grid-template-columns:180px 1fr 116px;gap:14px;padding:0 22px 9px;
  color:var(--dim);font-size:.7rem;text-transform:uppercase;letter-spacing:.08em;font-weight:600;
  border-bottom:1px solid var(--line);}}
.tl-axis{{position:relative;height:14px;}}
.tl-axis span{{position:absolute;transform:translateX(-50%);font-size:.66rem;}}
.lane{{display:grid;grid-template-columns:180px 1fr 116px;gap:14px;align-items:center;
  padding:9px 22px;border-bottom:1px solid rgba(255,255,255,.04);transition:background .14s;}}
.lane:hover{{background:rgba(255,255,255,.022);}}
.lane .who{{min-width:0;}}
.lane .pid{{font-family:"JetBrains Mono",monospace;font-size:.76rem;color:var(--muted);}}
.lane .amt{{font-family:"Space Grotesk",sans-serif;font-weight:700;font-size:.94rem;}}
.track{{position:relative;height:26px;}}
.track::before{{content:"";position:absolute;left:0;right:0;top:50%;height:1px;background:var(--line-2);}}
.gate{{position:absolute;top:50%;transform:translate(-50%,-50%);width:2px;height:15px;
  background:rgba(232,184,109,.32);}}
.dot{{position:absolute;top:50%;transform:translate(-50%,-50%);width:11px;height:11px;border-radius:50%;
  border:2px solid var(--surface);animation:pop .34s cubic-bezier(.3,1.5,.6,1) both;}}
.dot.miss{{background:#3E4A63;}}
.dot.win{{background:var(--recovered);box-shadow:0 0 0 4px rgba(59,155,125,.2);}}
@keyframes pop{{from{{transform:translate(-50%,-50%) scale(0);}}}}
.barred{{position:absolute;left:0;right:0;top:50%;height:1px;
  background:repeating-linear-gradient(90deg,var(--refused) 0 4px,transparent 4px 9px);opacity:.5;}}
.tag{{font-size:.7rem;font-weight:650;text-transform:uppercase;letter-spacing:.05em;text-align:right;}}
.tag.recovered{{color:var(--recovered);}} .tag.exhausted{{color:var(--dim);}}
.tag.refused{{color:var(--refused);}}
.tl-foot{{padding:13px 22px;color:var(--dim);font-size:.79rem;border-top:1px solid var(--line);}}

/* ---- graph ---- */
#graph{{height:400px;border-radius:12px;overflow:hidden;background:var(--surface);
  border:1px solid var(--line);}}
.legend{{display:flex;gap:20px;flex-wrap:wrap;margin-top:13px;font-size:.8rem;color:var(--muted);}}
.legend i{{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:7px;vertical-align:1px;}}

/* ---- tables ---- */
table{{width:100%;border-collapse:collapse;font-size:.87rem;}}
th,td{{text-align:left;padding:9px 13px;border-bottom:1px solid var(--line);}}
th{{color:var(--dim);font-weight:600;font-size:.7rem;text-transform:uppercase;letter-spacing:.07em;}}
td.r,th.r{{text-align:right;font-variant-numeric:tabular-nums lining-nums;}}
tr.hi td{{color:var(--recovered);font-weight:650;}}
tbody tr:last-child td{{border-bottom:none;}}
.pill{{display:inline-block;padding:1px 8px;border-radius:5px;font-size:.68rem;font-weight:650;
  text-transform:uppercase;letter-spacing:.04em;}}
.pill.soft{{background:rgba(59,155,125,.14);color:var(--recovered);}}
.pill.hard{{background:rgba(197,77,77,.15);color:var(--refused);}}

.two{{display:grid;grid-template-columns:1fr 1fr;gap:18px;}}
.curve{{width:100%;height:214px;}}

/* ---- explanations ---- */
.tabs{{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:16px;}}
.tab{{padding:6px 12px;border-radius:7px;border:1px solid var(--line);background:transparent;
  cursor:pointer;font-size:.775rem;color:var(--muted);font-family:"JetBrains Mono",monospace;transition:.14s;}}
.tab:hover{{color:var(--text);border-color:var(--line-2);}}
.tab.on{{background:var(--raised);border-color:var(--accent);color:var(--text);}}
.exp{{min-height:118px;}}
.exp-h{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px;}}
.exp-t{{line-height:1.75;}}
.exp-m{{margin-top:14px;padding-top:12px;border-top:1px solid var(--line);
  font-size:.76rem;color:var(--dim);font-family:"JetBrains Mono",monospace;}}
code{{font-family:"JetBrains Mono",monospace;background:rgba(255,255,255,.06);
  padding:1px 5px;border-radius:4px;font-size:.86em;}}
footer{{color:var(--dim);font-size:.79rem;margin-top:56px;padding-top:22px;
  border-top:1px solid var(--line);line-height:1.8;}}
:focus-visible{{outline:2px solid var(--accent);outline-offset:2px;}}
@media(prefers-reduced-motion:reduce){{*{{animation:none!important;transition:none!important;}}}}
@media(max-width:900px){{.kpis,.two{{grid-template-columns:1fr 1fr;}}
  .lane,.tl-head{{grid-template-columns:120px 1fr 84px;}}}}
@media(max-width:600px){{.kpis,.two{{grid-template-columns:1fr;}}}}
/*LIVE_CSS*/
</style>
</head>
<body>
<div class="noise"></div>

<div class="top"><div class="wrap">
  <div class="brandrow"><div class="mark">R</div>
    <div class="brand">Retry Waterfall <span>/ recovery control room</span></div></div>
  <h1>Failed auto-debits, recovered within the rules.</h1>
  <p class="lede">When a subscription auto-debit fails in India, the money isn't lost yet. But retrying
    blindly wastes spend, and past a point <b>breaches NPCI mandate rules</b>. This agent decides,
    per payment, whether a retry is <b>worth it</b>, whether it is <b>permitted</b>, and <b>how to reach
    the customer</b>, and it logs every decision for audit.</p>
  <p class="lede"><span class="rule">Retry timing is never the AI's choice.</span> It is fixed by regulation:
    T+24h, T+72h, T+7d, four attempts maximum. The learned part chooses only the contact channel, and
    only ever learns which channel recovers money <b>net of what that channel costs</b>.</p>

  <div class="kpis">
    <div class="kpi"><div class="l">Revenue at risk</div><div class="v num" id="k-risk">0</div>
      <div class="s">{st['ingest']['in']} failed payments in batch</div></div>
    <div class="kpi tealv"><div class="l">Recovered, net of cost</div><div class="v num" id="k-rec">0</div>
      <div class="s">{st['outcome']['recovered']} payments returned</div></div>
    <div class="kpi redv"><div class="l">Refused by compliance</div><div class="v num" id="k-ref">0</div>
      <div class="s">hard declines, never retried</div></div>
    <div class="kpi amberv"><div class="l">Of expert ceiling</div><div class="v num" id="k-cap">0</div>
      <div class="s">vs perfect-information oracle</div></div>
  </div>
</div></div>

<div class="wrap">
<!--LIVE_HTML-->
  <section>
    <div class="shead"><span class="snum">04</span><h2>The retry timeline</h2></div>
    <p class="note">One lane per payment, running left to right over the seven days the regulation allows.
      Each marker is one permitted attempt. <b>The amber line is the NPCI wall.</b> Four attempts,
      then the cycle is closed whether or not the money came back. Filled teal is the attempt that
      recovered it; a dashed lane never got an attempt at all, because it was refused outright.</p>
    <div class="card">
      <div class="tl-head"><div>Payment</div>
        <div class="tl-axis" id="axis"></div><div style="text-align:right">Outcome</div></div>
      <div id="lanes"></div>
      <div class="tl-foot" id="tl-foot"></div>
    </div>
  </section>

  <section>
    <div class="shead"><span class="snum">05</span><h2>Where each payment goes</h2></div>
    <p class="note">The same batch as a flow. Particles are real volume. The compliance gate is red because
      it <b>stopped {st['compliance_gate']['refused']} payments a naive retry bot would have illegally retried</b>,
      and the amber branch is {st['compliance_gate'].get('afa_escalated', 0)} payments over the RBI
      additional-factor-auth threshold, which cannot be silently auto-debited at all and are routed
      to an authenticated payment link instead.
      Drag to rotate, scroll to zoom.</p>
    <div id="graph"></div>
    <div class="legend">
      <span><i style="background:var(--accent)"></i>processing stage</span>
      <span><i style="background:var(--refused)"></i>stops traffic</span>
      <span><i style="background:var(--recovered)"></i>recovered</span>
      <span><i style="background:var(--attention)"></i>learned decision</span>
    </div>
  </section>

  <section>
    <div class="shead"><span class="snum">06</span><h2>Does it actually learn?</h2></div>
    <p class="note">A bandit always beats a naive baseline inside its own simulator, so that on its own proves nothing.
      So it is also scored against an <b>oracle</b> that knows the true recovery probabilities: the realistic
      ceiling. The claim is not "we win", it is <b>how much of the achievable gap it closes</b>, averaged over
      {h['n_seeds']} independent batches so one lucky run can't flatter it.</p>
    <div class="two">
      <div class="card pad">
        <table>
          <tr><th>Policy</th><th class="r">Recovery</th><th class="r">Net recovered</th><th class="r">Cost/win</th></tr>
          <tr><td>Baseline, retry all by SMS</td><td class="r">{h['baseline']['rate']*100:.1f}%</td>
              <td class="r">&#8377;{h['baseline']['net']:,.0f}</td><td class="r">&#8377;{h['baseline']['cpr']:.2f}</td></tr>
          <tr class="hi"><td>Bandit, this project</td><td class="r">{h['bandit']['rate']*100:.1f}%</td>
              <td class="r">&#8377;{h['bandit']['net']:,.0f}</td><td class="r">&#8377;{h['bandit']['cpr']:.2f}</td></tr>
          <tr><td>Oracle, perfect information</td><td class="r">{h['oracle']['rate']*100:.1f}%</td>
              <td class="r">&#8377;{h['oracle']['net']:,.0f}</td><td class="r">&#8377;{h['oracle']['cpr']:.2f}</td></tr>
        </table>
        <p class="note" style="margin:15px 0 0;font-size:.8rem">&plusmn;1 s.d. on net recovered:
          baseline &#8377;{h['baseline']['net_sd']:,.0f}, bandit &#8377;{h['bandit']['net_sd']:,.0f},
          oracle &#8377;{h['oracle']['net_sd']:,.0f}. {h['n_events']} events &times; {h['n_seeds']} seeds,
          fixed RNG offsets, reproducible run to run.</p>
      </div>
      <div class="card pad">
        <div style="font-size:.78rem;color:var(--dim);margin-bottom:4px;text-transform:uppercase;
          letter-spacing:.07em;font-weight:600">Net recovered as evidence accumulates</div>
        <svg class="curve" id="curve" viewBox="0 0 460 214" preserveAspectRatio="none"></svg>
        <p class="note" style="margin:6px 0 0;font-size:.8rem">At 50 events it has almost no evidence per
          channel and trails the baseline. The gap to oracle narrows as attempts accumulate, and
          that shape <b>is</b> the learning.</p>
      </div>
    </div>
  </section>

  <section>
    <div class="shead"><span class="snum">07</span><h2>Why it did that, in plain English</h2></div>
    <p class="note">An LLM turns each audit record into something a finance-ops person can act on. It writes
      <b>language only</b>. Every number and every decision comes from the audit trail, never from the
      model. Generated offline by <code>src/explain_exceptions.py</code> and committed, so this page needs no
      API key and reads identically on any machine.</p>
    <div class="card pad">
      <div class="tabs" id="tabs"></div>
      <div class="exp" id="exp"></div>
    </div>
  </section>

  <section>
    <div class="shead"><span class="snum">08</span><h2>Where the money came from</h2></div>
    <div class="two">
      <div class="card pad">
        <div style="font-size:.78rem;color:var(--dim);margin-bottom:11px;text-transform:uppercase;
          letter-spacing:.07em;font-weight:600">By decline reason</div>
        <table id="t-code"></table>
      </div>
      <div class="card pad">
        <div style="font-size:.78rem;color:var(--dim);margin-bottom:11px;text-transform:uppercase;
          letter-spacing:.07em;font-weight:600">By channel, the tradeoff it had to learn</div>
        <table id="t-chan"></table>
        <div style="font-size:.78rem;color:var(--dim);margin:20px 0 11px;text-transform:uppercase;
          letter-spacing:.07em;font-weight:600">By retry window (NPCI schedule)</div>
        <table id="t-win"></table>
      </div>
    </div>
  </section>

  <footer>
    NPCI / RBI constants in <code>src/domain_rules.py</code> are sourced regulation, checked 2026-08-22.<br>
    Simulator probabilities in <code>src/synthetic_data.py</code> are clearly-labelled assumptions.
    The eval is explicit about which is which.
  </footer>
</div>

<script>{vendor('countUp.umd.min.js')}</script>
<!-- THREE must load first and separately: 3d-force-graph bundles its own copy
     internally but never exposes window.THREE, and the custom labelled nodes
     below need the real constructors. -->
<script>{vendor('three.min.js')}</script>
<script>{vendor('3d-force-graph.min.js')}</script>
<script>
const D = {payload};
const C = {{teal:'#3B9B7D', red:'#C54D4D', amber:'#E8B86D', accent:'#5E86C7', slate:'#3E4A63'}};
/*LIVE_ENGINE*/
/*LIVE_NETWORK*/
/*LIVE_UI*/

/* counters */
const cu=(id,v,o)=>new countUp.CountUp(id,v,Object.assign({{duration:1.9}},o)).start();
cu('k-risk', D.stats.money.at_risk_inr, {{prefix:'\\u20B9',separator:',',decimalPlaces:0}});
cu('k-rec',  D.stats.money.net_recovered_inr, {{prefix:'\\u20B9',separator:',',decimalPlaces:0}});
cu('k-ref',  D.stats.hard_declines.length, {{suffix:' payments'}});
cu('k-cap',  D.headline.captured, {{decimalPlaces:1,suffix:'%'}});

/* ---- signature: retry timeline ---- */
(function(){{
  const WIN=[24,72,168], MAXH=192;           // 8-day track; NPCI wall at 168h
  const pos=h=>(h/MAXH)*100;
  document.getElementById('axis').innerHTML =
    WIN.map(w=>`<span style="left:${{pos(w)}}%">T+${{w}}h</span>`).join('') +
    `<span style="left:${{pos(MAXH)}}%;color:var(--amber)"></span>`;

  document.getElementById('lanes').innerHTML = D.timeline.map((l,i)=>{{
    const marks = l.hard
      ? '<div class="barred"></div>'
      : l.attempts.map(a=>`<div class="dot ${{a.won?'win':'miss'}}" style="left:${{pos(a.w)}}%;
           animation-delay:${{i*30+WIN.indexOf(a.w)*70}}ms" title="T+${{a.w}}h via ${{a.ch}}"></div>`).join('');
    return `<div class="lane">
      <div class="who"><div class="amt num">\\u20B9${{Math.round(l.amount).toLocaleString('en-IN')}}</div>
        <div class="pid">${{l.id}} &middot; ${{l.code.replace(/_/g,' ')}}</div></div>
      <div class="track">${{marks}}<div class="gate" style="left:${{pos(168)}}%"></div></div>
      <div class="tag ${{l.outcome}}">${{l.outcome}}</div></div>`;
  }}).join('');

  const s=D.stats.stages;
  document.getElementById('tl-foot').textContent =
    `Showing ${{D.timeline.length}} of ${{s.ingest['in']}} payments \\u00b7 `+
    `${{s.classify.soft}} eligible for retry, ${{s.classify.hard}} refused outright \\u00b7 `+
    `${{s.policy.attempts}} attempts made in total, never more than 3 per payment`;
}})();

/* ---- pipeline graph ----
   Fixed left-to-right positions, not force-directed: this is a pipeline
   with a known order, and letting the simulation arrange it produced an
   unreadable zigzag of unlabelled dots. Labels are always-on canvas
   sprites, because hover-only labels are useless in a recorded video. */
(function(){{
  const s=D.stats.stages;
  const nodes=[
    {{id:'ingest',  t:'Ingest',           d:s.ingest['in']+' failed payments',                     color:C.accent, val:11, fx:-250, fy:0}},
    {{id:'classify',t:'Classify',         d:s.classify.soft+' soft / '+s.classify.hard+' hard',    color:C.accent, val:11, fx:-125, fy:0}},
    {{id:'gate',    t:'Compliance gate',  d:s.compliance_gate.refused+' refused outright',         color:C.red,    val:14, fx:0,    fy:0}},
    {{id:'policy',  t:'Channel policy',   d:s.policy.attempts+' attempts made',                    color:C.amber,  val:13, fx:128,  fy:0}},
    {{id:'afa',     t:'AFA escalation',   d:(s.compliance_gate.afa_escalated||0)+' need customer auth', color:C.amber, val:11, fx:0, fy:-92}},
    {{id:'won',     t:'Recovered',        d:s.outcome.recovered+' payments',                       color:C.teal,   val:15, fx:250,  fy:-56}},
    {{id:'lost',    t:'Exhausted',        d:s.outcome.exhausted+' payments',                       color:C.slate,  val:10, fx:250,  fy:62}},
  ];
  nodes.forEach(n=>n.fz=0);
  const links=[
    {{source:'ingest',target:'classify',v:s.ingest['in']}},
    {{source:'classify',target:'gate',v:s.classify['in']}},
    {{source:'gate',target:'afa',v:s.compliance_gate.afa_escalated||0}},
    {{source:'gate',target:'policy',v:s.compliance_gate.passed}},
    {{source:'policy',target:'won',v:s.outcome.recovered}},
    {{source:'policy',target:'lost',v:s.outcome.exhausted}},
  ];

  function labelSprite(n){{
    const T=window.THREE, pad=8, W=420, H=132, S=3;   // S = supersample for crisp text
    const cv=document.createElement('canvas'); cv.width=W*S; cv.height=H*S;
    const x=cv.getContext('2d'); x.scale(S,S); x.textAlign='center';
    x.font='700 25px "Space Grotesk", sans-serif'; x.fillStyle=n.color;
    x.fillText(n.t, W/2, 34);
    x.font='400 20px Inter, sans-serif'; x.fillStyle='#8A97B0';
    x.fillText(n.d, W/2, 62+pad);
    const sp=new T.Sprite(new T.SpriteMaterial({{
      map:Object.assign(new T.CanvasTexture(cv),{{minFilter:T.LinearFilter}}),
      transparent:true, depthWrite:false
    }}));
    sp.scale.set(74, 23, 1);
    sp.position.set(0, -19, 0);
    const g=new T.Group();
    g.add(new T.Mesh(new T.SphereGeometry(Math.cbrt(n.val)*3.1, 26, 26),
      new T.MeshBasicMaterial({{color:n.color}})));  // Basic, not Lambert: colour must not depend on scene lights
    g.add(sp);
    return g;
  }}

  const el=document.getElementById('graph');
  const G=ForceGraph3D()(el)
    .backgroundColor('#141B2A').graphData({{nodes,links}})
    .nodeThreeObject(labelSprite)
    .linkColor(l=>l.target.id==='lost'?C.slate:l.target.id==='afa'?C.amber:C.accent)
    .linkWidth(l=>Math.max(.5,Math.log(l.v+1)*.62)).linkOpacity(.34)
    .linkDirectionalParticles(l=>Math.max(2,Math.round(Math.log(l.v+1)*2)))
    .linkDirectionalParticleSpeed(.005).linkDirectionalParticleWidth(2.3)
    .linkDirectionalParticleColor(l=>l.target.id==='won'?C.teal:
                                     l.target.id==='lost'?C.slate:
                                     l.target.id==='afa'?C.amber:C.accent)
    .showNavInfo(false).enableNodeDrag(false);
  // Explicit camera distance, not zoomToFit: the label sprites inflate each
  // node's bounding box, so zoomToFit pulls back until the pipeline is a
  // speck. Derived from the actual node span and the container aspect.
  const SPAN=560;
  function fit(){{
    const w=el.clientWidth, h=el.clientHeight;
    G.width(w).height(h);
    const vfov=50*Math.PI/180;
    const hSpan=Math.tan(vfov/2)*(w/h);          // half-width visible per unit distance
    G.cameraPosition({{x:0, y:0, z:(SPAN/2)/hSpan*1.12}});  // 1.12 = breathing room
  }}
  fit(); addEventListener('resize',fit);
}})();

/* ---- learning curve ---- */
(function(){{
  const svg=document.getElementById('curve');
  const cps=Object.keys(D.curve.bandit).map(Number).sort((a,b)=>a-b);
  const series=[['baseline',C.slate],['bandit',C.teal],['oracle',C.amber]];
  // Right gutter reserved for series labels: at n=800 all three lines converge
  // and end-of-line labels drawn in place would sit on top of each other.
  const W=460,H=214,P={{l:6,r:74,t:14,b:24}};
  const all=series.flatMap(([k])=>cps.map(c=>D.curve[k][c]));
  const mx=Math.max(...all), mn=Math.min(...all,0);
  const x=i=>P.l+i*(W-P.l-P.r)/(cps.length-1);
  const y=v=>H-P.b-(v-mn)/(mx-mn)*(H-P.t-P.b);
  let o='';
  cps.forEach((c,i)=>o+=`<text x="${{x(i)}}" y="${{H-7}}" fill="#5C6880" font-size="9.5"
    text-anchor="middle" font-family="JetBrains Mono">${{c}}</text>`);
  // Spread the labels vertically so they never collide, and tie each to its
  // line with a short leader.
  const ends=series.map(([k,col])=>({{k,col,v:D.curve[k][cps.at(-1)]}}))
                   .sort((a,b)=>a.v-b.v);
  const slot=(H-P.t-P.b)/3;
  series.forEach(([k,col])=>{{
    o+=`<polyline points="${{cps.map((c,i)=>x(i)+','+y(D.curve[k][c])).join(' ')}}" fill="none"
       stroke="${{col}}" stroke-width="2" stroke-linejoin="round"
       stroke-dasharray="1400" stroke-dashoffset="1400">
       <animate attributeName="stroke-dashoffset" to="0" dur="1.4s" fill="freeze"/></polyline>`;
    cps.forEach((c,i)=>o+=`<circle cx="${{x(i)}}" cy="${{y(D.curve[k][c])}}" r="2.6" fill="${{col}}"/>`);
  }});
  ends.forEach((e,i)=>{{
    const ex=x(cps.length-1), ey=y(e.v);
    const ly=H-P.b-slot*(i+0.5);
    o+=`<polyline points="${{ex}},${{ey}} ${{ex+11}},${{ly}} ${{ex+18}},${{ly}}" fill="none"
        stroke="${{e.col}}" stroke-width="1" opacity=".45"/>`;
    o+=`<text x="${{ex+22}}" y="${{ly+3.5}}" fill="${{e.col}}" font-size="10"
        font-weight="700">${{e.k}}</text>`;
  }});
  svg.innerHTML=o;
}})();

/* ---- explanations ---- */
(function(){{
  const ids=Object.keys(D.explanations);
  const tabs=document.getElementById('tabs'), body=document.getElementById('exp');
  if(!ids.length){{body.innerHTML='<p class="note">Run <code>python src/explain_exceptions.py</code>.</p>';return;}}
  const pill=o=>o==='refused_hard_decline'?'<span class="pill hard">compliance refusal</span>'
    :o==='recovered'?'<span class="pill soft">recovered</span>'
    :'<span class="pill hard">retries exhausted</span>';
  function show(id){{
    const e=D.explanations[id], c=e.case;
    body.innerHTML=`<div class="exp-h"><b class="mono">${{id}}</b>${{pill(c.outcome)}}
      <span style="color:var(--dim);font-size:.8rem" class="num">\\u20B9${{c.amount_inr.toLocaleString('en-IN')}}
      &middot; ${{c.decline_code.replace(/_/g,' ')}}</span></div>
      <div class="exp-t">${{e.explanation}}</div>
      <div class="exp-m">${{c.attempt_count}} attempt(s) &middot; ${{c.channels_used.join(', ')||'no channel'}}
      &middot; stopping rule: ${{c.stopping_rule||'none'}} &middot; text by ${{e.source}}</div>`;
    [...tabs.children].forEach(t=>t.classList.toggle('on',t.dataset.id===id));
  }}
  ids.forEach(id=>{{const b=document.createElement('button');b.className='tab';b.dataset.id=id;
    b.textContent=id;b.onclick=()=>show(id);tabs.appendChild(b);}});
  show(ids[0]);
}})();

/* ---- tables ---- */
function table(el,head,rows){{
  document.getElementById(el).innerHTML=
    '<tr>'+head.map(h=>`<th class="${{h.n?'r':''}}">${{h.t}}</th>`).join('')+'</tr>'+
    rows.map(r=>'<tr>'+r.map((c,i)=>`<td class="${{head[i].n?'r':''}}">${{c}}</td>`).join('')+'</tr>').join('');
}}
const SOFT=['insufficient_funds','bank_server_timeout','issuer_soft_decline'];
table('t-code',[{{t:'Decline reason'}},{{t:'Type'}},{{t:'Count',n:1}},{{t:'Recovered',n:1}},{{t:'Gross',n:1}}],
  Object.entries(D.stats.by_decline_code).sort((a,b)=>b[1].total-a[1].total).map(([k,v])=>[
    k.replace(/_/g,' '), SOFT.includes(k)?'<span class="pill soft">soft</span>':'<span class="pill hard">hard</span>',
    v.total, v.recovered, '\\u20B9'+Math.round(v.gross_inr).toLocaleString('en-IN')]));
table('t-chan',[{{t:'Channel'}},{{t:'Attempts',n:1}},{{t:'Wins',n:1}},{{t:'Win rate',n:1}},{{t:'Spend',n:1}}],
  Object.entries(D.stats.by_channel).map(([k,v])=>[
    k.replace(/_/g,' '), v.attempts, v.wins, (v.wins/v.attempts*100).toFixed(1)+'%',
    '\\u20B9'+v.cost_inr.toFixed(2)]));
table('t-win',[{{t:'Retry window'}},{{t:'Attempts',n:1}},{{t:'Wins',n:1}},{{t:'Win rate',n:1}}],
  Object.entries(D.stats.by_window).sort((a,b)=>a[0]-b[0]).map(([k,v])=>[
    'T+'+k+'h', v.attempts, v.wins, (v.wins/v.attempts*100).toFixed(1)+'%']));
</script>
</body>
</html>
"""
    # Placeholder substitution rather than f-string interpolation: the live
    # blocks are CSS and JavaScript, both brace-heavy, and doubling every
    # brace in them to survive an f-string would make them unreadable and
    # unmaintainable for no gain.
    # Order matters in the script block: the engine defines the helpers, the
    # network layer runs statements at its own top level that use them, and
    # the UI code renders a first decision on load which needs both.
    html = (html
            .replace("/*LIVE_CSS*/", LIVE_CSS + NETWORK_CSS)
            .replace("<!--LIVE_HTML-->", LIVE_HTML + NETWORK_HTML)
            .replace("/*LIVE_ENGINE*/", ENGINE_JS.replace("LIVE_CONSTANTS", live_constants(posteriors)))
            .replace("/*LIVE_NETWORK*/", NETWORK_JS)
            .replace("/*LIVE_UI*/", LIVE_UI_JS))

    with open(OUT_PATH, "w") as f:
        f.write(html)
    print(f"Wrote {os.path.abspath(OUT_PATH)} ({os.path.getsize(OUT_PATH)//1024} KB)")


if __name__ == "__main__":
    main()
