let pkg=null, sel={doc:null, idx:null};
const $=s=>document.querySelector(s);
const esc=s=>s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function toast(m){const t=$('#toast');t.textContent=m;t.style.display='block';setTimeout(()=>t.style.display='none',2600);}

let allTypes=[], streamTypes=new Set(), autoIntake=false, intaking=false;
let job=null, wasRunning=false, lastNeeds=0, agentSel=new Set();

async function init(){
  await loadTypes();
  await pollStatus(false);     // boot already-populated from the startup intake
}
async function loadTypes(){
  const d=await (await fetch('/api/docs')).json();
  allTypes=d.types||[]; streamTypes=new Set(allTypes);
  renderTypeChips();
}
function renderTypeChips(){
  const el=$('#typeChips'); el.innerHTML='';
  allTypes.forEach(t=>{
    const c=document.createElement('span'); c.className='type-chip'+(streamTypes.has(t)?' on':'');
    c.textContent=t;
    c.onclick=()=>{ streamTypes.has(t)?streamTypes.delete(t):streamTypes.add(t); renderTypeChips(); };
    el.appendChild(c);
  });
}
async function pollStatus(keepPolling=true){
  const r=await fetch('/api/status'); const d=await r.json();
  job=d.job; pkg=d.package;
  render();
  // NOTE: do NOT re-render an open modal here — polling would wipe an in-progress text
  // selection / highlight. The modal re-renders only on the officer's own actions.
  const running=job && job.status==='running';
  if(keepPolling && running) setTimeout(()=>pollStatus(true),800);
  else if(job && job.status==='done' && wasRunning){ wasRunning=false; toast('Batch triaged — review the documents.'); }
}
async function intakeBatch(){
  if(intaking) return;
  const types=[...streamTypes];
  if(!types.length){ toast('Select at least one document type to intake.'); return; }
  intaking=true;
  const d=await (await fetch('/api/intake',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({n:3,types})})).json();
  intaking=false;
  toast(`Brought in ${d.intake} document(s) — Unreviewed.`);
  pollStatus(false);
}
function toggleAuto(){ autoIntake=$('#autoIntake').checked; checkAutoIntake(); }
function checkAutoIntake(){
  // the constant stream: once everything is cleared, bring in more raw documents
  if(autoIntake && lastNeeds===0 && !intaking && !(job && job.status==='running')) intakeBatch();
}
// run the triage agent on specific docs (ids) or ALL unprocessed (ids=null)
async function runAgentOn(ids){
  if(job && job.status==='running'){ toast('Agent already running.'); return; }
  const body = (ids && ids.length) ? {doc_ids:ids} : {};
  const d=await (await fetch('/api/run_agent',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)})).json();
  if(d.running){ wasRunning=true; agentSel.clear(); if($('#docModal').open) closeModal();
    toast(`Running agent on ${d.running} document(s)…`); pollStatus(true); }
  else toast('Nothing to run.');
}
function runAgentSelected(){ runAgentOn([...agentSel]); }
// a document's REVIEW lifecycle (distinct from processing). Uses the officer's determination.
function reviewState(d){
  if(!d) return 'withheld';
  if(!d.agent_run) return 'unprocessed';               // raw intake -> Unreviewed (untouched)
  // a document leaves Needs attention ONLY when the officer marks it — never automatically
  if(d.officer_responsive===true) return 'reviewed';   // officer marked responsive -> Cleared
  if(d.officer_responsive===false) return 'withheld';  // officer marked non-responsive -> Cleared
  if(d.escalated) return 'escalated';                  // agent couldn't decide -> Needs attention
  return 'agent-responsive';                           // agent triaged, officer hasn't marked -> Needs attention
}
function docChips(rs,d){
  const meta=`<span class="chip type">${d?d.doc_type:'—'}</span><span class="chip cnt">${d?d.redactions.length:0} redactions</span>`;
  if(rs==='escalated') return `<span class="chip esc">escalated</span>${meta}`;
  if(rs==='agent-responsive'){const open=d.redactions.filter(r=>r.status==='proposed').length;
    return `<span class="chip resp">responsive</span>${meta}${open?`<span class="chip esc">${open} to review</span>`:''}`;}
  if(rs==='reviewed') return `<span class="chip reviewed">cleared</span>${meta}`;
  return `<span class="chip non">withheld</span>${meta}`;
}
function curDoc(){return pkg && pkg.docs.find(d=>d.doc_id===sel.doc);}

