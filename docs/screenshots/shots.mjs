// Every screenshot in the docs. `name` is the file in docs/images/screens/ and the name in the
// page's <!-- screenshot: … --> marker. `public: true` shots come from the public site without
// signing in; the rest from a local install with an ordinary test account.
// `path` is a string or (state, page) => string; `prepare(page, state)` clicks or scrolls;
// `highlight` takes Playwright selectors (role=…, text=…, CSS); `clip` crops to one element.

const role = (r, name) => `role=${r}[name=${name instanceof RegExp ? name : JSON.stringify(name)}]`;
const plotImg = 'img[alt^="Plot"]';
const plotCard = (n) => `${plotImg} >> nth=${n} >> xpath=ancestor::div[contains(@class,"border")][1]`;

/** Scroll the n-th plot on the page into view and wait for it to load. */
async function showPlot(page, n = 0) {
  const img = page.locator(plotImg).nth(n);
  await img.scrollIntoViewIfNeeded();
  await page.waitForFunction((el) => el.complete && el.naturalWidth > 0, await img.elementHandle(), {
    timeout: 20_000,
  });
  await img.evaluate((el) => el.scrollIntoView({ block: 'center' }));
}

/** A figure from a public example note, cropped to its plot card. */
const figure = (name, shareId, n = 0) => ({
  name,
  public: true,
  path: `/share/${shareId}`,
  prepare: (page) => showPlot(page, n),
  clip: plotCard(n),
});

/** The test account's own note: a copy of an example note, made once per run. */
async function myNote(state, page) {
  if (!state.note) {
    await page.goto(`${state.base}/examples`);
    if (new URL(page.url()).pathname.startsWith('/login')) throw new Error('signed out: check the test account');
    await page.locator('text=Explore').first().click();
    await page.locator(role('button', 'Continue this note')).click();
    await page.waitForURL(/\/c\/[0-9a-f-]{8,}/, { timeout: 60_000 });
    state.note = new URL(page.url()).pathname;
  }
  return state.note;
}


/** The first R cell card on the page. */
const firstCell = 'div.rounded-xl.border:has(button[aria-label="Run code in NoteKernel"]) >> nth=0';
/** What the cell shots run: an alpha diversity analysis, which OMA's alpha diversity chapter uses. */
const ANALYSIS = [
  'library(mia)',
  'data(GlobalPatterns, package = "mia")',
  'tse <- addAlpha(GlobalPatterns, index = c("shannon", "faith"))',
  'summary(tse$shannon)',
].join('\n');

/** The note for the cell and energy shots: OMICSBASE_CELL_NOTE (a /c/<id> path of a test note
 * with at least one R cell and some model use), or the copy of an example. */
const cellNote = async (state, page) => process.env.OMICSBASE_CELL_NOTE ?? (await myNote(state, page));

/** Put ANALYSIS into the first cell, save it as a revision and run it. */
async function runAnalysis(page) {
  const cell = page.locator(firstCell);
  await cell.scrollIntoViewIfNeeded();
  await cell.evaluate((el) => el.scrollIntoView({ block: 'start' }));
  await page.mouse.wheel(0, -90);
  await cell.locator('button[aria-label="Edit code"]').click();
  await cell.locator('.monaco-editor').first().click();
  await page.keyboard.press('Control+A');
  await page.keyboard.press('Delete');
  await page.keyboard.insertText(ANALYSIS);
  await page.waitForTimeout(500);
  // Already saved by an earlier shot of this run: nothing to save.
  const save = cell.locator('button[aria-label="Save revision"]');
  if (await save.isEnabled()) await save.click();
  await cell.locator('button[aria-label="Run code in NoteKernel"]:enabled').click({ timeout: 60_000 });
  // TEMP: the statuses the run goes through.
  (async () => {
    let last = '';
    for (let i = 0; i < 240; i++) {
      const text = await cell.locator('[data-testid="note-cell-status"]').first().textContent({ timeout: 1000 }).catch(() => null);
      if (text !== last) console.log(`     status ${(i / 4).toFixed(2)}s: ${text}`);
      last = text;
      await page.waitForTimeout(250).catch(() => {});
    }
  })().catch(() => {});
  return cell;
}

const scrollTo = (selector) => async (page) => {
  await page.locator(selector).first().scrollIntoViewIfNeeded();
  await page.locator(selector).first().evaluate((el) => el.scrollIntoView({ block: 'start' }));
  await page.mouse.wheel(0, -24);
  await page.waitForTimeout(800);
};

const openModels = async (page, state) => {
  await page.waitForTimeout(1500);
  const button = page.locator(role('button', 'Select a model')).first();
  if (!state.clean) {
    await button.evaluate((el) => {
      el.style.outline = '3px solid #e8590c';
      el.style.outlineOffset = '3px';
    });
  }
  // The menu fades in with an animation that does not run in an automated browser.
  await page.addStyleTag({ content: '.animate-popover { opacity: 1 !important; animation: none !important; }' });
  await button.click();
  await page.locator('[role=option]').first().waitFor({ state: 'visible', timeout: 15_000 });
  await page.waitForTimeout(500);
};

