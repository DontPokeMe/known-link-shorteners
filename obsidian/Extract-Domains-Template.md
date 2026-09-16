# Extract Domains → `candidates.txt`

Templater template for [DontPokeMe/known-link-shorteners](https://github.com/DontPokeMe/known-link-shorteners).

Select any raw text in a note — a clipped article, an RSS dump, a pile of social posts —
run this template, and a **local Ollama model** picks out the hostnames that look like
URL shorteners / link-in-bio / redirector / click-tracking services. Confirmed domains are
appended to `candidates.txt` at the root of your repo clone, where
`scripts/maintain_shorteners.py` will drain and triage them on its next run.

Nothing leaves your machine: the only network calls are to `http://localhost:11434`.

---

## Prerequisites

- **Obsidian desktop** (this template uses Node's `fs`; it will not work on mobile).
- **Templater** community plugin, installed and enabled.
- **Ollama** running locally with at least one model pulled:
  ```bash
  ollama serve            # usually already running as a service
  ollama pull qwen2.5     # or llama3.2, mistral, … any chat model works
  ollama list             # confirm you have at least one
  ```
- A local clone of the repo — you set its path once, in `REPO_PATH` below.

## Install

1. Copy this file into your vault, e.g. `Templates/Extract-Domains-Template.md`.
2. **Settings → Templater → Template folder location** → set it to `Templates`
   (or wherever you put the file).
3. **Settings → Templater → User System Command Functions** is *not* needed — this
   template only uses JavaScript, `fetch` and `require`.
4. Edit `REPO_PATH` in the execution block below so it points at *your* clone.
5. Optional hotkey: **Settings → Hotkeys** → search `Templater: Extract-Domains-Template`
   (the plugin registers one command per template once the template folder is set) and
   bind something like `Ctrl/Cmd + Shift + D`.

## Use

1. Select the raw text in a note (or run it with nothing selected and paste into the prompt).
2. Run the template (hotkey, or command palette → *Templater: Insert Extract-Domains-Template*).
3. Pick the Ollama model — the preferred default is pre-sorted to the top, just hit Enter.
4. Review the domain list in the confirmation prompt. Delete anything you do not want,
   press Enter to write, press Escape to abort.
5. A summary block is inserted at your cursor; a Notice reports extracted / new / duplicate counts.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Notice: *Ollama unreachable* / `ERR_CONNECTION_REFUSED` | Ollama is not running. `ollama serve`, then retry. |
| Notice: *Ollama has no models installed* | `ollama pull qwen2.5` (or any chat model). |
| CORS / `Failed to fetch` although Ollama is up | Ollama must allow Obsidian's origin: `OLLAMA_ORIGINS="app://obsidian.md*" ollama serve`, or on macOS `launchctl setenv OLLAMA_ORIGINS "app://obsidian.md*"` then restart Ollama. |
| Notice: *Could not write candidates.txt* | `REPO_PATH` is wrong, or the folder is not writable. It must be the **repo root** (the folder containing `data/` and `scripts/`), not the `obsidian/` subfolder. |
| Model returns nothing on obvious shorteners | Try a bigger model in the picker; tiny 1B models are over-cautious. The regex safety net only *supplies* candidates, the model still does the filtering. |
| Template inserts nothing / errors in console | Make sure this file is inside the Templater template folder and that you are on desktop Obsidian. |

---

<%*
/* =========================================================================
 * Extract shortener-like domains from the selection → repo candidates.txt
 * Requires: Obsidian desktop + Templater + local Ollama.
 * ====================================================================== */

// >>> SET ME <<< absolute path to your clone of known-link-shorteners.
// This is the REPO ROOT (the folder containing data/, scripts/, candidates.txt),
// not this obsidian/ subfolder. Windows: use forward slashes, e.g. "C:/code/known-link-shorteners".
const REPO_PATH = "/Users/sarah/Documents/Projects/known-link-shorteners";

// If your vault IS the repo, you can drop the Node fs calls further down and use
// Obsidian's own adapter instead, which also works on mobile:
//   await app.vault.adapter.append("candidates.txt", block);
//   const existing = await app.vault.adapter.read("candidates.txt");
// (adapter paths are vault-relative and cannot escape the vault, which is why the
//  default path below goes through require("fs")).

const OLLAMA = "http://localhost:11434";
const MODEL_PREFIXES = ["qwen2.5", "llama3.2", "mistral"]; // preference order, never required
const CHUNK_CHARS = 12000;   // characters of raw text per model call
const MAX_CHUNKS = 8;        // hard stop so a huge selection cannot hang the vault
const TAGS_TIMEOUT_MS = 5000;
const GEN_TIMEOUT_MS = 120000;

// Same pattern the repo's schema + maintain_shorteners.py enforce.
const DOMAIN_RE = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$/;

const notice = (msg, ms) => { try { new Notice(msg, ms || 6000); } catch (e) { console.log("[extract-domains]", msg); } };
const log = (...a) => console.log("[extract-domains]", ...a);

function stamp() {
  try { return tp.date.now("YYYY-MM-DD HH:mm"); }
  catch (e) { return new Date().toISOString().slice(0, 16).replace("T", " "); }
}

async function fetchJSON(url, init, timeoutMs) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, Object.assign({ signal: ctrl.signal }, init || {}));
    if (!res.ok) throw new Error("HTTP " + res.status + " " + res.statusText);
    return await res.json();
  } finally {
    clearTimeout(timer);
  }
}

