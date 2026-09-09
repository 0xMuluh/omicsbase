const fs = require('fs');
const path = require('path');
const paths = require('../../../config/paths');
const { logger } = require('@librechat/data-schemas');
let db = null;
function getDb() {
  if (db) {
    return db;
  }
  try {
    db = require('~/models');
  } catch {
    try {
      db = require('../../../models');
    } catch {
      db = null;
    }
  }
  return db;
}


const DEFAULT_INLINE_TEXT_LIMIT_BYTES = 51200; // 50 KB

const DEFAULT_DATA_EXTENSIONS = new Set([
  '.csv',
  '.tsv',
  '.tab',
  '.rds',
  '.rda',
  '.rdata',
  '.parquet',
  '.feather',
  '.arrow',
  '.h5ad',
  '.h5',
  '.hdf5',
  '.xlsx',
  '.xls',
  '.fasta',
  '.fa',
  '.fna',
  '.fastq',
  '.fq',
  '.bam',
  '.sam',
  '.cram',
  '.vcf',
  '.bcf',
  '.gtf',
  '.gff',
  '.gff3',
  '.bed',
  '.wig',
  '.bw',
  '.bigwig',
  '.mtx',
  '.loom',
  '.raw',
  '.mzml',
  '.mzxml',
  '.mgf',
  '.tar',
  '.gz',
  '.zip',
  '.bz2',
  '.xz',
]);

function getConfiguredDataExtensions() {
  const envExts = process.env.NOTE_DATA_EXTENSIONS;
  if (!envExts) {
    return DEFAULT_DATA_EXTENSIONS;
  }
  const set = new Set(DEFAULT_DATA_EXTENSIONS);
  for (const ext of envExts.split(',')) {
    const trimmed = ext.trim().toLowerCase();
    if (trimmed) {
      set.add(trimmed.startsWith('.') ? trimmed : `.${trimmed}`);
    }
  }
  return set;
}

/**
 * Inspects a byte buffer to check if it contains binary content (null bytes or excessive non-printable characters).
 */
function isBinaryBuffer(buffer) {
  if (!buffer || buffer.length === 0) {
    return false;
  }
  const len = Math.min(buffer.length, 1024);
  let nonPrintable = 0;
  for (let i = 0; i < len; i++) {
    const byte = buffer[i];
    if (byte === 0x00) {
      return true; // Null byte indicates binary format (e.g. RDS, BAM, H5AD, zip)
    }
    if ((byte < 0x20 || byte === 0x7f) && byte !== 0x09 && byte !== 0x0a && byte !== 0x0d) {
      nonPrintable++;
    }
  }
  return nonPrintable / len > 0.3;
}

/**
 * Sniffs the first lines of text to check if it has consistent tabular delimiters (tabs, commas, or semicolons).
 */
function isTabularText(textSample) {
  if (!textSample || typeof textSample !== 'string') {
    return false;
  }
  const lines = textSample.split(/\r?\n/).filter((l) => l.trim().length > 0).slice(0, 5);
  if (lines.length < 2) {
    return false;
  }
  for (const delimiter of ['\t', ',', ';']) {
    const counts = lines.map((l) => l.split(delimiter).length);
    if (counts[0] > 1 && counts.every((c) => c === counts[0])) {
      return true;
    }
  }
  return false;
}

function getProjectsDir() {
  return process.env.PROJECTS_DIR || path.resolve(__dirname, '../../../../projects');
}

/**
 * Dynamically determines whether an uploaded file should be treated as a workspace data file.
 * Checks:
 * 1. Configured / known scientific data extensions.
 * 2. File size > 50 KB (INLINE_TEXT_LIMIT_BYTES) to protect context window limits.
 * 3. Binary content detection (null bytes).
 * 4. Tabular delimiter sniffing on text samples.
 */