function render(){
  if(pkg && pkg.request_id){ $('#ref').textContent=pkg.request_id+'  ·  '+pkg.jurisdiction;
    const rr=$('#reqRef'); if(rr) rr.textContent=pkg.jurisdiction; }
  const live=job && job.model_available;
  const b=$('#modelBadge'); b.textContent=live?'model: live':'model: fail-safe (escalate)';
  b.className='badge '+(live?'live':'safe');
  const running=!!(job && job.status==='running');
  const pb=$('#produceBtn'); if(pb){ pb.disabled=running; pb.title=running?'Wait for processing to finish':''; }
  const prog=$('#progress');
  if(running){ const pct=job.total?Math.round(job.done/job.total*100):0;
    prog.style.display='block';
    prog.innerHTML=`<div class="pwrap"><div class="pbar" style="width:${pct}%"></div></div>
      <div class="pmeta">Processing ${job.done}/${job.total} — review them as they land</div>`;
  } else prog.style.display='none';
  renderBoard();
}
function card(fn,opts){
  const div=document.createElement('div');
  if(opts.proc){ div.className='card '+opts.st;
    const chip=opts.st==='queued'?'<span class="chip q">queued</span>':
      opts.st==='error'?'<span class="chip err">error</span>':'<span class="chip proc">processing…</span>';
    div.innerHTML=`<div class="card-fn">${fn}</div><div class="card-meta">${chip}</div>`;
    return div;
  }
  const {d,rs}=opts; const cleared=(rs==='reviewed'||rs==='withheld');
  div.className='card done'+(cleared?' cleared':'');
  const tick=rs==='reviewed'?'<span class="tick">✓</span>':'';
  const pre=d?esc(d.text.slice(0,150)).replace(/\s+/g,' '):'';
  div.innerHTML=`<div class="card-fn">${tick}${fn}</div><div class="card-meta">${docChips(rs,d)}</div><div class="card-pre">${pre}…</div>`;
  div.onclick=()=>openModal(d.doc_id);
  return div;
}
let _boardSig='';
function renderBoard(){
  const board=$('#board');
  if(!pkg){ board.innerHTML='<p class="empty">Loading…</p>'; lastNeeds=0; _boardSig=''; return; }
  const running=new Set();
  if(job && job.status==='running' && job.statuses){
    for(const id in job.statuses){ const s=job.statuses[id]; if(s==='processing'||s==='queued') running.add(id); }
  }
  const unreviewed=[], attention=[], cleared=[], nonresp=[];
  pkg.docs.forEach(d=>{ const rs=reviewState(d);
    if(rs==='unprocessed') unreviewed.push(d);
    else if(rs==='escalated'||rs==='agent-responsive') attention.push([d.filename,d,rs]);
    else if(rs==='withheld') nonresp.push([d.filename,d,rs]);   // officer marked non-responsive
    else cleared.push([d.filename,d,rs]);                       // reviewed -> released
  });
  lastNeeds = unreviewed.length + attention.length;
  checkAutoIntake();
  // only touch the DOM when the board would actually change — polling otherwise rebuilds
  // everything every 0.8s and flickers
  const sig=JSON.stringify([
    unreviewed.map(d=>[d.doc_id, running.has(d.doc_id), agentSel.has(d.doc_id)]),
    attention.map(([fn,d,rs])=>[d.doc_id, rs, d.redactions.length]),
    cleared.map(([fn,d,rs])=>[d.doc_id, rs]),
    nonresp.map(([fn,d,rs])=>[d.doc_id, rs])
  ]);
  if(sig===_boardSig) return;
  _boardSig=sig;

  board.innerHTML='';
  const grp=(label,n)=>{ const h=document.createElement('p'); h.className='board-grp';
    h.textContent=`${label} (${n})`; board.appendChild(h); };
  const grid=()=>{ const g=document.createElement('div'); g.className='cards'; board.appendChild(g); return g; };
  if(unreviewed.length){                  // Unreviewed — untouched, selectable, with run controls
    const nSel=unreviewed.filter(d=>agentSel.has(d.doc_id)).length;
    const row=document.createElement('div'); row.className='board-grp-row';
    row.innerHTML=`<span class="board-grp">Unreviewed (${unreviewed.length})</span>
      <span class="grp-actions">
        <button onclick="runAgentOn(null)">Use agent for all</button>
        <button class="primary" onclick="runAgentSelected()" ${nSel?'':'disabled'}>Run agent on selected (${nSel})</button>
      </span>`;
    board.appendChild(row);
    const g=grid(); unreviewed.forEach(d=>g.appendChild(rawCard(d, running.has(d.doc_id))));
  }
  if(attention.length){ grp('Needs attention', attention.length); const g=grid();
    attention.forEach(([fn,d,rs])=>g.appendChild(card(fn,{d,rs}))); }
  if(cleared.length){ grp('Cleared — released', cleared.length); const g=grid();
    cleared.forEach(([fn,d,rs])=>g.appendChild(card(fn,{d,rs}))); }
  if(nonresp.length){ grp('Marked non-responsive', nonresp.length); const g=grid();
    nonresp.forEach(([fn,d,rs])=>g.appendChild(card(fn,{d,rs}))); }
}
function rawCard(d, running){
  const div=document.createElement('div');
  div.className='card raw'+(running?' processing':'')+(agentSel.has(d.doc_id)?' picked':'');
  const pre=esc(d.text.slice(0,150)).replace(/\s+/g,' ');
  const cb=running?'':`<input type="checkbox" class="card-cb" ${agentSel.has(d.doc_id)?'checked':''}>`;
  const meta=running?'<span class="chip proc">processing…</span>'
    :`<span class="chip type">${d.doc_type}</span><span class="chip q">not triaged</span>`;
  div.innerHTML=`${cb}<div class="card-fn">${d.filename}</div>
    <div class="card-meta">${meta}</div><div class="card-pre">${pre}…</div>`;
  if(running) return div;
  const c=div.querySelector('.card-cb');
  c.onclick=(e)=>{ e.stopPropagation(); c.checked?agentSel.add(d.doc_id):agentSel.delete(d.doc_id); renderBoard(); };
  div.onclick=()=>openModal(d.doc_id);
  return div;
}

