// Erhvervsradar upload tool.
// 1. Read the uploaded spreadsheet in the browser (ExcelJS) and find the column of links.
// 2. Send the links one at a time to /api/crawl (a Netlify function), politely spaced.
// 3. Write the results into new columns of the same workbook and offer it as a download.
// The file itself never leaves the browser; only the links are sent.

const API = "/api/crawl";
const MAX_ROWS = 200;
// Seconds to wait between two requests to the same site.
const HOST_DELAY_MS = { "www.edc.dk": 2000, "edc.dk": 2000, "search.savills.com": 5000 };
const SUPPORTED_HOSTS = new Set(Object.keys(HOST_DELAY_MS));

const TYPE_LABELS = {
  office: "Office",
  warehouse_industrial: "Warehouse & industrial",
  retail: "Retail",
  hospitality: "Hotel & restaurant",
  mixed: "Mixed use",
  other: "Other",
};
const SOURCE_LABELS = { edc: "EDC Erhverv", savills: "Savills" };

// How each result is shown in the table and written to the sheet.
// The order here is the order of the colored badges on the finished screen.
const RESULT_LABELS = {
  available: ["Available", "available"],
  rented: ["Rented", "rented"],
  not_found: ["Listing gone", "gone"],
  unsupported_site: ["Site not supported", "skipped"],
  not_a_listing: ["Not a listing page", "skipped"],
  invalid_url: ["Not a link", "skipped"],
  blocked_by_robots: ["Blocked by robots.txt", "failed"],
  error: ["Couldn't load", "failed"],
};

// Columns added to the user's sheet. If they already exist (a results file
// uploaded again), they are overwritten instead of added twice.
const OUTPUT_COLUMNS = [
  ["Result", (r) => resultLabel(r)[0], 18],
  ["Address", (r) => r.listing?.address, 28],
  ["Postcode", (r) => r.listing?.zip_code, 10],
  ["City", (r) => r.listing?.city, 18],
  ["Property type", (r) => TYPE_LABELS[r.listing?.property_type], 20],
  ["Size m² (from)", (r) => r.listing?.size_m2_min, 14, "#,##0"],
  ["Size m² (to)", (r) => r.listing?.size_m2_max, 14, "#,##0"],
  ["Rent per year kr. (from)", (r) => r.listing?.yearly_rent_min, 22, "#,##0"],
  ["Rent per year kr. (to)", (r) => r.listing?.yearly_rent_max, 22, "#,##0"],
  ["Rent per m² kr.", (r) => r.listing?.rent_per_m2, 16, "#,##0"],
  ["Source", (r) => SOURCE_LABELS[r.listing?.source], 14],
  ["Checked at", (r) => localTime(r.checked_at), 18, "yyyy-mm-dd hh:mm"],
  ["Note", (r) => r.message, 40],
];

const EXAMPLE_ROWS = [
  ["Vester Voldgade 104, København V", "https://www.edc.dk/erhverv/erhvervslejemaal/kontor/1552-koebenhavn-v/vester-voldgade-104/114058334/"],
  ["Lyskær 5, Herlev", "https://www.edc.dk/erhverv/erhvervslejemaal/kontor/2730-herlev/lyskaer-5/27801563/"],
  ["Alleen 4, Skanderborg", "https://www.edc.dk/erhverv/erhvervslejemaal/detail/8660-skanderborg/alleen-4/87301724/"],
  ["Rygårds Allé 133, Hellerup", "https://search.savills.com/dk/da/ejendomsoplysninger/dff8a9c0-92ab-4c6e-afce-f1d7e327c393"],
  ["Jenagade 22, København S", "https://search.savills.com/dk/da/ejendomsoplysninger/1087c4ba-9c56-4aae-80c8-b90dcf9edab3"],
  ["A site that isn't supported", "https://www.example.com/listing/123"],
];

const nf = new Intl.NumberFormat("da-DK", { maximumFractionDigits: 0 });
// Excel dates have no time zone, so write the time as the user's clock shows it.
const localTime = (iso) => {
  if (!iso) return null;
  const d = new Date(iso);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000);
};
const $ = (id) => document.getElementById(id);

let job = null; // everything about the file currently being processed

// ---------------------------------------------------------------- reading the file

function cellText(cell) {
  const v = cell?.value;
  if (v == null) return "";
  if (typeof v === "object") {
    if (v.hyperlink) return String(v.hyperlink); // a clickable link: use its target
    if (v.text) return typeof v.text === "string" ? v.text : (v.text.richText || []).map((t) => t.text).join("");
    if (v.richText) return v.richText.map((t) => t.text).join("");
    if (v.result != null) return String(v.result); // formula
    if (v instanceof Date) return v.toISOString();
  }
  return String(v).trim();
}

