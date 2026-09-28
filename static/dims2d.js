/* CladForge — dimensions you can edit where they sit, in the 2D elevation view.
 *
 * Flat, the active elevation's dimensions are HTML labels over the view, placed from
 * projected points every frame. An editable one turns into an input when clicked:
 * Enter commits, Escape cancels, Tab moves to the next. Each writes to the parameter it
 * really measures, and the preview follows through the same 300 ms debounce as a typed
 * field. The ones driven by something else are greyed, and say what drives them.
 * The 3D view keeps its sprite labels and the course dialog.
 */

const D2 = { items: [], editing: null, scope: 'chain' };

const DIM_NAMES = { row: 'Panel row', course: 'Plank course', cut_left: 'Left closing cut', panel_w: 'Panel width',
                    centres: 'Batten centres', splash: 'Splash zone', level_top: 'Top of cladding',
                    level_base: 'Baserail level' };

function d2Elev(name) { return state.elevations.find(m => m.result && m.result.name === name); }

function d2Key(d) { return d.kind === 'row' ? 'row:' + d.row : (d.kind || '') + ':' + d.label; }

function d2Editable(d) { return !!(d.kind && DIM_NAMES[d.kind] && !d.lock); }

// Rebuild the labels after a preview or on entering 2D; clear them in 3D.
function renderDims2D() {
    const layer = document.getElementById('dim2d-layer');
    layer.innerHTML = '';
    D2.items = [];
    D2.edges = [];
    if (!in2D() || !window._lastPreview) { closeDimEditor(); return; }
    const e = d2Elev(view2d.name);
    if (!e) return;
    const M = frameMatrix(e.result.frame, 60);
    for (const d of window._lastPreview.dimensions.filter(x => x.elevation === view2d.name)) {
        const [nx, ny] = d.norm;
        const at = new THREE.Vector3((d.p1[0] + d.p2[0]) / 2 + nx * d.offset, (d.p1[1] + d.p2[1]) / 2 + ny * d.offset, 0).applyMatrix4(M);
        const el = document.createElement('button');
        const editable = d2Editable(d);
        el.className = 'dim2d' + (editable ? ' editable' : d.lock ? ' locked' : '');
        el.textContent = d.label;
        el.dataset.key = d2Key(d);
        if (d.lock) el.title = d.lock;
        else if (editable) el.title = `${DIM_NAMES[d.kind]} — click to type a value`;
        const item = { d, el, at };
        if (editable) el.onclick = ev => { ev.stopPropagation(); openDimEditor(item); };
        else el.onclick = ev => ev.stopPropagation();
        layer.appendChild(el);
        D2.items.push(item);
    }
    edgeLines(e, layer);
    jointLines(e, layer);
    cornerBadges(e, layer);
    if (D2.editing) {            // the preview was rebuilt under an open editor: re-anchor it
        const again = D2.items.concat(D2.edges).find(i => i.el.dataset.key === D2.editing.key);
        if (again) D2.editing.item = again; else closeDimEditor();
    }
    position2D();
}

