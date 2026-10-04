const $ = (s) => document.querySelector(s);
const MONETIZATION = { FLATRATE: "Subscription", FREE: "Free", ADS: "Free with ads", RENT: "Rent", BUY: "Buy" };
const COUNTRIES = ["GB", "US", "CA", "AU", "NZ", "IE", "DE", "FR", "ES", "IT", "NL", "BE", "SE", "NO", "DK", "FI", "PL", "PT", "AT", "CH", "BR", "MX", "IN", "JP", "KR", "ZA"];

let settings = null;
let providers = [];
let items = [];
let selected = new Set();
let view = { kind: "all", q: "", sort: "size", showAll: false };
let pollTimer = null;

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtSize = (b) => {
  if (!b) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(Math.floor(Math.log(b) / Math.log(1024)), u.length - 1);
  return `${(b / 1024 ** i).toFixed(i >= 3 ? 1 : 0)} ${u[i]}`;
};

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || r.statusText);
  return body;
}

function banner(msg) {
  const b = $("#banner");
  b.textContent = msg || "";
  b.classList.toggle("hidden", !msg);
}

async function saveSettings(patch) {
  settings = await api("/api/settings", { method: "PUT", body: JSON.stringify(patch) });
}

/* ---------- Settings panel ---------- */
function renderSettings() {
  $("#country").innerHTML = [...new Set([settings.country, ...COUNTRIES])]
    .map((c) => `<option ${c === settings.country ? "selected" : ""}>${c}</option>`).join("");

  $("#monetization").innerHTML = Object.entries(MONETIZATION).map(([k, v]) =>
    `<button type="button" class="chip ${settings.monetization.includes(k) ? "on" : ""}" data-m="${k}">${v}</button>`).join("");

  renderProviders();
}

function renderProviders() {
  const q = $("#provSearch").value.toLowerCase();
  const sel = new Set(settings.providers);
  const list = providers
    .filter((p) => !q || p.name.toLowerCase().includes(q))
    .sort((a, b) => (sel.has(b.id) - sel.has(a.id)));
  $("#providers").innerHTML = list.map((p) => `
    <label class="prov ${sel.has(p.id) ? "on" : ""}">
      <input type="checkbox" data-p="${p.id}" ${sel.has(p.id) ? "checked" : ""}>
      ${p.icon ? `<img src="${esc(p.icon)}" alt="" loading="lazy">` : ""}
      <span>${esc(p.name)}</span>
    </label>`).join("") || `<span class="muted">No services found.</span>`;
  $("#provCount").textContent = `(${sel.size} selected)`;
}

async function loadProviders() {
  try {
    providers = await api("/api/providers");
  } catch (e) {
    providers = [];
    banner(e.message);
  }
  renderProviders();
}

/* ---------- Results ---------- */
function matchingOffers(item) {
  const jw = item.justwatch;
  if (!jw) return [];
  const prov = new Set(settings.providers);
  const mon = new Set(settings.monetization);
  const seen = new Set();
  return jw.offers.filter((o) => {
    if (!prov.has(o.provider_id) || !mon.has(o.type) || seen.has(o.provider_id)) return false;
    seen.add(o.provider_id);
    return true;
  });
}

function visibleItems() {
  const q = view.q.toLowerCase();
  let list = items.map((i) => ({ ...i, matches: matchingOffers(i) }))
    .filter((i) => view.kind === "all" || i.kind === view.kind)
    .filter((i) => !q || i.title.toLowerCase().includes(q))
    .filter((i) => view.showAll || i.matches.length);
  const sorters = {
    size: (a, b) => b.size - a.size,
    title: (a, b) => a.title.localeCompare(b.title),
    year: (a, b) => (b.year || 0) - (a.year || 0),
  };
  return list.sort(sorters[view.sort]);
}

