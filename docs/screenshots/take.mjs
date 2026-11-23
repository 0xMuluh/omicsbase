#!/usr/bin/env node
// Retake the screenshots in the docs in one go, then place them in the pages.
//
//   node docs/screenshots/take.mjs [--clean] [name ...]
//
// --clean (or OMICSBASE_SHOTS_CLEAN=1) takes the same shots without the orange outlines, for press
// material, into docs/images/screens/press/ and leaves the pages alone.
//
// Signed-in shots use a local install with an ordinary test account (never a real person's):
// OMICSBASE_URL (default http://localhost:3080) and OMICSBASE_TEST_EMAIL / OMICSBASE_TEST_PASSWORD,
// read from docs/screenshots/.test-account when not set. Public shots (the start page, shared
// notes) come from OMICSBASE_PUBLIC_URL, by default https://omicsbase.utu.fi.
//
// Images go to docs/images/screens/<name>.webp. Each page marks where a picture belongs with
// <!-- screenshot: <name> — <description> -->; place.py puts the image under its marker.
// Uses the Playwright that the librechat submodule installs, the system's Google Chrome and
// Python with Pillow.
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, rmSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { shots } from './shots.mjs';

const here = dirname(fileURLToPath(import.meta.url));
// OMICSBASE_PLAYWRIGHT_FROM: a package.json whose node_modules has playwright-core (e.g. another checkout).
const require = createRequire(process.env.OMICSBASE_PLAYWRIGHT_FROM ?? join(here, '../../librechat/package.json'));
const { chromium } = require('playwright-core');

const accountFile = join(here, '.test-account');
if (existsSync(accountFile)) {
  for (const line of readFileSync(accountFile, 'utf8').split('\n')) {
    const [key, ...rest] = line.split('=');
    if (key && rest.length && !process.env[key]) process.env[key] = rest.join('=');
  }
}
const base = (process.env.OMICSBASE_URL ?? 'http://localhost:3080').replace(/\/$/, '');
const publicBase = (process.env.OMICSBASE_PUBLIC_URL ?? 'https://omicsbase.utu.fi').replace(/\/$/, '');
const args = process.argv.slice(2);
const clean = args.includes('--clean') || process.env.OMICSBASE_SHOTS_CLEAN === '1';
const out = join(here, '../images/screens', clean ? 'press' : '');
const tmp = join(here, '.tmp');
const only = new Set(args.filter((a) => !a.startsWith('--')));

/** Outline the elements a step talks about, so the reader sees where to click. */
async function highlight(page, selectors = []) {
  for (const selector of selectors) {
    const target = page.locator(selector).first();
    await target.waitFor({ state: 'visible', timeout: 15_000 });
    await target.evaluate((el) => {
      el.style.outline = '3px solid #e8590c';
      el.style.outlineOffset = '3px';
    });
  }
}

/** The box around an element, widened by `pad` pixels and kept inside the page. */
async function boxAround(page, selector, pad = 16) {
  const box = await page.locator(selector).first().boundingBox();
  if (!box) throw new Error(`nothing to crop to: ${selector}`);
  const { width, height } = page.viewportSize();
  const x = Math.max(0, box.x - pad);
  const y = Math.max(0, box.y - pad);
  return { x, y, width: Math.min(width - x, box.width + 2 * pad), height: Math.min(height - y, box.height + 2 * pad) };
}

async function signIn(page) {
  const { OMICSBASE_TEST_EMAIL: email, OMICSBASE_TEST_PASSWORD: password } = process.env;
  if (!email || !password) throw new Error('No test account: set OMICSBASE_TEST_EMAIL and OMICSBASE_TEST_PASSWORD.');
  await page.goto(`${base}/login`);
  await page.getByLabel(/email/i).fill(email);
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Continue' }).click();
  await page.getByRole('button', { name: 'Account Settings' }).waitFor({ timeout: 30_000 });
}

const browser = await chromium.launch({ channel: 'chrome' });
mkdirSync(out, { recursive: true });
mkdirSync(tmp, { recursive: true });
const contexts = {};
async function contextFor(kind, viewport) {
  const key = `${kind}:${viewport.width}x${viewport.height}`;
  if (!contexts[key]) {
    // Clipboard access lets the Copy buttons switch to "Copied".
    const context = await browser.newContext({
      viewport,
      colorScheme: 'light',
      deviceScaleFactor: 1.5,
      permissions: ['clipboard-read', 'clipboard-write'],
    });
    if (kind === 'signed-in') await signIn(await context.newPage());
    contexts[key] = context;
  }
  return contexts[key];
}

// State shared between shots, such as the address of the note copied from an example.
const state = { base, publicBase, here, clean };

let failed = 0;
for (const shot of shots) {
  var page;
  if (only.size && !only.has(shot.name)) continue;
  const viewport = shot.viewport ?? { width: 1280, height: 800 };
  try {
    const context = await contextFor(shot.public ? 'public' : 'signed-in', viewport);
    var page = await context.newPage();
    const path = typeof shot.path === 'function' ? await shot.path(state, page) : shot.path;
    await page.goto(`${shot.public ? publicBase : base}${path}`, { waitUntil: 'load' });
    await page.waitForTimeout(shot.wait ?? 3000);
    if (!shot.public && new URL(page.url()).pathname.startsWith('/login')) {
      await signIn(page);
      await page.goto(`${base}${path}`, { waitUntil: 'load' });
      await page.waitForTimeout(shot.wait ?? 3000);
    }
    if (shot.prepare) await shot.prepare(page, state);
    if (!clean) await highlight(page, shot.highlight);
    await page.waitForTimeout(300);
    const clip = shot.clip ? await boxAround(page, shot.clip, shot.pad) : undefined;
    const png = join(tmp, `${shot.name}.png`);
    await page.screenshot({ path: png, ...(clip ? { clip } : {}) });
    await page.close();
    execFileSync('python3', ['-I', join(here, 'to_webp.py'), png, join(out, `${shot.name}.webp`)]);
    console.log(`ok   ${shot.name}`);
  } catch (e) {
    failed += 1;
    console.error(`FAIL ${shot.name}: ${e.message.split('\n').slice(0, process.env.SHOTS_DEBUG ? 20 : 1).join('\n')}`);
    // What the page showed when it failed, for fixing the shot.
    if (process.env.SHOTS_DEBUG && page) {
      await page.screenshot({ path: join(process.env.SHOTS_DEBUG, `fail-${shot.name}.png`) }).catch(() => {});
    }
  }
}
await browser.close();
rmSync(tmp, { recursive: true, force: true });
if (!clean) execFileSync('python3', ['-I', join(here, 'place.py')], { stdio: 'inherit' });
process.exit(failed ? 1 : 0);