// Corners at the ends of the face: a badge for the detail in force (M, L, S), with the
// master for a lap. Clicking one offers the corner row's selector and Swap.
function cornerBadges(e, layer) {
    const box = cladBox(e), M = frameMatrix(e.result.frame, 60);
    // (the opening badges below use the same frame)
    chainCorners(e.chain).forEach((c, i) => {
        if (c.lo !== e && c.hi !== e) return;
        const runHi = c.lo === e;                         // the corner is at e's end of the run
        const u = (runHi !== !!e.rev) ? box.u1 : box.u0;
        const detail = cornerDetailInForce(c);
        const master = (c.lo.masterHi ? c.lo : c.hi).name.replace('Elevation ', '');
        const el = document.createElement('button');
        el.className = 'dim2d corner-badge';
        el.dataset.key = 'corner:' + i;
        el.textContent = CORNER_LETTER[detail] + (detail === 'lap' ? ' · ' + master : '');
        el.title = `${c.lo.name.replace('Elevation ', '')}–${c.hi.name.replace('Elevation ', '')} corner: ${CORNER_LABEL[detail]}`
            + (detail === 'lap' ? `, ${master} masters` : '');
        const item = { corner: { chain: e.chain.name, index: i }, el,
                       at: new THREE.Vector3(u, box.v1 + 350, 0).applyMatrix4(M) };
        el.onclick = ev => { ev.stopPropagation(); openCornerEditor(item); };
        layer.appendChild(el);
        D2.items.push(item);
    });
    // Each window and door: a badge on its left jamb (both jambs share it) and one on its head.
    for (const o of openingsOf(e)) {
        const [u0, u1, v0, v1] = o.rect;
        for (const [part, u, v] of [['jamb', u0, (v0 + v1) / 2], ['head', (u0 + u1) / 2, v1]]) {
            const el = document.createElement('button');
            el.className = 'dim2d corner-badge opening-badge';
            el.dataset.key = 'opening:' + o.key + ':' + part;
            el.textContent = CORNER_LETTER[o[part]] + (o[part] === 'lap' ? (o[part + '_master'] === 'face' ? ' · face' : ' · lining') : '');
            el.title = `${part === 'jamb' ? 'Jambs' : 'Head'}: ${CORNER_LABEL[o[part]]} — click to change`;
            const item = { edge: { kind: part, p1: [u, v], p2: [u, v] }, el,
                           at: new THREE.Vector3(u, v, 0).applyMatrix4(M) };
            el.onclick = ev => { ev.stopPropagation(); openEdgeEditor(item); };
            layer.appendChild(el);
            D2.items.push(item);
        }
    }
}

// Every frame while flat: put each label where its point projects.
function position2D() {
    const cam = activeCamera(), w = renderer.domElement.clientWidth, h = renderer.domElement.clientHeight;
    for (const item of D2.items) {
        const p = item.at.clone().project(cam);
        const off = Math.abs(p.x) > 1.05 || Math.abs(p.y) > 1.05;
        item.el.style.display = off ? 'none' : '';
        item.el.style.left = ((p.x + 1) / 2 * w) + 'px';
        item.el.style.top = ((1 - p.y) / 2 * h) + 'px';
    }
    for (const item of D2.edges) {
        const a = item.a.clone().project(cam), b = item.b.clone().project(cam);
        const x1 = (a.x + 1) / 2 * w, y1 = (1 - a.y) / 2 * h, x2 = (b.x + 1) / 2 * w, y2 = (1 - b.y) / 2 * h;
        item.el.setAttribute('x1', x1); item.el.setAttribute('y1', y1);
        item.el.setAttribute('x2', x2); item.el.setAttribute('y2', y2);
        item.mid = { left: (x1 + x2) / 2 + 'px', top: (y1 + y2) / 2 + 'px' };
    }
    const ed = document.getElementById('dim2d-editor');
    if (D2.editing && D2.editing.item) {
        const at = D2.editing.item.mid || D2.editing.item.el.style;
        ed.style.left = at.left;
        ed.style.top = at.top;
    }
}

// ─── EDGES ───
// The cladding's edges, coloured by what happens at each (cladding_edges.py), over the
// outline. Clicking one types its value; every value belongs to the chain.
const EDGE_COLOUR = { corner: '#ff9800', jamb: '#ff9800', top: '#a855f7', side: '#22c55e',
                      bottom: '#3b82f6', head: '#ef4444' };
const EDGE_INFO = { corner: ['Chain corner', 'gap', 'Mitre gap'], jamb: ['Window or door jamb', 'gap', 'Mitre gap'],
                    top: ['Top edge', 'top', 'Offset'], side: ['Free end', 'side', 'Offset'],
                    bottom: ['Bottom edge', 'bottom', 'Offset'], head: ['Window or door head', 'air', 'Air space'] };
D2.edges = [];

