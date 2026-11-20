/**
 * Export a set of public example notes for local installs.
 *
 * Run inside the production LibreChat container (it has MONGO_URI, the engine URL and the
 * engine secret); it only reads. Writes OUT_DIR/db/<collection>.json (Extended JSON) and
 * OUT_DIR/projects/<conversationId>/<file> for every plot and table the notes show, then
 * scripts/import_examples.js loads them into a local install.
 *
 *   docker cp scripts/export_examples.js LibreChat:/tmp/ && \
 *   docker exec -w /app/api -e SHARE_IDS="id1 id2" -e OUT_DIR=/tmp/examples LibreChat \
 *     node /tmp/export_examples.js
 *
 * The notes' owner is replaced by one local "OmicsBase examples" account, so no production
 * account id or e-mail leaves; attached files (uploads) are not exported.
 */
const fs = require('fs');
const path = require('path');
const { MongoClient, BSON, ObjectId } = require('mongodb');

const { EJSON } = BSON;

const EXAMPLES_USER = '6f6d69637362617365000001';
const SHARE_IDS = (process.env.SHARE_IDS || '').split(/\s+/).filter(Boolean);
const OUT_DIR = process.env.OUT_DIR || '/tmp/examples';
const ENGINE_URL = (process.env.NOTEKERNEL_URL || 'http://engine:8001').replace(/\/$/, '');
const SECRET = process.env.OMICSBASE_ENGINE_SECRET || process.env.OMICSBASE_AUTH_SECRET || '';
/** Per-conversation collections, matched on conversationId. */
const NOTE_COLLECTIONS = [
  'notecells',
  'notecellrevisions',
  'notecellexecutions',
  'noteexecutionartifacts',
  'noteexecutionevents',
];

/** Replace every reference to an owner id (string or ObjectId) with the examples account. */
function anonymise(doc, owners) {
  if (doc instanceof ObjectId) {
    return owners.has(doc.toHexString()) ? new ObjectId(EXAMPLES_USER) : doc;
  }
  if (typeof doc === 'string') {
    return owners.has(doc) ? EXAMPLES_USER : doc;
  }
  if (Array.isArray(doc)) {
    return doc.map((value) => anonymise(value, owners));
  }
  if (doc && typeof doc === 'object' && !(doc instanceof Date) && !Buffer.isBuffer(doc)) {
    return Object.fromEntries(Object.entries(doc).map(([k, v]) => [k, anonymise(v, owners)]));
  }
  return doc;
}

async function fetchFile(conversationId, relativePath) {
  const url = `${ENGINE_URL}/projects/${encodeURIComponent(conversationId)}/${relativePath
    .split('/')
    .map(encodeURIComponent)
    .join('/')}`;
  const response = await fetch(url, { headers: { 'X-Internal-Secret': SECRET }, redirect: 'error' });
  if (!response.ok) {
    throw new Error(`${response.status} for ${conversationId}/${relativePath}`);
  }
  return Buffer.from(await response.arrayBuffer());
}

async function main() {
  if (!SHARE_IDS.length) {
    throw new Error('Set SHARE_IDS to the share ids to export');
  }
  const client = await MongoClient.connect(process.env.MONGO_URI);
  const db = client.db();
  const out = {};
  const add = (name, docs) => (out[name] = (out[name] || []).concat(docs));

  const shares = await db.collection('sharedlinks').find({ shareId: { $in: SHARE_IDS } }).toArray();
  const missing = SHARE_IDS.filter((id) => !shares.some((s) => s.shareId === id));
  if (missing.length) {
    throw new Error(`Shares not found: ${missing.join(' ')}`);
  }
  const owners = new Set(shares.map((s) => String(s.user)));
  const conversationIds = shares.map((s) => s.conversationId);

  add('sharedlinks', shares);
  add('conversations', await db.collection('conversations').find({ conversationId: { $in: conversationIds } }).toArray());
  add('messages', await db.collection('messages').find({ conversationId: { $in: conversationIds } }).toArray());
  for (const name of NOTE_COLLECTIONS) {
    add(name, await db.collection(name).find({ conversationId: { $in: conversationIds } }).toArray());
  }
  add('discover_examples', await db.collection('discover_examples').find({ shareId: { $in: SHARE_IDS } }).toArray());
  add(
    'aclentries',
    await db
      .collection('aclentries')
      .find({ resourceId: { $in: shares.map((s) => s._id) }, principalType: 'public' })
      .toArray(),
  );

  fs.mkdirSync(path.join(OUT_DIR, 'db'), { recursive: true });
  for (const [name, docs] of Object.entries(out)) {
    const clean = docs.map((doc) => anonymise(doc, owners));
    fs.writeFileSync(path.join(OUT_DIR, 'db', `${name}.json`), EJSON.stringify(clean, null, 0, { relaxed: false }));
  }

  // Every plot and table the notes show, plus each example's cover. Console output is
  // already in each execution's stdout and markdown.
  const files = new Set(
    out.noteexecutionartifacts
      .filter((a) => a.artifactType !== 'console')
      .map((a) => `${a.conversationId}\n${a.relativePath}`),
  );
  for (const meta of out.discover_examples) {
    const share = shares.find((s) => s.shareId === meta.shareId);
    if (typeof meta.cover === 'string') {
      files.add(`${share.conversationId}\n${meta.cover}`);
    }
  }
  let bytes = 0;
  const failed = [];
  for (const entry of files) {
    const [conversationId, relativePath] = entry.split('\n');
    const target = path.join(OUT_DIR, 'projects', conversationId, relativePath);
    if (!target.startsWith(path.join(OUT_DIR, 'projects') + path.sep)) {
      continue;
    }
    try {
      const body = await fetchFile(conversationId, relativePath);
      fs.mkdirSync(path.dirname(target), { recursive: true });
      fs.writeFileSync(target, body);
      bytes += body.length;
    } catch (error) {
      failed.push(error.message);
    }
  }

  const manifest = {
    exportedAt: new Date().toISOString(),
    examplesUser: EXAMPLES_USER,
    shares: shares.map((s) => ({ shareId: s.shareId, title: s.title, conversationId: s.conversationId })),
    counts: Object.fromEntries(Object.entries(out).map(([k, v]) => [k, v.length])),
    files: files.size - failed.length,
    bytes,
    failed,
  };
  fs.writeFileSync(path.join(OUT_DIR, 'manifest.json'), JSON.stringify(manifest, null, 2));
  console.log(JSON.stringify({ ...manifest, shares: manifest.shares.length }, null, 2));
  await client.close();
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