/* ---------- normalisation --------------------------------------------- */

function normaliseHost(raw) {
  if (raw === null || raw === undefined) return null;
  let s = String(raw).trim().toLowerCase();
  if (!s) return null;
  s = s.replace(/^[<("'\[\u201c\u2018]+/, "").replace(/[>)"'\]\u201d\u2019.,;:!?]+$/, "");
  s = s.replace(/^[a-z][a-z0-9+.\-]*:\/\//, ""); // scheme
  s = s.replace(/^\/\//, "");                     // protocol-relative
  const at = s.lastIndexOf("@");                  // userinfo
  if (at !== -1) s = s.slice(at + 1);
  s = s.split(/[\/?#\\]/)[0];                     // path / query / fragment
  s = s.split(":")[0];                            // port
  s = s.replace(/\.+$/, "");                      // trailing dot
  if (s.startsWith("www.")) s = s.slice(4);       // no www duplicates
  if (!s || s.indexOf(".") === -1) return null;   // bare labels are not domains
  if (s.length > 253) return null;
  if (!DOMAIN_RE.test(s)) return null;
  if (!/\.[a-z]{2,63}$/.test(s)) return null;     // needs a lettered TLD
  return s;
}

// Second source of truth: pull every URL-ish / host-ish token out of the raw text
// ourselves, so the model can only ever *filter*, never invent.
function scanHosts(text) {
  const out = new Set();
  const re = /(?:[a-z][a-z0-9+.\-]*:\/\/|\/\/)?(?:[^\s\/@]+@)?(?:[a-z0-9\u00a1-\uffff](?:[a-z0-9\u00a1-\uffff-]{0,61}[a-z0-9\u00a1-\uffff])?\.)+[a-z]{2,63}\b(?:[:\/][^\s<>"'\)\]]*)?/gi;
  let m;
  while ((m = re.exec(text)) !== null) {
    const host = normaliseHost(m[0]);
    if (host) out.add(host);
  }
  return Array.from(out);
}

/* ---------- 0. selection ------------------------------------------------ */

let rawText = "";
try { rawText = (await tp.file.selection()) || ""; } catch (e) { rawText = ""; }

if (!rawText.trim()) {
  let pasted = null;
  try {
    pasted = await tp.system.prompt("No selection. Paste the raw text to scan:", "", false, true);
  } catch (e) { pasted = null; }
  rawText = (pasted || "").trim();
}

if (!rawText.trim()) {
  notice("Extract domains: nothing to scan — select some text first.");
  return;
}

/* ---------- 1. discover installed models -------------------------------- */

let models = [];
try {
  const tags = await fetchJSON(OLLAMA + "/api/tags", { method: "GET" }, TAGS_TIMEOUT_MS);
  models = (tags && Array.isArray(tags.models) ? tags.models : [])
    .map((m) => (m && (m.name || m.model)) || "")
    .filter((n) => !!n);
} catch (e) {
  log("tags failed", e);
  notice("Ollama unreachable at " + OLLAMA + " — start it with `ollama serve`, then retry.", 9000);
  return;
}

if (!models.length) {
  notice("Ollama is running but has no models installed. Try `ollama pull qwen2.5`.", 9000);
  return;
}

// Sort by preference; the winner lands at index 0 so Enter takes the sensible default.
const rank = (name) => {
  const n = name.toLowerCase();
  const base = n.split(":")[0];
  // Exact base match only: qwen2.5-coder is a code model and must not be
  // treated as the qwen2.5 instruct model (same rule as maintain_shorteners.py).
  for (let i = 0; i < MODEL_PREFIXES.length; i++) {
    if (base === MODEL_PREFIXES[i]) return i;
  }
  // Task-specialised models are usable but rank last.
  if (/coder|code|vision|-vl|math|guard|embed/.test(n)) return MODEL_PREFIXES.length + 1;
  return MODEL_PREFIXES.length; // fall back to installed order
};
const ordered = models.slice().sort((a, b) => rank(a) - rank(b) || models.indexOf(a) - models.indexOf(b));
const labels = ordered.map((n, i) => (i === 0 ? n + "  ← default" : n));

let model = null;
try {
  model = await tp.system.suggester(labels, ordered, false, "Ollama model for domain extraction");
} catch (e) { model = null; }
if (!model) { notice("Extract domains: cancelled."); return; }

/* ---------- 2. ask the model -------------------------------------------- */

function buildPrompt(chunk, hints) {
  return [
    "You are a strict classifier for a database of URL shorteners.",
    "",
    "Read the RAW TEXT below and return ONLY the hostnames that belong to URL shortening,",
    "link-in-bio, redirector or click-tracking services.",
    "",
    "INCLUDE services whose purpose is to shorten, redirect, cloak or track links",
    "(examples of the category: bit.ly, t.co, tinyurl.com, linktr.ee, lnk.to, trk.example.com).",
    "EXCLUDE ordinary websites: news sites, blogs, forums, shops, docs, social networks,",
    "CDNs, image hosts, package registries, and anything you are unsure about.",
    "",
    "Rules for the output:",
    "- bare lowercase hostnames only: no scheme, no path, no query, no port, no userinfo",
    "- strip any leading www. so there are no www duplicates",
    "- deduplicate",
    "- only hostnames that literally appear in the RAW TEXT; never invent or complete one",
    "- if nothing qualifies, return an empty array rather than guessing",
    "",
    'Respond with ONLY this JSON object and nothing else: {"domains": ["bit.ly", "linktr.ee"]}',
    "",
    "HOSTNAMES FOUND IN THE TEXT (classify these; ignore any that are not shorteners):",
    hints.length ? hints.join("\n") : "(none detected)",
    "",
    "RAW TEXT:",
    '"""',
    chunk,
    '"""'
  ].join("\n");
}

function parseDomains(payload) {
  // Ollama returns {response: "<json string>"} even with format:"json".
  let body = payload && typeof payload.response === "string" ? payload.response : "";
  let parsed = null;
  try {
    parsed = JSON.parse(body);
  } catch (e) {
    const m = body.match(/\{[\s\S]*\}/); // tolerate stray prose around the JSON
    if (m) { try { parsed = JSON.parse(m[0]); } catch (e2) { parsed = null; } }
  }
  if (!parsed) return [];
  let list = Array.isArray(parsed) ? parsed
    : Array.isArray(parsed.domains) ? parsed.domains
    : Array.isArray(parsed.shorteners) ? parsed.shorteners
    : [];
  if (typeof list === "string") list = [list];
  return list.filter((d) => typeof d === "string");
}

// Split on line boundaries so a URL is never cut in half.
function chunkText(text, size) {
  const chunks = [];
  const lines = text.split("\n");
  let cur = "";
  for (const line of lines) {
    if (cur.length + line.length + 1 > size && cur.length) { chunks.push(cur); cur = ""; }
    cur += (cur ? "\n" : "") + (line.length > size ? line.slice(0, size) : line);
  }
  if (cur.trim()) chunks.push(cur);
  return chunks.length ? chunks : [text];
}

const scanned = scanHosts(rawText);
if (!scanned.length) {
  notice("Extract domains: no hostnames found in the selected text.");
  return;
}

let chunks = chunkText(rawText, CHUNK_CHARS);
let truncated = false;
if (chunks.length > MAX_CHUNKS) { chunks = chunks.slice(0, MAX_CHUNKS); truncated = true; }

notice("Asking " + model + " about " + scanned.length + " hostname(s)…", 4000);

const accepted = new Set();
let failures = 0;
for (let i = 0; i < chunks.length; i++) {
  const hints = scanHosts(chunks[i]);
  try {
    const payload = await fetchJSON(
      OLLAMA + "/api/generate",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: model,
          prompt: buildPrompt(chunks[i], hints),
          stream: false,
          format: "json",
          options: { temperature: 0 }
        })
      },
      GEN_TIMEOUT_MS
    );
    for (const d of parseDomains(payload)) {
      const host = normaliseHost(d);
      // Safety net: the model may only keep what we actually saw in the text.
      if (host && scanned.indexOf(host) !== -1) accepted.add(host);
    }
  } catch (e) {
    failures++;
    log("generate failed on chunk " + (i + 1), e);
  }
}

if (failures === chunks.length) {
  notice("Extract domains: every request to Ollama failed — see the developer console.", 9000);
  return;
}

let extracted = Array.from(accepted).sort();
if (!extracted.length) {
  notice("Extract domains: " + model + " found no shortener-like domains in " + scanned.length + " hostname(s).", 8000);
  return;
}

/* ---------- 3. confirm with the user ------------------------------------ */

let confirmed = null;
try {
  confirmed = await tp.system.prompt(
    "Append these " + extracted.length + " domain(s) to candidates.txt? Edit to remove any, Esc to cancel:",
    extracted.join(", "),
    false,
    true
  );
} catch (e) { confirmed = null; }

if (confirmed === null || confirmed === undefined || !String(confirmed).trim()) {
  notice("Extract domains: cancelled, nothing written.");
  return;
}

const finalList = [];
const seen = new Set();
for (const piece of String(confirmed).split(/[\s,;]+/)) {
  const host = normaliseHost(piece);
  if (host && !seen.has(host)) { seen.add(host); finalList.push(host); }
}
finalList.sort();

if (!finalList.length) {
  notice("Extract domains: nothing valid left after editing.");
  return;
}

/* ---------- 4. append to <REPO_PATH>/candidates.txt ---------------------- */

let fs, nodePath;
try {
  fs = require("fs");
  nodePath = require("path");
} catch (e) {
  notice("Extract domains: Node fs is unavailable (desktop Obsidian only).", 9000);
  return;
}

const candidatesPath = nodePath.join(REPO_PATH, "candidates.txt");
let existingRaw = "";
let fileExisted = false;
try {
  if (fs.existsSync(candidatesPath)) {
    existingRaw = fs.readFileSync(candidatesPath, "utf8");
    fileExisted = true;
  } else if (!fs.existsSync(REPO_PATH)) {
    notice("Extract domains: REPO_PATH does not exist — " + REPO_PATH, 9000);
    return;
  }
} catch (e) {
  log("read failed", e);
  notice("Could not read " + candidatesPath + " — check REPO_PATH and permissions.", 9000);
  return;
}

// Existing entries are normalised the same way, so bit.ly / BIT.LY / https://bit.ly/x all match.
const known = new Set();
for (const line of existingRaw.split(/\r?\n/)) {
  const trimmed = line.trim();
  if (!trimmed || trimmed.startsWith("#")) continue;
  const host = normaliseHost(trimmed);
  if (host) known.add(host);
}

const toAdd = finalList.filter((d) => !known.has(d));
const dupes = finalList.length - toAdd.length;

if (toAdd.length) {
  const header = "# added from Obsidian on " + stamp();
  const prefix = (!fileExisted || existingRaw === "" || existingRaw.endsWith("\n")) ? "" : "\n";
  const block = prefix + header + "\n" + toAdd.join("\n") + "\n";
  try {
    fs.appendFileSync(candidatesPath, block, "utf8");
  } catch (e) {
    log("append failed", e);
    notice("Could not write " + candidatesPath + " — check REPO_PATH and permissions.", 9000);
    return;
  }
}

/* ---------- 5. report ---------------------------------------------------- */

notice(
  "Extract domains: " + extracted.length + " extracted, " +
  toAdd.length + " new, " + dupes + " already present." +
  (truncated ? " (selection truncated to " + MAX_CHUNKS + " chunks)" : "") +
  (failures ? " " + failures + " chunk(s) failed." : ""),
  9000
);

const when = stamp();
let summary = "";
summary += "> [!abstract]- Shortener candidates — " + when + "\n";
summary += "> model: `" + model + "` · scanned " + scanned.length + " hostname(s) · " +
           extracted.length + " extracted · **" + toAdd.length + " new** · " + dupes + " already present\n";
summary += "> file: `" + candidatesPath + "`\n";
if (toAdd.length) {
  summary += ">\n";
  for (const d of toAdd) summary += "> - `" + d + "`\n";
} else {
  summary += ">\n> _Nothing new — every extracted domain was already in candidates.txt._\n";
}
if (truncated) summary += ">\n> _Note: selection was longer than " + (CHUNK_CHARS * MAX_CHUNKS) + " characters and was truncated._\n";
if (failures) summary += ">\n> _Note: " + failures + " chunk(s) failed; see the developer console._\n";

tR += summary;
%>
