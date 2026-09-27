/* CladForge app — elevation state, Pyodide engine, live preview, downloads. */

const state = { elevations: [], chains: [], active: -1, pickMode: true, sliderDragging: false,
               model: null, seq: 0, editing: null, levels: null };
let pyodide = null, pyReady = false, ifcReady = false, _seq = 0, _numTimer = null, _restarting = false;

// ─── ELEVATIONS AND CHAINS ───
// An elevation is one coplanar region. A chain is an ordered run of elevations that
// meet at corners; coursing is set out along the whole run so joints carry round.
// A chain generates nothing until it is built: picking is a selection, the wizard
// turns it into cladding. See static/wizard.js.
function newChain() {
    const chain = { name: 'Chain ' + (++state.seq), members: [], length: 0, built: false,
                    topZ: null, bottomZ: null };
    state.chains.push(chain);
    return chain;
}

function newElevation(chain) {
    const n = state.elevations.length;
    const name = 'Elevation ' + String.fromCharCode(65 + (n % 26)) + (n >= 26 ? Math.floor(n / 26) : '');
    const e = { name, picks: [], result: null, manual: [], disabled: {},
                storey: null, highlights: [], chain: chain || newChain(), start: 0, rev: false, link: null,
                offset: 0,
                cornerLo: 0, cornerHi: 0, masterLo: false, masterHi: false, detailLo: null, detailHi: null,
                clipLo: 0, clipHi: null };
    e.chain.members.push(e);
    state.elevations.push(e);
    setActive(n);
    return e;
}

function deleteElevation() {
    if (state.active < 0) return;
    closeEditWidget();
    const e = state.elevations.splice(state.active, 1)[0];
    e.highlights.forEach(h => highlightGroup.remove(h));
    e.chain.members = e.chain.members.filter(m => m !== e);
    if (!e.chain.members.length) state.chains = state.chains.filter(c => c !== e.chain);
    else relinkChain(e.chain);
    setActive(Math.min(state.active, state.elevations.length - 1));
}

function chainLabel(e) { return e.chain.members.length > 1 ? e.chain.name + ' · ' + e.name : e.name; }

function setActive(i) {
    state.active = i;
    // Courses round a built chain are set out from the base of the elevation last clicked.
    const picked = state.elevations[i];
    if (picked && picked.chain.built && picked.result && picked.result.ok) picked.chain.datumFrom = picked;
    // Flat on an elevation, choosing another swings the view across to it.
    if (in2D() && picked && picked.result && picked.result.ok) enter2D(picked.result.name, picked.result.frame, cladBox(picked));
    renderElevationList();
    const sel = document.getElementById('active-elev');
    sel.innerHTML = state.elevations.map((e, k) => `<option value="${k}" ${k === i ? 'selected' : ''}>${chainLabel(e)}</option>`).join('');
    const e = state.elevations[i];
    document.getElementById('offset').value = e ? e.offset : 0;
    updateSliderRange();
    updatePreview();
}

function renameElevation(i, value) {
    state.elevations[i].name = value.trim() || state.elevations[i].name;
    if (state.elevations[i].result) state.elevations[i].result.name = state.elevations[i].name;
    setActive(state.active);
}

function abutKey(ab) { return ab.source + '|' + Math.round(ab.v) + '|' + Math.round(ab.u0); }

function toggleAbutment(i, key, on) {
    state.elevations[i].disabled[key] = !on;
    updatePreview();
}

function addManualLevel(i) {
    const input = document.getElementById('manual-level-' + i);
    const v = parseFloat(input.value);
    if (!isFinite(v)) return;
    state.elevations[i].manual.push(v);
    input.value = '';
    renderElevationList();
    updatePreview();
}

function removeManualLevel(i, k) {
    state.elevations[i].manual.splice(k, 1);
    renderElevationList();
    updatePreview();
}

function abutmentsFor(e) {
    if (!e.result || !e.result.ok) return [];
    const out = e.result.abutments.map(ab => Object.assign({}, ab, { enabled: !e.disabled[abutKey(ab)] }));
    e.manual.forEach((v, k) => out.push({ u0: 0, u1: e.result.width, v, line: [[0, v], [e.result.width, v]],
                                          source: 'manual', name: 'Manual level', enabled: true, k }));
    return out;
}

