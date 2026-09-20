/* CladForge app — elevation state, Pyodide engine, live preview, downloads. */

const state = { elevations: [], active: -1, pickMode: true, sliderDragging: false, model: null };
let pyodide = null, pyReady = false, ifcReady = false, _seq = 0, _numTimer = null;

// ─── ELEVATIONS ───
function newElevation() {
    const n = state.elevations.length;
    const name = 'Elevation ' + String.fromCharCode(65 + (n % 26)) + (n >= 26 ? Math.floor(n / 26) : '');
    state.elevations.push({ name, color: ELEV_COLORS[n % ELEV_COLORS.length], picks: [], result: null,
                            manual: [], disabled: {}, offset: 0, storey: null, highlights: [] });
    setActive(n);
}

function deleteElevation() {
    if (state.active < 0) return;
    const e = state.elevations.splice(state.active, 1)[0];
    e.highlights.forEach(h => highlightGroup.remove(h));
    setActive(Math.min(state.active, state.elevations.length - 1));
}

function setActive(i) {
    state.active = i;
    renderElevationList();
    const sel = document.getElementById('active-elev');
    sel.innerHTML = state.elevations.map((e, k) => `<option value="${k}" ${k === i ? 'selected' : ''}>${e.name}</option>`).join('');
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
    e.manual.forEach((v, k) => out.push({ u0: 0, u1: e.result.width, v, source: 'manual', name: 'Manual level', enabled: true, k }));
    return out;
}

function renderElevationList() {
    const box = document.getElementById('elevation-list');
    if (!state.elevations.length) { box.innerHTML = '<p class="hint">No elevations yet. Load a model, then click a wall face.</p>'; return; }
    box.innerHTML = state.elevations.map((e, i) => {
        const r = e.result, ok = r && r.ok;
        const meta = ok ? `${Math.round(r.width)} × ${Math.round(r.height)} mm · ${r.n_holes} opening${r.n_holes === 1 ? '' : 's'}` : `${e.picks.length} pick${e.picks.length === 1 ? '' : 's'}`;
        const abuts = abutmentsFor(e).map(ab => ab.source === 'manual'
            ? `<label><input type="checkbox" checked disabled> Manual level @ ${Math.round(ab.v)} <button class="mini" onclick="event.stopPropagation(); removeManualLevel(${i}, ${ab.k})">×</button></label>`
            : `<label onclick="event.stopPropagation()"><input type="checkbox" ${ab.enabled ? 'checked' : ''} onchange="toggleAbutment(${i}, '${abutKey(ab)}', this.checked)"> ${ab.name || ab.source} (${ab.source}) @ ${Math.round(ab.v)}${ab.pitched ? ' ⚠ pitched' : ''}</label>`).join('');
        const warn = r && r.warnings && r.warnings.length ? `<div class="elev-warn">${r.warnings.join(' · ')}</div>` : '';
        return `<div class="elev-card ${i === state.active ? 'active' : ''}" onclick="setActive(${i})">
            <div class="elev-head"><span class="elev-swatch" style="background:#${e.color.toString(16).padStart(6, '0')}"></span>
                <input class="elev-name" value="${e.name}" onclick="event.stopPropagation()" onchange="renameElevation(${i}, this.value)">
                <span class="elev-meta">${meta}${e.storey ? ' · ' + e.storey.name : ''}</span></div>
            ${ok ? `<div class="elev-abut">Abutments (splash zone above each):${abuts}
                <div class="row" style="margin-top:4px"><input type="number" id="manual-level-${i}" placeholder="Level mm above base" onclick="event.stopPropagation()">
                <button class="mini" onclick="event.stopPropagation(); addManualLevel(${i})">Add level</button></div></div>` : ''}${warn}</div>`;
    }).join('');
}

// ─── PICKING → EXTRACTION ───
function setPickMode(on) {
    state.pickMode = on;
    document.querySelectorAll('#pick-toggle .turn-btn').forEach(b => b.classList.toggle('active', (b.dataset.value === 'pick') === on));
}

