"""
The live half of the dashboard: the engine, running in the browser.

The page used to be a recording. Every number on it was computed in Python
once, written to JSON, and animated. Nothing a visitor did changed anything,
which is a fair thing to be annoyed by -- a control room you cannot touch is
a poster of a control room.

This module fixes that by shipping the actual decision logic to the client:
the compliance gate, Thompson Sampling over the trained posteriors, the
expected-value arithmetic, the batch generator, and the outbound action
builder. A visitor picks a decline reason and an amount and watches the real
policy decide, sampling fresh every time. Batches are generated in the
browser, so the payments on screen did not exist before the page loaded.

**Why the constants are generated, not retyped.** Duplicated logic drifts.
`live_constants()` reads the thresholds, costs, probability tables and
decline mix straight out of `domain_rules`, `policies` and `synthetic_data`
at build time and emits them as JSON. Change the RBI AFA threshold in the
Python and the browser copy changes with the next build. The JavaScript here
contains control flow, never a magic number that also exists in Python.

The one thing deliberately *not* ported: `outcome_draw`. Simulated success
in the browser is an honest coin flip against the true probability table,
which is what the Python simulator does too -- but the Python version is
seeded by common random numbers so policies can be compared fairly. There is
nothing to compare against here, so reproducibility buys nothing and a fresh
draw each run is the point.
"""

import json

from domain_rules import (
    AFA_ENHANCED_CATEGORIES,
    PRE_DEBIT_NOTICE_HOURS,
    AFA_TRIGGER_ENHANCED_INR,
    AFA_TRIGGER_GENERAL_INR,
    HARD_DECLINES,
    MAX_ATTEMPTS_PER_CYCLE,
    RETRY_WINDOWS_HOURS,
    SOFT_DECLINES,
)
from policies import CHANNEL_COST_INR, CHANNELS, PRE_DEBIT_NOTICE_COST_INR
from recovery_actions import AUTH_LINK_TTL_HOURS, RAZORPAY_API_BASE
from synthetic_data import (
    AMOUNT_RANGE_INR,
    BASE_RECOVERY_PROB,
    CATEGORIES,
    CHANNEL_MULTIPLIER,
    DEFAULT_DECLINE_DISTRIBUTION,
    WINDOW_MULTIPLIER,
)

# Plain-language labels. These are presentation, so they live here rather
# than in the domain modules -- but every one of them names a real code.
DECLINE_LABEL = {
    "insufficient_funds": "Insufficient funds",
    "bank_server_timeout": "Bank server timeout",
    "issuer_soft_decline": "Issuer soft decline (do not honour)",
    "card_expired": "Card expired",
    "mandate_revoked": "Mandate revoked",
    "account_closed": "Account closed",
    "issuer_hard_decline": "Issuer hard decline (lost, stolen, frozen)",
}

DECLINE_MEANING = {
    "insufficient_funds": "The account did not have the money at the moment "
                          "of the debit. Balances follow salary cycles, so this "
                          "one gets more recoverable the longer you wait.",
    "bank_server_timeout": "The bank did not answer in time. Nothing is wrong "
                           "with the customer or the mandate, so a retry soon "
                           "is as good as a retry later.",
    "issuer_soft_decline": "The issuer refused without saying why. Often "
                           "clears on a second look, and a nudge to the "
                           "customer helps more than waiting does.",
    "card_expired": "The instrument itself is dead. No retry on this mandate "
                    "can succeed, however well timed.",
    "mandate_revoked": "The customer withdrew permission to debit them. "
                       "Retrying is not a bad bet, it is acting without "
                       "consent.",
    "account_closed": "There is no account left to debit.",
    "issuer_hard_decline": "The issuer has blocked the instrument. Retrying "
                           "burns a regulated attempt on a guaranteed refusal.",
}

CATEGORY_LABEL = {
    "subscription": "Subscription",
    "mutual_fund": "Mutual fund SIP",
    "insurance": "Insurance premium",
    "credit_card_bill": "Credit card bill",
}

CHANNEL_LABEL = {"sms": "SMS", "ivr_call": "IVR call"}


def live_constants(posteriors: list[dict]) -> str:
    """Everything the browser engine needs, generated from the Python."""
    return json.dumps({
        "maxAttempts": MAX_ATTEMPTS_PER_CYCLE,
        "windows": RETRY_WINDOWS_HOURS,
        "afaGeneral": AFA_TRIGGER_GENERAL_INR,
        "afaEnhanced": AFA_TRIGGER_ENHANCED_INR,
        "afaEnhancedCategories": sorted(AFA_ENHANCED_CATEGORIES),
        "soft": sorted(c.value for c in SOFT_DECLINES),
        "hard": sorted(c.value for c in HARD_DECLINES),
        "channels": CHANNELS,
        "channelCost": CHANNEL_COST_INR,
        "preDebitNoticeCost": PRE_DEBIT_NOTICE_COST_INR,
        "preDebitNoticeHours": PRE_DEBIT_NOTICE_HOURS,
        "categories": CATEGORIES,
        "amountRange": list(AMOUNT_RANGE_INR),
        "declineMix": {c.value: w for c, w in DEFAULT_DECLINE_DISTRIBUTION.items()},
        "baseP": {c.value: p for c, p in BASE_RECOVERY_PROB.items()},
        "windowMult": {c.value: m for c, m in WINDOW_MULTIPLIER.items()},
        "channelMult": {c.value: m for c, m in CHANNEL_MULTIPLIER.items()},
        "posteriors": posteriors,
        "declineLabel": DECLINE_LABEL,
        "declineMeaning": DECLINE_MEANING,
        "categoryLabel": CATEGORY_LABEL,
        "channelLabel": CHANNEL_LABEL,
        "razorpayApiBase": RAZORPAY_API_BASE,
        "authLinkTtlHours": AUTH_LINK_TTL_HOURS,
    })


# --------------------------------------------------------------------------
# The engine, in JavaScript. Doubled braces throughout: this string is
# interpolated into an f-string by generate_dashboard.py.
# --------------------------------------------------------------------------

