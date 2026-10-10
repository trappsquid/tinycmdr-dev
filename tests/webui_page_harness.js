// Run the web UI's REAL page script in Node, against a DOM shim and a fake
// server that mirrors WebRun's line semantics.
//
// The Python suites only ever inspected the server's line buffer. Every
// rendering defect the operator hit lived in the page: how it keys nodes, what
// it does with a line that grows in place, what it does when a new run restarts
// the index at 0, and what a RELOADED page does with a run already going (the
// reload used to post its message, which the server turned into a steer, so the
// operator saw their own message twice and their old one shoved away). So the
// page's own script runs here, unmodified, pulled straight out of tinycmdr.py's
// WEB_PAGE - and a "reload" step re-instantiates it against a fresh DOM the way
// a browser would.
//
// The shim is deliberately small but it must cover everything the page actually
// touches, or a green run here means nothing: the page renders each run into its
// own container (div.run) and reconciles the lines inside it by uid, so the shim
// needs dataset, insertBefore and remove. And the fake server's lines MUST carry
// the same uid the real WebRun._line() emits - the page skips lines without one,
// so a stale harness would report the page as broken (or, worse, as fine).
//
// usage: node webui_page_harness.js scenario.json page_script.js
// prints one JSON object: {rendered, runs, events, note, pages, errors}

const fs = require('fs');

const scenario = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
// the server stamps its version into the page; do the same, so the stale-client
// banner stays out of these scenarios (it is its own check)
const pageScript = fs.readFileSync(process.argv[3], 'utf8')
  .replace(/\{\{VERSION\}\}/g, 'harness');

const errors = [];
const events = [];
let pages = 0;

// ---------------------------------------------------------------- DOM shim
class El {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.parent = null;
    this._text = '';
    this._cls = new Set();
    this.style = {};
    this.dataset = {};
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.clientHeight = 0;
    this.value = '';
    this.placeholder = '';
    this.listeners = {};
    const self = this;
    this.classList = {
      add(c) { self._cls.add(c); },
      remove(c) { self._cls.delete(c); },
      contains(c) { return self._cls.has(c); },
      // the real toggle FLIPS when `on` is absent; this used to always delete,
      // so a plain classList.toggle('x') never added anything (found 2026-10-04)
      toggle(c, on) { const want = (on === undefined) ? !self._cls.has(c) : !!on;
        if (want) { self._cls.add(c); } else { self._cls.delete(c); } },
    };
  }
  get className() { return [...this._cls].join(' '); }
  set className(v) { this._cls = new Set(String(v).split(/\s+/).filter(Boolean)); }
  appendChild(n) { this.children.push(n); n.parent = this; return n; }
  insertBefore(n, ref) {
    const at = ref ? this.children.indexOf(ref) : -1;
    if (at < 0) { this.children.push(n); } else { this.children.splice(at, 0, n); }
    n.parent = this;
    return n;
  }
  remove() {
    if (!this.parent) { return; }
    const i = this.parent.children.indexOf(this);
    if (i >= 0) { this.parent.children.splice(i, 1); }
    this.parent = null;
  }
  addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); }
  getAttribute(name) { return this._attrs ? this._attrs[name] : undefined; }
  setAttribute(name, value) { (this._attrs = this._attrs || {})[name] = String(value); }
  focus() {}
  click() {
    // an offered download draws an anchor with a name; this is how a page suite sees
    // which name the browser would have saved
    if (this.download) { (globalThis.__downloads = globalThis.__downloads || []).push(this.download); }
    for (const fn of this.listeners.click || []) { fn({ preventDefault() {} }); }
  }
  set textContent(v) { this.children = []; this._text = String(v); }
  get textContent() {
    if (this.children.length === 0) { return this._text; }
    return this.children.map((c) => c.textContent).join('');
  }
  set scrollTop(v) { this._scrollTop = v; }
  get scrollTop() { return this._scrollTop || 0; }
}