function renderGrid() {
  const list = visibleItems();
  $("#grid").innerHTML = list.map((i) => {
    const offers = i.matches.map((o) =>
      `<a href="${esc(o.url || i.justwatch?.url || "#")}" target="_blank" rel="noopener" title="${esc(o.provider)} · ${MONETIZATION[o.type] || o.type}">
        <img src="${esc(o.icon)}" alt="${esc(o.provider)}" loading="lazy"></a>`).join("");
    const jwLink = i.justwatch?.url ? `<a href="${esc(i.justwatch.url)}" target="_blank" rel="noopener">JustWatch</a>` : "<span>Not on JustWatch</span>";
    return `
      <div class="card ${selected.has(i.key) ? "sel" : ""} ${i.matches.length ? "" : "unavail"}" data-key="${esc(i.key)}">
        <input type="checkbox" class="cb" ${selected.has(i.key) ? "checked" : ""} tabindex="-1">
        <span class="kind">${i.kind === "movie" ? "Movie" : "Series"}</span>
        ${i.poster ? `<img class="poster" src="${esc(i.poster)}" alt="" loading="lazy">` : `<div class="poster"></div>`}
        <div class="meta">
          <div class="title">${esc(i.title)}</div>
          <div class="sub"><span>${i.year || ""} · ${fmtSize(i.size)}</span>${jwLink}</div>
          <div class="offers">${offers}</div>
        </div>
      </div>`;
  }).join("");

  const empty = $("#empty");
  if (!items.length) {
    empty.textContent = "No scan results yet. Pick your services under Providers, then click “Scan library”.";
  } else if (!settings.providers.length) {
    empty.textContent = "Select the streaming services you have under Providers to see matches.";
  } else if (!list.length) {
    empty.textContent = "Nothing in your library is available on the selected services.";
  }
  empty.classList.toggle("hidden", list.length > 0);

  // Drop selections that are no longer visible so we never delete something off-screen.
  const visibleKeys = new Set(list.map((i) => i.key));
  selected = new Set([...selected].filter((k) => visibleKeys.has(k)));
  renderSummary(list);
}

function renderSummary(list = visibleItems()) {
  const sel = list.filter((i) => selected.has(i.key));
  const total = list.reduce((s, i) => s + i.size, 0);
  const selSize = sel.reduce((s, i) => s + i.size, 0);
  $("#summary").textContent = `${list.length} shown (${fmtSize(total)})` +
    (sel.length ? ` · ${sel.length} selected (${fmtSize(selSize)})` : "");
  $("#deleteBtn").disabled = !sel.length;
  $("#selectAll").checked = list.length > 0 && sel.length === list.length;
  $("#selectAll").indeterminate = sel.length > 0 && sel.length < list.length;
}

async function loadResults() {
  const r = await api("/api/results");
  items = r.items || [];
  renderGrid();
}

/* ---------- Status / scanning ---------- */
async function refreshStatus() {
  const s = await api("/api/status");
  const scan = s.scan;
  const st = $("#status");
  if (!s.configured.length) {
    st.textContent = "Sonarr/Radarr not configured";
  } else if (scan.running) {
    st.textContent = `${scan.phase}… ${scan.total ? `${scan.done}/${scan.total}` : ""}`;
  } else if (s.last_scan) {
    st.textContent = `${s.configured.join(" + ")} · last scan ${new Date(s.last_scan * 1000).toLocaleString()} (${s.results_country})`;
  } else {
    st.textContent = `${s.configured.join(" + ")} · not scanned yet`;
  }
  $("#scanBtn").disabled = scan.running;
  $("#progress").classList.toggle("hidden", !scan.running);
  $("#progressBar").style.width = scan.total ? `${(scan.done / scan.total) * 100}%` : "5%";
  if (scan.error) banner(scan.error);
  if (s.results_country && settings && s.results_country !== settings.country && !scan.running) {
    banner(`Results are for ${s.results_country}. Re-scan to check availability in ${settings.country}.`);
  }
  return scan;
}

