const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');

function setup(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'note-bridge-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const source = path.join(root, 'upload.Rds');
  fs.writeFileSync(source, 'original');
  const file = { file_id: 'upload-1', filename: 'tse.Rds', path: source, user: 'owner' };
  let filter;
  const database = {
    getMessages: async (query) => {
      assert.deepEqual(JSON.parse(JSON.stringify(query)), { conversationId: 'thread-1', user: 'owner' });
      return [{ files: [{ file_id: 'upload-1' }] }];
    },
    getFiles: async (query) => { filter = query; return [file]; },
  };
  const module = { exports: {} };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../librechat/api/server/services/NoteCells/noteDataBridge.js'), 'utf8'), {
    module, process: { env: { PROJECTS_DIR: path.join(root, 'projects') } }, Buffer,
    require: (name) => {
      if (name === '~/models') return database;
      if (name === '../../../config/paths') return { uploads: root };
      if (name === '@librechat/data-schemas') return { logger: { info() {}, warn() {}, error() {} } };
      return require(name);
    },
  });
  return { ...module.exports, root, source, file, filter: () => filter };
}

test('copies message-linked uploads with owner filtering and preserves working edits', async (t) => {
  const bridge = setup(t);
  await bridge.bridgeConversationFiles('thread-1', 'owner');
  const target = path.join(bridge.root, 'projects/thread-1/data/tse.Rds');
  assert.equal(fs.readFileSync(target, 'utf8'), 'original');
  assert.equal(bridge.filter().user, 'owner');
  assert.equal(bridge.filter().$or[2].file_id.$in[0], 'upload-1');
  fs.writeFileSync(target, 'analysis edit');
  await bridge.bridgeConversationFiles('thread-1', 'owner');
  assert.equal(fs.readFileSync(target, 'utf8'), 'analysis edit');
});

test('preparation reports missing uploads instead of silently running R', async (t) => {
  const bridge = setup(t);
  fs.unlinkSync(bridge.source);
  await assert.rejects(bridge.bridgeConversationFiles('thread-1', 'owner'), /unavailable/);
});

test('rejects traversal in attachment names', async (t) => {
  const bridge = setup(t);
  bridge.file.filename = '../escape.Rds';
  await assert.rejects(bridge.bridgeConversationFiles('thread-1', 'owner'), /Invalid attachment/);
});
