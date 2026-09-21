/* CladForge chain wizard.
   Picking grows elevations and chains but generates nothing. A chain is built only
   when asked: the button top right of the viewport, or Enter, opens this wizard,
   which collects the cladding choices and switches the preview on for that chain. */

const WIZ = { step: 0, draft: null };

// The dimensions each cladding type needs, mirroring the inputs in the side panel.
const WIZ_DIMS = {
    plank: [['plank_w', 'Face width', '75–250 mm'], ['plank_t', 'Thickness', '12–32 mm'],
            ['plank_lap', 'Lap', '0 = open joint'], ['plank_gap', 'Joint gap', '0–15 mm'],
            ['plank_len', 'Max length', '1800–6000 mm']],
    panel: [['panel_t', 'Thickness', '6–20 mm'], ['panel_w', 'Max width', '600–1500 mm'],
            ['panel_h', 'Max height', '1200–3000 mm'], ['panel_gap', 'Joint gap', '0–15 mm']],
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
// Three steps for planks, two for panels: the orientation question only applies to
// boards that course, so panels skip it.
function wizStepIds() { return WIZ.draft.type === 'plank' ? ['type', 'orient', 'dims'] : ['type', 'dims']; }

function openWizard() {
    if (!readyChains().length) return;
    WIZ.draft = { type: toggleValue('cladding-type') || 'plank',
                  orient: toggleValue('plank-orient') || 'horizontal', dims: {} };
    for (const type of Object.keys(WIZ_DIMS)) for (const [id] of WIZ_DIMS[type]) WIZ.draft.dims[id] = val(id);
    WIZ.step = 0;
    document.getElementById('chain-wizard').classList.add('open');
    renderWizard();
}

function closeWizard() {
    document.getElementById('chain-wizard').classList.remove('open');
    WIZ.draft = null;
}

function wizTitle(id) {
    if (id === 'type') return 'Plank or panel?';
    if (id === 'orient') return 'Horizontal or vertical?';
    return WIZ.draft.type === 'panel' ? 'Panel dimensions' : 'Plank dimensions';
}

function wizChoice(key, options) {
    return options.map(([value, label, note]) =>
        `<button class="wiz-option ${WIZ.draft[key] === value ? 'active' : ''}" onclick="wizPick('${key}', '${value}')">
            <b>${label}</b><span>${note}</span></button>`).join('');
}

function wizBody(id) {
    if (id === 'type') return wizChoice('type', [
        ['plank', 'Plank', 'Boards coursed across the face, lapped or open-jointed'],
        ['panel', 'Panel', 'Sheet panels on open joints, set out from the openings']]);
    if (id === 'orient') return wizChoice('orient', [
        ['horizontal', 'Horizontal', 'Planks run along the elevation on vertical battens'],
        ['vertical', 'Vertical', 'Planks run up the elevation on horizontal battens over counter-battens']]);
    return '<div class="wiz-dims">' + WIZ_DIMS[WIZ.draft.type].map(([fid, label, unit]) => {
        const src = document.getElementById(fid);
        return `<div class="field"><label>${label} <span class="unit">${unit}</span></label>
            <input type="number" id="wiz-${fid}" value="${WIZ.draft.dims[fid]}" min="${src.min}" max="${src.max}"
                   step="${src.step || 1}" oninput="WIZ.draft.dims['${fid}'] = this.value"></div>`;
    }).join('') + '</div><p class="hint">Everything else — battens, splash zone, corners, openings — keeps its'
        + ' current setting and stays editable in the panel once the chain is built.</p>';
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
    const d = WIZ.draft, built = pendingChains();
    selectToggle('cladding-type', d.type);
    selectToggle('plank-orient', d.orient);
    for (const [id] of WIZ_DIMS[d.type]) document.getElementById(id).value = d.dims[id];
    built.forEach(c => c.built = true);
    closeWizard();
    onTypeChange();   // syncs the panel sections and the offset slider, then previews
    renderElevationList();
    setStatus(built.length ? 'Built ' + built.map(c => c.name).join(', ') : 'Rebuilt', 'ready');
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