function poll() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    const scan = await refreshStatus();
    if (scan.running) poll();
    else loadResults();
  }, 1000);
}

/* ---------- Events ---------- */
$("#settingsBtn").onclick = () => $("#settings").classList.toggle("hidden");

$("#scanBtn").onclick = async () => {
  banner("");
  try {
    await api("/api/scan", { method: "POST" });
  } catch (e) {
    banner(e.message);
  }
  poll();
};

$("#country").onchange = async (e) => {
  await saveSettings({ country: e.target.value, providers: [] });
  renderSettings();
  await loadProviders();
  renderGrid();
  refreshStatus();
};

$("#monetization").onclick = async (e) => {
  const m = e.target.dataset.m;
  if (!m) return;
  const set = new Set(settings.monetization);
  set.has(m) ? set.delete(m) : set.add(m);
  await saveSettings({ monetization: [...set] });
  renderSettings();
  renderGrid();
};

$("#providers").onchange = async (e) => {
  const id = Number(e.target.dataset.p);
  if (!id) return;
  const set = new Set(settings.providers);
  e.target.checked ? set.add(id) : set.delete(id);
  await saveSettings({ providers: [...set] });
  renderProviders();
  renderGrid();
};

$("#provSearch").oninput = renderProviders;

$("#tabs").onclick = (e) => {
  const k = e.target.dataset.kind;
  if (!k) return;
  view.kind = k;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.kind === k));
  renderGrid();
};
$("#search").oninput = (e) => { view.q = e.target.value; renderGrid(); };
$("#sort").onchange = (e) => { view.sort = e.target.value; renderGrid(); };
$("#showAll").onchange = (e) => { view.showAll = e.target.checked; renderGrid(); };

$("#grid").onclick = (e) => {
  if (e.target.closest("a")) return;
  const card = e.target.closest(".card");
  if (!card) return;
  const k = card.dataset.key;
  selected.has(k) ? selected.delete(k) : selected.add(k);
  card.classList.toggle("sel", selected.has(k));
  card.querySelector(".cb").checked = selected.has(k);
  renderSummary();
};

$("#selectAll").onchange = (e) => {
  const list = visibleItems();
  selected = e.target.checked ? new Set(list.map((i) => i.key)) : new Set();
  renderGrid();
};

$("#deleteBtn").onclick = () => {
  const sel = items.filter((i) => selected.has(i.key));
  $("#confirmCount").textContent = `${sel.length} item${sel.length === 1 ? "" : "s"}`;
  $("#confirmSize").textContent = `${fmtSize(sel.reduce((s, i) => s + i.size, 0))} on disk. This removes them from Sonarr/Radarr.`;
  $("#confirmList").innerHTML = sel.map((i) => `<li>${esc(i.title)} ${i.year ? `(${i.year})` : ""}</li>`).join("");
  $("#confirm").showModal();
};

$("#confirm").onclose = async () => {
  if ($("#confirm").returnValue !== "ok") return;
  const btn = $("#deleteBtn");
  btn.disabled = true;
  btn.textContent = "Deleting…";
  try {
    const r = await api("/api/delete", {
      method: "POST",
      body: JSON.stringify({
        keys: [...selected],
        delete_files: $("#optFiles").checked,
        add_exclusion: $("#optExclude").checked,
      }),
    });
    r.deleted.forEach((k) => selected.delete(k));
    banner(r.failed.length ? `Failed to delete ${r.failed.length}: ${r.failed.map((f) => f.error).join("; ")}` : "");
  } catch (e) {
    banner(e.message);
  }
  btn.textContent = "Delete selected";
  await loadResults();
};

/* ---------- Init ---------- */
(async () => {
  try {
    settings = await api("/api/settings");
    renderSettings();
    if (!settings.providers.length) $("#settings").classList.remove("hidden");
    await Promise.all([loadProviders(), loadResults()]);
    const scan = await refreshStatus();
    if (scan.running) poll();
  } catch (e) {
    banner(e.message);
  }
})();
