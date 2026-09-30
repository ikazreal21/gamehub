/* GameHub frontend - vanilla JS SPA */
const $ = (id) => document.getElementById(id);
const api = {
  token: localStorage.getItem("gh_token") || "",
  async req(path, opts = {}) {
    const r = await fetch(path, {
      ...opts,
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + this.token, ...(opts.headers || {}) },
    });
    if (r.status === 401) { this.logout(); throw new Error("unauthorized"); }
    const t = await r.text();
    // Cloudflare challenge / WAF block returns HTML instead of JSON - surface friendly hint
    if (t.trimStart().startsWith("<!DOCTYPE") || t.trimStart().startsWith("<html")) {
      throw new Error(`Blocked by Cloudflare (HTTP ${r.status}). Fix: Cloudflare dashboard → turn OFF Bot Fight Mode, add WAF Skip for /api/*, set Cache Rule Bypass for /api/*, keep WebSockets ON. Then retry.`);
    }
    let j; try { j = JSON.parse(t); } catch { j = { raw: t }; }
    if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail.slice(0, 300) : t.slice(0, 300));
    return j;
  },
  logout() { this.token = ""; localStorage.removeItem("gh_token"); location.reload(); },
};

let servers = [], templates = [], current = null, ws = null, hist = { cpu: [], mem: [] };

// login (button click or Enter key)
async function doLogin() {
  $("login-err").textContent = "";
  try {
    const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: $("login-user").value, password: $("login-pass").value }) });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || "login failed");
    api.token = j.access_token; localStorage.setItem("gh_token", api.token);
    boot();
  } catch (e) { $("login-err").textContent = String(e.message || e); }
}
$("login-btn").onclick = doLogin;
[$("login-user"), $("login-pass")].forEach(el => el?.addEventListener("keydown", e => {
  if (e.key === "Enter") doLogin();
}));
$("logout").onclick = () => api.logout();

async function boot() {
  if (!api.token) return;
  $("login-view").classList.add("hidden"); $("app").classList.remove("hidden");
  await Promise.all([loadTemplates(), loadServers(), loadSystem()]);
  setInterval(loadSystem, 15000);
  setInterval(() => { if (current) refreshStats(); }, 5000);
}

async function loadTemplates() {
  templates = await api.req("/api/templates");
  $("template-box").innerHTML = templates.map(t => `• <b>${t.name}</b> <span class="muted">${t.id}</span>`).join("<br>");
  $("ns-template").innerHTML = templates.map(t => `<option value="${t.id}">${t.name}</option>`).join("");
  updateNsDesc();
}
let nsSuggested = "";
function updateNsDesc(fillName) {
  const t = templates.find(x => x.id === $("ns-template").value);
  if (!t) return;
  $("ns-desc").textContent = t.description + " | image: " + (t.image || "(you supply)");
  $("ns-image").placeholder = t.image || "e.g. nginx:latest";
  // preset name suggestion from template (e.g. palworld-1); never clobber a custom name
  if (fillName && ($("ns-name").value.trim() === "" || $("ns-name").value.trim() === nsSuggested)) {
    nsSuggested = `${t.id}-1`;
    $("ns-name").value = nsSuggested;
  }
  $("ns-name").placeholder = `${t.id}-1`;
}
$("ns-template").onchange = () => updateNsDesc(true);

async function loadServers() {
  servers = await api.req("/api/servers");
  const box = $("server-list"); box.innerHTML = "";
  servers.forEach(s => {
    const b = document.createElement("button");
    b.className = "srv" + (current === s.id ? " active" : "");
    b.innerHTML = `<b>${s.name}${s.hidden ? " 🚫" : ""}</b><span class="muted small">${s.game_name}${s.hidden ? " · hidden" : ""}</span><br><span class="st ${s.state}">${s.state}</span>`;
    b.onclick = () => selectServer(s.id);
    box.appendChild(b);
  });
  if (current) { const s = servers.find(x => x.id === current); if (s) paintHead(s); }
}

async function loadSystem() {
  try {
    const s = await api.req("/api/system");
    $("system-box").innerHTML = `CPU ${s.cpu_percent}%<br>RAM ${s.mem_used_mb}/${s.mem_total_mb} MB (${s.mem_percent}%)`;
  } catch {}
}