function elevationCard(e, i) {
    const r = e.result, ok = r && r.ok;
    const meta = ok ? `${Math.round(r.width)} × ${Math.round(r.height)} mm · ${r.n_holes} opening${r.n_holes === 1 ? '' : 's'}` : `${e.picks.length} pick${e.picks.length === 1 ? '' : 's'}`;
    const abuts = abutmentsFor(e).map(ab => ab.source === 'manual'
        ? `<label><input type="checkbox" checked disabled> Manual level @ ${Math.round(ab.v)} <button class="mini" onclick="event.stopPropagation(); removeManualLevel(${i}, ${ab.k})">×</button></label>`
        : `<label onclick="event.stopPropagation()"><input type="checkbox" ${ab.enabled ? 'checked' : ''} onchange="toggleAbutment(${i}, '${abutKey(ab)}', this.checked)"> ${ab.name || ab.source} (${ab.source}) ${ab.pitched ? `pitched ${Math.round(ab.v_min)}–${Math.round(ab.v)}, splash follows the roof` : '@ ' + Math.round(ab.v)}</label>`).join('');
    let warn = r && r.warnings && r.warnings.length ? `<div class="elev-warn">${r.warnings.join(' · ')}</div>` : '';
    if (e.error) warn = `<div class="elev-warn elev-error">✖ ${e.error} <button class="mini" onclick="event.stopPropagation(); runExtraction(state.elevations[${i}])">Retry</button></div>`;
    else if (!ok && e.picks.length) warn = `<div class="elev-warn">${e.pending ? 'Queued — waiting for the engine' : 'Extracting…'}</div>`;
    const clipped = ok && (e.clipLo > 0.5 || (e.clipHi !== null && e.clipHi < r.width - 0.5));
    const corners = [e.cornerLo, e.cornerHi].filter(k => k);
    const place = (e.chain.members.length > 1 && ok ? ` · run ${Math.round(e.start)}–${Math.round(e.start + cladWidth(e))}${e.rev ? ' ↺' : ''}` : '')
        + (corners.length ? ` · ${corners.length} corner${corners.length > 1 ? 's' : ''}` : '')
        + (clipped ? ` · clad ${Math.round(e.clipLo)}–${Math.round(e.clipHi === null ? r.width : e.clipHi)}` : '')
        + (e.cover ? ` · course ${Math.round(e.cover)}` : '')
        + (e.panelRows && e.panelRows.length ? ` · rows ${e.panelRows.map(Math.round).join('/')}` : '')
        + (ok && (e.chain.topZ !== null || e.chain.bottomZ !== null)
            ? ` · levels ${Math.round(Math.max(0, vLocal(e, e.chain.bottomZ) || 0))}–${Math.round(Math.min(r.height, vLocal(e, e.chain.topZ) === null ? r.height : vLocal(e, e.chain.topZ)))}` : '');
    return `<div class="elev-card ${i === state.active ? 'active' : ''} ${e.error ? 'error' : ''}" onclick="setActive(${i})">
        <div class="elev-head"><input class="elev-name" value="${e.name}" onclick="event.stopPropagation()" onchange="renameElevation(${i}, this.value)">
            <span class="elev-meta">${meta}${e.storey ? ' · ' + e.storey.name : ''}${place}</span></div>
        ${ok ? `<div class="elev-abut">Abutments (splash zone above each):${abuts}
            <div class="row" style="margin-top:4px"><input type="number" id="manual-level-${i}" placeholder="Level mm above base" onclick="event.stopPropagation()">
            <button class="mini" onclick="event.stopPropagation(); addManualLevel(${i})">Add level</button></div></div>` : ''}${warn}</div>`;
}

function renderElevationList() {
    update2DButton();
    const box = document.getElementById('elevation-list');
    if (!state.elevations.length) {
        box.innerHTML = '<p class="hint">No elevations yet. Load a model, then click a wall face.</p>';
        updateMakeChain();
        return;
    }
    let html = '';
    for (const chain of state.chains) {
        const members = chain.members.slice().sort((a, b) => a.start - b.start);
        const pending = !chain.built && members.some(m => m.result && m.result.ok)
            ? ` · <b class="chain-pending">not built — press Enter</b>` : '';
        if (members.length > 1) {
            html += `<div class="chain-head">${chain.name} · ${members.map(m => m.name.replace('Elevation ', '')).join(' → ')} · run ${Math.round(chain.length)} mm${pending}</div>`;
            html += cornerRows(chain, members);
        } else if (pending) {
            html += `<div class="chain-head">${chain.name}${pending}</div>`;
        }
        for (const m of members) html += elevationCard(m, state.elevations.indexOf(m));
    }
    box.innerHTML = html;
    updateMakeChain();
}

// ─── CORNERS ───
// One row per corner in a chain. The lap detail needs to know which face runs past
// the other, so each row names the master and can swap it.
function chainCorners(chain) {
    const members = chain.members.filter(m => m.result && m.result.ok).sort((a, b) => a.start - b.start);
    const out = [];
    for (let i = 0; i < members.length - 1; i++) {
        if (members[i].cornerHi) out.push({ lo: members[i], hi: members[i + 1], k: members[i].cornerHi });
    }
    return out;
}

// Each corner can override the job's corner detail. The two faces meeting there hold the
// same value (lo member's detailHi, hi member's detailLo), set together as the master is.
const CORNER_LABEL = { mitre: 'Mitred', lap: 'Master-lap, open joint', butt: 'Square' };

function cornerDetailInForce(c) {
    const job = toggleValue('corner-type') || 'mitre';
    const d = c.lo.detailHi || job;
    return (d === 'lap' && toggleValue('cladding-type') !== 'panel') ? 'mitre' : d;
}

function cornerRows(chain, members) {
    const rows = chainCorners(chain);
    if (!rows.length) return '';
    const panel = toggleValue('cladding-type') === 'panel';
    return rows.map((c, i) => {
        const angle = c.hi.link ? Math.abs(c.hi.link.angle).toFixed(0) : '';
        const kind = c.k > 0 ? 'external' : 're-entrant';
        const detail = cornerDetailInForce(c);
        const master = c.lo.masterHi ? c.lo.name : c.hi.name;
        const own = c.lo.detailHi || '';
        const pick = `<select class="corner-detail" title="Corner detail for this corner" onclick="event.stopPropagation()"
            onchange="setCornerDetail('${chain.name}', ${i}, this.value)">`
            + [['', 'Job default'], ['mitre', 'Mitred'], ['lap', 'Master lap'], ['butt', 'Square']]
                .map(([v, t]) => `<option value="${v}" ${v === own ? 'selected' : ''} ${v === 'lap' && !panel ? 'disabled' : ''}>${t}</option>`).join('')
            + '</select>';
        const swap = detail === 'lap'
            ? ` · <b>${master.replace('Elevation ', '')}</b> masters <button class="mini" onclick="event.stopPropagation(); swapCorner('${chain.name}', ${i})">Swap</button>`
            : '';
        return `<div class="corner-row" data-detail="${detail}" onclick="selectCorner('${chain.name}', ${i})">`
            + `${c.lo.name.replace('Elevation ', '')}–${c.hi.name.replace('Elevation ', '')} corner`
            + ` · ${angle}° ${kind} · <span class="corner-in-force">${CORNER_LABEL[detail]}${own ? '' : ' (job)'}</span>${swap} ${pick}</div>`;
    }).join('');
}

