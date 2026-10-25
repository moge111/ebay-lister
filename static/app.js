import { html, render, useCallback, useEffect, useMemo, useRef, useState } from "./vendor/preact-htm.js";
import { api, Ctx, isIdentified, itemLabel, money, PACKAGE_LABELS, STATUS, timeAgo, uploadFiles, useApp, useFetch, useKey } from "./lib.js";
import { ItemDrawer } from "./item.js";

// ------------------------------------------------------------------ routing

function useRoute() {
  const parse = () => {
    const [path, query = ""] = location.hash.replace(/^#/, "").split("?");
    return { parts: path.split("/").filter(Boolean), query: new URLSearchParams(query) };
  };
  const [route, setRoute] = useState(parse());
  useEffect(() => {
    const fn = () => setRoute(parse());
    window.addEventListener("hashchange", fn);
    return () => window.removeEventListener("hashchange", fn);
  }, []);
  return route;
}

// ------------------------------------------------------------------ app shell

function App() {
  const route = useRoute();
  const [version, setVersion] = useState(0);
  const [state, setState] = useState(null);
  const [jobIds, setJobIds] = useState([]);
  const [toasts, setToasts] = useState([]);
  const [drawer, setDrawer] = useState(null); // {id, ids}

  const refresh = useCallback(() => setVersion((v) => v + 1), []);
  const toast = useCallback((msg, bad = false) => {
    const t = { id: Math.random(), msg, bad };
    setToasts((ts) => [...ts, t]);
    setTimeout(() => setToasts((ts) => ts.filter((x) => x !== t)), bad ? 7000 : 3200);
  }, []);
  const watchJob = useCallback((id) => setJobIds((ids) => (ids.includes(id) ? ids : [...ids, id])), []);

  useEffect(() => {
    api("GET", "/api/state").then((s) => {
      setState(s);
      s.jobs.forEach((j) => watchJob(j.id));
    }).catch(() => {});
  }, [version]);

  const ctx = { state, version, refresh, toast, watchJob, openItem: (id, ids) => setDrawer({ id, ids }) };
  const [page, arg] = route.parts;
  useEffect(() => setDrawer(null), [page, arg]);

  return html`
    <${Ctx.Provider} value=${ctx}>
      <${TopBar} page=${page || "home"} state=${state} />
      <main>
        ${page === "batch" ? html`<${BatchView} id=${Number(arg)} key=${arg} />`
          : page === "inventory" ? html`<${Inventory} />`
          : page === "settings" ? html`<${Settings} query=${route.query} />`
          : html`<${Home} />`}
      </main>
      ${drawer ? html`<${ItemDrawer} id=${drawer.id} ids=${drawer.ids}
        onNavigate=${(id) => setDrawer((d) => ({ ...d, id }))} onClose=${() => setDrawer(null)} />` : null}
      <div class="dock">
        ${jobIds.map((id) => html`<${JobCard} key=${id} id=${id} onDone=${() => { refresh(); }}
          onDismiss=${() => setJobIds((ids) => ids.filter((x) => x !== id))} />`)}
        ${toasts.map((t) => html`<div class=${"toast" + (t.bad ? " bad" : "")} key=${t.id}>${t.msg}</div>`)}
      </div>
    <//>`;
}

function TopBar({ page, state }) {
  const aiOk = !!state?.ai_backend;
  const eb = state?.ebay || {};
  const comps = state?.comps || {};
  return html`
    <header class="topbar">
      <a class="brand" href="#/"><span class="brand-mark">L</span> Lister</a>
      <nav class="nav">
        <a href="#/" class=${page === "home" || page === "batch" ? "active" : ""}>Batches</a>
        <a href="#/inventory" class=${page === "inventory" ? "active" : ""}>Inventory</a>
        <a href="#/settings" class=${page === "settings" ? "active" : ""}>Settings</a>
      </nav>
      <div class="spacer"></div>
      <div class="health">
        <a href="#/settings" title="AI identification backend"><span class=${"dot " + (aiOk ? "good" : "bad")}></span>AI ${state?.ai_backend === "api" ? "API" : state?.ai_backend === "cli" ? "CLI" : "off"}</a>
        <a href="#/settings" title=${comps.message || ""}><span class=${"dot " + (comps.ok ? (comps.hours_left < 3 ? "warn" : "good") : "bad")}></span>Comps ${comps.ok ? `${Math.round(comps.hours_left)}h` : "expired"}</a>
        <a href="#/settings" title="eBay Sell API"><span class=${"dot " + (eb.connected ? "good" : eb.app_keys ? "warn" : "bad")}></span>eBay ${eb.connected ? "connected" : "CSV mode"}</a>
      </div>
    </header>`;
}

function JobCard({ id, onDone, onDismiss }) {
  const [job, setJob] = useState(null);
  const logRef = useRef();
  useEffect(() => {
    let live = true, timer;
    const poll = async () => {
      const j = await api("GET", `/api/jobs/${id}`).catch(() => null);
      if (!live || !j) return;
      setJob(j);
      if (j.status === "running") timer = setTimeout(poll, 1000);
      else { onDone(); if (j.status === "done" && !/✗/.test(j.log)) timer = setTimeout(onDismiss, 6000); }
    };
    poll();
    return () => { live = false; clearTimeout(timer); };
  }, [id]);
  useEffect(() => { if (logRef.current) logRef.current.scrollTop = 1e9; }, [job?.log]);
  if (!job) return null;
  const pct = job.total ? Math.round((100 * job.done) / job.total) : 0;
  const errors = (job.log.match(/✗/g) || []).length;
  return html`
    <div class="job">
      <div class="row">
        <b style="font-size:13px">${job.label}</b><div class="spacer"></div>
        ${job.status === "running" ? html`<span class="faint" style="font-size:12px">${job.done}/${job.total || "?"}</span>`
          : html`<span class=${"pill " + (job.status === "error" || errors ? "warn" : "good")}>${job.status === "error" ? "failed" : errors ? `${errors} problem${errors > 1 ? "s" : ""}` : "done"}</span>`}
        <button class="sm ghost" onClick=${onDismiss}>✕</button>
      </div>
      ${job.status === "running" ? html`<div class=${"bar" + (job.total ? "" : " indeterminate")}><div style=${`width:${pct}%`}></div></div>` : null}
      <div class="log" ref=${logRef}>${job.log.trim().split("\n").slice(-40).join("\n")}</div>
    </div>`;
}

// ------------------------------------------------------------------ home: batches

function Home() {
  const app = useApp();
  const [data] = useFetch("/api/batches");
  const [over, setOver] = useState(false);
  const [uploading, setUploading] = useState("");
  const fileRef = useRef();

  const newBatchWith = async (files) => {
    if (!files?.length) return;
    const b = await api("POST", "/api/batches", {});
    setUploading(`Uploading 0/${files.length}…`);
    const r = await uploadFiles(b.id, files, (d, t) => setUploading(`Uploading ${d}/${t}…`));
    setUploading("");
    if (r.errors.length) app.toast(`${r.errors.length} file(s) failed: ${r.errors[0]}`, true);
    location.hash = `#/batch/${b.id}`;
  };

  return html`
    <div class="hero">
      <div class=${"drop" + (over ? " over" : "")}
        onDragOver=${(e) => { e.preventDefault(); setOver(true); }} onDragLeave=${() => setOver(false)}
        onDrop=${(e) => { e.preventDefault(); setOver(false); newBatchWith(e.dataTransfer.files); }}>
        <div style="font-size:34px">📸</div>
        <div class="big">${uploading || "Drop a pile of photos to start a batch"}</div>
        <div class="muted">HEIC is fine. Don't sort them — AI groups photos into items, identifies, and prices them.</div>
        <input type="file" multiple accept="image/*,.heic,.heif" class="hidden" ref=${fileRef} onChange=${(e) => newBatchWith(e.target.files)} />
        <button class="primary lg" onClick=${() => fileRef.current.click()} disabled=${!!uploading}>Choose photos</button>
      </div>
      <${FolderImport} />
    </div>

    <div class="row" style="margin-bottom:12px"><h2>Batches</h2></div>
    ${!data ? html`<div class="faint">Loading…</div>` : html`
      <div class="batch-list">
        ${data.batches.map((b) => html`<${BatchCard} b=${b} />`)}
        ${data.legacy_items ? html`
          <a class="card batch-card" href="#/inventory">
            <b>Earlier items</b>
            <div class="muted" style="font-size:12.5px">${data.legacy_items} items from before batches existed</div>
            <div class="row"><span class="pill">open in Inventory →</span></div>
          </a>` : null}
      </div>
      ${!data.batches.length && !data.legacy_items ? html`<div class="empty"><div class="big">No batches yet</div>Drop photos above to make one.</div>` : null}`}`;
}

function BatchCard({ b }) {
  const [data] = useFetch(`/api/items?batch=${b.id}`);
  const covers = (data?.items || []).filter((i) => i.images?.length).slice(0, 5);
  return html`
    <a class="card batch-card" href=${`#/batch/${b.id}`}>
      <div class="row"><b>${b.name}</b><div class="spacer"></div><span class="faint" style="font-size:12px">${timeAgo(b.created_at)}</span></div>
      <div class="batch-covers">${covers.map((i) => html`<img src=${i.images[0].thumb_url} loading="lazy" />`)}</div>
      <div class="row wrap">
        <span class="pill">${b.items} items</span>
        ${b.loose ? html`<span class="pill warn">${b.loose} loose photos</span>` : null}
        ${b.listed ? html`<span class="pill good">${b.listed} listed</span>` : null}
      </div>
    </a>`;
}

function FolderImport({ batchId, compact }) {
  const app = useApp();
  const [path, setPath] = useState("");
  const [preview, setPreview] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => { if (app.state && !path) setPath(app.state.import_dir); }, [app.state]);
  useEffect(() => {
    if (!path) return;
    const t = setTimeout(() => api("GET", `/api/folder-preview?path=${encodeURIComponent(path)}`)
      .then((p) => { setPreview(p); setErr(""); }).catch((e) => { setPreview(null); setErr(e.message); }), 300);
    return () => clearTimeout(t);
  }, [path]);

  const go = async () => {
    let id = batchId;
    if (!id) id = (await api("POST", "/api/batches", { name: `Batch ${new Date().toLocaleDateString(undefined, { month: "short", day: "numeric" })}` })).id;
    const r = await api("POST", `/api/batches/${id}/import-folder`, { path });
    app.watchJob(r.job);
    if (!batchId) location.hash = `#/batch/${id}`;
  };

  return html`
    <div class=${compact ? "row" : "card folder-import"}>
      ${compact ? null : html`<h2>Or import a folder</h2><div class="muted" style="font-size:13px">AirDropped photos land in Downloads. Already-imported files are skipped.</div>`}
      <input value=${path} onInput=${(e) => setPath(e.target.value)} placeholder="~/Downloads/ebay" />
      ${compact ? null : html`<div style="font-size:12.5px" class=${err ? "" : "muted"}>
        ${err ? html`<span style="color:var(--bad)">${err}</span>` : preview ? preview.count ? `${preview.count} photos · ${preview.first} → ${preview.last}` : "No photos in this folder" : " "}</div>`}
      <button class=${compact ? "" : "primary"} disabled=${!preview?.count} onClick=${go}>Import ${preview?.count || ""} photos</button>
    </div>`;
}

