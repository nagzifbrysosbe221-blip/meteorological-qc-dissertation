/* Display only preserved files in the generated catalog; never execute Python. */
(() => {
  'use strict';
  const catalog = window.ASSESSMENT_PYTHON;
  const escapeHTML = value => value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  // A small display lexer. Escaping happens before tokens enter the DOM.
  function highlight(source) {
    const tokens = /#[^\r\n]*|(?:[rubf]{0,2})(?:"""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\[\s\S]|[^"\\\r\n])*"|'(?:\\[\s\S]|[^'\\\r\n])*')|\b(?:False|None|True|and|as|assert|async|await|break|class|continue|def|del|elif|else|except|finally|for|from|global|if|import|in|is|lambda|nonlocal|not|or|pass|raise|return|try|while|with|yield)\b|\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b/g;
    let end = 0, html = '';
    for (const match of source.matchAll(tokens)) {
      html += escapeHTML(source.slice(end, match.index));
      const word = match[0];
      const kind = word.startsWith('#') ? 'comment' : /["']/.test(word) ? 'string' : /^\d/.test(word) ? 'number' : 'keyword';
      html += '<span class="py-' + kind + '">' + escapeHTML(word) + '</span>';
      end = match.index + word.length;
    }
    return html + escapeHTML(source.slice(end));
  }
  for (const section of document.querySelectorAll('[data-python-view]')) {
    const code = section.querySelector('.code-display code');
    const pre = code.parentElement;
    const status = section.querySelector('.code-status');
    const button = section.querySelector('[data-copy-code]');
    const download = section.querySelector('[data-download-code]');
    const description = section.querySelector('.code-file-description');
    const pathLabel = section.querySelector('.code-file-path');
    if (!catalog) {
      status.textContent = 'The file selector could not load. The complete default file and its download remain available.';
      continue;
    }
    const select = section.querySelector('select');
    const preferred = JSON.parse(section.dataset.preferred);
    const preferredSet = new Set(preferred);
    const groups = [
      ['Files for this page', preferred],
      ['Other monitoring files', Object.keys(catalog).filter(p => p.startsWith('source/qc-monitor/') && !preferredSet.has(p))],
      ['Other analysis files', Object.keys(catalog).filter(p => p.startsWith('source/result-analysis/') && !preferredSet.has(p))],
      ['Assessment checks', Object.keys(catalog).filter(p => p.startsWith('checks/') && !preferredSet.has(p))]
    ];
    for (const [label, paths] of groups) {
      if (!paths.length) continue;
      const group = document.createElement('optgroup');
      group.label = label;
      for (const path of paths) {
        if (!catalog[path]) continue;
        const option = document.createElement('option');
        option.value = path;
        option.textContent = path.split('/').pop() + ' — ' + catalog[path].label;
        group.append(option);
      }
      select.append(group);
    }
    let current = section.dataset.defaultFile;
    function show(path, saveChoice = false) {
      if (!catalog[path]) return;
      current = path;
      const file = catalog[path];
      code.innerHTML = highlight(file.text);
      select.value = path;
      description.textContent = file.description;
      pathLabel.textContent = path + ' · ' + file.lines + ' lines · Complete file';
      pre.setAttribute('aria-label', 'Complete Python code: ' + path);
      download.href = '../' + path;
      download.download = path.split('/').pop();
      status.textContent = 'Complete ' + path.split('/').pop() + ' displayed. Copy preserves the original indentation.';
      button.textContent = 'Copy code';
      if (saveChoice) {
        try {
          const url = new URL(location.href);
          url.searchParams.set('code', path);
          history.replaceState(null, '', url);
        } catch (_) { /* Local file reading still works without URL updates. */ }
      }
    }
    const requested = new URLSearchParams(location.search).get('code');
    show(requested && catalog[requested] ? requested : current);
    section.querySelector('.code-picker').hidden = false;
    section.querySelector('.code-wrap').hidden = false;
    button.disabled = false;
    select.addEventListener('change', () => show(select.value, true));
    section.querySelector('[data-wrap-code]').addEventListener('change', event => {
      pre.classList.toggle('no-wrap', !event.target.checked);
    });
    function fallbackCopy(text) {
      const area = document.createElement('textarea');
      area.value = text;
      area.setAttribute('aria-label', 'Code to copy');
      area.style.cssText = 'position:fixed;left:-10000px;top:0';
      document.body.append(area);
      area.select();
      let copied = false;
      try { copied = document.execCommand('copy'); } catch (_) { /* Manual copy below. */ }
      area.remove();
      button.focus({preventScroll:true});
      return copied;
    }
    button.addEventListener('click', async () => {
      const path = current;
      const text = catalog[path].text;
      let copied = false;
      button.disabled = true;
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(text);
          copied = true;
        }
      } catch (_) { /* Clipboard permission may be unavailable in local copies. */ }
      if (!copied) copied = fallbackCopy(text);
      button.disabled = false;
      if (current !== path) {
        status.textContent = copied ? 'Copied ' + path.split('/').pop() + '. The displayed file has since changed.' : 'The earlier file could not be copied. Try Copy code for the displayed file.';
        return;
      }
      if (copied) {
        button.textContent = 'Copied';
        status.textContent = 'Copied the complete ' + path.split('/').pop() + ' (' + catalog[path].lines + ' lines).';
      } else {
        const range = document.createRange();
        range.selectNodeContents(code);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        status.textContent = 'Automatic copying is unavailable. The complete code is selected: press Ctrl+C (Cmd+C on Mac), or use Download .py.';
      }
    });
  }
})();