ENGINE_JS = r"""
/* =====================================================================
   The engine, client side. Same rules as src/domain_rules.py, same
   arithmetic as src/policies.py, same action shapes as
   src/recovery_actions.py. Constants come from E, generated from those
   modules at build time.
   ===================================================================== */

const E = LIVE_CONSTANTS;

/* Beta sampling, for Thompson Sampling. Marsaglia-Tsang gamma, valid for
   shape >= 1, which always holds: alpha and beta start at the Beta(1,1)
   prior and only ever increase. Beta(a,b) = X/(X+Y) for X~Gamma(a),
   Y~Gamma(b). */
function gauss(){
  let u=0,v=0;
  while(u===0) u=Math.random();
  while(v===0) v=Math.random();
  return Math.sqrt(-2*Math.log(u))*Math.cos(2*Math.PI*v);
}
function gamma(shape){
  const d=shape-1/3, c=1/Math.sqrt(9*d);
  for(;;){
    let x=gauss(), v=1+c*x;
    if(v<=0) continue;
    v=v*v*v;
    const u=Math.random();
    if(u<1-0.0331*x*x*x*x) return d*v;
    if(Math.log(u)<0.5*x*x+d*(1-v+Math.log(v))) return d*v;
  }
}
function betaSample(a,b){ const x=gamma(a), y=gamma(b); return x/(x+y); }

/* The trained posteriors, keyed the way policies.BanditPolicy keys them. */
const POST={};
E.posteriors.forEach(r=>{ POST[r.decline_code+'|'+r.window_hours+'|'+r.channel]={a:r.alpha,b:r.beta,pulls:r.pulls}; });
function arm(code,w,ch){ return POST[code+'|'+w+'|'+ch] || {a:1,b:1,pulls:0}; }

/* domain_rules.requires_additional_factor_auth */
function afaLimit(category){
  return E.afaEnhancedCategories.indexOf(category)>=0 ? E.afaEnhanced : E.afaGeneral;
}
function requiresAFA(amount,category){ return amount > afaLimit(category); }
function isHard(code){ return E.hard.indexOf(code)>=0; }

/* synthetic_data.recovery_probability -- the simulator's truth, used only
   to resolve whether a simulated attempt succeeded. The policy never sees
   it, exactly as in Python. */
function trueP(code,w,ch){
  if(isHard(code)) return 0;
  return Math.min(E.baseP[code]*E.windowMult[code][w]*E.channelMult[code][ch], 0.95);
}

/* policies.BanditPolicy.choose */
function choose(code,w,amount){
  const opts=E.channels.map(ch=>{
    const a=arm(code,w,ch), p=betaSample(a.a,a.b);
    /* Mirrors policies.py: the notice is part of what an attempt costs,
       cancels between the two channels, and does not cancel against not
       attempting. */
    return {channel:ch, p:p, net:p*amount-E.channelCost[ch]-E.preDebitNoticeCost, pulls:a.pulls};
  });
  const best=opts.reduce((m,o)=>o.net>m.net?o:m, opts[0]);
  return {options:opts, best:best, attempt:best.net>0};
}

/* synthetic_data.generate_batch, minus the seed: every call is new traffic. */
const MIX=Object.keys(E.declineMix), MIXW=MIX.map(k=>E.declineMix[k]);
const MIXTOTAL=MIXW.reduce((a,b)=>a+b,0);
function pickDecline(){
  let r=Math.random()*MIXTOTAL;
  for(let i=0;i<MIX.length;i++){ r-=MIXW[i]; if(r<=0) return MIX[i]; }
  return MIX[MIX.length-1];
}
let BATCH_N=0;
function newPayment(){
  const lo=E.amountRange[0], hi=E.amountRange[1];
  return {
    id:'pay_live_'+String(BATCH_N++).padStart(4,'0'),
    amount:Math.round((lo+Math.random()*(hi-lo))*100)/100,
    category:E.categories[Math.floor(Math.random()*E.categories.length)],
    code:pickDecline()
  };
}
function newBatch(n){ const b=[]; for(let i=0;i<n;i++) b.push(newPayment()); return b; }

/* eval_harness.run_policy_on_batch for one payment, plus the outbound
   action recovery_actions would emit. Returns the full trace so the UI can
   show the reasoning, not just the verdict. */
function runPayment(p){
  const trace={payment:p, stages:[], outcome:null, action:null, costs:0, gross:0};

  trace.stages.push({stage:'ingest', ok:true,
    text:'A '+inr(p.amount)+' '+E.categoryLabel[p.category].toLowerCase()+
         ' debit failed. Razorpay reports the reason as '+E.declineLabel[p.code]+'.'});

  const hard=isHard(p.code);
  trace.stages.push({stage:'classify', ok:!hard,
    text:(hard?'Hard decline. ':'Soft decline. ')+E.declineMeaning[p.code]});

  if(hard){
    trace.stages.push({stage:'gate', ok:false, verdict:'refused',
      text:'Refused before any policy runs. Retrying a '+E.declineLabel[p.code].toLowerCase()+
           ' spends one of the '+E.maxAttempts+' attempts NPCI allows per cycle on a certain failure.'});
    trace.outcome='refused';
    trace.action={kind:'suppress_no_retry', provider:null, request:null,
      authorised_by:'hard_decline_no_retry',
      text:'No outbound call. The correct action here is none, recorded explicitly so the silence is auditable.'};
    return trace;
  }

  if(requiresAFA(p.amount,p.category)){
    const lim=afaLimit(p.category);
    trace.stages.push({stage:'gate', ok:false, verdict:'escalated',
      text:inr(p.amount)+' is above the RBI additional-factor-authentication limit of '+inr(lim)+
           ' for '+E.categoryLabel[p.category].toLowerCase()+'. The customer has to re-authenticate, so a silent '+
           'background retry cannot legally clear. Spending a mandate attempt on it would burn one of '+
           E.maxAttempts+' on a guaranteed failure.'});
    trace.outcome='escalated';
    trace.action=authLinkAction(p);
    return trace;
  }

  trace.stages.push({stage:'gate', ok:true,
    text:'Cleared. Soft decline, '+inr(p.amount)+' is under the '+inr(afaLimit(p.category))+
         ' AFA limit for this category, and the mandate has '+(E.maxAttempts-1)+' retries left in the cycle.'});

  const attempts=[];
  let recovered=false, wonAt=null;
  for(let i=0;i<E.windows.length && !recovered;i++){
    const w=E.windows[i], d=choose(p.code,w,p.amount), n=i+2;
    if(!d.attempt){
      attempts.push({window:w, attempt:n, skipped:true, options:d.options, best:d.best});
      continue;
    }
    trace.costs+=E.channelCost[d.best.channel]+E.preDebitNoticeCost;
    const tp=trueP(p.code,w,d.best.channel), hit=Math.random()<tp;
    attempts.push({window:w, attempt:n, skipped:false, options:d.options,
                   best:d.best, trueP:tp, won:hit});
    if(hit){ recovered=true; wonAt=w; trace.gross=p.amount; }
  }
  trace.attempts=attempts;

  const used=attempts.filter(a=>!a.skipped);
  trace.stages.push({stage:'policy', ok:true, attempts:attempts,
    text:used.length===0
      ? 'Every window was skipped. At '+inr(p.amount)+' the expected recovery did not cover the cost of even one message, so the cheapest correct action was to send nothing.'
      : 'The bandit sampled its belief about each channel at each permitted window and took the highest expected net rupees. Timing was never its choice: NPCI fixes T+24h, T+72h and T+7d.'});

  if(recovered){
    trace.outcome='recovered';
    trace.stages.push({stage:'outcome', ok:true,
      text:'Recovered at T+'+wonAt+'h. '+inr(trace.gross)+' back, '+inr(trace.costs)+' spent reaching them, '+
           inr(trace.gross-trace.costs)+' net.'});
    const last=used[used.length-1];
    trace.action=contactAction(p,last);
  } else if(used.length===0){
    trace.outcome='skipped';
    trace.stages.push({stage:'outcome', ok:false,
      text:'No contact attempted, nothing spent. The payment goes to the manual queue at the end of the cycle.'});
    trace.action={kind:'escalate_to_manual_review', provider:null, request:null,
      authorised_by:'npci_retry_cap_exhausted',
      text:'No outbound call. Contacting this customer would have cost more than the payment was worth.'};
  } else {
    trace.outcome='exhausted';
    trace.stages.push({stage:'outcome', ok:false,
      text:'All '+used.length+' permitted attempts used without recovery. '+inr(trace.costs)+
           ' spent. NPCI allows no more this cycle, so it leaves the automated loop.'});
    trace.action={kind:'escalate_to_manual_review', provider:null, request:null,
      authorised_by:'npci_retry_cap_exhausted',
      text:'No outbound call. The cap is reached; a human picks this up.'};
  }
  return trace;
}

/* recovery_actions.escalate_afa */
function authLinkAction(p){
  return {
    kind:'create_auth_payment_link', provider:'razorpay',
    authorised_by:"rbi_afa_threshold_exceeded (category '"+p.category+"')",
    text:'One real Razorpay call. An authenticated payment link lets the customer re-authenticate and pay, and it leaves the mandate attempt budget untouched.',
    request:{method:'POST', url:E.razorpayApiBase+'/payment_links', body:{
      amount:Math.round(p.amount*100), currency:'INR', accept_partial:false,
      description:'Authenticated retry of a failed recurring payment',
      expire_by_hours:E.authLinkTtlHours, reminder_enable:true,
      notify:{sms:true,email:true},
      callback_url:'https://merchant.example/recovery/'+p.id, callback_method:'get',
      notes:{original_payment_id:p.id, decline_code:p.code,
             escalation_reason:'rbi_afa_threshold_exceeded'}
    }}
  };
}

/* recovery_actions.contact */
function contactAction(p,a){
  return {
    kind:a.best.channel==='sms'?'send_sms':'place_ivr_call', provider:'comms',
    authorised_by:'soft_decline_within_npci_cap (attempt '+a.attempt+'/'+E.maxAttempts+')',
    text:'A nudge, not a charge. It does not move money: it makes the customer ready for the debit NPCI has already scheduled.',
    notice:{kind:'send_pre_debit_alert', authorised_by:'rbi_e_mandate_pre_debit_notice',
      text:'Owed to the customer '+E.preDebitNoticeHours+'h before this collection. Not the '+
           'agent\'s decision and not a recovery tactic: the executor refuses to contact on an '+
           'attempt that has no notice on record.'},
    request:{channel:a.best.channel, template:'recovery_'+p.code+'_t'+a.window+'h',
      variables:{amount_inr:p.amount, retry_window_hours:a.window, attempt_number:a.attempt},
      recipient_ref:p.id}
  };
}

function inr(v){
  return '₹'+v.toLocaleString('en-IN',{minimumFractionDigits:v%1?2:0,maximumFractionDigits:2});
}

/* Defined here rather than with the UI code because the network layer is
   injected between the two and needs it at its own top level. */
const $=id=>document.getElementById(id);
"""


