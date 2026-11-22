#!/usr/bin/env node
// Screenshots that need a real signed-in account on omicsbase.utu.fi (Reports on Roihu).
//
//   node docs/screenshots/take-signed-in.mjs [name ...]
//
// Opens a Chrome window at the sign-in page; you sign in yourself, so the script never sees a
// password, and the sign-in lives only in that window. It then takes the shots below that the
// account's current state allows (a running session gives the session bar and the workspace;
// no session gives the launch form), writes docs/images/screens/<name>.webp and places them.
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import { mkdirSync, rmSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(join(here, '../../librechat/package.json'));
const { chromium } = require('playwright-core');
const base = (process.env.OMICSBASE_PUBLIC_URL ?? 'https://omicsbase.utu.fi').replace(/\/$/, '');
const out = join(here, '../images/screens');
const tmp = join(here, '.tmp');
const only = new Set(process.argv.slice(2));

const outline = (page, selector) =>
  page.locator(selector).first().evaluate((el) => {
    el.style.outline = '3px solid #e8590c';
    el.style.outlineOffset = '3px';
  });
/** The account's name, avatar and note titles stay out of the docs. */
const hideAccount = (page) =>
  page.addStyleTag({
    content: '[aria-label="Account Settings"], a[href^="/c/"] { visibility: hidden !important; }',
  });

async function save(page, name, clipSelector) {
  let clip;
  if (clipSelector) {
    const box = await page.locator(clipSelector).first().boundingBox();
    const { width, height } = page.viewportSize();
    clip = { x: Math.max(0, box.x - 16), y: Math.max(0, box.y - 16) };
    clip.width = Math.min(width - clip.x, box.width + 32);
    clip.height = Math.min(height - clip.y, box.height + 32);
  }
  const png = join(tmp, `${name}.png`);
  await page.screenshot({ path: png, ...(clip ? { clip } : {}) });
  execFileSync('python3', ['-I', join(here, 'to_webp.py'), png, join(out, `${name}.webp`)]);
  console.log(`ok   ${name}`);
}

const shots = {
  'reports-page': async (page) => {
    await page.goto(`${base}/projects`, { waitUntil: 'load' });
    await page.waitForTimeout(4000);
    await hideAccount(page);
    await outline(page, 'role=button[name="New report"]');
    await save(page, 'reports-page');
  },
  'session-bar': async (page) => {
    await openWorkspaceTab(page);
    await outline(page, 'role=button[name="Stop session"]');
    await save(page, 'session-bar', 'text=Roihu session running >> xpath=ancestor::div[.//button][1]');
  },
  workspace: async (page) => {
    await openWorkspaceTab(page);
    await page.waitForTimeout(15_000); // the workspace connects after the bar appears
    await hideAccount(page);
    await save(page, 'workspace');
  },
  'launch-form-filled': async (page) => {
    await openReport(page);
    await page.locator('text="Workspace"').first().click();
    await page.locator('role=button[name="Launch"]').waitFor({ timeout: 30_000 });
    await page.getByLabel('CSC project').fill('project_1234567');
    await hideAccount(page);
    await outline(page, 'text=Get a token from MyCSC');
    await outline(page, 'role=button[name="Launch"]');
    await save(page, 'launch-form-filled');
  },
};

/** The report "Docs example", where the Roihu card lives; made the first time. */
const REPORT = 'Docs example';
let reportUrl;
async function openReport(page) {
  if (reportUrl) {
    await page.goto(reportUrl, { waitUntil: 'load' });
    await page.waitForTimeout(3000);
    return;
  }
  await page.goto(`${base}/projects`, { waitUntil: 'load' });
  await page.waitForTimeout(3000);
  if (!(await page.locator(`text="${REPORT}"`).count())) {
    await page.locator('role=button[name="New report"]').first().click();
    await page.getByLabel('Report name').fill(REPORT);
    await page.locator('role=button[name="Create report"]').last().click();
    await page.waitForTimeout(4000);
  }
  if (!/\/projects\/[^/]+/.test(page.url())) {
    // The report's card in the list (the sidebar entry only folds open).
    await page.locator(`button:has-text("${REPORT}"):has-text("notes")`).first().click();
  }
  await page.waitForURL(/\/projects\/[^/]+/, { timeout: 30_000 });
  reportUrl = page.url();
  await page.waitForTimeout(3000);
}

/** The report's Workspace tab, where the Roihu session card is. */
async function openWorkspaceTab(page) {
  await openReport(page);
  await page.locator('text="Workspace"').first().click();
  await page.locator('text=Roihu session running').first().waitFor({ timeout: 60_000 });
}

mkdirSync(out, { recursive: true });
mkdirSync(tmp, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: false });
const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1.5 });
const page = await context.newPage();
await page.goto(`${base}/login`);
console.log('Sign in in the Chrome window; waiting up to 10 minutes...');
await page.locator('[aria-label="Account Settings"]').waitFor({ timeout: 600_000 });
console.log('Signed in.');
let failed = 0;
for (const [name, take] of Object.entries(shots)) {
  if (only.size && !only.has(name)) continue;
  try {
    await take(page);
  } catch (e) {
    failed += 1;
    console.error(`skip ${name}: ${e.message.split('\n')[0]}`);
    if (process.env.SHOTS_DEBUG) {
      await page.screenshot({ path: join(process.env.SHOTS_DEBUG, `fail-${name}.png`) }).catch(() => {});
    }
  }
}
await browser.close();
rmSync(tmp, { recursive: true, force: true });
execFileSync('python3', ['-I', join(here, 'place.py')], { stdio: 'inherit' });
process.exit(failed ? 1 : 0);
