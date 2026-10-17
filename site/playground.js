// Playground: a real SQLite database (sql.js) in the browser, protected by the same drill as the nightly job.
// Backups are encrypted with AES-256-GCM (Web Crypto), restored into a NEW database and verified table by table.

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const CDN = "https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.14.2/";
const TABLES = ["customers", "products", "orders", "order_items"];

const FIRST = ["Lena", "Noah", "Mia", "Luca", "Emma", "Elias", "Lara", "Leon", "Sofia", "Matteo", "Nina", "Jonas"];
const LAST = ["Meier", "Müller", "Keller", "Huber", "Weber", "Schmid", "Frei", "Baumann", "Steiner", "Fischer"];
const PRODUCTS = [["Kaffeebohnen 500 g", 1890], ["Thermosflasche", 2990], ["Notizbuch A5", 990], ["USB-C Kabel", 1490], ["Rucksack", 8990],
  ["Velolicht", 3490], ["Kopfhörer", 6990], ["Tastatur", 7490], ["Powerbank", 3990], ["Regenjacke", 12990]];

let SQL, db, key;
let snaps = [];
let day = 0;
let busy = false;
let damaged = false; // a disaster hit the live data since the last restore

// ------------------------------------------------------------ log
const log = (text, cls = "") => {
  const el = $("#pg-log");
  if (el.dataset.fresh !== "1") { el.textContent = ""; el.dataset.fresh = "1"; }
  el.insertAdjacentHTML("beforeend", `<span class="${cls}">${esc(text)}</span>\n`);
  el.scrollTop = el.scrollHeight;
};
const clearLog = () => { $("#pg-log").textContent = ""; $("#pg-log").dataset.fresh = "1"; };

// ------------------------------------------------------------ data
function rand(seed) { let s = seed >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32); }

function seed(d) {
  const r = rand(42);
  d.run(`CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, city TEXT NOT NULL);
         CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT NOT NULL, price_rappen INTEGER NOT NULL, stock INTEGER NOT NULL);
         CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL, status TEXT NOT NULL, total_rappen INTEGER NOT NULL, day INTEGER NOT NULL);
         CREATE TABLE order_items (id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, product_id INTEGER NOT NULL, qty INTEGER NOT NULL);`);
  const cities = ["Brugg", "Baden", "Aarau", "Basel", "Zürich", "Bern"];
  d.run("BEGIN");
  for (let i = 1; i <= 300; i++) {
    const n = `${FIRST[Math.floor(r() * FIRST.length)]} ${LAST[Math.floor(r() * LAST.length)]}`;
    d.run("INSERT INTO customers VALUES (?,?,?,?)", [i, n, `${n.toLowerCase().replace(" ", ".")}.${i}@example.ch`, cities[Math.floor(r() * cities.length)]]);
  }
  PRODUCTS.forEach(([name, price], i) => d.run("INSERT INTO products VALUES (?,?,?,?)", [i + 1, name, price, 100 + Math.floor(r() * 200)]));
  d.run("COMMIT");
  for (let k = 0; k < 10; k++) businessDay(d, 120);
}

function businessDay(d, orders) {
  day += 1;
  const r = rand(1000 + day);
  const maxC = d.exec("SELECT MAX(id) FROM customers")[0]?.values[0][0] || 1;
  let oid = (d.exec("SELECT COALESCE(MAX(id),0) FROM orders")[0]?.values[0][0] || 0) + 1;
  let iid = (d.exec("SELECT COALESCE(MAX(id),0) FROM order_items")[0]?.values[0][0] || 0) + 1;
  d.run("BEGIN");
  for (let n = 0; n < (orders ?? 40 + Math.floor(r() * 40)); n++, oid++) {
    let total = 0;
    for (let k = 0; k < 1 + Math.floor(r() * 3); k++, iid++) {
      const p = 1 + Math.floor(r() * PRODUCTS.length), q = 1 + Math.floor(r() * 3);
      d.run("INSERT INTO order_items VALUES (?,?,?,?)", [iid, oid, p, q]);
      total += PRODUCTS[p - 1][1] * q;
    }
    const status = r() < 0.1 ? "cancelled" : r() < 0.5 ? "shipped" : "paid";
    d.run("INSERT INTO orders VALUES (?,?,?,?,?)", [oid, 1 + Math.floor(r() * maxC), status, total, day]);
  }
  d.run("COMMIT");
}

