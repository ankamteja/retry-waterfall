"""
Head to head: blind retry against this agent, on the same payments.

The evaluation's central claim is a number in a table, +Rs 12,197 per batch.
True, reproducible, and completely inert to look at. This runs the
comparison in front of you instead.

Both policies see the same generated batch and, critically, the same outcome
draws: whether an attempt succeeds is a hash of (race, payment, window,
channel), fixed before either policy runs. That is the browser's port of
`eval_harness.outcome_draw`, and it is what makes the divergence on screen
attributable to the decisions rather than to one side getting luckier.
Without it the race would be theatre.

Alongside it, the bandit's posteriors are drawn as they update. Thompson
Sampling is normally something an audience is asked to take on faith. Here
the beliefs visibly sharpen from a flat prior as pulls accumulate, which is
the only honest way to show something is being learned rather than looked
up.

One deliberate choice about the opponent: blind retry is simulated honestly,
not strawmanned. It pays for every message it sends and it recovers whatever
the true probabilities give it. Its losses come from contacting people it
should not have and from paying for attempts that were never worth making,
which is exactly the argument, so it does not need help losing.
"""

RACE_CSS = r"""
.race{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--line);
  border:1px solid var(--line);border-radius:12px;overflow:hidden;margin:18px 0;}
@media(max-width:820px){.race{grid-template-columns:1fr;}}
.lane{background:var(--surface);padding:18px 20px;position:relative;transition:background .3s;}
.lane.win{background:linear-gradient(180deg,rgba(59,155,125,.08),var(--surface));}
.lane h4{margin:0 0 3px;font-family:"Space Grotesk",sans-serif;font-size:.98rem;font-weight:700;}
.lane .sub{color:var(--dim);font-size:.78rem;margin-bottom:14px;}
.lane .big{font-family:"Space Grotesk",sans-serif;font-size:1.85rem;font-weight:700;
  letter-spacing:-.02em;color:var(--recovered);}
.lane .biglab{color:var(--dim);font-size:.68rem;text-transform:uppercase;
  letter-spacing:.09em;font-weight:600;margin-top:2px;}
.lrow{display:flex;justify-content:space-between;font-size:.82rem;padding:5px 0;
  border-top:1px solid var(--line);margin-top:11px;color:var(--muted);}
.lrow b{color:var(--text);font-weight:600;}
.verdict{padding:15px 18px;border-radius:10px;background:var(--surface-2);
  border:1px solid var(--line-2);font-size:.92rem;}
.verdict b{color:var(--recovered);}
.verdict.neg b{color:var(--refused);}
.spark{width:100%;height:74px;display:block;margin:2px 0 14px;}

.beliefs{display:grid;grid-template-columns:repeat(auto-fill,minmax(236px,1fr));gap:11px;margin-top:16px;}
.bcell{border:1px solid var(--line);border-radius:9px;padding:11px 13px;background:var(--surface);}
.bcell .bh{font-family:"JetBrains Mono",monospace;font-size:.67rem;color:var(--dim);
  letter-spacing:.04em;margin-bottom:9px;}
.brow{display:grid;grid-template-columns:54px 1fr 62px;gap:8px;align-items:center;
  font-size:.74rem;margin-bottom:5px;}
.bbar{height:20px;position:relative;background:var(--surface-2);border-radius:4px;overflow:hidden;}
.bbar i{position:absolute;top:0;bottom:0;opacity:.38;}
.bbar u{position:absolute;top:0;bottom:0;width:2px;text-decoration:none;}
.bpulls{color:var(--dim);text-align:right;font-family:"JetBrains Mono",monospace;font-size:.68rem;}
"""