function edgeLines(e, layer) {
    D2.edges = [];
    const info = (window._lastPreview.info || []).find(i => i.elevation === e.result.name);
    if (!info || !info.edges) return;
    const M = frameMatrix(e.result.frame, 60), ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('class', 'edge2d-svg');
    info.edges.forEach((edge, i) => {
        const line = document.createElementNS(ns, 'line');
        line.setAttribute('stroke', EDGE_COLOUR[edge.kind] || '#ffffff');
        line.dataset.kind = edge.kind;
        line.dataset.key = 'edge:' + i;
        const title = document.createElementNS(ns, 'title');
        title.textContent = EDGE_INFO[edge.kind][0] + ' — click to set its ' + EDGE_INFO[edge.kind][2].toLowerCase();
        line.appendChild(title);
        const item = { edge, el: line, a: new THREE.Vector3(edge.p1[0], edge.p1[1], 0).applyMatrix4(M),
                       b: new THREE.Vector3(edge.p2[0], edge.p2[1], 0).applyMatrix4(M) };
        line.addEventListener('click', ev => { ev.stopPropagation(); openEdgeEditor(item); });
        svg.appendChild(line);
        D2.edges.push(item);
    });
    layer.appendChild(svg);
}

// ─── PANEL JOINTS ───
// Each horizontal joint between two panel rows, one segment per bay. Clicking one
// dissolves it: the panels above and below in that bay become one. Clicking again puts
// the joint back. A merge the stock board cannot take, either way round, is refused.
function jointLines(e, layer) {
    const info = (window._lastPreview.info || []).find(i => i.elevation === e.result.name);
    if (!info || !info.hjoints || !info.hjoints.length) return;
    const M = frameMatrix(e.result.frame, 60), ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('class', 'edge2d-svg');
    for (const hj of info.hjoints) {
        hj.spans.forEach(([u0, u1], n) => {
            const line = document.createElementNS(ns, 'line');
            line.setAttribute('stroke', hj.dissolved ? '#ffd166' : '#cfd8e3');
            line.setAttribute('stroke-opacity', hj.dissolved ? '0.8' : '0.45');
            if (hj.dissolved) line.setAttribute('stroke-dasharray', '6 6');
            line.dataset.kind = 'joint';
            line.dataset.key = `joint:${hj.row}:${hj.bay}:${n}`;
            const title = document.createElementNS(ns, 'title');
            title.textContent = hj.dissolved
                ? `Dissolved joint, R${hj.row + 1}/R${hj.row + 2} bay ${hj.bay + 1} — click to put the joint back`
                : `Joint R${hj.row + 1}/R${hj.row + 2} bay ${hj.bay + 1} — click to dissolve it (${Math.round(hj.size[0])} × ${Math.round(hj.size[1])} panel)`;
            line.appendChild(title);
            line.addEventListener('click', ev => { ev.stopPropagation(); toggleJoint(e, hj); });
            svg.appendChild(line);
            D2.edges.push({ joint: hj, el: line, a: new THREE.Vector3(u0, hj.v, 0).applyMatrix4(M),
                            b: new THREE.Vector3(u1, hj.v, 0).applyMatrix4(M) });
        });
    }
    layer.appendChild(svg);
}

function toggleJoint(e, hj) {
    const list = (e.panelJoints || []).filter(q => !(q[0] === hj.row && q[1] === hj.bay));
    const where = `R${hj.row + 1}/R${hj.row + 2} bay ${hj.bay + 1} on ${e.name}`;
    if (hj.dissolved) {
        setStatus(`Joint ${where} put back`, 'ready');
    } else {
        const bw = parseFloat(document.getElementById('board_w').value) || 1250;
        const bh = parseFloat(document.getElementById('board_h').value) || 2500;
        const [w, h] = hj.size;
        if (!((w <= bw + 0.5 && h <= bh + 0.5) || (w <= bh + 0.5 && h <= bw + 0.5))) {
            setStatus(`Not dissolved: the panel would be ${Math.round(w)} × ${Math.round(h)} mm, larger than the ${Math.round(bw)} × ${Math.round(bh)} board either way round`, 'busy');
            return false;
        }
        list.push([hj.row, hj.bay, hj.u0, hj.u1, hj.v]);
        setStatus(`Joint ${where} dissolved: one ${Math.round(w)} × ${Math.round(h)} panel`, 'ready');
    }
    e.panelJoints = list.length ? list : null;
    renderElevationList();
    onNumeric();
    return true;
}