// every id the page looks up at load; a missing one makes getElementById return
// null and the page dies on the next property write
const IDS = ['log', 'in', 'send', 'stop', 'ver',
             'note', 'notetext', 'noteact',
             // the rail, the meter and the panel drawer: a missing id makes
             // getElementById null and the page dies on its first write
             'rail', 'sessions', 'title', 'model', 'meterfill', 'drawer', 'panel',
             'pal', 'host', 'allclients', 'menu', 'tools', 'newchat', 'tabs',
             'panelclose', 'lanewarn',
             // the page's composer gained a file button and a hidden input
             'clip', 'file',
             // the lane banner: its text span and its dismiss button
             'lanetext', 'lanedismiss',
             // the live surfaces: the lane banner's detail/actions, the amber config notice,
             // and the empty state with its two actions
             'lanedetail', 'laneretry', 'lanemore',
             'configwarn', 'configtext', 'configdismiss',
             'empty', 'emptymark',  // freshDom writes its src from the markup
             'emptynew', 'emptylast',
             'stage-status', 'stage-state',
             // the page shell: the rail's filter, the host card, the hero's stats and
             // the stage header's state
             'filter', 'hits', 'hostver', 'stage-state', 'logwrap',
             'stat-session', 'stat-context', 'stat-model'];
const byId = {};
function freshDom() {
  for (const id of IDS) { byId[id] = new El(id === 'in' ? 'textarea' : 'div'); }
  // the page's own markup gives the empty state its art (`<img id=emptymark src=...>`) and
  // the lane detail starts closed (`<span id=lanedetail hidden>`); the shim has no HTML
  // parser, so put the two attributes the driver reads back
  byId.emptymark.setAttribute('src', '/mark.png');
  byId.lanedetail.hidden = true;
  globalThis.document.body = new El('body');
}

globalThis.document = {
  addEventListener: () => {},
  title: '',
  getElementById: (id) => byId[id] || null,
  createElement: (tag) => new El(tag),
  createTextNode: (t) => { const e = new El('#text'); e._text = String(t); return e; },
  body: new El('body'),
};
const store = { fb_token: 'test-token' };
store.removeItem = (k) => { delete store[k]; };
globalThis.localStorage = store;
let promptCalls = 0;
let promptMsg = '';
globalThis.prompt = (msg) => { promptCalls++; promptMsg = String(msg); return 'test-token'; };
globalThis.window = { addEventListener() {} };
globalThis.setInterval = () => 0;
// The page takes its token from the link (?token=...) first and strips it from
// the address bar again, so it reads location.search at load and calls
// history.replaceState. A shim missing either one throws at load and the page
// reads as dead - which is what the real defect looked like in a browser.
// scenario.no_token starts the browser with nothing remembered, which is the
// install whose page has to ASK.
if (scenario.no_token) { delete store.fb_token; }
globalThis.location = { search: scenario.query || '', hash: scenario.hash || '',
                        pathname: '/',
                        href: 'http://127.0.0.1:8790/' + (scenario.query || '') };
const replaced = [];
globalThis.history = { replaceState: (_s, _t, url) => { replaced.push(url); } };
// the page makes blob URLs for downloads; Node's URL.createObjectURL refuses a plain
// object, and without this the fetchFile path throws before it names the file
globalThis.URL = {
  createObjectURL: () => 'blob:harness',
  revokeObjectURL: () => {},
};

const authSeen = [];
let authFlaked = false;
const loginCalls = [];
const uploads = [];
let healthFetches = 0;

// -------------------------------------------------------- fake web server
// Mirrors tinycmdr.py's WebRun: growth follows an explicit "streaming line"
// pointer, an identical fragment is a no-op, growth in place keeps the index,
// and a tool call ends the turn (the next text starts a new line).
const runs = [];
let runSeq = 0;
let active = null;

// Mirrors WebRun._line(): the uid is the page's identity for a line - stable
// for the life of the run. Without it here the page's reconciler skips every
// line it is handed (it only draws lines that carry a uid).
function line(r, kind, text) {
  const i = r.lines.length;
  return { i, uid: r.id + '#' + i, kind, text, r: ++r.rev, t: i };
}

function grow(r, kind, text) {
  const t = (r.stream_i === null || r.stream_i === undefined) ? null : r.lines[r.stream_i];
  if (t) {
    const same = ((t.kind === 'say' || t.kind === 'final') && (kind === 'say' || kind === 'final'))
      || t.kind === kind;
    if (same) {
      if (text === t.text) { return null; }
      if (text.indexOf(t.text) === 0) {
        t.text = text; t.kind = kind; t.r = ++r.rev;
        events.push({ run: r.id, kind: 'grow', i: t.i, text });
        return t;
      }
    }
  }
  const l = line(r, kind, text);
  r.lines.push(l);
  r.stream_i = l.i;
  events.push({ run: r.id, kind: 'add', i: l.i, text });
  if (kind === 'tool' || kind === 'tool_done' || kind === 'tool_fail') { r.stream_i = null; }
  return l;
}

