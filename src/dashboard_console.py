"""
The console shell.

The page this replaces was a scrolling document: eight stacked sections,
paragraphs of explanation, results arriving in the order an essay would put
them. It read as a lecture about a system rather than as the system, and
that was the consistent feedback on it.

An operator console is a different object. It fills the viewport once and
never scrolls as a page. Controls live in a fixed rail on the left, output
fills the stage on the right, and the stage switches between panes instead
of growing. Labels are 10px and uppercase, numbers are tabular, and prose
appears only where one line of it changes what someone does next. The long
form explanation still exists, in docs/EXPLAINER.md and behind the primer
overlay, where a reader can choose it instead of being handed it.

Every element id here matches the ones the engine, race, network and
visualisation code already drive, so this is a re-housing of the same
machinery rather than a second implementation of it.
"""

CONSOLE_CSS = r"""
:root{
  --bg:#07090F; --surface:#0D1119; --surface-2:#141A25; --surface-3:#1C2432;
  --line:rgba(230,238,252,.09); --line-2:rgba(230,238,252,.16);
  --text:#E6EAF4; --muted:#8A97B0; --dim:#5C6880; --faint:#454F63;
  --recovered:#3B9B7D; --refused:#C54D4D; --attention:#E8B86D; --accent:#4D7EE8;
  --s1:3px; --s2:6px; --s3:9px; --s4:12px; --s6:18px; --s8:24px;
  --r-sm:4px; --r-md:8px; --r-lg:12px;
}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--text);
  font-family:Inter,-apple-system,"Segoe UI",sans-serif;font-size:13.5px;line-height:1.5;
  -webkit-font-smoothing:antialiased;overflow:hidden;}
.num{font-variant-numeric:tabular-nums lining-nums;font-feature-settings:"tnum" 1,"lnum" 1}
.mono{font-family:"JetBrains Mono",ui-monospace,monospace}
.noise{position:fixed;inset:0;pointer-events:none;opacity:.02;z-index:9999;
  background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='3'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E")}

.shell{height:100vh;display:flex;flex-direction:column;overflow:hidden}

/* ── masthead ─────────────────────────────────────────────────────── */
.masthead{flex-shrink:0;display:flex;align-items:center;gap:var(--s4);
  padding:var(--s4) var(--s6);border-bottom:1px solid var(--line);
  position:relative;overflow:hidden;background:var(--surface)}
.masthead::before{content:"";position:absolute;left:-2%;top:-300%;width:44%;height:700%;
  background:radial-gradient(ellipse at center,rgba(77,126,232,.13),transparent 68%);
  pointer-events:none}
.mark{width:30px;height:30px;border-radius:8px;position:relative;z-index:1;flex-shrink:0;
  background:linear-gradient(145deg,var(--recovered),var(--accent));
  display:flex;align-items:center;justify-content:center;color:#07090F;
  font-family:"Space Grotesk",sans-serif;font-weight:700;font-size:13px}
.brand{position:relative;z-index:1;min-width:0}
.wordmark{font-family:"Space Grotesk",sans-serif;font-size:15px;font-weight:700;
  letter-spacing:.13em;text-transform:uppercase;margin:0;line-height:1.1}
.tagline{font-size:9.5px;letter-spacing:.2em;text-transform:uppercase;color:var(--faint);
  font-weight:500;margin-top:2px}
.mright{margin-left:auto;display:flex;align-items:center;gap:var(--s6);position:relative;z-index:1}
.mstat{text-align:right;line-height:1.25}
.mstat .k{display:block;font-size:9px;letter-spacing:.12em;text-transform:uppercase;
  color:var(--faint);font-weight:600}
.mstat .v{font-family:"Space Grotesk",sans-serif;font-size:15px;font-weight:700}
.sdot{width:7px;height:7px;border-radius:50%;background:var(--faint);flex-shrink:0}
.sdot.on{background:var(--recovered);box-shadow:0 0 9px rgba(59,155,125,.8);animation:pulse 1.6s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}
.livetext{font-size:10px;color:var(--faint);letter-spacing:.1em;text-transform:uppercase}
.qbtn{all:unset;cursor:pointer;width:24px;height:24px;border-radius:50%;
  border:1px solid var(--line-2);color:var(--dim);display:flex;align-items:center;
  justify-content:center;font-size:12px;font-weight:600}
.qbtn:hover{color:var(--text);border-color:var(--dim)}

.body{flex:1;display:grid;grid-template-columns:344px 1fr;min-height:0}
@media(max-width:1000px){.body{grid-template-columns:1fr;overflow:auto}}

/* ── control rail ─────────────────────────────────────────────────── */
.rail{border-right:1px solid var(--line);background:var(--surface);
  padding:var(--s6);overflow-y:auto;display:flex;flex-direction:column;gap:var(--s6)}
.grp{display:flex;flex-direction:column;gap:var(--s3)}
.eyebrow{font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--faint);
  font-weight:700;display:flex;align-items:center;gap:var(--s2)}
.eyebrow::after{content:"";flex:1;height:1px;background:var(--line)}
label{display:block;font-size:10px;letter-spacing:.08em;text-transform:uppercase;
  color:var(--dim);font-weight:600;margin-bottom:var(--s2)}
select,input[type=number]{width:100%;background:#04060B;color:var(--text);
  border:1px solid var(--line-2);border-radius:var(--r-sm);padding:9px 10px;
  font-family:inherit;font-size:12.5px;cursor:pointer;transition:border-color .18s,box-shadow .18s}
select:focus-visible,button:focus-visible{outline:none;border-color:var(--accent);
  box-shadow:0 0 0 3px rgba(77,126,232,.30)}
input[type=range]{width:100%;accent-color:var(--accent)}
.amtrow{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:var(--s2)}
.amtval{font-family:"Space Grotesk",sans-serif;font-size:19px;font-weight:700}
.afaflag{font-size:10.5px;color:var(--attention);font-weight:600;min-height:1.2em;line-height:1.35}
.btn{all:unset;box-sizing:border-box;width:100%;min-height:34px;display:flex;
  align-items:center;justify-content:center;gap:var(--s2);border-radius:var(--r-sm);
  font-family:"Space Grotesk",sans-serif;font-size:12.5px;font-weight:700;cursor:pointer;
  background:var(--accent);color:#07090F;transition:filter .16s,transform .1s}
.btn:hover{filter:brightness(1.1)} .btn:active{transform:translateY(1px)}
.btn:disabled{opacity:.4;cursor:not-allowed}
/* Four control groups each with a solid button gave the rail four equally
   loud primary actions and no indication which one drives what you are
   looking at. A group's button is solid only while its pane is on stage;
   the rest recede to the ghost treatment but stay clickable, because
   pressing one is a legitimate way to switch context. */
.rail .grp[data-for] .btn:not(.ghost){background:transparent;color:var(--muted);
  box-shadow:inset 0 0 0 1px var(--line)}
.rail .grp[data-for] .btn:not(.ghost):hover{color:var(--text);
  box-shadow:inset 0 0 0 1px var(--line-2);filter:none}
body[data-pane="decision"] .rail .grp[data-for~="decision"] .btn:not(.ghost),
body[data-pane="sim"]      .rail .grp[data-for~="sim"]      .btn:not(.ghost),
body[data-pane="stream"]   .rail .grp[data-for~="stream"]   .btn:not(.ghost),
body[data-pane="evidence"] .rail .grp[data-for~="evidence"] .btn:not(.ghost),
body[data-pane="how"]      .rail .grp[data-for~="how"]      .btn:not(.ghost){
  background:var(--accent);color:#07090F;box-shadow:none}
body[data-pane="decision"] .rail .grp[data-for~="decision"] .btn:not(.ghost):hover,
body[data-pane="sim"]      .rail .grp[data-for~="sim"]      .btn:not(.ghost):hover,
body[data-pane="stream"]   .rail .grp[data-for~="stream"]   .btn:not(.ghost):hover,
body[data-pane="evidence"] .rail .grp[data-for~="evidence"] .btn:not(.ghost):hover,
body[data-pane="how"]      .rail .grp[data-for~="how"]      .btn:not(.ghost):hover{
  filter:brightness(1.1);box-shadow:none}
/* The group on stage also gets a quiet marker, so the link between the rail
   and the tab reads even before you notice the button weight. */
.rail .grp[data-for]{position:relative}
.rail .grp[data-for] .eyebrow{transition:color .18s}
body[data-pane="decision"] .rail .grp[data-for~="decision"] .eyebrow,
body[data-pane="sim"]      .rail .grp[data-for~="sim"]      .eyebrow,
body[data-pane="stream"]   .rail .grp[data-for~="stream"]   .eyebrow,
body[data-pane="evidence"] .rail .grp[data-for~="evidence"] .eyebrow,
body[data-pane="how"]      .rail .grp[data-for~="how"]      .eyebrow{color:var(--muted)}

.btn.ghost{background:transparent;color:var(--text);box-shadow:inset 0 0 0 1px var(--line-2)}
.btn.ghost:hover{box-shadow:inset 0 0 0 1px var(--dim)}
.btn.ghost.on{color:var(--accent);box-shadow:inset 0 0 0 1px var(--accent)}
.btnrow{display:flex;gap:var(--s2)}
.btnrow .btn{flex:1}
.hint{font-size:11px;color:var(--dim);line-height:1.45}
.wirehead{display:flex;align-items:center;gap:var(--s2);font-size:10px;
  letter-spacing:.08em;text-transform:uppercase;color:var(--dim);font-weight:600}

/* ── stage ────────────────────────────────────────────────────────── */
.stagemain{display:flex;flex-direction:column;min-width:0;min-height:0}
.hud{flex-shrink:0;display:grid;grid-template-columns:repeat(4,1fr);gap:1px;
  background:var(--line);border-bottom:1px solid var(--line)}
.hud>div{background:var(--surface);padding:10px var(--s6)}
.hud .k{font-size:9px;letter-spacing:.12em;text-transform:uppercase;color:var(--faint);font-weight:700}
.hud .v{font-family:"Space Grotesk",sans-serif;font-size:18px;font-weight:700;
  margin-top:2px;letter-spacing:-.02em}
.navtabs{flex-shrink:0;display:flex;border-bottom:1px solid var(--line);
  background:var(--surface-2);padding:0 var(--s6);overflow-x:auto}
.navb{all:unset;cursor:pointer;padding:10px 14px;font-size:11px;font-weight:700;
  letter-spacing:.08em;text-transform:uppercase;color:var(--faint);
  border-bottom:2px solid transparent;white-space:nowrap}
.navb:hover{color:var(--muted)}
.navb.on{color:var(--text);border-bottom-color:var(--accent)}
/* The explanation card. Its chip row reuses .navtabs for the look, which
   carries none of the spacing a wrapped row of chips needs, and the
   paragraph had no measure at all -- at this card width a line ran past 120
   characters and the block read as one dense slab. */
#tabs{gap:8px;flex-wrap:wrap;margin-bottom:var(--s6);overflow-x:visible}
.expcard{max-width:900px}
.exp{min-height:118px}
.exp-h{margin-bottom:var(--s4)}
.exp-t{max-width:70ch;line-height:1.8}
.panes{flex:1;overflow-y:auto;padding:var(--s6);min-height:0}
[data-tab]{display:none} [data-tab].show{display:block}
.log{flex-shrink:0;height:104px;overflow-y:auto;border-top:1px solid var(--line);
  background:#04060B;padding:8px var(--s6);font-family:"JetBrains Mono",monospace;
  font-size:11px;line-height:1.65}
.log div{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.log .t{color:var(--faint)}
.log .rec{color:var(--recovered)} .log .ref{color:var(--refused)}
.log .esc{color:var(--attention)} .log .exh{color:var(--dim)}

/* shared pane furniture */
.ptitle{font-family:"Space Grotesk",sans-serif;font-size:13px;font-weight:700;
  margin:0 0 3px;letter-spacing:-.01em}
.psub{color:var(--dim);font-size:11.5px;margin:0 0 var(--s4);max-width:88ch}
.phead{margin-bottom:var(--s4)}
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--r-lg)}
.pad{padding:var(--s6)}
.two{display:grid;grid-template-columns:1fr 1fr;gap:var(--s4)}
@media(max-width:1180px){.two{grid-template-columns:1fr}}
.sep{height:1px;background:var(--line);margin:var(--s8) 0}

/* ── primer overlay ───────────────────────────────────────────────── */
.ovl{position:fixed;inset:0;z-index:200;background:rgba(4,6,11,.82);
  backdrop-filter:blur(5px);display:none;align-items:center;justify-content:center;padding:var(--s8)}
.ovl.show{display:flex}
.ovlbox{background:var(--surface);border:1px solid var(--line-2);border-radius:var(--r-lg);
  max-width:880px;width:100%;max-height:86vh;overflow-y:auto;padding:var(--s8);position:relative}
.ovlbox h3{font-family:"Space Grotesk",sans-serif;font-size:17px;margin:0 0 var(--s2);max-width:34ch}
.ovlbox p{color:var(--muted);font-size:12.5px;margin:0 0 var(--s3);max-width:74ch}
.ovlbox p b{color:var(--text)}
.rulecard{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--line);
  border:1px solid var(--line);border-radius:var(--r-md);overflow:hidden;margin:var(--s6) 0}
@media(max-width:760px){.rulecard{grid-template-columns:1fr}}
.rulecard>div{background:var(--surface-2);padding:var(--s4) var(--s6)}
.rulecard .n{font-family:"Space Grotesk",sans-serif;font-size:19px;font-weight:700;color:var(--attention)}
.rulecard .h{font-size:12px;font-weight:600;margin:5px 0 3px}
.rulecard .d{color:var(--dim);font-size:11px;line-height:1.5}
.xbtn{position:absolute;top:var(--s4);right:var(--s4);all:unset;cursor:pointer;
  color:var(--dim);font-size:18px;line-height:1;padding:4px 8px}
.xbtn:hover{color:var(--text)}
"""