const looksLikeUrl = (text) => /^(https?:\/\/|www\.)\S+$/i.test(text.trim());

/** Very small CSV reader: quoted fields, and ";" or "," as separator (Danish Excel uses ";"). */
function parseCsv(text) {
  text = text.replace(/^﻿/, "");
  const firstLine = text.split(/\r?\n/, 1)[0];
  const sep = (firstLine.match(/;/g) || []).length > (firstLine.match(/,/g) || []).length ? ";" : ",";
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (c === '"') quoted = false;
      else field += c;
    } else if (c === '"') quoted = true;
    else if (c === sep) { row.push(field); field = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field); rows.push(row); row = []; field = "";
    } else field += c;
  }
  if (field || row.length) { row.push(field); rows.push(row); }
  return rows.filter((r) => r.some((f) => f.trim()));
}

async function readWorkbook(file) {
  const workbook = new ExcelJS.Workbook();
  if (/\.csv$/i.test(file.name)) {
    const sheet = workbook.addWorksheet("Results");
    parseCsv(await file.text()).forEach((r) => sheet.addRow(r));
  } else if (/\.xlsx$/i.test(file.name)) {
    await workbook.xlsx.load(await file.arrayBuffer());
  } else {
    throw new Error("Please choose an .xlsx or .csv file. Older .xls files can be saved as .xlsx from Excel.");
  }
  const sheet = workbook.worksheets.find((ws) => ws.actualRowCount > 0);
  if (!sheet) throw new Error("This file is empty.");
  return { workbook, sheet };
}

/** Find the column holding the links, and whether row 1 is a header. */
function findUrlColumn(sheet) {
  const counts = new Map();
  const lastRow = Math.min(sheet.rowCount, 60);
  for (let r = 1; r <= lastRow; r++) {
    sheet.getRow(r).eachCell((cell, col) => {
      if (looksLikeUrl(cellText(cell))) counts.set(col, (counts.get(col) || 0) + 1);
    });
  }
  // Prefer a column whose header says "url" or "link"; otherwise the one with the most links.
  const header = sheet.getRow(1);
  const named = [...counts.keys()].find((col) => /url|link|annonce|webadresse/i.test(cellText(header.getCell(col))));
  const best = named ?? [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0];
  if (!best) return null;
  const hasHeader = !looksLikeUrl(cellText(header.getCell(best)));
  return { col: best, hasHeader };
}

async function loadFile(file) {
  showError("");
  if (!file) return;
  let workbook, sheet;
  try {
    ({ workbook, sheet } = await readWorkbook(file));
  } catch (err) {
    showError(err.message || "The file could not be read. Is it a valid .xlsx or .csv file?");
    return;
  }

  const found = findUrlColumn(sheet);
  if (!found) {
    showError("No links were found in this file. Put one link per row in a single column, for example under a header called “URL”.");
    return;
  }
  if (!found.hasHeader) {
    // No header row: insert one so the result columns get names.
    sheet.spliceRows(1, 0, []);
    sheet.getRow(1).getCell(found.col).value = "URL";
  }

  const rows = [];
  for (let r = 2; r <= sheet.rowCount; r++) {
    const url = cellText(sheet.getRow(r).getCell(found.col));
    if (url) rows.push({ rowNumber: r, url, result: null });
  }
  const skipped = Math.max(0, rows.length - MAX_ROWS);
  job = {
    fileName: file.name,
    workbook,
    sheet,
    urlCol: found.col,
    rows: rows.slice(0, MAX_ROWS),
    skipped,
    stopped: false,
    running: false,
  };
  showConfirm();
}

// ---------------------------------------------------------------- confirming

function hostOf(url) {
  try {
    return new URL(/^www\./i.test(url) ? `https://${url}` : url).host.toLowerCase();
  } catch {
    return null;
  }
}

