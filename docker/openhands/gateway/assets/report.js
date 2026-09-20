(function() {
  const pathParts = window.location.pathname.split('/');
  const conversationId = pathParts[pathParts.indexOf('report') + 1] || 'default';
  const API_BASE = `/api/omicsbase/report/${conversationId}`;

  let currentStatus = null;
  let activeView = 'dashboard';
  let activeSectionTab = 'chapters';
  let userExplicitlySelectedView = false;
  let hasAutoTransitionedToSite = false;

  // DOM Elements
  const dashboardView = document.getElementById('dashboard-view');
  const siteView = document.getElementById('site-view');
  const siteIframe = document.getElementById('site-iframe');
  const tabDashboard = document.getElementById('tab-dashboard');
  const tabSite = document.getElementById('tab-site');
  const pipelineStatusBadge = document.getElementById('pipeline-status-badge');
  const pipelineStatusText = document.getElementById('pipeline-status-text');
  const refreshBtn = document.getElementById('refresh-btn');
  const openNewTabBtn = document.getElementById('open-new-tab-btn');
  const projectTitle = document.getElementById('project-title');

  // Section 1: Option A Pills & Grids
  const pillChapters = document.getElementById('pill-chapters');
  const pillScripts = document.getElementById('pill-scripts');
  const pillCountChapters = document.getElementById('pill-count-chapters');
  const pillCountScripts = document.getElementById('pill-count-scripts');
  const stagesSectionMeta = document.getElementById('stages-section-meta');
  const chaptersGrid = document.getElementById('chapters-grid');
  const scriptsGrid = document.getElementById('scripts-grid');

  // Section 2: Figures
  const figuresSectionTitle = document.getElementById('figures-section-title');
  const figuresSectionCount = document.getElementById('figures-section-count');
  const figuresGrid = document.getElementById('figures-grid');
  const figuresCountMeta = document.getElementById('figures-count-meta');

  // Modal Elements
  const modal = document.getElementById('preview-modal');
  const modalTitle = document.getElementById('modal-title');
  const modalIframe = document.getElementById('modal-iframe');
  const modalImgContainer = document.getElementById('modal-img-container');
  const modalImg = document.getElementById('modal-img');
  const modalCodeContainer = document.getElementById('modal-code-container');
  const modalGutter = document.getElementById('modal-gutter');
  const modalCode = document.getElementById('modal-code');
  const modalCopyBtn = document.getElementById('modal-copy-btn');
  let rawScriptSource = '';
  const modalCopyText = document.getElementById('modal-copy-text');
  const modalCloseBtn = document.getElementById('modal-close-btn');

  // ─── Theme Sync ───────────────────────────────────────────────
  function applyThemeMode(isDark, colors = null) {
    const root = document.documentElement;
    root.classList.toggle('dark', isDark);
    root.classList.toggle('light', !isDark);
    root.style.colorScheme = isDark ? 'dark' : 'light';

    const sunIcon = document.getElementById('theme-icon-sun');
    const moonIcon = document.getElementById('theme-icon-moon');
    if (sunIcon && moonIcon) {
      sunIcon.style.display = isDark ? 'none' : 'block';
      moonIcon.style.display = isDark ? 'block' : 'none';
    }

    if (colors && typeof colors === 'object') {
      if (colors['accent-primary']) root.style.setProperty('--accent', colors['accent-primary']);
      if (colors['surface-primary']) root.style.setProperty('--bg-surface', colors['surface-primary']);
      if (colors['presentation']) root.style.setProperty('--bg-base', colors['presentation']);
      if (colors['border-light']) root.style.setProperty('--border', colors['border-light']);
      if (colors['text-primary']) root.style.setProperty('--text-primary', colors['text-primary']);
      if (colors['text-secondary']) root.style.setProperty('--text-secondary', colors['text-secondary']);
    }
  }

  function syncThemeFromParent() {
    try {
      const localTheme = localStorage.getItem('omicsbase_report_theme');
      const urlTheme = new URLSearchParams(window.location.search).get('theme');
      const cookieMatch = document.cookie.match(/omicsbase_theme=([^;]+)/);
      const cookieTheme = cookieMatch ? cookieMatch[1] : null;
      const explicitTheme = localTheme || urlTheme || cookieTheme;
      if (explicitTheme) {
        const isDark = explicitTheme.toLowerCase() !== 'light';
        if (!isDark) {
          const root = document.documentElement;
          root.style.removeProperty('--accent');
          root.style.removeProperty('--bg-base');
          root.style.removeProperty('--bg-surface');
          root.style.removeProperty('--border');
          root.style.removeProperty('--text-primary');
          root.style.removeProperty('--text-secondary');
        }
        applyThemeMode(isDark);
        return;
      }

      if (window.parent && window.parent !== window) {
        const pDoc = window.parent.document;
        const pRoot = pDoc.documentElement;
        const pBody = pDoc.body;
        const themedEl = pDoc.querySelector('[data-theme]');
        const dataTheme = themedEl?.getAttribute('data-theme') || pRoot.getAttribute('data-theme') || pBody?.getAttribute('data-theme');

        let isDark = true;
        if (dataTheme) {
          isDark = dataTheme.toLowerCase().includes('dark');
        } else if (pRoot.dataset.omicsbaseTheme) {
          isDark = pRoot.dataset.omicsbaseTheme !== 'light';
        } else if (pRoot.classList.contains('light') || pBody?.classList.contains('light')) {
          isDark = false;
        } else if (pRoot.classList.contains('dark') || pBody?.classList.contains('dark')) {
          isDark = true;
        }

        if (!isDark) {
          const root = document.documentElement;
          root.style.removeProperty('--accent');
          root.style.removeProperty('--bg-base');
          root.style.removeProperty('--bg-surface');
          root.style.removeProperty('--border');
          root.style.removeProperty('--text-primary');
          root.style.removeProperty('--text-secondary');
          applyThemeMode(false);
          return;
        }

        const pStyles = window.parent.getComputedStyle(pRoot);
        const accent = pStyles.getPropertyValue('--oh-color-primary').trim() || (isDark ? '#63ccc0' : '#1c5757');
        const bgBase = pStyles.getPropertyValue('--oh-background').trim() || (isDark ? '#0d0f11' : '#f8fafc');
        const bgSurface = pStyles.getPropertyValue('--oh-surface').trim() || (isDark ? '#14171a' : '#ffffff');
        const border = pStyles.getPropertyValue('--border').trim() || (isDark ? '#23272e' : '#e2e8f0');
        const textPrimary = pStyles.getPropertyValue('--text-primary').trim() || (isDark ? '#f3f4f6' : '#0f172a');
        const textSecondary = pStyles.getPropertyValue('--text-secondary').trim() || (isDark ? '#9ca3af' : '#475569');

        const root = document.documentElement;
        root.style.setProperty('--accent', accent);
        root.style.setProperty('--accent-hover', isDark ? '#7de0d5' : '#236d6d');
        root.style.setProperty('--accent-subtle', isDark ? 'rgba(99, 204, 192, 0.14)' : 'rgba(28, 87, 87, 0.12)');
        root.style.setProperty('--accent-border', isDark ? 'rgba(99, 204, 192, 0.28)' : 'rgba(28, 87, 87, 0.28)');
        root.style.setProperty('--bg-base', bgBase);
        root.style.setProperty('--bg-surface', bgSurface);
        root.style.setProperty('--border', border);
        root.style.setProperty('--text-primary', textPrimary);
        root.style.setProperty('--text-secondary', textSecondary);
        applyThemeMode(isDark);
      }
    } catch (e) {}
  }

  syncThemeFromParent();
  try {
    if (window.parent && window.parent !== window) {
      const pObserver = new MutationObserver(() => syncThemeFromParent());
      pObserver.observe(window.parent.document.documentElement, {
        attributes: true,
        attributeFilter: ['class', 'data-omicsbase-theme', 'data-theme', 'style']
      });
      if (window.parent.document.body) {
        pObserver.observe(window.parent.document.body, {
          attributes: true,
          attributeFilter: ['class', 'data-theme', 'style']
        });
      }
    }
  } catch (e) {}

  window.addEventListener('message', (event) => {
    if (event.data?.type === 'OMICSBASE_THEME' || event.data?.type === 'omicsbase:theme') {
      const isDark = event.data.mode === 'dark' || (!event.data.mode && document.documentElement.classList.contains('dark'));
      applyThemeMode(isDark, event.data.colors);
      syncThemeFromParent();
    } else if (
      event.data?.type === 'OMICSBASE_AGENT_RUNNING' ||
      event.data?.type === 'OMICSBASE_PIPELINE_START' ||
      event.data?.type === 'OMICSBASE_WAKEUP'
    ) {
      startPolling(3000);
      fetchStatus();
    }
  });

  // ─── Section 1 Tab Switching (Option A) ────────────────────────
  pillChapters.addEventListener('click', () => setSectionTab('chapters'));
  pillScripts.addEventListener('click', () => setSectionTab('scripts'));

  function setSectionTab(tab) {
    activeSectionTab = tab;
    if (tab === 'chapters') {
      pillChapters.classList.add('active');
      pillChapters.setAttribute('aria-selected', 'true');
      pillScripts.classList.remove('active');
      pillScripts.setAttribute('aria-selected', 'false');
      chaptersGrid.style.display = 'grid';
      scriptsGrid.style.display = 'none';
    } else {
      pillScripts.classList.add('active');
      pillScripts.setAttribute('aria-selected', 'true');
      pillChapters.classList.remove('active');
      pillChapters.setAttribute('aria-selected', 'false');
      chaptersGrid.style.display = 'none';
      scriptsGrid.style.display = 'grid';
    }
    updateSectionMeta();
  }

  function updateSectionMeta() {
    if (!currentStatus) return;
    if (activeSectionTab === 'chapters') {
      const chapters = currentStatus.chapters || [];
      const completed = chapters.filter(c => c.status === 'completed' || c.compiled).length;
      stagesSectionMeta.textContent = `${completed} of ${chapters.length} chapters compiled`;
    } else {
      const scripts = currentStatus.scripts || [];
      const completed = scripts.filter(s => s.status === 'completed').length;
      stagesSectionMeta.textContent = `${completed} of ${scripts.length} scripts executed`;
    }
  }

  // ─── Adaptive Lifecycle Polling ───────────────────────────────
  let pollTimer = null;
  let isPolling = false;
  let consecutiveSettledPolls = 0;

  function startPolling(intervalMs = 3000) {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(fetchStatus, intervalMs);
    isPolling = true;
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    isPolling = false;
    consecutiveSettledPolls = 0;
  }

  async function fetchStatus() {
    try {
      const res = await fetch(`${API_BASE}/status`);
      if (!res.ok) return;
      const data = await res.json();
      currentStatus = data;
      updateUI(data);
    } catch (e) {
      console.warn('[OmicsBase Report] Status fetch warning:', e);
    }
  }

  function updateUI(data) {
    syncThemeFromParent();

    // Project Title
    if (data.project_title) {
      projectTitle.textContent = data.project_title;
      document.title = `${data.project_title} — Report`;
    }

    const chapters = data.chapters || [];
    const scripts = data.scripts || [];
    const compiledCount = chapters.filter(c => c.compiled || c.status === 'completed').length;
    const totalChapters = chapters.length;
    const totalScripts = scripts.length;

    // Auto-switch tab if only one exists
    if (totalChapters === 0 && totalScripts > 0 && activeSectionTab === 'chapters') {
      setSectionTab('scripts');
    }

    // Header Mode Switcher
    if (data.has_site) {
      tabSite.classList.remove('disabled');
      tabSite.title = "Full Compiled Quarto Book (Ready)";
    } else {
      tabSite.title = "Full Book not compiled yet (Viewing Report Blueprint)";
    }

    // Section 1: Pill counts & metadata
    pillCountChapters.textContent = totalChapters;
    pillCountScripts.textContent = totalScripts;
    updateSectionMeta();

    // Section 2: Figures
    figuresSectionTitle.textContent = 'Figures';
    figuresSectionCount.textContent = `(${data.figures?.length || 0})`;
    if (data.tables?.length) {
      figuresCountMeta.textContent = `${data.figures?.length || 0} plots • ${data.tables.length} tables`;
    } else {
      figuresCountMeta.textContent = `${data.figures?.length || 0} plots`;
    }

    // Header Pipeline Status Badge & Adaptive Sleep/Wake Lifecycle
    const anyRunning = chapters.some(c => c.status === 'running') || scripts.some(s => s.status === 'running');
    const anyQueued = chapters.some(c => c.status === 'queued') || scripts.some(s => s.status === 'queued');
    const anyFailed = chapters.some(c => c.status === 'failed') || scripts.some(s => s.status === 'failed');
    const isAgentRunning = Boolean(data.is_agent_running);
    const isPipelineActive = isAgentRunning || anyRunning || anyQueued;

    if (anyFailed && !isPipelineActive) {
      pipelineStatusBadge.className = 'badge badge-idle';
      pipelineStatusBadge.style.color = 'var(--danger)';
      pipelineStatusBadge.style.borderColor = 'rgba(239, 68, 68, 0.3)';
      pipelineStatusBadge.title = 'Issues detected in pipeline';
      pipelineStatusBadge.innerHTML = '<span class="badge-dot" style="background:var(--danger);"></span><span>Alert</span>';
      stopPolling();
    } else if (data.has_site && !isPipelineActive && (totalChapters === 0 || compiledCount === totalChapters)) {
      pipelineStatusBadge.className = 'badge badge-ready';
      pipelineStatusBadge.style.color = '';
      pipelineStatusBadge.style.borderColor = '';
      pipelineStatusBadge.title = 'Report ready';
      pipelineStatusBadge.innerHTML = '<span class="badge-dot"></span><span>Ready</span>';
      if (!hasAutoTransitionedToSite && !userExplicitlySelectedView) {
        hasAutoTransitionedToSite = true;
        setModeView('site');
      }
      consecutiveSettledPolls++;
      if (consecutiveSettledPolls >= 1) {
        stopPolling();
      }
    } else if (anyRunning || isAgentRunning) {
      pipelineStatusBadge.className = 'badge badge-generating';
      pipelineStatusBadge.style.color = '';
      pipelineStatusBadge.style.borderColor = '';
      pipelineStatusBadge.title = isAgentRunning ? 'Agent is actively generating analysis...' : 'Pipeline actively running';
      pipelineStatusBadge.innerHTML = '<span class="badge-dot badge-pulse"></span><span>Active</span>';
      if (!isPolling) startPolling(3000);
      consecutiveSettledPolls = 0;
    } else if (totalChapters > 0 && compiledCount < totalChapters) {
      pipelineStatusBadge.className = 'badge badge-generating';
      pipelineStatusBadge.style.color = '';
      pipelineStatusBadge.style.borderColor = '';
      pipelineStatusBadge.title = `Rendering chapters (${compiledCount}/${totalChapters})`;
      pipelineStatusBadge.innerHTML = `<span class="badge-dot badge-pulse"></span><span>Active</span>`;
      if (!isPolling) startPolling(3000);
      consecutiveSettledPolls = 0;
    } else if (totalScripts > 0 && anyQueued) {
      pipelineStatusBadge.className = 'badge badge-generating';
      pipelineStatusBadge.style.color = '';
      pipelineStatusBadge.style.borderColor = '';
      pipelineStatusBadge.title = 'Pipeline scripts queued';
      pipelineStatusBadge.innerHTML = '<span class="badge-dot badge-pulse"></span><span>Active</span>';
      if (!isPolling) startPolling(3000);
      consecutiveSettledPolls = 0;
    } else {
      pipelineStatusBadge.className = 'badge badge-idle';
      pipelineStatusBadge.style.color = '';
      pipelineStatusBadge.style.borderColor = '';
      pipelineStatusBadge.title = 'Idle';
      pipelineStatusBadge.innerHTML = '<span class="badge-dot"></span><span>Idle</span>';
      consecutiveSettledPolls++;
      if (consecutiveSettledPolls >= 1) {
        stopPolling();
      }
    }

    renderChapters(chapters);
    renderScripts(scripts);
    renderFigures(data.figures || []);
  }

  function renderChapters(chapters) {
    chaptersGrid.innerHTML = '';
    if (!chapters || chapters.length === 0) {
      chaptersGrid.innerHTML = `
        <div style="padding: 24px; color: var(--text-muted); grid-column: 1 / -1; border: 1px dashed var(--border); border-radius: 7px; text-align: center;">
          <div style="font-weight: 600; color: var(--text-secondary); margin-bottom: 4px;">No report chapters found</div>
          <div style="font-size: 11.5px;">Quarto chapters will appear here as pages are generated</div>
        </div>`;
      return;
    }

    chapters.forEach(ch => {
      const status = (ch.status || (ch.compiled ? 'completed' : 'queued')).toLowerCase();
      const card = document.createElement('div');
      card.className = `chapter-card state-${status}`;
      const statusLabel = status.charAt(0).toUpperCase() + status.slice(1);
      card.title = `${ch.title || ch.name} (${ch.name}) • ${statusLabel}`;

      const main = document.createElement('div');
      main.className = 'chapter-main';

      const iconCol = document.createElement('div');
      iconCol.className = 'chapter-icon-col';
      iconCol.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"></path><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"></path></svg>';

      const titleCol = document.createElement('div');
      titleCol.className = 'chapter-title-col';
      const title = document.createElement('div');
      title.className = 'chapter-title';
      title.textContent = ch.title || formatChapterTitle(ch.name);
      const file = document.createElement('div');
      file.className = 'chapter-file';
      file.textContent = ch.name;
      titleCol.appendChild(title);
      titleCol.appendChild(file);

      main.appendChild(iconCol);
      main.appendChild(titleCol);
      card.appendChild(main);

      if (ch.compiled || status === 'completed') {
        const actionBtn = document.createElement('button');
        actionBtn.className = 'card-action-btn';
        actionBtn.title = 'Read chapter';
        actionBtn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>';
        actionBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          openChapterModal(ch);
        });
        card.appendChild(actionBtn);
        card.addEventListener('click', () => openChapterModal(ch));
      }

      chaptersGrid.appendChild(card);
    });
  }

  function renderScripts(scripts) {
    scriptsGrid.innerHTML = '';
    if (!scripts || scripts.length === 0) {
      scriptsGrid.innerHTML = `
        <div style="padding: 24px; color: var(--text-muted); grid-column: 1 / -1; border: 1px dashed var(--border); border-radius: 7px; text-align: center;">
          <div style="font-weight: 600; color: var(--text-secondary); margin-bottom: 4px;">No R scripts found</div>
          <div style="font-size: 11.5px;">Pipeline scripts in R/ will appear here as they are added</div>
        </div>`;
      return;
    }

    scripts.forEach(sc => {
      const status = (sc.status || 'queued').toLowerCase();
      const card = document.createElement('div');
      card.className = `chapter-card state-${status}`;
      const statusLabel = status.charAt(0).toUpperCase() + status.slice(1);
      card.title = `${sc.title || sc.name} (${sc.path || sc.name}) • ${statusLabel}`;

      const main = document.createElement('div');
      main.className = 'chapter-main';

      const iconCol = document.createElement('div');
      iconCol.className = 'chapter-icon-col';
      iconCol.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 18 22 12 16 6"></polyline><polyline points="8 6 2 12 8 18"></polyline></svg>';

      const titleCol = document.createElement('div');
      titleCol.className = 'chapter-title-col';
      const title = document.createElement('div');
      title.className = 'chapter-title';
      title.textContent = sc.title || formatChapterTitle(sc.name);
      const file = document.createElement('div');
      file.className = 'chapter-file';
      file.textContent = sc.path || sc.name;
      titleCol.appendChild(title);
      titleCol.appendChild(file);

      main.appendChild(iconCol);
      main.appendChild(titleCol);
      card.appendChild(main);

      const actionBtn = document.createElement('button');
      actionBtn.className = 'card-action-btn';
      actionBtn.title = 'View script code';
      actionBtn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 18 22 12 16 6"></polyline><polyline points="8 6 2 12 8 18"></polyline></svg>';
      actionBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        openScriptModal(sc);
      });
      card.appendChild(actionBtn);
      card.addEventListener('click', () => openScriptModal(sc));

      scriptsGrid.appendChild(card);
    });
  }

  function renderFigures(figures) {
    figuresGrid.innerHTML = '';
    if (figures.length === 0) {
      figuresGrid.innerHTML = '<div style="padding: 18px; color: var(--text-muted); grid-column: 1 / -1; border: 1px dashed var(--border); border-radius: 7px; text-align: center; font-size: 12px;">Figures will appear here in real-time as plots are generated</div>';
      return;
    }

    figures.forEach(fig => {
      const card = document.createElement('div');
      card.className = 'figure-card';
      card.title = `Click to zoom: ${fig.name}`;

      const preview = document.createElement('div');
      preview.className = 'figure-preview';
      const img = document.createElement('img');
      const url = `${API_BASE}/site/${fig.path}?t=${Date.now()}`;
      img.src = url;
      img.alt = fig.name;
      img.loading = 'lazy';
      preview.appendChild(img);

      const zoomBadge = document.createElement('div');
      zoomBadge.className = 'figure-zoom-badge';
      zoomBadge.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line><line x1="11" y1="8" x2="11" y2="14"></line><line x1="8" y1="11" x2="14" y2="11"></line></svg>';
      preview.appendChild(zoomBadge);

      const info = document.createElement('div');
      info.className = 'figure-info';
      const name = document.createElement('div');
      name.className = 'figure-name';
      name.textContent = formatFigureTitle(fig.name);
      name.title = fig.name;

      const meta = document.createElement('div');
      meta.className = 'figure-meta';
      meta.innerHTML = `<span>${fig.name}</span><span>${(fig.size / 1024).toFixed(1)} KB</span>`;
      info.appendChild(name);
      info.appendChild(meta);

      card.appendChild(preview);
      card.appendChild(info);

      card.addEventListener('click', () => {
        modalTitle.textContent = formatFigureTitle(fig.name);
        modalImg.src = url;
        modalIframe.style.display = 'none';
        modalCodeContainer.style.display = 'none';
        modalCopyBtn.style.display = 'none';
        modalImgContainer.style.display = 'flex';
        modal.classList.add('open');
      });

      figuresGrid.appendChild(card);
    });
  }

  function formatFigureTitle(filename) {
    const base = filename.replace(/\.(png|jpg|jpeg|svg|webp)$/i, '');
    return base.split(/[-_]/).map(w => {
      if (['pca', 'qc', 'kw', 'lmm', 'bm', 'de'].includes(w.toLowerCase())) return w.toUpperCase();
      return w.charAt(0).toUpperCase() + w.slice(1);
    }).join(' ');
  }

  function formatChapterTitle(filename) {
    const base = filename.replace(/\.(qmd|R|py)$/i, '');
    return base.split(/[-_]/).map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
  }

  function openChapterModal(ch) {
    modalTitle.textContent = ch.title || ch.name;
    modalIframe.src = `${API_BASE}/site/${ch.html_path || ch.name.replace(/\.qmd$/, '.html')}`;
    modalImgContainer.style.display = 'none';
    modalCodeContainer.style.display = 'none';
    modalCopyBtn.style.display = 'none';
    modalIframe.style.display = 'block';
    modal.classList.add('open');
  }

  function renderCodeWithHighlight(code, filename) {
    let lang = 'r';
    if (filename.endsWith('.py')) lang = 'python';
    else if (filename.endsWith('.sh') || filename.endsWith('.bash')) lang = 'bash';

    let highlightedHtml = '';
    if (window.Prism && Prism.languages && Prism.languages[lang]) {
      highlightedHtml = Prism.highlight(code, Prism.languages[lang], lang);
    } else {
      highlightedHtml = code
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
    }

    modalCode.innerHTML = highlightedHtml;

    // Line numbers gutter
    const lines = code.split('\n');
    const count = lines.length;
    const gutterNums = [];
    for (let i = 1; i <= count; i++) {
      gutterNums.push(i);
    }
    if (modalGutter) modalGutter.textContent = gutterNums.join('\n');
  }

  async function openScriptModal(sc) {
    modalTitle.textContent = `${sc.name} — R Pipeline Script`;
    modalIframe.style.display = 'none';
    modalImgContainer.style.display = 'none';
    modalCodeContainer.style.display = 'block';
    modalCopyBtn.style.display = 'inline-flex';
    modalCopyText.textContent = 'Copy';
    if (modalGutter) modalGutter.textContent = '';
    modalCode.textContent = 'Loading script source...';
    modal.classList.add('open');

    try {
      const res = await fetch(`${API_BASE}/file/${sc.path || sc.name}`);
      if (res.ok) {
        const code = await res.text();
        rawScriptSource = code;
        renderCodeWithHighlight(code, sc.name || '');
      } else {
        rawScriptSource = '';
        if (modalGutter) modalGutter.textContent = '';
        modalCode.textContent = `Unable to load script (HTTP ${res.status})`;
      }
    } catch (err) {
      rawScriptSource = '';
      if (modalGutter) modalGutter.textContent = '';
      modalCode.textContent = `Error fetching script: ${err.message}`;
    }
  }

  function closeModal() {
    modal.classList.remove('open');
    modalIframe.src = '';
    modalImg.src = '';
    modalCode.textContent = '';
    if (modalGutter) modalGutter.textContent = '';
    rawScriptSource = '';
    modalCopyBtn.style.display = 'none';
  }

  modalCloseBtn.addEventListener('click', closeModal);
  modal.addEventListener('click', (e) => {
    if (e.target === modal) closeModal();
  });
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && modal.classList.contains('open')) closeModal();
  });

  modalCopyBtn.addEventListener('click', async () => {
    const text = rawScriptSource || modalCode.textContent;
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      modalCopyText.textContent = 'Copied!';
      setTimeout(() => { modalCopyText.textContent = 'Copy'; }, 2000);
    } catch (e) {
      console.warn('Copy failed', e);
    }
  });

  // View Switching
  function setModeView(view) {
    activeView = view;
    if (view === 'site') {
      tabSite.classList.add('active');
      tabSite.setAttribute('aria-selected', 'true');
      tabDashboard.classList.remove('active');
      tabDashboard.setAttribute('aria-selected', 'false');
      dashboardView.style.display = 'none';
      siteView.style.display = 'block';
      if (!siteIframe.src || siteIframe.src === 'about:blank' || !siteIframe.src.includes(`${API_BASE}/site/index.html`)) {
        siteIframe.src = `${API_BASE}/site/index.html`;
      }
    } else {
      tabDashboard.classList.add('active');
      tabDashboard.setAttribute('aria-selected', 'true');
      tabSite.classList.remove('active');
      tabSite.setAttribute('aria-selected', 'false');
      dashboardView.style.display = 'flex';
      siteView.style.display = 'none';
    }
  }

  tabDashboard.addEventListener('click', () => {
    userExplicitlySelectedView = true;
    setModeView('dashboard');
  });

  tabSite.addEventListener('click', () => {
    userExplicitlySelectedView = true;
    setModeView('site');
  });

  const themeToggleBtn = document.getElementById('theme-toggle-btn');
  if (themeToggleBtn) {
    themeToggleBtn.addEventListener('click', () => {
      const isCurrentlyDark = document.documentElement.classList.contains('dark');
      const nextDark = !isCurrentlyDark;
      localStorage.setItem('omicsbase_report_theme', nextDark ? 'dark' : 'light');
      document.cookie = `omicsbase_theme=${nextDark ? 'dark' : 'light'}; path=/; max-age=604800; SameSite=Lax`;
      if (!nextDark) {
        const root = document.documentElement;
        root.style.removeProperty('--accent');
        root.style.removeProperty('--bg-base');
        root.style.removeProperty('--bg-surface');
        root.style.removeProperty('--border');
        root.style.removeProperty('--text-primary');
        root.style.removeProperty('--text-secondary');
      }
      applyThemeMode(nextDark);
    });
  }

  openNewTabBtn.addEventListener('click', () => {
    if (currentStatus?.has_site) {
      window.open(`${API_BASE}/site/index.html`, '_blank');
    } else {
      window.open(window.location.href, '_blank');
    }
  });

  refreshBtn.addEventListener('click', () => {
    startPolling(3000);
    fetchStatus();
  });

  // Wakeup triggers: Tab focus & visibility change
  window.addEventListener('focus', () => {
    if (!isPolling) fetchStatus();
  });

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && !isPolling) fetchStatus();
  });

  // Initial fetch on mount (starts active poll, auto-stops when settled)
  startPolling(3000);
  fetchStatus();
})();