function startRun(spec, conv) {
  const r = { id: 'r' + (++runSeq), conv: conv || 'web', spec, cursor: 0, lines: [],
              rev: 0, stream_i: null, done: false };
  runs.push(r); active = r;
  return r;
}

// Mirrors WebRun.add(): always a new line. It does NOT move the streaming
// pointer (except after a tool line), which is exactly why a mid-run steer does
// not break the line the model is still growing.
function addLine(r, kind, text) {
  const l = line(r, kind, text);
  r.lines.push(l);
  if (kind === 'tool' || kind === 'tool_done' || kind === 'tool_fail') { r.stream_i = null; }
  events.push({ run: r.id, kind: 'add', i: l.i, text });
  return l;
}

function pollRun(id, since, rev) {
  const r = runs.find((x) => x.id === id);
  if (!r) { return null; }
  // one scripted fragment per poll: that is how the real callbacks arrive
  if (r.cursor < r.spec.length) {
    const [k, t, ui] = r.spec[r.cursor++];
    const l = grow(r, k, t);
    if (ui && l) { l.ui = ui; }        // a card line carries its payload, like WebRun._line
  }
  const done = r.cursor >= r.spec.length;
  r.done = done;
  // mirror WebRun.view(): lines at/after the index, PLUS lines the caller has
  // already passed whose text grew in place since rev
  const lines = r.lines.filter((l) => l.i >= since);
  const updates = r.lines.filter((l) => l.i < since && l.r > rev);
  return { lines, updates, rev: r.rev, done, elapsed: 1.0, status: 'working', steps: 1 };
}

const jres = (o) => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(o) });

// the conversations the fake host knows about. One to start with, the same way
// a real host has the shared conversation; the page is expected to read the list
// at boot, open one, and paint it from /api/session.
let convSeq = 0;
const sessions = [{ key: 'web', title: 'the shared conversation', created: 0,
                    last_active: 0, exchanges: 0, tokens: 0, model: 'main',
                    owner: 'shared', live: null }];

