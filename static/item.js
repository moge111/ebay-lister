import { html, useEffect, useMemo, useRef, useState } from "./vendor/preact-htm.js";
import { api, money, PACKAGE_LABELS, STATUS, useApp, useKey } from "./lib.js";

const EDITABLE = ["title", "price", "category_id", "category_name", "condition_id", "condition_descriptors",
  "aspects", "description", "brand", "package", "quantity", "notes", "cert_number", "grade", "grader", "status"];
const GRADERS = ["PSA", "BGS", "CGC", "SGC", "BVG", "BCCG"];
const GRADES = ["10", "9.5", "9", "8.5", "8", "7.5", "7", "6.5", "6", "5.5", "5", "4.5", "4", "3.5", "3", "2.5", "2", "1.5", "1"];

function pick(item) {
  const f = {};
  EDITABLE.forEach((k) => (f[k] = item[k] ?? ""));
  f.aspects = { ...(item.aspects || {}) };
  f.condition_descriptors = { ...(item.condition_descriptors || {}) };
  return f;
}

function diff(item, form) {
  const out = {};
  for (const k of EDITABLE) {
    const a = JSON.stringify(item[k] ?? (k === "aspects" || k === "condition_descriptors" ? {} : ""));
    const b = JSON.stringify(form[k] ?? "");
    if (a !== b) out[k] = form[k];
  }
  return out;
}

