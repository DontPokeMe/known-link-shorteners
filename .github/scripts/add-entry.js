/**
 * Add submission to the appropriate data file and sort by domain.
 * Reads submission from submission.json (created by parse-issue.js).
 */
const fs = require('fs');
const path = require('path');

const workspace = process.env.GITHUB_WORKSPACE || path.resolve(__dirname, '../..');
const submissionPath = process.env.SUBMISSION_JSON || path.join(workspace, 'submission.json');
const dataDir = path.join(workspace, 'data');

if (!fs.existsSync(submissionPath)) {
  console.error('Missing submission.json');
  process.exit(1);
}

const submission = JSON.parse(fs.readFileSync(submissionPath, 'utf8'));
const fileByType = {
  shortener: 'shorteners.json',
  redirector: 'redirectors.json',
  tracking: 'tracking.json',
};

const dataFile = fileByType[submission.type];
if (!dataFile) {
  console.error('Unknown type:', submission.type);
  process.exit(1);
}

const filePath = path.join(dataDir, dataFile);
if (!fs.existsSync(filePath)) {
  console.error('Data file not found:', filePath);
  process.exit(1);
}

const evidence = Array.isArray(submission.evidence) ? submission.evidence : [submission.evidence].filter(Boolean);
const addedAt = new Date().toISOString().slice(0, 10); // YYYY-MM-DD

const entry = {
  domain: submission.domain.trim().toLowerCase(),
  type: submission.type,
  status: submission.status || 'active',
  added_at: addedAt,
  source: 'submission',
  evidence: evidence.map((u) => String(u).trim()),
  notes: submission.notes ? String(submission.notes).slice(0, 500) : undefined,
};
if (entry.notes === '') delete entry.notes;

const data = JSON.parse(fs.readFileSync(filePath, 'utf8'));
data.push(entry);
data.sort((a, b) => (a.domain < b.domain ? -1 : a.domain > b.domain ? 1 : 0));

fs.writeFileSync(filePath, JSON.stringify(data, null, 2) + '\n');
console.log('Added', entry.domain, 'to', dataFile);