// ------------------------------------------------------------ fingerprint: row count + SHA-256 per table
async function sha256(bytes) {
  const h = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(h)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function fingerprint(d) {
  const out = {};
  for (const t of TABLES) {
    let res;
    try { res = d.exec(`SELECT * FROM ${t} ORDER BY 1`)[0]; } catch { continue; } // table may be gone
    const text = (res?.values || []).map((row) => row.join("\t")).join("\n");
    out[t] = { rows: res?.values.length || 0, sha256: await sha256(new TextEncoder().encode(text)) };
  }
  return out;
}

const totalRows = (fp) => Object.values(fp).reduce((s, t) => s + t.rows, 0);
const sameFp = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// ------------------------------------------------------------ render
async function render() {
  const counts = TABLES.map((t) => {
    try { return [t, db.exec(`SELECT COUNT(*) FROM ${t}`)[0].values[0][0]]; } catch { return [t, null]; }
  });
  $("#pg-tables").innerHTML = counts.map(([t, n]) => `<tr><td>${t}</td><td class="r">${n === null ? '<b style="color:var(--bad)">table missing</b>' : n.toLocaleString("de-CH") + " rows"}</td></tr>`).join("");

  let rows = [];
  try {
    rows = db.exec(`SELECT o.id, COALESCE(c.name, '(customer missing)'), o.status, o.total_rappen / 100.0
                    FROM orders o LEFT JOIN customers c ON c.id = o.customer_id ORDER BY o.id DESC LIMIT 6`)[0]?.values || [];
  } catch { rows = []; }
  $("#pg-orders").innerHTML = rows.length
    ? rows.map((r) => `<tr><td>${r[0]}</td><td>${esc(r[1])}</td><td>${esc(r[2])}</td><td class="r">${Number(r[3]).toFixed(2)}</td></tr>`).join("")
    : `<tr><td colspan="4" style="color:var(--bad)">no orders table</td></tr>`;

  const fp = await fingerprint(db);
  const newest = snaps.at(-1);
  const pill = $("#pg-state");
  if (newest && sameFp(fp, newest.manifest)) { pill.textContent = `matches ${newest.id}`; pill.className = "pill ok"; }
  else if (damaged || counts.some(([, n]) => n === null)) { pill.textContent = "damaged"; pill.className = "pill bad"; }
  else { pill.textContent = newest ? "changed since last backup" : "no backup yet"; pill.className = "pill"; }

  $("#pg-snaps").innerHTML = snaps.length
    ? [...snaps].reverse().map((s) => `
      <li><b>${esc(s.id)}</b>
        <small>${(s.ct.length / 1024).toFixed(0)} KB encrypted · ${totalRows(s.manifest).toLocaleString("de-CH")} rows · sha256 ${s.sha256.slice(0, 10)}…${s.tampered ? " · you flipped a byte" : ""}</small>
        <button class="btn btn--ok" type="button" data-restore="${esc(s.id)}">Restore drill</button></li>`).join("")
    : '<li class="muted small">No backup yet. Take one first.</li>';
}

// ------------------------------------------------------------ actions
async function backup() {
  clearLog();
  log(`▸ backup of database "shop"`, "head");
  let t0 = performance.now();
  const plain = db.export();
  const manifest = await fingerprint(db);
  log(`  dump: ${(plain.length / 1024).toFixed(0)} KB, ${totalRows(manifest).toLocaleString("de-CH")} rows in ${(performance.now() - t0).toFixed(0)} ms`);
  t0 = performance.now();
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, plain));
  log(`  encrypt: AES-256-GCM, ${(ct.length / 1024).toFixed(0)} KB in ${(performance.now() - t0).toFixed(0)} ms`);
  const id = `shop-day${String(day).padStart(2, "0")}-${new Date().toTimeString().slice(0, 8).replaceAll(":", "")}`;
  const snap = { id, iv, ct, sha256: await sha256(ct), manifest, tampered: false };
  snaps.push(snap);
  log(`  manifest: ${Object.keys(manifest).length} tables fingerprinted, sha256 ${snap.sha256.slice(0, 12)}…`);
  log(`✓ backup ${id} stored`, "ok");
}

