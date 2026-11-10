// Runs inside the page. Walks the slide and reports every painted box, every
// block of text (with the lines the browser actually broke it into), every
// chart placeholder and every table, in paint order and in slide pixels.
() => {
  const root = document.querySelector('.slide');
  const origin = root.getBoundingClientRect();
  const num = v => parseFloat(v) || 0;
  const items = [];
  const notes = [];   // things the converter cannot reproduce
  const clipped = []; // containers whose content does not fit

  const rectOf = r => ({x: r.left - origin.left, y: r.top - origin.top, w: r.width, h: r.height});
  const transparent = c => !c || c === 'transparent' || /rgba\(.*,\s*0\)$/.test(c);
  const clipsText = cs => cs.webkitBackgroundClip === 'text' || cs.backgroundClip === 'text';

  function opacityOf(el) {
    let o = 1;
    for (let e = el; e && e !== root.parentElement; e = e.parentElement) o *= num(getComputedStyle(e).opacity);
    return o;
  }
  function painted(cs) {
    if (!transparent(cs.backgroundColor)) return true;
    if (cs.backgroundImage && cs.backgroundImage !== 'none') return true;
    return ['Top', 'Right', 'Bottom', 'Left'].some(s => num(cs['border' + s + 'Width']) > 0 && cs['border' + s + 'Style'] !== 'none');
  }
  function inlineLike(node) {
    if (node.nodeType === 3) return true;
    if (node.nodeType !== 1) return false;
    if (node.tagName === 'BR') return true;
    const cs = getComputedStyle(node);
    // inline-blocks (pills, badges) are boxes of their own, not runs of the surrounding text
    return cs.display === 'inline';
  }
  function styleOf(el) {
    const cs = getComputedStyle(el);
    return {color: cs.color, size: num(cs.fontSize), weight: num(cs.fontWeight), italic: cs.fontStyle === 'italic',
            font: cs.fontFamily, spacing: num(cs.letterSpacing), transform: cs.textTransform,
            grad: clipsText(cs) ? cs.backgroundImage : null};
  }
  function runsOf(nodes) {
    const runs = [];
    (function walk(list) {
      for (const n of list) {
        if (n.nodeType === 3) {
          const st = styleOf(n.parentElement);
          let t = n.textContent;
          if (st.transform === 'uppercase') t = t.toUpperCase();
          runs.push({t, ...st});
        } else if (n.nodeType === 1) {
          if (n.tagName === 'BR') runs.push({br: true});
          else walk(n.childNodes);
        }
      }
    })(nodes);
    return runs;
  }
  function paintedAncestor(el) {
    for (let e = el; e && e !== root; e = e.parentElement) { const cs = getComputedStyle(e); if (painted(cs) && !clipsText(cs)) return e; }
    return root;
  }
  function emitText(nodes, owner) {
    const range = document.createRange();
    range.setStartBefore(nodes[0]);
    range.setEndAfter(nodes[nodes.length - 1]);
    const rects = [...range.getClientRects()].filter(q => q.width > 0.5 && q.height > 0.5);
    if (!rects.length) return;
    const tops = [];
    rects.forEach(q => { if (!tops.some(t => Math.abs(t - q.top) < q.height * 0.5)) tops.push(q.top); });
    const cs = getComputedStyle(owner);
    const ob = owner.getBoundingClientRect();
    const pl = num(cs.paddingLeft) + num(cs.borderLeftWidth), pr = num(cs.paddingRight) + num(cs.borderRightWidth);
    const tr = range.getBoundingClientRect();
    const align = cs.textAlign === 'start' ? 'left' : cs.textAlign === 'end' ? 'right' : cs.textAlign;
    // Range rects are glyph boxes; the line box is taller or shorter by the leading.
    const first = rects.reduce((m, q) => q.top < m.top ? q : m, rects[0]);
    const tallest = rects.filter(q => Math.abs(q.top - first.top) < q.height * 0.5).reduce((m, q) => Math.max(m, q.height), 0);
    items.push({kind: 'text', runs: runsOf(nodes), lines: tops.length, align, op: opacityOf(owner),
                cls: owner.className || '', linePx: cs.lineHeight === 'normal' ? null : num(cs.lineHeight), glyphPx: tallest,
                text: rectOf(tr), box: {x: ob.left - origin.left + pl, w: ob.width - pl - pr},
                parent: rectOf(paintedAncestor(owner).getBoundingClientRect())});
  }
  function emitBox(el, cs) {
    const r = el.getBoundingClientRect();
    if (r.width < 0.5 || r.height < 0.5) return;
    const sides = ['Top', 'Right', 'Bottom', 'Left'].map(s => ({w: num(cs['border' + s + 'Width']), c: cs['border' + s + 'Color'], st: cs['border' + s + 'Style']}));
    const short = Math.min(r.width, r.height);
    const radius = [cs.borderTopLeftRadius, cs.borderTopRightRadius, cs.borderBottomRightRadius, cs.borderBottomLeftRadius]
      .map(v => v.trim().split(' ')[0]).map(v => v.endsWith('%') ? parseFloat(v) / 100 * short : num(v));
    items.push({kind: 'box', ...rectOf(r), bg: cs.backgroundColor, img: cs.backgroundImage, sides, radius, shadow: cs.boxShadow, op: opacityOf(el)});
  }
  function emitTable(el) {
    const rows = [...el.querySelectorAll('tr')];
    if (!rows.length) return;
    const cells = tr => [...tr.children].map(c => c.textContent.replace(/\s+/g, ' ').trim());
    const head = rows[0];
    const body = rows.slice(1);
    const sample = (body[0] || head).children[0];
    items.push({kind: 'table', ...rectOf(el.getBoundingClientRect()), headers: cells(head), rows: body.map(cells),
                widths: [...head.children].map(c => c.getBoundingClientRect().width),
                size: num(getComputedStyle(sample).fontSize), variant: el.dataset.variant || ''});
  }
  function visit(el) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return;
    if (el !== root) {
      if (cs.transform && cs.transform !== 'none') notes.push('transform');
      if (cs.filter && cs.filter !== 'none') notes.push('filter');
      if (cs.backgroundImage && cs.backgroundImage.includes('url(')) notes.push('background image');
      if (el.tagName === 'IMG' || el.tagName === 'SVG' || el.tagName === 'svg' || el.tagName === 'CANVAS') { notes.push(el.tagName.toLowerCase()); return; }
      if (cs.overflow !== 'visible' && (el.scrollHeight > el.clientHeight + 2 || el.scrollWidth > el.clientWidth + 2)) {
        clipped.push({...rectOf(el.getBoundingClientRect()), text: el.textContent.trim().slice(0, 30)});
      }
      if (!clipsText(cs) && painted(cs)) emitBox(el, cs);
      if (el.classList.contains('chart') && el.dataset.chart) {
        items.push({kind: 'chart', ...rectOf(el.getBoundingClientRect()), spec: el.dataset.chart});
        return;
      }
      if (el.tagName === 'TABLE') { emitTable(el); return; }
    }
    let group = [];
    const flush = () => {
      if (group.some(n => (n.nodeType === 3 && n.textContent.trim()) || (n.nodeType === 1 && n.tagName !== 'BR' && n.textContent.trim()))) emitText(group, el);
      group = [];
    };
    for (const n of el.childNodes) {
      if (inlineLike(n)) group.push(n);
      else if (n.nodeType === 1) { flush(); visit(n); }
    }
    flush();
  }
  visit(root);
  return {background: getComputedStyle(root).backgroundColor, items, notes: [...new Set(notes)], clipped,
          scroll: {w: root.scrollWidth, h: root.scrollHeight}};
}
