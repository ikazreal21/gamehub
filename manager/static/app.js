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
    let j; try { j = JSON.parse(t); } catch { j = { raw: t }; }
    if (!r.ok) throw new Error(j.detail || t.slice(0, 300));
    return j;
  },
  logout() { this.token = ""; localStorage.removeItem("gh_token"); location.reload(); },
};

let servers = [], templates = [], current = null, ws = null, hist = { cpu: [], mem: [] };

// login
$("login-btn").onclick = async () => {
  $("login-err").textContent = "";
  try {
    const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: $("login-user").value, password: $("login-pass").value }) });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || "login failed");
    api.token = j.access_token; localStorage.setItem("gh_token", api.token);
    boot();
  } catch (e) { $("login-err").textContent = String(e.message || e); }
};
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
function updateNsDesc() {
  const t = templates.find(x => x.id === $("ns-template").value);
  if (t) { $("ns-desc").textContent = t.description + " | image: " + (t.image || "(you supply)"); $("ns-image").placeholder = t.image || "e.g. nginx:latest"; }
}
$("ns-template").onchange = updateNsDesc;

async function loadServers() {
  servers = await api.req("/api/servers");
  const box = $("server-list"); box.innerHTML = "";
  servers.forEach(s => {
    const b = document.createElement("button");
    b.className = "srv" + (current === s.id ? " active" : "");
    b.innerHTML = `<b>${s.name}</b><span class="muted small">${s.game_name}</span><br><span class="st ${s.state}">${s.state}</span>`;
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
  await Promise.all([refreshStats(), loadLogs(), loadPlayers(), loadConfig(), loadEnv(), loadBackups(), loadPalSettings(), loadQuickCmds()]);
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

// config
async function loadConfig() {
  if (!current) return;
  try {
    const j = await api.req(`/api/servers/${current}/config`);
    $("config-editor").value = j.content || ""; $("config-path").textContent = "Path: " + j.path + (j.exists ? "" : " (not created yet — defaults shown)");
  } catch (e) { $("config-editor").value = "ERROR: " + e.message; }
}
$("config-load").onclick = loadConfig;
$("config-save").onclick = async () => {
  await api.req(`/api/servers/${current}/config`, { method: "PUT", body: JSON.stringify({ content: $("config-editor").value }) });
  alert("Saved. Restart server to apply.");
};

// palworld form
async function loadPalSettings() {
  if (!current) return;
  const srv = servers.find(s => s.id === current);
  if (!srv || srv.game !== "palworld") { $("pal-form").innerHTML = "<p class='muted'>Select a Palworld server.</p>"; return; }
  const j = await api.req(`/api/servers/${current}/palworld-settings`);
  const f = $("pal-form"); f.innerHTML = "";
  Object.entries(j.settings).forEach(([k, v]) => {
    const l = document.createElement("label"); l.textContent = k;
    const i = document.createElement("input"); i.value = v; i.dataset.key = k;
    l.appendChild(i); f.appendChild(l);
  });
}
$("pal-load").onclick = loadPalSettings;
$("pal-save").onclick = async () => {
  const settings = {};
  $("pal-form").querySelectorAll("input").forEach(i => settings[i.dataset.key] = i.value);
  await api.req(`/api/servers/${current}/palworld-settings`, { method: "PUT", body: JSON.stringify({ settings }) });
  alert("Saved. Restart server to apply."); loadConfig();
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
    const url = `${location.origin}/share/${current}`;
    const a = $("share-link"); a.href = url; a.textContent = url;
  } catch {}
}
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

// new server modal
$("add-server-btn").onclick = () => { $("modal").classList.remove("hidden"); $("ns-err").textContent = ""; };
$("ns-cancel").onclick = () => $("modal").classList.add("hidden");
$("ns-create").onclick = async () => {
  $("ns-err").textContent = "";
  try {
    let env = {};
    if ($("ns-env").value.trim()) env = JSON.parse($("ns-env").value);
    await api.req("/api/servers", { method: "POST", body: JSON.stringify({
      name: $("ns-name").value.trim(), game: $("ns-template").value,
      image: $("ns-image").value.trim() || null, rcon_password: $("ns-rcon").value || null, env,
    }) });
    $("modal").classList.add("hidden");
    $("ns-name").value = ""; $("ns-rcon").value = "";
    await loadServers();
  } catch (e) { $("ns-err").textContent = e.message; }
};

if (api.token) boot();