function paintHead(s) {
  $("empty-state").classList.add("hidden"); $("detail").classList.remove("hidden");
  $("d-name").textContent = `${s.name}`;
  $("d-meta").textContent = ` ${s.game_name} · ${s.image} · ${s.state}`;
  updateActionButtons(s.state);
}

// State-aware action buttons: running -> can't Start/Delete (must Stop first).
// Stopped/exited -> can't Stop (must Start first). Delete requires stopped.
function updateActionButtons(state) {
  const running = state === "running";
  const btns = {};
  document.querySelectorAll("#detail-head [data-act]").forEach(b => btns[b.dataset.act] = b);
  if (btns.start) { btns.start.disabled = running; btns.start.title = running ? "Server is already running" : "Start server"; }
  if (btns.stop) { btns.stop.disabled = !running; btns.stop.title = running ? "Stop server" : "Server is not running"; }
  if (btns.restart) { btns.restart.disabled = !running; btns.restart.title = running ? "Restart server" : "Start server instead (not running)"; }
  if (btns.update) { btns.update.disabled = false; btns.update.title = "Pull latest image + recreate (game update)"; }
  if (btns.delete) { btns.delete.disabled = running; btns.delete.title = running ? "Stop server before deleting" : "Delete server container"; }
}

async function selectServer(id) {
  current = id;
  lastState = "";
  await loadServers();
  closeWs();
  stopLogPoll();
  hist = { cpu: [], mem: [] };
  // always open on Console tab with live logs on
  document.querySelectorAll(".tabs button").forEach(x => x.classList.toggle("active", x.dataset.tab === "console"));
  document.querySelectorAll(".tab").forEach(t => t.classList.add("hidden"));
  $("tab-console").classList.remove("hidden");
  $("logs-live").checked = true;
  await Promise.all([refreshStats(), loadLogs(), loadPlayers(), loadConfigList().then(loadConfig), loadEnv(), loadBuildBox(), loadMods(), loadBackups(), loadSettings(), loadQuickCmds()]);
  openWs();
  startLogPoll();
}

let lastState = "";
async function reliveLogs(reason) {
  if (!current) return;
  if (reason) $("logs").textContent += `\n--- ${reason} ---\n`;
  try { await loadLogs(); } catch {}
  closeWs(); openWs(); stopLogPoll(); startLogPoll();
}

document.querySelectorAll("#detail-head [data-act]").forEach(b => b.onclick = async () => {
  if (!current) return;
  const act = b.dataset.act;
  if (act === "delete" && !confirm("Delete server container? (volumes kept unless you remove them manually)")) return;
  try {
    if (act === "delete") {
      await api.req(`/api/servers/${current}`, { method: "DELETE" });
      current = null; lastState = "";
      closeWs(); stopLogPoll();
      $("detail").classList.add("hidden"); $("empty-state").classList.remove("hidden");
      await loadServers();
    } else if (act === "update") {
      const btn = document.querySelector('#detail-head [data-act="update"]');
      if (btn) { btn.disabled = true; btn.textContent = "⏳ Updating…"; }
      $("cmd-out").textContent = "Update started — pulling image (minutes)…";
      try {
        await api.req(`/api/servers/${current}/update`, { method: "POST" });
      } catch (e) {
        if (!String(e.message).includes("already in progress")) throw e;
      }
      // poll job until done/error
      for (let i = 0; i < 120; i++) {
        await new Promise(r => setTimeout(r, 3000));
        const st = await api.req(`/api/servers/${current}/update/status`);
        const last = (st.log || []).slice(-3).join(" | ");
        $("cmd-out").textContent = `Updating… ${last || ""}`;
        if (st.state === "done" || st.state === "error") {
          $("cmd-out").textContent = st.state === "done"
            ? "✅ Update done: " + (st.log || []).slice(-2).join(" | ")
            : "❌ Update failed: " + (st.error || "unknown");
          break;
        }
      }
      if (btn) { btn.disabled = false; btn.textContent = "⬆ Update"; }
      await loadServers(); if (current) await refreshStats();
      await reliveLogs("update finished — fresh logs below");
    } else {
      await api.req(`/api/servers/${current}/${act}`, { method: "POST" });
      await loadServers(); if (current) await refreshStats();
      // container recreated/restarted -> old WS stream is dead, resubscribe + show event
      await reliveLogs(act === "start" ? "starting server…" : act === "stop" ? "stopping server…" : "restarting server…");
    }
  } catch (e) { alert(e.message); }
});