const chatOptions = role('button', /^Chat options/);

/** The Workspace tab of the test account's report, made once per run. */
async function reportWorkspace(state, page) {
  if (!state.report) {
    const name = 'Docs example';
    // The report's card in the list (the sidebar entry only folds open).
    const card = page.locator(`button:has-text("${name}"):has-text("notes")`).first();
    await page.goto(`${state.base}/projects`, { waitUntil: 'load' });
    await page.waitForTimeout(3000);
    if (!(await card.count())) {
      await page.locator(role('button', 'New report')).first().click();
      await page.getByLabel('Report name').fill(name);
      await page.locator(role('button', 'Create report')).last().click();
      await page.waitForTimeout(4000);
    }
    if (!/\/projects\/[^/]+/.test(page.url())) await card.click();
    await page.waitForURL(/\/projects\/[^/]+/, { timeout: 30_000 });
    state.report = new URL(page.url()).pathname;
  }
  return state.report;
}

/** The panel "Use my own computer" opens, with the download and pairing commands; the
 * innermost match (the last in page order) is the panel itself. */
const pairingPanel = 'div.rounded-xl.border:has-text("Code valid for")';
const copyCommand = role('button', /^(Copy command|Copied)$/);

/** On the Workspace tab, choose Use my own computer and wait for the pairing code. */
async function startPairing(page) {
  await page.locator('text="Workspace"').first().click();
  await page.locator(role('button', 'Use my own computer')).click();
  await page.locator('text=/Code valid for/').first().waitFor({ timeout: 30_000 });
  await page.locator(pairingPanel).last().scrollIntoViewIfNeeded();
}

