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

const scrollTo = (selector) => async (page) => {
  await page.locator(selector).first().scrollIntoViewIfNeeded();
  await page.locator(selector).first().evaluate((el) => el.scrollIntoView({ block: 'start' }));
  await page.mouse.wheel(0, -24);
  await page.waitForTimeout(800);
};

const openModels = async (page) => {
  await page.waitForTimeout(1500);
  const button = page.locator(role('button', 'Select a model')).first();
  await button.evaluate((el) => {
    el.style.outline = '3px solid #e8590c';
    el.style.outlineOffset = '3px';
  });
  // The menu fades in with an animation that does not run in an automated browser.
  await page.addStyleTag({ content: '.animate-popover { opacity: 1 !important; animation: none !important; }' });
  await button.click();
  await page.locator('[role=option]').first().waitFor({ state: 'visible', timeout: 15_000 });
  await page.waitForTimeout(500);
};

const chatOptions = role('button', /Chat options|More options/);

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
      const edit = page.locator(role('button', 'Edit code')).first();
      await edit.scrollIntoViewIfNeeded();
      await edit.click();
      await page.locator(`${role('button', 'Run code in NoteKernel')} >> visible=true`).first().click();
      await page.locator('text=/Queued|Running/').first().waitFor({ timeout: 15_000 });
      await page.locator('text=/Queued|Running/').first().evaluate((el) => el.scrollIntoView({ block: 'center' }));
    },
    wait: 2000,
    highlight: ['text=/Queued|Running/ >> nth=0'],
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
    highlight: ['[role=dialog] input[value*="/share/"] >> xpath=..'],
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

  // A local install's first screens.
  { name: 'local-sign-up-page', path: '/register' },
];
