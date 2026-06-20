"""HTML for the portal's browser experience (deployment layer, Phase 3).

Three pages, all served by app/portal.py:
  home_html()        GET /            browse + search questions, create a new one
  viewer_html(qid)   GET /q/{id}      the read view — reuses viewer/template.html, rendered live
  contribute_html()  GET /q/{id}/add  add sources WITHOUT a server key: find (OpenAlex, free) ->
                                       fetch real text -> copy a labelling prompt for YOUR chatbot
                                       -> paste the JSON back -> deterministic merge server-side.

The contribute flow keeps the manual / chatbot path available in the browser (no key ever leaves
the user's own chatbot), mirroring the local CLI's --dry-run path.
"""
import json
import os

from engine.assess import assess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# shared palette (matches viewer/template.html)
_CSS = """
:root{--bg:#F5F6F7;--surface:#FFFFFF;--ink:#15171B;--muted:#61676E;--faint:#8A9098;
--line:#E4E7EA;--line-strong:#CDD2D7;--chrome:#1B1D24;--ochre:#8a6510;--flag:#2f6296;
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
--sans:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;}
*{box-sizing:border-box;} body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);}
a{color:var(--flag);text-decoration:none;} a:hover{text-decoration:underline;}
.wrap{max-width:960px;margin:0 auto;padding:32px 24px 80px;}
.kicker{font-family:var(--mono);font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--faint);}
h1{font-size:30px;margin:6px 0 4px;letter-spacing:-.01em;} .sub{color:var(--muted);max-width:60ch;margin:0 0 26px;}
input,button,textarea{font:inherit;}
button{cursor:pointer;} input[type=checkbox]{cursor:pointer;}
.bar{display:flex;gap:10px;margin:0 0 22px;flex-wrap:wrap;}
.bar input{flex:1;min-width:220px;padding:11px 13px;border:1px solid var(--line-strong);border-radius:9px;background:#fff;}
.btn{padding:11px 16px;border:1px solid var(--chrome);background:var(--chrome);color:#fff;border-radius:9px;cursor:pointer;transition:opacity .15s,box-shadow .15s;}
.btn.ghost{background:#fff;color:var(--ink);border-color:var(--line-strong);}
.btn:hover{opacity:.92;box-shadow:0 1px 4px rgba(0,0,0,.12);} .btn.ghost:hover{border-color:var(--chrome);background:#fafbfc;}
.btn:disabled{opacity:.5;cursor:default;box-shadow:none;}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px;}
.card{display:block;border:1px solid var(--line);border-radius:12px;padding:16px;background:var(--surface);cursor:pointer;transition:border-color .15s,box-shadow .15s,transform .15s;}
.card:hover{border-color:var(--line-strong);text-decoration:none;box-shadow:0 4px 14px rgba(0,0,0,.09);transform:translateY(-2px);}
.card .q{font-size:16px;font-weight:600;color:var(--ink);line-height:1.35;}
.card:hover .q{color:var(--flag);}
.card .meta{font-family:var(--mono);font-size:12px;color:var(--faint);margin-top:10px;display:flex;gap:12px;flex-wrap:wrap;}
.empty{color:var(--muted);padding:30px 0;}
.back{font-family:var(--mono);font-size:12px;color:var(--muted);}
.back a{cursor:pointer;}
.panel{border:1px solid var(--line);border-radius:12px;padding:18px;background:var(--surface);margin:16px 0;}
.panel h2{font-size:17px;margin:0 0 4px;} .panel .desc{color:var(--muted);font-size:14px;margin:0 0 14px;}
.step{font-family:var(--mono);font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--faint);}
.cand{display:flex;gap:9px;align-items:flex-start;padding:8px 6px;border-top:1px solid var(--line);border-radius:6px;transition:background .12s;}
.cand:hover{background:#fafbfc;}
.cand label{font-size:14px;cursor:pointer;} .cand .why{color:var(--faint);font-size:12px;font-family:var(--mono);}
textarea{width:100%;border:1px solid var(--line-strong);border-radius:9px;padding:10px;min-height:120px;font-family:var(--mono);font-size:12px;}
.toast{margin-top:10px;font-size:13px;color:var(--muted);} .toast.ok{color:#2E8B6F;} .toast.warn{color:var(--ochre);}
.log{background:#0d0f13;color:#cfe;border-radius:8px;padding:10px;font-family:var(--mono);font-size:12px;white-space:pre-wrap;margin-top:10px;display:none;}
"""


def _esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _page(title, body):
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>{}</title><style>{}</style></head><body><div class='wrap'>{}</div></body></html>"
            ).format(_esc(title), _CSS, body)