function showConfirm() {
  const supported = job.rows.filter((r) => SUPPORTED_HOSTS.has(hostOf(r.url)));
  const colName = cellText(job.sheet.getRow(1).getCell(job.urlCol)) || `column ${job.urlCol}`;
  const count = job.rows.length;
  const others = count - supported.length;
  $("confirm-title").textContent = `Ready to check ${count} ${count === 1 ? "link" : "links"}`;
  $("confirm-detail").textContent =
    `Found in the “${colName}” column of ${job.fileName}. ` +
    (supported.length === 1 ? "1 is an EDC Erhverv or Savills listing" : `${supported.length} are EDC Erhverv or Savills listings`) +
    (others ? `; ${others} other ${others === 1 ? "link" : "links"} will get a note in the Result column.` : ".") +
    (job.skipped ? ` Only the first ${MAX_ROWS} rows are checked; ${job.skipped} more were left out.` : "");

  // Each site has its own pause, and requests to different sites can follow each other,
  // so the slowest site sets the pace. Plus roughly a second per page to load it.
  const perHost = {};
  supported.forEach((r) => { const h = hostOf(r.url); perHost[h] = (perHost[h] || 0) + 1; });
  const paced = Math.max(0, ...Object.entries(perHost).map(([h, n]) => ((n - 1) * HOST_DELAY_MS[h]) / 1000));
  const seconds = Math.max(paced, supported.length) + 2;
  $("confirm-time").textContent = supported.length
    ? `Takes about ${seconds < 90 ? `${Math.ceil(seconds / 5) * 5} seconds` : `${Math.ceil(seconds / 60)} minutes`}: the checker waits a few seconds between pages to be polite to each site.`
    : "";
  $("start").disabled = false;
  show("step-confirm");
}

// ---------------------------------------------------------------- crawling

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function localResult(url) {
  // Answer obviously unsupported rows here, without calling the server.
  const host = hostOf(url);
  const checked_at = new Date().toISOString();
  if (!host || !looksLikeUrl(url)) return { status: "invalid_url", url, checked_at, message: "This is not a valid web address." };
  if (!SUPPORTED_HOSTS.has(host)) {
    return { status: "unsupported_site", url, checked_at, message: "Only edc.dk and search.savills.com listings are supported." };
  }
  return null;
}

async function fetchResult(url) {
  const target = /^www\./i.test(url) ? `https://${url}` : url;
  try {
    const resp = await fetch(`${API}?url=${encodeURIComponent(target)}`, { signal: AbortSignal.timeout(25000) });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const result = await resp.json();
    // Answers served from Netlify's cache didn't touch the site, so no need to wait.
    result.fromCache = /hit/i.test(resp.headers.get("cache-status") || "");
    return result;
  } catch {
    return { status: "error", url, checked_at: new Date().toISOString(), message: "The checker could not be reached. Try again in a moment." };
  }
}

const RETRY_ROUNDS = 2; // failed links are tried up to 2 more times
const RETRY_PAUSE_MS = [5000, 15000]; // give a busy site a moment before each retry round

async function run() {
  job.running = true;
  job.stopped = false;
  job.retryRound = 0;
  show("step-run");
  setRunning(true);

  const cache = new Map(); // the same link twice in one file is only fetched once
  const lastRequest = new Map();

  async function checkRows(rows) {
    for (const row of rows) {
      if (job.stopped) return;
      let result = cache.get(row.url) || localResult(row.url);
      if (!result) {
        const host = hostOf(row.url);
        const wait = (HOST_DELAY_MS[host] || 2000) - (Date.now() - (lastRequest.get(host) || 0));
        if (wait > 0) await sleep(wait);
        if (job.stopped) return;
        result = await fetchResult(row.url);
        if (!result.fromCache) lastRequest.set(host, Date.now());
      }
      if (result.status !== "error") cache.set(row.url, result);
      row.result = result;
      updateProgress();
    }
  }

  await checkRows(job.rows);
  for (let round = 1; round <= RETRY_ROUNDS && !job.stopped; round++) {
    const failed = job.rows.filter((r) => r.result?.status === "error");
    if (!failed.length) break;
    job.retryRound = round;
    updateProgress();
    await sleep(RETRY_PAUSE_MS[round - 1]);
    await checkRows(failed);
  }

  job.running = false;
  job.retryRound = 0;
  setRunning(false);
  writeResultsToSheet();
}

function resultLabel(result) {
  if (!result) return ["Waiting", "waiting"];
  if (result.status === "ok") return RESULT_LABELS[result.listing?.status === "rented" ? "rented" : "available"];
  return RESULT_LABELS[result.status] || RESULT_LABELS.error;
}

// ---------------------------------------------------------------- writing the sheet

function writeResultsToSheet() {
  const { sheet } = job;
  const header = sheet.getRow(1);
  const existing = new Map();
  header.eachCell((cell, col) => existing.set(cellText(cell), col));
  let nextCol = Math.max(header.cellCount, sheet.columnCount, job.urlCol) + 1;

  for (const [name, getValue, width, numFmt] of OUTPUT_COLUMNS) {
    let col = existing.get(name);
    if (!col) {
      col = nextCol++;
      const cell = header.getCell(col);
      cell.value = name;
      cell.font = { bold: true };
      sheet.getColumn(col).width = width;
    }
    for (const row of job.rows) {
      if (!row.result) continue; // stopped before this row
      const cell = sheet.getRow(row.rowNumber).getCell(col);
      const value = getValue(row.result);
      cell.value = value ?? null;
      if (numFmt && value != null) cell.numFmt = numFmt;
    }
  }
  const urlColumn = sheet.getColumn(job.urlCol);
  if (!urlColumn.width || urlColumn.width < 30) urlColumn.width = 40;
}