CONSOLE_JS = r"""
/* primer overlay: available, never in the way */
$('qOpen').addEventListener('click',()=>$('qOvl').classList.add('show'));
$('qClose').addEventListener('click',()=>$('qOvl').classList.remove('show'));
$('qOvl').addEventListener('click',e=>{ if(e.target===$('qOvl')) $('qOvl').classList.remove('show'); });
document.addEventListener('keydown',e=>{ if(e.key==='Escape') $('qOvl').classList.remove('show'); });

/* One log strip for the whole console, so activity stays visible from any
   tab instead of each panel keeping a private history nobody is looking at. */
function conLog(html,cls){
  const d=document.createElement('div');
  d.innerHTML='<span class="t">'+new Date().toLocaleTimeString('en-GB')+'</span>  '+
    '<span class="'+(cls||'')+'">'+html+'</span>';
  $('conLog').prepend(d);
  while($('conLog').childElementCount>200) $('conLog').lastElementChild.remove();
}
conLog('console ready. engine running in this page, no decision taken yet.','exh');
"""


def console_body(h, st):
    """The document body. `h` is the headline aggregate across seeds, `st`
    the per-stage counts from the audited demo batch."""
    afa = st["compliance_gate"].get("afa_escalated", 0)
    refused = st["compliance_gate"]["refused"]
    return f"""
<div class="noise"></div>
<div class="shell">

  <header class="masthead">
    <div class="mark">R</div>
    <div class="brand">
      <h1 class="wordmark">Retry Waterfall</h1>
      <div class="tagline">failed auto-debit recovery &middot; npci bounded</div>
    </div>
    <div class="mright">
      <div class="mstat"><span class="k">of expert ceiling</span>
        <span class="v num" style="color:var(--attention)">{h['captured']:.1f}%</span></div>
      <div class="mstat"><span class="k">paired lift / batch</span>
        <span class="v num" style="color:var(--recovered)">+&#8377;{h['paired']['lift']:,.0f}</span></div>
      <button class="qbtn" id="qOpen" title="What this is">?</button>
      <span class="sdot" id="navDot"></span><span class="livetext" id="navTag">offline</span>
    </div>
  </header>

  <div class="body">

    <aside class="rail">
      <div class="grp" data-for="decision">
        <div class="eyebrow">Compose a failed payment</div>
        <div><label for="lvCode">Why the debit failed</label><select id="lvCode"></select></div>
        <div><label for="lvCat">What it was for</label><select id="lvCat"></select></div>
        <div>
          <div class="amtrow"><label style="margin:0">Amount</label>
            <span class="amtval num" id="lvAmtV">&#8377;0</span></div>
          <input type="range" id="lvAmt" min="200" max="120000" step="100" value="4200">
          <div class="afaflag" id="lvAfa"></div>
        </div>
        <button class="btn" id="lvRun">Run it through the engine</button>
        <div class="btnrow">
          <button class="btn ghost" id="lvRand">Random</button>
          <button class="btn ghost" id="lvMany">Sample 200&times;</button>
        </div>
        <div id="lvDist"></div>
      </div>

      <div class="grp" data-for="sim">
        <div class="eyebrow">Benchmark</div>
        <div class="hint">Sixty fresh payments. Blind retry against this, identical luck.</div>
        <button class="btn" id="rcRun">Race 60 payments</button>
        <button class="btn ghost" id="rcAgain">Wipe what it learned, race again</button>
      </div>

      <div class="grp" data-for="stream">
        <div class="eyebrow">Traffic</div>
        <div class="btnrow">
          <button class="btn" id="stPlay">Play</button>
          <button class="btn ghost" id="stNew">New batch</button>
        </div>
        <div class="btnrow">
          <button class="btn ghost spd" data-hz="2">Slow</button>
          <button class="btn ghost spd on" data-hz="6">Normal</button>
          <button class="btn ghost spd" data-hz="20">Fast</button>
        </div>
        <div class="hint"><span class="sdot" id="stDot"
          style="display:inline-block;vertical-align:middle"></span> <span id="stState">idle</span></div>
      </div>

      <div class="grp" data-for="stream">
        <div class="eyebrow">Webhook</div>
        <div class="wirehead"><span class="sdot" id="wDot"></span><span id="wState">checking</span></div>
        <div class="hint" id="wModel"></div>
        <button class="btn" id="wSend">Send a test webhook</button>
        <button class="btn ghost" id="wCopy">Copy the curl</button>
      </div>
    </aside>

    <main class="stagemain">
      <div class="hud">
        <div><div class="k">Revenue at risk</div><div class="v num" id="k-risk">0</div></div>
        <div><div class="k">Recovered, net of cost</div>
          <div class="v num" id="k-rec" style="color:var(--recovered)">0</div></div>
        <div><div class="k">Refused by compliance</div>
          <div class="v num" id="k-ref" style="color:var(--refused)">0</div></div>
        <div><div class="k">Escalated for auth</div>
          <div class="v num" style="color:var(--attention)">{afa} payments</div></div>
      </div>

      <nav class="navtabs">
        <button class="navb on" data-go="decision">Decision</button>
        <button class="navb" data-go="sim">Benchmark</button>
        <button class="navb" data-go="stream">Stream</button>
        <button class="navb" data-go="evidence">Evidence</button>
        <button class="navb" data-go="how">Pipeline</button>
      </nav>

      <div class="panes">

        <section data-tab="decision" class="show">
          <div class="phead">
            <h3 class="ptitle">One payment, every step it took</h3>
            <p class="psub">The gate, the sampling and the outbound call execute in this page, from the
              posteriors the bandit learned during evaluation. The same payment twice can pick a different
              channel: it samples a belief rather than reading a table.</p>
          </div>
          <div class="card pad" id="lvOut"></div>
        </section>

        <section data-tab="sim">
          <div class="phead">
            <h3 class="ptitle">Blind retry against this, same payments</h3>
            <p class="psub">Both sides go through the identical compliance gate first, so the only thing
              being compared is channel choice, which is what the reported lift measures. Whether an
              attempt succeeds is fixed per payment and channel before either policy runs, so when they
              pick the same thing they get the same result.</p>
          </div>
          <div class="race">
            <div class="rlane" id="laneA">
              <h4>Blind retry</h4><div class="sub">Same gate, then always SMS at every window</div>
              <div class="big num" id="aNet">&#8377;0</div><div class="biglab">net recovered</div>
              <div class="lrow"><span>Recovered</span><b class="num" id="aRec">0</b></div>
              <div class="lrow"><span>Messages sent</span><b class="num" id="aMsg">0</b></div>
              <div class="lrow"><span>Spent</span><b class="num" id="aCost">&#8377;0</b></div>
              <div class="lrow"><span>Refused or escalated by the gate</span><b class="num" id="aGate">0</b></div>
            </div>
            <div class="rlane" id="laneB">
              <h4>This agent</h4><div class="sub">Same gate, then a learned channel per window</div>
              <div class="big num" id="bNet">&#8377;0</div><div class="biglab">net recovered</div>
              <div class="lrow"><span>Recovered</span><b class="num" id="bRec">0</b></div>
              <div class="lrow"><span>Messages sent</span><b class="num" id="bMsg">0</b></div>
              <div class="lrow"><span>Spent</span><b class="num" id="bCost">&#8377;0</b></div>
              <div class="lrow"><span>Refused or escalated</span><b class="num" id="bStop">0</b></div>
            </div>
          </div>
          <svg class="spark" id="rcSpark" viewBox="0 0 800 74" preserveAspectRatio="none"></svg>
          <div class="verdict" id="rcVerdict">Press race in the rail. Same sixty payments, same luck, so
            whatever separates them is the decision making.</div>
          <div class="sep"></div>
          <div class="phead">
            <h3 class="ptitle">What it has learned so far</h3>
            <p class="psub">The pale band is the range still considered plausible, the bright line the
              current best guess. Wide means not enough evidence to commit, so it keeps trying that
              channel until it has some.</p>
          </div>
          <div class="beliefs" id="beliefs"></div>
        </section>

        <section data-tab="stream">
          <div class="phead">
            <h3 class="ptitle">Generated traffic</h3>
            <p class="psub">Payments that did not exist before you pressed play, through the same engine.
              Every batch lands somewhere different, which is the honest picture of a policy with
              variance.</p>
          </div>
          <div class="counters">
            <div><div class="l">Processed</div><div class="v num" id="cProc">0</div></div>
            <div><div class="l">Refused hard</div>
              <div class="v num" id="cRef" style="color:var(--refused)">0</div></div>
            <div><div class="l">AFA escalated</div>
              <div class="v num" id="cAfa" style="color:var(--attention)">0</div></div>
            <div><div class="l">Recovered</div>
              <div class="v num" id="cRec" style="color:var(--recovered)">0</div></div>
            <div><div class="l">Spent reaching</div><div class="v num" id="cCost">&#8377;0</div></div>
            <div><div class="l">Net recovered</div>
              <div class="v num" id="cNet" style="color:var(--recovered)">&#8377;0</div></div>
          </div>
          <div class="feed" id="stFeed" style="height:180px"></div>
          <div class="sep"></div>
          <div class="phead">
            <h3 class="ptitle">Real webhooks</h3>
            <p class="psub">Post a genuine Razorpay <span class="mono">subscription.pending</span> body to
              the server. Same adapter, same gate, same bandit, and the decision arrives below over a live
              stream. Fire it from any terminal.</p>
          </div>
          <pre class="wirecurl" id="wCurl"></pre>
          <div class="feed" id="wFeed" style="height:150px"></div>
        </section>

        <section data-tab="evidence">
          <div class="phead">
            <h3 class="ptitle">One batch of 60, and 200 of them</h3>
            <p class="psub">The timeline and tables are one audited run. The headline percentages are
              averaged over 200 independent runs, because a single batch of 60 is mostly luck.</p>
          </div>
          <div class="card">
            <div class="tl-head"><div>Payment</div>
              <div class="tl-axis" id="axis"></div><div style="text-align:right">Outcome</div></div>
            <div id="lanes"></div>
            <div class="tl-foot" id="tl-foot"></div>
          </div>
          <div class="sep"></div>
          <div class="two">
            <div class="card pad">
              <div class="ptitle" style="margin-bottom:9px">Against a perfect-information ceiling</div>
              <table id="t-policy"></table>
              <p class="psub" style="margin:12px 0 0;font-size:11px">&plusmn;1 s.d. on net recovered:
                baseline &#8377;{h['baseline']['net_sd']:,.0f}, bandit &#8377;{h['bandit']['net_sd']:,.0f},
                oracle &#8377;{h['oracle']['net_sd']:,.0f}. {h['n_events']} events &times; {h['n_seeds']}
                seeds, common random numbers across policies, reproducible run to run.</p>
            </div>
            <div class="card pad">
              <div class="ptitle" style="margin-bottom:9px">Net recovered as evidence accumulates</div>
              <svg class="curve" id="curve" viewBox="0 0 460 214" preserveAspectRatio="none"></svg>
              <p class="psub" style="margin:8px 0 0;font-size:11px">At 50 events it has almost no evidence
                per channel and trails the baseline. The gap to oracle narrows as attempts accumulate, and
                that shape is the learning.</p>
            </div>
          </div>
          <div class="sep"></div>
          <div class="two">
            <div class="card pad"><div class="ptitle" style="margin-bottom:9px">By decline reason</div>
              <table id="t-code"></table></div>
            <div class="card pad">
              <div class="ptitle" style="margin-bottom:9px">By channel, the tradeoff it had to learn</div>
              <table id="t-chan"></table></div>
          </div>
          <div class="sep"></div>
          <div class="card pad"><div class="ptitle" style="margin-bottom:9px">By retry window</div>
            <table id="t-win"></table></div>
        </section>

        <section data-tab="how">
          <div class="phead">
            <h3 class="ptitle">Where each payment goes</h3>
            <p class="psub">Particles are real volume. The gate is red because it stopped {refused}
              payments a naive retry bot would have illegally retried. The amber branch is {afa} over the
              RBI authentication threshold, routed to an authenticated link instead. Drag to rotate,
              scroll to zoom.</p>
          </div>
          <div class="card"><div id="graph"></div></div>
          <div class="sep"></div>
          <div class="phead">
            <h3 class="ptitle">Pre-generated explanations</h3>
            <p class="psub">Committed with the repo so the page needs no key. With the server running, a
              model writes one live for each decision on the Decision tab instead.</p>
          </div>
          <div class="card pad expcard">
            <div class="navtabs" id="tabs" style="background:none;border:0;padding:0"></div>
            <div class="exp" id="exp"></div>
          </div>
        </section>

      </div>

      <div class="log" id="conLog"></div>
    </main>
  </div>
</div>

<div class="ovl" id="qOvl">
  <div class="ovlbox">
    <button class="xbtn" id="qClose">&times;</button>
    <h3>A subscription payment failed. You get four tries, and the clock is not yours.</h3>
    <p>In India a recurring payment is not a charge you make. The customer signs a <b>mandate</b> once and
      after that you ask the rails to execute it. When execution fails the customer has not cancelled, so
      the revenue was lost to plumbing. What makes India different is that <b>you are not allowed to just
      keep retrying.</b></p>
    <div class="rulecard">
      <div><div class="n">4</div><div class="h">Attempts per cycle</div>
        <div class="d">NPCI caps UPI AutoPay at one execution plus three retries. A fifth is not
          aggressive dunning, it is non compliant.</div></div>
      <div><div class="n">T+24h &middot; 72h &middot; 7d</div><div class="h">Fixed windows</div>
        <div class="d">Timing is set by regulation. Nothing here, learned or otherwise, gets to choose
          it.</div></div>
      <div><div class="n">&#8377;15,000</div><div class="h">Authentication threshold</div>
        <div class="d">Above it the customer must re-authenticate, so a silent retry cannot clear.
          &#8377;1,00,000 for mutual funds, insurance and card bills.</div></div>
    </div>
    <p>So the question is not when to retry, which is answered for you. It is <b>which payments should be
      touched at all</b> and <b>how to reach the customer</b> before an attempt you only get four of.
      That is the only learned part of this.</p>
  </div>
</div>
"""