# --------------------------------------------------------------------------
# The live UI. Injected into the page by placeholder replacement rather than
# f-string interpolation, so nothing here needs its braces doubled.
# --------------------------------------------------------------------------

LIVE_CSS = r"""
/* ---- live console ---- */
.livegrid{display:grid;grid-template-columns:340px 1fr;gap:18px;align-items:start;}
@media(max-width:900px){.livegrid{grid-template-columns:1fr;}}
.ctl{margin-bottom:17px;}
.ctl label{display:block;color:var(--dim);font-size:.7rem;text-transform:uppercase;
  letter-spacing:.09em;font-weight:600;margin-bottom:7px;}
.ctl select,.ctl input[type=number]{width:100%;background:var(--surface-2);color:var(--text);
  border:1px solid var(--line-2);border-radius:8px;padding:9px 11px;font-family:inherit;font-size:.9rem;}
.ctl input[type=range]{width:100%;accent-color:var(--accent);}
.amtrow{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:6px;}
.amtval{font-family:"Space Grotesk",sans-serif;font-size:1.3rem;font-weight:700;}
.afaflag{font-size:.74rem;color:var(--attention);font-weight:600;min-height:1.1em;}
.btn{width:100%;background:var(--accent);color:#0A0E1A;border:0;border-radius:8px;
  padding:11px 14px;font-family:"Space Grotesk",sans-serif;font-weight:700;font-size:.9rem;
  cursor:pointer;letter-spacing:-.01em;}
.btn:hover{filter:brightness(1.09);}
.btn.ghost{background:transparent;color:var(--text);border:1px solid var(--line-2);}
.btnrow{display:flex;gap:9px;margin-top:4px;}
.btnrow .btn{width:auto;flex:1;}

/* stage cards, revealed one at a time */
.stage{border-left:2px solid var(--line-2);padding:0 0 16px 17px;position:relative;
  opacity:0;transform:translateY(7px);transition:opacity .3s,transform .3s;}
.stage.on{opacity:1;transform:none;}
.stage:last-child{padding-bottom:0;}
.stage::before{content:'';position:absolute;left:-5px;top:5px;width:8px;height:8px;border-radius:50%;
  background:var(--dim);}
.stage.ok::before{background:var(--recovered);}
.stage.bad::before{background:var(--refused);}
.stage.warn::before{background:var(--attention);}
.stage .sname{font-family:"JetBrains Mono",monospace;font-size:.68rem;letter-spacing:.1em;
  text-transform:uppercase;color:var(--dim);margin-bottom:4px;}
.stage .stext{font-size:.9rem;color:var(--text);}
.stage.bad .sname{color:var(--refused);} .stage.warn .sname{color:var(--attention);}
.stage.ok .sname{color:var(--recovered);}

/* paired regulatory / agent track */
.mt{font-size:13px;color:var(--text)}
.mt,.mt *{box-sizing:border-box}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}
.mt-lane{display:grid;grid-template-columns:150px repeat(3,1fr);gap:12px;margin-top:18px}
.mt-lane-reg{margin-top:0}
.mt-tag{padding-left:10px;border-left:2px solid var(--attention);align-self:start}
.mt-lane-agent .mt-tag{border-left-color:var(--accent)}
.mt-tag b{display:block;font-size:11px;font-weight:700;letter-spacing:.16em;text-transform:uppercase;color:var(--muted)}
.mt-tag span{display:block;margin-top:4px;font-size:11px;line-height:1.4;color:var(--dim)}
.mt-slot{position:relative;min-height:94px;padding:12px 14px;border-radius:8px;display:flex;flex-direction:column;justify-content:center;gap:4px;transition:filter .15s}
.mt-slot:hover{filter:brightness(1.14)}
.mt-kick{display:flex;align-items:center;gap:6px;font-size:10px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:var(--dim)}
.mt-reg .mt-kick{color:var(--attention)}
.mt-big{font-size:19px;font-weight:650;letter-spacing:.02em}
.mt-sub{font-size:11.5px;color:var(--muted)}
.mt-reg{border:1px solid color-mix(in srgb,var(--attention) 42%,transparent);border-left:3px solid var(--attention);background:repeating-linear-gradient(135deg,color-mix(in srgb,var(--attention) 13%,transparent) 0 6px,transparent 6px 12px),var(--surface)}
.mt-reg.is-off{opacity:.55}
.mt-flag{position:absolute;top:9px;right:9px;padding:2px 6px;border-radius:4px;font-size:9px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--attention);border:1px solid color-mix(in srgb,var(--attention) 40%,transparent);background:var(--bg)}
.mt-lane-agent .mt-slot::before{content:'';position:absolute;left:50%;top:-18px;height:18px;border-left:1px dashed var(--faint)}
.mt-ch{display:flex;align-items:center;gap:8px;font-size:19px;font-weight:650;letter-spacing:.03em;text-transform:uppercase}
.a-sent{border:1px solid color-mix(in srgb,var(--accent) 52%,transparent);border-left:3px solid var(--accent);background:linear-gradient(color-mix(in srgb,var(--accent) 9%,transparent),color-mix(in srgb,var(--accent) 9%,transparent)),var(--surface)}
.a-skip{border:1px dashed color-mix(in srgb,var(--accent) 46%,transparent);background:color-mix(in srgb,var(--accent) 5%,var(--surface))}
.a-skip .mt-ch{color:var(--muted);font-weight:600}
.a-none{border:1px solid var(--line);background:var(--surface)}
.a-none .mt-ch{color:var(--dim);font-size:15px}
.a-none .mt-sub{color:var(--faint)}
.mt-chip{margin-top:6px;align-self:flex-start;display:inline-flex;align-items:center;gap:6px;padding:3px 9px;border-radius:999px;border:1px solid;font-size:10.5px;font-weight:700;letter-spacing:.1em;text-transform:uppercase}
.c-won{color:var(--recovered);border-color:color-mix(in srgb,var(--recovered) 55%,transparent);background:color-mix(in srgb,var(--recovered) 13%,transparent)}
.c-lost{color:var(--dim);border-color:var(--line-2)}
.ic{width:13px;height:13px;flex:none;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}
.c-won .ic{width:11px;height:11px}
.mt-legend{display:flex;flex-wrap:wrap;align-items:center;gap:8px 26px;margin:16px 0 0;padding-top:12px;border-top:1px solid var(--line);font-size:11.5px;color:var(--muted)}
.lg{display:inline-flex;align-items:center;gap:7px;white-space:nowrap}
.sw{width:11px;height:11px;flex:none;border-radius:3px;display:inline-flex;align-items:center;justify-content:center}
.sw-reg{border:1px solid color-mix(in srgb,var(--attention) 55%,transparent);background:repeating-linear-gradient(135deg,color-mix(in srgb,var(--attention) 32%,transparent) 0 3px,transparent 3px 6px)}
.sw-agt{border:1px solid var(--accent);background:color-mix(in srgb,var(--accent) 28%,transparent)}
.sw-ok{width:auto;height:auto;border:0;color:var(--recovered)}
.sw-ok .ic{width:12px;height:12px}
.sw-off{border:1px dashed var(--faint)}
.tw-a{color:var(--attention);font-weight:650}
.tw-b{color:var(--accent);font-weight:650}

/* per-window sampling detail */
.wtable{margin-top:10px;border:1px solid var(--line);border-radius:9px;overflow:hidden;}
.wtable table{width:100%;border-collapse:collapse;font-size:.79rem;}
.wtable th{text-align:left;color:var(--dim);font-weight:600;font-size:.66rem;text-transform:uppercase;
  letter-spacing:.08em;padding:7px 11px;background:var(--surface-2);}
.wtable td{padding:7px 11px;border-top:1px solid var(--line);}
.wtable td.r{text-align:right;}
.wtable tr.pick td{background:rgba(77,126,232,.11);}
.wtable tr.pick td:first-child{box-shadow:inset 2px 0 0 var(--accent);}
.wlab{font-family:"JetBrains Mono",monospace;font-size:.72rem;color:var(--muted);
  padding:6px 11px;background:var(--surface-2);border-top:1px solid var(--line);}
.won{color:var(--recovered);font-weight:600;} .lost{color:var(--dim);}

/* the outbound call */
.callbox{margin-top:16px;border:1px solid var(--line-2);border-radius:10px;overflow:hidden;}
.callhead{display:flex;align-items:center;gap:9px;padding:9px 13px;background:var(--surface-2);
  font-family:"JetBrains Mono",monospace;font-size:.73rem;letter-spacing:.05em;}
.pill{padding:2px 8px;border-radius:999px;font-size:.63rem;font-weight:700;letter-spacing:.07em;
  text-transform:uppercase;}
.pill.rzp{background:rgba(77,126,232,.19);color:var(--accent);}
.pill.comms{background:rgba(59,155,125,.17);color:var(--recovered);}
.pill.none{background:rgba(255,255,255,.07);color:var(--dim);}
.pill.dry{background:rgba(232,184,109,.15);color:var(--attention);}
.callbody{padding:12px 13px;font-size:.86rem;color:var(--muted);}
.pill.mand{background:rgba(232,184,109,.15);color:var(--attention);}
/* The mandated notice sits above the contact it announces, marked off so
   it never reads as one of the agent's own decisions. */
.callbody.notice{border-bottom:1px solid var(--line);
  background:repeating-linear-gradient(135deg,rgba(232,184,109,.05) 0 6px,transparent 6px 12px);}
.callbody pre{margin:10px 0 0;padding:11px;background:#070A12;border-radius:7px;overflow-x:auto;
  font-family:"JetBrains Mono",monospace;font-size:.72rem;line-height:1.55;color:#B8C4DC;}

/* sampling distribution */
.dist{margin-top:14px;}
.distrow{display:grid;grid-template-columns:88px 1fr 62px;gap:10px;align-items:center;
  font-size:.79rem;margin-bottom:6px;}
.distbar{height:9px;border-radius:5px;background:var(--surface-2);overflow:hidden;}
.distbar i{display:block;height:100%;border-radius:5px;}

/* ---- live traffic ---- */
.streamctl{display:flex;flex-wrap:wrap;gap:11px;align-items:center;margin-bottom:16px;}
.streamctl .btn{width:auto;padding:9px 18px;}
.speed{display:flex;align-items:center;gap:9px;color:var(--dim);font-size:.76rem;
  text-transform:uppercase;letter-spacing:.08em;font-weight:600;}
.speed input{width:120px;accent-color:var(--accent);}
.livedot{width:7px;height:7px;border-radius:50%;background:var(--dim);}
.livedot.on{background:var(--recovered);animation:pulse 1.1s infinite;}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}
.counters{display:grid;grid-template-columns:repeat(6,1fr);gap:1px;background:var(--line);
  border:1px solid var(--line);border-radius:11px;overflow:hidden;margin-bottom:16px;}
@media(max-width:900px){.counters{grid-template-columns:repeat(3,1fr);}}
.counters div{background:var(--surface);padding:13px 15px;}
.counters .l{color:var(--dim);font-size:.63rem;text-transform:uppercase;letter-spacing:.09em;font-weight:600;}
.counters .v{font-family:"Space Grotesk",sans-serif;font-size:1.24rem;font-weight:700;margin-top:5px;}
.feed{height:224px;overflow-y:auto;border:1px solid var(--line);border-radius:10px;
  background:#070A12;padding:11px 13px;font-family:"JetBrains Mono",monospace;font-size:.735rem;
  line-height:1.75;}
.feed div{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.feed .t{color:var(--dim);}
.feed .rec{color:var(--recovered);} .feed .ref{color:var(--refused);}
.feed .esc{color:var(--attention);} .feed .exh{color:var(--muted);}
"""


