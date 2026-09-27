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
    cornerBadges(e, layer);
    if (D2.editing) {            // the preview was rebuilt under an open editor: re-anchor it
        const again = D2.items.find(i => i.el.dataset.key === D2.editing.key);
        if (again) D2.editing.item = again; else closeDimEditor();
    }
    position2D();
}

// Corners at the ends of the face: a badge for the detail in force (M, L, S), with the
// master for a lap. Clicking one offers the corner row's selector and Swap.
function cornerBadges(e, layer) {
    const box = cladBox(e), M = frameMatrix(e.result.frame, 60);
    chainCorners(e.chain).forEach((c, i) => {
        if (c.lo !== e && c.hi !== e) return;
        const runHi = c.lo === e;                         // the corner is at e's end of the run
        const u = (runHi !== !!e.rev) ? box.u1 : box.u0;
        const detail = cornerDetailInForce(c);
        const master = (c.lo.masterHi ? c.lo : c.hi).name.replace('Elevation ', '');
        const el = document.createElement('button');
        el.className = 'dim2d corner-badge';
        el.dataset.key = 'corner:' + i;
        el.textContent = { mitre: 'M', lap: 'L', butt: 'S' }[detail] + (detail === 'lap' ? ' · ' + master : '');
        el.title = `${c.lo.name.replace('Elevation ', '')}–${c.hi.name.replace('Elevation ', '')} corner: ${CORNER_LABEL[detail]}`
            + (detail === 'lap' ? `, ${master} masters` : '');
        const item = { corner: { chain: e.chain.name, index: i }, el,
                       at: new THREE.Vector3(u, box.v1 + 350, 0).applyMatrix4(M) };
        el.onclick = ev => { ev.stopPropagation(); openCornerEditor(item); };
        layer.appendChild(el);
        D2.items.push(item);
    });
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
    const ed = document.getElementById('dim2d-editor');
    if (D2.editing && D2.editing.item) {
        ed.style.left = D2.editing.item.el.style.left;
        ed.style.top = D2.editing.item.el.style.top;
    }
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
        <input id="d2-input" type="number" step="1" value="${Math.round(d.value)}"> <span class="unit">mm</span>
        ${scoped ? `<div class="d2-scope turn-toggle">
            <button class="turn-btn ${D2.scope === 'chain' ? 'active' : ''}" data-scope="chain">${e.chain.name}</button>
            <button class="turn-btn ${D2.scope === 'one' ? 'active' : ''}" data-scope="one">This elevation</button></div>` : ''}
        ${d.kind === 'row' ? `<div class="d2-actions"><button class="mini" id="d2-split">Split row</button>
            <button class="mini" id="d2-merge">Merge with row above</button></div>` : ''}
        <div class="d2-hint">Enter to apply · Esc to cancel · Tab for the next</div>`;
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
    input.onkeydown = ev => {
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
    if (!input || !item) return closeDimEditor();
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
    switch (d.kind) {
        case 'row': { const rows = withRow(e, d.row, v); members.forEach(m => { m.panelRows = rows.slice(); }); break; }
        case 'course': members.forEach(m => { m.cover = v; }); break;
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

// Split a row into two halves with the joint gap between them.
function splitRow(e, j) {
    const gap = parseFloat(document.getElementById('panel_gap').value) || 0;
    const h = rowsDrawn(e)[j];
    if (!h) return;
    const rows = withRow(e, j, h), half = Math.max(1, (h - gap) / 2);
    rows.splice(j, 1, half, half);
    d2SetRows(e, rows);
}

// Merge a row with the one above: one row spanning both and the joint between.
function mergeRow(e, j) {
    const gap = parseFloat(document.getElementById('panel_gap').value) || 0;
    const drawn = rowsDrawn(e);
    if (j + 1 >= drawn.length) { setStatus('That is the top row: there is no row above to merge with', 'busy'); return; }
    const rows = withRow(e, j + 1, drawn[j + 1]);
    rows.splice(j, 2, drawn[j] + gap + drawn[j + 1]);
    d2SetRows(e, rows);
}

function d2SetRows(e, rows) {
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
        <select id="d2-corner">${[['', 'Job default'], ['mitre', 'Mitred'], ['lap', 'Master lap'], ['butt', 'Square']]
            .map(([v, t]) => `<option value="${v}" ${v === own ? 'selected' : ''} ${v === 'lap' && !panel ? 'disabled' : ''}>${t}</option>`).join('')}</select>
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