export function ItemDrawer({ id, ids, onNavigate, onClose }) {
  const app = useApp();
  const [item, setItem] = useState(null);
  const [form, setForm] = useState(null);
  const [meta, setMeta] = useState(null);
  const [busy, setBusy] = useState("");
  const [hint, setHint] = useState("");

  const load = async () => {
    const it = await api("GET", `/api/items/${id}`);
    setItem(it);
    setForm(pick(it));
  };
  useEffect(() => { setItem(null); setForm(null); load().catch((e) => app.toast(e.message, true)); }, [id]);

  const categoryId = form?.category_id;
  useEffect(() => {
    setMeta(null);
    if (!categoryId || !/^\d+$/.test(categoryId)) return;
    api("GET", `/api/categories/${categoryId}/meta`).then((m) => {
      setMeta(m);
      // Required specifics with a single allowed value (e.g. Funko Type = Pop!) fill themselves.
      const defaults = m.aspects.filter((d) => d.required && d.values.length === 1);
      setForm((f) => {
        const missing = defaults.filter((d) => !f.aspects[d.name]);
        return missing.length ? { ...f, aspects: { ...f.aspects, ...Object.fromEntries(missing.map((d) => [d.name, d.values[0]])) } } : f;
      });
    }).catch(() => {});
  }, [categoryId]);

  const changes = item && form ? diff(item, form) : {};
  const dirty = Object.keys(changes).length > 0;
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));

  const save = async (quiet) => {
    if (!dirty) return item;
    setBusy("save");
    try {
      const it = await api("PATCH", `/api/items/${id}`, changes);
      const full = await api("GET", `/api/items/${id}`);
      setItem(full);
      setForm(pick(full));
      if (!quiet) app.toast("Saved");
      app.refresh();
      return it;
    } catch (e) {
      app.toast(e.message, true);
      throw e;
    } finally {
      setBusy("");
    }
  };

  const close = () => {
    if (dirty && !confirm("Discard unsaved changes?")) return;
    onClose();
  };
  const go = (delta) => {
    if (!ids || ids.length < 2) return;
    if (dirty && !confirm("Discard unsaved changes?")) return;
    const i = ids.indexOf(id);
    const next = ids[(i + delta + ids.length) % ids.length];
    onNavigate(next);
  };

  useKey((e, typing) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "s") { e.preventDefault(); save(); }
    else if (e.key === "Escape") { e.preventDefault(); close(); }
    else if (!typing && (e.key === "j" || e.key === "ArrowDown")) { e.preventDefault(); go(1); }
    else if (!typing && (e.key === "k" || e.key === "ArrowUp")) { e.preventDefault(); go(-1); }
  }, [dirty, changes, id, ids]);

  const identify = async () => {
    if (dirty) await save(true);
    setBusy("identify");
    try {
      const it = await api("POST", `/api/items/${id}/identify`, { hint });
      setItem(it); setForm(pick(it)); setHint("");
      app.toast("Identified");
      app.refresh();
    } catch (e) { app.toast(e.message, true); }
    setBusy("");
  };

  const publish = async () => {
    if (dirty) await save(true);
    setBusy("publish");
    try {
      const r = await api("POST", `/api/items/${id}/publish`);
      app.toast(`Live on eBay — item ${r.listing_id}`);
      await load();
      app.refresh();
    } catch (e) { app.toast(e.message, true); await load(); }
    setBusy("");
  };

  const endListing = async () => {
    if (!confirm("End this listing on eBay?")) return;
    setBusy("end");
    try { const it = await api("POST", `/api/items/${id}/end`); setItem(it); setForm(pick(it)); app.refresh(); app.toast("Listing ended"); }
    catch (e) { app.toast(e.message, true); }
    setBusy("");
  };

  const exportCsv = async () => {
    if (dirty) await save(true);
    setBusy("csv");
    try {
      const r = await api("POST", "/api/items/bulk", { ids: [id], action: "csv" });
      if (r.count) window.location = `/api/exports/${r.file}`;
      else app.toast(`Not exported: ${r.skipped[0]?.issues.join(", ")}`, true);
      await load(); app.refresh();
    } catch (e) { app.toast(e.message, true); }
    setBusy("");
  };

  const remove = async () => {
    if (!confirm("Delete this item? (Batch photos go back to the loose pile.)")) return;
    await api("DELETE", `/api/items/${id}`);
    app.refresh();
    onClose();
  };

  if (!item || !form) {
    return html`<div class="scrim" onClick=${onClose}></div><div class="drawer"><div class="empty">Loading…</div></div>`;
  }

  const listed = item.status === "listed";
  const pos = ids ? ids.indexOf(id) + 1 : 0;
  const issues = item.issues || [];
  const ebayConnected = app.state?.ebay?.connected;

  return html`
    <div class="scrim" onClick=${close}></div>
    <div class="drawer" role="dialog">
      <div class="drawer-head">
        <span class="mono muted">${item.sku}</span>
        <span class=${"pill " + (STATUS[item.status]?.[1] || "")}>${STATUS[item.status]?.[0] || item.status}</span>
        ${item.title_locked ? html`<span class="pill" title="Title edited by hand — AI won't overwrite it">title locked</span>` : null}
        ${item.ebay_listing_id ? html`<a href=${`https://www.ebay.com/itm/${item.ebay_listing_id}`} target="_blank">View on eBay ↗</a>` : null}
        <div class="spacer"></div>
        ${ids && ids.length > 1 ? html`
          <span class="faint" style="font-size:12px">${pos} / ${ids.length}</span>
          <button class="sm ghost" onClick=${() => go(-1)} title="Previous (k)">↑</button>
          <button class="sm ghost" onClick=${() => go(1)} title="Next (j)">↓</button>` : null}
        <button class="sm ghost" onClick=${close} title="Close (Esc)">✕</button>
      </div>

      <div class="drawer-body">
        <${Gallery} item=${item} reload=${load} />
        <div class="form-col">
          ${item.publish_error ? html`<div class="note bad"><b>Last publish failed:</b> ${item.publish_error}</div>` : null}
          ${item.ai?.notes ? html`<div class="note warn"><b>AI notes:</b> ${item.ai.notes}</div>` : null}

          <div class="card">
            <label class="field">
              <span class="lbl">Title <span class=${"counter" + (form.title.length > 80 ? " over" : "")}>${form.title.length}/80</span></span>
              <input value=${form.title} onInput=${(e) => set({ title: e.target.value })} placeholder="Run AI identify, or type a title" />
            </label>
            <div class="field-row">
              <label class="field"><span class="lbl">Price</span>
                <input type="number" step="0.01" min="0" value=${form.price ?? ""} onInput=${(e) => set({ price: e.target.value })} /></label>
              <label class="field"><span class="lbl">Quantity</span>
                <input type="number" min="1" value=${form.quantity} onInput=${(e) => set({ quantity: e.target.value })} /></label>
              <label class="field"><span class="lbl">Ships as</span>
                <select value=${form.package || "small"} onChange=${(e) => set({ package: e.target.value })}>
                  ${Object.entries(PACKAGE_LABELS).map(([k, v]) => html`<option value=${k}>${v}</option>`)}
                </select></label>
            </div>
            <${Comps} item=${item} setItem=${setItem} price=${form.price} onUse=${(p) => set({ price: p })} />
          </div>

          <div class="card">
            <${CategoryPicker} form=${form} set=${set} />
            <${ConditionFields} form=${form} set=${set} meta=${meta} />
          </div>

          <div class="card">
            <${AspectsEditor} defs=${meta?.aspects} values=${form.aspects} onChange=${(aspects) => set({ aspects })} source=${meta?.source} />
          </div>

          <div class="card">
            <label class="field"><span class="lbl">Description <span class="faint">built from the details above${dirty ? " — save to refresh" : ""}</span></span>
              <div class="note" style="white-space:pre-wrap;color:var(--text)">${item.full_description}</div></label>
            <label class="field"><span class="lbl">Add condition notes / flaws <span class="faint">optional — shows up in the description, e.g. “soft bottom-left corner”</span></span>
              <textarea rows="2" value=${form.description} onInput=${(e) => set({ description: e.target.value })}></textarea></label>
            <label class="field"><span class="lbl">Private notes</span>
              <textarea rows="2" value=${form.notes} onInput=${(e) => set({ notes: e.target.value })}></textarea></label>
          </div>

          <div class="card">
            <div class="row">
              <h3>AI</h3>
              ${item.ai?.confidence ? html`<span class=${"pill " + ({ high: "good", medium: "", low: "warn" }[item.ai.confidence])}>${item.ai.confidence} confidence</span>` : null}
              <div class="spacer"></div>
              <span class="faint" style="font-size:12px">backend: ${app.state?.ai_backend || "none"}</span>
            </div>
            <div class="row">
              <input placeholder="Optional hint, e.g. “it's the Japanese version” or “PSA 10”" value=${hint} onInput=${(e) => setHint(e.target.value)} />
              <button onClick=${identify} disabled=${!!busy}>${busy === "identify" ? "Identifying…" : item.title ? "Re-identify" : "Identify"}</button>
            </div>
            ${item.title_locked ? html`<div class="faint" style="font-size:12px">Title is locked (edited by hand), so re-identify keeps it and updates everything else.</div>` : null}
          </div>
        </div>
      </div>

      <div class="drawer-foot">
        <div class="issues">${issues.length ? issues.map((i) => html`<span class="pill bad">${i}</span>`) : html`<span class="pill good">✓ ready to list</span>`}</div>
        <div class="spacer"></div>
        <button class="danger ghost" onClick=${remove}>Delete</button>
        ${listed && item.ebay_offer_id ? html`<button onClick=${endListing} disabled=${!!busy}>End listing</button>` : null}
        <button onClick=${exportCsv} disabled=${!!busy} title="File Exchange CSV for just this item">CSV</button>
        <button onClick=${() => save()} disabled=${!dirty || !!busy}>${busy === "save" ? "Saving…" : "Save"} <span class="kbd">⌘S</span></button>
        ${ebayConnected ? html`
          <button class="primary" onClick=${publish} disabled=${!!busy || issues.length > 0 || (listed && !item.ebay_offer_id)}>
            ${busy === "publish" ? "Publishing…" : listed ? "Update listing" : "Publish to eBay"}
          </button>` : html`<a class="btn" href="#/settings" title="Connect eBay to publish directly">Connect eBay to publish</a>`}
      </div>
    </div>`;
}

