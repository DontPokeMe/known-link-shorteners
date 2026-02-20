const fs = require('fs');

const DOMAIN_PATTERN = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$/;

const files = ['shorteners.json', 'redirectors.json', 'tracking.json'];
let invalid = false;

files.forEach(file => {
  const path = `data/${file}`;
  if (!fs.existsSync(path)) return;

  const data = JSON.parse(fs.readFileSync(path, 'utf8'));
  let fileInvalid = false;

  data.forEach((entry, index) => {
    const domain = entry.domain;
    if (typeof domain !== 'string') {
      console.error(`❌ ${file}[${index}]: domain must be a string`);
      invalid = true;
      fileInvalid = true;
      return;
    }
    if (domain !== domain.toLowerCase()) {
      console.error(`❌ ${file}[${index}]: domain must be lowercase: "${domain}"`);
      invalid = true;
      fileInvalid = true;
    }
    if (domain.includes('/') || domain.includes(':')) {
      console.error(`❌ ${file}[${index}]: domain must not contain path or protocol: "${domain}"`);
      invalid = true;
      fileInvalid = true;
    }
    if (!DOMAIN_PATTERN.test(domain)) {
      console.error(`❌ ${file}[${index}]: invalid domain format: "${domain}"`);
      invalid = true;
      fileInvalid = true;
    }
  });

  if (!fileInvalid) {
    console.log(`✅ ${file} domain format OK`);
  }
});

if (invalid) {
  process.exit(1);
}