function setCornerDetail(chainName, index, value) {
    const chain = state.chains.find(c => c.name === chainName);
    const corner = chain && chainCorners(chain)[index];
    if (!corner) return;
    corner.lo.detailHi = corner.hi.detailLo = value || null;
    renderElevationList();
    updatePreview();
}

function swapCorner(chainName, index) {
    const chain = state.chains.find(c => c.name === chainName);
    const corner = chain && chainCorners(chain)[index];
    if (!corner) return;
    corner.lo.masterHi = !corner.lo.masterHi;
    corner.hi.masterLo = !corner.hi.masterLo;
    renderElevationList();
    updatePreview();
}

function selectCorner(chainName, index) {
    const chain = state.chains.find(c => c.name === chainName);
    const corner = chain && chainCorners(chain)[index];
    if (!corner) return;
    const e = corner.lo;
    highlightCorner(e.result.frame, e.rev ? 0 : e.result.width, e.result.height);
}

function samePlane(hit, e) {
    if (!e.result || !e.result.ok) return e.picks.length === 0;
    const f = e.result.frame, n = toIfc(hit.normal), p = toIfc(hit.point);
    if (n[0] * f.n[0] + n[1] * f.n[1] < 0.9998) return false;
    const d = f.n[0] * (p[0] - f.origin[0]) + f.n[1] * (p[1] - f.origin[1]);
    return Math.abs(d) < 25;
}

async function pyLink(a, b) {
    const slim = r => JSON.stringify({ frame: r.frame, width: r.width, height: r.height });
    pyodide.globals.set('_link_a', slim(a)); pyodide.globals.set('_link_b', slim(b));
    const out = await pyodide.runPythonAsync(`
import json as _json
from fabric_extract import chain_link as _cl
_json.dumps(_cl(_json.loads(_link_a), _json.loads(_link_b)))`);
    return JSON.parse(out);
}

function placeInChain(e, other, link) {
    // Chain coordinate c runs along the whole run. *other* occupies [start, start + W];
    // the corner sits at one of its ends, and e extends away from that corner.
    const W = e.result.width, Wo = other.result.width;
    const cornerC = (link.end_a === 'right') !== other.rev ? other.start + Wo : other.start;
    const after = cornerC >= other.start + Wo - 1;
    if (after) { e.start = cornerC; e.rev = link.end_b === 'right'; }
    else { e.start = cornerC - W; e.rev = link.end_b === 'left'; }
    // Cut each face back to the corner. A wall that runs past it belongs to the other
    // face from there on, and cladding it would project through the corner.
    const clamp = (u, width) => Math.max(0, Math.min(width, u));
    if (link.end_a === 'right') other.clipHi = clamp(link.corner_u_a, Wo);
    else other.clipLo = clamp(link.corner_u_a, Wo);
    if (link.end_b === 'right') e.clipHi = clamp(link.corner_u_b, W);
    else e.clipLo = clamp(link.corner_u_b, W);
    // The corner's slope belongs to the touching end of both members. The face that
    // was already in the chain masters the lap by default; the corner row swaps it.
    const k = link.k || 0;
    if (after) { other.cornerHi = k; e.cornerLo = k; other.masterHi = true; e.masterLo = false; other.detailHi = e.detailLo = null; }
    else { other.cornerLo = k; e.cornerHi = k; other.masterLo = true; e.masterHi = false; other.detailLo = e.detailHi = null; }
    e.link = link;
}

function cladWidth(m) {
    // The clad part of the face: a wall running past a corner is cut back to it.
    const hi = m.clipHi === null || m.clipHi === undefined ? m.result.width : m.clipHi;
    const w = hi - (m.clipLo || 0);
    return w > 1 ? w : m.result.width;
}

function relayoutChain(chain) {
    const placed = chain.members.filter(m => m.result && m.result.ok);
    const lo = placed.length ? Math.min(...placed.map(m => m.start)) : 0;
    placed.forEach(m => m.start -= lo);
    chain.length = placed.length ? Math.max(...placed.map(m => m.start + cladWidth(m))) : 0;
}

async function linkIntoChain(e) {
    const chain = e.chain;
    const others = chain.members.filter(m => m !== e && m.result && m.result.ok).reverse();
    for (const other of others) {
        let link = null;
        try { link = await pyLink(other.result, e.result); } catch (err) { console.warn('link failed', err); }
        if (link) { placeInChain(e, other, link); relayoutChain(chain); return true; }
    }
    if (others.length) {   // not adjacent to anything in this chain: give it a chain of its own
        chain.members = chain.members.filter(m => m !== e);
        e.chain = newChain(); e.chain.members.push(e);
    }
    e.start = 0; e.rev = false; e.link = null; e.cornerLo = 0; e.cornerHi = 0; e.detailLo = e.detailHi = null;
    e.clipLo = 0; e.clipHi = null;
    relayoutChain(e.chain);
    return false;
}

async function relinkChain(chain) {
    const members = chain.members.slice().sort((a, b) => a.start - b.start);
    chain.members = [];
    members.forEach(m => { m.cornerLo = 0; m.cornerHi = 0; m.masterLo = false; m.masterHi = false; m.detailLo = m.detailHi = null;
                           m.link = null; m.clipLo = 0; m.clipHi = null; });
    for (const m of members) { m.chain = chain; chain.members.push(m); if (m.result && m.result.ok) await linkIntoChain(m); }
    renderElevationList();
    updatePreview();
}

// ─── 2D ELEVATION ───
// The clad part of an elevation in its own (u, v): cut back at corners, and between the
// chain's picked top and bottom where they are set.
function cladBox(e) {
    const r = e.result, lo = vLocal(e, e.chain.bottomZ), hi = vLocal(e, e.chain.topZ);
    const u0 = e.clipLo || 0, u1 = (e.clipHi === null || e.clipHi === undefined) ? r.width : e.clipHi;
    return { u0, u1: u1 - u0 > 1 ? u1 : r.width,
             v0: Math.max(0, lo || 0), v1: Math.min(r.height, hi === null ? r.height : hi) };
}

