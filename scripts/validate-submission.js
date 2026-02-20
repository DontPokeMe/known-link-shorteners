/**
 * Validate parsed submission: domain format, type, status, evidence URLs, duplicates.
 * Reads submission from submission.json (created by parse-issue.js).
 * Exit 1 on validation failure.
 */
const fs = require('fs');
const path = require('path');

const workspace = process.env.GITHUB_WORKSPACE || path.resolve(__dirname, '..');
const submissionPath = process.env.SUBMISSION_JSON || path.join(workspace, 'submission.json');

if (!fs.existsSync(submissionPath)) {
  console.error('Missing submission.json (run parse-issue.js first)');
  process.exit(1);
}

const submission = JSON.parse(fs.readFileSync(submissionPath, 'utf8'));
const DOMAIN_PATTERN = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$/;
const URL_PATTERN = /^https?:\/\/[^\s]+$/i;

let failed = false;

// Domain
if (!submission.domain || typeof submission.domain !== 'string') {
  console.error('❌ Missing or invalid domain');
  failed = true;
} else {
  const d = submission.domain.trim().toLowerCase();
  if (d.includes('/') || d.includes(':')) {
    console.error('❌ Domain must not contain path or protocol:', d);
    failed = true;
  } else if (!DOMAIN_PATTERN.test(d)) {
    console.error('❌ Invalid domain format:', d);
    failed = true;
  }
}

// Type
const validTypes = ['shortener', 'redirector', 'tracking'];
if (!validTypes.includes(submission.type)) {
  console.error('❌ Invalid type. Must be one of:', validTypes.join(', '));
  failed = true;
}

// Status
const validStatus = ['active', 'defunct', 'malicious'];
if (!validStatus.includes(submission.status)) {
  console.error('❌ Invalid status. Must be one of:', validStatus.join(', '));
  failed = true;
}

// Evidence: at least one URL
const evidence = Array.isArray(submission.evidence) ? submission.evidence : [submission.evidence].filter(Boolean);
if (evidence.length === 0) {
  console.error('❌ At least one evidence URL required');
  failed = true;
} else {
  for (const url of evidence) {
    if (!url || !URL_PATTERN.test(String(url).trim())) {
      console.error('❌ Invalid evidence URL:', url);
      failed = true;
    }
  }
}

// Duplicate check
const dataDir = path.join(workspace, 'data');
const files = ['shorteners.json', 'redirectors.json', 'tracking.json'];
const domain = (submission.domain || '').trim().toLowerCase();

for (const file of files) {
  const p = path.join(dataDir, file);
  if (!fs.existsSync(p)) continue;
  const data = JSON.parse(fs.readFileSync(p, 'utf8'));
  for (const entry of data) {
    if (entry.domain && entry.domain.toLowerCase() === domain) {
      console.error(`❌ Domain already exists in ${file}: ${domain}`);
      failed = true;
      break;
    }
  }
}

if (failed) process.exit(1);
console.log('✅ Submission validation passed');