// The chain corner at the end of *e* that an orange corner edge sits on.
function cornerAt(e, u) {
    const box = cladBox(e);
    return chainCorners(e.chain).findIndex(c => (c.lo === e || c.hi === e)
        && Math.abs(((c.lo === e) !== !!e.rev ? box.u1 : box.u0) - u) < 2);
}

// The window or door whose jamb or head an edge (or badge) lies on.
function openingAt(e, part, p) {
    return openingsOf(e).find(o => {
        const [u0, u1, v0, v1] = o.rect;
        return part === 'jamb' ? (Math.abs(p[0] - u0) < 2 || Math.abs(p[0] - u1) < 2) && p[1] >= v0 - 2 && p[1] <= v1 + 2
                               : Math.abs(p[1] - v1) < 2 && p[0] >= u0 - 2 && p[0] <= u1 + 2;
    });
}

// The detail part of the box: a chain corner's detail and master, or an opening's jambs
// or head, whose change asks whether it goes to every window and door in the chain.
function detailBlock(e, item, box) {
    const kind = item.edge.kind;
    if (kind === 'corner') {
        const i = cornerAt(e, item.edge.p1[0]), c = chainCorners(e.chain)[i];
        if (!c) return '';
        selectCorner(e.chain.name, i);
        const detail = cornerDetailInForce(c);
        item.bind = () => {
            document.getElementById('o2-detail').onchange = ev => { setCornerDetail(e.chain.name, i, ev.target.value); closeDimEditor(); };
            const sw = document.getElementById('o2-swap');
            if (sw) sw.onclick = () => { swapCorner(e.chain.name, i); closeDimEditor(); };
        };
        return `<div class="d2-row">Detail <select id="o2-detail">${cornerOptions(c.lo.detailHi || '', profileOffered(c.k), true)}</select>
            ${detail === 'lap' ? '<button class="mini" id="o2-swap">Swap master</button>' : ''}</div>`;
    }
    if (kind !== 'jamb' && kind !== 'head') return '';
    const o = openingAt(e, kind, item.edge.p1);
    if (!o) return '';
    const pending = { detail: o[kind], master: o[kind + '_master'] };
    const ask = () => {
        document.getElementById('o2-ask').style.display = '';
        const arr = document.getElementById('o2-master');
        if (arr) arr.style.display = pending.detail === 'lap' ? '' : 'none';
    };
    item.bind = () => {
        document.getElementById('o2-detail').onchange = ev => { pending.detail = ev.target.value; ask(); };
        box.querySelectorAll('[data-master]').forEach(b => b.onclick = () => {
            pending.master = b.dataset.master;
            box.querySelectorAll('[data-master]').forEach(x => x.classList.toggle('active', x === b));
            ask();
        });
        document.getElementById('o2-yes').onclick = () => { setOpeningDetail(e, o.key, kind, pending.detail, pending.master, 'chain'); closeDimEditor(); };
        document.getElementById('o2-no').onclick = () => { setOpeningDetail(e, o.key, kind, pending.detail, pending.master, 'one'); closeDimEditor(); };
    };
    return `<div class="d2-row">${kind === 'jamb' ? 'Both jambs' : 'Head'} <select id="o2-detail">${cornerOptions(o[kind], toggleValue('cladding-type') === 'panel', false)}</select></div>
        <div class="d2-scope turn-toggle" id="o2-master" style="${o[kind] === 'lap' ? '' : 'display:none'}">
            <button class="turn-btn ${o[kind + '_master'] === 'face' ? 'active' : ''}" data-master="face">Face board over</button>
            <button class="turn-btn ${o[kind + '_master'] === 'reveal' ? 'active' : ''}" data-master="reveal">Lining over</button></div>
        <div class="d2-ask" id="o2-ask" style="display:none">Apply this to every window and door in this chain?${kind === 'head' ? ' (heads only)' : ''}
            <button class="mini" id="o2-yes">Yes</button> <button class="mini" id="o2-no">No, this one only</button></div>`;
}

