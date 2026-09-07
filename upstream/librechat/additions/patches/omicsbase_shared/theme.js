(() => {
  try {
    if (!localStorage.getItem('omicsbase-default-tab-set-served-v1')) {
      localStorage.setItem('conversation-selected-tab', JSON.stringify('served'));
      localStorage.setItem('omicsbase-default-tab-set-served-v1', '1');
    }
    const raw = localStorage.getItem('conversation-selected-tab');
    if (raw !== null) {
      try {
        const parsed = JSON.parse(raw);
        if (parsed !== 'editor' && parsed !== 'served' && parsed !== 'vscode') {
          localStorage.setItem('conversation-selected-tab', JSON.stringify('served'));
        }
      } catch {
        localStorage.setItem('conversation-selected-tab', JSON.stringify('served'));
      }
    } else {
      localStorage.setItem('conversation-selected-tab', JSON.stringify('served'));
    }
  } catch {}

  if (window.parent === window) return;

  let parentOrigin;
  try {
    const referrerOrigin = new URL(document.referrer).origin;
    parentOrigin =
      referrerOrigin === window.location.origin
        ? sessionStorage.getItem('omicsbase-parent-origin')
        : referrerOrigin;
    if (!parentOrigin) return;
  } catch {
    return;
  }

  const required = ['presentation', 'surface-primary', 'text-primary'];
  /** @type {{ mode: string, colors: Record<string, string> } | null} */
  let lastTheme = null;

  /** Parse #rgb / #rrggbb / rgb() / rgba() → [r,g,b] 0–255. */
  function parseRgb(color) {
    if (typeof color !== 'string') return null;
    const hex = color.trim().match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
    if (hex) {
      let h = hex[1];
      if (h.length === 3) h = h.split('').map((c) => c + c).join('');
      return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
    }
    const rgb = color.trim().match(/^rgba?\(\s*([0-9.]+)\s*[, ]\s*([0-9.]+)\s*[, ]\s*([0-9.]+)/i);
    if (rgb) return [Number(rgb[1]), Number(rgb[2]), Number(rgb[3])];
    return null;
  }

  /** Soft Daisy accent → HeroUI "H S% L%" channel string. */
  function toHslChannels(color) {
    const rgb = parseRgb(color);
    if (!rgb) return null;
    let [r, g, b] = rgb.map((v) => v / 255);
    const max = Math.max(r, g, b);
    const min = Math.min(r, g, b);
    const l = (max + min) / 2;
    if (max === min) return `0 0% ${(l * 100).toFixed(2)}%`;
    const d = max - min;
    const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    let h;
    switch (max) {
      case r:
        h = ((g - b) / d + (g < b ? 6 : 0)) / 6;
        break;
      case g:
        h = ((b - r) / d + 2) / 6;
        break;
      default:
        h = ((r - g) / d + 4) / 6;
    }
    return `${(h * 360).toFixed(2)} ${(s * 100).toFixed(2)}% ${(l * 100).toFixed(2)}%`;
  }

  /** Map Soft Daisy parent tokens → OpenHands CSS custom properties. */
  function applySoftDaisy(mode, colors) {
    const c = (name, fallback) =>
      typeof colors[name] === 'string' && CSS.supports('color', colors[name])
        ? colors[name]
        : fallback;

    const presentation = c('presentation');
    const surfacePrimary = c('surface-primary', presentation);
    const surfacePrimaryAlt = c('surface-primary-alt', surfacePrimary);
    const surfaceSecondary = c('surface-secondary', surfacePrimaryAlt);
    const surfaceTertiary = c('surface-tertiary', surfaceSecondary);
    const surfaceHover = c('surface-hover', surfaceTertiary);
    const surfaceDialog = c('surface-dialog', surfacePrimary);
    const surfaceChat = c('surface-chat', surfacePrimary);
    const textPrimary = c('text-primary');
    const textSecondary = c('text-secondary', textPrimary);
    const textTertiary = c('text-tertiary', textSecondary);
    const borderLight = c('border-light', surfaceTertiary);
    const borderMedium = c('border-medium', borderLight);
    const accent = c('accent-primary', mode === 'dark' ? '#63ccc0' : '#1c5757');
    const accentHover = c('accent-primary-hover', accent);
    const ring = c('ring-primary', accent);
    const success = c('status-success', accent);
    const accentFg = mode === 'dark' ? '#05080c' : '#fafafa';
    const accentHsl = toHslChannels(accent);

    if (!presentation || !textPrimary) return false;

    const root = document.documentElement;
    root.dataset.omicsbaseTheme = mode;
    root.classList.toggle('dark', mode === 'dark');
    root.classList.toggle('light', mode === 'light');
    root.style.colorScheme = mode;

    // Sync HeroUI / AgentServerUI theme attribute so light mode can take effect.
    document.querySelectorAll('[data-theme]').forEach((el) => {
      el.setAttribute('data-theme', mode);
      el.classList.remove('dark', 'light', 'default');
      el.classList.add(mode);
    });

    const vars = {
      // Bridge aliases (legacy + editors)
      '--base': presentation,
      '--color-base': presentation,
      '--color-base-secondary': surfaceSecondary,
      '--color-tertiary': surfaceTertiary,
      '--color-tertiary-light': textSecondary,
      '--color-content': textPrimary,
      '--color-content-2': textPrimary,
      '--bg-dark': surfacePrimary,
      '--bg-light': surfaceSecondary,
      '--bg-input': surfaceTertiary,
      '--bg-workspace': surfacePrimary,
      '--bg-editor-sidebar': surfaceSecondary,
      '--bg-editor-active': surfaceHover,
      '--border': borderLight,
      '--border-editor-sidebar': borderLight,
      '--text-editor-base': textSecondary,
      '--text-editor-active': textPrimary,
      '--omicsbase-text': textPrimary,
      '--omicsbase-muted': textSecondary,
      '--omicsbase-hover': surfaceHover,
      '--omicsbase-accent': accent,

      // Soft Daisy → OpenHands semantic tokens
      '--oh-color-primary': accent,
      '--oh-accent': accent,
      '--oh-warning': accentHover,
      '--oh-focus': ring,
      '--oh-color-logo': accent,
      '--oh-color-base': presentation,
      '--oh-color-base-secondary': surfaceSecondary,
      '--oh-color-tertiary': surfaceTertiary,
      '--oh-color-tertiary-light': textTertiary,
      '--oh-color-content': textPrimary,
      '--oh-color-content-2': textPrimary,
      '--oh-color-basic': textSecondary,
      '--oh-background': presentation,
      '--oh-foreground': textPrimary,
      '--oh-surface': surfaceSecondary,
      '--oh-surface-foreground': textPrimary,
      '--oh-surface-raised': surfaceTertiary,
      '--oh-surface-deep': surfacePrimary,
      '--oh-overlay': surfaceDialog,
      '--oh-overlay-foreground': textPrimary,
      '--oh-muted': textSecondary,
      '--oh-text-secondary': textSecondary,
      '--oh-text-tertiary': textTertiary,
      '--oh-text-dim': textTertiary,
      '--oh-text-subtle': textTertiary,
      '--oh-interactive-hover': surfaceHover,
      '--oh-interactive-hover-low': surfaceSecondary,
      '--oh-interactive-active': surfaceTertiary,
      '--oh-interactive-selected': surfaceHover,
      '--oh-default': surfaceTertiary,
      '--oh-default-foreground': textPrimary,
      '--oh-accent-foreground': accentFg,
      '--oh-success': success,
      '--oh-success-foreground': accentFg,
      '--oh-warning-foreground': accentFg,
      '--oh-segment': surfaceSecondary,
      '--oh-segment-foreground': textPrimary,
      '--oh-border': borderLight,
      '--oh-border-input': borderMedium,
      '--oh-border-subtle': borderLight,
      '--oh-separator': borderLight,
      '--oh-link': textPrimary,
      '--oh-status-success': success,
      '--oh-bg-dark': surfacePrimary,
      '--oh-bg-light': surfaceSecondary,
      '--oh-bg-input': surfaceTertiary,
      '--oh-bg-workspace': surfaceChat || surfacePrimary,
      '--oh-text-editor-base': textSecondary,
      '--oh-text-editor-active': textPrimary,
      '--oh-bg-editor-sidebar': surfaceSecondary,
      '--oh-bg-editor-active': surfaceHover,
      '--oh-border-editor-sidebar': borderLight,

      // CRITICAL: Tailwind v4 bakes --color-primary:#c9b974 at build time.
      // .bg-primary / .text-primary read --color-primary, NOT --oh-color-primary.
      '--color-primary': accent,
      '--accent': accent,
      '--accent-foreground': accentFg,
      '--warning': accentHover,
      '--warning-foreground': accentFg,

      // Flatten cool-grey scale onto Soft Daisy surfaces so leftover OH greys match.
      '--cool-grey-50': mode === 'dark' ? textPrimary : presentation,
      '--cool-grey-100': mode === 'dark' ? textPrimary : surfacePrimary,
      '--cool-grey-200': mode === 'dark' ? textSecondary : surfaceSecondary,
      '--cool-grey-300': textTertiary,
      '--cool-grey-400': textSecondary,
      '--cool-grey-500': textTertiary,
      '--cool-grey-600': borderMedium,
      '--cool-grey-700': borderLight,
      '--cool-grey-800': surfaceTertiary,
      '--cool-grey-900': surfaceSecondary,
      '--cool-grey-925': surfaceSecondary,
      '--cool-grey-950': surfacePrimary,
      '--cool-grey-975': presentation,
    };

    if (accentHsl) {
      vars['--heroui-primary'] = accentHsl;
      vars['--heroui-primary-foreground'] =
        mode === 'dark' ? '210 20% 2%' : '0 0% 100%';
    }

    for (const [name, value] of Object.entries(vars)) {
      root.style.setProperty(name, value);
    }

    // Also stamp onto AgentServerUI scope roots (inline styles otherwise win).
    document.querySelectorAll('[data-agent-server-ui]').forEach((el) => {
      // React remounts can reset data-theme=dark — keep Soft Daisy mode sticky.
      el.setAttribute('data-theme', mode);
      el.classList.remove('dark', 'light', 'default');
      el.classList.add(mode);
      for (const [name, value] of Object.entries(vars)) {
        if (
          name.startsWith('--oh-') ||
          name.startsWith('--cool-grey-') ||
          name.startsWith('--heroui-') ||
          name === '--color-primary' ||
          name === '--accent' ||
          name === '--accent-foreground' ||
          name === '--warning' ||
          name === '--warning-foreground'
        ) {
          el.style.setProperty(name, value);
        }
      }
    });

    paintVscodeIframes(mode, {
      accent,
      accentFg,
      presentation,
      surfacePrimary,
      surfaceSecondary,
      textPrimary,
      textSecondary,
    });

    return true;
  }

  /** Soft Daisy chrome for same-origin OpenVSCode iframe (kills default blue status bar). */
  function paintVscodeIframes(mode, t) {
    const css = `
      .monaco-workbench .part.statusbar,
      .monaco-workbench .part.statusbar .statusbar-item {
        background-color: ${t.accent} !important;
        color: ${t.accentFg} !important;
        border-color: transparent !important;
      }
      .monaco-workbench .part.statusbar a,
      .monaco-workbench .part.statusbar .codicon {
        color: ${t.accentFg} !important;
      }
      .monaco-workbench .part.activitybar {
        background-color: ${t.presentation} !important;
        border-color: color-mix(in srgb, ${t.accent} 25%, transparent) !important;
      }
      .monaco-workbench .part.activitybar .action-item.checked .action-label,
      .monaco-workbench .activitybar .action-label.checked {
        color: ${t.accent} !important;
      }
      .monaco-workbench .part.sidebar,
      .monaco-workbench .part.auxiliarybar {
        background-color: ${t.surfacePrimary} !important;
        color: ${t.textPrimary} !important;
      }
      .monaco-workbench .part.titlebar {
        background-color: ${t.presentation} !important;
        color: ${t.textPrimary} !important;
      }
      .monaco-workbench .part.editor > .content,
      .monaco-workbench .editor-group-container,
      .monaco-workbench .monaco-editor,
      .monaco-workbench .monaco-editor-background,
      .monaco-workbench .margin {
        background-color: ${t.presentation} !important;
      }
      .monaco-workbench .part.panel {
        background-color: ${t.surfaceSecondary} !important;
      }
      .monaco-workbench .tab.active {
        border-top-color: ${t.accent} !important;
      }
      .monaco-workbench .button,
      .monaco-workbench .monaco-button:not(.secondary) {
        background-color: ${t.accent} !important;
        color: ${t.accentFg} !important;
      }
    `;

    document.querySelectorAll('iframe').forEach((frame) => {
      const src = frame.getAttribute('src') || '';
      if (!src.includes('/vscode') && !/vscode/i.test(frame.title || '')) return;
      try {
        const doc = frame.contentDocument;
        if (!doc) return;
        let tag = doc.getElementById('omicsbase-vscode-theme');
        if (!tag) {
          tag = doc.createElement('style');
          tag.id = 'omicsbase-vscode-theme';
          (doc.head || doc.documentElement).appendChild(tag);
        }
        tag.textContent = css;
        doc.documentElement.dataset.omicsbaseTheme = mode;
        doc.documentElement.style.colorScheme = mode;
      } catch {
        // Not ready / sandboxed — retry via observer interval.
      }
    });
  }

  // Literal Tailwind surface utilities used by this OpenHands build.
  const literalSurfaces = [
    '#0D0F11',
    '#05080c',
    '#0a0a0a',
    '#0c1216',
    '#1a1a1a',
    '#24272E',
    '#25272D',
    '#26282D',
    '#27272A',
    '#3C3C49',
    '#3a3c45',
    '#454545',
    '#171717',
    '#212121',
    '#fafafa',
    '#ffffff',
    '#f4f4f6',
  ];
  const surfaceSelectors = literalSurfaces
    .map((color) => `html[data-omicsbase-theme] .${CSS.escape(`bg-[${color}]`)}`)
    .join(',');

  // OpenHands gold brand hexes baked into utilities (not via CSS vars).
  const goldLiterals = [
    '#C9B974',
    '#c9b974',
    '#C9B97433',
    '#C9B97480',
    '#cfb755',
    '#CFB755',
  ];
  const goldBgSelectors = goldLiterals
    .map((color) => `html[data-omicsbase-theme] .${CSS.escape(`bg-[${color}]`)}`)
    .join(',');
  const goldBorderSelectors = goldLiterals
    .map((color) => `html[data-omicsbase-theme] .${CSS.escape(`border-[${color}]`)}`)
    .join(',');
  const goldHoverBgSelectors = goldLiterals
    .map(
      (color) =>
        `html[data-omicsbase-theme] .${CSS.escape(`hover:bg-[${color}]`)}:hover`,
    )
    .join(',');
  const goldHoverBorderSelectors = goldLiterals
    .map(
      (color) =>
        `html[data-omicsbase-theme] .${CSS.escape(`hover:border-[${color}]`)}:hover`,
    )
    .join(',');

  const style = document.createElement('style');
  style.id = 'omicsbase-theme-style';
  style.textContent = `
    html[data-omicsbase-theme],
    html[data-omicsbase-theme] body,
    html[data-omicsbase-theme] [data-testid="root-layout"],
    html[data-omicsbase-theme] [data-agent-server-ui] {
      background-color: var(--oh-background, var(--base)) !important;
      color: var(--oh-foreground, var(--omicsbase-text)) !important;
    }
    ${surfaceSelectors} {
      background-color: var(--oh-surface, var(--bg-light)) !important;
    }
    html[data-omicsbase-theme] .text-white,
    html[data-omicsbase-theme] .text-gray-100,
    html[data-omicsbase-theme] .text-neutral-100 {
      color: var(--oh-foreground, var(--omicsbase-text)) !important;
    }
    html[data-omicsbase-theme] .text-gray-400,
    html[data-omicsbase-theme] .text-neutral-400,
    html[data-omicsbase-theme] .text-tertiary {
      color: var(--oh-muted, var(--omicsbase-muted)) !important;
    }
    html[data-omicsbase-theme] .border-neutral-700,
    html[data-omicsbase-theme] .border-gray-700 {
      border-color: var(--oh-border, var(--border)) !important;
    }

    /* Primary surfaces — force Soft Daisy teal (overrides baked #c9b974) */
    html[data-omicsbase-theme] .bg-primary,
    html[data-omicsbase-theme] button.bg-primary,
    html[data-omicsbase-theme] .hover\\:bg-primary:hover,
    ${goldBgSelectors},
    ${goldHoverBgSelectors} {
      background-color: var(--color-primary, var(--oh-color-primary, var(--omicsbase-accent))) !important;
      color: var(--oh-accent-foreground) !important;
    }
    html[data-omicsbase-theme] .text-primary,
    html[data-omicsbase-theme] .hover\\:text-primary:hover {
      color: var(--color-primary, var(--oh-color-primary, var(--omicsbase-accent))) !important;
    }
    html[data-omicsbase-theme] .border-primary,
    ${goldBorderSelectors},
    ${goldHoverBorderSelectors} {
      border-color: var(--color-primary, var(--oh-color-primary, var(--omicsbase-accent))) !important;
    }
    html[data-omicsbase-theme] .bg-primary\\/20,
    html[data-omicsbase-theme] .data-\\[hover\\=true\\]\\:bg-primary\\/20[data-hover=true],
    html[data-omicsbase-theme] .data-\\[selectable\\=true\\]\\:focus\\:bg-primary\\/20[data-selectable=true]:focus {
      background-color: color-mix(in srgb, var(--color-primary, var(--oh-color-primary)) 20%, transparent) !important;
    }
    html[data-omicsbase-theme] .text-primary\\/80 {
      color: color-mix(in srgb, var(--color-primary, var(--oh-color-primary)) 80%, transparent) !important;
    }
    html[data-omicsbase-theme] .shadow-primary\\/40,
    html[data-omicsbase-theme] .data-\\[active\\=true\\]\\:shadow-primary\\/40[data-active=true],
    html[data-omicsbase-theme] .data-\\[hover\\=true\\]\\:shadow-primary\\/30[data-hover=true] {
      --tw-shadow-color: color-mix(in srgb, var(--color-primary, var(--oh-color-primary)) 40%, transparent) !important;
    }

    /* Clean Live Report embed — hide redundant OpenHands mini-browser address bar */
    div.border-b:has(input[name="url"]),
    div:has(> iframe[title="Served App"]) > div.border-b {
      display: none !important;
    }

    /* ─── Light Mode Overrides for Agent Chat & Plan Tasks ───────── */
    /* 1. Collapsible plan & observation headers in Soft Daisy Deep Teal */
    html[data-omicsbase-theme="light"] .border-neutral-300 {
      border-color: var(--color-primary, #1c5757) !important;
    }
    html[data-omicsbase-theme="light"] .text-neutral-300 {
      color: var(--color-primary, #1c5757) !important;
    }
    html[data-omicsbase-theme="light"] .fill-neutral-300,
    html[data-omicsbase-theme="light"] svg.fill-neutral-300 {
      fill: var(--color-primary, #1c5757) !important;
    }

    /* 2. Task List, Plan Details & Tool Results (remap hardcoded dark bg-gray-900) */
    html[data-omicsbase-theme="light"] .bg-gray-900 {
      background-color: var(--oh-surface, #f8fafc) !important;
      color: var(--oh-foreground, #0f172a) !important;
      border: 1px solid color-mix(in srgb, var(--color-primary, #1c5757) 22%, #cbd5e1) !important;
      box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04) !important;
    }
    html[data-omicsbase-theme="light"] .bg-gray-900 .text-white {
      color: var(--oh-foreground, #0f172a) !important;
      font-weight: 600 !important;
    }
    html[data-omicsbase-theme="light"] .bg-gray-900 .text-gray-300 {
      color: #334155 !important;
    }
    html[data-omicsbase-theme="light"] .bg-gray-900 .text-gray-400 {
      color: #64748b !important;
    }
    html[data-omicsbase-theme="light"] .bg-gray-900 .text-sm.text-gray-400 {
      color: var(--color-primary, #1c5757) !important;
      font-weight: 600 !important;
    }
    html[data-omicsbase-theme="light"] .bg-gray-900 .border-gray-600 {
      border-color: color-mix(in srgb, var(--color-primary, #1c5757) 38%, transparent) !important;
    }
    html[data-omicsbase-theme="light"] .bg-gray-900 pre {
      color: var(--oh-foreground, #0f172a) !important;
      background-color: transparent !important;
    }

    /* 3. High-contrast Slate & Teal for grey chat elements so nothing fades out */
    html[data-omicsbase-theme="light"] .text-gray-400,
    html[data-omicsbase-theme="light"] .text-neutral-400,
    html[data-omicsbase-theme="light"] .text-gray-500,
    html[data-omicsbase-theme="light"] .text-neutral-500 {
      color: #475569 !important;
    }
    html[data-omicsbase-theme="light"] .border-neutral-600,
    html[data-omicsbase-theme="light"] .border-gray-600 {
      border-color: color-mix(in srgb, var(--color-primary, #1c5757) 20%, #cbd5e1) !important;
    }

    /* 4. Feedback Thumbs (Up / Down) - Fix white-on-white SVG paths */
    html[data-omicsbase-theme="light"] button[data-testid="positive-feedback"],
    html[data-omicsbase-theme="light"] button[data-testid="negative-feedback"] {
      background-color: #f1f5f9 !important;
      border: 1px solid #cbd5e1 !important;
      transition: all 0.15s ease !important;
    }
    html[data-omicsbase-theme="light"] button[data-testid="positive-feedback"]:hover {
      background-color: color-mix(in srgb, var(--color-primary, #1c5757) 12%, #ffffff) !important;
      border-color: var(--color-primary, #1c5757) !important;
    }
    html[data-omicsbase-theme="light"] button[data-testid="negative-feedback"]:hover {
      background-color: #fef2f2 !important;
      border-color: #f87171 !important;
    }
    html[data-omicsbase-theme="light"] button[data-testid="positive-feedback"] svg path,
    html[data-omicsbase-theme="light"] button[data-testid="negative-feedback"] svg path {
      fill: #475569 !important;
      transition: fill 0.15s ease !important;
    }
    html[data-omicsbase-theme="light"] button[data-testid="positive-feedback"]:hover svg path {
      fill: var(--color-primary, #1c5757) !important;
    }
    html[data-omicsbase-theme="light"] button[data-testid="negative-feedback"]:hover svg path {
      fill: #b91c1c !important;
    }

    /* 5. Copy Message Icon - Fix white-on-white SVG path */
    html[data-omicsbase-theme="light"] button[data-testid="copy-to-clipboard"] {
      color: #475569 !important;
    }
    html[data-omicsbase-theme="light"] button[data-testid="copy-to-clipboard"]:hover {
      background-color: #f1f5f9 !important;
      border-radius: 6px !important;
    }
    html[data-omicsbase-theme="light"] button[data-testid="copy-to-clipboard"] svg path {
      fill: #475569 !important;
      transition: fill 0.15s ease !important;
    }
    html[data-omicsbase-theme="light"] button[data-testid="copy-to-clipboard"]:hover svg path {
      fill: var(--color-primary, #1c5757) !important;
    }

    /* 6. Scroll To Bottom Icon - Fix white arrow on white surface */
    html[data-omicsbase-theme="light"] button[data-testid="scroll-to-bottom"] {
      background-color: #ffffff !important;
      border: 1px solid #cbd5e1 !important;
      box-shadow: 0 2px 6px rgba(0, 0, 0, 0.08) !important;
    }
    html[data-omicsbase-theme="light"] button[data-testid="scroll-to-bottom"]:hover {
      background-color: #f8fafc !important;
      border-color: var(--color-primary, #1c5757) !important;
    }
    html[data-omicsbase-theme="light"] button[data-testid="scroll-to-bottom"] svg path {
      fill: var(--color-primary, #1c5757) !important;
    }

    /* 7. Chat Input Box Container, Textarea & Placeholder */
    html[data-omicsbase-theme="light"] div[class*="rounded-[15px]"].bg-\[\#25272D\],
    html[data-omicsbase-theme="light"] div[class*="rounded-[15px]"][class*="bg-"] {
      background-color: #ffffff !important;
      border: 1px solid #cbd5e1 !important;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04) !important;
    }
    html[data-omicsbase-theme="light"] div[class*="rounded-[15px]"]:focus-within {
      border-color: var(--color-primary, #1c5757) !important;
      box-shadow: 0 0 0 2px color-mix(in srgb, var(--color-primary, #1c5757) 20%, transparent) !important;
    }
    html[data-omicsbase-theme="light"] .chat-input {
      color: #0f172a !important;
    }
    html[data-omicsbase-theme="light"] .chat-input:empty:before,
    html[data-omicsbase-theme="light"] .chat-input[data-placeholder]:empty:before {
      color: #64748b !important;
      opacity: 1 !important;
    }

    /* 8. Attach File / Paperclip Icon */
    html[data-omicsbase-theme="light"] button[data-testid="paperclip-icon"] svg,
    html[data-omicsbase-theme="light"] button[data-testid="chat-plus-button"] svg {
      color: var(--color-primary, #1c5757) !important;
      stroke: var(--color-primary, #1c5757) !important;
      transition: all 0.15s ease !important;
    }
    html[data-omicsbase-theme="light"] button[data-testid="paperclip-icon"]:hover svg,
    html[data-omicsbase-theme="light"] button[data-testid="chat-plus-button"]:hover svg {
      color: var(--oh-warning, #154242) !important;
      stroke: var(--oh-warning, #154242) !important;
    }
    html[data-omicsbase-theme="light"] button[data-testid="paperclip-icon"][disabled] svg,
    html[data-omicsbase-theme="light"] button[data-testid="paperclip-icon"].cursor-not-allowed svg {
      color: #94a3b8 !important;
      stroke: #94a3b8 !important;
    }

    /* 9. Send Button (Solid Teal Circle with Crisp White Arrow) */
    html[data-omicsbase-theme="light"] button[data-testid="submit-button"]:not(:disabled) {
      background-color: var(--color-primary, #1c5757) !important;
      border-color: var(--color-primary, #1c5757) !important;
      box-shadow: 0 2px 4px rgba(28, 87, 87, 0.25) !important;
    }
    html[data-omicsbase-theme="light"] button[data-testid="submit-button"]:not(:disabled):hover {
      background-color: var(--oh-warning, #154242) !important;
      border-color: var(--oh-warning, #154242) !important;
    }
    html[data-omicsbase-theme="light"] button[data-testid="submit-button"]:not(:disabled) svg {
      stroke: #ffffff !important;
      color: #ffffff !important;
    }
    html[data-omicsbase-theme="light"] button[data-testid="submit-button"]:disabled {
      background-color: transparent !important;
      border: 1px solid #cbd5e1 !important;
    }
    html[data-omicsbase-theme="light"] button[data-testid="submit-button"]:disabled svg {
      stroke: #94a3b8 !important;
      color: #94a3b8 !important;
    }

    /* 10. Tools & Running / Status Indicators inside Input Box */
    html[data-omicsbase-theme="light"] div[class*="rounded-[15px]"] span.text-white {
      color: #334155 !important;
    }
    html[data-omicsbase-theme="light"] div[class*="rounded-[15px]"] svg[color="#959CB2"] {
      color: var(--color-primary, #1c5757) !important;
    }
    html[data-omicsbase-theme="light"] div[class*="rounded-[15px]"] svg[color="#BCFF8C"] {
      color: #15803d !important;
    }
    html[data-omicsbase-theme="light"] div[class*="rounded-[15px]"] div.bg-\[\#525252\] {
      background-color: #e2e8f0 !important;
      border: 1px solid #cbd5e1 !important;
    }
    html[data-omicsbase-theme="light"] div[class*="rounded-[15px]"] div.bg-\[\#525252\] svg {
      color: #475569 !important;
      stroke: #475569 !important;
      fill: #475569 !important;
    }

    /* 11. Footer Status Bar Pills ("No Repo Connected", "No Branch", etc.) */
    html[data-omicsbase-theme="light"] a[class*="rounded-[100px]"],
    html[data-omicsbase-theme="light"] button[class*="rounded-[100px]"] {
      background-color: #f8fafc !important;
      border-color: #cbd5e1 !important;
      color: #334155 !important;
    }
    html[data-omicsbase-theme="light"] a[class*="rounded-[100px]"] .text-white,
    html[data-omicsbase-theme="light"] button[class*="rounded-[100px]"] .text-white {
      color: #334155 !important;
    }
    html[data-omicsbase-theme="light"] a[class*="rounded-[100px]"] svg,
    html[data-omicsbase-theme="light"] button[class*="rounded-[100px]"] svg {
      color: var(--color-primary, #1c5757) !important;
    }
    html[data-omicsbase-theme="light"] a[class*="rounded-[100px]"] svg path[fill="white"],
    html[data-omicsbase-theme="light"] button[class*="rounded-[100px]"] svg path[fill="white"] {
      fill: var(--color-primary, #1c5757) !important;
    }
  `;

  function notifyChildIframes(mode) {
    document.querySelectorAll('iframe').forEach((frame) => {
      try {
        frame.contentWindow?.postMessage({ type: 'OMICSBASE_THEME', mode }, '*');
      } catch {}
    });
  }

  function commitTheme(mode, colors) {
    lastTheme = { mode, colors };
    if (!applySoftDaisy(mode, colors)) return;
    if (!style.isConnected) document.head.appendChild(style);
    notifyChildIframes(mode);
  }

  window.addEventListener('message', (event) => {
    if (event.source !== window.parent || event.origin !== parentOrigin) return;
    const data = event.data;
    if (data?.type !== 'omicsbase:theme' || !['light', 'dark'].includes(data.mode)) return;
    if (!data.colors || typeof data.colors !== 'object') return;
    if (!required.every((name) => typeof data.colors[name] === 'string')) return;

    try {
      sessionStorage.setItem('omicsbase-parent-origin', parentOrigin);
    } catch {}

    commitTheme(data.mode, data.colors);
  });

  // Re-apply when AgentServerUI / VS Code iframe mounts late.
  const mo = new MutationObserver(() => {
    if (!lastTheme) return;
    let needsResend = false;
    document.querySelectorAll('[data-agent-server-ui]').forEach((el) => {
      if (el.dataset.omicsbaseThemed === '1') return;
      el.dataset.omicsbaseThemed = '1';
      needsResend = true;
    });
    if (needsResend) {
      window.parent.postMessage({ type: 'omicsbase:theme-ready' }, parentOrigin);
    } else {
      applySoftDaisy(lastTheme.mode, lastTheme.colors);
    }
  });
  mo.observe(document.documentElement, { childList: true, subtree: true });

  // React can reset data-theme=dark after Soft Daisy applies — keep it sticky.
  setInterval(() => {
    if (!lastTheme) return;
    applySoftDaisy(lastTheme.mode, lastTheme.colors);
  }, 1500);

  window.parent.postMessage({ type: 'omicsbase:theme-ready' }, parentOrigin);
})();