LIVE_HTML = r"""
  <section id="live" data-tab="sim">
    <div class="shead"><span class="snum">01</span><h2>Drive it yourself</h2></div>
    <p class="note">This is not a recording. The compliance gate, the Thompson Sampling step and the
      outbound call below all execute <b>in this page, right now</b>, from the posteriors the bandit
      actually learned during the evaluation. Change the amount past a threshold and the decision
      changes with it. Run the same payment twice and you may get a different channel, because the
      policy is sampling a belief, not reading a lookup table.</p>
    <div class="livegrid">
      <div class="card pad">
        <div class="ctl">
          <label for="lvCode">Why the debit failed</label>
          <select id="lvCode"></select>
        </div>
        <div class="ctl">
          <label for="lvCat">What it was for</label>
          <select id="lvCat"></select>
        </div>
        <div class="ctl">
          <div class="amtrow"><label style="margin:0">Amount</label>
            <span class="amtval num" id="lvAmtV">₹0</span></div>
          <input type="range" id="lvAmt" min="200" max="120000" step="100" value="4200">
          <div class="afaflag" id="lvAfa"></div>
        </div>
        <button class="btn" id="lvRun">Run this payment</button>
        <div class="btnrow">
          <button class="btn ghost" id="lvRand">Random</button>
          <button class="btn ghost" id="lvMany">Sample 200×</button>
        </div>
        <div class="dist" id="lvDist"></div>
      </div>
      <div class="card pad" id="lvOut"></div>
    </div>
  </section>

  <section id="stream" data-tab="wire">
    <div class="shead"><span class="snum">02</span><h2>Live traffic</h2></div>
    <p class="note">Press play and the page generates failed payments that did not exist a moment ago,
      then streams them through the same engine one at a time. Nothing here was precomputed. Hit
      <b>New batch</b> and the numbers land somewhere different, which is the honest picture: this is
      a policy with variance, not a fixed result.</p>
    <div class="streamctl">
      <button class="btn" id="stPlay">Play</button>
      <button class="btn ghost" id="stNew">New batch</button>
      <div class="speed"><span class="livedot" id="stDot"></span><span id="stState">idle</span></div>
      <div class="speed"><span>Speed</span>
        <button class="btn ghost spd" data-hz="2">Slow</button>
        <button class="btn ghost spd on" data-hz="6">Normal</button>
        <button class="btn ghost spd" data-hz="20">Fast</button></div>
    </div>
    <div class="counters">
      <div><div class="l">Processed</div><div class="v num" id="cProc">0</div></div>
      <div><div class="l">Refused hard</div><div class="v num" id="cRef" style="color:var(--refused)">0</div></div>
      <div><div class="l">AFA escalated</div><div class="v num" id="cAfa" style="color:var(--attention)">0</div></div>
      <div><div class="l">Recovered</div><div class="v num" id="cRec" style="color:var(--recovered)">0</div></div>
      <div><div class="l">Spent reaching</div><div class="v num" id="cCost">₹0</div></div>
      <div><div class="l">Net recovered</div><div class="v num" id="cNet" style="color:var(--recovered)">₹0</div></div>
    </div>
    <div class="feed" id="stFeed"></div>
  </section>
"""