RACE_HTML = r"""
  <section data-tab="sim" id="race">
    <div class="shead"><span class="snum">02</span><h2>Blind retry against this, same payments</h2></div>
    <p class="note">Sixty fresh payments. Both policies, identical luck: whether an attempt succeeds is
      fixed per payment and channel before either one runs, so when they pick the same thing they get the
      same result, and the gap is the decisions. Underneath, the bandit's beliefs sharpen from a flat
      prior as evidence lands.</p>
    <div class="btnrow" style="margin-bottom:14px">
      <button class="btn" id="rcRun" style="flex:0 0 auto;padding:11px 22px">Race 60 payments</button>
      <button class="btn ghost" id="rcAgain" style="flex:0 0 auto;padding:11px 22px">Wipe what it learned, race again</button>
    </div>
    <div class="race">
      <div class="lane" id="laneA">
        <h4>Blind retry</h4><div class="sub">Every payment, every window, always SMS</div>
        <div class="big num" id="aNet">₹0</div><div class="biglab">net recovered</div>
        <div class="lrow"><span>Recovered</span><b class="num" id="aRec">0</b></div>
        <div class="lrow"><span>Messages sent</span><b class="num" id="aMsg">0</b></div>
        <div class="lrow"><span>Spent</span><b class="num" id="aCost">₹0</b></div>
        <div class="lrow"><span>Chased a dead mandate</span><b class="num" id="aIllegal">0</b></div>
      </div>
      <div class="lane" id="laneB">
        <h4>This agent</h4><div class="sub">Gate first, then a learned channel per window</div>
        <div class="big num" id="bNet">₹0</div><div class="biglab">net recovered</div>
        <div class="lrow"><span>Recovered</span><b class="num" id="bRec">0</b></div>
        <div class="lrow"><span>Messages sent</span><b class="num" id="bMsg">0</b></div>
        <div class="lrow"><span>Spent</span><b class="num" id="bCost">₹0</b></div>
        <div class="lrow"><span>Refused or escalated</span><b class="num" id="bStop">0</b></div>
      </div>
    </div>
    <svg class="spark" id="rcSpark" viewBox="0 0 800 74" preserveAspectRatio="none"></svg>
    <div class="verdict" id="rcVerdict">Press race. Same sixty payments, same luck, so whatever
      separates them at the end is the decision making.</div>

    <div class="shead" style="margin-top:36px"><span class="snum">03</span><h2>What it has learned so far</h2></div>
    <p class="note">One belief per decline reason, retry window and channel. The pale band is the range
      the policy still thinks is plausible, the bright line is its current best guess. Wide means it has
      not seen enough to commit, so it keeps trying that channel until it has. That is the whole
      mechanism, and there is no tuning knob behind it.</p>
    <div class="beliefs" id="beliefs"></div>
  </section>
"""