def home_html():
    body = """
    <div class="kicker">Epistemic Coverage</div>
    <h1>Research disputes, mapped like the news</h1>
    <p class="sub">Browse questions, see how the evidence splits, and whether an apparent
    consensus is real — or the same few sources counted many times. Add a question or contribute
    sources to one.</p>
    <div class="bar">
      <input id="q" placeholder="Search questions…" autocomplete="off">
      <button class="btn ghost" onclick="newQ()">+ New question</button>
    </div>
    <div id="list" class="grid"></div>
    <script>
    const E=s=>(s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
    async function load(){
      const s=document.getElementById('q').value.trim();
      const r=await fetch('/api/questions'+(s?'?search='+encodeURIComponent(s):''));
      const {questions}=await r.json();
      const box=document.getElementById('list');
      if(!questions.length){box.innerHTML='<div class="empty">No questions yet. Create one above.</div>';return;}
      box.innerHTML=questions.map(q=>{
        const c=q.counts||{};
        return `<a class="card" href="/q/${q.id}">
          <div class="q">${E(q.question)}</div>
          <div class="meta"><span>v${c.version||0}</span><span>${c.sources||0} sources</span>
          <span>${c.positions||0} positions</span><span>${c.datasets||0} datasets</span></div></a>`;
      }).join('');
    }
    async function newQ(){
      const question=prompt('What research question? (e.g. "Do eggs increase cardiovascular disease risk?")');
      if(!question)return;
      const r=await fetch('/api/questions',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({question})});
      const j=await r.json();
      if(j.id)location.href='/q/'+j.id;
    }
    let t;document.getElementById('q').addEventListener('input',()=>{clearTimeout(t);t=setTimeout(load,200);});
    load();
    </script>"""
    return _page("Epistemic Coverage", body)


def viewer_html(qid, get_question):
    """The read view: reuse viewer/template.html, populated live with this one question."""
    q = get_question(qid)
    if not q:
        return None
    bundle = {"order": [q["id"]], "cases": {q["id"]: {"kb": q["kb"], "assessment": assess(q["kb"])}}}
    with open(os.path.join(ROOT, "viewer", "template.html"), encoding="utf-8") as f:
        tpl = f.read()
    html = tpl.replace("/*__DATA__*/null", json.dumps(bundle, ensure_ascii=False))
    # inject a thin top bar linking back home and to the contribute flow.
    # Use a <style> (not inline colors) so :hover works — inline styles can't carry :hover.
    # Built by concatenation (NOT .format) because the CSS braces would clash with placeholders.
    bar = ("<style>.pbar{max-width:980px;margin:0 auto;padding:14px 24px 0;"
           "font-family:ui-monospace,monospace;font-size:12px;}"
           ".pbar a{color:#2f6296;text-decoration:none;cursor:pointer;}"
           ".pbar a:hover{text-decoration:underline;}"
           ".pbar .sep{color:#8A9098;margin:0 8px;}</style>"
           "<div class='pbar'><a href='/'>← all questions</a>"
           "<span class='sep'>·</span><a href='/q/" + _esc(qid) + "/add'>+ add sources</a></div>")
    return html.replace("<body>", "<body>" + bar, 1)


