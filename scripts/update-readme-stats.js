const fs = require('fs');
const path = require('path');

const workspace = path.resolve(__dirname, '..');
const dataDir = path.join(workspace, 'data');
const readmePath = path.join(workspace, 'README.md');
const updateDate = process.argv[2] || new Date().toISOString().slice(0, 10);

const countEntries = (file) => {
  const data = JSON.parse(fs.readFileSync(path.join(dataDir, file), 'utf8'));
  if (!Array.isArray(data)) {
    throw new Error(`${file} must contain a JSON array`);
  }
  return data.length;
};

const stats = [
  `- Total shorteners: ${countEntries('shorteners.json').toLocaleString('en-US')}`,
  `- Total redirectors: ${countEntries('redirectors.json').toLocaleString('en-US')}`,
  `- Total tracking domains: ${countEntries('tracking.json').toLocaleString('en-US')}`,
  `- Last updated: ${updateDate}`,
].join('\n');

const readme = fs.readFileSync(readmePath, 'utf8');
const next = readme.replace(
  /- Total shorteners: .*\r?\n- Total redirectors: .*\r?\n- Total tracking domains: .*\r?\n- Last updated: .*/,
  stats,
);

if (next === readme) {
  throw new Error('Could not find README statistics block to update');
}

fs.writeFileSync(readmePath, next);
console.log('README statistics updated');
