"""Browser-side perception library, injected into every frame.

It produces the *operator's view* of a page: controls with a role and the name a human
would read (including legacy table-layout labels: the text in the preceding cell), and
text with its table context (row label, column header). This is the same shape a
desktop accessibility tree (UIA/AX) or an OCR pass would produce, which is what keeps
the artifact schema surface-agnostic.

It also installs an event recorder used during human handoff: every click/change is
described with the same vocabulary and forwarded to the automation process.
"""

LIB = r"""
(() => {
  if (window.__cuauto) return;
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const clean = s => norm(s).replace(/[:*]\s*$/, '').trim();
  const visible = el => {
    if (!el.getClientRects || !el.getClientRects().length) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none';
  };
  const xpathOf = el => {
    const parts = [];
    while (el && el.nodeType === 1) {
      let i = 1, sib = el.previousElementSibling;
      while (sib) { if (sib.tagName === el.tagName) i++; sib = sib.previousElementSibling; }
      parts.unshift(el.tagName.toLowerCase() + '[' + i + ']');
      el = el.parentElement;
    }
    return '/' + parts.join('/');
  };
  const roleOf = el => {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit;
    const t = el.tagName.toLowerCase(), ty = (el.getAttribute('type') || 'text').toLowerCase();
    if (t === 'a' && el.hasAttribute('href')) return 'link';
    if (t === 'button') return 'button';
    if (t === 'input' && ['submit', 'button', 'reset', 'image'].includes(ty)) return 'button';
    if (t === 'select') return 'combobox';
    if (t === 'textarea') return 'textbox';
    if (t === 'input') {
      if (ty === 'hidden') return null;
      if (ty === 'checkbox') return 'checkbox';
      if (ty === 'radio') return 'radio';
      return 'textbox';
    }
    return null;
  };
  const cellOf = el => el.closest('td,th');
  const rowLabelOf = cell => {
    const tr = cell && cell.parentElement;
    if (!tr || tr.tagName !== 'TR') return '';
    for (const c of tr.children) { if (c === cell) return ''; const t = clean(c.innerText); if (t) return t; }
    return '';
  };
  const columnHeaderOf = cell => {
    const table = cell && cell.closest('table');
    if (!table) return '';
    const headerRow = Array.from(table.rows).find(r => r.querySelector('th'));
    if (!headerRow || headerRow === cell.parentElement) return '';
    const h = headerRow.cells[cell.cellIndex];
    return h ? clean(h.innerText) : '';
  };
  const adjacentLabel = el => {
    const cell = cellOf(el);
    if (!cell) return '';
    let prev = cell.previousElementSibling;
    while (prev) { const t = clean(prev.innerText); if (t) return t; prev = prev.previousElementSibling; }
    return '';
  };
  const nameOf = (el, role) => {
    const aria = el.getAttribute('aria-label');
    if (aria) return clean(aria);
    const lb = el.getAttribute('aria-labelledby');
    if (lb) { const t = lb.split(/\s+/).map(id => { const n = document.getElementById(id); return n ? n.innerText : ''; }).join(' '); if (norm(t)) return clean(t); }
    if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) return clean(l.innerText); }
    if (role === 'button') return norm(el.innerText || el.value || el.getAttribute('alt') || el.title);
    if (role === 'link') return norm(el.innerText || el.title);
    const wrap = el.closest('label');
    if (wrap) return clean(wrap.innerText);
    return adjacentLabel(el) || clean(el.getAttribute('placeholder') || el.title || '');
  };
  const ctxOf = el => {
    const cell = cellOf(el);
    return { row_label: cell ? rowLabelOf(cell) : '', column_header: cell ? columnHeaderOf(cell) : '',
             field_label: adjacentLabel(el) };
  };
  const describe = el => {
    const role = roleOf(el);
    const d = { kind: 'control', role, name: nameOf(el, role), ctx: ctxOf(el), xpath: xpathOf(el),
                tag: el.tagName.toLowerCase(), sensitive: (el.getAttribute('type') || '') === 'password',
                disabled: !!el.disabled };
    if (role === 'textbox') d.value = d.sensitive ? '' : (el.value || '');
    if (role === 'combobox') { d.options = Array.from(el.options).map(o => norm(o.text)); d.value = el.selectedIndex >= 0 ? norm(el.options[el.selectedIndex].text) : ''; }
    return d;
  };
  const SKIP = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'OPTION', 'SELECT', 'TEXTAREA', 'HEAD', 'TITLE']);
  const ownText = el => norm(Array.from(el.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent).join(' '));
  const inventory = (maxItems) => {
    const out = [];
    const all = document.body ? document.body.querySelectorAll('*') : [];
    for (const el of all) {
      if (out.length >= (maxItems || 400)) break;
      const role = roleOf(el);
      if (role) { if (visible(el)) out.push(describe(el)); continue; }
      if (SKIP.has(el.tagName)) continue;  // text-bearing internals of controls, scripts ...
      if (el.closest('a,button')) continue;
      const t = ownText(el);
      if (!t || !visible(el)) continue;
      const cell = cellOf(el);
      out.push({ kind: 'text', role: 'text', text: t, xpath: xpathOf(el),
                 ctx: { row_label: cell ? rowLabelOf(cell) : '', column_header: cell ? columnHeaderOf(cell) : '',
                        field_label: '' },
                 emphasis: !!el.closest('b,strong,h1,h2,h3,th') });
    }
    return { url: location.href, title: document.title, items: out };
  };
  const readText = xp => {
    const el = document.evaluate(xp, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
    return el ? norm(el.innerText || el.value || '') : null;
  };
  const setBlur = (patterns, labels, on) => {
    const rxs = patterns.map(p => new RegExp(p, 'i'));
    const lab = new Set(labels.map(l => l.toLowerCase()));
    for (const el of document.body ? document.body.querySelectorAll('td,span,b,font,p,div,input') : []) {
      if (!on) { if (el.dataset.cuBlur) { el.style.filter = ''; delete el.dataset.cuBlur; } continue; }
      const t = el.tagName === 'INPUT' ? (el.value || '') : ownText(el);
      const cell = cellOf(el);
      const ctxHit = cell && (lab.has(rowLabelOf(cell).toLowerCase()) || lab.has(columnHeaderOf(cell).toLowerCase()));
      if ((t && rxs.some(r => r.test(t))) || (ctxHit && t)) { el.style.filter = 'blur(6px)'; el.dataset.cuBlur = '1'; }
    }
  };
  const send = d => { try { if (window.__cuautoRecord) window.__cuautoRecord(Object.assign(d, { url: location.href })); } catch (_) {} };
  document.addEventListener('click', e => {
    const el = e.target && e.target.closest && e.target.closest('a[href],button,input,select,textarea,[role=button]');
    if (el && !['text', 'password'].includes((el.getAttribute('type') || '').toLowerCase())) send(Object.assign({ action: 'click' }, describe(el)));
  }, true);
  document.addEventListener('change', e => {
    const el = e.target;
    if (!el || !el.matches || !el.matches('input,select,textarea')) return;
    const d = describe(el);
    d.action = el.tagName === 'SELECT' ? 'select' : 'fill';
    d.value = d.sensitive ? '[SECRET]' : (el.tagName === 'SELECT' ? d.value : el.value);
    send(d);
  }, true);
  window.__cuauto = { inventory, readText, setBlur, describe };
})();
"""
