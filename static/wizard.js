/* CladForge chain wizard.
   Picking grows elevations and chains but generates nothing. A chain is built only
   when asked: the button top right of the viewport, or Enter, opens this wizard,
   which collects the cladding choices and switches the preview on for that chain.
   Every answer is written into the side panel's own control, so the panel keeps
   editing the chain live afterwards and the wizard starts from whatever it holds. */

const WIZ = { step: 0, draft: null };

// Number fields per step, mirroring the inputs in the side panel: [id, label, unit].
const WIZ_GROUPS = {
    plank: [['plank_w', 'Face width', '75–250 mm'], ['plank_t', 'Thickness', '12–32 mm'],
            ['plank_lap', 'Lap', '0 = open joint'], ['plank_gap', 'Joint gap', '0–15 mm'],
            ['plank_len', 'Max length', '1800–6000 mm']],
    panel: [['panel_t', 'Thickness', '6–20 mm'], ['panel_w', 'Max width', '600–1500 mm'],
            ['panel_h', 'Max height', '1200–3000 mm'], ['panel_gap', 'Joint gap', '0–15 mm']],
    battens: [['batten_centres', 'Max centres', '300–600 mm'], ['batten_w', 'Width', '25–100 mm'],
              ['batten_d', 'Depth', '19–100 mm']],
    cbattens: [['cb_centres', 'Max centres', '300–900 mm'], ['cb_w', 'Width', '25–100 mm'],
               ['cb_d', 'Depth', '19–100 mm']],
};

function chainFaces(c) { return c.members.filter(m => m.result && m.result.ok); }
function readyChains() { return state.chains.filter(c => chainFaces(c).length); }
function pendingChains() { return readyChains().filter(c => !c.built); }

function chainSummary(c) {
    const faces = chainFaces(c);
    return faces.length > 1 ? `${c.name} (${faces.map(m => m.name.replace('Elevation ', '')).join(' → ')})` : faces[0].name;
}

function updateMakeChain() {
    const btn = document.getElementById('make-chain');
    if (!btn) return;
    const n = pendingChains().reduce((a, c) => a + chainFaces(c).length, 0);
    btn.style.display = n ? '' : 'none';
    btn.textContent = (n > 1 ? `Make chain · ${n} faces` : 'Build cladding · 1 face') + '  ⏎';
}

// ─── THE WIZARD ───
// Steps drop out when they do not apply: panels never course, so they skip the
// orientation question, and a buildup with no counter-battens skips their step.
function wizNeedsCb() {
    const forced = document.getElementById('counter_batten').value;
    if (forced !== 'auto') return forced === 'yes';
    return WIZ.draft.type === 'plank' && WIZ.draft.orient === 'vertical';
}

function wizStepIds() {
    const ids = ['type'];
    if (WIZ.draft.type === 'plank') ids.push('orient');
    ids.push('dims', 'battens');
    if (wizNeedsCb()) ids.push('cbattens');
    ids.push('base');
    return ids;
}

function openWizard() {
    if (!readyChains().length) return;
    WIZ.draft = { type: toggleValue('cladding-type') || 'plank',
                  orient: toggleValue('plank-orient') || 'horizontal', base: 'foot', dims: {} };
    for (const group of Object.keys(WIZ_GROUPS)) for (const [id] of WIZ_GROUPS[group]) WIZ.draft.dims[id] = val(id);
    WIZ.step = 0;
    document.getElementById('chain-wizard').classList.add('open');
    renderWizard();
}

function closeWizard() {
    document.getElementById('chain-wizard').classList.remove('open');
    WIZ.draft = null;
}

function wizBattenRun() { return WIZ.draft.type === 'plank' && WIZ.draft.orient === 'vertical' ? 'Horizontal' : 'Vertical'; }

