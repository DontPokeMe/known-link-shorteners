const Ajv = require('ajv');
const addFormats = require('ajv-formats');
const fs = require('fs');

const ajv = new Ajv({ allErrors: true });
addFormats(ajv);

const schema = JSON.parse(fs.readFileSync('schema/shortener.schema.json', 'utf8'));
const files = ['shorteners.json', 'redirectors.json', 'tracking.json'];

let errors = false;

files.forEach(file => {
  const data = JSON.parse(fs.readFileSync(`data/${file}`, 'utf8'));
  const validate = ajv.compile(schema);

  if (!validate(data)) {
    console.error(`❌ Validation failed for ${file}:`);
    console.error(validate.errors);
    errors = true;
  } else {
    console.log(`✅ ${file} is valid`);
  }
});

if (errors) {
  process.exit(1);
}