function can2D() {
    const e = state.elevations[state.active];
    return !!(e && e.result && e.result.ok);
}

function toggle2D() {
    if (in2D()) { exit2D(); update2DButton(); renderDims2D(); return; }
    if (!can2D()) { setStatus('Pick and extract an elevation first — the 2D view looks at the active one', 'busy'); return; }
    const e = state.elevations[state.active];
    enter2D(e.result.name, e.result.frame, cladBox(e));
    update2DButton();
    renderDims2D();
    setStatus('2D elevation: click a dimension to type over it · E or Esc for 3D', 'ready');
}

function update2DButton() {
    for (const id of ['view-2d', 'edit-2d']) {
        const b = document.getElementById(id);
        if (!b) continue;
        b.textContent = in2D() ? '3D' : '2D elevation';
        b.disabled = !in2D() && !can2D();
        b.title = in2D() ? 'Back to the 3D view (Esc)' : 'Look at the active elevation flat and square-on (E)';
    }
}

// ─── EDIT MODE ───
// A built chain is edited, not re-picked. Clicking one of its faces brings its
// setting-out over the view. The offset is this elevation's alone, so two faces of a
// chain can be set out differently — at the cost of the joints carrying round.
function openEditWidget(e) {
    state.editing = e;
    document.getElementById('edit-widget').style.display = '';
    document.getElementById('edit-title').textContent = chainLabel(e);
    document.getElementById('edit-datum').textContent = e.chain.members.length > 1
        ? 'Courses round ' + e.chain.name + ' now start from the base of ' + e.name + '.' : '';
    updateSliderRange();
    setStatus('Editing ' + chainLabel(e) + ' — shift this elevation\'s setting-out', 'ready');
}

function closeEditWidget() {
    if (!state.editing) return;
    state.editing = null;
    document.getElementById('edit-widget').style.display = 'none';
}

function onEditSlide(value) {
    document.getElementById('offset').value = value;
    onSlider(value);
    document.getElementById('edit-offset-val').textContent = Math.round(value) + ' mm';
}

function nudgeOffset(step) {
    const s = document.getElementById('edit-offset');
    onEditSlide(s.value = Math.max(+s.min, Math.min(+s.max, parseFloat(s.value) + step)));
}

// ─── PICKING → EXTRACTION ───
function setPickMode(on) {
    state.pickMode = on;
    document.querySelectorAll('#pick-toggle .turn-btn').forEach(b => b.classList.toggle('active', (b.dataset.value === 'pick') === on));
}

function onViewportClick(event) {
    if (event.target !== renderer.domElement || !allMeshes.length || state._dragged) return;
    const dim = pickDim(event);
    if (dim && !state.levels) { openCourseDialog(dim); return; }
    // While the level picker is open every click is a height, wherever it lands.
    if (state.levels) {
        const snap = snapPick(event);
        if (snap) levelPicked(toIfc(snap.point)[2]);
        return;
    }
    if (!state.pickMode) return;
    const hit = pickAt(event);
    if (!hit) return;
    const faces = coplanarFaces(hit.mesh, hit.faceIndex);
    if (!faces) { setStatus('That face is not vertical — pick a wall face', 'busy'); return; }
    // A face already clad is not a selection any more. Clicking it edits its chain's
    // setting-out instead of piling another elevation onto the same plane.
    const owner = state.elevations.find(m => m.result && m.result.ok && samePlane(hit, m));
    if (owner && owner.chain.built) { setActive(state.elevations.indexOf(owner)); openEditWidget(owner); return; }
    closeEditWidget();
    if (state.active < 0) newElevation();
    let e = state.elevations[state.active];
    // Coplanar with an elevation anywhere: merge into it. Otherwise start a new elevation
    // in the active chain; after extraction it links at a corner or moves to its own chain.
    if (!samePlane(hit, e)) e = owner || newElevation(e.chain);
    const existing = e.picks.findIndex(p => p.mesh === hit.mesh && p.faces.includes(hit.faceIndex));
    if (existing >= 0) e.picks.splice(existing, 1);
    else e.picks.push({ mesh: hit.mesh, faces, normal: toIfc(hit.normal), point: toIfc(hit.point) });
    e.highlights.forEach(h => { highlightGroup.remove(h); h.geometry.dispose(); });
    e.highlights = e.picks.map(p => highlightFaces(p.mesh, p.faces));
    if (!e.storey) { const meta = meshMeta[hit.mesh.userData.index]; e.storey = meta && meta.storey ? meta.storey : null; }
    runExtraction(e);
}

async function runExtraction(e) {
    if (!e.picks.length) { e.result = null; renderElevationList(); updatePreview(); return; }
    if (!pyReady) {   // engine loading or rebuilding: queue it, initPyodide picks it up
        e.pending = true;
        setStatus('Waiting for the engine — ' + e.name + ' is queued', 'busy');
        renderElevationList();
        return;
    }
    // One attempt per elevation at a time. A call that arrives mid-flight is never
    // dropped: it is remembered and run once the current one ends, with the picks as
    // they are then — so a pick added while extracting is not lost, and nothing stalls
    // silently behind a flag that was never cleared.
    if (e._running) {
        e._again = true;
        log(`${e.name}: still extracting — will run again with the latest picks`);
        return;
    }
    e._running = true;
    e.pending = false;
    try {
        await extractOnce(e);
    } finally {
        e._running = false;
    }
    if (e._again) { e._again = false; return runExtraction(e); }
}