async function disaster(kind) {
  clearLog();
  if (kind !== "corrupt") damaged = true;
  if (kind === "drop") { try { db.run("DROP TABLE orders"); log("✗ DROP TABLE orders: every order is gone.", "bad"); } catch { log("orders is already gone."); } }
  if (kind === "delete") {
    db.run("DELETE FROM customers WHERE id % 10 < 3");
    log("✗ a bad migration ran DELETE FROM customers WHERE id % 10 < 3: 30 % of customers are gone, their orders point to nobody.", "bad");
  }
  if (kind === "ransom") {
    db.run("UPDATE customers SET name = upper(hex(randomblob(6))), email = lower(hex(randomblob(8))) || '@locked'");
    log("✗ ransomware overwrote every customer name and email. The row count is unchanged, only checksums notice.", "bad");
  }
  if (kind === "corrupt") {
    const s = snaps.at(-1);
    if (!s) return log("Take a backup first, then corrupt it.");
    s.ct[Math.floor(s.ct.length / 2)] ^= 1;
    s.tampered = true;
    log(`✗ one bit flipped in the middle of ${s.id} (bit rot, a bad disk, or an attacker). Try restoring it.`, "bad");
  }
}

async function restore(id) {
  const s = snaps.find((x) => x.id === id);
  clearLog();
  log(`▸ restore drill from ${id}`, "head");
  const t0 = performance.now();
  const sum = await sha256(s.ct);
  if (sum !== s.sha256) {
    log(`✗ sha256 mismatch: ${sum.slice(0, 12)}… vs manifest ${s.sha256.slice(0, 12)}…`, "bad");
    log("✗ the backup changed after it was taken. Restore refused, the damaged data never touches the database.", "bad");
    log(snaps.length > 1 ? "  → try an older backup." : "  → this is why you keep more than one backup.");
    return;
  }
  log("  sha256 matches the manifest");
  let plain;
  try {
    plain = new Uint8Array(await crypto.subtle.decrypt({ name: "AES-GCM", iv: s.iv }, key, s.ct));
  } catch {
    log("✗ AES-GCM authentication failed. Restore refused.", "bad");
    return;
  }
  log("  decrypted, GCM tag authenticated");
  const fresh = new SQL.Database(plain); // a NEW, empty database: nothing survives from the damaged one
  log("  restored into a new, empty database");
  const fp = await fingerprint(fresh);
  let ok = true;
  for (const [t, exp] of Object.entries(s.manifest)) {
    const got = fp[t];
    const good = got && got.rows === exp.rows && got.sha256 === exp.sha256;
    ok &&= good;
    log(`  verify ${t.padEnd(12)} ${String(got?.rows ?? "missing").padStart(5)} rows  ${good ? "identical" : "MISMATCH"}`, good ? "" : "bad");
  }
  const ms = performance.now() - t0;
  if (!ok) { log("✗ verification failed: the restored data is not what was backed up.", "bad"); return; }
  db.close();
  db = fresh;
  damaged = false;
  log(`✓ drill passed: ${totalRows(fp).toLocaleString("de-CH")} rows back in ${ms.toFixed(0)} ms (that is the RTO). The shop is live again.`, "ok");
}

async function guard(fn) {
  if (busy) return;
  busy = true;
  document.querySelectorAll(".playground button").forEach((b) => (b.disabled = true));
  try { await fn(); } catch (err) { log(`error: ${err.message}`, "bad"); }
  await render();
  document.querySelectorAll(".playground button").forEach((b) => (b.disabled = false));
  busy = false;
}

// ------------------------------------------------------------ start
async function start() {
  try {
    SQL = await initSqlJs({ locateFile: (f) => CDN + f });
  } catch {
    $("#pg-log").textContent = "Could not load SQLite (sql.js). Check your connection and reload.";
    return;
  }
  db = new SQL.Database();
  seed(db);
  key = await crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
  $("#pg-log").textContent = "SQLite shop database ready: 300 customers, 10 days of orders.\nStart with \"Take backup\", then cause a disaster and run the restore drill.\n(The encryption key was generated in this tab and never leaves it.)";
  await render();

  $("#pg-backup").addEventListener("click", () => guard(backup));
  $("#pg-day").addEventListener("click", () => guard(async () => {
    try { businessDay(db); clearLog(); log(`✓ day ${day}: new orders came in. The live data has moved on from the last backup.`, "ok"); }
    catch { clearLog(); log("✗ the shop cannot take orders: a table is missing. Restore first.", "bad"); }
  }));
  document.querySelector(".pg__side").addEventListener("click", (e) => {
    const d = e.target.closest("[data-disaster]");
    if (d) guard(() => disaster(d.dataset.disaster));
    const r = e.target.closest("[data-restore]");
    if (r) guard(() => restore(r.dataset.restore));
  });
}

start();
