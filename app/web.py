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
import os

from engine.assess import assess
from engine.render import json_for_script

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
.overlay{position:fixed;inset:0;background:rgba(21,23,27,.45);backdrop-filter:blur(2px);
  display:none;align-items:flex-start;justify-content:center;padding:14vh 20px 20px;z-index:50;}
.overlay.show{display:flex;}
.modal{background:var(--surface);border:1px solid var(--line-strong);border-radius:14px;
  width:100%;max-width:520px;padding:22px 22px 18px;box-shadow:0 18px 50px rgba(0,0,0,.22);
  animation:pop .14s ease-out;}
@keyframes pop{from{opacity:0;transform:translateY(-6px) scale(.98);}to{opacity:1;transform:none;}}
.modal h2{margin:0 0 4px;font-size:19px;}
.modal .desc{color:var(--muted);font-size:13.5px;margin:0 0 14px;}
.modal textarea{width:100%;border:1px solid var(--line-strong);border-radius:9px;padding:11px 12px;
  font:inherit;font-size:15px;min-height:70px;resize:vertical;}
.modal textarea:focus{outline:none;border-color:var(--flag);box-shadow:0 0 0 3px rgba(47,98,150,.12);}
.modal .actions{display:flex;justify-content:flex-end;gap:9px;margin-top:14px;}
.modal .hintline{color:var(--faint);font-size:12px;margin-top:9px;}
pre{background:#0d0f13;color:#e6edf3;border-radius:9px;padding:13px 15px;overflow-x:auto;
  font-family:var(--mono);font-size:12.5px;line-height:1.65;margin:10px 0;}
