/**
 * Import domains from PeterDaveHello/url-shorteners list.
 * Existing entries are left unchanged; new domains get the PeterDaveHello note.
 * Usage: node scripts/import-peterdavehello.js [list-url-or-path]
 * Default URL: https://raw.githubusercontent.com/PeterDaveHello/url-shorteners/master/list
 */
const fs = require('fs');
const path = require('path');
const https = require('https');

const LIST_URL = 'https://raw.githubusercontent.com/PeterDaveHello/url-shorteners/master/list';
const EVIDENCE = 'https://github.com/PeterDaveHello/url-shorteners';
const NOTE = 'Known Shortener - PeterDaveHello List (Sept 2025)';
const DOMAIN_PATTERN = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$/;
const ADDED_AT = new Date().toISOString().slice(0, 10);

function fetchUrl(url) {
  return new Promise((resolve, reject) => {
    https.get(url, (res) => {
      let data = '';
      res.on('data', (ch) => { data += ch; });
      res.on('end', () => resolve(data));
    }).on('error', reject);
  });
}

function parseList(text) {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim().toLowerCase())
    .filter((line) => line && !line.startsWith('#'))
    .filter((d) => DOMAIN_PATTERN.test(d));
}

async function main() {
  const root = path.resolve(__dirname, '..');
  const dataPath = path.join(root, 'data', 'shorteners.json');
  const listArg = process.argv[2] || LIST_URL;

  let listText;
  if (listArg.startsWith('http://') || listArg.startsWith('https://')) {
    listText = await fetchUrl(listArg);
  } else {
    listText = fs.readFileSync(path.isAbsolute(listArg) ? listArg : path.join(root, listArg), 'utf8');
  }

  const incoming = [...new Set(parseList(listText))];
  const current = JSON.parse(fs.readFileSync(dataPath, 'utf8'));
  const existingDomains = new Set(current.map((e) => e.domain));

  const toAdd = incoming.filter((d) => !existingDomains.has(d));
  const newEntries = toAdd.map((domain) => ({
    domain,
    type: 'shortener',
    status: 'active',
    added_at: ADDED_AT,
    source: 'internal',
    evidence: [EVIDENCE],
    notes: NOTE,
  }));

  const merged = current.concat(newEntries);
  merged.sort((a, b) => (a.domain < b.domain ? -1 : a.domain > b.domain ? 1 : 0));

  fs.writeFileSync(dataPath, JSON.stringify(merged, null, 2) + '\n');
  console.log(`Added ${newEntries.length} new shorteners from PeterDaveHello list. Total: ${merged.length}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