// ------------------------------------------------------------------ batch view

function BatchView({ id }) {
  const app = useApp();
  const [data, err] = useFetch(`/api/batches/${id}`);
  const [sel, setSel] = useState([]); // loose photo ids in click order
  const [itemSel, setItemSel] = useState([]);
  const [over, setOver] = useState(false);
  const [uploading, setUploading] = useState("");
  const [every, setEvery] = useState(2);
  const [focus, setFocus] = useState(-1);

  const items = data?.items || [];
  const loose = data?.loose || [];
  const needId = items.filter((i) => !isIdentified(i) || !i.ai?.confidence);
  const ready = items.filter((i) => isIdentified(i) && !i.issues.length && i.status === "draft");
  const listed = items.filter((i) => i.status === "listed");
  const needsWork = items.filter((i) => isIdentified(i) && i.issues.length && i.status === "draft");

  useKey((e, typing) => {
    if (typing || !items.length) return;
    if (e.key === "j" || e.key === "ArrowRight") { e.preventDefault(); setFocus((f) => Math.min(f + 1, items.length - 1)); }
    else if (e.key === "k" || e.key === "ArrowLeft") { e.preventDefault(); setFocus((f) => Math.max(f - 1, 0)); }
    else if (e.key === "x" && focus >= 0) toggleItem(items[focus].id);
    else if (e.key === "Enter" && focus >= 0) app.openItem(items[focus].id, items.map((i) => i.id));
  }, [items, focus]);

  if (err) return html`<div class="empty"><div class="big">${err}</div><a href="#/">Back to batches</a></div>`;
  if (!data) return html`<div class="faint">Loading…</div>`;

  const upload = async (files) => {
    setUploading(`Uploading 0/${files.length}…`);
    const r = await uploadFiles(id, files, (d, t) => setUploading(`Uploading ${d}/${t}…`));
    setUploading("");
    if (r.errors.length) app.toast(`${r.errors.length} failed: ${r.errors[0]}`, true);
    app.refresh();
  };
  const startJob = async (url, body) => {
    try { const r = await api("POST", url, body); if (r.job) app.watchJob(r.job); else app.refresh(); return r; }
    catch (e) { app.toast(e.message, true); }
  };
  const toggle = (pid) => setSel((s) => (s.includes(pid) ? s.filter((x) => x !== pid) : [...s, pid]));
  const toggleItem = (iid) => setItemSel((s) => (s.includes(iid) ? s.filter((x) => x !== iid) : [...s, iid]));
  const bulk = async (action, extra = {}) => {
    try {
      const r = await api("POST", "/api/items/bulk", { ids: itemSel, action, ...extra });
      if (r.job) app.watchJob(r.job);
      if (r.file) {
        if (r.count) window.location = `/api/exports/${r.file}`;
        app.toast(`CSV: ${r.count} rows${r.skipped.length ? `, ${r.skipped.length} skipped (not ready)` : ""}`, !!r.skipped.length);
      }
      if (["delete", "merge"].includes(action)) setItemSel([]);
      app.refresh();
    } catch (e) { app.toast(e.message, true); }
  };
  const rename = async () => {
    const name = prompt("Batch name", data.batch.name);
    if (name) { await api("PATCH", `/api/batches/${id}`, { name }); app.refresh(); }
  };
  const removeBatch = async () => {
    if (!confirm("Delete this batch's unlisted items and loose photos? Photo files stay on disk.")) return;
    await api("DELETE", `/api/batches/${id}`);
    location.hash = "#/";
  };

  const step = loose.length ? 2 : needId.length ? 3 : ready.length || needsWork.length ? 4 : items.length ? 5 : 1;
  const connected = app.state?.ebay?.connected;

  return html`
    <div class="row">
      <a href="#/" class="muted">Batches</a><span class="faint">/</span>
      <h1 style="cursor:pointer" onClick=${rename} title="Rename">${data.batch.name}</h1>
      <div class="spacer"></div>
      <button class="sm ghost danger" onClick=${removeBatch}>Delete batch</button>
    </div>

    <div class="steps">
      ${[
        ["Photos", `${loose.length + items.reduce((n, i) => n + i.images.length, 0)} photos`],
        ["Group", loose.length ? `${loose.length} loose` : `${items.length} items`],
        ["Identify + price", needId.length ? `${needId.length} to do` : items.length ? "done" : "—"],
        ["Review", needsWork.length ? `${needsWork.length} need a fix` : ready.length ? `${ready.length} ready` : "—"],
        ["Publish", listed.length ? `${listed.length} live` : "—"],
      ].map(([t, s], i) => html`
        <div class=${"step" + (i + 1 < step ? " done" : i + 1 === step ? " current" : "")}>
          <span class="n">${i + 1 < step ? "✓" : i + 1}</span><div style="min-width:0"><div class="t">${t}</div><div class="s">${s}</div></div>
        </div>`)}
    </div>

    <div class=${"drop compact" + (over ? " over" : "")} style="margin-bottom:18px"
      onDragOver=${(e) => { e.preventDefault(); setOver(true); }} onDragLeave=${() => setOver(false)}
      onDrop=${(e) => { e.preventDefault(); setOver(false); upload(e.dataTransfer.files); }}>
      <span class="muted">${uploading || "Drop more photos here, or"}</span>
      <label class="btn" style="cursor:pointer">Choose files<input type="file" multiple accept="image/*,.heic,.heif" class="hidden" onChange=${(e) => upload(e.target.files)} /></label>
      <span class="faint">or</span>
      <div style="width:380px"><${FolderImport} batchId=${id} compact /></div>
    </div>

    ${loose.length ? html`
      <div class="card section" style="margin-bottom:22px">
        <div class="row wrap" style="margin-bottom:12px">
          <h2>${loose.length} loose photos</h2>
          <span class="muted" style="font-size:13px">in shoot order · click photos (cover first) to hand-group</span>
          <div class="spacer"></div>
          ${sel.length ? html`
            <button onClick=${async () => { await startJob(`/api/batches/${id}/group`, { photo_ids: sel }); setSel([]); }}>Make item from ${sel.length}</button>
            <button class="ghost" onClick=${() => setSel([])}>Clear</button>` : html`
            <span class="row" style="gap:4px"><button onClick=${() => startJob(`/api/batches/${id}/autogroup`, { mode: "every", n: every })}>Every</button>
              <input type="number" min="1" max="12" value=${every} onInput=${(e) => setEvery(e.target.value)} style="width:54px" /></span>
            <button onClick=${() => startJob(`/api/batches/${id}/autogroup`, { mode: "time", gap: 20 })} title="New item whenever there's a 20s+ gap between shots">By time gap</button>
            <button class="primary" onClick=${() => startJob(`/api/batches/${id}/autogroup`, { mode: "ai" })}>✨ AI group into items</button>`}
        </div>
        <div class="photo-grid">
          ${loose.map((p, i) => {
            const gap = i && p.taken_at && loose[i - 1].taken_at ? Math.round((new Date(p.taken_at) - new Date(loose[i - 1].taken_at)) / 1000) : null;
            return html`<div class=${"ph" + (sel.includes(p.id) ? " sel" : "")} onClick=${() => toggle(p.id)} title=${p.file_name}>
              <img src=${p.thumb_url} loading="lazy" />
              ${sel.includes(p.id) ? html`<span class="ord">${sel.indexOf(p.id) + 1}</span>` : null}
              ${gap !== null && gap > 20 ? html`<span class="gap">+${gap}s</span>` : null}
            </div>`;
          })}
        </div>
      </div>` : null}

    ${items.length ? html`
      <div class="row wrap" style="margin-bottom:12px">
        <h2>${items.length} items</h2>
        <div class="spacer"></div>
        ${needId.length ? html`<button class="primary" onClick=${() => startJob(`/api/batches/${id}/process`, {})}>✨ Identify + price ${needId.length} item${needId.length > 1 ? "s" : ""}</button>` : null}
        ${ready.length ? connected
          ? html`<button class=${needId.length ? "" : "primary"} onClick=${() => startJob("/api/items/bulk", { ids: ready.map((i) => i.id), action: "publish" })}>Publish ${ready.length} ready to eBay</button>`
          : html`<button class=${needId.length ? "" : "primary"} onClick=${async () => {
              const r = await api("POST", "/api/items/bulk", { ids: ready.map((i) => i.id), action: "csv" });
              if (r.count) window.location = `/api/exports/${r.file}`;
              app.toast(`CSV with ${r.count} items saved to output/${r.file}`); app.refresh();
            }}>Export ${ready.length} ready as CSV</button>` : null}
      </div>
      ${itemSel.length ? html`
        <div class="bulkbar">
          <b>${itemSel.length} selected</b>
          <button class="sm" onClick=${() => bulk("process")}>Identify + price</button>
          <button class="sm" onClick=${() => bulk("price")}>Re-price</button>
          ${connected ? html`<button class="sm" onClick=${() => bulk("publish")}>Publish</button>` : null}
          <button class="sm" onClick=${() => bulk("csv")}>CSV</button>
          <select style="width:auto;min-height:26px;height:26px;font-size:12px" onChange=${(e) => { if (e.target.value) bulk("set", { fields: { package: e.target.value } }); e.target.value = ""; }}>
            <option value="">Ships as…</option>${Object.entries(PACKAGE_LABELS).map(([k, v]) => html`<option value=${k}>${v}</option>`)}
          </select>
          ${itemSel.length > 1 ? html`<button class="sm" onClick=${() => confirm(`Merge ${itemSel.length} items into one?`) && bulk("merge")}>Merge</button>` : null}
          <button class="sm danger" onClick=${() => confirm(`Delete ${itemSel.length} items? Photos go back to loose.`) && bulk("delete")}>Delete</button>
          <div class="spacer"></div>
          <button class="sm ghost" onClick=${() => setItemSel(items.map((i) => i.id))}>All</button>
          <button class="sm ghost" onClick=${() => setItemSel([])}>None</button>
        </div>` : null}
      <div class="item-grid">
        ${items.map((it, i) => html`<${ItemCard} key=${it.id} item=${it} focused=${i === focus} selected=${itemSel.includes(it.id)}
          onToggle=${() => toggleItem(it.id)} onOpen=${() => { setFocus(i); app.openItem(it.id, items.map((x) => x.id)); }} />`)}
      </div>
      <div class="faint" style="margin-top:14px;font-size:12px">Keys: <span class="kbd">j</span>/<span class="kbd">k</span> move · <span class="kbd">x</span> select · <span class="kbd">Enter</span> open · in the editor <span class="kbd">⌘S</span> save, <span class="kbd">j</span>/<span class="kbd">k</span> next/prev</div>
    ` : !loose.length ? html`<div class="empty"><div class="big">Empty batch</div>Add photos above.</div>` : null}`;
}