export const shots = [
  // The start page and signing in.
  { name: 'landing-page', public: true, path: '/', highlight: [role('textbox', 'Message')] },
  {
    name: 'sign-in-choices',
    public: true,
    path: '/login',
    highlight: ['[data-testid="saml"]', '[data-testid="google"]'],
  },
  {
    name: 'landing-examples',
    public: true,
    path: '/',
    prepare: scrollTo('text=Examples >> nth=0'),
    highlight: ['text=Explore >> nth=0'],
  },
  {
    name: 'landing-examples-filters',
    public: true,
    path: '/',
    prepare: scrollTo('text=Examples >> nth=0'),
    highlight: ['input[placeholder^="Search examples"]'],
  },
  {
    name: 'landing-datasets-open-studies',
    public: true,
    path: '/',
    prepare: async (page) => {
      await page.locator(role('tab', 'Open studies')).first().click();
      await scrollTo(role('tab', 'Open studies'))(page);
      await page.mouse.wheel(0, -120);
    },
    highlight: [role('tab', 'Open studies')],
  },

  // Models.
  {
    name: 'model-picker-open',
    public: true,
    path: '/',
    prepare: openModels,
  },
  {
    name: 'model-picker',
    public: true,
    path: '/',
    prepare: openModels,
  },
  // Notes, signed in.
  { name: 'new-note', path: '/c/new', highlight: [role('link', 'New note')] },
  {
    name: 'attach-menu',
    path: '/c/new',
    prepare: async (page) => page.locator(role('button', 'Attach and tools')).click(),
    highlight: [role('button', 'Attach and tools'), 'text=From Local Computer'],
  },
  {
    name: 'file-in-tray',
    path: '/c/new',
    prepare: async (page, state) => {
      await page.locator(role('button', 'Attach and tools')).click();
      const chooser = page.waitForEvent('filechooser');
      await page.locator('text=From Local Computer').first().click();
      await (await chooser).setFiles(`${state.here}/sample-counts.csv`);
      await page.locator(role('textbox', /Message/)).first().fill('Compare the two groups in this table.');
      await page.waitForTimeout(2500);
    },
    highlight: ['text=sample-counts.csv'],
  },
  {
    name: 'datasets-page',
    path: '/datasets',
    highlight: [role('tab', 'Built in'), role('tab', 'Open studies')],
  },
  {
    name: 'r-cell-result',
    path: myNote,
    prepare: (page) => showPlot(page, 0),
    highlight: [`${role('button', 'Copy R code')} >> nth=0`, `${role('button', 'Download plot')} >> nth=0`],
  },
  {
    name: 'edit-cell',
    path: myNote,
    prepare: async (page) => {
      const edit = page.locator(role('button', 'Edit code')).first();
      await edit.scrollIntoViewIfNeeded();
      await edit.click();
      await page.waitForTimeout(800);
      await edit.evaluate((el) => el.scrollIntoView({ block: 'center' }));
    },
    highlight: [role('button', 'Save revision'), role('button', /Run code/)],
  },
  {
    name: 'first-reply',
    path: myNote,
    prepare: async (page) => {
      const cell = await runAnalysis(page);
      const status = cell.locator('[data-testid="note-cell-status"]', { hasText: /Running/ });
      await status.waitFor({ timeout: 60_000 });
      await status.evaluate((el) => el.scrollIntoView({ block: 'center' }));
    },
    wait: 2000,
    highlight: [`${firstCell} >> [data-testid="note-cell-status"]`],
  },
  {
    name: 'share-menu',
    path: myNote,
    prepare: async (page) => page.locator(chatOptions).first().click(),
    highlight: [role('menuitem', 'Share')],
  },
  {
    name: 'share-dialog',
    path: myNote,
    prepare: async (page) => {
      await page.locator(chatOptions).first().click();
      await page.locator(role('menuitem', 'Share')).click();
      const create = page.locator(role('button', /Create a shared link|Create link/));
      if (await create.count()) await create.first().click();
      await page.locator('[role=dialog] input[value*="/share/"]').first().waitFor({ timeout: 15_000 });
    },
    highlight: [`[role=dialog] >> ${role('button', 'Copy link')}`],
  },
  {
    name: 'delete-link',
    path: myNote,
    prepare: async (page) => {
      await page.locator(chatOptions).first().click();
      await page.locator(role('menuitem', 'Share')).click();
      await page.locator(role('button', /Delete/)).first().click();
      await page.waitForTimeout(800);
    },
    highlight: [role('button', /Delete Link/)],
  },
  {
    name: 'note-export-quarto',
    path: myNote,
    prepare: async (page) => {
      await page.locator(chatOptions).first().click();
      await page.locator(role('menuitem', /Export/)).click();
      await page.locator('[role=dialog] >> text=/markdown \\(\\.md\\)/').first().click();
      await page.locator('text=/quarto \\(\\.qmd\\)/').first().click();
      await page.waitForTimeout(800);
    },
  },
  {
    name: 'shared-note-view',
    public: true,
    path: '/share/NFrFVhfYAQlvZStXRsKrO',
    prepare: (page) => showPlot(page, 0),
    highlight: [role('button', 'Continue this note')],
  },

  // Figures from public example notes.
  figure('transcriptomics-volcano', 'NFrFVhfYAQlvZStXRsKrO'),
  figure('functional-species-pathways', '0XYFhfmIKUsPWDMlQsO7k'),
  figure('multiomics-mofa-variance', 'phsU5du5NnJz6mmIRepWc'),
  figure('spatial-domains', 'LzwjVZ1Faxy22SBEz0oet'),
  figure('microbiome-example-figure', 'aiX8hShwNDHM0cQq4Ztzs'),
  figure('proteomics-volcano', 'V2M6cSUr7lFPto0GJPUK2'),
  figure('single-cell-qc-plot', 'Q84Un8slx3cxWtT6uIuPA'),

  // Cells and energy.
  {
    name: 'cell-status-line',
    path: cellNote,
    prepare: async (page) => {
      const cell = await runAnalysis(page);
      await cell.locator('[data-testid="note-cell-status"]', { hasText: /Running · 0:0[1-9]/ }).waitFor({ timeout: 120_000 });
    },
    clip: firstCell,
    highlight: [`${firstCell} >> [data-testid="note-cell-status"]`],
  },
  {
    name: 'cell-book-badge',
    path: cellNote,
    prepare: async (page) => {
      const cell = await runAnalysis(page);
      const line = cell.locator('text=From the book').first();
      await line.waitFor({ timeout: 180_000 });
      await line.evaluate((el) => el.scrollIntoView({ block: 'end' }));
      await page.mouse.wheel(0, 140);
      await page.waitForTimeout(500);
    },
    clip: firstCell,
    highlight: [`${firstCell} >> text=From the book`],
  },
  {
    name: 'note-usage-energy',
    path: cellNote,
    prepare: async (page) => {
      await page.locator('[data-testid="token-usage"]').click();
      await page.locator('text=/This note: about/').first().waitFor({ timeout: 15_000 });
    },
    clip: '[data-testid="token-usage-card"]',
    pad: 24,
    highlight: ['text=/This note: about/'],
  },
  {
    name: 'settings-energy-total',
    path: '/c/new',
    prepare: async (page) => {
      await page.locator(role('button', 'Account Settings')).click();
      await page.locator('text=Settings').last().click();
      await page.locator(role('tab', /Account/)).click();
      await page.locator('text=/All your notes: about/').first().waitFor({ timeout: 15_000 });
    },
    highlight: ['text=/All your notes: about/'],
  },
  // A local install's first screens.
  { name: 'local-sign-up-page', path: '/register' },

  // Reports on your own computer: the pairing panel on a report's Workspace tab.
  {
    name: 'use-own-computer-panel',
    path: reportWorkspace,
    prepare: startPairing,
    clip: `${pairingPanel} >> nth=-1`,
    highlight: [`${copyCommand} >> nth=0`, `${copyCommand} >> nth=1`, 'text=/Code valid for/'],
  },
  {
    name: 'pairing-waiting',
    path: reportWorkspace,
    prepare: async (page) => {
      await startPairing(page);
      await page.locator(copyCommand).nth(1).click();
      await page.locator(role('button', 'Copied')).waitFor({ timeout: 10_000 });
    },
    clip: `${pairingPanel} >> nth=-1`,
  },
];
