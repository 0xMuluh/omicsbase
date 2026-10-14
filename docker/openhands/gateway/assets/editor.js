(function() {
  const pathParts = window.location.pathname.split('/');
  const conversationId = pathParts[pathParts.indexOf('editor') + 1] || 'default';
  const API_BASE = `/api/omicsbase/editor/${conversationId}`;

  let monacoInstance = null;
  let editorInstance = null;
  let fileTreeData = [];
  let expandedFolders = new Set(['', 'R', 'data', 'results']);
  let openTabs = []; // [{ path, name, content, dirty: boolean, model }]
  let activeTabPath = null;
  let isSaving = false;

  // DOM elements
  const treeRoot = document.getElementById('tree-root');
  const treeSearch = document.getElementById('tree-search');
  const treeSearchClear = document.getElementById('tree-search-clear');
  const refreshBtn = document.getElementById('refresh-tree-btn');
  const expandAllBtn = document.getElementById('expand-all-btn');
  const collapseAllBtn = document.getElementById('collapse-all-btn');
  const tabsBar = document.getElementById('tabs-bar');
  const monacoRoot = document.getElementById('monaco-root');
  const fallbackTextarea = document.getElementById('fallback-textarea');
  const imageViewer = document.getElementById('image-viewer');
  const imageElement = document.getElementById('image-element');
  const tableViewer = document.getElementById('table-viewer');
  const tableContent = document.getElementById('table-content');
  const tableInfo = document.getElementById('table-info');
  const tableToggleSource = document.getElementById('table-toggle-source');
  const emptyState = document.getElementById('empty-state');
  const breadcrumb = document.getElementById('breadcrumb');
  const saveStatus = document.getElementById('save-status');
  const saveBtn = document.getElementById('save-btn');

  // Initialize Monaco
  if (typeof require !== 'undefined') {
    require.config({ paths: { vs: 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.45.0/min/vs' } });
    require(['vs/editor/editor.main'], function(monaco) {
      monacoInstance = monaco;
      monaco.editor.defineTheme('omicsbase-dark', {
        base: 'vs-dark',
        inherit: true,
        rules: [
          { token: 'comment', foreground: '6b7280', fontStyle: 'italic' },
          { token: 'keyword', foreground: '38bdf8' },
          { token: 'string', foreground: '34d399' },
          { token: 'number', foreground: 'f59e0b' },
          { token: 'type', foreground: '818cf8' },
        ],
        colors: {
          'editor.background': '#0d0f11',
          'editor.foreground': '#f3f4f6',
          'editor.lineHighlightBackground': '#14171a',
          'editorLineNumber.foreground': '#4b5563',
          'editorLineNumber.activeForeground': '#9ca3af',
          'editorIndentGuide.background': '#1f2428',
          'editorIndentGuide.activeBackground': '#374151',
          'editor.selectionBackground': 'rgba(14, 165, 233, 0.25)',
        }
      });

      editorInstance = monaco.editor.create(monacoRoot, {
        theme: 'omicsbase-dark',
        fontSize: 13,
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        automaticLayout: true,
        tabSize: 2,
        wordWrap: 'on',
        lineNumbers: 'on',
        folding: true,
        foldingHighlight: true,
        foldingStrategy: 'auto',
        showFoldingControls: 'always',
      });

      // Ctrl+S / Cmd+S save command
      editorInstance.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
        saveCurrentFile();
      });

      // Track edits
      editorInstance.onDidChangeModelContent(() => {
        if (!activeTabPath) return;
        const tab = openTabs.find(t => t.path === activeTabPath);
        if (tab && !tab.dirty) {
          tab.dirty = true;
          updateTabState(tab);
          updateSaveBadge(false);
        }
      });

      // If an active tab was loaded before monaco ready
      if (activeTabPath) {
        renderActiveFile();
      }
    });
  } else {
    fallbackTextarea.style.display = 'block';
    monacoRoot.style.display = 'none';
    fallbackTextarea.addEventListener('input', () => {
      if (!activeTabPath) return;
      const tab = openTabs.find(t => t.path === activeTabPath);
      if (tab && !tab.dirty) {
        tab.dirty = true;
        updateTabState(tab);
        updateSaveBadge(false);
      }
    });
  }

  function getLanguageForExt(ext) {
    switch (ext) {
      case 'qmd':
      case 'rmd':
      case 'md': return 'markdown';
      case 'r': return 'r';
      case 'py': return 'python';
      case 'json': return 'json';
      case 'yml':
      case 'yaml': return 'yaml';
      case 'css': return 'css';
      case 'js': return 'javascript';
      case 'ts': return 'typescript';
      case 'html': return 'html';
      case 'sh':
      case 'bash': return 'shell';
      case 'sql': return 'sql';
      case 'csv':
      case 'tsv': return 'plaintext';
      default: return 'plaintext';
    }
  }

  function getFileIcon(name, isDir) {
    if (isDir) return '<span class="tree-icon icon-folder">📁</span>';
    const ext = name.split('.').pop().toLowerCase();
    if (ext === 'qmd' || ext === 'rmd') return '<span class="tree-icon icon-qmd">Q</span>';
    if (ext === 'r') return '<span class="tree-icon icon-r">R</span>';
    if (ext === 'py') return '<span class="tree-icon icon-py">PY</span>';
    if (['csv', 'tsv', 'xlsx', 'parquet'].includes(ext)) return '<span class="tree-icon icon-data">DT</span>';
    if (['png', 'jpg', 'jpeg', 'svg', 'webp', 'gif'].includes(ext)) return '<span class="tree-icon icon-img">IMG</span>';
    if (['json', 'yml', 'yaml', 'toml', 'ini'].includes(ext)) return '<span class="tree-icon icon-cfg">{}</span>';
    if (['css', 'html', 'js', 'ts'].includes(ext)) return '<span class="tree-icon icon-web">&lt;&gt;</span>';
    return '<span class="tree-icon icon-file">📄</span>';
  }

  // Load Tree
  async function loadTree() {
    try {
      const res = await fetch(`${API_BASE}/tree`);
      if (!res.ok) throw new Error('Failed to load file tree');
      fileTreeData = await res.json();
      renderTree();
      // Auto open index.qmd or first qmd file if no tabs open
      if (openTabs.length === 0) {
        const firstFile = findFile(fileTreeData, f => f.name === 'index.qmd')
          || findFile(fileTreeData, f => f.name.endsWith('.qmd'))
          || findFile(fileTreeData, f => f.type === 'file');
        if (firstFile) {
          openFile(firstFile.path);
        }
      }
    } catch (e) {
      treeRoot.innerHTML = `<div style="padding: 12px; color: var(--danger);">Failed to load files</div>`;
    }
  }

  function findFile(nodes, predicate) {
    for (const node of nodes) {
      if (node.type === 'file' && predicate(node)) return node;
      if (node.type === 'directory' && node.children) {
        const found = findFile(node.children, predicate);
        if (found) return found;
      }
    }
    return null;
  }

  function expandAll() {
    function collect(nodes) {
      nodes.forEach(n => {
        if (n.type === 'directory') {
          expandedFolders.add(n.path);
          if (n.children) collect(n.children);
        }
      });
    }
    collect(fileTreeData);
    renderTree();
  }

  function collapseAll() {
    expandedFolders.clear();
    renderTree();
  }

  if (expandAllBtn) expandAllBtn.addEventListener('click', () => expandAll());
  if (collapseAllBtn) collapseAllBtn.addEventListener('click', () => collapseAll());

  function nodeMatchesOrHasChild(node, q) {
    if (node.name.toLowerCase().includes(q) || node.path.toLowerCase().includes(q)) return true;
    if (node.type === 'directory' && node.children) {
      return node.children.some(child => nodeMatchesOrHasChild(child, q));
    }
    return false;
  }

  function renderTree() {
    const query = treeSearch.value.trim().toLowerCase();
    treeRoot.innerHTML = '';

    function buildNode(node, depth) {
      if (query && !nodeMatchesOrHasChild(node, query)) {
        return null;
      }

      const isDir = node.type === 'directory';
      const isExpanded = query ? true : expandedFolders.has(node.path);

      const el = document.createElement('div');
      el.className = `tree-item ${activeTabPath === node.path ? 'active' : ''}`;
      el.style.paddingLeft = `${depth * 14 + 6}px`;

      const toggle = document.createElement('span');
      toggle.className = 'folder-toggle';
      toggle.textContent = isDir ? (isExpanded ? '▾' : '▸') : '';

      const icon = document.createElement('span');
      icon.innerHTML = getFileIcon(node.name, isDir);

      const label = document.createElement('span');
      label.className = 'tree-name';
      label.textContent = node.name;
      label.title = node.path;

      el.appendChild(toggle);
      el.appendChild(icon);
      el.appendChild(label);

      el.addEventListener('click', (e) => {
        e.stopPropagation();
        if (isDir) {
          if (isExpanded) expandedFolders.delete(node.path);
          else expandedFolders.add(node.path);
          renderTree();
        } else {
          openFile(node.path);
        }
      });

      const container = document.createElement('div');
      container.appendChild(el);

      if (isDir && isExpanded && node.children) {
        const childContainer = document.createElement('div');
        let hasVisibleChild = false;
        node.children.forEach(child => {
          const childEl = buildNode(child, depth + 1);
          if (childEl) {
            childContainer.appendChild(childEl);
            hasVisibleChild = true;
          }
        });
        if (hasVisibleChild || !query) {
          container.appendChild(childContainer);
        } else if (query && !hasVisibleChild) {
          return null;
        }
      }

      return container;
    }

    let renderedCount = 0;
    fileTreeData.forEach(node => {
      const nodeEl = buildNode(node, 0);
      if (nodeEl) {
        treeRoot.appendChild(nodeEl);
        renderedCount++;
      }
    });

    if (query && renderedCount === 0) {
      treeRoot.innerHTML = `<div style="padding: 16px 12px; color: var(--text-muted); font-size: 12px; text-align: center;">No files match "${escapeHtml(query)}"</div>`;
    }
  }

  treeSearch.addEventListener('input', () => {
    if (treeSearchClear) {
      treeSearchClear.style.display = treeSearch.value.trim() ? 'block' : 'none';
    }
    renderTree();
  });

  if (treeSearchClear) {
    treeSearchClear.addEventListener('click', () => {
      treeSearch.value = '';
      treeSearchClear.style.display = 'none';
      renderTree();
      treeSearch.focus();
    });
  }

  treeSearch.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      treeSearch.value = '';
      if (treeSearchClear) treeSearchClear.style.display = 'none';
      renderTree();
    }
  });

  refreshBtn.addEventListener('click', () => loadTree());

  // Open file
  async function openFile(filePath) {
    let tab = openTabs.find(t => t.path === filePath);
    if (!tab) {
      const ext = filePath.split('.').pop().toLowerCase();
      const isImg = ['png', 'jpg', 'jpeg', 'svg', 'webp', 'gif'].includes(ext);
      const isTable = ['csv', 'tsv'].includes(ext);

      updateSaveBadge('loading');
      try {
        const res = await fetch(`${API_BASE}/file?path=${encodeURIComponent(filePath)}`);
        if (!res.ok) throw new Error('Could not read file');

        let content = '';
        if (!isImg) {
          const data = await res.json();
          content = data.content || '';
        }

        tab = {
          path: filePath,
          name: filePath.split('/').pop(),
          content: content,
          dirty: false,
          ext: ext,
          isImg: isImg,
          isTable: isTable,
          viewMode: isTable ? 'table' : 'editor',
          model: null
        };

        if (monacoInstance && !isImg) {
          const lang = getLanguageForExt(ext);
          tab.model = monacoInstance.editor.createModel(content, lang);
        }

        openTabs.push(tab);
      } catch (err) {
        alert(`Failed to open ${filePath}: ${err.message}`);
        updateSaveBadge(true);
        return;
      }
    }

    activeTabPath = filePath;
    renderTabs();
    renderActiveFile();
    renderTree();
  }

  function renderTabs() {
    tabsBar.innerHTML = '';
    openTabs.forEach(tab => {
      const el = document.createElement('div');
      el.className = `tab ${tab.path === activeTabPath ? 'active' : ''}`;

      const icon = document.createElement('span');
      icon.innerHTML = getFileIcon(tab.name, false);

      const name = document.createElement('span');
      name.textContent = tab.name;

      el.appendChild(icon);
      el.appendChild(name);

      if (tab.dirty) {
        const dot = document.createElement('span');
        dot.className = 'tab-dirty';
        el.appendChild(dot);
      }

      const closeBtn = document.createElement('span');
      closeBtn.className = 'tab-close';
      closeBtn.textContent = '×';
      closeBtn.title = 'Close tab';
      closeBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        closeTab(tab.path);
      });
      el.appendChild(closeBtn);

      el.addEventListener('click', () => {
        activeTabPath = tab.path;
        renderTabs();
        renderActiveFile();
        renderTree();
      });

      tabsBar.appendChild(el);
    });
  }

  function updateTabState(tab) {
    renderTabs();
  }

  function closeTab(filePath) {
    const idx = openTabs.findIndex(t => t.path === filePath);
    if (idx === -1) return;
    const tab = openTabs[idx];
    if (tab.dirty) {
      if (!confirm(`Save changes to ${tab.name} before closing?`)) {
        // drop changes
      } else {
        saveCurrentFile();
      }
    }
    if (tab.model) tab.model.dispose();
    openTabs.splice(idx, 1);

    if (activeTabPath === filePath) {
      activeTabPath = openTabs.length > 0 ? openTabs[Math.max(0, idx - 1)].path : null;
    }
    renderTabs();
    renderActiveFile();
    renderTree();
  }

  function renderActiveFile() {
    const tab = openTabs.find(t => t.path === activeTabPath);
    if (!tab) {
      breadcrumb.textContent = 'No file selected';
      emptyState.style.display = 'flex';
      monacoRoot.style.display = 'none';
      imageViewer.style.display = 'none';
      tableViewer.style.display = 'none';
      fallbackTextarea.style.display = 'none';
      updateSaveBadge(true);
      return;
    }

    breadcrumb.textContent = tab.path;
    emptyState.style.display = 'none';
    updateSaveBadge(!tab.dirty);

    if (tab.isImg) {
      monacoRoot.style.display = 'none';
      tableViewer.style.display = 'none';
      fallbackTextarea.style.display = 'none';
      imageViewer.style.display = 'flex';
      imageElement.src = `${API_BASE}/file?path=${encodeURIComponent(tab.path)}&t=${Date.now()}`;
      return;
    }

    if (tab.isTable && tab.viewMode === 'table') {
      monacoRoot.style.display = 'none';
      imageViewer.style.display = 'none';
      fallbackTextarea.style.display = 'none';
      tableViewer.style.display = 'flex';
      renderTableContent(tab);
      return;
    }

    // Standard Text/Code Monaco editor
    imageViewer.style.display = 'none';
    tableViewer.style.display = 'none';
    if (editorInstance && tab.model) {
      monacoRoot.style.display = 'block';
      fallbackTextarea.style.display = 'none';
      editorInstance.setModel(tab.model);
      editorInstance.focus();
    } else {
      monacoRoot.style.display = 'none';
      fallbackTextarea.style.display = 'block';
      fallbackTextarea.value = tab.content;
    }
  }

  function renderTableContent(tab) {
    tableInfo.textContent = `${tab.name} (Table View)`;
    const lines = (tab.model ? tab.model.getValue() : tab.content).split('\n').filter(l => l.trim().length > 0);
    if (lines.length === 0) {
      tableContent.innerHTML = '<div style="padding: 12px; color: var(--text-muted);">Empty table</div>';
      return;
    }
    const delimiter = tab.ext === 'tsv' ? '\t' : ',';
    const headers = parseCsvLine(lines[0], delimiter);
    let html = '<table class="data-table"><thead><tr>';
    headers.forEach(h => html += `<th>${escapeHtml(h)}</th>`);
    html += '</tr></thead><tbody>';
    lines.slice(1, 100).forEach(line => {
      const cells = parseCsvLine(line, delimiter);
      html += '<tr>';
      headers.forEach((_, i) => html += `<td>${escapeHtml(cells[i] || '')}</td>`);
      html += '</tr>';
    });
    html += '</tbody></table>';
    if (lines.length > 100) {
      html += `<div style="padding: 8px; color: var(--text-muted); text-align: center;">Showing first 100 rows of ${lines.length - 1}</div>`;
    }
    tableContent.innerHTML = html;
  }

  function parseCsvLine(text, delimiter) {
    return text.split(delimiter).map(c => c.replace(/^["']|["']$/g, '').trim());
  }

  function escapeHtml(str) {
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  tableToggleSource.addEventListener('click', () => {
    const tab = openTabs.find(t => t.path === activeTabPath);
    if (!tab) return;
    tab.viewMode = tab.viewMode === 'table' ? 'editor' : 'table';
    renderActiveFile();
  });

  function updateSaveBadge(state) {
    if (state === 'loading') {
      saveStatus.className = 'badge badge-saving';
      saveStatus.textContent = '⟳ Loading...';
    } else if (state === 'saving') {
      saveStatus.className = 'badge badge-saving';
      saveStatus.textContent = '⟳ Saving...';
    } else if (state === false) {
      saveStatus.className = 'badge badge-unsaved';
      saveStatus.textContent = '● Unsaved (Ctrl+S)';
    } else {
      saveStatus.className = 'badge badge-saved';
      saveStatus.textContent = '● Saved';
    }
  }

  async function saveCurrentFile() {
    if (!activeTabPath || isSaving) return;
    const tab = openTabs.find(t => t.path === activeTabPath);
    if (!tab || tab.isImg) return;

    isSaving = true;
    updateSaveBadge('saving');

    const currentContent = editorInstance && tab.model ? tab.model.getValue() : fallbackTextarea.value;
    try {
      const res = await fetch(`${API_BASE}/file?path=${encodeURIComponent(tab.path)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: currentContent })
      });
      if (!res.ok) throw new Error('Failed to save');
      tab.content = currentContent;
      tab.dirty = false;
      updateTabState(tab);
      updateSaveBadge(true);
    } catch (err) {
      alert(`Save error: ${err.message}`);
      updateSaveBadge(false);
    } finally {
      isSaving = false;
    }
  }

  saveBtn.addEventListener('click', () => saveCurrentFile());

  window.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 's') {
      e.preventDefault();
      saveCurrentFile();
    }
  });

  // Initial load
  loadTree();
})();