function fetchShim(url, opts) {
  const body = (opts && typeof opts.body === 'string') ? JSON.parse(opts.body) : {};
  const hdrs = (opts && opts.headers) || {};
  if ('X-Tinycmdr-Token' in hdrs) { authSeen.push(hdrs['X-Tinycmdr-Token']); }
  if (url.indexOf('/api/download') === 0) {
    // what _web_filename_header sends: an ASCII fallback plus filename*=UTF-8''.
    const nm = scenario.download_name || 'report.pdf';
    const ascii = scenario.download_ascii || nm.replace(/[^\x20-\x7e]/g, '_');
    return Promise.resolve({ ok: true, status: 200,
      headers: { get: (h) => (String(h).toLowerCase() === 'content-disposition'
        ? "attachment; filename=" + ascii + "; filename*=UTF-8''" + encodeURIComponent(nm)
        : null) },
      blob: () => Promise.resolve({}), json: () => Promise.resolve({}) });
  }
  if (url.indexOf('/api/upload') === 0) {
    // the page POSTs the File itself; only the name and the token matter here
    const name = decodeURIComponent((url.split('name=')[1] || 'x').split('&')[0]);
    uploads.push({ name: name, token: hdrs['X-Tinycmdr-Token'] || '' });
    return jres({ path: 'uploads/123_' + name, name: '123_' + name, bytes: 12,
                  note: '[attachment saved to uploads/123_' + name + ']' });
  }
  // the scenario may carry a health payload, so the page's lane banner is gradeable
  if (url.indexOf('/api/health') === 0) {
    healthFetches++;
    return jres(scenario.health || { ok: true, version: 'harness' });
  }
  if (url.indexOf('/api/search') === 0) {
    // the operator's search INSIDE conversations: the scenario carries the hits
    return jres({ query: (url.split('q=')[1] || ''), matches: scenario.search_hits || [] });
  }
  if (url.indexOf('/api/sessions') === 0) {
    // scenario.auth_401_once: the FIRST list call is refused, which is the stale
    // token a browser wakes up with; the page must ask once and retry the GET.
    if (scenario.auth_401_once && !authFlaked) {
      authFlaked = true;
      return Promise.resolve({ ok: false, status: 401,
                               json: () => Promise.resolve({ error: 'unauthorized' }) });
    }
    if (opts && opts.method === 'POST') {
      if (body.op === 'new') {
        const k = 'web-new' + (++convSeq);
        sessions.unshift({ key: k, title: 'a new conversation', created: 0,
                           last_active: 0, exchanges: 0, tokens: 0, model: 'main',
                           owner: 'mine', live: null });
        return jres({ key: k, sessions: sessions });
      }
      if (body.op === 'open') { return jres({ open: body.key }); }
      if (body.op === 'rename') { return jres({ ok: true, sessions: sessions }); }
      if (body.op === 'delete') { return jres({ deleted: body.key, sessions: [] }); }
      return jres({ error: 'unknown op' });
    }
    return jres({ sessions: sessions, open: 'web', budget: 200000,
                  host: 'harness', version: 'harness' });
  }
  if (url.indexOf('/api/session?') === 0) {
    // what a reload paints: THIS conversation's runs, in order. scenario.session_status
    // >= 400 makes the load fail, which the page must show as a card with a way out.
    if (scenario.session_status >= 400) {
      return Promise.resolve({ ok: false, status: scenario.session_status,
                               json: () => Promise.resolve({}) });
    }
    const key = (url.split('key=')[1] || 'web').split('&')[0];
    const mine = runs.filter((r) => r.conv === key);
    const reply = jres({ key: key, runs: mine.map((r) => ({ run_id: r.id, lines: r.lines,
                                                            live: !r.done })) });
    if (scenario.session_delay) {
      // hold the reply for N driver ticks: the switch-mid-flight race needs the first
      // open's reply to land AFTER the second open has run
      return new Promise((res) => {
        let n = scenario.session_delay;
        const step = () => (n-- <= 0 ? res(reply) : setImmediate(step));
        step();
      });
    }
    return reply;
  }
  if (url.indexOf('/api/commands') === 0) {
    return jres({ commands: [{ cmd: '/new', help: 'fresh conversation' },
                             { cmd: '/status', help: 'this host' }] });
  }
  if (url.indexOf('/api/tasks') === 0) { return jres({ items: [], next_id: 1 }); }
  if (url.indexOf('/api/jobs') === 0) { return jres({ jobs: [], scheduler: false }); }
  if (url.indexOf('/api/login') === 0) {
    loginCalls.push(((opts && opts.method) || 'GET') + ':' + ((opts && opts.headers && opts.headers['X-Tinycmdr-Token']) ? 'hdr' : 'no-hdr'));
    // GET is the page's "does this browser already authenticate?" probe (the HttpOnly
    // cookie is unreadable to the page, readable to the server). A browser with no
    // token and no cookie is refused - the default, and what a fresh profile looks
    // like; scenario.login_ok marks one whose cookie already authenticates.
    if ((opts && opts.method) === 'GET' && scenario.login_status) {
      // a server that refuses the page itself (the Host/Origin check): the page must
      // name the address, not the token
      return Promise.resolve({ ok: false, status: scenario.login_status,
                               json: () => Promise.resolve({ error: 'refused' }) });
    }
    if ((opts && opts.method) === 'GET' && !scenario.login_ok) {
      return Promise.resolve({ ok: false, status: 401,
                               json: () => Promise.resolve({ error: 'unauthorized' }) });
    }
    return jres({ ok: true });
  }
  if (url.indexOf('/api/log') === 0) { return jres({ lines: [], path: 'tinycmdr.log' }); }
  if (url.indexOf('/api/inventory') === 0) {
    return jres({ skills: [], tools: [], spill: { files: 0, bytes: 0 } });
  }
  if (url.indexOf('/api/live') === 0) {
    // a page that just loaded asks who is running instead of posting a message
    // and having the server turn that into a steer of the run already going
    return jres({ run_id: (active && !active.done) ? active.id : null });
  }
  if (url.indexOf('/api/events') === 0) {
    const q = {};
    url.split('?')[1].split('&').forEach((p) => { const kv = p.split('='); q[kv[0]] = kv[1]; });
    const v = pollRun(q.run_id, parseInt(q.since || '0', 10), parseInt(q.rev || '0', 10));
    if (v === null) { return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({ error: 'no such run' }) }); }
    return jres(v);
  }
  if (url.indexOf('/api/steer') === 0) {
    const r = runs.find((x) => x.id === body.run_id);
    if (r && !r.done) { addLine(r, 'you', body.message + '   (mid-run)'); }
    return jres({ queued: true });
  }
  if (url.indexOf('/api/stop') === 0) { return jres({ stopping: true }); }
  if (url.indexOf('/api/run') === 0) {
    if (scenario.busy_reply && active && !active.done) {
      addLine(active, 'you', body.message + '   (mid-run)');
      return jres({ run_id: active.id, busy: true, steered: true });
    }
    const spec = scenario.runs[runs.length];
    const r = startRun(spec, body.session);
    addLine(r, 'you', body.message);
    return jres({ run_id: r.id, busy: false });
  }
  if (url.indexOf('/api/chat') === 0) { return jres({ reply: 'command reply' }); }
  return jres({});
}
globalThis.fetch = fetchShim;