async function extractOnce(e) {
    const tris = [];
    for (const p of e.picks) tris.push(...faceTriangles(p.mesh, p.faces));
    const { elements: context, dropped, triangles: nTris } = contextFor(new Set(e.picks.map(p => p.mesh)), tris, 300, e.picks[0].normal);
    setStatus(`Extracting ${e.name}… ${tris.length} faces, ${context.length} nearby elements (${nTris} triangles)`
              + (dropped ? `, ${dropped} too far to fit` : ''), 'busy');
    e.error = null;
    renderElevationList();
    await new Promise(r => setTimeout(r, 30));   // let the status paint before Pyodide blocks the thread
    const payload = { name: e.name, faces: tris, outward: e.picks[0].normal, context,
                      seeds: e.picks.map(p => p.point).filter(Boolean),
                      options: { penetrations: document.getElementById('penetrations').checked } };
    const json = JSON.stringify(payload);
    log(`${e.name}: extracting — ${tris.length} faces, ${context.length} context elements, `
        + `${nTris} triangles, ${dropped} dropped, payload ${Math.round(json.length / 1024)} kB`);
    try {
        const t0 = performance.now();
        pyodide.globals.set('_payload_json', json);
        pyodide.globals.set('_params_json', JSON.stringify(getParams()));
        // The engine works out the cladding zone's depth, so the rule lives in one place.
        const out = await pyodide.runPythonAsync(`
import json as _json
from fabric_extract import extract_elevation as _ex
from cladding_constants import _parse as _parse_params
from cladding_primitives import buildup_depth as _buildup_depth
_pl = _json.loads(_payload_json)
_pl.setdefault("options", {})["clad_depth"] = _buildup_depth(_parse_params(_json.loads(_params_json)))
_json.dumps(_ex(_pl))`);
        e.result = JSON.parse(out);
        const slow = e.result.timings_ms || {};
        log(`${e.name}: ${e.result.ok ? 'ok' : 'FAILED'} in ${Math.round(performance.now() - t0)} ms`
            + (Object.keys(slow).length ? ` · slowest: ${Object.entries(slow).map(([k, v]) => k + ' ' + v + 'ms').join(', ')}` : '')
            + (e.result.warnings || []).map(w => ' · ' + w).join(''));
        if (e.result.ok) {
            if (dropped) e.result.warnings.push(`${dropped} nearby element(s) left out to keep the engine within memory`);
            const linked = await linkIntoChain(e);
            const corner = linked ? `${e.name} joined ${e.chain.name} at a corner (${Math.abs(e.link.angle)}°)` : e.name + ' extracted';
            setStatus(e.chain.built ? corner : corner + ' — press Enter to build', 'ready');
            // The view stays where it was put: picking a face must not move the camera.
        } else {
            e.error = e.result.warnings.join('; ') || 'Extraction failed';
            e.result = null;
            setStatus(e.name + ': ' + e.error, 'busy');
        }
    } catch (err) {
        console.error(err);
        // A C++ abort inside WASM kills the runtime for good rather than raising, so the
        // only way back is a fresh one. Rebuild it once and retry before giving up.
        if (/fatally failed|Aborted/i.test(err.message || '') && !e._restarted) {
            e._restarted = true;
            e.pending = true;
            restartEngine();         // resumes every elevation still without a result
            return;
        }
        e.result = null; e.error = 'Extraction error: ' + err.message;
        log(`${e.name}: threw — ${err.message}`);
        setStatus(e.name + ': ' + e.error, 'busy');
    }
    renderElevationList();
    updateSliderRange();
    updatePreview();
}

// ─── PARAMETERS ───
const NUM = ['sheathing_t', 'insulation_t', 'batten_w', 'batten_d', 'batten_centres', 'cb_w', 'cb_d',
             'cb_centres', 'splash',
             'panel_t', 'panel_w', 'panel_h', 'panel_gap', 'plank_w', 'plank_t', 'plank_lap', 'plank_gap',
             'plank_len', 'closer_w'];
function val(id) { return document.getElementById(id).value; }
function toggleValue(id) { const b = document.querySelector('#' + id + ' .turn-btn.active'); return b ? b.dataset.value : null; }
function selectToggle(id, value) {
    document.querySelectorAll('#' + id + ' .turn-btn').forEach(b => b.classList.toggle('active', b.dataset.value === value));
}

function elevationRecords() {
    const out = [];
    for (const chain of state.chains) {
        if (!chain.built) continue;   // picked but not built: nothing is generated for it yet
        const members = chain.members.filter(e => e.result && e.result.ok).sort((a, b) => a.start - b.start);
        for (const e of members) {
            out.push(Object.assign({}, e.result, { name: e.name, offset: e.offset || 0, abutments: abutmentsFor(e), storey: e.storey,
                                                   chain: members.length > 1 ? chain.name : null, chain_start: e.start,
                                                   chain_reversed: e.rev, corner_lo: e.cornerLo || 0,
                                                   corner_hi: e.cornerHi || 0, master_lo: !!e.masterLo,
                                                   master_hi: !!e.masterHi, detail_lo: e.detailLo || null,
                                                   detail_hi: e.detailHi || null, clip_lo: e.clipLo || 0,
                                                   clip_hi: e.clipHi,
                                                   clip_v_lo: vLocal(e, chain.bottomZ) || 0,
                                                   clip_v_hi: vLocal(e, chain.topZ),
                                                   cover: e.cover || null, panel_rows: e.panelRows || null,
                                                   course_datum_from: chain.datumFrom ? chain.datumFrom.name : null }));
        }
    }
    return out;
}

// A picked level is a world height; each elevation reads it in its own v.
function vLocal(e, z) {
    return (z === null || z === undefined) ? null : z - e.result.frame.origin[2];
}

function getParams() {
    const p = { elevations: elevationRecords(), context: Object.assign({ offset: modelOffset }, modelContext),
                corner: toggleValue('corner-type') || 'mitre',
                trim: !state.sliderDragging && document.getElementById('live-trim').checked };
    for (const k of NUM) p[k] = parseFloat(val(k));
    p.sheathing = document.getElementById('sheathing').checked;
    p.insulation = document.getElementById('insulation').checked;
    p.reveals = document.getElementById('reveals').checked;
    p.set_out_from_openings = document.getElementById('set_out_from_openings').checked;
    p.cladding_type = toggleValue('cladding-type');
    p.plank_orient = toggleValue('plank-orient');
    p.counter_batten = val('counter_batten');
    return p;
}