// ------------------------------------------------------------------ photos

function Gallery({ item, reload }) {
  const app = useApp();
  const [main, setMain] = useState(0);
  const [dragId, setDragId] = useState(null);
  const [overId, setOverId] = useState(null);
  const [picked, setPicked] = useState([]);
  const imgs = item.images || [];
  useEffect(() => { setMain(0); setPicked([]); }, [item.id]);

  const order = async (ids) => {
    await api("PUT", `/api/items/${item.id}/photos`, { photo_ids: ids });
    await reload();
    app.refresh();
  };
  const drop = (targetId) => {
    if (!dragId || dragId === targetId) return;
    const ids = imgs.map((i) => i.id).filter((i) => i !== dragId);
    ids.splice(ids.indexOf(targetId), 0, dragId);
    order(ids);
  };
  const removePhoto = async (img) => {
    if (img.batch_id) return order(imgs.map((i) => i.id).filter((i) => i !== img.id));
    if (!confirm("Remove this photo from the item?")) return;
    await api("DELETE", `/api/photos/${img.id}`);
    await reload();
  };
  const split = async () => {
    const r = await api("POST", `/api/items/${item.id}/split`, { photo_ids: picked });
    app.toast(`Split ${picked.length} photo(s) into a new item`);
    setPicked([]);
    await reload();
    app.refresh();
    return r;
  };
  const current = imgs[Math.min(main, imgs.length - 1)];

  return html`
    <div class="gallery">
      <div class="main">${current ? html`<a href=${current.url} target="_blank"><img src=${current.url} /></a>` : html`<div class="empty">No photos</div>`}</div>
      <div class="strip">
        ${imgs.map((img, i) => html`
          <div class=${"ph" + (i === main ? " sel" : "") + (dragId === img.id ? " dragging" : "") + (overId === img.id && dragId !== img.id ? " drop-before" : "")}
            draggable="true"
            onDragStart=${() => setDragId(img.id)}
            onDragEnd=${() => { setDragId(null); setOverId(null); }}
            onDragOver=${(e) => { e.preventDefault(); setOverId(img.id); }}
            onDrop=${(e) => { e.preventDefault(); drop(img.id); setOverId(null); }}
            onClick=${(e) => (e.shiftKey || e.metaKey ? setPicked((p) => p.includes(img.id) ? p.filter((x) => x !== img.id) : [...p, img.id]) : setMain(i))}>
            <img src=${img.thumb_url} loading="lazy" />
            ${i === 0 ? html`<span class="cover-tag">COVER</span>` : null}
            ${picked.includes(img.id) ? html`<span class="ord">✓</span>` : null}
            <span class="x" title=${img.batch_id ? "Send back to loose photos" : "Remove"} onClick=${(e) => { e.stopPropagation(); removePhoto(img); }}>×</span>
          </div>`)}
      </div>
      <div class="row faint" style="font-size:12px">
        <span>Drag to reorder (first = cover). ⇧-click to pick photos to split off.</span>
        <div class="spacer"></div>
        ${picked.length ? html`<button class="sm" onClick=${split}>Split ${picked.length} → new item</button>` : null}
      </div>
    </div>`;
}