// ---------------------------------------- the clipboard a plain-http page has
// navigator does not exist in Node, and the LAN page is served over http:// (not a
// secure context), so the path that really runs there is the document one. Capture
// what the selection would have copied, the way execCommand would have.
const copied = [];
globalThis.document.execCommand = (cmd) => {
  const kids = (globalThis.document.body && globalThis.document.body.children) || [];
  const ta = kids[kids.length - 1];
  if (cmd === 'copy' && ta && ta.value !== undefined) { copied.push(ta.value); }
  return true;
};

// -------------------------------------------------------------- fake clock
let timers = [];
globalThis.setTimeout = (fn) => { timers.push(fn); return timers.length; };
globalThis.clearTimeout = () => {};

async function tick() {
  const t = timers; timers = [];
  for (const f of t) { await f(); }
  await new Promise((r) => setImmediate(r));
  return t.length;
}

// --------------------------------------------------------- run the page
// The script is evaluated as the body of a function (so a "reload" can
// re-evaluate it cleanly) and exports the two functions the driver drives. The
// page source itself is untouched: this appended line is the whole difference.
const EXPORTS = "\n;globalThis.__page={send:send,stop:stop,"
  + "newConversation:newConversation,openSession:openSession,fetchFile:fetchFile,"
  + "uploadFiles:uploadFiles,versionCheck:versionCheck,"
  + "renameSession:renameSession,state:function(){return {sessionKey:sessionKey,"
  + "sessions:sessions};}};";

function loadPage() {
  pages++;
  freshDom();
  timers = [];                      // a reload kills the old page's poll loop
  globalThis.__page = null;
  try {
    new Function(pageScript + EXPORTS)();
  } catch (e) {
    errors.push('page script threw at load: ' + e.message);
  }
  return globalThis.__page || {};
}
loadPage();

// Each run draws into its own container (div.run, display:contents), so a line
// is that container's child - not a direct child of #log.
function logNodes() {
  const out = [];
  for (const c of byId.log.children) {
    const kids = (c.className === 'run' && c.children.length) ? c.children : [c];
    for (const k of kids) { out.push(k); }
  }
  return out;
}

function rendered() {
  return logNodes().map((k) => ({
    cls: k.className,
    text: k.textContent,
    // the copy button is a child element, so it survives in the render report
    hasCopy: (k.children || []).some((c) => c.className === 'copyb'),
    // an offered download draws an anchor (the click fetches with the token)
    hasFileLink: (k.children || []).some((c) => c.tagName === 'a'),
  }));
}

function noteState() {
  return { text: byId.notetext.textContent, shown: byId.note.className.indexOf('show') >= 0 };
}