function onViewportClick(event) {
    if (event.target !== renderer.domElement || !state.pickMode || !allMeshes.length) return;
    if (state._dragged) return;
    const hit = pickAt(event);
    if (!hit) return;
    const faces = coplanarFaces(hit.mesh, hit.faceIndex);
    if (!faces) { setStatus('That face is not vertical — pick a wall face', 'busy'); return; }
    if (state.active < 0) newElevation();
    const e = state.elevations[state.active];
    const existing = e.picks.findIndex(p => p.mesh === hit.mesh && p.faces.includes(hit.faceIndex));
    if (existing >= 0) e.picks.splice(existing, 1);
    else e.picks.push({ mesh: hit.mesh, faces, normal: toIfc(hit.normal) });
    e.highlights.forEach(h => { highlightGroup.remove(h); h.geometry.dispose(); });
    e.highlights = e.picks.map(p => highlightFaces(p.mesh, p.faces, e.color));
    if (!e.storey) { const meta = meshMeta[hit.mesh.userData.index]; e.storey = meta && meta.storey ? meta.storey : null; }
    runExtraction(e);
}

async function runExtraction(e) {
    if (!e.picks.length) { e.result = null; renderElevationList(); updatePreview(); return; }
    if (!pyReady) { setStatus('Engine still loading…', 'busy'); return; }
    setStatus('Extracting ' + e.name + '…', 'busy');
    const tris = [];
    for (const p of e.picks) tris.push(...faceTriangles(p.mesh, p.faces));
    const payload = { name: e.name, faces: tris, outward: e.picks[0].normal,
                      context: contextFor(new Set(e.picks.map(p => p.mesh)), tris, 300),
                      options: { penetrations: document.getElementById('penetrations').checked } };
    try {
        pyodide.globals.set('_payload_json', JSON.stringify(payload));
        const out = await pyodide.runPythonAsync(`
import json as _json
from fabric_extract import extract_elevation as _ex
_json.dumps(_ex(_json.loads(_payload_json)))`);
        e.result = JSON.parse(out);
        setStatus(e.result.ok ? 'Ready' : (e.result.warnings.join('; ') || 'Extraction failed'), e.result.ok ? 'ready' : 'busy');
        if (e.result.ok) frameElevation(e.result);
    } catch (err) {
        console.error(err); e.result = null; setStatus('Extraction error: ' + err.message, 'busy');
    }
    renderElevationList();
    updateSliderRange();
    updatePreview();
}

// ─── PARAMETERS ───
const NUM = ['sheathing_t', 'insulation_t', 'batten_w', 'batten_d', 'batten_centres', 'cb_w', 'cb_d', 'splash',
             'panel_t', 'panel_w', 'panel_h', 'panel_gap', 'plank_w', 'plank_t', 'plank_lap', 'plank_gap', 'plank_len'];
function val(id) { return document.getElementById(id).value; }
function toggleValue(id) { const b = document.querySelector('#' + id + ' .turn-btn.active'); return b ? b.dataset.value : null; }
function selectToggle(id, value) {
    document.querySelectorAll('#' + id + ' .turn-btn').forEach(b => b.classList.toggle('active', b.dataset.value === value));
}

function elevationRecords() {
    return state.elevations.filter(e => e.result && e.result.ok).map(e =>
        Object.assign({}, e.result, { name: e.name, offset: e.offset, abutments: abutmentsFor(e), storey: e.storey }));
}

function getParams() {
    const p = { elevations: elevationRecords(), context: modelContext || {}, trim: !state.sliderDragging && document.getElementById('live-trim').checked };
    for (const k of NUM) p[k] = parseFloat(val(k));
    p.sheathing = document.getElementById('sheathing').checked;
    p.insulation = document.getElementById('insulation').checked;
    p.cladding_type = toggleValue('cladding-type');
    p.plank_orient = toggleValue('plank-orient');
    p.counter_batten = val('counter_batten');
    return p;
}

function onTypeChange() {
    const panel = toggleValue('cladding-type') === 'panel';
    document.getElementById('panel-section').style.display = panel ? '' : 'none';
    document.getElementById('plank-section').style.display = panel ? 'none' : '';
    document.getElementById('batten_centres').readOnly = panel;
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
    if (e) { e.offset = Math.max(-half, Math.min(half, e.offset)); s.value = e.offset; }
    document.getElementById('offset-val').textContent = (e ? e.offset : 0) + ' mm';
}