async function refreshStats() {
  if (!current) return;
  try {
    const s = await api.req(`/api/servers/${current}/stats`);
    $("s-state").textContent = s.state || "?";
    $("s-cpu").textContent = (s.cpu_percent ?? "?") + " %";
    $("s-mem").textContent = (s.mem_mb ?? "?") + " MB";
    $("s-players").textContent = s.players_online ?? "?";
    updateActionButtons(s.state);
    const srv = servers.find(x => x.id === current);
    if (srv && srv.state !== s.state) { srv.state = s.state; paintHead({ ...srv, state: s.state }); }
    // state flipped (e.g. running -> exited after Stop pressed elsewhere) -> resubscribe logs
    if (lastState && lastState !== s.state) { reliveLogs(`state: ${lastState} → ${s.state}`); }
    lastState = s.state;
    hist.cpu.push(s.cpu_percent || 0); hist.mem.push(s.mem_mb || 0);
    if (hist.cpu.length > 60) { hist.cpu.shift(); hist.mem.shift(); }
    drawChart();
  } catch {}
}

function drawChart() {
  const c = $("metric-chart"), ctx = c.getContext("2d");
  ctx.clearRect(0, 0, c.width, c.height);
  const series = [hist.cpu, hist.mem.map(v => v / 10)];
  const colors = ["#5aa2ff", "#3ecf8e"];
  series.forEach((arr, si) => {
    ctx.strokeStyle = colors[si]; ctx.beginPath();
    const max = Math.max(10, ...arr);
    arr.forEach((v, i) => {
      const x = (i / Math.max(1, 59)) * c.width, y = c.height - (v / max) * (c.height - 6) - 3;
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.stroke();
  });
}

// tabs
document.querySelectorAll(".tabs button").forEach(b => b.onclick = () => {
  document.querySelectorAll(".tabs button").forEach(x => x.classList.remove("active"));
  b.classList.add("active");
  document.querySelectorAll(".tab").forEach(t => t.classList.add("hidden"));
  $("tab-" + b.dataset.tab).classList.remove("hidden");
});

// console
async function loadLogs() {
  if (!current) return;
  const j = await api.req(`/api/servers/${current}/logs?tail=300`);
  $("logs").textContent = j.logs || "";
  $("logs").scrollTop = 1e9;
}
$("logs-refresh").onclick = loadLogs;
$("cmd-send").onclick = async () => {
  const cmd = $("cmd-input").value.trim(); if (!cmd || !current) return;
  $("cmd-out").textContent = "…";
  try {
    const j = await api.req(`/api/servers/${current}/rcon`, { method: "POST", body: JSON.stringify({ command: cmd }) });
    $("cmd-out").textContent = j.output || "(empty response)";
    $("cmd-input").value = "";
  } catch (e) { $("cmd-out").textContent = "ERROR: " + e.message; }
};
async function loadQuickCmds() {
  const t = templates.find(x => x.id === (servers.find(s => s.id === current) || {}).game);
  $("quick-cmds").innerHTML = "";
  (t?.helpful_commands || []).forEach(c => {
    const b = document.createElement("button"); b.textContent = c; b.className = "mini";
    b.onclick = () => { $("cmd-input").value = c.replace(/<.*>/, "").trim(); };
    $("quick-cmds").appendChild(b);
  });
}
let logPollTimer = null, wsRetryTimer = null;
function openWs() {
  closeWs();
  if (!$("logs-live").checked || !current) return;
  try {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/api/servers/${current}/logs/ws?token=${api.token}`);
    ws.onmessage = (e) => {
      // auto-scroll only if user is already near bottom
      const nearBottom = $("logs").scrollTop + $("logs").clientHeight > $("logs").scrollHeight - 80;
      $("logs").textContent += e.data;
      // cap buffer so long sessions don't freeze the tab
      if ($("logs").textContent.length > 200000) $("logs").textContent = $("logs").textContent.slice(-150000);
      if (nearBottom) $("logs").scrollTop = 1e9;
    };
    ws.onclose = () => {
      // container restarted or proxy killed WS -> retry in 3s while live is on
      if (!$("logs-live").checked || !current) return;
      clearTimeout(wsRetryTimer);
      wsRetryTimer = setTimeout(() => { if (current && $("logs-live").checked) openWs(); }, 3000);
    };
  } catch {}
}
function closeWs() { try { ws?.close(); } catch {} ws = null; clearTimeout(wsRetryTimer); }
// Live = WS primary + 5s REST replace-fallback (covers restarts / WS drops).
function startLogPoll() {
  stopLogPoll();
  logPollTimer = setInterval(async () => {
    if (!current || !$("logs-live").checked) return;
    if (ws && ws.readyState === WebSocket.OPEN) return; // WS already live
    try {
      const j = await api.req(`/api/servers/${current}/logs?tail=150`);
      if (j.logs !== undefined) { $("logs").textContent = (j.logs || "").slice(-60000); $("logs").scrollTop = 1e9; }
    } catch {}
  }, 5000);
}
function stopLogPoll() { if (logPollTimer) clearInterval(logPollTimer); logPollTimer = null; }
$("logs-live").onchange = () => { closeWs(); if ($("logs-live").checked) { openWs(); startLogPoll(); } else stopLogPoll(); };

// players
async function loadPlayers() {
  if (!current) return;
  try {
    const j = await api.req(`/api/servers/${current}/players`);
    const tb = $("players-table tbody"); tb.innerHTML = "";
    (j.players || []).forEach(p => {
      const tr = document.createElement("tr");
      const detail = p.steam_id || p.uid || "";
      tr.innerHTML = `<td>${p.name}</td><td class="muted">${detail}</td><td></td>`;
      const td = tr.lastChild;
      [["Kick", "mini"], ["Ban", "mini"]].forEach(([label]) => {
        const b = document.createElement("button"); b.textContent = label; b.className = "mini";
        b.onclick = async () => {
          const game = (servers.find(s => s.id === current) || {}).game;
          let cmd = `${label === "Kick" ? "kick" : "ban"} ${p.name}`;
          if (game === "palworld") cmd = `${label === "Kick" ? "KickPlayer" : "BanPlayer"} ${p.steam_id || p.uid || p.name}`;
          if (!confirm(`Run: ${cmd}?`)) return;
          await api.req(`/api/servers/${current}/rcon`, { method: "POST", body: JSON.stringify({ command: cmd }) });
          loadPlayers();
        };
        td.appendChild(b);
      });
      tb.appendChild(tr);
    });
    $("players-raw").textContent = j.raw || "";
  } catch (e) { $("players-raw").textContent = "ERROR: " + e.message; }
}
$("players-refresh").onclick = loadPlayers;

// config (per-template file list; some games like Valheim have none)
let configFiles = [];
async function loadConfigList() {
  configFiles = [];
  try {
    const d = await api.req(`/api/servers/${current}`);
    configFiles = d.config_files || [];
  } catch {}
  const sel = $("config-file"); sel.innerHTML = "";
  configFiles.forEach(f => {
    const o = document.createElement("option");
    o.value = f.index; o.textContent = f.path;
    sel.appendChild(o);
  });
  if (!configFiles.length) {
    $("config-editor").value = "";
    $("config-path").textContent = "No editable config files for this game template.";
  }
}
async function loadConfig() {
  if (!current) return;
  if (!configFiles.length) await loadConfigList();
  if (!configFiles.length) return;
  const idx = $("config-file").value || 0;
  try {
    const j = await api.req(`/api/servers/${current}/config?file_idx=${idx}`);
    $("config-editor").value = j.content || "";
    const f = configFiles.find(x => String(x.index) === String(idx));
    $("config-path").textContent = "Path: " + j.path + (j.exists ? "" : " (not created yet — defaults shown)") + (f?.description ? " — " + f.description : "");
  } catch (e) { $("config-editor").value = "ERROR: " + e.message; }
}
$("config-file").onchange = loadConfig;
$("config-load").onclick = loadConfig;
$("config-save").onclick = async () => {
  if (!configFiles.length) return alert("No config file for this game.");
  const idx = $("config-file").value || 0;
  await api.req(`/api/servers/${current}/config?file_idx=${idx}`, { method: "PUT", body: JSON.stringify({ content: $("config-editor").value }) });
  alert("Saved. Restart server to apply.");
};

// settings form (per-game friendly form alongside raw Config tab)
const SETTINGS_BLURBS = {
  palworld: "Parsed OptionSettings from PalWorldSettings.ini — same file as the Config tab, friendlier.",
  properties: "server.properties as a form — same file as the Config tab, friendlier.",
  ini: "Server .ini as a form — same file as the Config tab, friendlier.",
  env: "These live in the server environment (mirrored in the Env tab). Restart to apply.",
};
async function loadSettings() {
  if (!current) return;
  const f = $("settings-form"); f.innerHTML = "<p class='muted'>Loading…</p>";
  try {
    const j = await api.req(`/api/servers/${current}/settings`);
    $("settings-desc").textContent = `${j.game_name}: ${SETTINGS_BLURBS[j.source] || ""}`.trim()
      + (j.path ? ` (File: ${j.path}${j.exists ? "" : " — not created yet, defaults shown)"}` : "");
    $("settings-note").textContent = j.note || "Restart server to apply.";
    f.innerHTML = "";
    if (!j.fields.length) { f.innerHTML = "<p class='muted'>No form settings for this game — use Config / Env tabs.</p>"; return; }
    j.fields.forEach(field => {
      const l = document.createElement("label");
      l.textContent = field.label + (field.description ? ` — ${field.description}` : "");
      const val = (j.values[field.key] ?? field.default ?? "");
      let input;
      if (field.type === "boolean") {
        input = document.createElement("select");
        ["true", "false"].forEach(o => {
          const op = document.createElement("option");
          op.value = o; op.textContent = o;
          if (String(val).toLowerCase() === o) op.selected = true;
          input.appendChild(op);
        });
      } else if (field.type === "select" && field.options?.length) {
        input = document.createElement("select");
        field.options.forEach(o => {
          const op = document.createElement("option");
          op.value = o; op.textContent = o;
          if (String(val) === String(o)) op.selected = true;
          input.appendChild(op);
        });
        if (!field.options.map(String).includes(String(val))) {
          const op = document.createElement("option");
          op.value = val; op.textContent = `${val} (current)`; op.selected = true;
          input.appendChild(op);
        }
      } else {
        input = document.createElement("input");
        input.value = val ?? "";
        if (field.type === "password") input.type = "password";
        else if (field.type === "integer" || field.type === "number") input.type = "number";
      }
      input.dataset.key = field.key;
      l.appendChild(input); f.appendChild(l);
    });
  } catch (e) { f.innerHTML = ""; $("settings-desc").textContent = "ERROR: " + e.message; }
}
$("settings-load").onclick = loadSettings;
$("settings-save").onclick = async () => {
  const values = {};
  $("settings-form").querySelectorAll("input,select").forEach(i => values[i.dataset.key] = i.value);
  const j = await api.req(`/api/servers/${current}/settings`, { method: "PUT", body: JSON.stringify({ values }) });
  alert("Saved. " + (j.note || "Restart server to apply.")); loadConfig(); loadEnv();
};

// env + public address / share link
async function loadEnv() {
  if (!current) return;
  const j = await api.req(`/api/servers/${current}/env`);
  $("env-editor").value = JSON.stringify(j.env, null, 2);
  $("ports-view").textContent = JSON.stringify(j.ports, null, 2);
  try {
    const d = await api.req(`/api/servers/${current}`);
    $("public-addr").value = d.public_address || "";
    $("show-homepage").checked = !d.hidden;
    const url = `${location.origin}/share/${current}`;
    const a = $("share-link"); a.href = url; a.textContent = url;
  } catch {}
}
$("show-homepage").onchange = async () => {
  if (!current) return;
  const j = await api.req(`/api/servers/${current}/visibility`, { method: "PUT", body: JSON.stringify({ hidden: !$("show-homepage").checked }) });
  const srv = servers.find(x => x.id === current);
  if (srv) srv.hidden = j.hidden;
  loadServers();
};
$("public-save").onclick = async () => {
  if (!current) return;
  const j = await api.req(`/api/servers/${current}/public-address`, { method: "PUT", body: JSON.stringify({ public_address: $("public-addr").value.trim() }) });
  const url = `${location.origin}/share/${current}`;
  const a = $("share-link"); a.href = url; a.textContent = url;
  alert("Saved. Share link: " + url);
};
$("share-copy").onclick = () => { navigator.clipboard?.writeText($("share-link").href); };
$("env-save").onclick = async () => {
  const env = JSON.parse($("env-editor").value);
  await api.req(`/api/servers/${current}/env`, { method: "PUT", body: JSON.stringify({ env }) });
  alert("Saved. Use Update/Restart to apply.");
};

// custom image build box
async function loadBuildBox() {
  if (!current) return;
  try {
    const d = await api.req(`/api/servers/${current}`);
    const j = await api.req(`/api/servers/${current}/build/dockerfile`);
    $("build-dockerfile").value = j.dockerfile || "";
    $("build-state").textContent = d.has_dockerfile
      ? `Custom image: ${d.image} (Dockerfile stored — Rebuild to apply changes, then Restart)`
      : "No custom Dockerfile stored. Pulled image: " + d.image;
  } catch (e) { $("build-state").textContent = "ERROR: " + e.message; }
}
async function runBuild(startAfter) {
  if (!current) return;
  const df = $("build-dockerfile").value;
  if (!df.trim()) return alert("Paste a Dockerfile first");
  await api.req(`/api/servers/${current}/build`, { method: "POST", body: JSON.stringify({ dockerfile: df, start_after: startAfter }) });
  const st = await pollBuild(current, $("build-log"));
  alert(st.state === "done" ? "Image built." : "Build failed: " + (st.error || "unknown"));
  loadServers(); refreshStats();
}
$("build-run").onclick = () => runBuild(false);
$("build-run-start").onclick = () => runBuild(true);

// mods
async function loadMods() {
  if (!current) return;
  try {
    const j = await api.req(`/api/servers/${current}/mods`);
    $("mods-path").textContent = "Mods folder: " + j.mods_dir + "/  (inside server volume)";
    const cat = $("mods-catalog"); cat.innerHTML = "";
    (j.catalog || []).forEach(m => {
      const row = document.createElement("div"); row.className = "cmdrow";
      row.innerHTML = `<div style="flex:1"><b>${m.name}</b><br><span class="muted small">${m.description || ""}</span></div>`;
      const btn = document.createElement("button"); btn.textContent = "Install"; btn.className = "primary";
      btn.onclick = async () => {
        let url = m.url;
        if (!url) url = prompt(`Paste download URL for ${m.name} (Linux build .zip):`, "https://");
        if (!url) return;
        btn.disabled = true; btn.textContent = "Installing…";
        try {
          await api.req(`/api/servers/${current}/mods/install`, { method: "POST", body: JSON.stringify({ mod_id: m.id, url_override: url }) });
          alert("Installed. Restart server to load it."); loadMods();
        } catch (e) { alert("Install failed: " + e.message); }
        btn.disabled = false; btn.textContent = "Install";
      };
      row.appendChild(btn); cat.appendChild(row);
    });
    if (!(j.catalog || []).length) cat.innerHTML = "<p class='muted small'>No catalog for this game — use URL install or upload below.</p>";
    const tb = $("mods-table tbody"); tb.innerHTML = "";
    (j.installed || []).forEach(f => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${f.name}${f.is_dir ? " /" : ""}</td><td class="muted">${f.is_dir ? "dir" : (f.size_bytes / 1024).toFixed(1) + " KB"}</td><td></td>`;
      const del = document.createElement("button"); del.textContent = "Delete"; del.className = "mini";
      del.onclick = async () => { if (confirm(`Delete ${f.name}?`)) { await api.req(`/api/servers/${current}/mods/${encodeURIComponent(f.name)}`, { method: "DELETE" }); loadMods(); } };
      tr.lastChild.appendChild(del); tb.appendChild(tr);
    });
  } catch (e) { $("mods-path").textContent = "ERROR: " + e.message; }
}
$("mods-refresh").onclick = loadMods;
$("mod-install-url").onclick = async () => {
  const url = $("mod-url").value.trim(); if (!url || !current) return;
  await api.req(`/api/servers/${current}/mods/install`, { method: "POST", body: JSON.stringify({ url }) });
  $("mod-url").value = ""; alert("Installed. Restart server to load it."); loadMods();
};
$("mod-upload").onclick = async () => {
  const f = $("mod-file").files[0]; if (!f || !current) return alert("Pick a file first");
  const fd = new FormData(); fd.append("file", f);
  const r = await fetch(`/api/servers/${current}/mods/upload`, { method: "POST", headers: { Authorization: "Bearer " + api.token }, body: fd });
  if (!r.ok) throw new Error(await r.text());
  alert("Uploaded. Restart server to load it."); loadMods();
};

// backups
async function loadBackups() {
  if (!current) return;
  const j = await api.req(`/api/backups?server=${current}`);
  const tb = $("backup-table tbody"); tb.innerHTML = "";
  j.forEach(b => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${b.filename}</td><td>${(b.size_bytes / 1024).toFixed(1)} KB</td><td>${b.created}</td><td></td>`;
    const del = document.createElement("button"); del.textContent = "Delete"; del.className = "mini";
    del.onclick = async () => { if (confirm("Delete backup?")) { await api.req(`/api/backups/${b.filename}`, { method: "DELETE" }); loadBackups(); } };
    tr.lastChild.appendChild(del); tb.appendChild(tr);
  });
}
$("backup-now").onclick = async () => { await api.req(`/api/servers/${current}/backup`, { method: "POST" }); loadBackups(); };
$("backup-refresh").onclick = loadBackups;

// new server modal (Create vs Create + Start)
$("add-server-btn").onclick = () => {
  $("modal").classList.remove("hidden"); $("ns-err").textContent = "";
  updateNsDesc(true);
};
$("ns-cancel").onclick = () => $("modal").classList.add("hidden");
async function pollBuild(serverId, logEl, btns) {
  // poll build job until done/error, streaming log tail into logEl
  for (let i = 0; i < 200; i++) {
    await new Promise(r => setTimeout(r, 3000));
    const st = await api.req(`/api/servers/${serverId}/build/status`);
    if (logEl) { logEl.classList.remove("hidden"); logEl.textContent = (st.log || []).slice(-30).join("\n"); logEl.scrollTop = 1e9; }
    if (st.state === "done" || st.state === "error") return st;
  }
  return { state: "error", error: "timed out waiting for build" };
}
async function doCreate(start) {
  $("ns-err").textContent = "";
  const dockerfile = $("ns-dockerfile").value;
  try {
    let env = {};
    if ($("ns-env").value.trim()) env = JSON.parse($("ns-env").value);
    const j = await api.req("/api/servers", { method: "POST", body: JSON.stringify({
      name: $("ns-name").value.trim(), game: $("ns-template").value,
      image: $("ns-image").value.trim() || null, rcon_password: $("ns-rcon").value || null, env, start,
      dockerfile: dockerfile.trim() || null,
    }) });
    // optional build context zip (uploaded before the real build so COPY works)
    const ctx = $("ns-context").files[0];
    if (ctx && j.id) {
      const fd = new FormData(); fd.append("file", ctx);
      await fetch(`/api/servers/${j.id}/build/context`, { method: "POST", headers: { Authorization: "Bearer " + api.token }, body: fd });
      // wait out the auto-started build (it may have missed the context), then rebuild properly
      await pollBuild(j.id, $("ns-buildlog"));
      try {
        await api.req(`/api/servers/${j.id}/build`, { method: "POST", body: JSON.stringify({ start_after: start }) });
      } catch (e) { if (!String(e.message).includes("already in progress")) throw e; }
    }
    if (j.build === "started") {
      // custom Dockerfile: build first, then land on the server
      const st = await pollBuild(j.id, $("ns-buildlog"));
      if (st.state !== "done") { $("ns-err").textContent = "Build failed: " + (st.error || "unknown"); return; }
    }
    $("modal").classList.add("hidden");
    nsSuggested = "";
    $("ns-name").value = ""; $("ns-rcon").value = ""; $("ns-env").value = ""; $("ns-dockerfile").value = "";
    $("ns-context").value = ""; $("ns-buildlog").classList.add("hidden"); $("ns-buildlog").textContent = "";
    await loadServers();
    if (j.id) selectServer(j.id);
  } catch (e) { $("ns-err").textContent = e.message; }
}
$("ns-create").onclick = () => doCreate(false);
$("ns-create-start").onclick = () => doCreate(true);

if (api.token) boot();