async function download() {
  const buffer = await job.workbook.xlsx.writeBuffer();
  const base = job.fileName.replace(/\.(xlsx|csv)$/i, "").replace(/ \(checked\)$/, "");
  saveBlob(new Blob([buffer], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }), `${base} (checked).xlsx`);
}

async function downloadExample() {
  const workbook = new ExcelJS.Workbook();
  const sheet = workbook.addWorksheet("Listings");
  sheet.columns = [
    { header: "Property", key: "name", width: 34 },
    { header: "URL", key: "url", width: 90 },
  ];
  sheet.getRow(1).font = { bold: true };
  EXAMPLE_ROWS.forEach(([name, url]) => sheet.addRow({ name, url }));
  saveBlob(new Blob([await workbook.xlsx.writeBuffer()]), "erhvervsradar-sample.xlsx");
}

function saveBlob(blob, name) {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

// ---------------------------------------------------------------- page updates

function show(stepId) {
  for (const id of ["step-upload", "step-confirm", "step-run"]) $(id).hidden = id !== stepId;
  $(stepId).scrollIntoView({ block: "nearest" });
}

function showError(message) {
  $("upload-error").textContent = message;
  $("upload-error").hidden = !message;
}

function updateProgress() {
  const done = job.rows.filter((r) => r.result).length;
  const total = job.rows.length;
  $("progress").setAttribute("aria-valuemax", String(total));
  $("progress").setAttribute("aria-valuenow", String(done));
  $("progress-bar").style.width = `${(100 * done) / Math.max(total, 1)}%`;

  const failed = job.rows.filter((r) => r.result?.status === "error").length;
  $("progress-text").textContent = job.retryRound
    ? `Trying ${failed} failed ${failed === 1 ? "link" : "links"} again (retry ${job.retryRound} of ${RETRY_ROUNDS})…`
    : `${done} of ${total} checked`;

  $("run-status").textContent = job.running
    ? "Keep this page open. Your file will be ready to download in a moment."
    : `${done} of ${total} links checked`;
  renderSummary();
}

/** One colored badge per result type, e.g. "● 4 Available", always in the same order. */
function renderSummary() {
  const counts = new Map();
  for (const r of job.rows) {
    if (!r.result) continue;
    const [label, tone] = resultLabel(r.result);
    const entry = counts.get(label) || { tone, n: 0 };
    entry.n++;
    counts.set(label, entry);
  }
  const order = Object.values(RESULT_LABELS).map(([label]) => label);
  const list = $("summary");
  list.replaceChildren(
    ...order
      .filter((label) => counts.has(label))
      .map((label) => {
        const { tone, n } = counts.get(label);
        const item = document.createElement("li");
        item.className = `badge badge--${tone}`;
        item.innerHTML = `<span class="badge__count"></span> <span class="badge__label"></span>`;
        item.querySelector(".badge__count").textContent = n;
        item.querySelector(".badge__label").textContent = label;
        return item;
      }),
  );
  list.hidden = job.running || !counts.size;
}

function setRunning(running) {
  $("run-title").textContent = running
    ? "Checking links…"
    : job.stopped
      ? "Stopped: your partial results are ready"
      : "Your Excel file is ready";
  $("run-actions").hidden = !running;
  $("done-actions").hidden = running;
  updateProgress();
}

function restart() {
  if (job) job.stopped = true;
  job = null;
  $("file").value = "";
  show("step-upload");
}

// ---------------------------------------------------------------- events

$("file").addEventListener("change", (e) => loadFile(e.target.files[0]));
// Drop a file anywhere on the page while the upload screen is showing.
const overlay = $("drop-overlay");
const uploadVisible = () => !$("step-upload").hidden;
let dragDepth = 0;
window.addEventListener("dragenter", (e) => {
  if (!uploadVisible() || !e.dataTransfer?.types.includes("Files")) return;
  dragDepth++;
  overlay.hidden = false;
});
window.addEventListener("dragleave", () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) overlay.hidden = true;
});
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => {
  e.preventDefault();
  dragDepth = 0;
  overlay.hidden = true;
  if (uploadVisible() && e.dataTransfer?.files[0]) loadFile(e.dataTransfer.files[0]);
});

$("example").addEventListener("click", downloadExample);
$("start").addEventListener("click", () => run());
$("stop").addEventListener("click", () => { job.stopped = true; });
$("download").addEventListener("click", download);
document.querySelectorAll("[data-restart]").forEach((b) => b.addEventListener("click", restart));