function ItemCard({ item, focused, selected, onToggle, onOpen }) {
  const cover = item.images?.[0];
  const identified = isIdentified(item);
  const conf = item.ai?.confidence;
  return html`
    <div class=${"card item-card" + (focused ? " focus" : "") + (selected ? " sel" : "")} onClick=${onOpen}>
      <div class="cover">
        ${cover ? html`<img src=${cover.thumb_url} loading="lazy" />` : null}
        <span class="n-photos">${item.images?.length || 0} 📷</span>
        <label class="check" onClick=${(e) => e.stopPropagation()}><input type="checkbox" checked=${selected} onChange=${onToggle} /></label>
        <span class="corner">
          ${item.status !== "draft" ? html`<span class=${"pill " + STATUS[item.status]?.[1]}>${STATUS[item.status]?.[0]}</span>`
            : conf === "low" ? html`<span class="pill warn">low confidence</span>` : null}
        </span>
      </div>
      <div class="body">
        <div class=${"title" + (identified ? "" : " placeholder")}>${itemLabel(item)}</div>
        <div class="meta">
          <span class="price">${money(item.price)}</span>
          ${item.comps?.n ? html`<span class="faint">· ${item.comps.n} comps</span>` : null}
          <div class="spacer"></div>
          <span class="faint mono" style="font-size:11px">${item.sku.replace("LOCAL-ITEM_", "#")}</span>
        </div>
        ${identified && item.issues.length && item.status === "draft" ? html`<div class="issues">${item.issues.slice(0, 3).map((x) => html`<span class="pill bad">${x}</span>`)}${item.issues.length > 3 ? html`<span class="pill bad">+${item.issues.length - 3}</span>` : null}</div>`
          : identified && item.status === "draft" ? html`<div class="issues"><span class="pill good">✓ ready</span></div>` : null}
      </div>
    </div>`;
}

