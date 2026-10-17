// Live status: renders data/history.json and data/backups.json, written by the nightly drill.

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function ago(iso) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}
const mb = (b) => `${(b / 1e6).toFixed(2)} MB`;
const fmt = (n) => Number(n).toLocaleString("de-CH");
const when = (iso) => new Date(iso).toLocaleString("de-CH", { dateStyle: "medium", timeStyle: "short" });

async function load(name) {
  const res = await fetch(`data/${name}?t=${Date.now()}`);
  if (!res.ok) throw new Error(`${name}: HTTP ${res.status}`);
  return res.json();
}

function chart(history) {
  const runs = history.filter((r) => r.restore || r.backup).slice(-30);
  if (runs.length < 2) return '<p class="small muted">The chart appears after a few nights.</p>';
  const W = 520, H = 150, P = 28;
  const rows = runs.map((r) => r.backup?.total_rows ?? r.restore?.rows ?? 0);
  const rto = runs.map((r) => r.restore?.restore_seconds ?? 0);
  const x = (i) => P + (i * (W - 2 * P)) / (runs.length - 1);
  const scale = (vals) => { const max = Math.max(...vals, 1); return (v) => H - P - (v / max) * (H - 2 * P); };
  const yr = scale(rows), yt = scale(rto);
  const line = (vals, y) => vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  return `
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Rows protected and restore time over the last runs">
      <line x1="${P}" y1="${H - P}" x2="${W - P}" y2="${H - P}" stroke="var(--line)"/>
      <path d="${line(rows, yr)}" fill="none" stroke="var(--ink)" stroke-width="2.5"/>
      <path d="${line(rto, yt)}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-dasharray="5 4"/>
      <text x="${P}" y="16">rows ${fmt(Math.max(...rows))}</text>
      <text x="${W - P}" y="16" text-anchor="end" fill="var(--accent)">restore ${Math.max(...rto).toFixed(2)} s</text>
      <text x="${P}" y="${H - 8}">run ${runs[0].run}</text>
      <text x="${W - P}" y="${H - 8}" text-anchor="end">run ${runs.at(-1).run}</text>
    </svg>`;
}

async function render() {
  let history, backups;
  try {
    [history, backups] = await Promise.all([load("history.json"), load("backups.json")]);
  } catch {
    $("#verdict").textContent = "No drill has run yet.";
    return;
  }
  const last = history.at(-1);
  const passed = last.status === "passed";
  $("#verdict").innerHTML = `Last restore drill: <span class="${passed ? "ok" : "bad"}">${passed ? "passed" : "FAILED"}</span>`;
  $("#verdict-meta").textContent = `run ${last.run} · ${ago(last.finished_at)} · ${when(last.finished_at)}${last.restore ? ` · restored ${last.restore.source}` : " · genesis"}`;

  let streak = 0;
  for (let i = history.length - 1; i >= 0 && history[i].status === "passed"; i--) streak++;
  const lastRestore = [...history].reverse().find((r) => r.restore);
  $("#n-rto").textContent = lastRestore ? `${lastRestore.restore.restore_seconds.toFixed(2)} s` : "-";
  $("#n-streak").textContent = streak;
  $("#n-kept").textContent = backups.length;
  $("#n-rows").textContent = fmt(backups.at(-1)?.total_rows ?? 0);

  $("#steps").innerHTML = last.steps.map((s) => `
    <li><span class="i ${s.status === "ok" ? "" : "bad"}">${s.status === "ok" ? "&#10003;" : "!"}</span>
      <div><b>${esc(s.name)}</b>${s.detail ? `<small>${esc(s.detail)}</small>` : ""}</div>
      <span class="t">${s.seconds}s</span></li>`).join("");
  $("#run-link").innerHTML = last.ci_run
    ? `<a href="https://github.com/NotDom1n1c/lifeboat/actions/runs/${esc(last.ci_run)}">Open this run on GitHub Actions</a>`
    : "";

  $("#chart").innerHTML = chart(history);
  $("#runs").innerHTML = [...history].reverse().slice(0, 40).map((r) => `
    <li><span class="dot ${r.status === "passed" ? "" : "bad"}"></span><span>#${r.run}</span>
      <span>${esc(when(r.started_at))}</span>
      <span>${r.restore ? `${r.restore.restore_seconds.toFixed(2)} s` : r.status === "passed" ? "genesis" : "failed"}</span></li>`).join("");

  $("#backups").innerHTML = [...backups].reverse().map((b, i) => `
    <tr><td><code>${esc(b.id)}</code></td><td>${esc(when(b.created_at))}</td>
      <td class="r">${mb(b.bytes.encrypted)}</td><td class="r">${fmt(b.total_rows)}</td>
      <td><code title="${esc(b.sha256)}">${esc(b.sha256.slice(0, 12))}…</code></td>
      <td>${b.kept_as.map((k) => `<span class="tag ${k === "latest" ? "latest" : ""}">${esc(k)}</span>`).join("")}</td></tr>`).join("");
}

render();
