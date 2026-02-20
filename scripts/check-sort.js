const fs = require('fs');

const files = ['shorteners.json', 'redirectors.json', 'tracking.json'];
let unsorted = false;

files.forEach(file => {
  const data = JSON.parse(fs.readFileSync(`data/${file}`, 'utf8'));
  const domains = data.map(e => e.domain);
  const sorted = [...domains].sort();

  if (JSON.stringify(domains) !== JSON.stringify(sorted)) {
    console.error(`❌ ${file} is not sorted alphabetically`);
    console.error('Expected order:', sorted.slice(0, 5).join(', '), '...');
    console.error('Actual order:', domains.slice(0, 5).join(', '), '...');
    unsorted = true;
  } else {
    console.log(`✅ ${file} is sorted correctly`);
  }
});

if (unsorted) {
  process.exit(1);
}