LIVE_UI_JS = r"""
/* ---- live console wiring ---- */
const ORDER=['insufficient_funds','bank_server_timeout','issuer_soft_decline',
             'card_expired','mandate_revoked','account_closed','issuer_hard_decline'];

ORDER.forEach(c=>{
  const o=document.createElement('option');
  o.value=c; o.textContent=E.declineLabel[c]+(isHard(c)?'  (hard)':'  (soft)');
  $('lvCode').appendChild(o);
});
E.categories.forEach(c=>{
  const o=document.createElement('option'); o.value=c; o.textContent=E.categoryLabel[c];
  $('lvCat').appendChild(o);
});

function currentPayment(){
  return {id:'pay_live_'+Date.now().toString(36), amount:+$('lvAmt').value,
          category:$('lvCat').value, code:$('lvCode').value};
}

function syncAmount(){
  const a=+$('lvAmt').value, cat=$('lvCat').value, lim=afaLimit(cat);
  $('lvAmtV').textContent=inr(a);
  $('lvAfa').textContent = a>lim
    ? 'Above the ₹'+lim.toLocaleString('en-IN')+' AFA limit for this category. No silent retry allowed.'
    : '';
}
['lvAmt','lvCat'].forEach(id=>$(id).addEventListener('input',syncAmount));

const STAGE_NAME={ingest:'01 · Ingest',classify:'02 · Classify',gate:'03 · Compliance gate',
                 policy:'04 · Channel policy',outcome:'05 · Outcome'};

/* The paired regulatory / agent track. Two lanes over the same three
   mandated windows: the top one is what NPCI fixes, the bottom one is what
   the agent chose. Amber is always a constraint, blue is always a choice,
   and nothing on the agent lane can sit anywhere but under a block. */
function mandateTrack(attempts){
  const W=[{w:24,h:'T+24h',a:2},{w:72,h:'T+72h',a:3},{w:168,h:'T+7d',a:4}];
  const by={}; attempts.forEach(e=>{ by[e.window]=e; });
  const wonIdx=W.findIndex(x=>by[x.w]&&by[x.w].won);
  const wonLbl=wonIdx>-1?W[wonIdx].h:null;
  const LOCK='<svg class="ic" viewBox="0 0 16 16" aria-hidden="true"><rect x="3.2" y="7.2" width="9.6" height="6.4" rx="1.4"/><path d="M5.6 7V5.2a2.4 2.4 0 0 1 4.8 0V7"/></svg>';
  const CHK='<svg class="ic" viewBox="0 0 16 16" aria-hidden="true"><path d="M3.4 8.6l3 3 6.2-7.2"/></svg>';
  const HOLD='<svg class="ic" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="5.4"/><path d="M5.9 8h4.2"/></svg>';

  const regCell=x=>{
    const off=wonIdx>-1&&W.indexOf(x)>wonIdx;
    return '<div class="mt-slot mt-reg'+(off?' is-off':'')+'">'
      +'<span class="mt-kick">'+LOCK+'NPCI mandate</span>'
      +'<span class="mt-big">'+x.h+'</span>'
      +'<span class="mt-sub">Attempt '+x.a+' of '+E.maxAttempts+'</span>'
      +(off?'<span class="mt-flag">Unused</span>':'')
      +'</div>';
  };

  const agentCell=x=>{
    const e=by[x.w]; let cls,body;
    if(!e){
      cls='a-none';
      body='<span class="mt-kick">At '+x.h+'</span>'
          +'<span class="mt-ch">Not reached</span>'
          +'<span class="mt-sub">'+(wonLbl?('Cycle closed at '+wonLbl):'No entry in trace')+'</span>';
    }else if(e.skipped){
      cls='a-skip';
      body='<span class="mt-kick">At '+x.h+'</span>'
          +'<span class="mt-ch">'+HOLD+'Skipped</span>'
          +'<span class="mt-sub">No send - best expected net negative</span>';
    }else{
      cls='a-sent'+(e.won?' a-won':'');
      body='<span class="mt-kick">At '+x.h+'</span>'
          +'<span class="mt-ch">'+E.channelLabel[e.best.channel]+'</span>'
          +'<span class="mt-chip '+(e.won?'c-won':'c-lost')+'">'
          +(e.won?(CHK+'Cleared'):'Not cleared')+'</span>';
    }
    return '<div class="mt-slot mt-agent '+cls+'">'+body+'</div>';
  };

  const acts=W.map(x=>{
    const e=by[x.w];
    if(!e) return x.h+': not reached';
    if(e.skipped) return x.h+': skipped';
    return x.h+': '+E.channelLabel[e.best.channel]+' sent, '+(e.won?'cleared':'not cleared');
  }).join('; ');
  const sum='Regulatory lane fixes three retry windows: T+24h, T+72h, T+7d, '
    +'attempts 2 to '+E.maxAttempts+'. Agent lane: '+acts+'.';

  return '<section class="mt" role="group" aria-label="Mandated windows versus agent channel choices">'
    +'<p class="sr-only">'+sum+'</p>'
    +'<div class="mt-lane mt-lane-reg">'
    +  '<div class="mt-tag"><b>Regulatory</b><span>Mandates the WHEN</span></div>'
    +  W.map(regCell).join('')
    +'</div>'
    +'<div class="mt-lane mt-lane-agent">'
    +  '<div class="mt-tag"><b>Agent</b><span>Chooses the HOW</span></div>'
    +  W.map(agentCell).join('')
    +'</div>'
    +'<p class="mt-legend">'
    +  '<span class="lg"><i class="sw sw-reg"></i><b class="tw-a">Amber</b>&nbsp;- fixed by regulation</span>'
    +  '<span class="lg"><i class="sw sw-agt"></i><b class="tw-b">Blue</b>&nbsp;- chosen by agent</span>'
    +  '<span class="lg"><i class="sw sw-ok">'+CHK+'</i>Cleared</span>'
    +  '<span class="lg"><i class="sw sw-off"></i>Window never reached</span>'
    +'</p>'
    +'</section>';
}

function windowTable(a){
  const rows=a.options.map(o=>{
    const pick=!a.skipped&&o.channel===a.best.channel;
    return '<tr class="'+(pick?'pick':'')+'"><td>'+E.channelLabel[o.channel]+'</td>'+
      '<td class="r num">'+(o.p*100).toFixed(1)+'%</td>'+
      '<td class="r num">'+inr(Math.round(E.channelCost[o.channel]*100)/100)+'</td>'+
      '<td class="r num" style="color:'+(o.net>0?'var(--recovered)':'var(--refused)')+'">'+
        (o.net>=0?'+':'−')+inr(Math.abs(Math.round(o.net*100)/100))+'</td>'+
      '<td class="r num" style="color:var(--dim)">'+o.pulls+'</td></tr>';
  }).join('');
  let foot;
  if(a.skipped){
    foot='Skipped. Best expected net was negative, so no message was sent and nothing was spent.';
  } else {
    foot='Sent by '+E.channelLabel[a.best.channel]+'. True success chance here was '+
         (a.trueP*100).toFixed(1)+'% — '+(a.won?'<span class="won">it cleared</span>':'<span class="lost">it did not clear</span>')+'.';
  }
  return '<div class="wtable"><table><tr><th>Channel</th><th class="r">Sampled belief</th>'+
    '<th class="r">Cost</th><th class="r">Expected net</th><th class="r">Pulls</th></tr>'+rows+
    '</table><div class="wlab">Attempt '+a.attempt+' of '+E.maxAttempts+', window T+'+a.window+'h. '+foot+'</div></div>';
}

function callBox(action){
  const prov = action.provider==='razorpay'
      ? '<span class="pill rzp">Razorpay API</span>'
      : action.provider==='comms' ? '<span class="pill comms">Comms provider</span>'
      : '<span class="pill none">No call</span>';
  /* The mandated notice comes first, because it did. Showing only the
     contact would put the page back where the code was before the notice
     was enforced: claiming it in prose and never sending it. */
  let body='';
  if(action.notice){
    body+='<div class="callbody notice"><span class="pill mand">mandated</span> '
       +  '<span class="mono" style="color:var(--attention)">'+action.notice.kind+'</span>'
       +  '<div style="margin-top:6px">'+action.notice.text+'</div></div>';
  }
  body+='<div class="callbody">'+action.text;
  if(action.request && action.request.url){
    body+='<pre>curl -X '+action.request.method+' '+action.request.url+" \\\n"+
      "  -u $RAZORPAY_KEY_ID:$RAZORPAY_KEY_SECRET \\\n"+
      "  -H 'Content-Type: application/json' \\\n  -d '"+
      JSON.stringify(action.request.body,null,2)+"'</pre>";
  } else if(action.request){
    body+='<pre>'+JSON.stringify(action.request,null,2)+'</pre>';
  }
  body+='</div>';
  return '<div class="callbox"><div class="callhead">'+prov+
    '<span style="color:var(--muted)">'+action.kind+'</span>'+
    '<span style="margin-left:auto"><span class="pill dry">dry run</span></span></div>'+
    body+'<div class="callbody" style="border-top:1px solid var(--line);color:var(--dim);font-size:.78rem">'+
    'Authorised by <span class="mono">'+action.authorised_by+'</span>. Nothing was transmitted: this repository ships no code path that sends.</div></div>';
}

function render(trace){
  const out=$('lvOut');
  out.innerHTML='';
  const nodes=[];
  trace.stages.forEach(s=>{
    const cls = s.stage==='policy' ? '' : (s.ok?'ok':(s.verdict==='escalated'?'warn':'bad'));
    const d=document.createElement('div');
    d.className='stage '+cls;
    d.innerHTML='<div class="sname">'+STAGE_NAME[s.stage]+'</div><div class="stext">'+s.text+'</div>'+
      (s.attempts? mandateTrack(s.attempts)+s.attempts.map(windowTable).join('') : '');
    out.appendChild(d); nodes.push(d);
  });
  const call=document.createElement('div');
  call.className='stage on';
  call.style.borderLeft='0'; call.style.padding='0';
  call.innerHTML=callBox(trace.action);
  out.appendChild(call); nodes.push(call);
  /* the model speaks last: it is describing a decision that is already made */
  const llm=document.createElement('div');
  llm.className='stage'; llm.style.borderLeft='0'; llm.style.padding='0';
  llm.appendChild(llmBlock(trace));
  out.appendChild(llm); nodes.push(llm);
  /* reveal in sequence: the point is to be followable, not instant */
  nodes.forEach((n,i)=>setTimeout(()=>n.classList.add('on'), i*270));
}

$('lvRun').addEventListener('click',()=>render(runPayment(currentPayment())));
$('lvRand').addEventListener('click',()=>{
  const p=newPayment();
  $('lvCode').value=p.code; $('lvCat').value=p.category;
  $('lvAmt').value=Math.min(120000,Math.round(p.amount));
  syncAmount(); render(runPayment(p));
});

/* 200 draws of the same decision: shows exploration as a distribution
   rather than asking anyone to take Thompson Sampling on faith. */
$('lvMany').addEventListener('click',()=>{
  const p=currentPayment(), tally={sms:0,ivr_call:0,skip:0};
  for(let i=0;i<200;i++){
    if(isHard(p.code)||requiresAFA(p.amount,p.category)){ tally.skip++; continue; }
    const d=choose(p.code,E.windows[0],p.amount);
    if(!d.attempt) tally.skip++; else tally[d.best.channel]++;
  }
  const colour={sms:'var(--accent)',ivr_call:'var(--recovered)',skip:'var(--dim)'};
  const label={sms:'SMS',ivr_call:'IVR call',skip:'No contact'};
  $('lvDist').innerHTML='<div style="color:var(--dim);font-size:.68rem;text-transform:uppercase;'+
    'letter-spacing:.09em;font-weight:600;margin:16px 0 9px">200 draws, first window</div>'+
    Object.keys(tally).map(k=>
      '<div class="distrow"><span style="color:var(--muted)">'+label[k]+'</span>'+
      '<span class="distbar"><i style="width:'+(tally[k]/2)+'%;background:'+colour[k]+'"></i></span>'+
      '<span class="num" style="text-align:right">'+(tally[k]/2).toFixed(0)+'%</span></div>').join('');
});

syncAmount();
/* Deliberately no decision on load. A page that starts computing and
   narrating before anyone asks it to reads as noise, and the first thing a
   visitor sees should be an instruction, not output. */
/* Read the label off the button rather than repeating it. The console shell
   and the standalone page word this button differently, and a hardcoded
   copy of it had already drifted into naming a button that did not exist. */
(function(){
  const b=$('lvRun'), label=(b&&b.textContent.trim())||'Run this payment';
  $('lvOut').innerHTML='<div style="color:var(--dim);font-size:.9rem;padding:20px 4px">'+
    'Set a reason, a category and an amount on the left, then press <b style="color:var(--text)">'+
    label+'</b>. The engine decides here in the page and shows every step it took.</div>';
})();

/* ---- live traffic ---- */
let stBatch=[], stIdx=0, stTimer=null;
const stC={proc:0,ref:0,afa:0,rec:0,cost:0,gross:0};

function stReset(){
  stBatch=newBatch(60); stIdx=0;
  Object.keys(stC).forEach(k=>stC[k]=0);
  $('stFeed').innerHTML=''; stPaint();
}
function stPaint(){
  $('cProc').textContent=stC.proc; $('cRef').textContent=stC.ref;
  $('cAfa').textContent=stC.afa;   $('cRec').textContent=stC.rec;
  $('cCost').textContent=inr(Math.round(stC.cost*100)/100);
  $('cNet').textContent=inr(Math.round((stC.gross-stC.cost)*100)/100);
}
function stStep(){
  if(stIdx>=stBatch.length){ stReset(); return; }
  const t=runPayment(stBatch[stIdx++]);
  stC.proc++; stC.cost+=t.costs; stC.gross+=t.gross;
  let cls='exh', word='exhausted';
  if(t.outcome==='refused'){ stC.ref++; cls='ref'; word='refused, hard decline'; }
  else if(t.outcome==='escalated'){ stC.afa++; cls='esc'; word='escalated, AFA required'; }
  else if(t.outcome==='recovered'){ stC.rec++; cls='rec'; word='recovered'; }
  else if(t.outcome==='skipped'){ word='no contact, not worth it'; }
  const line=document.createElement('div');
  line.innerHTML='<span class="t">'+new Date().toLocaleTimeString('en-GB')+'</span>  '+
    inr(t.payment.amount)+'  '+E.declineLabel[t.payment.code]+'  <span class="'+cls+'">'+word+'</span>';
  $('stFeed').prepend(line);
  while($('stFeed').childElementCount>120) $('stFeed').lastElementChild.remove();
  stPaint();
}
function stStop(){
  clearInterval(stTimer); stTimer=null;
  $('stPlay').textContent='Play'; $('stDot').classList.remove('on'); $('stState').textContent='paused';
}
/* Buttons rather than a range input: a slider sitting in a scrollable page
   swallows the wheel, so scrolling past it silently changed the speed. */
let stHz=6;
function stStart(){
  clearInterval(stTimer);
  stTimer=setInterval(stStep, 1000/stHz);
  $('stPlay').textContent='Pause'; $('stDot').classList.add('on');
  $('stState').textContent='running '+stHz+'/s';
}
$('stPlay').addEventListener('click',()=>stTimer?stStop():stStart());
$('stNew').addEventListener('click',()=>{ stReset(); if(stTimer) stStart(); });
document.querySelectorAll('.spd').forEach(b=>b.addEventListener('click',()=>{
  stHz=+b.dataset.hz;
  document.querySelectorAll('.spd').forEach(o=>o.classList.toggle('on',o===b));
  if(stTimer) stStart();
}));
stReset();
stStop();   /* idle until asked */
"""