async function main() {
  await new Promise((r) => setImmediate(r));
  for (const step of scenario.steps) {
    const page = () => globalThis.__page || {};
    if (step.kind === 'message') {
      byId.in.value = step.text;
      await page().send();
      for (let i = 0; i < (step.polls || 12); i++) { await tick(); }
    } else if (step.kind === 'type') {
      // type into the box during a run and press send: the steer path
      byId.in.value = step.text;
      await page().send();
      for (let i = 0; i < (step.polls || 4); i++) { await tick(); }
    } else if (step.kind === 'reload') {
      // a browser reload: fresh DOM, the transcript comes back from the SERVER
      // (/api/session), and the page has to work out on its own that a run is
      // still going and re-attach to it
      loadPage();
      const n = step.polls || 3;
      for (let i = 0; i < n; i++) { await tick(); }
    } else if (step.kind === 'call') {
      // drive the rail the way a click does
      const fn = page()[step.fn];
      if (typeof fn === 'function') {
        const p = fn.apply(null, step.args || []);
        if (!step.fire) { await p; }   // fire: leave it in flight (a race is the subject)
      }
      for (let i = 0; i < (step.polls || 4); i++) { await tick(); }
    } else if (step.kind === 'input') {
      // type into a field and fire its handler (the rail's search box)
      const el = byId[step.id];
      if (!el) { errors.push('input step: no element #' + step.id); }
      else {
        el.value = step.text;
        if (typeof el.oninput === 'function') { await el.oninput({}); }
        for (let i = 0; i < (step.polls || 4); i++) { await tick(); }
      }
    } else if (step.kind === 'click') {
      // click an element by id: either wiring works (`onclick=`, or addEventListener)
      const el = byId[step.id];
      if (!el) {
        errors.push('click step: no element #' + step.id);
      } else if (typeof el.onclick === 'function') {
        await el.onclick({ preventDefault() {} });
      } else {
        for (const fn of (el.listeners.click || [])) { await fn({ preventDefault() {} }); }
      }
      for (let i = 0; i < (step.polls || 2); i++) { await tick(); }
    } else if (step.kind === 'health') {
      // swap the health payload mid-scenario (a lane that fails differently)
      scenario.health = step.value;
      for (let i = 0; i < (step.polls || 2); i++) { await tick(); }
    } else if (step.kind === 'polls') {
      for (let i = 0; i < (step.n || 1); i++) { await tick(); }
    } else if (step.kind === 'copy') {
      // click the copy button on the box that contains <text>: the real path
      const target = logNodes().find((k) => (step.cls === undefined || k.className.indexOf(step.cls) >= 0)
        && k.textContent.indexOf(step.text) >= 0);
      if (!target) {
        errors.push('copy step: no box contains ' + JSON.stringify(step.text));
      } else {
        const b = (target.children || []).find((c) => c.className === 'copyb');
        if (!b) { errors.push('copy step: that box has no copy button'); }
        else { for (const fn of (b.listeners.click || [])) { await fn({ stopPropagation() {} }); } }
      }
    }
  }
  const out = {
    state: (globalThis.__page && globalThis.__page.state)
      ? globalThis.__page.state() : {},
    rendered: rendered(),
    runs: runs.map((r) => ({ id: r.id, lines: r.lines.map((l) => ({ i: l.i, kind: l.kind, text: l.text })) })),
    events,
    note: noteState(),
    pages,
    copied,
    prompts: promptCalls,
    bodyCls: (globalThis.document.body || {}).className || '',
    hits: (byId.hits || {}).textContent || '',
    hitsShown: !(byId.hits || {}).hidden,
    stage: (byId['stage-state'] || {}).textContent || null,
    stageS: (byId['stage-status'] || {dataset:{}}).dataset.s,
    logins: loginCalls,
    promptMsg: promptMsg,
    replaced: replaced,
    auth: authSeen,
    token: store.fb_token,
    uploads: uploads,
    downloads: (globalThis.__downloads || []),
    composer: byId.in.value,
    ver: { cls: byId.ver.className, title: byId.ver.title },
    warn: { cls: byId.lanewarn.className, text: byId.lanetext.textContent,
            detail: byId.lanetext ? byId.lanedetail.textContent : '',
            detailShown: byId.lanedetail ? !byId.lanedetail.hidden : false },
    config: { cls: byId.configwarn.className, text: byId.configtext.textContent },
    empty: { hidden: !!byId.empty.hidden, text: byId.empty.textContent,
             img: byId.emptymark.getAttribute('src') },
    retryLabel: byId.laneretry.textContent,
    healthFetches: healthFetches,
    title: globalThis.document.title,
    errors,
  };
  process.stdout.write(JSON.stringify(out));
}

main().catch((e) => {
  errors.push('driver failed: ' + e.message);
  process.stdout.write(JSON.stringify({ rendered: rendered(), runs: [], events,
                                        note: noteState(), pages, copied, errors }));
});