def contribute_html(qid, get_question):
    q = get_question(qid)
    if not q:
        return None
    head = """
    <div class="back"><a href="/q/{id}">← back to the report</a></div>
    <div class="kicker">Add sources · no API key needed</div>
    <h1>{question}</h1>
    <p class="sub">Find papers, fetch their real text, then label them with <b>your own</b> chatbot
    — copy the prompt below into Claude or ChatGPT and paste its JSON back. Nothing you paste needs
    a key on our side; the merge is deterministic.</p>
    """.format(id=_esc(qid), question=_esc(q["question"]))
    body = head + """
    <div class="panel">
      <div class="step">Step 1 · Find or paste a URL</div><h2>Find candidate papers</h2>
      <p class="desc">Free scholarly search (OpenAlex) across the positions — no key.</p>
      <div class="bar"><input id="k" type="number" value="10" style="flex:0 0 90px">
        <button class="btn" onclick="find()">Find sources</button></div>
      <div id="finds"></div>
      <p class="desc" style="margin:16px 0 6px">— or add <b>one source by URL</b>, no search —</p>
      <div class="bar"><input id="oneUrl" placeholder="https://doi.org/… , a PubMed/arXiv link, or any article URL" style="flex:1">
        <button class="btn ghost" onclick="fetchUrl()">Fetch this URL ↓</button></div>
    </div>
    <div class="panel">
      <div class="step">Step 2 · Fetch &amp; get one file</div><h2>Fetch text &amp; get one labelling file</h2>
      <p class="desc">We download every selected paper's real text and bundle it into a <b>single
      file</b>. Upload that one file to Claude or ChatGPT and ask it to follow the instructions
      inside — it returns one JSON array for all of them. No more pasting prompts one by one.</p>
      <button class="btn" id="fetchBtn" disabled onclick="fetchSel()">Fetch &amp; build file</button>
      <div id="prompts"></div>
    </div>
    <div class="panel">
      <div class="step">Step 3 · Paste &amp; import</div><h2>Paste your chatbot's JSON</h2>
      <p class="desc">Paste the JSON array your chatbot returned, then import — it's merged into the report.</p>
      <textarea id="delta" placeholder='[ { "source": { "title": "...", "position": "NEW:...", ... }, "factorWeights": [...] } ]'></textarea>
      <div style="margin-top:10px"><button class="btn" onclick="importDelta()">Import</button></div>
      <div id="imp" class="toast"></div>
    </div>
    <script>
    const QID="{id}";
    const E=s=>(s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
    let CANDS=[];
    async function find(){
      const k=document.getElementById('k').value||10;
      const r=await fetch(`/api/questions/${QID}/discover`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({k:+k})});
      const j=await r.json(); CANDS=j.candidates||[];
      const nFull = CANDS.filter(c=>c.relevance!=='partial').length;
      document.getElementById('finds').innerHTML = CANDS.length? (
        `<p class="why" style="margin:6px 0 2px">${CANDS.length} found · ${nFull} strong match${nFull===1?'':'es'} pre-selected. Review and tick/untick before fetching.</p>`+
        CANDS.map((c,i)=>{
        const partial = c.relevance==='partial';
        return `<div class="cand"><input type="checkbox" class="ck" data-i="${i}" onchange="upd()" ${partial?'':'checked'}>
         <label><b>${E(c.title)}</b>${partial?' <span class="why" style="color:#8a6510">· weaker match</span>':''}
         <span class="why">${E(c.why||'')}</span><br>
         <span class="why">${E(c.url)}</span></label></div>`;}).join(''))
        : '<div class="empty">No candidates found.</div>';
      upd();
    }
    function selected(){return [...document.querySelectorAll('.ck:checked')].map(c=>CANDS[+c.dataset.i].url);}
    function upd(){document.getElementById('fetchBtn').disabled = selected().length===0;}
    let BUNDLE='';
    function fetchSel(){doFetch(selected());}
    function fetchUrl(){const u=document.getElementById('oneUrl').value.trim(); if(!u){return;} doFetch([u]);}
    async function doFetch(urls){
      if(!urls.length)return;
      const b=document.getElementById('prompts'); b.innerHTML='Fetching real text…';
      const r=await fetch(`/api/questions/${QID}/fetch`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({urls})});
      const j=await r.json();
      BUNDLE=j.bundle||'';
      const skip=j.skipped&&j.skipped.length?` · ${j.skipped.length} couldn't be fetched (skipped)`:'';
      if(!BUNDLE){b.innerHTML=`<p class="toast warn">Nothing fetched${skip}.</p>`;return;}
      b.innerHTML=`<p class="toast ok">Fetched ${j.fetched} source(s)${skip}.</p>
        <p class="desc">Upload this one file to your chatbot (or copy it), tell it to follow the
        instructions inside, then paste the JSON array it returns into Step 3.</p>
        <div style="margin:8px 0"><button class="btn" onclick="dlBundle()">⬇ Download labelling file</button>
        &nbsp;<button class="btn ghost" onclick="copyBundle()">Copy instead</button></div>`;
    }
    function dlBundle(){
      const blob=new Blob([BUNDLE],{type:'text/markdown'});
      const a=document.createElement('a');a.href=URL.createObjectURL(blob);
      a.download='label-sources.md';document.body.appendChild(a);a.click();a.remove();
      setTimeout(()=>URL.revokeObjectURL(a.href),1000);
    }
    function copyBundle(){navigator.clipboard.writeText(BUNDLE);}
    async function importDelta(){
      let data; try{data=JSON.parse(document.getElementById('delta').value);}catch(e){return toast('Not valid JSON: '+e.message,'warn');}
      const r=await fetch(`/api/questions/${QID}/delta`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({delta:data})});
      const j=await r.json();
      if(j.error)return toast(j.error,'warn');
      toast(`Imported. ${j.added} added, ${j.duplicates||0} duplicate(s). Now v${j.version}. `,'ok');
    }
    function toast(m,c){const e=document.getElementById('imp');e.textContent=m;e.className='toast '+(c||'');
      if(c==='ok')e.innerHTML+=`<a href="/q/${QID}"> View the report →</a>`;}
    </script>""".replace("{id}", _esc(qid))
    return _page("Add sources · " + q["question"], body)
