/**
 * Load the example notes (from scripts/export_examples.js) into this install, so the
 * landing page has examples. Runs as the `examples` service on every start; records are
 * upserted by id and files overwritten, so running it again changes nothing.
 *
 * No account is created: a shared note is shown without its owner, and continuing one
 * copies it into the account of whoever continues it.
 */
const fs = require('fs');
const path = require('path');
const { MongoClient, BSON } = require('mongodb');

const BUNDLE = process.env.EXAMPLES_DIR || '/examples';
const PROJECTS = process.env.PROJECTS_DIR || '/app/projects';

/** Production details that mean nothing here. */
function localise(name, doc) {
  if (name === 'conversations') {
    return { ...doc, chatProjectId: null, _meiliIndex: false };
  }
  if (name === 'messages') {
    return { ...doc, _meiliIndex: false };
  }
  return doc;
}

async function main() {
  const dbDir = path.join(BUNDLE, 'db');
  if (!fs.existsSync(dbDir)) {
    console.log(`No examples in ${BUNDLE}; nothing to load.`);
    return;
  }
  const client = await MongoClient.connect(process.env.MONGO_URI);
  const db = client.db();
  const loaded = {};
  for (const file of fs.readdirSync(dbDir).filter((f) => f.endsWith('.json'))) {
    const name = path.basename(file, '.json');
    const docs = BSON.EJSON.parse(fs.readFileSync(path.join(dbDir, file), 'utf8'), { relaxed: false });
    if (!docs.length) {
      continue;
    }
    await db.collection(name).bulkWrite(
      docs.map((doc) => ({
        replaceOne: { filter: { _id: doc._id }, replacement: localise(name, doc), upsert: true },
      })),
    );
    loaded[name] = docs.length;
  }
  await client.close();

  const files = path.join(BUNDLE, 'projects');
  if (fs.existsSync(files)) {
    fs.cpSync(files, PROJECTS, { recursive: true, force: true });
  }
  console.log(`Examples loaded: ${JSON.stringify(loaded)}`);
}

main().catch((error) => {
  console.error(`Examples not loaded: ${error.message}`);
  process.exit(1);
});