// ------------------------------------------------------------------ inventory

function Inventory() {
  const app = useApp();
  const [data] = useFetch("/api/items");
  const [tab, setTab] = useState("all");
  const [q, setQ] = useState("");
  const [sel, setSel] = useState([]);
  const [focus, setFocus] = useState(-1);
  const searchRef = useRef();

  const all = data?.items || [];
  const TABS = {
    all: ["All", () => true],
    work: ["Needs work", (i) => i.status === "draft" && (!isIdentified(i) || i.issues.length)],
    ready: ["Ready", (i) => i.status === "draft" && isIdentified(i) && !i.issues.length],
    exported: ["Exported", (i) => i.status === "exported"],
    listed: ["Listed", (i) => i.status === "listed"],
    ended: ["Ended / sold", (i) => i.status === "ended" || i.status === "sold"],
  };
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  const rows = useMemo(() => all.filter(TABS[tab][1]).filter((i) => {
    const hay = `${i.title} ${i.sku} ${i.category_name} ${i.brand}`.toLowerCase();
    return words.every((w) => hay.includes(w));
  }), [all, tab, q]);

  useKey((e, typing) => {
    if (e.key === "/" && !typing) { e.preventDefault(); searchRef.current?.focus(); return; }
    if (typing || !rows.length) return;
    if (e.key === "j" || e.key === "ArrowDown") { e.preventDefault(); setFocus((f) => Math.min(f + 1, rows.length - 1)); }
    else if (e.key === "k" || e.key === "ArrowUp") { e.preventDefault(); setFocus((f) => Math.max(f - 1, 0)); }
    else if (e.key === "x" && focus >= 0) toggle(rows[focus].id);
    else if (e.key === "Enter" && focus >= 0) app.openItem(rows[focus].id, rows.map((r) => r.id));
  }, [rows, focus]);

  const toggle = (id) => setSel((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  const bulk = async (action, extra = {}) => {
    try {
      const r = await api("POST", "/api/items/bulk", { ids: sel, action, ...extra });
      if (r.job) app.watchJob(r.job);
      if (r.file) {
        if (r.count) window.location = `/api/exports/${r.file}`;
        app.toast(`CSV: ${r.count} rows${r.skipped?.length ? `, ${r.skipped.length} skipped (not ready)` : ""}`, !!r.skipped?.length);
      }
      if (action === "delete") setSel([]);
      app.refresh();
    } catch (e) { app.toast(e.message, true); }
  };
  const inflight = (i) => i.status === "exported" || i.status === "listed";

  return html`
    <div class="row" style="margin-bottom:14px">
      <h1>Inventory</h1><div class="spacer"></div>
      <input ref=${searchRef} style="max-width:320px" placeholder="Search title, SKU, category…  ( / )" value=${q} onInput=${(e) => setQ(e.target.value)} />
    </div>
    <div class="tabs">
      ${Object.entries(TABS).map(([k, [label, fn]]) => html`
        <button class=${tab === k ? "active" : ""} onClick=${() => { setTab(k); setFocus(-1); }}>${label} <span class="count">${all.filter(fn).length}</span></button>`)}
    </div>
    ${sel.length ? html`
      <div class="bulkbar">
        <b>${sel.length} selected</b>
        <button class="sm" onClick=${() => bulk("process")}>Identify + price</button>
        <button class="sm" onClick=${() => bulk("price")}>Re-price</button>
        ${app.state?.ebay?.connected ? html`<button class="sm" onClick=${() => bulk("publish")}>Publish / update</button>` : null}
        <button class="sm" onClick=${() => bulk("csv")}>CSV (add)</button>
        <button class="sm" onClick=${() => bulk("csv", { csv_action: "Revise" })} title="File Exchange Revise rows — updates live listings matched by SKU, no duplicates">CSV (revise)</button>
        <select style="width:auto;min-height:26px;height:26px;font-size:12px" onChange=${(e) => { if (e.target.value) bulk("status", { status: e.target.value }); e.target.value = ""; }}>
          <option value="">Mark as…</option>${Object.entries(STATUS).map(([k, [l]]) => html`<option value=${k}>${l}</option>`)}
        </select>
        <button class="sm danger" onClick=${() => {
          const live = all.filter((i) => sel.includes(i.id) && inflight(i)).length;
          if (confirm(`Delete ${sel.length} items from the app?${live ? ` ${live} are exported/listed — this does NOT end them on eBay.` : ""}`)) bulk("delete");
        }}>Delete</button>
        <div class="spacer"></div>
        <button class="sm ghost" onClick=${() => setSel(rows.map((r) => r.id))}>All ${rows.length}</button>
        <button class="sm ghost" onClick=${() => setSel([])}>None</button>
      </div>` : null}
    ${!data ? html`<div class="faint">Loading…</div>` : !rows.length ? html`<div class="empty"><div class="big">Nothing here</div>${q ? "No matches." : ""}</div>` : html`
      <table class="list">
        <thead><tr>
          <th style="width:34px"><input type="checkbox" checked=${sel.length && rows.every((r) => sel.includes(r.id))}
            onChange=${(e) => setSel(e.target.checked ? rows.map((r) => r.id) : [])} /></th>
          <th style="width:56px"></th><th>Item</th><th>Status</th><th>Category</th><th class="num">Price</th><th>Ready?</th>
        </tr></thead>
        <tbody>
          ${rows.map((i, n) => html`
            <tr class=${n === focus ? "focus" : ""} onClick=${() => { setFocus(n); app.openItem(i.id, rows.map((r) => r.id)); }}>
              <td onClick=${(e) => e.stopPropagation()}><input type="checkbox" checked=${sel.includes(i.id)} onChange=${() => toggle(i.id)} /></td>
              <td>${i.images?.[0] ? html`<img class="thumb" src=${i.images[0].thumb_url} loading="lazy" />` : html`<div class="thumb"></div>`}</td>
              <td><div class="t">${itemLabel(i)}</div><div class="faint mono" style="font-size:11px">${i.sku}${i.kind === "graded" ? ` · ${i.grader} ${i.grade}` : ""}</div></td>
              <td><span class=${"pill " + (STATUS[i.status]?.[1] || "")}>${STATUS[i.status]?.[0] || i.status}</span></td>
              <td class="muted" style="font-size:12.5px;max-width:220px">${(i.category_name || i.category_id || "—").split(" › ").pop()}</td>
              <td class="num"><b>${money(i.price)}</b></td>
              <td>${i.issues.length ? html`<span class="pill bad" title=${i.issues.join(", ")}>${i.issues[0]}${i.issues.length > 1 ? ` +${i.issues.length - 1}` : ""}</span>` : html`<span class="pill good">✓</span>`}</td>
            </tr>`)}
        </tbody>
      </table>`}`;
}

// ------------------------------------------------------------------ settings

function Settings({ query }) {
  const app = useApp();
  const [s, , setS] = useFetch("/api/settings");
  const [form, setForm] = useState({});
  const [aiTest, setAiTest] = useState(null);
  const [policies, setPolicies] = useState(null);
  const [code, setCode] = useState("");

  useEffect(() => {
    if (query.get("ebay") === "connected") app.toast("eBay connected");
    if (query.get("ebay_error")) app.toast(query.get("ebay_error"), true);
  }, []);
  useEffect(() => {
    if (s?.ebay_refresh_token) api("GET", "/api/ebay/policies").then(setPolicies).catch((e) => setPolicies({ error: e.message }));
  }, [s?.ebay_refresh_token]);

  if (!s) return html`<div class="faint">Loading…</div>`;
  const val = (k) => (k in form ? form[k] : s[k]);
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const save = async (extra = {}) => {
    try {
      const out = await api("POST", "/api/settings", { ...form, ...extra });
      setS(out); setForm({}); app.toast("Settings saved"); app.refresh();
    } catch (e) { app.toast(e.message, true); }
  };
  const secret = (k, label, placeholder) => html`
    <label class="field"><span class="lbl">${label} ${s[k] ? html`<span class="pill good">saved</span>` : null}</span>
      <input type="password" placeholder=${s[k] ? "•••••••• (leave blank to keep)" : placeholder} value=${form[k] || ""} onInput=${(e) => set(k, e.target.value)} /></label>`;
  const fp = val("ebay_fulfillment_policies") || {};
  const guess = (names, pkg) => {
    const want = { card: "envelope", small: "under 1", medium: "1-5", large: "5-10" }[pkg];
    return names.find((p) => p.name.toLowerCase().includes(want))?.id || "";
  };

  return html`
    <h1 style="margin-bottom:16px">Settings</h1>
    <div class="settings">
      <div class="card">
        <h2>eBay publishing (Sell API)</h2>
        ${s.ebay_refresh_token ? html`<div class="note good">Connected. Listings publish directly and specifics come from eBay's live category rules.</div>`
          : html`<div class="note">Not connected — the app exports File Exchange CSVs instead. Connecting lets you publish in one click and pulls each category's required specifics from eBay, so uploads stop failing.</div>`}
        <ol>
          <li>Sign in at <a href="https://developer.ebay.com/my/keys" target="_blank">developer.ebay.com → Application Keys</a> and create a <b>Production</b> keyset.</li>
          <li>Click <b>User Tokens</b> next to the Production keyset (check the toggle at the top says Production). Under <b>Get a Token from eBay via Your Application</b>, click <b>Add eBay Redirect URL</b>. Fill in any privacy policy URL, and set the <i>auth accepted URL</i> to any https page (e.g. <code>https://www.ebay.com</code>). Save, then copy the <b>RuName</b> it shows (looks like <code>Name-App-PRD-xxxx-yyyy</code>).</li>
          <li>Paste the App ID, Cert ID and RuName here, save, then Connect.</li>
        </ol>
        <label class="field"><span class="lbl">App ID (Client ID)</span><input value=${val("ebay_app_id")} onInput=${(e) => set("ebay_app_id", e.target.value)} /></label>
        ${/-SBX-/.test(val("ebay_app_id") || "") ? html`<div class="note bad">This is a <b>Sandbox</b> key (<code>SBX</code>) — it can't create real listings. Use the <b>Production</b> keyset (<code>PRD</code>) App ID and Cert ID instead.</div>` : null}
        ${secret("ebay_cert_id", "Cert ID (Client Secret)", "PRD-…")}
        <label class="field"><span class="lbl">RuName (redirect URL name)</span><input value=${val("ebay_ru_name")} onInput=${(e) => set("ebay_ru_name", e.target.value)} /></label>
        <div class="row">
          <button onClick=${() => save()} disabled=${!Object.keys(form).length}>Save keys</button>
          <div class="spacer"></div>
          ${s.ebay_refresh_token ? html`<button class="ghost danger" onClick=${async () => { await api("POST", "/api/ebay/disconnect"); setS({ ...s, ebay_refresh_token: false }); app.refresh(); }}>Disconnect</button>`
            : html`<a class=${"btn" + (s.ebay_app_id && s.ebay_cert_id && s.ebay_ru_name ? "" : " hidden")} href="/api/ebay/connect" style="background:var(--accent);color:#fff;border-color:var(--accent)">Connect eBay account →</a>`}
        </div>
        ${s.ebay_app_id ? html`<div class="row">
          <input placeholder="After Connect, paste the full URL you land on (it contains ?code=…)" value=${code} onInput=${(e) => setCode(e.target.value)} />
          <button disabled=${!code} onClick=${async () => {
            try { await api("POST", "/api/ebay/code", { code }); app.toast("eBay connected"); setCode(""); setS({ ...s, ebay_refresh_token: true }); app.refresh(); }
            catch (e) { app.toast(e.message, true); }
          }}>Connect</button></div>` : null}

        ${s.ebay_refresh_token ? html`
          <h3 style="margin-top:8px">Business policies</h3>
          ${!policies ? html`<div class="faint">Loading policies…</div>` : policies.error ? html`<div class="note bad">${policies.error}</div>` : html`
            <div class="field-row">
              <label class="field"><span class="lbl">Payment</span>
                <select value=${val("ebay_payment_policy_id")} onChange=${(e) => set("ebay_payment_policy_id", e.target.value)}>
                  <option value="">Choose…</option>${policies.payment.map((p) => html`<option value=${p.id}>${p.name}</option>`)}</select></label>
              <label class="field"><span class="lbl">Returns</span>
                <select value=${val("ebay_return_policy_id")} onChange=${(e) => set("ebay_return_policy_id", e.target.value)}>
                  <option value="">Choose…</option>${policies.return.map((p) => html`<option value=${p.id}>${p.name}</option>`)}</select></label>
            </div>
            <div class="muted" style="font-size:12.5px">Shipping policy per package size (the AI picks a size for each item):</div>
            <div class="field-row">
              ${Object.entries(PACKAGE_LABELS).map(([k, label]) => html`
                <label class="field"><span class="lbl">${label}</span>
                  <select value=${fp[k] || ""} onChange=${(e) => set("ebay_fulfillment_policies", { ...fp, [k]: e.target.value })}>
                    <option value="">Choose…</option>${policies.fulfillment.map((p) => html`<option value=${p.id}>${p.name}</option>`)}</select></label>`)}
            </div>
            <div class="field-row">
              <label class="field"><span class="lbl">Promoted Listings ad rate % <span class="faint">blank = off</span></span>
                <input type="number" step="0.1" min="0" max="100" value=${val("promoted_ad_rate")} onInput=${(e) => set("promoted_ad_rate", e.target.value)} placeholder="e.g. 2" /></label>
            </div>
            ${val("promoted_ad_rate") && !app.state?.ebay?.can_promote ? html`<div class="note warn">Your eBay connection predates Promoted Listings support. <a href="/api/ebay/connect">Reconnect eBay</a> (then paste the URL below) to allow ads.</div>` : null}
            <div class="row">
              <label class="row" style="font-weight:550"><input type="checkbox" checked=${val("best_offer")} onChange=${(e) => set("best_offer", e.target.checked)} /> Accept Best Offers</label>
              <div class="spacer"></div>
              ${!Object.values(fp).some(Boolean) ? html`<button class="sm" onClick=${() => set("ebay_fulfillment_policies",
                Object.fromEntries(Object.keys(PACKAGE_LABELS).map((k) => [k, guess(policies.fulfillment, k)])))}>Auto-match by name</button>` : null}
              <button class="primary" onClick=${() => save()} disabled=${!Object.keys(form).length}>Save policies</button>
            </div>`}` : null}
      </div>

      <div class="stack">
        <div class="card">
          <h2>AI identification</h2>
          <div class="muted" style="font-size:13px">Groups photos into items, then writes the title, category, condition, specifics and description for each one. The API is fastest (items run in parallel). The CLI uses your Claude subscription.</div>
          <div class="field-row">
            <label class="field"><span class="lbl">Backend</span>
              <select value=${val("ai_backend")} onChange=${(e) => set("ai_backend", e.target.value)}>
                <option value="auto">Auto (API key if set, else CLI)</option><option value="api">Anthropic API</option><option value="cli">claude CLI</option></select></label>
            <label class="field"><span class="lbl">Parallel items</span>
              <input type="number" min="1" max="12" value=${val("ai_workers")} onInput=${(e) => set("ai_workers", Number(e.target.value))} /></label>
          </div>
          ${secret("anthropic_api_key", "Anthropic API key (optional)", "sk-ant-…")}
          <div class="field-row">
            <label class="field"><span class="lbl">API model</span><input value=${val("ai_model")} onInput=${(e) => set("ai_model", e.target.value)} /></label>
            <label class="field"><span class="lbl">CLI model</span><input value=${val("ai_cli_model")} onInput=${(e) => set("ai_cli_model", e.target.value)} /></label>
          </div>
          <div class="row">
            <button onClick=${async () => { setAiTest({ message: "Testing… (a stale CLI login can take ~3 min to fail)" }); setAiTest(await api("POST", "/api/settings/test-ai")); }}>Test AI</button>
            ${aiTest ? html`<span class=${"pill " + (aiTest.ok ? "good" : aiTest.backend !== undefined ? "bad" : "")}>${aiTest.message}</span>` : null}
            <div class="spacer"></div>
            <button class="primary" onClick=${() => save()} disabled=${!Object.keys(form).length}>Save</button>
          </div>
          <div class="faint" style="font-size:12px">If the CLI fails with a login error, run <code>claude</code> in Terminal and type <code>/login</code>.</div>
        </div>

        <div class="card">
          <h2>Sold comps</h2>
          <div class=${"note " + (app.state?.comps?.ok ? "good" : "bad")}>${app.state?.comps?.message}</div>
          <div class="muted" style="font-size:12.5px">Optional and unofficial: suggested prices come from eBay's mobile-app search, which needs an app session token in <code>~/.ebay_mobile_auth.json</code> (see README). Without it, pricing is skipped and you set prices yourself.</div>
        </div>

        <div class="card">
          <h2>Seller</h2>
          <div class="muted" style="font-size:12.5px">Item location shown to buyers (required to publish).</div>
          <div class="field-row">
            <label class="field"><span class="lbl">City</span><input value=${val("seller_city")} onInput=${(e) => set("seller_city", e.target.value)} /></label>
            <label class="field"><span class="lbl">State</span><input value=${val("seller_state")} placeholder="UT" onInput=${(e) => set("seller_state", e.target.value)} /></label>
            <label class="field"><span class="lbl">ZIP</span><input value=${val("seller_postal_code")} onInput=${(e) => set("seller_postal_code", e.target.value)} /></label>
          </div>
          <details><summary class="muted" style="cursor:pointer;font-size:12.5px">CSV export settings (only if you upload via File Exchange)</summary>
            <div class="stack" style="margin-top:10px">
              <div class="muted" style="font-size:12px">Your eBay business-policy <b>names</b>, exactly as they appear in Seller Hub.</div>
              <div class="field-row">
                <label class="field"><span class="lbl">Payment policy name</span><input value=${val("csv_payment_profile")} onInput=${(e) => set("csv_payment_profile", e.target.value)} /></label>
                <label class="field"><span class="lbl">Return policy name</span><input value=${val("csv_return_profile")} onInput=${(e) => set("csv_return_profile", e.target.value)} /></label>
              </div>
              <div class="field-row">
                ${Object.entries(PACKAGE_LABELS).map(([k, label]) => html`
                  <label class="field"><span class="lbl">Shipping: ${label}</span>
                    <input value=${(val("csv_shipping_profiles") || {})[k] || ""} onInput=${(e) => set("csv_shipping_profiles", { ...(val("csv_shipping_profiles") || {}), [k]: e.target.value })} /></label>`)}
              </div>
            </div>
          </details>
          <div class="row"><div class="spacer"></div><button class="primary" onClick=${() => save()} disabled=${!Object.keys(form).length}>Save</button></div>
        </div>

        <div class="card">
          <h2>Photo hosting</h2>
          <div class="muted" style="font-size:12.5px">Photos are uploaded to imgbb when you publish or export. Get a free key at <a href="https://api.imgbb.com/" target="_blank">api.imgbb.com</a>.</div>
          ${secret("imgbb_api_key", "imgbb API key", "")}
          <div class="row"><div class="spacer"></div><button onClick=${() => save()} disabled=${!form.imgbb_api_key}>Save</button></div>
        </div>
      </div>
    </div>`;
}

render(html`<${App} />`, document.getElementById("app"));