function onTypeChange() {
    const panel = toggleValue('cladding-type') === 'panel';
    // The master-lap needs a board to run past the corner, so it is a panel detail.
    const lap = document.querySelector('#corner-type .turn-btn[data-value="lap"]');
    lap.disabled = !panel;
    lap.title = panel ? '' : 'Panel cladding only';
    lap.style.opacity = panel ? '' : '0.45';
    if (!panel && lap.classList.contains('active')) selectToggle('corner-type', 'mitre');
    document.getElementById('panel-section').style.display = panel ? '' : 'none';
    document.getElementById('plank-section').style.display = panel ? 'none' : '';
    document.getElementById('batten_centres').readOnly = panel;
    renderElevationList();              // the corner rows say which detail is in force
    updateSliderRange();
    updatePreview();
}

function updateSliderRange() {
    const p = getParams();
    let half;
    if (p.cladding_type === 'panel') half = (p.panel_w + p.panel_gap) / 2;
    else if (p.plank_orient === 'vertical') half = (p.plank_lap > 0 ? p.plank_w - p.plank_lap : p.plank_w + p.plank_gap) / 2;
    else half = p.batten_centres / 2;
    const s = document.getElementById('offset');
    s.min = -Math.round(half); s.max = Math.round(half); s.step = 5;
    const e = state.elevations[state.active];
    if (e) { e.offset = Math.max(-half, Math.min(half, e.offset || 0)); s.value = e.offset; }
    document.getElementById('offset-val').textContent = (e ? e.offset : 0) + ' mm';
    const w = document.getElementById('edit-offset');   // the widget mirrors the panel slider
    w.min = s.min; w.max = s.max; w.step = s.step; w.value = s.value;
    document.getElementById('edit-offset-val').textContent = (e ? Math.round(e.offset || 0) : 0) + ' mm';
}

function onSlider(value) {
    const e = state.elevations[state.active];
    if (!e) return;
    e.offset = parseFloat(value);
    document.getElementById('offset-val').textContent = e.offset + ' mm';
    document.getElementById('edit-offset').value = e.offset;
    document.getElementById('edit-offset-val').textContent = Math.round(e.offset) + ' mm';
    updatePreview();
}

function onNumeric() {   // 300 ms debounce on typed numbers
    if (_numTimer) clearTimeout(_numTimer);
    _numTimer = setTimeout(() => { updateSliderRange(); updatePreview(); }, 300);
}

// ─── PREVIEW ───
async function updatePreview() {
    const params = getParams();
    updateDerivedStatic(params);
    if (!pyReady || !params.elevations.length) {
        renderGeometry([]); renderOutlines([], 0); renderDimensions([], {}); renderChecks([]); renderInfo([]);
        renderDims2D();
        return;
    }
    const seq = ++_seq;
    try {
        pyodide.globals.set('_params_json', JSON.stringify(params));
        const out = await pyodide.runPythonAsync(`
import json as _json
from cladding_preview import generate_preview as _gp, check_rules as _cr
_p = _json.loads(_params_json)
_o = _gp(_p)
_o["checks"] = _cr(_p, _o["info"])
_json.dumps(_o)`);
        if (seq !== _seq) return;
        const result = JSON.parse(out);
        window._lastPreview = result;
        const byName = {};
        params.elevations.forEach(e => byName[e.name] = e);
        renderGeometry(result.geometry);
        renderOutlines(params.elevations, params.splash);
        const act = state.elevations[state.active];
        renderDimensions(result.dimensions, byName, act && act.result ? act.result.name : null);
        renderDims2D();
        renderChecks(result.checks);
        renderInfo(result.info);
    } catch (err) {
        console.error('preview failed', err);
        if (/fatally failed|Aborted/i.test(err.message || '')) {
            if (await restartEngine()) return updatePreview();
        }
        setStatus('Preview error: ' + err.message, 'busy');
    }
}

function renderChecks(checks) {
    const box = document.getElementById('checks-container');
    box.innerHTML = checks.length ? '' : '<p class="hint">Checks appear once an elevation is extracted.</p>';
    for (const c of checks) {
        const div = document.createElement('div');
        div.className = 'check-item';
        div.innerHTML = `<div class="check-dot ${c.status}"></div><span>${c.message}</span>`;
        box.appendChild(div);
    }
}

function updateDerivedStatic(p) {
    const vertical = p.cladding_type === 'plank' && p.plank_orient === 'vertical';
    const battens = vertical ? 'Horizontal' : 'Vertical';
    const cb = p.counter_batten === 'auto' ? vertical : p.counter_batten === 'yes';
    document.getElementById('d-battens').textContent = battens + (cb ? ' on vertical counter-battens' : '');
    document.getElementById('d-cover').textContent = p.cladding_type === 'panel'
        ? `${p.panel_w + p.panel_gap} mm bay` : `${p.plank_lap > 0 ? p.plank_w - p.plank_lap : p.plank_w + p.plank_gap} mm`;
    if (p.cladding_type === 'panel') {
        const bay = p.panel_w + p.panel_gap;
        document.getElementById('batten_centres').value = (bay / Math.ceil(bay / 600)).toFixed(0);
    }
}

function renderInfo(infos) {
    const e = state.elevations[state.active];
    const i = infos.find(x => e && x.elevation === e.name) || infos[0];
    const set = (id, v) => document.getElementById(id).textContent = v;
    if (!i) { ['dim-size', 'dim-centres', 'dim-courses', 'dim-cuts', 'dim-boards'].forEach(id => set(id, '--')); return; }
    set('dim-size', `${Math.round(i.width)} × ${Math.round(i.height)} mm`);
    set('dim-centres', `${i.batten_centres.toFixed(0)} mm`);
    set('dim-courses', `${i.n_courses} @ ${i.cover.toFixed(0)}`);
    set('dim-cuts', `L ${Math.round(i.closing_cut_left)} · R ${Math.round(i.closing_cut_right)} · T ${Math.round(i.closing_cut_top)}`);
    set('dim-boards', `${i.n_boards} (setting-out only)`);
}