function isDataFile(fileOrPath, options = {}) {
  if (!fileOrPath) {
    return false;
  }

  const filename = typeof fileOrPath === 'string' ? fileOrPath : fileOrPath.filename || fileOrPath.originalname || '';
  const ext = filename ? path.extname(filename).toLowerCase() : '';
  const dataExts = getConfiguredDataExtensions();

  if (dataExts.has(ext)) {
    return true;
  }

  // Size check: files larger than 50 KB are treated as workspace data to save prompt tokens
  const limitBytes = Number(process.env.NOTE_DATA_MAX_INLINE_BYTES) || DEFAULT_INLINE_TEXT_LIMIT_BYTES;
  const bytes = options.bytes || (typeof fileOrPath === 'object' ? fileOrPath.bytes : null);
  if (bytes && bytes > limitBytes) {
    return true;
  }

  // Content inspection: sample buffer from disk if available
  let buffer = options.buffer || (typeof fileOrPath === 'object' && Buffer.isBuffer(fileOrPath.buffer) ? fileOrPath.buffer : null);
  const diskPath = options.path || (typeof fileOrPath === 'string' && fs.existsSync(fileOrPath) ? fileOrPath : (fileOrPath.path && fs.existsSync(fileOrPath.path) ? fileOrPath.path : null));

  if (!buffer && diskPath && fs.existsSync(diskPath)) {
    try {
      const stat = fs.statSync(diskPath);
      if (stat.size > limitBytes) {
        return true;
      }
      const fd = fs.openSync(diskPath, 'r');
      const sample = Buffer.alloc(1024);
      const bytesRead = fs.readSync(fd, sample, 0, 1024, 0);
      fs.closeSync(fd);
      buffer = sample.subarray(0, bytesRead);
    } catch {
      // Non-fatal if read fails
    }
  }

  if (buffer && buffer.length > 0) {
    if (isBinaryBuffer(buffer)) {
      return true;
    }
    const sampleStr = buffer.toString('utf8');
    if (isTabularText(sampleStr)) {
      return true;
    }
  }

  return false;
}

/**
 * Resolves the physical path of an uploaded file on disk.
 * Handles both relative paths (e.g. /uploads/userId/file_id__filename) and absolute paths.
 */
