(() => {
  if (window.__swmAdminRichTextLoaded) return;
  window.__swmAdminRichTextLoaded = true;
  let initialized = false;

  const iconButtons = [
    ['undo', 'ri-arrow-go-back-line', 'Undo'],
    ['redo', 'ri-arrow-go-forward-line', 'Redo'],
    ['separator'],
    ['bold', 'ri-bold', 'Bold'],
    ['italic', 'ri-italic', 'Italic'],
    ['underline', 'ri-underline', 'Underline'],
    ['strikeThrough', 'ri-strikethrough', 'Strikethrough'],
    ['removeFormat', 'ri-eraser-line', 'Clear formatting'],
    ['separator'],
    ['justifyleft', 'ri-align-left', 'Align left'],
    ['justifycenter', 'ri-align-center', 'Align center'],
    ['justifyright', 'ri-align-right', 'Align right'],
    ['justifyfull', 'ri-align-justify', 'Justify'],
    ['separator'],
    ['insertUnorderedList', 'ri-list-unordered', 'Bullet list'],
    ['insertOrderedList', 'ri-list-ordered', 'Numbered list'],
    ['outdent', 'ri-indent-decrease', 'Decrease indent'],
    ['indent', 'ri-indent-increase', 'Increase indent'],
    ['separator'],
    ['blockquote', 'ri-double-quotes-l', 'Quote'],
    ['codeBlock', 'ri-code-s-slash-line', 'Code block'],
    ['createLink', 'ri-link', 'Insert link'],
    ['unlink', 'ri-link-unlink', 'Remove link'],
    ['insertImage', 'ri-image-line', 'Image URL'],
    ['insertTable', 'ri-table-line', 'Table'],
    ['insertHorizontalRule', 'ri-separator', 'Horizontal line'],
  ];

  const formats = [
    ['P', 'Paragraph'],
    ['H1', 'Heading 1'],
    ['H2', 'Heading 2'],
    ['H3', 'Heading 3'],
    ['H4', 'Heading 4'],
    ['PRE', 'Preformatted'],
  ];

  const sizes = [
    ['2', 'Small'],
    ['3', 'Normal'],
    ['4', 'Medium'],
    ['5', 'Large'],
    ['6', 'Extra Large'],
  ];

  const syncFromTextarea = (textarea) => {
    const editor = textarea.__swmRichEditor;
    if (!editor) return;
    const value = textarea.value || '';
    if (editor.dataset.lastTextareaValue !== value) {
      editor.innerHTML = value;
      editor.dataset.lastTextareaValue = value;
    }
  };

  const syncToTextarea = (textarea) => {
    const editor = textarea.__swmRichEditor;
    if (!editor) return;
    if (textarea.__swmMode === 'html') {
      editor.innerHTML = textarea.value || '';
    } else {
      textarea.value = editor.innerHTML.trim();
    }
    editor.dataset.lastTextareaValue = textarea.value || '';
  };

  const exec = (textarea, action, value = null) => {
    const editor = textarea.__swmRichEditor;
    if (!editor) return;
    if (textarea.__swmMode === 'html') setMode(textarea, 'visual');

    editor.focus();

    if (action === 'createLink') {
      const selected = window.getSelection()?.toString();
      const url = window.prompt('Enter URL', selected?.startsWith('http') ? selected : 'https://');
      if (!url || url === 'https://') return;
      document.execCommand('createLink', false, url);
    } else if (action === 'insertImage') {
      const url = window.prompt('Enter image URL', 'https://');
      if (!url || url === 'https://') return;
      document.execCommand('insertImage', false, url);
    } else if (action === 'insertTable') {
      const rows = Math.min(Math.max(Number(window.prompt('Rows', '3')) || 3, 1), 12);
      const columns = Math.min(Math.max(Number(window.prompt('Columns', '3')) || 3, 1), 8);
      const tableRows = Array.from({ length: rows }, () => {
        const cells = Array.from({ length: columns }, () => '<td>&nbsp;</td>').join('');
        return `<tr>${cells}</tr>`;
      }).join('');
      document.execCommand('insertHTML', false, `<table><tbody>${tableRows}</tbody></table><p></p>`);
    } else if (action === 'blockquote') {
      document.execCommand('formatBlock', false, 'BLOCKQUOTE');
    } else if (action === 'codeBlock') {
      document.execCommand('formatBlock', false, 'PRE');
    } else {
      document.execCommand(action, false, value);
    }

    syncToTextarea(textarea);
  };

  function setMode(textarea, mode) {
    const editor = textarea.__swmRichEditor;
    const shell = textarea.__swmRichShell;
    if (!editor || !shell) return;

    textarea.__swmMode = mode;
    if (mode === 'html') {
      syncToTextarea(textarea);
      editor.classList.add('hidden');
      textarea.classList.remove('hidden');
    } else {
      editor.innerHTML = textarea.value || '';
      editor.classList.remove('hidden');
      textarea.classList.add('hidden');
    }

    shell.querySelectorAll('[data-editor-mode]').forEach((button) => {
      button.classList.toggle('active', button.dataset.editorMode === mode);
    });
  }

  const createSelect = (label, options, onChange) => {
    const select = document.createElement('select');
    select.className = 'admin-richtext-select';
    select.setAttribute('aria-label', label);
    options.forEach(([value, text]) => {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = text;
      select.appendChild(option);
    });
    select.addEventListener('change', () => onChange(select.value));
    return select;
  };

  const createColorPicker = (textarea, label, command, defaultColor) => {
    const wrapper = document.createElement('div');
    wrapper.className = 'admin-richtext-colors';
    wrapper.title = label;
    wrapper.setAttribute('aria-label', label);

    const input = document.createElement('input');
    input.type = 'color';
    input.className = 'admin-richtext-color';
    input.value = defaultColor;
    input.title = label;
    input.setAttribute('aria-label', label);
    input.addEventListener('input', () => exec(textarea, command, input.value));
    input.addEventListener('change', () => exec(textarea, command, input.value));

    const labelText = document.createElement('span');
    labelText.className = 'admin-richtext-color-label';
    labelText.textContent = label;

    wrapper.append(input, labelText);

    return wrapper;
  };

  const buildEditor = (textarea) => {
    if (textarea.dataset.richtext === 'off' || textarea.__swmRichEditor) return;

    const shell = document.createElement('div');
    shell.className = 'admin-richtext';

    const topbar = document.createElement('div');
    topbar.className = 'admin-richtext-topbar';

    const formatSelect = createSelect('Text format', formats, (value) => exec(textarea, 'formatBlock', value));
    const sizeSelect = createSelect('Text size', sizes, (value) => exec(textarea, 'fontSize', value));
    const modeGroup = document.createElement('div');
    modeGroup.className = 'admin-richtext-modes';
    modeGroup.innerHTML = `
      <button type="button" class="admin-richtext-mode active" data-editor-mode="visual">Visual</button>
      <button type="button" class="admin-richtext-mode" data-editor-mode="html">HTML</button>
    `;
    modeGroup.querySelectorAll('[data-editor-mode]').forEach((button) => {
      button.addEventListener('click', () => setMode(textarea, button.dataset.editorMode));
    });
    topbar.append(formatSelect, sizeSelect, modeGroup);

    const toolbar = document.createElement('div');
    toolbar.className = 'admin-richtext-toolbar';

    iconButtons.forEach(([action, icon, label]) => {
      if (action === 'separator') {
        const separator = document.createElement('span');
        separator.className = 'admin-richtext-separator';
        toolbar.appendChild(separator);
        return;
      }

      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'admin-richtext-btn';
      button.title = label;
      button.setAttribute('aria-label', label);
      button.innerHTML = `<i class="${icon}"></i>`;
      button.addEventListener('click', () => exec(textarea, action));
      toolbar.appendChild(button);
    });

    toolbar.append(
      createColorPicker(textarea, 'Text color', 'foreColor', '#111827'),
      createColorPicker(textarea, 'Highlight color', 'hiliteColor', '#fef08a')
    );

    const editor = document.createElement('div');
    editor.className = 'admin-richtext-editor modal-scroll';
    editor.contentEditable = 'true';
    editor.innerHTML = textarea.value || '';
    editor.dataset.lastTextareaValue = textarea.value || '';
    editor.dataset.placeholder = textarea.getAttribute('placeholder') || '';
    editor.setAttribute('role', 'textbox');
    editor.setAttribute('aria-multiline', 'true');

    const rows = Number(textarea.getAttribute('rows') || 3);
    editor.style.minHeight = `${Math.max(120, rows * 38)}px`;

    editor.addEventListener('input', () => syncToTextarea(textarea));
    editor.addEventListener('blur', () => syncToTextarea(textarea));
    editor.addEventListener('paste', () => window.setTimeout(() => syncToTextarea(textarea), 0));

    textarea.classList.add('admin-richtext-source', 'hidden');
    textarea.addEventListener('input', () => {
      if (textarea.__swmMode === 'html') {
        editor.innerHTML = textarea.value || '';
        editor.dataset.lastTextareaValue = textarea.value || '';
      }
    });

    shell.append(topbar, toolbar, editor);
    textarea.after(shell);
    shell.appendChild(textarea);
    textarea.__swmRichEditor = editor;
    textarea.__swmRichShell = shell;
    textarea.__swmMode = 'visual';
  };

  const syncAll = () => {
    document.querySelectorAll('textarea').forEach((textarea) => syncFromTextarea(textarea));
  };

  const init = () => {
    document.querySelectorAll('textarea').forEach(buildEditor);
    if (initialized) return;
    initialized = true;
    document.addEventListener('click', () => window.setTimeout(syncAll, 0), true);
    document.addEventListener('input', (event) => {
      if (event.target?.matches?.('textarea')) syncFromTextarea(event.target);
    }, true);
    document.querySelectorAll('form').forEach((form) => {
      form.addEventListener('submit', () => {
        form.querySelectorAll('textarea').forEach(syncToTextarea);
      });
    });
    window.SWMAdminRichText = { syncAll, syncFromTextarea, syncToTextarea, setMode };
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
  document.addEventListener('turbo:load', init);
})();