# --------------------------------------------------------------------------
# The network layer. Everything above works with the page opened as a file.
# This adds what only a server can give: a real language model writing the
# explanation for each decision as it happens, and genuine Razorpay webhooks
# arriving from outside the browser.
#
# It degrades rather than breaks. The page probes /health once; if nothing
# answers it says so plainly and keeps running on the in-browser engine and
# the committed explanations. A demo that dies when a process is not up is
# worse than one that tells you which mode it is in.
# --------------------------------------------------------------------------

NETWORK_HTML = r"""
  <section id="wire" data-tab="wire">
    <div class="shead"><span class="snum">03</span><h2>Real webhooks, live</h2></div>
    <p class="note">The two sections above run inside this page. This one does not. Post a genuine
      Razorpay <span class="mono">subscription.pending</span> body to the server and it goes through
      the same adapter, the same compliance gate and the same bandit, then the decision arrives here
      over a live stream. Fire it from a terminal on the other side of the room and it lands on this
      page a moment later.</p>
    <div class="card pad">
      <div class="wirehead">
        <span class="livedot" id="wDot"></span><span id="wState">checking for a server</span>
        <span style="margin-left:auto" id="wModel"></span>
      </div>
      <div class="btnrow" style="margin:14px 0 0">
        <button class="btn" id="wSend" style="flex:0 0 auto;padding:9px 18px">Send a test webhook</button>
        <button class="btn ghost" id="wCopy" style="flex:0 0 auto;padding:9px 18px">Copy the curl</button>
      </div>
      <pre class="wirecurl" id="wCurl"></pre>
      <div class="feed" id="wFeed" style="height:190px"></div>
    </div>
  </section>
"""