function openEdgeEditor(item) {
    const e = d2Elev(view2d.name);
    if (!e) return;
    const kind = item.edge.kind, [title, key, what] = EDGE_INFO[kind], ed = e.chain.edges;
    D2.editing = { key: item.el.dataset.key, item };
    const box = document.getElementById('dim2d-editor');
    const vent = kind === 'head' ? `<div class="d2-scope turn-toggle" id="e2-vent">
            <button class="turn-btn ${ed.vent === 'front' ? 'active' : ''}" data-vent="front">Vent at front</button>
            <button class="turn-btn ${ed.vent === 'back' ? 'active' : ''}" data-vent="back">Vent at back</button></div>` : '';
    item.bind = null;
    const detail = detailBlock(e, item, box);
    box.innerHTML = `<div class="d2-title" style="color:${EDGE_COLOUR[kind]}">${title} · ${e.chain.name}</div>${detail}${vent}
        <span>${what}</span> <input id="e2-input" type="number" step="1" min="0" max="100" value="${Math.round(ed[key])}"> <span class="unit">mm</span>
        <div class="d2-hint">${what} applies to every elevation in ${e.chain.name} · Enter to apply · Esc to cancel</div>`;
    box.style.display = '';
    if (item.bind) item.bind();
    const input = document.getElementById('e2-input');
    input.disabled = kind === 'head' && ed.vent !== 'back';
    box.querySelectorAll('[data-vent]').forEach(b => b.onclick = () => {
        setEdge(e, 'vent', b.dataset.vent);
        onNumeric();
        openEdgeEditor(item);
    });
    input.onkeydown = ev => {
        ev.stopPropagation();                  // Escape here closes the box, not the 2D view
        if (ev.key === 'Enter') {
            ev.preventDefault();
            const done = setEdge(e, key, input.value);
            closeDimEditor();
            if (done) { setStatus(`${what} ${Math.round(e.chain.edges[key])} mm on ${e.chain.name}`, 'ready'); onNumeric(); }
        } else if (ev.key === 'Escape') { ev.preventDefault(); closeDimEditor(); }
    };
    position2D();
    if (!input.disabled) { input.focus(); input.select(); }
}

