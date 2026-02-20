const fs = require('fs');

const files = ['shorteners.json', 'redirectors.json', 'tracking.json'];
const allDomains = new Set();
let duplicates = false;

files.forEach(file => {
  const data = JSON.parse(fs.readFileSync(`data/${file}`, 'utf8'));

  data.forEach(entry => {
    if (allDomains.has(entry.domain)) {
      console.error(`❌ Duplicate domain found: ${entry.domain} in ${file}`);
      duplicates = true;
    } else {
      allDomains.add(entry.domain);
    }
  });
});

if (duplicates) {
  process.exit(1);
} else {
  console.log('✅ No duplicates found');
}