function onSlider(value) {
    const e = state.elevations[state.active];
    if (!e) return;
    e.offset = parseFloat(value);
    document.getElementById('offset-val').textContent = e.offset + ' mm';
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
        renderDimensions(result.dimensions, byName);
        renderChecks(result.checks);
        renderInfo(result.info);
    } catch (err) { console.error('preview failed', err); setStatus('Preview error: ' + err.message, 'busy'); }
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

async function loadModel(file) {
    const info = document.getElementById('model-info');
    try {
        state.elevations.forEach(e => e.highlights.forEach(h => highlightGroup.remove(h)));
        state.elevations = []; state.active = -1; renderElevationList();
        const summary = await loadIFC(file, t => setStatus(t, 'busy'));
        state.model = summary;
        info.innerHTML = `<b>${file.name}</b><br>${summary.meshes} elements · ${summary.storeys} storeys · ${summary.context.project || 'unnamed project'}`;
        setStatus(pyReady ? 'Ready — click a wall face' : 'Model loaded, engine still loading…', pyReady ? 'ready' : 'busy');
        newElevation();
    } catch (err) { console.error(err); info.textContent = 'Load failed: ' + err.message; setStatus('Load failed', 'busy'); }
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
    if (!params.elevations.length) { alert('Extract at least one elevation first.'); return; }
    const btn = document.getElementById('ifc-btn');
    busy(btn, true, 'Generating IFC4X3…');
    try {
        let blob = null;
        try {   // served by Flask: fast server-side export
            const r = await fetch('api/download', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(params) });
            if (r.ok && (r.headers.get('content-type') || '').indexOf('json') < 0) blob = await r.blob();
        } catch (e) { /* static hosting — fall back to the WASM wheel */ }
        if (!blob) {
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
            blob = new Blob([proxy.toJs()], { type: 'application/x-step' });
            if (proxy.destroy) proxy.destroy();
        }
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
await micropip.install("https://ifcopenshell.github.io/wasm-wheels/ifcopenshell-0.8.2+d50e806-cp312-cp312-emscripten_3_1_58_wasm32.whl")
import ifc_generator`);
    ifcReady = true;
}

async function downloadDXF() {
    if (!pyReady) { alert('The engine is still loading.'); return; }
    const params = getParams(); params.trim = true;
    if (!params.elevations.length) { alert('Extract at least one elevation first.'); return; }
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

// ─── PYODIDE ───
async function initPyodide() {
    try {
        setStatus('Loading Python runtime…', 'busy');
        pyodide = await loadPyodide();
        window.pyodide = pyodide;
        setStatus('Loading Shapely…', 'busy');
        await pyodide.loadPackage(['shapely', 'micropip']);
        const modules = ['cladding_constants', 'cladding_primitives', 'cladding_geometry', 'cladding_booleans',
                         'cladding_preview', 'fabric_extract', 'dxf_generator', 'ifc_generator'];
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
        setStatus(allMeshes.length ? 'Ready — click a wall face' : 'Ready — load an IFC', 'ready');
        for (const e of state.elevations) if (e.picks.length && !e.result) runExtraction(e);
        updatePreview();
    } catch (err) { console.error(err); setStatus('Engine failed to load: ' + err.message, 'busy'); }
}

// ─── INIT ───
function initApp() {
    initThree();
    initWebIfc();
    initPyodide();
    renderElevationList();
    onTypeChange();
    const vp = document.getElementById('viewport');
    let down = null;
    vp.addEventListener('pointerdown', e => { down = [e.clientX, e.clientY]; state._dragged = false; });
    vp.addEventListener('pointermove', e => { if (down && Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 4) state._dragged = true; });
    vp.addEventListener('click', onViewportClick);
    const drop = document.getElementById('file-drop');
    drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('over'); });
    drop.addEventListener('dragleave', () => drop.classList.remove('over'));
    drop.addEventListener('drop', e => { e.preventDefault(); drop.classList.remove('over'); if (e.dataTransfer.files.length) loadModel(e.dataTransfer.files[0]); });
    const slider = document.getElementById('offset');
    slider.addEventListener('pointerdown', () => { state.sliderDragging = true; });
    const release = () => { if (state.sliderDragging) { state.sliderDragging = false; updatePreview(); } };
    slider.addEventListener('pointerup', release);
    slider.addEventListener('change', release);
    document.getElementById('download-reminder').addEventListener('click', e => { if (e.target.id === 'download-reminder') e.currentTarget.classList.remove('open'); });
}