// ─── THE EDITOR ───
function openDimEditor(item) {
    const d = item.d, e = d2Elev(d.elevation);
    if (!e) return;
    D2.editing = { key: d2Key(d), item };
    const multi = e.chain.members.filter(m => m.result && m.result.ok).length > 1;
    const scoped = (d.kind === 'row' || d.kind === 'course') && multi;
    const ed = document.getElementById('dim2d-editor');
    ed.innerHTML = `<div class="d2-title">${DIM_NAMES[d.kind]}${d.kind === 'row' ? ' ' + (d.row + 1) : ''}</div>
        <input id="d2-input" type="number" step="1" value="${Math.round(d.value)}"${d.fixed ? ' disabled' : ''}> <span class="unit">mm</span>
        ${d.fixed ? `<div class="d2-hint">${d.fixed}</div>` : ''}
        ${scoped ? `<div class="d2-scope turn-toggle">
            <button class="turn-btn ${D2.scope === 'chain' ? 'active' : ''}" data-scope="chain">${e.chain.name}</button>
            <button class="turn-btn ${D2.scope === 'one' ? 'active' : ''}" data-scope="one">This elevation</button></div>` : ''}
        ${d.kind === 'row' ? `<div class="d2-actions"><button class="mini" id="d2-split">Split row</button>
            <button class="mini" id="d2-merge"${d.fixed ? ' disabled title="The top row has no row above it"' : ''}>Merge with row above</button></div>` : ''}
        <div class="d2-hint">${d.fixed ? 'Esc to close' : 'Enter to apply · Esc to cancel'} · Tab for the next</div>`;
    ed.style.display = '';
    ed.querySelectorAll('[data-scope]').forEach(b => b.onclick = () => {
        D2.scope = b.dataset.scope;
        ed.querySelectorAll('[data-scope]').forEach(x => x.classList.toggle('active', x === b));
        document.getElementById('d2-input').focus();
    });
    if (d.kind === 'row') {
        document.getElementById('d2-split').onclick = () => { splitRow(e, d.row); closeDimEditor(); };
        document.getElementById('d2-merge').onclick = () => { mergeRow(e, d.row); closeDimEditor(); };
    }
    const input = document.getElementById('d2-input');
    // A disabled input takes no keys: the box itself does, for Escape and Tab.
    (d.fixed ? ed : input).onkeydown = ev => {
        ev.stopPropagation();                  // Escape here cancels the edit, not the 2D view
        if (ev.key === 'Enter') { ev.preventDefault(); commitDimEditor(); }
        else if (ev.key === 'Escape') { ev.preventDefault(); closeDimEditor(); }
        else if (ev.key === 'Tab') {
            ev.preventDefault();
            const editable = D2.items.filter(i => i.d && d2Editable(i.d));
            const k = editable.indexOf(D2.editing.item);
            commitDimEditor();
            const next = editable[(k + (ev.shiftKey ? editable.length - 1 : 1)) % editable.length];
            if (next) openDimEditor(next);
        }
    };
    position2D();
    if (d.fixed) { ed.tabIndex = -1; ed.focus(); return; }
    input.focus();
    input.select();
}

function closeDimEditor() {
    D2.editing = null;
    const ed = document.getElementById('dim2d-editor');
    if (ed) { ed.style.display = 'none'; ed.innerHTML = ''; }
}

function commitDimEditor() {
    const input = document.getElementById('d2-input');
    const item = D2.editing && D2.editing.item;
    if (!input || !item || item.d.fixed) return closeDimEditor();
    const v = parseFloat(input.value);
    closeDimEditor();
    if (!isFinite(v) || v <= 0 || Math.abs(v - item.d.value) < 0.5) return;
    applyDim(item.d, v, D2.scope);
}

function setField(id, v) { const el = document.getElementById(id); if (el) el.value = Math.round(v); }

// Write a typed value to the parameter the dimension measures, then preview through the
// same debounce a typed field uses.
function applyDim(d, v, scope) {
    const e = d2Elev(d.elevation);
    if (!e) return;
    const members = scope === 'chain' ? e.chain.members.filter(m => m.result && m.result.ok) : [e];
    const z0 = e.result.frame.origin[2];
    switch (d.kind) {      // a row or course height is a vertical input: it locks the datum
        case 'row': {
            const r = lockForRow(e, d.row), rows = withRow(e, r.j, v, r.drawn);
            members.forEach(m => { m.panelRows = rows.slice(); });
            break;
        }
        case 'course': lockDatum(e); members.forEach(m => { m.cover = v; }); break;
        case 'cut_left': {
            // The left cut moves one for one with the offset (against it on a reversed
            // face), so the offset that gives the typed cut is found directly, then
            // wrapped into one bay, where the slider lives.
            const bay = d.bay, sign = e.rev ? -1 : 1;
            const off = (e.offset || 0) + sign * (v - d.value);
            e.offset = Math.round(((((off + bay / 2) % bay) + bay) % bay - bay / 2) * 10) / 10;
            document.getElementById('offset').value = e.offset;
            break;
        }
        case 'panel_w': setField('panel_w', v); break;
        case 'centres': setField('batten_centres', v); break;
        case 'splash': setField('splash', v); break;
        case 'level_top': e.chain.topZ = z0 + v; break;
        case 'level_base': e.chain.bottomZ = z0 + v; break;
        default: return;
    }
    setStatus(`${DIM_NAMES[d.kind]} ${Math.round(v)} mm on ${scope === 'chain' && members.length > 1 ? e.chain.name : e.name}`, 'ready');
    renderElevationList();
    onNumeric();
}