RACE_JS = r"""
/* eval_harness.outcome_draw, in the browser: deterministic in
   (race, payment, window, channel), so both policies face identical luck. */
function hashU(str){
  let h=2166136261>>>0;
  for(let i=0;i<str.length;i++){ h^=str.charCodeAt(i); h=Math.imul(h,16777619)>>>0; }
  h^=h>>>13; h=Math.imul(h,2654435761)>>>0; h^=h>>>16;
  return (h>>>0)/4294967296;
}

let raceN=0, rcArms=null, rcTimer=null;

/* Same key space as the trained posteriors, but starting from Beta(1,1):
   the race is about watching it learn, not about replaying what it knows. */
function freshArms(){
  const a={};
  Object.keys(POST).forEach(k=>{ a[k]={a:1,b:1,pulls:0}; });
  return a;
}
function rcSample(k){ const x=rcArms[k]||{a:1,b:1}; return betaSample(x.a,x.b); }
function rcUpdate(k,won){
  const x=rcArms[k]||(rcArms[k]={a:1,b:1,pulls:0});
  if(won) x.a++; else x.b++;
  x.pulls++;
}

function runBlind(p,tag){
  let cost=0,msgs=0,gross=0,illegal=isHard(p.code)?1:0;
  for(let i=0;i<E.windows.length;i++){
    const w=E.windows[i];
    cost+=E.channelCost['sms']; msgs++;
    if(hashU(tag+'|'+p.id+'|'+w+'|sms')<trueP(p.code,w,'sms')){ gross=p.amount; break; }
  }
  return {cost:cost,msgs:msgs,gross:gross,illegal:illegal};
}

function runAgent(p,tag){
  if(isHard(p.code)||requiresAFA(p.amount,p.category)) return {cost:0,msgs:0,gross:0,stopped:1};
  let cost=0,msgs=0,gross=0;
  for(let i=0;i<E.windows.length;i++){
    const w=E.windows[i];
    let best=null;
    E.channels.forEach(ch=>{
      const k=p.code+'|'+w+'|'+ch, net=rcSample(k)*p.amount-E.channelCost[ch];
      if(!best||net>best.net) best={ch:ch,net:net,k:k};
    });
    if(best.net<=0) continue;
    cost+=E.channelCost[best.ch]; msgs++;
    const won=hashU(tag+'|'+p.id+'|'+w+'|'+best.ch)<trueP(p.code,w,best.ch);
    rcUpdate(best.k,won);
    if(won){ gross=p.amount; break; }
  }
  return {cost:cost,msgs:msgs,gross:gross,stopped:0};
}

function drawBeliefs(){
  const groups={};
  Object.keys(rcArms).forEach(k=>{
    const parts=k.split('|');
    (groups[parts[0]+'|'+parts[1]]=groups[parts[0]+'|'+parts[1]]||[]).push({ch:parts[2],x:rcArms[k]});
  });
  $('beliefs').innerHTML=Object.keys(groups).sort().map(g=>{
    const parts=g.split('|');
    const rows=groups[g].sort((x,y)=>x.ch<y.ch?-1:1).map(r=>{
      const a=r.x.a, b=r.x.b, m=a/(a+b);
      const sd=Math.sqrt(a*b/((a+b)*(a+b)*(a+b+1)));   /* one s.d. of the Beta */
      const lo=Math.max(0,m-sd)*100, hi=Math.min(1,m+sd)*100;
      const col=r.ch==='sms'?'var(--accent)':'var(--recovered)';
      return '<div class="brow"><span style="color:var(--muted)">'+E.channelLabel[r.ch]+'</span>'+
        '<span class="bbar"><i style="left:'+lo+'%;width:'+(hi-lo)+'%;background:'+col+'"></i>'+
        '<u style="left:'+(m*100)+'%;background:'+col+'"></u></span>'+
        '<span class="bpulls">'+r.x.pulls+' pulls</span></div>';
    }).join('');
    return '<div class="bcell"><div class="bh">'+parts[0].replace(/_/g,' ')+'  ·  T+'+parts[1]+'h</div>'+rows+'</div>';
  }).join('');
}

function spark(d){
  const el=$('rcSpark'), n=d.length;
  if(n<2){ el.innerHTML=''; return; }
  let mx=1; for(let i=0;i<n;i++) mx=Math.max(mx,Math.abs(d[i]));
  const pts=d.map((v,i)=>(i/(n-1)*800).toFixed(1)+','+(37-v/mx*32).toFixed(1)).join(' ');
  const last=d[n-1];
  el.innerHTML='<line x1="0" y1="37" x2="800" y2="37" stroke="rgba(255,255,255,.13)"/>'+
    '<polyline points="'+pts+'" fill="none" stroke="'+(last>=0?'#3B9B7D':'#C54D4D')+'" stroke-width="2"/>'+
    '<text x="6" y="14" fill="#5C6880" font-size="11" font-family="JetBrains Mono">'+
      (last>=0?'agent ahead by ':'agent behind by ')+inr(Math.round(Math.abs(last)))+'</text>';
}

function rcVerdict(A,B){
  drawBeliefs();
  const d=(B.net-B.cost)-(A.net-A.cost), el=$('rcVerdict');
  el.classList.toggle('neg',d<0);
  el.innerHTML = d>=0
    ? 'The agent finished <b>'+inr(Math.round(d))+'</b> ahead on the same sixty payments, sending '+
      Math.max(0,A.msgs-B.msgs)+' fewer messages. Blind retry chased <b>'+A.illegal+'</b> mandates that '+
      'were already dead, which is the part that is not merely wasteful.'
    : 'Blind retry finished <b>'+inr(Math.round(-d))+'</b> ahead this time. That happens in about 74 of '+
      'every 200 batches and pretending otherwise would be dishonest. Race it again. Averaged over 200 '+
      'batches the agent is +₹12,197, with a 95% interval of +6,330 to +18,065.';
}

function rcRace(resetBeliefs){
  clearInterval(rcTimer);
  if(resetBeliefs||!rcArms) rcArms=freshArms();
  const tag='race'+(raceN++), batch=newBatch(60);
  const A={net:0,rec:0,msgs:0,cost:0,illegal:0}, B={net:0,rec:0,msgs:0,cost:0,stop:0};
  const diffs=[];
  let i=0;
  $('rcVerdict').classList.remove('neg');
  $('rcVerdict').textContent='Running. Same payments, same luck, different decisions.';

  rcTimer=setInterval(()=>{
    if(i>=batch.length){ clearInterval(rcTimer); rcTimer=null; rcVerdict(A,B); return; }
    const p=batch[i++], a=runBlind(p,tag), b=runAgent(p,tag);
    A.cost+=a.cost; A.msgs+=a.msgs; A.illegal+=a.illegal; if(a.gross){ A.rec++; A.net+=a.gross; }
    B.cost+=b.cost; B.msgs+=b.msgs; B.stop+=b.stopped;    if(b.gross){ B.rec++; B.net+=b.gross; }
    $('aNet').textContent=inr(Math.round(A.net-A.cost)); $('aRec').textContent=A.rec;
    $('aMsg').textContent=A.msgs; $('aCost').textContent=inr(Math.round(A.cost*100)/100);
    $('aIllegal').textContent=A.illegal;
    $('bNet').textContent=inr(Math.round(B.net-B.cost)); $('bRec').textContent=B.rec;
    $('bMsg').textContent=B.msgs; $('bCost').textContent=inr(Math.round(B.cost*100)/100);
    $('bStop').textContent=B.stop;
    diffs.push((B.net-B.cost)-(A.net-A.cost));
    spark(diffs);
    $('laneA').classList.toggle('win',(A.net-A.cost)>(B.net-B.cost));
    $('laneB').classList.toggle('win',(B.net-B.cost)>=(A.net-A.cost));
    if(i%6===0) drawBeliefs();
  },95);
}

$('rcRun').addEventListener('click',()=>rcRace(false));
$('rcAgain').addEventListener('click',()=>rcRace(true));
rcArms=freshArms();
drawBeliefs();
"""
