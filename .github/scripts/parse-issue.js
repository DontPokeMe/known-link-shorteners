/**
 * Parse issue body (form or API-created) and extract submission data.
 * Writes submission.json for add-entry.js and sets GITHUB_OUTPUT for the workflow.
 */
const fs = require('fs');

const issueBody = process.env.ISSUE_BODY || '';
if (!issueBody) {
  console.error('Missing ISSUE_BODY');
  process.exit(1);
}

// Support both API-created format (**Domain**: `x`) and form-created format (### Domain\n\nx)
function escapeRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
function getBlock(body, label) {
  const esc = escapeRe(label);
  // Form: ### Domain\n\nvalue
  const formRe = new RegExp(`###\\s*${esc}\\s*\\n+\\s*([^#]*?)($|\\n\\s*###)`, 'i');
  const form = body.match(formRe);
  if (form) return form[1].trim();
  // API: **Domain**: `value` or **Domain**: value
  const apiRe = new RegExp(`\\*\\*${esc}\\*\\*:\\s*\`([^`]+)\``);
  const api1 = body.match(apiRe);
  if (api1) return api1[1].trim();
  const apiRe2 = new RegExp(`\\*\\*${esc}\\*\\*:\\s*([^\n*]+)`);
  const api2 = body.match(apiRe2);
  if (api2) return api2[1].trim();
  return null;
}

const domain = getBlock(issueBody, 'Domain') || issueBody.match(/\*\*Domain\*\*:\s*`(.+?)`/)?.[1]?.trim();
const type = getBlock(issueBody, 'Type') || issueBody.match(/\*\*Type\*\*:\s*(\w+)/)?.[1]?.trim();
const status = getBlock(issueBody, 'Status') || issueBody.match(/\*\*Status\*\*:\s*(\w+)/)?.[1]?.trim() || 'active';
let evidenceRaw = getBlock(issueBody, 'Evidence (URLs)') || getBlock(issueBody, 'Evidence') || issueBody.match(/\*\*Evidence\*\*:\s*([^\n*]+)/)?.[1]?.trim() || '';
const notes = getBlock(issueBody, 'Notes') || issueBody.match(/\*\*Notes\*\*:\s*([^\n]*)/)?.[1]?.trim() || '';

// Evidence: one or more URLs (newline or space separated)
const evidence = evidenceRaw
  ? evidenceRaw.split(/[\n\s]+/).map((u) => u.trim()).filter(Boolean)
  : [];

if (!domain || !type || evidence.length === 0) {
  console.error('Failed to parse required fields from issue (domain, type, evidence)');
  console.error('Domain:', domain, 'Type:', type, 'Evidence count:', evidence.length);
  process.exit(1);
}

const validTypes = ['shortener', 'redirector', 'tracking'];
const validStatus = ['active', 'defunct', 'malicious'];
if (!validTypes.includes(type)) {
  console.error('Invalid type:', type);
  process.exit(1);
}
if (!validStatus.includes(status)) {
  console.error('Invalid status:', status);
  process.exit(1);
}

const data = {
  domain: domain.toLowerCase().trim(),
  type,
  status,
  evidence,
  notes: notes.slice(0, 500),
};

// Write for add-entry.js
const outPath = process.env.SUBMISSION_JSON || `${process.env.GITHUB_WORKSPACE || '.'}/submission.json`;
fs.writeFileSync(outPath, JSON.stringify(data, null, 2));
console.log('Wrote submission to', outPath);

// GitHub Actions outputs (use delimiter for multiline/special chars)
const ghOut = process.env.GITHUB_OUTPUT;
if (ghOut) {
  const delim = (name) => `submission_${name}_EOF`;
  const append = (name, value) => {
    const v = String(value ?? '');
    fs.appendFileSync(ghOut, `${name}<<${delim(name)}\n${v}\n${delim(name)}\n`);
  };
  append('domain', data.domain);
  append('type', data.type);
  append('status', data.status);
  append('evidence', data.evidence[0] || '');
  append('notes', data.notes);
}