pre .c{color:#7d8590;}
.doc code{font-family:var(--mono);font-size:.88em;background:#eef0f2;padding:1px 5px;border-radius:5px;color:#0d0f13;}
.doc h2{font-size:20px;margin:34px 0 6px;letter-spacing:-.01em;}
.doc h3{font-size:15px;margin:22px 0 4px;}
.doc p,.doc li{color:var(--muted);line-height:1.65;}
.doc ul,.doc ol{margin:8px 0;padding-left:22px;} .doc li{margin:3px 0;}
.toc{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 8px;}
.toc a{font-size:13px;padding:5px 12px;border:1px solid var(--line-strong);border-radius:20px;color:var(--muted);}
.toc a:hover{border-color:var(--chrome);color:var(--ink);text-decoration:none;}
.cmdtbl{width:100%;border-collapse:collapse;margin:8px 0;font-size:13.5px;}
.cmdtbl td{border-top:1px solid var(--line);padding:8px 8px;vertical-align:top;color:var(--muted);}
.cmdtbl td:first-child{white-space:nowrap;width:1%;}
.note{background:#eef4fb;border-left:3px solid var(--flag);border-radius:0 8px 8px 0;
  padding:11px 14px;margin:14px 0;font-size:13.5px;color:var(--ink);line-height:1.6;}
.thesis{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:0 0 26px;}
.tcol{border:1px solid var(--line);border-radius:12px;padding:15px 16px;background:var(--surface);}
.tnum{font-family:var(--mono);font-size:11px;letter-spacing:.08em;text-transform:uppercase;
  color:var(--flag);margin-bottom:6px;}
.tcol p{margin:0;font-size:13.5px;line-height:1.55;color:var(--muted);}
@media (max-width:720px){
  .wrap{padding:22px 16px 64px;}
  h1{font-size:24px;}
  .thesis{grid-template-columns:1fr;gap:10px;margin-bottom:20px;}
  .bar{gap:8px;}
  .bar input:not([type=number]){flex:1 1 100%!important;min-width:0;}
  .bar input[type=number]{flex:0 0 72px!important;}
  .bar .btn{flex:1;text-align:center;justify-content:center;}
  .grid{grid-template-columns:1fr;}
  .toc{gap:6px;} .cmdtbl td:first-child{white-space:normal;}
  pre{font-size:11.5px;}
}
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
    <div class="kicker">Ground Knowledge</div>
    <h1>Research disputes, mapped by the evidence</h1>
    <p class="sub">See how the evidence on a contested question splits — and whether an apparent
    consensus is real, or just the same few sources counted many times.</p>

    <div class="thesis">
      <div class="tcol">
        <div class="tnum">1 · The problem</div>
        <p>Count the sources and whoever publishes most appears to win — even when it's the same
        lab, funder, or dataset showing up again and again. That's how <b>false balance</b> creeps in.</p>
      </div>
      <div class="tcol">
        <div class="tnum">2 · The fix</div>
        <p>We weight each side by <b>independent</b> evidence, not headcount. Ten papers resting on
        one dataset count as roughly <b>one</b> look — not ten.</p>
      </div>
      <div class="tcol">
        <div class="tnum">3 · How</div>
        <p>Each source is fetched, labelled with a quote-backed position, and merged into a living
        map — positions, shared datasets, the real cruxes, and each side's blind spots.</p>
      </div>
    </div>

    <div class="bar">
      <input id="q" placeholder="Search questions…" autocomplete="off">
      <button class="btn ghost" onclick="newQ()">+ New question</button>
      <a class="btn ghost" href="/docs" style="display:inline-flex;align-items:center;text-decoration:none">⌨ CLI &amp; local app</a>
    </div>
    <div id="list" class="grid"></div>

    <div class="overlay" id="nqOverlay">
      <div class="modal" role="dialog" aria-modal="true" aria-labelledby="nqTitle">
        <h2 id="nqTitle">New question</h2>
        <p class="desc">State it as a yes/no research dispute — the way the two sides would frame it.</p>
        <textarea id="nqInput" placeholder="e.g. Do eggs increase cardiovascular disease risk?"></textarea>
        <div class="hintline">Press ⌘/Ctrl + Enter to create · Esc to cancel</div>
        <div class="actions">
          <button class="btn ghost" onclick="closeNQ()">Cancel</button>
          <button class="btn" id="nqCreate" onclick="createNQ()">Create question</button>
        </div>
      </div>
    </div>

    <script>
    const E=s=>String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
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
    const ov=document.getElementById('nqOverlay'), nqIn=document.getElementById('nqInput');
    function newQ(){ov.classList.add('show');nqIn.value='';setTimeout(()=>nqIn.focus(),30);}
    function closeNQ(){ov.classList.remove('show');}
    async function createNQ(){
      const question=nqIn.value.trim();
      if(!question){nqIn.focus();return;}
      const btn=document.getElementById('nqCreate');btn.disabled=true;btn.textContent='Creating…';
      try{
        const r=await fetch('/api/questions',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({question})});
        const j=await r.json();
        if(j.id){location.href='/q/'+j.id;return;}
        btn.disabled=false;btn.textContent='Create question';
      }catch(e){btn.disabled=false;btn.textContent='Create question';}
    }
    ov.addEventListener('click',e=>{if(e.target===ov)closeNQ();});       // click outside to close
    document.addEventListener('keydown',e=>{
      if(e.key==='Escape')closeNQ();
      if((e.metaKey||e.ctrlKey)&&e.key==='Enter'&&ov.classList.contains('show'))createNQ();
    });
    let t;document.getElementById('q').addEventListener('input',()=>{clearTimeout(t);t=setTimeout(load,200);});
    load();
    </script>"""
    return _page("Ground Knowledge", body)


def docs_html(repo="https://github.com/Zhenia-Magic/epistemic-coverage"):
    """Static documentation page for the power-user tools: the CLI and the local web console.
    Explains why they exist, how to install and configure them, and the command reference."""
    body = """
    <div class="back"><a href="/">← all questions</a></div>
    <div class="kicker">Ground Knowledge</div>
    <h1>Power tools: the CLI &amp; local app</h1>
    <p class="sub">This website is the easy, keyless way to read and contribute. The
    <b>command-line tool</b> and the <b>local web console</b> are for power users who want to
    cold-start a whole question, label sources automatically with their own AI key, batch-import
    citations, or curate the knowledge base — then push the result back here.</p>

    <div class="doc">
    <div class="toc">
      <a href="#why">Why these exist</a>
      <a href="#install">Install</a>
      <a href="#configure">Configure</a>
      <a href="#localui">Local app</a>
      <a href="#cli">CLI commands</a>
      <a href="#workflow">A typical workflow</a>
      <a href="#sync">Push &amp; pull</a>
    </div>

    <h2 id="why">Why these exist</h2>
    <p>The portal does <b>no AI work and holds no API key</b> — contributing here means you fetch a
    source, copy a labelling prompt into your own chatbot, and paste the JSON back. That keeps the
    server safe and free to run. The local tools remove the copy-paste: with your own AI key they
    label automatically, and they can do things the website can't:</p>
    <ul>
      <li><b>Cold-start</b> a brand-new question from scratch (discover + label dozens of sources).</li>
      <li><b>Automatic labelling</b> of fetched text using your own key — Anthropic, OpenAI, DeepSeek, Mistral, Groq, Gemini, or OpenRouter.</li>
      <li><b>Import a whole library</b> from Zotero / Mendeley / EndNote (.ris, .bib, .csl-json).</li>
      <li><b>Curate</b>: merge duplicate datasets, rename, tidy messy labels.</li>
      <li><b>Push / pull</b> a knowledge base between your machine and this portal.</li>
    </ul>
    <div class="note">Everything except <i>automatic</i> labelling works with <b>no API key</b> —
    finding, fetching, the local store, the viewer, push/pull and the manual (chatbot) path all run
    keyless, exactly like this website.</div>

    <h2 id="install">Install</h2>
    <p>You need <b>Python 3.9+</b> and <code>git</code>. The core engine, viewer, fetching, the
    manual path and the local store need <b>no third-party packages</b>.</p>
    <pre><span class="c"># 1. Get the code</span>
git clone %REPO%.git
cd epistemic-coverage

<span class="c"># 2. (optional) extras — only for full-text PDF, .docx, or running the portal yourself</span>
pip install -r requirements.txt</pre>
    <p>That's it — <code>python cli.py --help</code> should now list every command.</p>

    <h2 id="configure">Configure (.env)</h2>
    <p>Copy the template and fill in what you need. A key is <b>only</b> required for automatic
    labelling; leave it blank to use the manual / chatbot path.</p>
    <pre><span class="c"># copy the template, then edit .env</span>
cp .env.example .env</pre>
    <table class="cmdtbl">
      <tr><td><code>ANTHROPIC_API_KEY</code></td><td>Your AI key for automatic labelling. Web search / deep research need Anthropic.</td></tr>
      <tr><td>… or any one of</td><td><code>OPENAI_API_KEY</code>, <code>DEEPSEEK_API_KEY</code>, <code>MISTRAL_API_KEY</code>, <code>GROQ_API_KEY</code>, <code>GEMINI_API_KEY</code>, <code>OPENROUTER_API_KEY</code> — all label fetched text fine. The local app also lets you paste a key and pick the provider.</td></tr>
      <tr><td><code>EPISTEMIC_PORTAL</code></td><td>This portal's URL for push/pull — e.g. <code>https://groundknowledge.org</code>.</td></tr>
      <tr><td><code>EPISTEMIC_CONTACT_EMAIL</code></td><td>Your email → OpenAlex/Crossref "polite pool" (faster, higher limits).</td></tr>
      <tr><td><code>EPISTEMIC_ADMIN_TOKEN</code></td><td>Only if you run a portal and want to push whole-KB replacements.</td></tr>
    </table>

    <h2 id="localui">The local app (browser console)</h2>
    <p>Prefer clicking to typing? Launch a local web console that drives the same
    find → fetch → label → merge flow in your browser — including a panel to paste your API key and
    to push/pull cases to this portal.</p>
    <pre>python cli.py ui            <span class="c"># opens http://localhost:8765</span>
python cli.py ui --port 9000 --no-open</pre>

    <h2 id="cli">CLI command reference</h2>
    <p>Every command is <code>python cli.py &lt;command&gt;</code>. Add <code>--build</code> to most
    mutating commands to regenerate the static viewer afterwards.</p>

    <h3>Create &amp; inspect</h3>
    <table class="cmdtbl">
      <tr><td><code>init &lt;id&gt; "question"</code></td><td>Start a new, empty knowledge base.</td></tr>
      <tr><td><code>show &lt;kb&gt;</code></td><td>Print a summary of the KB.</td></tr>
      <tr><td><code>assess &lt;kb&gt;</code></td><td>Print the metrics (distribution, independence, cruxes, blindspots).</td></tr>
      <tr><td><code>build &lt;kb…&gt;</code></td><td>Render the standalone HTML viewer for one or more cases.</td></tr>
    </table>

    <h3>Add sources</h3>
    <table class="cmdtbl">
      <tr><td><code>research &lt;kb&gt;</code></td><td><b>One-shot cold start.</b> Emits a single prompt for a browsing chatbot that discovers <i>and</i> labels sources. <code>--apply</code> to auto-merge with a key.</td></tr>
      <tr><td><code>discover &lt;kb&gt;</code></td><td>Find candidate sources (<code>--source api</code> = OpenAlex, no key; <code>web</code> / <code>both</code> use AI search).</td></tr>
      <tr><td><code>ingest &lt;kb&gt; &lt;url&gt;</code></td><td>Fetch one source and label it. <code>--dry-run</code> writes a prompt to paste into a chatbot; <code>--apply</code> labels with your key.</td></tr>
      <tr><td><code>ingest-batch &lt;kb&gt; …</code></td><td>Fetch + label many at once. <code>--bundle</code> (with <code>--dry-run</code>) packs them into ONE file to upload to a chatbot.</td></tr>
      <tr><td><code>harvest &lt;kb&gt;</code></td><td>Discover + ingest in one step.</td></tr>
      <tr><td><code>add &lt;kb&gt; &lt;delta&gt;</code></td><td>Merge a hand-written / chatbot-returned delta JSON into the KB.</td></tr>
    </table>

    <h3>Curate</h3>
    <table class="cmdtbl">
      <tr><td><code>dups &lt;kb&gt;</code></td><td>List likely-duplicate entities worth merging.</td></tr>
      <tr><td><code>merge &lt;kb&gt; &lt;type&gt; &lt;src&gt; &lt;dst&gt;</code></td><td>Fold one entity (position/dataset/factor/…) into another.</td></tr>
      <tr><td><code>rename &lt;kb&gt; &lt;type&gt; &lt;ref&gt; "label"</code></td><td>Rename an entity.</td></tr>
      <tr><td><code>tidy &lt;kb&gt;</code></td><td>Prettify id-style / slug labels for display.</td></tr>
    </table>

    <h3>Citations &amp; sync</h3>
    <table class="cmdtbl">
      <tr><td><code>import-citations &lt;kb&gt; &lt;file&gt;</code></td><td>Import a Zotero/Mendeley/EndNote export (.ris, .bib, .csl-json) and label each entry.</td></tr>
      <tr><td><code>export &lt;kb&gt; --format bibtex|ris|csl</code></td><td>Export the KB's sources as a citation file.</td></tr>
      <tr><td><code>questions</code></td><td>List the questions on the portal.</td></tr>
      <tr><td><code>pull &lt;id&gt;</code></td><td>Download a question's KB from the portal to your machine.</td></tr>
      <tr><td><code>push &lt;kb&gt;</code></td><td>Send your local sources up to the portal.</td></tr>
    </table>

    <h2 id="workflow">A typical workflow</h2>
    <p>Cold-start a new question locally, then publish it here:</p>
    <pre><span class="c"># 1. create it</span>
python cli.py init eggs "Do eggs increase cardiovascular disease risk?"

<span class="c"># 2. find + label sources automatically (needs an AI key in .env)</span>
python cli.py harvest cases/eggs.kb.json --k 12 --build

<span class="c"># 3. no key? do it the manual way — get one file, label it in your chatbot, merge it back</span>
python cli.py ingest-batch cases/eggs.kb.json --from finds.json --dry-run --bundle
python cli.py add cases/eggs.kb.json delta.json --build

<span class="c"># 4. look at it</span>
python cli.py assess cases/eggs.kb.json

<span class="c"># 5. publish to the portal</span>
python cli.py push cases/eggs.kb.json --as "Your name"</pre>

    <h2 id="sync">Push &amp; pull (sync with this portal)</h2>
    <p>Set <code>EPISTEMIC_PORTAL</code> in <code>.env</code> (or pass <code>--portal</code>), then:</p>
    <pre>python cli.py questions                 <span class="c"># browse what's here</span>
python cli.py pull &lt;question-id&gt;        <span class="c"># grab it locally</span>
python cli.py push cases/your.kb.json   <span class="c"># send your sources up</span></pre>
    <div class="note">Source-by-source contributions are <b>keyless</b> — anyone can push new
    sources. Replacing a whole knowledge base is gated by the portal's admin token.</div>

    <p style="margin-top:30px"><a href="%REPO%">Full source &amp; README on GitHub →</a></p>
    </div>"""
    body = body.replace("%REPO%", repo)
    return _page("CLI & local app — Ground Knowledge", body)


def viewer_html(qid, get_question):
    """The read view: reuse viewer/template.html, populated live with this one question."""
    q = get_question(qid)
    if not q:
        return None
    bundle = {"order": [q["id"]], "cases": {q["id"]: {"kb": q["kb"], "assessment": assess(q["kb"])}}}
    with open(os.path.join(ROOT, "viewer", "template.html"), encoding="utf-8") as f:
        tpl = f.read()
    html = tpl.replace("/*__DATA__*/null", json_for_script(bundle))
    # inject a thin top bar linking back home and to the contribute flow.
    # Use a <style> (not inline colors) so :hover works — inline styles can't carry :hover.
    # Built by concatenation (NOT .format) because the CSS braces would clash with placeholders.
    qe = _esc(qid)
    bar = ("<style>.pbar{max-width:980px;margin:0 auto;padding:14px 24px 0;"
           "font-family:ui-monospace,monospace;font-size:12px;}"
           ".pbar a{color:#2f6296;text-decoration:none;cursor:pointer;}"
           ".pbar a:hover{text-decoration:underline;}"
           ".pbar .sep{color:#8A9098;margin:0 8px;} .pbar .lbl{color:#8A9098;}</style>"
           "<div class='pbar'><a href='/'>← all questions</a>"
           "<span class='sep'>·</span><a href='/q/" + qe + "/add'>+ add sources</a>"
           "<span class='sep'>·</span><span class='lbl'>export</span> "
           "<a href='/api/questions/" + qe + "/export?format=kb'>knowledge base (JSON)</a>"
           "<span class='lbl'> · citations:</span> "
           "<a href='/api/questions/" + qe + "/export?format=bibtex'>BibTeX</a> "
           "<a href='/api/questions/" + qe + "/export?format=ris'>RIS</a> "
           "<a href='/api/questions/" + qe + "/export?format=csl'>CSL-JSON</a>"
           "<span class='sep'>·</span><a href='/q/" + qe + "/manage'>manage</a></div>")
    return html.replace("<body>", "<body>" + bar, 1)


def manage_html(qid, get_question):
    """Admin-only moderation page: remove sources or delete the whole question. Gated by an
    admin token (set ADMIN_TOKEN on the server, paste it here once — stored on this device)."""
    q = get_question(qid)
    if not q:
        return None
    head = """
    <div class="back"><a href="/q/{id}">← back to the report</a></div>
    <div class="kicker">Admin · moderation</div>
    <h1>{question}</h1>
    <div id="gate"></div>
    <div id="panel" style="display:none">
      <div class="panel"><h2>Sources</h2>
        <p class="desc">Remove a source if it's inappropriate or spam — the metrics recompute.</p>
        <div id="srcs"></div></div>
      <div class="panel"><h2 style="color:#B4502E">Danger zone</h2>
        <p class="desc">Delete the entire question and every source in it. This cannot be undone.</p>
        <button class="btn" style="background:#B4502E;border-color:#B4502E" onclick="delQuestion()">Delete this question</button>
        <button class="btn ghost" style="margin-left:8px" onclick="signOut()">Sign out admin</button></div>
    </div>
    """.format(id=_esc(qid), question=_esc(q["question"]))
    script = """
    <script>
    const QID="{id}";
    const E=s=>String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const tok=()=>localStorage.getItem('gk_admin')||'';
    const H=()=>({'Content-Type':'application/json','X-Admin-Token':tok()});
    async function isAdmin(){ if(!tok())return false;
      try{const r=await fetch('/api/admin-check',{method:'POST',headers:H()});return (await r.json()).admin;}catch(e){return false;} }
    async function render(){
      if(!await isAdmin()){
        document.getElementById('panel').style.display='none';
        document.getElementById('gate').innerHTML=`<div class="panel"><h2>Enter admin token</h2>
          <p class="desc">Paste the admin token to manage this question. Stored on this device only.</p>
          <div class="bar"><input id="tk" type="password" placeholder="admin token" style="flex:1">
          <button class="btn" onclick="saveTok()">Unlock</button></div>
          <div id="gerr" class="toast warn"></div></div>`;
        return;
      }
      document.getElementById('gate').innerHTML='';
      document.getElementById('panel').style.display='';
      const r=await fetch('/api/questions/'+QID); const kb=(await r.json()).kb||{};
      const srcs=kb.sources||[];
      document.getElementById('srcs').innerHTML = srcs.length? srcs.map(s=>`
        <div class="cand"><div style="flex:1"><b>${E(s.title)}</b> <span class="why">${E(s.year||'')}</span><br>
        <span class="why">${E((s.authors||[]).slice(0,3).join(', '))}${(s.authors||[]).length>3?' et al.':''}</span></div>
        <button class="btn ghost" onclick="delSource('${s.id}',this)">✕ remove</button></div>`).join('')
        : '<div class="empty">No sources yet.</div>';
    }
    async function saveTok(){
      const t=document.getElementById('tk').value.trim(); if(!t)return;
      localStorage.setItem('gk_admin',t);
      if(!await isAdmin()){localStorage.removeItem('gk_admin');document.getElementById('gerr').textContent='That token is not valid.';return;}
      render();
    }
    async function delSource(sid,btn){
      if(!confirm('Remove this source? The metrics will recompute.'))return; btn.disabled=true;
      const r=await fetch('/api/admin/delete-source',{method:'POST',headers:H(),body:JSON.stringify({id:QID,sourceId:sid})});
      const j=await r.json(); if(j.error){alert(j.error);btn.disabled=false;return;} render();
    }
    async function delQuestion(){
      if(!confirm('Delete the ENTIRE question and all its sources? This cannot be undone.'))return;
      const r=await fetch('/api/admin/delete-question',{method:'POST',headers:H(),body:JSON.stringify({id:QID})});
      const j=await r.json(); if(j.ok)location.href='/'; else alert(j.error||'failed');
    }
    function signOut(){localStorage.removeItem('gk_admin');render();}
    render();
    </script>""".replace("{id}", _esc(qid))
    return _page("Manage · " + q["question"], head + script)


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
      <p class="desc" style="margin:16px 0 6px">— or import from <b>Zotero / a reference manager</b> (.ris, .bib, .json) —</p>
      <input type="file" accept=".ris,.bib,.bibtex,.json" onchange="importCit(this)"
        style="font-size:13px;color:var(--muted)">
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
    const E=s=>String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    let CANDS=[];
    function renderFinds(note){
      const nFull = CANDS.filter(c=>c.relevance!=='partial').length;
      const head = note || `${CANDS.length} found · ${nFull} strong match${nFull===1?'':'es'} pre-selected. Review and tick/untick before fetching.`;
      document.getElementById('finds').innerHTML = CANDS.length? (
        `<p class="why" style="margin:6px 0 2px">${E(head)}</p>`+
        CANDS.map((c,i)=>{
        const partial = c.relevance==='partial';
        return `<div class="cand"><input type="checkbox" class="ck" data-i="${i}" onchange="upd()" ${partial?'':'checked'}>
         <label><b>${E(c.title)}</b>${partial?' <span class="why" style="color:#8a6510">· weaker match</span>':''}
         <span class="why">${E(c.why||'')}</span><br>
         <span class="why">${E(c.url)}</span></label></div>`;}).join(''))
        : '<div class="empty">No candidates found.</div>';
      upd();
    }
    async function find(){
      const k=document.getElementById('k').value||10;
      const r=await fetch(`/api/questions/${QID}/discover`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({k:+k})});
      const j=await r.json(); CANDS=j.candidates||[]; renderFinds();
    }
    function importCit(input){
      const f=input.files&&input.files[0]; if(!f)return;
      const rd=new FileReader();
      rd.onload=async()=>{
        const r=await fetch(`/api/questions/${QID}/import-citations`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:rd.result,filename:f.name})});
        const j=await r.json();
        if(j.error){document.getElementById('finds').innerHTML='<div class="empty">'+E(j.error)+'</div>';return;}
        CANDS=(j.candidates||[]).map(c=>({...c,relevance:'full'}));   // imported = all pre-selected
        renderFinds(`Imported ${CANDS.length} citation(s) from ${E(f.name)}. Review, then fetch & label.`);
      };
      rd.readAsText(f);
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
        <div style="margin:8px 0"><button class="btn" onclick="dlBundle(this)">⬇ Download labelling file</button>
        &nbsp;<button class="btn ghost" onclick="copyBundle(this)">Copy instead</button></div>`;
    }
    function flash(btn,msg){if(!btn)return;const t=btn.textContent;btn.textContent=msg;
      btn.disabled=true;setTimeout(()=>{btn.textContent=t;btn.disabled=false;},1500);}
    function dlBundle(btn){
      const blob=new Blob([BUNDLE],{type:'text/markdown'});
      const a=document.createElement('a');a.href=URL.createObjectURL(blob);
      a.download='label-sources.md';document.body.appendChild(a);a.click();a.remove();
      setTimeout(()=>URL.revokeObjectURL(a.href),1000);
      flash(btn,'Downloaded ✓');
    }
    function copyBundle(btn){
      navigator.clipboard.writeText(BUNDLE).then(()=>flash(btn,'Copied ✓'))
        .catch(()=>flash(btn,'Copy failed — select & copy manually'));
    }
    async function importDelta(){
      let data; try{data=JSON.parse(document.getElementById('delta').value);}catch(e){return toast('Not valid JSON: '+e.message,'warn');}
      const r=await fetch(`/api/questions/${QID}/delta`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({delta:data})});
      const j=await r.json();
      if(j.error)return toast(j.error,'warn');
      const off=j.offTopic?`, ${j.offTopic} skipped as off-topic`:'';
      toast(`Imported. ${j.added} added, ${j.duplicates||0} duplicate(s)${off}. Now v${j.version}. `,'ok');
    }
    function toast(m,c){const e=document.getElementById('imp');e.textContent=m;e.className='toast '+(c||'');
      if(c==='ok')e.innerHTML+=`<a href="/q/${QID}"> View the report →</a>`;}
    </script>""".replace("{id}", _esc(qid))
    return _page("Add sources · " + q["question"], body)