// ------------------------------------------------------------------ comps

function Comps({ item, setItem, onUse, price }) {
  const app = useApp();
  const c = item.comps || {};
  const [query, setQuery] = useState(c.query || item.ai?.search_query || item.title || "");
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  useEffect(() => { setQuery((item.comps || {}).query || item.ai?.search_query || item.title || ""); }, [item.id]);

  const run = async () => {
    setBusy(true);
    try {
      const it = await api("POST", `/api/items/${item.id}/comps`, { query });
      setItem((prev) => ({ ...prev, comps: it.comps }));
      if (!price && it.comps?.suggested) onUse(it.comps.suggested);
      setOpen(true);
    } catch (e) { app.toast(e.message, true); }
    setBusy(false);
  };

  return html`
    <div class="comps">
      <div class="row">
        <input value=${query} onInput=${(e) => setQuery(e.target.value)} onKeyDown=${(e) => e.key === "Enter" && run()} placeholder="Sold-comps search" />
        <button onClick=${run} disabled=${busy || !query}>${busy ? "Searching…" : "Sold comps"}</button>
      </div>
      ${c.n ? html`
        <div class="stats">
          <div class="stat"><div class="v">${money(c.median)}</div><div class="l">median of ${c.n}${c.filtered ? "" : " (loose match)"}</div></div>
          <div class="stat"><div class="v">${money(c.low)}</div><div class="l">low</div></div>
          <div class="stat"><div class="v">${money(c.high)}</div><div class="l">high</div></div>
          ${c.suggested ? html`<div class="stat" style="cursor:pointer" onClick=${() => onUse(c.suggested)} title="Use this price">
            <div class="v" style="color:var(--accent-text)">${money(c.suggested)}</div><div class="l">use suggested →</div></div>`
            : html`<div class="stat"><div class="v faint">—</div><div class="l">no suggestion</div></div>`}
        </div>
        ${c.filtered ? null : html`<div class="note warn" style="font-size:12px">No close matches — these sold listings are other cards/products, so no price is suggested. Try a more specific search (player + card # + parallel).</div>`}
        <div class="row" style="font-size:12px"><a href="#" onClick=${(e) => { e.preventDefault(); setOpen(!open); }}>${open ? "Hide" : "Show"} sold listings</a>
          <span class="faint">for “${c.query}”</span></div>
        ${open ? html`<div class="comp-list">${(c.items || []).map((x) => html`
          <div>${x.url ? html`<a href=${x.url} target="_blank" rel="noopener">${x.title} ↗</a>` : html`<span>${x.title}</span>`}<span class="p">${money(x.price)}</span></div>`)}</div>` : null}
      ` : c.query ? html`<div class="faint" style="font-size:12px">No usable sold comps for “${c.query}”. Try a shorter search.</div>` : null}
    </div>`;
}