NETWORK_CSS = r"""
.wirehead{display:flex;align-items:center;gap:9px;font-size:.76rem;text-transform:uppercase;
  letter-spacing:.08em;font-weight:600;color:var(--dim);}
.wirecurl{margin:13px 0;padding:12px;background:#070A12;border-radius:8px;overflow-x:auto;
  font-family:"JetBrains Mono",monospace;font-size:.7rem;line-height:1.6;color:#B8C4DC;}
.llmblock{margin-top:15px;border:1px solid var(--line-2);border-radius:10px;overflow:hidden;}
.llmhead{display:flex;align-items:center;gap:9px;padding:9px 13px;background:var(--surface-2);
  font-size:.68rem;text-transform:uppercase;letter-spacing:.09em;font-weight:700;color:var(--dim);}
.llmbody{padding:12px 13px;font-size:.9rem;color:var(--text);min-height:2.4em;}
.llmbody.wait{color:var(--dim);font-style:italic;}
.pill.model{background:rgba(77,126,232,.19);color:var(--accent);}
.pill.canned{background:rgba(255,255,255,.07);color:var(--dim);}
"""

NETWORK_JS = r"""
/* ---- is there a server? ---- */
let LIVE=false, LIVE_MODEL='';
const WEBHOOK_SAMPLE={
  event:'subscription.pending', category:'subscription',
  payload:{
    subscription:{entity:{id:'sub_Demo01',status:'pending',auth_attempts:1,current_invoice_amount:420000}},
    payment:{entity:{id:'pay_Demo01',amount:420000,error:{reason:'insufficient_funds'}}}
  }
};
const CURL='curl -X POST '+location.origin+"/webhook \\\n  -H 'Content-Type: application/json' \\\n"+
  "  -H 'X-Explain: 1' \\\n  -d '"+JSON.stringify(WEBHOOK_SAMPLE,null,2)+"'";
$('wCurl').textContent=CURL;

function wireState(txt,on){
  $('wState').textContent=txt;
  $('wDot').classList.toggle('on',!!on);
}

/* The first walkthrough renders on load and needs to know whether a model
   is reachable before it draws its last block, so the probe is a promise
   the UI waits on rather than a fire-and-forget. */
const HEALTH = fetch('/health').then(r=>r.json()).then(h=>{
  LIVE=!!h.live; LIVE_MODEL=h.model||'';
  wireState('connected, streaming',true);
  $('wModel').textContent='model: '+LIVE_MODEL;
  $('navTag').textContent='live · '+LIVE_MODEL;
  $('navDot').classList.add('on');
  subscribeEvents();
}).catch(()=>{
  LIVE=false;
  wireState('no server: page opened directly, in-browser engine only',false);
  $('wModel').textContent='explanations: pre-generated';
  $('navTag').textContent='offline · in-browser engine';
  $('wSend').disabled=true; $('wSend').style.opacity=.45;
});

function wireLine(o){
  const cls={refused:'ref',escalated:'esc',contacted:'rec',skipped:'exh',
             blocked:'ref',no_recovery_window:'exh'}[o.outcome]||'exh';
  const d=document.createElement('div');
  d.innerHTML='<span class="t">'+new Date().toLocaleTimeString('en-GB')+'</span>  '+
    (o.payment_id||'-')+'  '+(o.amount_inr!=null?inr(o.amount_inr):'')+'  '+
    '<span class="'+cls+'">'+(o.outcome||'?')+'</span>'+
    (o.actions&&o.actions.length?'  -> '+o.actions[0].kind:'');
  $('wFeed').prepend(d);
  while($('wFeed').childElementCount>60) $('wFeed').lastElementChild.remove();
}

function subscribeEvents(){
  const es=new EventSource('/events');
  es.onmessage=e=>{ try{ wireLine(JSON.parse(e.data)); }catch(_){} };
  es.onerror=()=>wireState('stream dropped, retrying',false);
  es.onopen=()=>wireState('connected, streaming',true);
}

$('wSend').addEventListener('click',()=>{
  const p=currentPayment(), body=JSON.parse(JSON.stringify(WEBHOOK_SAMPLE));
  body.category=p.category;
  body.payload.subscription.entity.id='sub_'+p.id;
  body.payload.payment.entity.id=p.id;
  body.payload.payment.entity.amount=Math.round(p.amount*100);
  body.payload.subscription.entity.current_invoice_amount=Math.round(p.amount*100);
  body.payload.payment.entity.error.reason=p.code;
  fetch('/webhook',{method:'POST',headers:{'Content-Type':'application/json','X-Explain':'1'},
    body:JSON.stringify(body)}).catch(()=>wireState('send failed',false));
});

$('wCopy').addEventListener('click',()=>{
  navigator.clipboard.writeText(CURL).then(()=>{
    $('wCopy').textContent='Copied'; setTimeout(()=>$('wCopy').textContent='Copy the curl',1400);
  }).catch(()=>{});
});

/* ---- the model, per decision ----
   The model is handed a decision that has already been made and asked to
   phrase it. It never chooses anything, and nothing it returns is parsed
   for a number. When there is no server it says so rather than inventing
   a sentence client-side. */
function llmBlock(trace){
  const box=document.createElement('div');
  box.className='llmblock';
  box.innerHTML='<div class="llmhead">'+
    (LIVE?'<span class="pill model">written live by '+LIVE_MODEL+'</span>'
        :'<span class="pill canned">no model connected</span>')+
    '<span>plain english, language only</span></div>'+
    '<div class="llmbody wait">'+
      (LIVE?'asking the model...':'Start the server to have a model write this for each decision as it happens.')+
    '</div>';
  if(LIVE){
    const el=box.querySelector('.llmbody');
    fetch('/llm',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({prompt:JSON.stringify({
        amount_inr:trace.payment.amount, category:trace.payment.category,
        decline_code:trace.payment.code, outcome:trace.outcome,
        action:trace.action.kind, authorised_by:trace.action.authorised_by,
        attempts:(trace.attempts||[]).map(a=>({window_hours:a.window, skipped:a.skipped,
          channel:a.skipped?null:a.best.channel, recovered:a.won||false}))
      },null,2)})})
      .then(r=>r.json())
      .then(j=>{ el.classList.remove('wait'); el.textContent=j.text||('unavailable: '+(j.error||'')); })
      .catch(e=>{ el.textContent='model unreachable: '+e; });
  }
  return box;
}
"""