function resolveSourcePath(file, userId) {
  if (file.path && fs.existsSync(file.path)) {
    return file.path;
  }

  const rawFilepath = file.filepath || file.path || '';
  const cleanPath = rawFilepath.split('?')[0];

  if (path.isAbsolute(cleanPath) && fs.existsSync(cleanPath)) {
    return cleanPath;
  }

  const { uploads, publicPath } = paths;

  // Pattern: /uploads/:userId/:filename
  if (cleanPath.startsWith('/uploads/')) {
    const rel = cleanPath.replace(/^\/uploads\//, '');
    const candidate = path.join(uploads, rel);
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }

  // Pattern: /images/:userId/:filename
  if (cleanPath.startsWith('/images/')) {
    const rel = cleanPath.replace(/^\/images\//, '');
    const candidate = path.join(publicPath, 'images', rel);
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }

  // User-scoped uploads folder check
  if (userId) {
    const userDir = path.join(uploads, String(userId));
    if (file.file_id) {
      // Look for files starting with file_id
      try {
        if (fs.existsSync(userDir)) {
          const files = fs.readdirSync(userDir);
          const match = files.find((f) => f.startsWith(`${file.file_id}__`) || f === file.file_id);
          if (match) {
            return path.join(userDir, match);
          }
        }
      } catch (e) {
        logger.warn('[noteDataBridge] Failed searching userDir:', e.message);
      }
    }
  }

  return null;
}

/**
 * Bridges a single file into the thread workspace (projects/<thread_id>/data/<filename>).
 */
async function bridgeFileToThread({ conversationId, userId, file, strict = false }) {
  if (!conversationId || conversationId === 'new' || !file) {
    return null;
  }

  const filename = file.filename || file.originalname;
  if (!filename) {
    return null;
  }

  if (path.basename(filename) !== filename || !/^[a-zA-Z0-9_-]+$/.test(String(conversationId))) {
    throw new Error("Invalid attachment workspace path");
  }

  const sourcePath = resolveSourcePath(file, userId);
  if (!sourcePath || !fs.existsSync(sourcePath)) {
    if (strict) throw new Error(`Uploaded file is unavailable: ${filename}`);
    logger.warn(`[noteDataBridge] Source file not found on disk for file_id=${file.file_id}, name=${filename}`);
    return null;
  }

  const projectsDir = getProjectsDir();
  const threadDataDir = path.join(projectsDir, String(conversationId), 'data');

  try {
    await fs.promises.mkdir(threadDataDir, { recursive: true });

    const targetPath = path.join(threadDataDir, filename);

    // Preserve the analysis working copy on subsequent executions.
    try {
      await fs.promises.copyFile(sourcePath, targetPath, fs.constants.COPYFILE_EXCL);
    } catch (err) {
      if (err.code !== "EEXIST") throw err;
      const stat = await fs.promises.lstat(targetPath);
      if (!stat.isFile() || stat.isSymbolicLink()) throw new Error("Invalid attachment target");
    }

    logger.info(`[noteDataBridge] Successfully bridged ${filename} -> projects/${conversationId}/data/${filename}`);
    return {
      bridged: true,
      filename,
      workspacePath: `data/${filename}`,
      targetPath,
      bytes: file.bytes || (await fs.promises.stat(targetPath)).size,
    };
  } catch (err) {
    if (strict) throw new Error(`Could not prepare attachment ${filename}: ${err.message}`);
    logger.error(`[noteDataBridge] Error bridging file ${filename} to thread ${conversationId}:`, err);
    return null;
  }
}

/**
 * Bridges all files associated with a conversation into the thread workspace.
 */
async function bridgeConversationFiles(conversationId, userId) {
  if (!conversationId || conversationId === 'new') {
    return [];
  }

  try {
    const database = getDb();
    if (!database || !database.getFiles) {
      throw new Error('Attachment database unavailable');
    }

    if (!userId) throw new Error('Attachment owner required');
    // Uploads made before a new conversation exists are linked by its messages.
    const messages = await database.getMessages({ conversationId, user: userId }, 'files');
    const ids = messages.flatMap((message) => (message.files || []).map((file) => file.file_id)).filter(Boolean);
    const convoFiles = await database.getFiles({
      user: userId,
      $or: [{ conversationId }, { 'metadata.conversationId': conversationId }, { file_id: { $in: ids } }],
    });

    if (!convoFiles || convoFiles.length === 0) {
      return [];
    }

    const results = [];
    for (const file of convoFiles) {
      const bridged = await bridgeFileToThread({
        conversationId,
        userId,
        file,
        strict: true,
      });
      if (bridged) {
        results.push(bridged);
      }
    }
    return results;
  } catch (err) {
    logger.error(`[noteDataBridge] Error bridging files for conversation ${conversationId}:`, err);
    throw err;
  }
}

/**
 * Formats a prompt context section describing the bridged files available in the R workspace.
 */
function formatDataFilesContext(bridgedFiles) {
  if (!bridgedFiles || bridgedFiles.length === 0) {
    return '';
  }

  let text = '\n\n[Attached Data Files in R Workspace]\n';
  text += 'The following data files have been uploaded and placed into your R NoteKernel workspace (`data/`):\n';

  for (const f of bridgedFiles) {
    const sizeMb = f.bytes ? (f.bytes / (1024 * 1024)).toFixed(2) : 'unknown';
    text += `- \`${f.workspacePath}\` (Size: ${sizeMb} MB)\n`;
  }

  text += '\n**Analysis Instructions for Agent:**\n';
  text += '- The data files are already on disk in your working directory. Do NOT ask the user to re-upload.\n';
  text += '- In your R code cells, load them directly using standard Bioconductor / R functions (e.g. `read.csv("data/<filename>")` or `readRDS("data/<filename>")`).\n';
  text += '- Inspect dimensions, summaries, and column names, then proceed with the requested omics analysis.\n';

  return text;
}

module.exports = {
  isDataFile,
  isBinaryBuffer,
  isTabularText,
  getConfiguredDataExtensions,
  DEFAULT_INLINE_TEXT_LIMIT_BYTES,
  getProjectsDir,
  bridgeFileToThread,
  bridgeConversationFiles,
  formatDataFilesContext,
};