function setStatus(text, cls) {
    const chip = document.getElementById('status-chip');
    chip.textContent = text; chip.className = 'status-chip ' + (cls || '');
}

// ─── MODEL LOADING ───
async function onFileChosen(input) {
    if (!input.files.length) return;
    await loadModel(input.files[0]);
}

function retryOnServer() {
    if (state.file) loadModel(state.file, 'server');
}

async function loadModel(file, force) {
    const info = document.getElementById('model-info');
    state.file = file;
    closeEditWidget();
    try {
        state.elevations.forEach(e => e.highlights.forEach(h => highlightGroup.remove(h)));
        state.elevations = []; state.chains = []; state.active = -1; state.seq = 0; renderElevationList();
        const summary = await loadIFC(file, t => setStatus(t, 'busy'), force);
        state.model = summary;
        const notes = (summary.warnings || []).filter(Boolean);
        info.innerHTML = `<b>${file.name}</b><br>${summary.meshes} elements · ${summary.storeys} storeys · `
            + `${summary.context.project || 'unnamed project'}<br><span class="hint">Read by ${summary.reader}.</span>`
            + (notes.length ? `<div class="elev-warn">${notes.join('<br>')}</div>` : '')
            + (summary.reader.indexOf('server') < 0
                ? `<button class="btn btn-secondary btn-sm" style="margin-top:6px" onclick="retryOnServer()">Re-import on the server</button>` : '');
        setStatus(pyReady ? 'Ready — click a wall face' : 'Model loaded, engine still loading…', pyReady ? 'ready' : 'busy');
        newElevation();
    } catch (err) {
        console.error(err);
        info.innerHTML = `<div class="elev-warn elev-error">Load failed: ${err.message}</div>`
            + `<button class="btn btn-secondary btn-sm" style="margin-top:6px" onclick="retryOnServer()">Try the server importer</button>`;
        setStatus('Load failed', 'busy');
    }
}

// ─── DOWNLOADS ───
let _pendingBlob = null, _pendingExt = '';
function downloadName(ext) {
    const d = new Date(), stamp = [String(d.getDate()).padStart(2, '0'), String(d.getMonth() + 1).padStart(2, '0'), String(d.getFullYear()).slice(-2)].join('-');
    const key = 'cladforge_dl_' + ext + '_' + stamp;
    let n = 1;
    try { n = parseInt(localStorage.getItem(key) || '0', 10) + 1; localStorage.setItem(key, String(n)); } catch (e) { /* private mode */ }
    return `CladForge_${stamp}_${n}.${ext}`;
}
function showReminder(blob, ext) { _pendingBlob = blob; _pendingExt = ext; document.getElementById('download-reminder').classList.add('open'); }
function confirmDownload() {
    document.getElementById('download-reminder').classList.remove('open');
    if (!_pendingBlob) return;
    const url = URL.createObjectURL(_pendingBlob), a = document.createElement('a');
    a.href = url; a.download = downloadName(_pendingExt); document.body.appendChild(a); a.click();
    document.body.removeChild(a); URL.revokeObjectURL(url); _pendingBlob = null;
}

function busy(btn, on, label) {
    btn.disabled = on; btn.classList.toggle('btn-generating', on);
    btn.innerHTML = on ? '<span class="btn__text">' + label + '</span>' : btn.dataset.label;
}

async function downloadIFC() {
    const params = getParams(); params.trim = true;
    if (!params.elevations.length) { alert('Pick some faces and build the chain first (press Enter).'); return; }
    const btn = document.getElementById('ifc-btn');
    busy(btn, true, 'Generating IFC4X3…');
    try {
        await ensureIfcOpenShell(btn);
        pyodide.globals.set('_params_json', JSON.stringify(params));
        const proxy = await pyodide.runPythonAsync(`
import json as _json, os as _os
from cladding_preview import generate_preview as _gp
from ifc_generator import meshes_to_ifc as _to_ifc
_p = _json.loads(_params_json)
_o = _gp(_p)
_path = _to_ifc(_o["geometry"], _p, _o["info"])
with open(_path, 'rb') as _f:
    _data = _f.read()
_os.unlink(_path)
_data`);
        const blob = new Blob([proxy.toJs()], { type: 'application/x-step' });
        if (proxy.destroy) proxy.destroy();
        showReminder(blob, 'ifc');
    } catch (err) { alert('IFC export failed: ' + err.message); }
    finally { busy(btn, false); }
}

async function ensureIfcOpenShell(btn) {
    if (ifcReady) return;
    busy(btn, true, 'Installing IfcOpenShell (one-off, ~40 MB)…');
    await pyodide.loadPackage(['numpy']);
    await pyodide.runPythonAsync(`
import micropip
await micropip.install(["lark", "isodate", "python-dateutil", "typing_extensions"])
await micropip.install("https://ifcopenshell.github.io/wasm-wheels/ifcopenshell-0.8.5-cp313-cp313-pyodide_2025_0_wasm32.whl")
import ifc_generator`);
    ifcReady = true;
}

async function downloadDXF() {
    if (!pyReady) { alert('The engine is still loading.'); return; }
    const params = getParams(); params.trim = true;
    if (!params.elevations.length) { alert('Pick some faces and build the chain first (press Enter).'); return; }
    const btn = document.getElementById('dxf-btn');
    busy(btn, true, 'Generating DXF…');
    try {
        pyodide.globals.set('_params_json', JSON.stringify(params));
        const dxf = await pyodide.runPythonAsync(`
import json as _json
from cladding_preview import generate_preview as _gp
from dxf_generator import meshes_to_dxf_string as _to_dxf
_p = _json.loads(_params_json)
_o = _gp(_p)
_to_dxf(_o["geometry"], _p, _o["info"])`);
        showReminder(new Blob([dxf], { type: 'application/dxf' }), 'dxf');
    } catch (err) { alert('DXF export failed: ' + err.message); }
    finally { busy(btn, false); }
}