// ------------------------------------------------------------------ category

function CategoryPicker({ form, set }) {
  const [q, setQ] = useState(null);
  const [results, setResults] = useState([]);
  const [open, setOpen] = useState(false);
  const [on, setOn] = useState(0);
  const timer = useRef();
  const shown = q ?? (form.category_name || (form.category_id ? `Category ${form.category_id}` : ""));

  const search = (text) => {
    setQ(text);
    setOpen(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(async () => {
      const r = await api("GET", `/api/categories/suggest?q=${encodeURIComponent(text)}`).catch(() => ({ results: [] }));
      setResults(r.results || []);
      setOn(0);
    }, 220);
  };
  const choose = (r) => {
    set({ category_id: String(r.id), category_name: r.path || r.name });
    setQ(null);
    setOpen(false);
  };
  const key = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setOn((o) => Math.min(o + 1, results.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setOn((o) => Math.max(o - 1, 0)); }
    else if (e.key === "Enter") {
      e.preventDefault();
      if (/^\d+$/.test(q || "")) choose({ id: q, name: `Category ${q}` });
      else if (results[on]) choose(results[on]);
    } else if (e.key === "Escape") { e.stopPropagation(); setOpen(false); setQ(null); }
  };

  return html`
    <label class="field suggest">
      <span class="lbl">Category ${form.category_id ? html`<span class="mono faint">#${form.category_id}</span>` : html`<span style="color:var(--bad)">required</span>`}</span>
      <input value=${shown} onInput=${(e) => search(e.target.value)} onFocus=${() => search(q ?? "")} onBlur=${() => setTimeout(() => { setOpen(false); setQ(null); }, 150)}
        onKeyDown=${key} placeholder="Search categories, or type an eBay category id" />
      ${open && results.length ? html`<div class="suggest-list">
        ${results.map((r, i) => html`<div class=${i === on ? "on" : ""} onMouseDown=${() => choose(r)}>
          ${r.name} ${r.known ? html`<span class="pill good" style="margin-left:6px">used before</span>` : null}
          ${r.path && r.path !== r.name ? html`<div class="path">${r.path}</div>` : null}</div>`)}
      </div>` : null}
    </label>`;
}

// ------------------------------------------------------------------ condition

function ConditionFields({ form, set, meta }) {
  if (!form.category_id) return null;
  if (!meta) return html`<div class="faint">Loading category rules…</div>`;

  if (meta.card) {
    const graded = !!(form.grade || form.cert_number);
    const raw = (meta.conditions || []).find((c) => c.id === "4000");
    const cardDesc = raw?.descriptors?.find((d) => d.id === "40001");
    const cardValues = cardDesc?.values?.length ? cardDesc.values : [{ id: "400010", name: "Near mint or better" }];
    return html`
      <div class="field-row">
        <label class="field"><span class="lbl">Card</span>
          <select value=${graded ? "graded" : "raw"} onChange=${(e) => e.target.value === "graded"
            ? set({ grader: "PSA", grade: "10", condition_id: "2750", condition_descriptors: undefined })
            : set({ grader: "", grade: "", cert_number: "", condition_id: "4000", condition_descriptors: { 40001: "400010" } })}>
            <option value="raw">Raw (ungraded)</option><option value="graded">Graded slab</option>
          </select></label>
        ${graded ? html`
          <label class="field"><span class="lbl">Grader</span>
            <select value=${form.grader || "PSA"} onChange=${(e) => set({ grader: e.target.value, condition_descriptors: undefined })}>${GRADERS.map((g) => html`<option>${g}</option>`)}</select></label>
          <label class="field"><span class="lbl">Grade</span>
            <select value=${form.grade} onChange=${(e) => set({ grade: e.target.value, condition_descriptors: undefined })}>${GRADES.map((g) => html`<option>${g}</option>`)}</select></label>
          <label class="field"><span class="lbl">Cert #</span>
            <input value=${form.cert_number} onInput=${(e) => set({ cert_number: e.target.value, condition_descriptors: undefined })} /></label>
        ` : html`
          <label class="field"><span class="lbl">Card condition</span>
            <select value=${form.condition_descriptors?.["40001"] || "400010"}
              onChange=${(e) => set({ condition_id: "4000", condition_descriptors: { 40001: e.target.value } })}>
              ${cardValues.map((v) => html`<option value=${v.id}>${v.name}</option>`)}</select></label>`}
      </div>`;
  }

  const conditions = meta.conditions || [];
  const valid = conditions.some((c) => c.id === String(form.condition_id));
  return html`
    <label class="field"><span class="lbl">Condition ${!valid && form.condition_id ? html`<span style="color:var(--bad)">not allowed in this category</span>` : null}</span>
      <select value=${String(form.condition_id || "")} onChange=${(e) => set({ condition_id: e.target.value, condition_descriptors: {} })}>
        ${!valid ? html`<option value=${form.condition_id}>${form.condition_id ? `(${form.condition_id})` : "Choose…"}</option>` : null}
        ${conditions.map((c) => html`<option value=${c.id}>${c.name}</option>`)}
      </select></label>`;
}

// ------------------------------------------------------------------ item specifics

function AspectsEditor({ defs, values, onChange, source }) {
  const [newName, setNewName] = useState("");
  const [newVal, setNewVal] = useState("");
  defs = defs || [];
  const known = new Set(defs.map((d) => d.name));
  const main = defs.filter((d) => d.required || d.recommended || values[d.name]);
  const more = defs.filter((d) => !main.includes(d));
  const custom = Object.keys(values).filter((k) => !known.has(k));
  const setVal = (name, v) => {
    const next = { ...values };
    if (v === "" || v == null) delete next[name]; else next[name] = v;
    onChange(next);
  };
  const missing = defs.filter((d) => d.required && !values[d.name]).length;

  const input = (d, i) => html`
    <label class=${"field" + (d.required ? " req" : "") + (values[d.name] ? " ok" : "")}>
      <span class="lbl">${d.name}</span>
      <input list=${d.values?.length ? `asp-${i}` : undefined} value=${values[d.name] || ""} onInput=${(e) => setVal(d.name, e.target.value)}
        placeholder=${d.mode === "SELECTION_ONLY" ? "pick from list" : ""} />
      ${d.values?.length ? html`<datalist id=${`asp-${i}`}>${d.values.map((v) => html`<option value=${v} />`)}</datalist>` : null}
    </label>`;

  return html`
    <div class="row">
      <h3>Item specifics</h3>
      ${missing ? html`<span class="pill bad">${missing} required missing</span>` : defs.length ? html`<span class="pill good">required ✓</span>` : null}
      <div class="spacer"></div>
      <span class="faint" style="font-size:11.5px">${source === "ebay" ? "from eBay's category rules" : "built-in rules — connect eBay keys for the full list"}</span>
    </div>
    <div class="aspect-grid">${main.map(input)}</div>
    ${more.length ? html`<details><summary class="muted" style="cursor:pointer;font-size:12.5px">${more.length} more optional specifics</summary>
      <div class="aspect-grid" style="margin-top:8px">${more.map((d, i) => input(d, i + 500))}</div></details>` : null}
    ${custom.length ? html`<div class="aspect-grid">${custom.map((name) => html`
      <label class="field"><span class="lbl">${name} <a href="#" onClick=${(e) => { e.preventDefault(); setVal(name, ""); }}>remove</a></span>
        <input value=${values[name]} onInput=${(e) => setVal(name, e.target.value)} /></label>`)}</div>` : null}
    <div class="row">
      <input placeholder="Add specific (name)" value=${newName} onInput=${(e) => setNewName(e.target.value)} />
      <input placeholder="value" value=${newVal} onInput=${(e) => setNewVal(e.target.value)} />
      <button disabled=${!newName || !newVal} onClick=${() => { setVal(newName.trim(), newVal.trim()); setNewName(""); setNewVal(""); }}>Add</button>
    </div>`;
}