function wizTitle(id) {
    if (id === 'type') return 'Plank or panel?';
    if (id === 'orient') return 'Horizontal or vertical?';
    if (id === 'battens') return wizBattenRun() + ' battens';
    if (id === 'cbattens') return 'Counter-battens';
    if (id === 'base') return 'Where does the cladding start?';
    return WIZ.draft.type === 'panel' ? 'Panel dimensions' : 'Plank dimensions';
}

function wizNote(id) {
    if (id === 'battens') {
        return WIZ.draft.type === 'panel'
            ? 'Centres are derived from the panel bay so every joint lands on a batten, with intermediate battens keeping the span under 600 mm.'
            : `${wizBattenRun()} battens carry the boards. Centres are a maximum: the run is divided evenly to reach it.`;
    }
    if (id === 'cbattens') return 'Counter-battens run behind the battens so the cavity still drains where the battens cross the flow.';
    if (id === 'base') return 'This is only about the foot of the wall. Every slab or roof the extractor found meeting the face keeps its splash zone either way.';
    return 'Everything else — insulation, splash zone, corners, openings — keeps its current setting and stays editable in the panel once the chain is built.';
}

function wizChoice(key, options) {
    return options.map(([value, label, note]) =>
        `<button class="wiz-option ${WIZ.draft[key] === value ? 'active' : ''}" onclick="wizPick('${key}', '${value}')">
            <b>${label}</b><span>${note}</span></button>`).join('');
}

// batten_centres is derived from the panel bay, so it is shown but not editable.
function wizDerived(fid) { return fid === 'batten_centres' && WIZ.draft.type === 'panel'; }

function wizFields(group) {
    return '<div class="wiz-dims">' + WIZ_GROUPS[group].map(([fid, label, unit]) => {
        const src = document.getElementById(fid), off = wizDerived(fid);
        return `<div class="field${off ? ' wiz-off' : ''}"><label>${label} <span class="unit">${off ? 'derived' : unit}</span></label>
            <input type="number" id="wiz-${fid}" value="${off ? src.value : WIZ.draft.dims[fid]}" min="${src.min}" max="${src.max}"
                   step="${src.step || 1}" ${off ? 'disabled' : ''} oninput="WIZ.draft.dims['${fid}'] = this.value"></div>`;
    }).join('') + '</div>';
}

function wizBody(id) {
    if (id === 'type') return wizChoice('type', [
        ['plank', 'Plank', 'Boards coursed across the face, lapped or open-jointed'],
        ['panel', 'Panel', 'Sheet panels on open joints, set out from the openings']]);
    if (id === 'orient') return wizChoice('orient', [
        ['horizontal', 'Horizontal', 'Planks run along the elevation on vertical battens'],
        ['vertical', 'Vertical', 'Planks run up the elevation on horizontal battens over counter-battens']]);
    if (id === 'base') return wizChoice('base', [
        ['foot', 'At the foot of the wall', 'Nothing meets the wall here, so the boards run all the way down'],
        ['splash', 'Above a splash zone', `A ${val('splash')} mm band is left at the base, as at any other abutment`]])
        + `<p class="hint">${wizNote('base')}</p>`;
    return wizFields(id === 'dims' ? WIZ.draft.type : id) + `<p class="hint">${wizNote(id)}</p>`;
}

function renderWizard() {
    const ids = wizStepIds(), id = ids[WIZ.step];
    const chains = pendingChains().length ? pendingChains() : readyChains();
    document.getElementById('wiz-title').textContent = wizTitle(id);
    document.getElementById('wiz-step').textContent = `Step ${WIZ.step + 1} of ${ids.length}`;
    document.getElementById('wiz-chain').textContent = 'Building ' + chains.map(chainSummary).join(' · ');
    document.getElementById('wiz-body').innerHTML = wizBody(id);
    document.getElementById('wiz-back').style.visibility = WIZ.step ? '' : 'hidden';
    document.getElementById('wiz-next').textContent = WIZ.step === ids.length - 1 ? 'Build' : 'Next';
}

function wizPick(key, value) { WIZ.draft[key] = value; wizNext(); }
function wizBack() { if (WIZ.step) { WIZ.step--; renderWizard(); } }