/* ---- review modal: open a document, review + redact, approve ---- */
function openModal(doc_id){ sel={doc:doc_id,idx:null}; $('#docModal').showModal(); renderModal(); }
function closeModal(){ $('#docModal').close(); }
$('#docModal').addEventListener('close',()=>{ sel={doc:null,idx:null}; pendingSel=null; hideTip(); renderBoard(); });

/* hover a redaction highlight -> a cursor-following tooltip naming its exemption type.
   Lives on <body> (fixed) so the scrolling document column can't clip it. */
// append INSIDE the <dialog> — showModal() puts the dialog in the browser top layer, so a
// body-level tooltip (even at z-index 9999) renders BEHIND the modal. A child of the dialog
// shares its top layer and shows above the document text.
const tipEl=document.createElement('div'); tipEl.className='hovertip'; $('#docModal').appendChild(tipEl);
function hideTip(){ tipEl.style.display='none'; }
function onDocHover(e){
  const m=e.target.closest && e.target.closest('mark[data-tip]');
  if(!m){ hideTip(); return; }
  tipEl.textContent=m.getAttribute('data-tip');
  tipEl.style.display='block';
  const pad=12;
  let x=e.clientX+14, y=e.clientY+18;
  if(x+tipEl.offsetWidth+pad>innerWidth) x=e.clientX-tipEl.offsetWidth-14;   // flip near right edge
  if(y+tipEl.offsetHeight+pad>innerHeight) y=e.clientY-tipEl.offsetHeight-14; // flip near bottom
  tipEl.style.left=x+'px'; tipEl.style.top=y+'px';
}
$('#m-text').addEventListener('mousemove', onDocHover);
$('#m-text').addEventListener('mouseleave', hideTip);