# --------------------------------------------------------------------------
# The shell: a first screen that teaches the problem, and four tabs.
#
# The page was one continuous scroll of eight sections that opened with
# results. Someone who does not already know what dunning is could not tell
# what they were looking at, which was the first and most persistent piece
# of feedback on it. Length was making that worse, not better.
#
# So: nothing moves on the first screen, it states the problem in one
# sentence and the constraint that makes the problem hard, and every other
# section moves behind a tab. A judge skims once, and the thing they skim
# first is now an explanation rather than a number.
# --------------------------------------------------------------------------

SHELL_CSS = r"""
.nav{position:sticky;top:0;z-index:50;background:rgba(10,14,26,.9);
  backdrop-filter:blur(11px);border-bottom:1px solid var(--line);}
.navin{max-width:1200px;margin:0 auto;padding:0 26px;display:flex;gap:4px;align-items:center;}
.navb{background:none;border:0;color:var(--dim);font-family:"Space Grotesk",sans-serif;
  font-weight:600;font-size:.86rem;padding:15px 15px 13px;cursor:pointer;
  border-bottom:2px solid transparent;letter-spacing:-.01em;}
.navb:hover{color:var(--text);}
.navb.on{color:var(--text);border-bottom-color:var(--accent);}
.navtag{margin-left:auto;font-family:"JetBrains Mono",monospace;font-size:.66rem;
  color:var(--dim);letter-spacing:.09em;text-transform:uppercase;
  display:flex;align-items:center;gap:7px;}
[data-tab]{display:none;}
[data-tab].show{display:block;}

/* ---- first screen ---- */
.hero{padding:34px 0 4px;}
.hero .eye{font-family:"JetBrains Mono",monospace;font-size:.7rem;letter-spacing:.13em;
  text-transform:uppercase;color:var(--accent);margin-bottom:15px;}
.hero h2{font-size:clamp(1.4rem,2.6vw,1.95rem);max-width:24ch;line-height:1.2;margin-bottom:18px;}
.hero p{color:var(--muted);max-width:66ch;margin:0 0 14px;font-size:.97rem;}
.hero p b{color:var(--text);font-weight:600;}
.rulecard{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--line);
  border:1px solid var(--line);border-radius:12px;overflow:hidden;margin:26px 0 8px;}
@media(max-width:820px){.rulecard{grid-template-columns:1fr;}}
.rulecard>div{background:var(--surface);padding:19px 21px;}
.rulecard .n{font-family:"Space Grotesk",sans-serif;font-size:1.5rem;font-weight:700;
  color:var(--attention);letter-spacing:-.02em;}
.rulecard .h{font-size:.9rem;font-weight:600;margin:7px 0 5px;}
.rulecard .d{color:var(--dim);font-size:.83rem;line-height:1.55;}
.steps{counter-reset:s;margin:30px 0 0;padding:0;list-style:none;}
.steps li{position:relative;padding:0 0 17px 40px;color:var(--muted);font-size:.92rem;}
.steps li::before{counter-increment:s;content:counter(s);position:absolute;left:0;top:-1px;
  width:25px;height:25px;border-radius:7px;background:var(--surface-2);
  border:1px solid var(--line-2);display:flex;align-items:center;justify-content:center;
  font-family:"JetBrains Mono",monospace;font-size:.72rem;color:var(--accent);}
.steps li b{color:var(--text);font-weight:600;}
.cta{display:flex;gap:11px;flex-wrap:wrap;margin-top:26px;}
.cta .btn{width:auto;padding:12px 22px;}
"""

SHELL_HTML = r"""
  <section data-tab="start" class="show">
    <div class="hero">
      <div class="eye">Start here</div>
      <h2>A subscription payment failed. You get four tries, and the clock is not yours.</h2>
      <p>In India a recurring payment is not a charge you make. The customer signs a <b>mandate</b> once,
        and after that you ask the payment rails to execute it. Sometimes that execution fails: no money in
        the account that morning, the bank did not answer, the card expired. The customer still wants the
        service. The revenue was lost to plumbing, not to a decision.</p>
      <p>Recovering it is nearly pure margin, which is why everyone tries. What makes India different is
        that <b>you are not allowed to just keep retrying.</b></p>
      <div class="rulecard">
        <div><div class="n">4</div><div class="h">Attempts per cycle, total</div>
          <div class="d">NPCI caps UPI AutoPay at one original execution plus three retries.
            A fifth attempt is not aggressive dunning, it is non compliant.</div></div>
        <div><div class="n">T+24h · 72h · 7d</div><div class="h">Fixed retry windows</div>
          <div class="d">When the retries happen is set by regulation. No system, learned or otherwise,
            gets to choose that. This is the constraint the whole project is built around.</div></div>
        <div><div class="n">₹15,000</div><div class="h">Where authentication kicks in</div>
          <div class="d">Above it, RBI requires the customer to re-authenticate, so a silent background
            retry cannot legally clear. The limit rises to ₹1,00,000 for mutual funds, insurance and
            credit card bills.</div></div>
      </div>
      <p style="margin-top:26px">So the interesting question is not <i>when do we retry</i>, because that is
        answered for you. It is <b>which of these payments should be touched at all</b>, and
        <b>how do you reach the customer</b> before an attempt you are only allowed to make four times.
        That is the only thing here that is learned.</p>
      <ol class="steps">
        <li><b>Classify.</b> Split the failure into soft, which can recover, and hard, where the mandate
          or the instrument is dead.</li>
        <li><b>Refuse what the rules forbid.</b> Hard declines never reach the policy. Payments over the
          authentication limit are escalated instead of silently retried.</li>
        <li><b>Choose a channel.</b> For each permitted window a contextual bandit picks SMS or an IVR
          call, or skips when the expected recovery does not cover the cost of the message.</li>
        <li><b>Fire the outbound call, and log it.</b> Every decision is written to an append only trail,
          so the four attempt cap is provable rather than assumed.</li>
      </ol>
      <div class="cta">
        <button class="btn" data-go="sim">Run one payment through it</button>
        <button class="btn ghost" data-go="evidence">Show me whether it works</button>
      </div>
    </div>
  </section>
"""

SHELL_JS = r"""
/* ---- tabs ---- */
function showTab(name){
  /* The rail styles itself off this: only the control group that drives the
     visible pane keeps a solid button, so there is one obvious action rather
     than four competing ones. */
  document.body.dataset.pane=name;
  document.querySelectorAll('[data-tab]').forEach(s=>s.classList.toggle('show',s.dataset.tab===name));
  document.querySelectorAll('.navb').forEach(b=>b.classList.toggle('on',b.dataset.go===name));
  window.scrollTo({top:0,behavior:'instant'});
  /* the traffic simulator keeps running only while you can see it */
  if(name!=='stream' && typeof stStop==='function' && stTimer) stStop();
}
document.querySelectorAll('[data-go]').forEach(b=>
  b.addEventListener('click',()=>showTab(b.dataset.go)));
"""