function wizNext() {
    if (WIZ.step >= wizStepIds().length - 1) return wizBuild();
    WIZ.step++;
    renderWizard();
}

function wizBuild() {
    const d = WIZ.draft, built = pendingChains(), steps = wizStepIds();
    selectToggle('cladding-type', d.type);
    selectToggle('plank-orient', d.orient);
    for (const group of [d.type, 'battens'].concat(steps.indexOf('cbattens') >= 0 ? ['cbattens'] : [])) {
        for (const [fid] of WIZ_GROUPS[group]) if (!wizDerived(fid)) document.getElementById(fid).value = d.dims[fid];
    }
    built.forEach(c => c.built = true);
    // The synthetic "Elevation base" line is the only splash zone this answers for;
    // a detected slab or roof keeps its own either way.
    for (const chain of (built.length ? built : readyChains())) {
        for (const m of chainFaces(chain)) m.disabled['base|0|0'] = d.base !== 'splash';
    }
    closeWizard();
    onTypeChange();   // syncs the panel sections and the offset slider, then previews
    renderElevationList();
    setStatus(built.length ? 'Built ' + built.map(c => c.name).join(', ') : 'Rebuilt', 'ready');
    startLevelPick(built.length ? built : readyChains());
}

// ─── TOP AND BOTTOM OF THE CLADDING ───
// Two clicks after the build, one per level. Only the height of each point is used,
// and the pair applies to the whole chain: every face is clamped to its own extent,
// so a lower wing in the same run never gets cladding floating above it.
function startLevelPick(chains) {
    const live = (chains || []).filter(c => chainFaces(c).length);
    if (!live.length) return;
    state.levels = { chains: live, step: 0, top: null, bottom: null };
    renderLevelPick();
}

function renderLevelPick() {
    const L = state.levels, box = document.getElementById('level-picker');
    if (!L) { box.style.display = 'none'; return; }
    box.style.display = '';
    document.getElementById('level-title').textContent = L.step === 0
        ? 'Set the height of the top of the cladding' : 'Set the height of the cladding baserail';
    document.getElementById('level-note').textContent =
        'Click any point in the model — only its height is used. A red dot sits on a surface; it turns green when it snaps to a corner. '
        + (L.step === 0 ? 'Dismiss to keep the top as it is.'
                        : (L.top === null ? 'Dismiss to keep the cladding as it is.'
                                          : `Top set at ${Math.round(L.top)}. Dismiss to keep the base as it is.`));
    document.getElementById('level-step').textContent = `${L.step + 1} of 2`;
}

function levelPicked(z) {
    const L = state.levels;
    if (!L) return;
    if (L.step === 0) { L.top = z; L.step = 1; renderLevelPick(); return; }
    L.bottom = z;
    finishLevelPick();
}

// Dismiss closes the picker wherever it is, keeping whatever has not been set. A top
// clicked before dismissing still applies; nothing clicked leaves the chain untouched.
function dismissLevels() {
    if (state.levels) finishLevelPick();
}

function finishLevelPick() {
    const L = state.levels;
    state.levels = null;
    showSnap(null);
    document.getElementById('level-picker').style.display = 'none';
    if (L.top !== null && L.bottom !== null) {   // clicked the wrong way round: still a band
        const hi = Math.max(L.top, L.bottom), lo = Math.min(L.top, L.bottom);
        L.chains.forEach(c => { c.topZ = hi; c.bottomZ = lo; });
    } else {
        L.chains.forEach(c => {
            if (L.top !== null) c.topZ = L.top;
            if (L.bottom !== null) c.bottomZ = L.bottom;
        });
    }
    const set = [L.top !== null ? 'top ' + Math.round(L.top) : '', L.bottom !== null ? 'bottom ' + Math.round(L.bottom) : ''].filter(Boolean);
    setStatus(set.length ? 'Cladding ' + set.join(', ') : 'Cladding runs the full face', 'ready');
    renderElevationList();
    updatePreview();
}