/* drag to select text -> the right panel offers the exemption type to withhold it as */
let pendingSel=null;
function onTextSelect(){
  const d=curDoc(); if(!d || !d.agent_run) return;   // no manual redaction before triage
  const off=selectionOffsets($('#m-text'));
  if(off && off.text.trim()){ pendingSel=off; sel.idx=null; renderModalDetail(); }
  else if(pendingSel){ pendingSel=null; renderModalDetail(); }   // selection cleared -> drop picker
}
function cancelPending(){ pendingSel=null; window.getSelection().removeAllRanges(); renderModalDetail(); }
$('#m-text').addEventListener('mouseup', onTextSelect);

function bannerHtml(d){
  if(!d.agent_run) return `<div class="banner esc"><b>Not yet triaged.</b>
    Run the triage agent to assess responsiveness and propose redactions.
    <div style="margin-top:8px"><button class="acc" onclick="runAgentOn(['${d.doc_id}'])">Run agent on this document</button></div></div>`;
  if(d.escalated) return `<div class="banner esc"><b>Escalated — responsiveness undetermined.</b>
    The agent declined to decide and routed this to you. Use the buttons below to determine it.</div>`;
  if(d.officer_responsive===true) return `<div class="banner non" style="color:var(--accept);border-color:var(--accept)"><b>Responsive</b> — determined by officer; redactions applied.</div>`;
  if(d.officer_responsive===false) return `<div class="banner non"><b>Withheld</b> — non-responsive; excluded from the release.</div>`;
  if(!d.responsive) return `<div class="banner non"><b>Non-responsive.</b> ${esc(d.responsive_rationale||'')} Confidence ${(d.responsive_confidence*100|0)}%.</div>`;
  return `<div class="banner non" style="color:var(--accept);border-color:var(--accept)"><b>Responsive</b> — assessed by the agent. Review the highlighted redactions, then mark it below.</div>`;
}
function docTextHtml(d){
  // longest-first on ties so a contained span is covered by its container
  const spans=[...d.redactions.map((r,i)=>({...r,i}))].sort((a,b)=> a.start-b.start || b.end-a.end);
  let html='', cur=0;
  spans.forEach(s=>{
    const start=Math.max(s.start, cur);    // CLIP to current position (don't drop overlaps)
    if(start>=s.end) return;               // fully covered by an earlier redaction
    html+=esc(d.text.slice(cur,start));     // plain text before this span
    if(s.status==='rejected'){
      html+=esc(d.text.slice(start,s.end)); // rejected -> plain text, no highlight
    } else {
      // black out ONLY after the officer marks the doc responsive; before that, highlight.
      const cls=(d.officer_responsive===true)?'accepted':'proposed';
      const selc=(sel.idx===s.i)?' sel':'';
      html+=`<mark class="${cls}${selc}" data-ex="${s.exemption}" data-tip="${esc(redTip(s))}" onclick="pickInModal(${s.i})">${esc(d.text.slice(start,s.end))}</mark>`;
    }
    cur=s.end;
  });
  html+=esc(d.text.slice(cur));
  return html;
}
function renderModal(){
  const d=curDoc(); if(!d){ closeModal(); return; }
  $('#m-title').innerHTML=`${d.filename} <span class="chip type">${d.doc_type}</span>`;
  $('#m-banner').innerHTML=bannerHtml(d);
  $('#m-text').innerHTML=docTextHtml(d);
  // responsiveness decision + redaction only make sense AFTER the agent has triaged
  const proc=!!d.agent_run;
  $('#m-resp').disabled=!proc; $('#m-nonresp').disabled=!proc;
  $('#m-hint').textContent = proc ? 'Click & drag over any text in the document to withhold it.'
                                  : 'Run the agent to triage this document before reviewing.';
  renderModalDetail();
}
function pickInModal(i){ pendingSel=null; sel.idx=i; $('#m-text').innerHTML=docTextHtml(curDoc()); renderModalDetail(); }
function renderModalDetail(){
  const d=curDoc(); const el=$('#m-detail');
  if(!d){ el.innerHTML=''; return; }
  if(pendingSel){   // a fresh text selection awaiting an exemption type
    el.innerHTML=`<div class="detail">
      <div class="ex">Withhold this selection</div>
      <div class="basis">&ldquo;${esc(pendingSel.text.slice(0,90))}${pendingSel.text.length>90?'…':''}&rdquo;</div>
      <div class="kv">Withhold it as:</div>
      <div class="ex-pick">${EXEMPTIONS.map(x=>
        `<button class="${x.cls}" onclick="redactSelection('${x.code}')" title="${esc(x.tip)}">${x.label}</button>`).join('')}
      </div>
      <button onclick="cancelPending()">Cancel</button></div>`;
    return;
  }
  if(sel.idx==null){ el.innerHTML='<p class="empty">Click a highlighted redaction to review it — or drag to select text in the document and withhold it.</p>'; return; }
  const r=d.redactions[sel.idx]; if(!r){ el.innerHTML='<p class="empty">Select a highlighted redaction.</p>'; return; }
  const pick=(x)=>`<button class="${x.cls}${r.exemption===x.code?' current':''}" onclick="reclassify('${x.code}')">${x.label}</button>`;
  el.innerHTML=`<div class="detail">
    <div class="ex">${r.exemption||'—'} · ${r.pii_type||'—'}</div>
    <div class="basis">${esc(r.basis||'')}</div>
    <div class="kv"><b>detector</b> ${r.detector||'—'}</div>
    <div class="kv"><b>confidence</b> ${(r.confidence*100|0)}%
      <div class="conf-bar"><span style="width:${(r.confidence*100|0)}%"></span></div></div>
    <div class="rationale">${esc(r.rationale||'')}</div>
    <div class="kv">Classify as:</div>
    <div class="ex-pick">${EXEMPTIONS.map(pick).join('')}</div>
    <div class="actions"><button class="rej" onclick="decide('rejected')">Delete redaction</button></div></div>`;
}
// Single source of truth for the withholding types offered everywhere in the redaction UI.
const EXEMPTIONS=[
  {code:'b6',  cls:'ex-b6',  label:'b6 · Personal privacy',         tip:'SSN, address, phone, DOB, email, names of private individuals', basis:'5 U.S.C. § 552(b)(6) — personal privacy (PII)'},
  {code:'b5',  cls:'ex-b5',  label:'b5 · Deliberative process',     tip:'Pre-decisional recommendations, attorney-client advice, work product', basis:'5 U.S.C. § 552(b)(5) — deliberative process / attorney-client / work product'},
  {code:'b7E', cls:'ex-b7e', label:'b7E · Law-enforcement technique', tip:'Investigative techniques/procedures whose disclosure risks circumvention', basis:'5 U.S.C. § 552(b)(7)(E) — law-enforcement techniques and procedures'},
  {code:'b4',  cls:'ex-b4',  label:'b4 · Confidential commercial',  tip:'Trade secrets, confidential commercial/financial info — e.g. account/invoice numbers', basis:'5 U.S.C. § 552(b)(4) — trade secrets / confidential commercial or financial information'},
  {code:'b7C', cls:'ex-b7c', label:'b7C · LE personal privacy',     tip:'Personal privacy in law-enforcement records — suspects, targets, third parties named in an investigation', basis:'5 U.S.C. § 552(b)(7)(C) — law-enforcement records, unwarranted invasion of personal privacy'},
  {code:'b7D', cls:'ex-b7d', label:'b7D · Confidential source',     tip:'Identity of a confidential source/informant, or info that would reveal one', basis:'5 U.S.C. § 552(b)(7)(D) — identity of a confidential source'},
  {code:'b7F', cls:'ex-b7f', label:'b7F · Personal safety',         tip:'Locating details whose release could endanger an individual’s life or physical safety', basis:'5 U.S.C. § 552(b)(7)(F) — endanger the life or physical safety of an individual'},
];
const BASIS=Object.fromEntries(EXEMPTIONS.map(x=>[x.code,x.basis]));
const EXMAP=Object.fromEntries(EXEMPTIONS.map(x=>[x.code,x.label]));
// human-readable hover label for a redaction span: exemption + (meaningful) detector type
function redTip(s){
  const lbl=EXMAP[s.exemption]||s.exemption;
  const t=(s.pii_type && !['contextual','manual'].includes(s.pii_type)) ? ' · '+s.pii_type.replace(/_/g,' ') : '';
  return lbl+t;
}
async function reclassify(exemption){
  const d=curDoc(); if(!d || sel.idx==null) return;
  const r=d.redactions[sel.idx]; if(!r || r.exemption===exemption) return;
  r.exemption=exemption; r.basis=BASIS[exemption]||r.basis;
  fetch('/api/reclassify',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({doc_id:d.doc_id,redaction_index:sel.idx,exemption})}).catch(()=>{});
  renderModal();   // updates the [b5]/[b6] label on the span + the detail panel
}
async function decide(action){
  const d=curDoc();
  await fetch('/api/decision',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({doc_id:d.doc_id,redaction_index:sel.idx,action})});
  d.redactions[sel.idx].status=action; d.redactions[sel.idx].decided_by='officer';
  if(action==='rejected') sel.idx=null;   // no longer a redaction; clear its detail too
  renderModal(); renderBoard();
}
async function resolve(doc_id, responsive){
  await fetch('/api/resolve',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({doc_id,responsive})});
  const d=pkg.docs.find(x=>x.doc_id===doc_id); d.escalated=false; d.officer_responsive=responsive;
  if(responsive){   // apply: accept every still-open redaction so they black out
    for(let i=0;i<d.redactions.length;i++){
      if(d.redactions[i].status==='proposed'){
        await fetch('/api/decision',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({doc_id,redaction_index:i,action:'accepted'})});
        d.redactions[i].status='accepted'; d.redactions[i].decided_by='officer';
      }
    }
  }
  renderModal(); renderBoard();
  toast(responsive?'Marked responsive — redactions applied.':'Marked non-responsive — withheld from release.');
}
/* ---- highlight text -> add a redaction ---- */
function selectionOffsets(container){
  const s=window.getSelection();
  if(!s.rangeCount || s.isCollapsed) return null;
  const range=s.getRangeAt(0);
  if(!container.contains(range.commonAncestorContainer)) return null;
  const pre=range.cloneRange(); pre.selectNodeContents(container); pre.setEnd(range.startContainer,range.startOffset);
  const start=pre.toString().length, txt=range.toString();
  return {start, end:start+txt.length, text:txt};
}
async function redactSelection(exemption){
  const d=curDoc(); if(!d || !d.agent_run) return;   // no manual redaction before triage
  const off=pendingSel || selectionOffsets($('#m-text'));
  if(!off || !off.text.trim()){ toast('Drag to select text in the document first.'); return; }
  await fetch('/api/add_redaction',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({doc_id:d.doc_id,start:off.start,end:off.end,text:off.text,exemption})});
  // same kind of highlight as the agent's until the doc is marked responsive
  const st=d.officer_responsive===true?'accepted':'proposed';
  d.redactions.push({start:off.start,end:off.end,text:off.text,pii_type:'manual',exemption,
    basis:BASIS[exemption]||'Manually flagged',detector:'officer',confidence:1.0,
    rationale:'Manually flagged by the reviewing officer.',status:st,decided_by:'officer'});
  pendingSel=null; window.getSelection().removeAllRanges();
  renderModal(); renderBoard(); toast(`Withheld as ${exemption}.`);
}
async function markResp(responsive){
  const d=curDoc(); if(!d || !d.agent_run) return;   // can only decide once the agent has triaged
  if(responsive){
    // the instant the officer commits: flash every highlight to its applied (black) form,
    // then let it sit a beat before the modal closes
    $('#m-text').querySelectorAll('mark.proposed').forEach(m=>m.className='accepted');
  }
  d.escalated=false; d.officer_responsive=responsive;
  const calls=[fetch('/api/resolve',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({doc_id:d.doc_id,responsive})})];
  if(responsive) d.redactions.forEach((r,i)=>{ if(r.status==='proposed'){ r.status='accepted'; r.decided_by='officer';
    calls.push(fetch('/api/decision',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({doc_id:d.doc_id,redaction_index:i,action:'accepted'})})); } });
  await Promise.all(calls).catch(()=>{});            // persist before the board re-buckets
  toast(responsive?'Marked responsive — redactions applied.':'Marked non-responsive — withheld from release.');
  await new Promise(r=>setTimeout(r, responsive?420:80));   // hold the black flash
  closeModal();
}
async function produce(){
  if(!pkg){toast('Run the agent first.');return;}
  if(job && job.status==='running'){toast('Still processing — wait for the batch to finish.');return;}
  const r=await fetch('/api/produce',{method:'POST'});
  if(r.status===409){
    const e=(await r.json()).detail;
    let msg='Release blocked. ';
    if(e.unresolved_responsiveness && e.unresolved_responsiveness.length)
      msg+=`${e.unresolved_responsiveness.length} doc(s) need a responsiveness decision. `;
    if(e.docs_with_open_redactions && e.docs_with_open_redactions.length)
      msg+=`${e.docs_with_open_redactions.length} doc(s) have unreviewed redactions.`;
    toast(msg); showAudit(); return;
  }
  const out=await r.json();
  toast(`Cleared for release — ${out.released} document(s), every redaction reviewed.`);
  showAudit();
}
// tiny markdown -> HTML for the guideline/corpus .md bodies (headings, lists, **bold**, paragraphs)
function mdToHtml(md){
  const lines=esc(md||'').split('\n'); let html='', inList=false;
  const inline=s=>s.replace(/\*\*(.+?)\*\*/g,'<b>$1</b>');
  const closeList=()=>{ if(inList){ html+='</ul>'; inList=false; } };
  for(const ln of lines){
    if(/^\s*##\s+/.test(ln)){ closeList(); html+=`<h5>${inline(ln.replace(/^\s*##\s+/,''))}</h5>`; }
    else if(/^\s*#\s+/.test(ln)){ closeList(); html+=`<h4>${inline(ln.replace(/^\s*#\s+/,''))}</h4>`; }
    else if(/^\s*[-*]\s+/.test(ln)){ if(!inList){ html+='<ul>'; inList=true; } html+=`<li>${inline(ln.replace(/^\s*[-*]\s+/,''))}</li>`; }
    else if(ln.trim()===''){ closeList(); }
    else { closeList(); html+=`<p>${inline(ln)}</p>`; }
  }
  closeList(); return html;
}
function openRef(title, html){ $('#refTitle').textContent=title; $('#refBody').innerHTML=html; $('#refDlg').showModal(); }
function refCard(item){ return `<div class="ref-card"><div class="ref-h">${esc(item.title)} <span class="chip type">${esc(item.kind)}</span></div>${mdToHtml(item.body)}</div>`; }

async function openGuidelines(){
  const d=await (await fetch('/api/guidelines')).json();
  let html=`<p class="ref-lead">How we redact — the rules the agent applies (terrain <b>${esc(d.profile||'')}</b>). These are editable .md files; change them to change behavior, no code.</p>`;
  d.guidelines.forEach(g=> html+=refCard(g));
  if(d.examples){
    html+='<h3 class="ex-h">Worked examples — criteria &amp; before/after</h3>';
    html+='<div class="ex-criteria"><h3>What the agent looks for</h3>';
    d.examples.criteria.forEach(c=>{ html+=`<div class="crit"><span class="chip exr">${c.code}</span> <b>${esc(c.label)}</b> — ${esc(c.look_for)}</div>`; });
    html+='</div>';
    [['standard','Standard cases'],['challenging','Challenging cases']].forEach(([k,label])=>{
      const items=d.examples.examples.filter(e=>e.difficulty===k);
      if(!items.length) return;
      html+=`<h3 class="ex-h">${label}</h3>`;
      items.forEach(e=>html+=renderExample(e));
    });
  }
  openRef('Guidelines — how we redact', html);
}
async function openCorpus(){
  const d=await (await fetch('/api/corpus')).json();
  let html=`<p class="ref-lead">The regulations the agent references for terrain <b>${esc(d.profile||'')}</b> — injected as authoritative law at runtime.</p>`;
  if(!d.corpus.length) html+='<p class="empty">No regulations registered for this terrain.</p>';
  d.corpus.forEach(g=> html+=refCard(g));
  openRef('Corpus — regulations & authorities', html);
}
async function openLearned(){
  const d=await (await fetch('/api/learned')).json();
  let html=`<p class="ref-lead">Officer corrections the agent has tracked and now retrieves (RAG) when a new document resembles them — terrain <b>${esc(d.profile||'')}</b>. ${d.count} recorded.</p>`;
  if(!d.corrections.length){
    html+='<p class="empty">No corrections yet. When an officer removes, adds, or reclassifies a redaction, it is tracked here and fed back into the agent at runtime.</p>';
  } else {
    html+='<ul class="learned">';
    d.corrections.forEach(c=>{
      const verb=c.action==='rejected'?'<span class="lv rel">released</span>'
                :c.action==='reclassified'?'<span class="lv recl">reclassified</span>'
                :'<span class="lv add">withheld&nbsp;(missed)</span>';
      const tag=c.action==='reclassified'?`${esc(c.prior_exemption||'?')} → ${esc(c.exemption||'?')}`:esc(c.exemption||'');
      html+=`<li>${verb} <b>&ldquo;${esc((c.text||'').slice(0,100))}&rdquo;</b>${tag?` <span class="chip exr">${tag}</span>`:''}</li>`;
    });
    html+='</ul>';
  }
  openRef('Learned — officer corrections (RAG source)', html);
}
function highlightExample(e){
  let html=esc(e.text);
  e.redactions.forEach(r=>{ const q=esc(r.quote); html=html.replace(q, `<mark class="proposed">${q}</mark>`); });
  return html.replace(/\n/g,'<br>');
}
function renderExample(e){
  const finds=e.redactions.map(r=>`<li><span class="chip exr">${r.exemption}</span> <b>${esc(r.label)}</b> — ${esc(r.why)}</li>`).join('');
  const kept=(e.skip||[]).map(s=>`<li class="kept">✓ kept &ldquo;${esc(s.quote)}&rdquo; — ${esc(s.why)}</li>`).join('');
  const diffChip=e.difficulty==='challenging'?'<span class="chip esc">challenging</span>':'<span class="chip resp">standard</span>';
  return `<div class="ex-card">
    <div class="ex-title"><b>${esc(e.title)}</b> ${diffChip}<span class="chip type">${e.doc_type}</span></div>
    <div class="ex-grid">
      <div><div class="ex-lab">Document — agent flags</div><div class="doctext small">${highlightExample(e)}</div></div>
      <div><div class="ex-lab">Released as</div><div class="doctext small after">${esc(e.after).replace(/\n/g,'<br>')}</div></div>
    </div>
    <ul class="ex-finds">${finds}${kept}</ul>
    <div class="ex-note">${esc(e.note)}</div>
  </div>`;
}
function showAudit(){
  if(!pkg)return; $('#auditBody').textContent=pkg.audit.map(a=>
    `${a.at}  ${(a.actor||'').padEnd(8)} ${a.action}  ${JSON.stringify(a.detail)}`).join('\n');
  $('#auditDlg').showModal();
}
init();