// Split a row into two halves with the joint gap between them. The top row is whatever is
// left, so splitting it types a new row below and leaves the remainder on top: the list
// ends at the new row.
function splitRow(e, j) {
    const gap = parseFloat(document.getElementById('panel_gap').value) || 0;
    const drawn = rowsDrawn(e), h = drawn[j];
    if (!h) return;
    const r = lockForRow(e, j), rows = withRow(e, r.j, h, r.drawn), half = Math.max(1, (h - gap) / 2);
    if (j === drawn.length - 1) rows.splice(r.j, rows.length, half);
    else rows.splice(r.j, 1, half, half);
    d2SetRows(e, rows);
}

// Merge a row with the one above: one row spanning both and the joint between.
function mergeRow(e, j) {
    const gap = parseFloat(document.getElementById('panel_gap').value) || 0;
    const drawn = rowsDrawn(e);
    if (j + 1 >= drawn.length) { setStatus('That is the top row: there is no row above to merge with', 'busy'); return; }
    const r = lockForRow(e, j), rows = withRow(e, r.j + 1, drawn[j + 1], r.drawn);
    rows.splice(r.j, 2, drawn[j] + gap + drawn[j + 1]);
    d2SetRows(e, rows);
}

function d2SetRows(e, rows) {      // Split row and Merge row come through here, locked already
    const multi = e.chain.members.filter(m => m.result && m.result.ok).length > 1;
    const members = D2.scope === 'chain' && multi ? e.chain.members.filter(m => m.result && m.result.ok) : [e];
    members.forEach(m => { m.panelRows = rows.slice(); });
    setStatus(`Rows ${rows.map(Math.round).join(' / ')} on ${members.length > 1 ? e.chain.name : e.name}`, 'ready');
    renderElevationList();
    onNumeric();
}

// A corner badge: the corner row's selector and Swap, and the corner lit in the model.
function openCornerEditor(item) {
    const { chain: name, index } = item.corner;
    const chain = state.chains.find(c => c.name === name);
    const c = chain && chainCorners(chain)[index];
    if (!c) return;
    selectCorner(name, index);
    D2.editing = { key: item.el.dataset.key, item };
    const detail = cornerDetailInForce(c), own = c.lo.detailHi || '';
    const panel = toggleValue('cladding-type') === 'panel';
    const ed = document.getElementById('dim2d-editor');
    ed.innerHTML = `<div class="d2-title">${c.lo.name.replace('Elevation ', '')}–${c.hi.name.replace('Elevation ', '')} corner · ${CORNER_LABEL[detail]}</div>
        <select id="d2-corner">${cornerOptions(own, profileOffered(c.k), true)}</select>
        ${detail === 'lap' ? `<button class="mini" id="d2-swap">Swap master</button>` : ''}
        <button class="mini" id="d2-close">Done</button>`;
    ed.style.display = '';
    document.getElementById('d2-corner').onchange = ev => { setCornerDetail(name, index, ev.target.value); closeDimEditor(); };
    const swap = document.getElementById('d2-swap');
    if (swap) swap.onclick = () => { swapCorner(name, index); closeDimEditor(); };
    document.getElementById('d2-close').onclick = closeDimEditor;
    position2D();
}

function initDims2D() {
    view2d.onFrame = position2D;
    document.addEventListener('pointerdown', ev => {        // a click elsewhere closes the editor
        const ed = document.getElementById('dim2d-editor');
        if (D2.editing && ed && !ed.contains(ev.target) && !(D2.editing.item && D2.editing.item.el === ev.target)) closeDimEditor();
    });
}