function initWizard() {
    document.addEventListener('keydown', e => {
        const open = document.getElementById('chain-wizard').classList.contains('open');
        if (e.key === 'Escape' && open) return closeWizard();
        if (e.key !== 'Enter') return;
        const el = document.activeElement, typing = el && /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName);
        if (open) { if (!typing || el.id.indexOf('wiz-') === 0) { e.preventDefault(); wizNext(); } return; }
        if (typing) return;   // Enter in a panel field belongs to the field
        e.preventDefault();
        openWizard();
    });
    document.getElementById('chain-wizard').addEventListener('click', e => { if (e.target.id === 'chain-wizard') closeWizard(); });
    updateMakeChain();
}

// ─── COURSE HEIGHT ───
// Clicking a course dimension types a new one over it. The scope is asked before it
// applies, because a chain is set out as one run: changing one member's coursing
// breaks the joints that carry round its corners.
const COURSE = { dim: null };

function openCourseDialog(dim) {
    if (!dim) return;
    const e = state.elevations.find(m => m.result && m.result.name === dim.elevation);
    if (!e) return;
    COURSE.dim = dim;
    COURSE.elev = e;
    const panel = dim.kind === 'row';
    document.getElementById('course-title').textContent = panel ? `Panel row ${dim.row + 1} height` : 'Course height';
    document.getElementById('course-where').textContent =
        `${chainLabel(e)} — currently ${Math.round(dim.value)} mm`;
    const input = document.getElementById('course-value');
    input.value = Math.round(dim.value);
    input.min = panel ? 150 : 50;
    input.max = panel ? 3000 : 1000;
    const single = e.chain.members.length < 2;
    document.getElementById('course-chain').style.display = single ? 'none' : '';
    document.getElementById('course-one').textContent = single ? 'Apply' : 'This elevation only';
    document.getElementById('course-reset').style.display = (e.cover || (e.panelRows || []).length) ? '' : 'none';
    document.getElementById('course-dialog').classList.add('open');
    input.focus();
    input.select();
}

// The row list for elevation *e* with row *j* set to *h*. Rows under j that were not
// listed are pinned at the heights they are drawn at now, so editing row 3 keeps rows 1
// and 2 where they are; rows above j are left to carry on at the panel height.
function rowsDrawn(e) {
    const info = ((window._lastPreview || {}).info || []).find(i => i.elevation === e.result.name);
    return (info && info.rows) || [];
}

function withRow(e, j, h) {
    const rows = (e.panelRows || []).slice(), drawn = rowsDrawn(e);
    const fallback = parseFloat(document.getElementById('panel_h').value) || 2400;
    for (let k = rows.length; k < j; k++) rows.push(drawn[k] || fallback);
    rows[j] = h;
    return rows;
}

function closeCourseDialog() {
    document.getElementById('course-dialog').classList.remove('open');
    COURSE.dim = null;
}

function applyCourse(scope) {
    const v = parseFloat(document.getElementById('course-value').value);
    const e = COURSE.elev, dim = COURSE.dim;
    if (!e || !dim || !isFinite(v) || v <= 0) return closeCourseDialog();
    const targets = scope === 'chain' ? e.chain.members : [e];
    if (dim.kind === 'row') {
        const rows = withRow(e, dim.row, v);
        targets.forEach(m => { m.panelRows = rows.slice(); });
    } else targets.forEach(m => { m.cover = v; });
    closeCourseDialog();
    setStatus(`${dim.kind === 'row' ? 'Row ' + (dim.row + 1) : 'Course'} height ${Math.round(v)} mm on ${scope === 'chain' ? e.chain.name : e.name}`, 'ready');
    renderElevationList();
    updatePreview();
}

function resetCourse() {
    const e = COURSE.elev;
    if (e) e.chain.members.forEach(m => { m.cover = null; m.panelRows = null; });
    closeCourseDialog();
    setStatus('Course height back to the panel setting', 'ready');
    renderElevationList();
    updatePreview();
}