// ─── DIAGNOSTICS ───
// Extraction runs in a WASM runtime that can die outright, so the log is the only
// record of what a stuck elevation was doing. cladforge() dumps it with the state.
const LOG = [];
function log(line) {
    LOG.push(new Date().toISOString().slice(11, 23) + '  ' + line);
    if (LOG.length > 300) LOG.shift();
    console.log('[cladforge]', line);
}

function cladforge() {
    const dump = {
        engine: { pyReady, ifcReady, restarting: _restarting, alive: !!pyodide },
        model: state.model && { file: state.file && state.file.name, meshes: state.model.meshes,
                                storeys: state.model.storeys, reader: state.model.reader },
        elevations: state.elevations.map(e => ({
            name: e.name, chain: e.chain.name, built: e.chain.built, picks: e.picks.length,
            state: e.result ? 'extracted' : (e.error ? 'error' : (e.pending ? 'queued' : (e._running ? 'running' : 'idle'))),
            error: e.error || null,
            size: e.result ? `${Math.round(e.result.width)} x ${Math.round(e.result.height)}` : null,
            warnings: (e.result && e.result.warnings) || [],
        })),
        log: LOG,
    };
    console.log(JSON.stringify(dump, null, 2));
    return dump;
}
window.cladforge = cladforge;

// ─── PYODIDE ───
async function restartEngine() {
    if (_restarting) return false;          // one rebuild at a time
    _restarting = true;
    log('engine died — rebuilding');
    setStatus('Engine stopped — rebuilding it…', 'busy');
    pyReady = false; ifcReady = false; pyodide = null; window.pyodide = null;
    try {
        await initPyodide();     // resumes anything queued while it was down
    } catch (err) {
        console.error('engine restart failed', err);
    }
    _restarting = false;
    return pyReady;
}

async function initPyodide() {
    try {
        setStatus('Loading Python runtime…', 'busy');
        pyodide = await loadPyodide();
        window.pyodide = pyodide;
        setStatus('Loading Shapely…', 'busy');
        await pyodide.loadPackage(['shapely', 'micropip']);
        const modules = ['cladding_constants', 'cladding_primitives', 'cladding_geometry', 'cladding_booleans',
                         'cladding_checks', 'cladding_preview', 'fabric_extract', 'dxf_generator',
                         'ifc_generator'];
        const v = Date.now();
        for (const mod of modules) {
            const src = await (await fetch(mod + '.py?v=' + v)).text();
            pyodide.FS.writeFile('/home/pyodide/' + mod + '.py', src);
        }
        await pyodide.runPythonAsync(`
import sys
sys.path.insert(0, '/home/pyodide')
import cladding_preview, fabric_extract, dxf_generator`);
        pyReady = true;
        log('engine ready');
        setStatus(allMeshes.length ? 'Ready — click a wall face' : 'Ready — load an IFC', 'ready');
        const queued = state.elevations.filter(e => e.picks.length && !e.result);
        if (queued.length) log(`engine ready — resuming ${queued.map(e => e.name).join(', ')}`);
        for (const e of queued) runExtraction(e);
        updatePreview();
    } catch (err) { console.error(err); setStatus('Engine failed to load: ' + err.message, 'busy'); }
}

// ─── INIT ───
function initApp() {
    initThree();
    initWebIfc();
    initPyodide();
    initWizard();
    initDims2D();
    renderElevationList();
    onTypeChange();
    const vp = document.getElementById('viewport');
    let down = null;
    vp.addEventListener('pointerdown', e => { down = [e.clientX, e.clientY]; state._dragged = false; });
    vp.addEventListener('pointermove', e => { if (down && Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 4) state._dragged = true; });
    vp.addEventListener('click', onViewportClick);
    // While picking levels, show where the click would land and whether it snaps.
    vp.addEventListener('pointermove', e => {
        if (!state.levels || e.target !== renderer.domElement) { if (snapMarker) showSnap(null); return; }
        showSnap(snapPick(e));
    });
    const drop = document.getElementById('file-drop');
    drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('over'); });
    drop.addEventListener('dragleave', () => drop.classList.remove('over'));
    drop.addEventListener('drop', e => { e.preventDefault(); drop.classList.remove('over'); if (e.dataTransfer.files.length) loadModel(e.dataTransfer.files[0]); });
    const release = () => { if (state.sliderDragging) { state.sliderDragging = false; updatePreview(); } };
    for (const id of ['offset', 'edit-offset']) {
        const slider = document.getElementById(id);
        slider.addEventListener('pointerdown', () => { state.sliderDragging = true; });
        slider.addEventListener('pointerup', release);
        slider.addEventListener('change', release);
    }
    document.addEventListener('keydown', e => {
        const el = document.activeElement, typing = el && /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName);
        const modal = ['chain-wizard', 'course-dialog', 'download-reminder'].some(id => document.getElementById(id).classList.contains('open'));
        if ((e.key === 'e' || e.key === 'E') && !typing && !modal && !e.ctrlKey && !e.metaKey && !e.altKey) { toggle2D(); return; }
        if (e.key !== 'Escape' || document.getElementById('chain-wizard').classList.contains('open')) return;
        if (document.getElementById('course-dialog').classList.contains('open')) { closeCourseDialog(); return; }
        if (state.levels) { dismissLevels(); return; }
        // Escape unwinds one thing at a time: the edit widget first, then the flat view.
        if (state.editing) { closeEditWidget(); return; }
        if (in2D()) { exit2D(); update2DButton(); renderDims2D(); }
    });
    document.getElementById('download-reminder').addEventListener('click', e => { if (e.target.id === 'download-reminder') e.currentTarget.classList.remove('open'); });
}
