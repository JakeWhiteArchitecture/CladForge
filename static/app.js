/* CladForge app — elevation state, Pyodide engine, live preview, downloads. */

const state = { elevations: [], chains: [], active: -1, pickMode: true, mode: 'pick', sliderDragging: false,
               model: null, seq: 0, editing: null, levels: null };
let pyodide = null, pyReady = false, ifcReady = false, _seq = 0, _numTimer = null, _restarting = false;

// Edge settings, per chain: offsets (mm) pulling the buildup back from its top, bottom and
// free-end edges, the gap in every mitre, and how a window or door head is ventilated.
// Standing seam ends, per chain too: folded at 45° or not at all, and if so the top ends (a
// seam running out upward: the head, under a sill) and bottom ends (the base, over a head) each
// folded or flat.
const ABUTS = ['auto', 'g3', 'below', 'mitre', 'none'];   // a roof run up to a wall that stands on above it: by what that wall is clad in, the G3 flashing, the trays turned up under its cladding, or nothing
const HEADS = ['auto', 'g3', 'none'];       // a monopitch's head verge: over a picked wall, on every level top edge, or not at all
const EDGE_DEFAULTS = { top: 10, side: 0, bottom: 10, gap: 10, vent: 'front', air: 10, pgap: 1, frame: 50,
                        fold: 'on', seamTop: 'folded', seamBottom: 'folded', verge: 'both', vergeType: 'auto', vergeLast: 'vmz', eaves: 'on', head: 'auto', windows: 'on', abutment: 'auto', capEnds: 'edge', ridgeEnd: 'square', ridgeAngle: 90 };

// ─── ELEVATIONS AND CHAINS ───
// An elevation is one coplanar region. A chain is an ordered run of elevations that
// meet at corners; coursing is set out along the whole run so joints carry round.
// A chain generates nothing until it is built: picking is a selection, the wizard
// turns it into cladding. See static/wizard.js.
function newChain() {
    const chain = { name: 'Chain ' + (++state.seq), members: [], length: 0, built: false,
                    topZ: null, bottomZ: null, edges: Object.assign({}, EDGE_DEFAULTS),
                    settings: typeof formSettings === 'function' ? formSettings() : null };
    state.chains.push(chain);
    return chain;
}

function newElevation(chain) {
    const n = state.elevations.length;
    const label = k => 'Elevation ' + String.fromCharCode(65 + (k % 26)) + (k >= 26 ? Math.floor(k / 26) : '');
    let k = n;           // the next free name: a loaded state or a deletion can leave gaps
    while (state.elevations.some(m => m.name === label(k))) k++;
    const name = label(k);
    const e = { name, picks: [], result: null, manual: [], disabled: {},
                storey: null, highlights: [], chain: chain || newChain(), start: 0, rev: false, link: null,
                offset: 0,
                cornerLo: 0, cornerHi: 0, masterLo: false, masterHi: false, detailLo: null, detailHi: null,
                clipLo: 0, clipHi: null, openingDetails: {} };
    e.chain.members.push(e);
    state.elevations.push(e);
    setActive(n);
    return e;
}

// A face's standing seams can follow another face's (e.seamFrom): a wall over a roof, a roof
// under a wall. Set from the Edges panel's list, or with the pipette: press it, then click
// the face to take the seams from.
function seamSources(e) {
    return state.elevations.filter(m => m !== e && m.result && m.result.ok && m.chain.built);
}
function linkSeams(e, from) {
    if (!e) return;
    e.seamFrom = from && from !== e ? from : null;
    if (e.seamFrom && e.seamFrom.seamFrom === e) e.seamFrom.seamFrom = null;      // one follows the other, not each the other
    renderElevationList();
    syncEdgeFields();
    setStatus(e.seamFrom ? `${e.name}: its seams follow ${e.seamFrom.name}'s` : `${e.name}: its seams are set out on their own`, 'ready');
    updatePreview();
}
function setSeamFrom(name) {
    linkSeams(state.elevations[state.active], state.elevations.find(m => m.name === name) || null);
}
function startPipette() {
    const e = state.elevations[state.active];
    if (state.pipette || !e) { endPipette(); return; }
    state.pipette = e;
    document.getElementById('seam-pipette').classList.add('active');
    setStatus(`Pipette: click the clad face whose seams ${e.name} should follow · Esc to cancel`, 'busy');
}
function endPipette() {
    state.pipette = null;
    document.getElementById('seam-pipette').classList.remove('active');
}
function pipetteClick(event) {
    const target = state.pipette, hit = pickAt(event);
    const owner = hit && state.elevations.find(m => m.result && m.result.ok && samePlane(hit, m));
    if (!owner || owner === target || !state.elevations.includes(target)) {
        setStatus(`Pipette: click a face that is already clad, other than ${target.name} · Esc to cancel`, 'busy');
        return;
    }
    endPipette();
    linkSeams(target, owner);
}

function deleteElevation() {
    if (state.active < 0) return;
    closeEditWidget();
    const e = state.elevations.splice(state.active, 1)[0];
    e.highlights.forEach(h => highlightGroup.remove(h));
    for (const m of state.elevations) if (m.valleys) m.valleys = m.valleys.filter(v => v.partner !== e);
    for (const m of state.elevations) if (m.seamFrom === e) m.seamFrom = null;
    if (state.pipette === e) endPipette();
    if (e.chain.datumFrom === e) e.chain.datumFrom = null;     // back to the lowest start
    e.chain.members = e.chain.members.filter(m => m !== e);
    if (!e.chain.members.length) state.chains = state.chains.filter(c => c !== e.chain);
    else relinkChain(e.chain);
    setActive(Math.min(state.active, state.elevations.length - 1));
    showHeals();         // any offer to heal it goes with it
}

// The chain's course datum locks to the elevation where its first custom vertical input
// is made — a typed row or course height, a split or a merge — and later edits on other
// faces leave it there. Until then the engine falls back to the lowest start in the chain.
function lockDatum(e) {
    if (!e || !e.chain || e.chain.datumFrom) return false;
    e.chain.datumFrom = e;
    return true;
}

function chainLabel(e) { return e.chain.members.length > 1 ? e.chain.name + ' · ' + e.name : e.name; }

// Which sides of a roof slope's chain take a verge strip (cladding_verge.py): the start of
// the run is the left of the chain's first face, as you look at it.
const VERGES = ['both', 'start', 'end', 'none'];
// What a verge is built as: the G3 strip the last tray is welted over, or the flashing down
// over a clad wall's head from a seam at the roof's edge (VMZ-SN-MA-ZI-05).
const VERGE_TYPES = ['auto', 'g3', 'cladding'];   // auto: the flashing over cladding where a picked wall stands under the verge, else the G3 strip
const RIDGE_ENDS = ['square', 'v', 'flat'];                 // a ridge cap's stop end over a verge: the cap's section, or a skirt down over the apex to a point or a short level edge

// The panel's edge fields show the active elevation's chain and write to all of it.
function syncEdgeFields() {
    const e = state.elevations[state.active], ed = e ? e.chain.edges : EDGE_DEFAULTS;
    for (const k of ['top', 'side', 'bottom', 'gap', 'air', 'pgap', 'frame']) {
        const el = document.getElementById('edge-' + k);
        if (el && document.activeElement !== el) el.value = ed[k];
    }
    selectToggle('head-vent', ed.vent);
    selectToggle('seam-fold', ed.fold === 'off' ? 'off' : 'on');
    for (const k of ['seamTop', 'seamBottom']) {
        const el = document.getElementById('edge-' + k);
        el.value = ed[k] === 'flat' ? 'flat' : 'folded';
        el.disabled = ed.fold === 'off';
    }
    document.getElementById('edge-verge').value = VERGES.includes(ed.verge) ? ed.verge : 'both';
    document.getElementById('edge-vergeType').value = VERGE_TYPES.includes(ed.vergeType) ? ed.vergeType : 'auto';
    document.getElementById('edge-vergeLast').value = ed.vergeLast === 'full' ? 'full' : 'vmz';
    document.getElementById('edge-vergeLast').disabled = ed.vergeType === 'cladding';
    const over = document.getElementById('edge-vergeOver');           // blank: taken from the cladding on the wall below
    if (document.activeElement !== over) over.value = ed.vergeOver == null ? '' : ed.vergeOver;
    over.disabled = ed.vergeType === 'g3';
    document.getElementById('edge-eaves').value = ed.eaves === 'none' ? 'none' : 'on';
    document.getElementById('edge-windows').value = ed.windows === 'none' ? 'none' : 'on';
    document.getElementById('edge-head').value = HEADS.includes(ed.head) ? ed.head : 'auto';
    document.getElementById('edge-abutment').value = ABUTS.includes(ed.abutment) ? ed.abutment : 'auto';
    const from = document.getElementById('edge-seamFrom');
    from.innerHTML = '<option value="">Its own setting-out</option>'
        + (e ? seamSources(e) : []).map(m => `<option value="${m.name}">${m.name}${m.chain.members.length > 1 ? ' (' + m.chain.name + ')' : ''}</option>`).join('');
    from.value = e && e.seamFrom && seamSources(e).includes(e.seamFrom) ? e.seamFrom.name : '';
    document.getElementById('edge-capEnds').value = ed.capEnds === 'square' ? 'square' : 'edge';
    document.getElementById('edge-ridgeEnd').value = RIDGE_ENDS.includes(ed.ridgeEnd) ? ed.ridgeEnd : 'square';
    const angle = document.getElementById('edge-ridgeAngle');
    if (document.activeElement !== angle) angle.value = ed.ridgeAngle == null ? 90 : ed.ridgeAngle;
    angle.disabled = !RIDGE_ENDS.includes(ed.ridgeEnd) || ed.ridgeEnd === 'square';
    document.getElementById('edge-air').disabled = ed.vent !== 'back';
    document.getElementById('edge-chain').textContent = e ? 'for ' + e.chain.name : 'for the active chain';
}

function setEdge(e, key, value) {
    if (!e) return false;
    if (key === 'vent') e.chain.edges.vent = value === 'back' ? 'back' : 'front';
    else if (key === 'fold') e.chain.edges.fold = value === 'off' ? 'off' : 'on';
    else if (key === 'seamTop' || key === 'seamBottom') e.chain.edges[key] = value === 'flat' ? 'flat' : 'folded';
    else if (key === 'verge') e.chain.edges.verge = VERGES.includes(value) ? value : 'both';
    else if (key === 'vergeType') e.chain.edges.vergeType = VERGE_TYPES.includes(value) ? value : 'auto';
    else if (key === 'vergeLast') e.chain.edges.vergeLast = value === 'full' ? 'full' : 'vmz';
    else if (key === 'vergeOver') { const v = parseFloat(value); e.chain.edges.vergeOver = isFinite(v) ? Math.max(0, Math.min(400, v)) : null; }
    else if (key === 'eaves') e.chain.edges.eaves = value === 'none' ? 'none' : 'on';
    else if (key === 'windows') e.chain.edges.windows = value === 'none' ? 'none' : 'on';
    else if (key === 'head') e.chain.edges.head = HEADS.includes(value) ? value : 'auto';
    else if (key === 'abutment') e.chain.edges.abutment = ABUTS.includes(value) ? value : 'auto';
    else if (key === 'capEnds') e.chain.edges.capEnds = value === 'square' ? 'square' : 'edge';
    else if (key === 'ridgeEnd') e.chain.edges.ridgeEnd = RIDGE_ENDS.includes(value) ? value : 'square';
    else if (key === 'ridgeAngle') { const v = parseFloat(value); if (!isFinite(v)) return false; e.chain.edges.ridgeAngle = Math.max(30, Math.min(150, v)); }
    else {
        const v = parseFloat(value);
        if (!isFinite(v)) return false;
        // the frame setback is behind the wall face, and negative where it stands forward
        e.chain.edges[key] = key === 'frame' ? Math.max(-300, Math.min(300, v)) : Math.max(0, Math.min(100, v));
    }
    syncEdgeFields();
    return true;
}

function onEdgeField(key, value) {
    if (setEdge(state.elevations[state.active], key, value)) onNumeric();
}

function setActive(i) {
    state.active = i;
    // Selecting never moves the chain's course datum: that locks on the first row or
    // course edit (lockDatum), so choosing a face with a different base moves nothing.
    const picked = state.elevations[i];
    showActiveChainSettings();         // the sidebar follows the face's chain
    renderSettingsLibrary();           // and the chains it can copy from are the others
    syncEdgeFields();
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
    const meta = ok ? `${Math.round(r.width)} × ${Math.round(r.height)} mm${lieOf(r)} · ${r.n_holes} opening${r.n_holes === 1 ? '' : 's'}` : `${e.picks.length} pick${e.picks.length === 1 ? '' : 's'}`;
    const abuts = abutmentsFor(e).map(ab => ab.source === 'manual'
        ? `<label><input type="checkbox" checked disabled> Manual level @ ${Math.round(ab.v)} <button class="mini" onclick="event.stopPropagation(); removeManualLevel(${i}, ${ab.k})">×</button></label>`
        : `<label onclick="event.stopPropagation()"><input type="checkbox" ${ab.enabled ? 'checked' : ''} onchange="toggleAbutment(${i}, '${abutKey(ab)}', this.checked)"> ${ab.name || ab.source} (${ab.source}) ${ab.pitched ? `pitched ${Math.round(ab.v_min)}–${Math.round(ab.v)}, splash follows the roof` : '@ ' + Math.round(ab.v)}</label>`).join('');
    let warn = r && r.warnings && r.warnings.length ? `<div class="elev-warn">${r.warnings.join(' · ')}</div>` : '';
    if (e.error) warn = `<div class="elev-warn elev-error">✖ ${e.error} <button class="mini" onclick="event.stopPropagation(); runExtraction(state.elevations[${i}])">Retry</button></div>`;
    else if (!ok && e.picks.length) warn = `<div class="elev-warn">${e.pending ? 'Queued — waiting for the engine' : 'Extracting…'}</div>`;
    const clipped = ok && (e.clipLo > 0.5 || (e.clipHi !== null && e.clipHi < r.width - 0.5));
    const corners = [e.cornerLo, e.cornerHi].filter(k => k);
    const place = (e.chain.members.length > 1 && ok ? ` · run ${Math.round(e.start)}–${Math.round(e.start + cladWidth(e))}${e.rev ? ' ↺' : ''}` : '')
        + ROOF_JOINS.map(kind => { const with_ = ok ? valleyRecords(e).filter(v => v.kind === kind).map(v => v.with.replace('Elevation ', '')) : [];
                                           return with_.length ? ` · ${kind === 'rake' ? 'raking corner' : kind === 'eaves' ? (valleyRecords(e).some(v => v.kind === 'eaves' && isPitched(e) && isPitched(state.elevations.find(m => m.name === v.with) || {})) ? 'mansard' : 'eaves (no overhang)') : kind === 'head' ? 'head verge' : kind === 'abut' ? 'head-wall abutment' : kind} with ${with_.join(', ')}` : ''; }).join('')
        + (ok && seamFollows(e) ? ` · seams follow ${seamFollows(e).replace('Elevation ', '')}` : '')
        + (ok && ridgeRecord(e) ? ` · ridge with ${e.ridge.partner.name.replace('Elevation ', '')}${e.ridge.master ? ' (carries the cap)' : ''}` : '')
        + (corners.length ? ` · ${corners.length} corner${corners.length > 1 ? 's' : ''}` : '')
        + (ok && matchedTo(e) ? ` · battens matched to ${(matchedTo(e).chain || matchedTo(e).name)} ${matchedTo(e).side}` : '')
        + (clipped ? ` · clad ${Math.round(e.clipLo)}–${Math.round(e.clipHi === null ? r.width : e.clipHi)}` : '')
        + (e.cover ? ` · course ${Math.round(e.cover)}` : '')
        + (e.panelRows && e.panelRows.length ? ` · rows ${e.panelRows.map(Math.round).join('/')}` : '')
        + (e.panelJoints && e.panelJoints.length ? ` · ${e.panelJoints.length} joint${e.panelJoints.length > 1 ? 's' : ''} dissolved` : '')
        + (e.postStates && Object.keys(e.postStates).length ? ` · ${Object.keys(e.postStates).length} client batten${Object.keys(e.postStates).length > 1 ? 's' : ''} set` : '')
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
        } else if (pending || !/^Chain \d+$/.test(chain.name)) {         // a named chain says its name, one face or many
            html += `<div class="chain-head">${chain.name}${pending}</div>`;
        }
        for (const m of members) html += elevationCard(m, state.elevations.indexOf(m));
    }
    box.innerHTML = html;
    updateMakeChain();
    renderSettingsLibrary();
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
const CORNER_LABEL = { mitre: 'Mitred', lap: 'Master', profile: 'Profile', butt: 'Square' };
// A standing seam chain's corners read differently: "lap" is a seam stood on the corner (the
// face that masters the corner carries it, so Swap moves it to the other face) and "profile"
// a flashing strip over the trays, which meet as if mitred (cladding_geometry._seam_corners).
const SEAM_CORNER_LABEL = { mitre: 'Mitred', lap: 'Seam', profile: 'Flashing strip', butt: 'Square' };
function seamChain() { return toggleValue('cladding-type') !== 'panel' && toggleValue('plank-profile') === 'seam'; }
function cornerLabel(detail) { return (seamChain() ? SEAM_CORNER_LABEL : CORNER_LABEL)[detail]; }
const CORNER_LETTER = { mitre: 'M', lap: 'L', profile: 'P', butt: 'S' };

// Profile is for right-angled external corners only: the aluminium profile with panels,
// the Marley-type corner profile with planks (cladding_corners.py).
function profileOffered(k) { return k > 0 && Math.abs(k - 1) < 0.05; }

function cornerDetailInForce(c) {
    const job = toggleValue('corner-type') || 'mitre';
    const d = c.lo.detailHi || job, panel = toggleValue('cladding-type') === 'panel';
    if (seamChain()) return (d === 'lap' || d === 'profile') && !profileOffered(c.k) ? 'mitre' : d;
    if (d === 'lap' && !panel) return 'mitre';
    if (d === 'profile' && !profileOffered(c.k)) return 'mitre';
    return d;
}

// The four details as <option>s, Master and Profile greyed where they do not apply.
function cornerOptions(own, profileOk, withDefault) {
    const panel = toggleValue('cladding-type') === 'panel';
    // withDefault marks a chain corner (a window's or door's has no job default): there a
    // standing seam chain offers its own corner seam and flashing strip
    const seam = seamChain(), chainSeam = seam && withDefault, labels = chainSeam ? SEAM_CORNER_LABEL : CORNER_LABEL;
    return (withDefault ? [['', 'Job default']] : []).concat(['mitre', 'lap', 'profile', 'butt'].map(v => [v, labels[v]]))
        .map(([v, t]) => {
            const off = chainSeam ? ((v === 'lap' || v === 'profile') && !profileOk)
                                  : (v === 'lap' && !panel) || (v === 'profile' && !profileOk);
            const why = chainSeam ? 'right-angled external corners only'
                : v === 'lap' ? 'panel cladding only' : (seam ? 'not with standing seam' : 'right-angled external corners only');
            return `<option value="${v}" ${v === own ? 'selected' : ''} ${off ? 'disabled' : ''}>${t}${off ? ' (' + why + ')' : ''}</option>`;
        }).join('');
}

// ─── WINDOW AND DOOR CORNERS ───
// Each opening's jambs share one detail and one master arrangement (mirrored); its head
// has its own. Choices live on the elevation by opening key (u0,v0 of the structural
// opening), so they survive every rebuild.
function openingsOf(m) {
    const info = ((window._lastPreview || {}).info || []).find(i => m.result && i.elevation === m.result.name);
    return (info && info.opening_details) || [];
}

function setOpeningDetail(e, key, part, detail, master, scope) {
    const targets = scope === 'chain' ? e.chain.members.filter(m => m.result && m.result.ok) : [e];
    for (const m of targets) {
        const keys = scope === 'chain' ? openingsOf(m).map(o => o.key) : [key];
        for (const k of keys) {
            m.openingDetails = m.openingDetails || {};
            const cur = m.openingDetails[k] = Object.assign({}, m.openingDetails[k]);
            cur[part] = detail;
            cur[part + '_master'] = master;
        }
    }
    renderElevationList();
    onNumeric();
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
            onchange="setCornerDetail('${chain.name}', ${i}, this.value)">` + cornerOptions(own, profileOffered(c.k), true) + '</select>';
        const swap = detail === 'lap'
            ? ` · ${seamChain() ? 'on' : ''} <b>${master.replace('Elevation ', '')}</b>${seamChain() ? '' : ' masters'} <button class="mini" onclick="event.stopPropagation(); swapCorner('${chain.name}', ${i})">Swap</button>`
            : '';
        return `<div class="corner-row" data-detail="${detail}" onclick="selectCorner('${chain.name}', ${i})">`
            + `${c.lo.name.replace('Elevation ', '')}–${c.hi.name.replace('Elevation ', '')} corner`
            + ` · ${angle}° ${kind} · <span class="corner-in-force">${cornerLabel(detail)}${own ? '' : ' (job)'}</span>${swap} ${pick}</div>`;
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
    if (n[0] * f.n[0] + n[1] * f.n[1] + n[2] * f.n[2] < 0.9998) return false;
    const d = f.n[0] * (p[0] - f.origin[0]) + f.n[1] * (p[1] - f.origin[1]) + f.n[2] * (p[2] - f.origin[2]);
    return Math.abs(d) < 25;
}

async function pyLink(a, b) {
    const slim = r => JSON.stringify({ frame: r.frame, width: r.width, height: r.height, polygons: r.polygons });   // the outline: a hipped slope's ridge is shorter than the face
    pyodide.globals.set('_link_a', slim(a)); pyodide.globals.set('_link_b', slim(b));
    const out = await enginePy(`
import json as _json
from fabric_extract import chain_link as _cl
_json.dumps(_cl(_json.loads(_link_a), _json.loads(_link_b)))`);
    return JSON.parse(out);
}

// Two roof slopes at a ridge lie along one another, not end to end. The new face takes the
// other's place in the run, turned round (its u runs against the other's: u_b = u_b0 - u_a),
// so both are set out from one line and their seams and joints carry over the ridge. The
// face that was there first masters the ridge: it carries the cap.
function placeAtRidge(e, other, link) {
    const W = e.result.width, Wo = other.result.width;
    e.rev = !other.rev;
    e.start = other.rev ? other.start + Wo - link.u_b0 : other.start - W + link.u_b0;
    e.clipLo = 0; e.clipHi = null; e.cornerLo = 0; e.cornerHi = 0; e.masterLo = e.masterHi = false; e.detailLo = e.detailHi = null;
    const part = (partner, v, u, master) => ({ partner, v, u0: u[0], u1: u[1], k: link.k, angle: link.angle, master });
    other.ridge = part(e, link.v_a, link.u_a, true);
    e.ridge = part(other, link.v_b, link.u_b, false);
    e.link = link;
}

// A face's ridge as the engine and a saved state take it: the partner by name.
function ridgeRecord(e) {
    const r = e.ridge;
    if (!r || !r.partner || r.partner.chain !== e.chain || !(r.partner.result && r.partner.result.ok)) return null;
    return { with: r.partner.name, v: r.v, u0: r.u0, u1: r.u1, k: r.k, angle: r.angle, master: !!r.master };
}

// A valley joins two roof slopes that need share nothing else (a main roof's and a wing's), so
// it is looked for between a face and every other roof slope, whatever chains they are in. The
// face that was there first masters the valley: it carries the gutter.
const isRoofSlope = m => !!(m.result && m.result.ok && m.result.pitch !== undefined && m.result.pitch < 89 && !m.result.faces_down);
const isPitched = m => !!(m.result && m.result.ok && m.result.pitch !== undefined && (m.result.pitch < 89 || m.result.faces_down));
const ROOF_JOINS = ['valley', 'hip', 'rake', 'eaves', 'head', 'abut'];
async function linkValleys(e) {
    for (const m of state.elevations) if (m.valleys) m.valleys = m.valleys.filter(v => v.partner !== e);
    e.valleys = [];
    // Two roof slopes meet at a valley or a hip; a pitched face (a leaning wall, a slope) and
    // any other face at a raking corner. Two upright walls are a chain corner, not this.
    const ok = m => !!(m.result && m.result.ok), upright = m => ok(m) && !isPitched(m);
    const others = state.elevations.filter(m => m !== e && ok(m) && !(upright(e) && upright(m)));
    if (!ok(e) || !others.length) return [];
    const slim = r => ({ frame: r.frame, width: r.width, height: r.height, polygons: r.polygons });
    pyodide.globals.set('_valley_a', JSON.stringify(slim(e.result)));
    pyodide.globals.set('_valley_b', JSON.stringify(others.map(m => slim(m.result))));
    let links = [];
    try {
        links = JSON.parse(await enginePy(`
import json as _json
from fabric_extract import valley_links as _vl
_json.dumps(_vl(_json.loads(_valley_a), _json.loads(_valley_b)))`));
    } catch (err) { console.warn('valley link failed', err); }
    const found = [];
    links.forEach((link, i) => {
        if (!link) return;
        const other = others[i];
        const part = (partner, side, master) => Object.assign({ partner, k: link.k, angle: link.angle, length: link.length, master }, side);
        (other.valleys = other.valleys || []).push(part(e, link.b, true));
        e.valleys.push(part(other, link.a, false));
        found.push({ face: other, kind: ROOF_JOINS.includes(link.kind) ? link.kind : 'valley' });
    });
    return found;
}

// A face's valleys as the engine and a saved state take them: each partner by name.
// A raking corner is one corner whatever chains its two faces are in, so both are told the
// same detail: Profile where either chain's corner is set to it (whichever was active when
// it was chosen), else its master's. A corner profile needs both faces clad alike.
function rakeDetail(master, other) {
    const of = m => (m.chain && m.chain.settings) || (typeof formSettings === 'function' ? formSettings() : {}) || {};
    const a = of(master), b = of(other);
    const alike = ['cladding_type', 'plank_profile', 'panel_t', 'plank_t'].every(k => String(a[k]) === String(b[k]));
    return { detail: [a.corner, b.corner].includes('profile') ? 'profile' : (a.corner || 'mitre'), match: alike };
}

// The face this one's seams follow, by name: as set (the list, the pipette), or as the last
// build linked them by itself (a mitred wall abutment).
function seamFollows(e) {
    if (e.seamFrom && state.elevations.includes(e.seamFrom)) return e.seamFrom.name;
    const info = ((window._lastPreview || {}).info || []).find(i => i.elevation === e.name);
    return info && info.seam_from && info.seam_from.lines ? info.seam_from.name : null;
}

function valleyRecords(e) {
    return (e.valleys || []).filter(v => v.partner && state.elevations.includes(v.partner) && v.partner.result && v.partner.result.ok)
        .map(v => Object.assign(v.kind === 'rake' ? rakeDetail(v.master ? e : v.partner, v.master ? v.partner : e) : {}, { kind: ROOF_JOINS.includes(v.kind) ? v.kind : 'valley', with: v.partner.name, p1: v.p1, p2: v.p2, inside: v.inside, other_up: v.other_up, other_n: v.other_n,
                     k: v.k, angle: v.angle, length: v.length, master: !!v.master, roof: !!v.roof }));
}

function placeInChain(e, other, link) {
    if (link.kind === 'ridge') return placeAtRidge(e, other, link);
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

// ─── HEALING NEAR MISSES ───
// A face modelled a little short of its neighbour has no edge on the line the two would meet
// on, so they are not joined: no corner, ridge, hip or valley. After each extraction the
// engine is asked which other faces this one nearly meets (fabric_extract.heal_link) and
// what each would have to be extended by. Those strips are ghosted in red with a tick and a
// cross. The tick adds them to the faces (as picked surface, so everything downstream sees
// one face that reaches the line) and extracts both again, which is what joins them; the
// cross leaves that pair alone from then on.
const HEAL_KINDS = { corner: 'a corner', ridge: 'a ridge', hip: 'a hip', valley: 'a valley', rake: 'a raking corner', edge: 'an edge (no junction is detailed between a roof and a wall)', abutment: 'an abutment (the other face runs on past it, and is left as it is)' };
//
// Where a face runs on a little past the line instead (a wall drawn through its corner, a
// roof's edge past the wall it lands on) the offer is the other way about: what is past the
// line is ghosted in red, and the tick trims it off (h.trim: the outlines go to the face as
// "trims", cut out of its surface when it is read again), bringing a face that is short
// up to the line with it. A pair can have both offers; each is declined on its own.
const healKey = (a, b, trim) => [a.name, b.name].sort().join(' | ') + (trim ? ' | trim' : '');
function showHeals() {
    state.heals = (state.heals || []).filter(h => state.elevations.includes(h.a) && state.elevations.includes(h.b));
    renderHeals(state.heals.map(h => h.trim ? { quads: h.show.concat(h.quads.a, h.quads.b),
        title: `Trim ${h.cut.a.length && h.cut.b.length ? h.a.name + ' and ' + h.b.name : (h.cut.a.length ? h.a.name : h.b.name)} back by up to ${Math.round(h.over)} mm, to the line ${h.a.name} and ${h.b.name} meet on, so they meet at ${HEAL_KINDS[h.kind] || 'an edge'}`
            + (h.quads.a.length || h.quads.b.length ? `; ${h.quads.a.length ? h.a.name : h.b.name} is extended to it` : '') }
        : { quads: h.quads.a.concat(h.quads.b),
        title: `Heal ${h.a.name} and ${h.b.name}: extend ${h.quads.a.length && h.quads.b.length ? 'both' : (h.quads.a.length ? h.a.name : h.b.name)} by up to ${Math.round(h.gap)} mm so they meet at ${HEAL_KINDS[h.kind] || 'an edge'}` }),
        applyHeal, dismissHeal);
}

async function offerHeals(e) {
    state.heals = (state.heals || []).filter(h => h.a !== e && h.b !== e);
    const others = state.elevations.filter(m => m !== e && m.result && m.result.ok && m.picks.length);
    if (e.result && e.result.ok && e.picks.length && others.length) {
        const slim = r => ({ frame: r.frame, width: r.width, height: r.height, polygons: r.polygons, healed: r.healed });
        pyodide.globals.set('_heal_a', JSON.stringify(slim(e.result)));
        pyodide.globals.set('_heal_b', JSON.stringify(others.map(m => slim(m.result))));
        let links = [];
        try {
            links = JSON.parse(await enginePy(`
import json as _json
from fabric_extract import heal_links as _hl
_json.dumps(_hl(_json.loads(_heal_a), _json.loads(_heal_b)))`));
        } catch (err) { console.warn('heal check failed', err); }
        state.healNo = state.healNo || new Set();
        links.forEach((h, i) => {
            if (!h) return;
            if ((h.a.length || h.b.length) && !state.healNo.has(healKey(e, others[i])))
                state.heals.push({ a: e, b: others[i], kind: h.kind, gap: h.gap, length: h.length, quads: { a: h.a, b: h.b } });
            const t = h.trim;                     // one or both run on past the line: the overshoot, to trim
            if (t && !state.healNo.has(healKey(e, others[i], true)))
                state.heals.push({ a: e, b: others[i], kind: t.kind, trim: true, over: t.over, length: h.length, show: t.show,
                                   cut: { a: t.a, b: t.b }, quads: { a: t.add.a, b: t.add.b } });
        });
    }
    showHeals();
    return state.heals.filter(h => h.a === e || h.b === e);
}

// The junctions a face takes part in, as "face|kind|other": a heal must not undo one (a wall
// healed up under a roof loses the monopitch head it stood under; the corner it turned).
function junctionsOf(faces) {
    const out = new Set();
    for (const e of faces) {
        const key = (kind, other) => { const [x, y] = [e.name, other.name].sort(); return [x, kind, y].join('|'); };   // the same whichever face holds it
        for (const v of e.valleys || []) if (v.partner) out.add(key(v.kind || 'valley', v.partner));
        if (e.ridge && e.ridge.partner) out.add(key('ridge', e.ridge.partner));
    }
    return out;
}

async function applyHeal(i) {
    const h = state.heals[i];
    if (!h) return;
    state.heals = state.heals.filter(x => !(x.a === h.a && x.b === h.b) && !(x.a === h.b && x.b === h.a));   // the pair's other offer goes with it
    showHeals();
    const before = junctionsOf(state.elevations), was = [h.a, h.b].map(e => ({ e, heals: e.heals, trims: e.trims, chain: e.chain }));
    if (h.trim) {                                 // what ran on past the line comes off each face's surface
        h.a.trims = (h.a.trims || []).concat(h.cut.a);
        h.b.trims = (h.b.trims || []).concat(h.cut.b);
    }
    // a strip, and the elements it was healed to: those do not cut it, everything else does (fabric_extract._extract)
    // (healed up under a face that runs on over it, an "abutment", the strip stops under what it was healed to)
    const onto = m => m.picks.map(p => p.mesh.userData.index), under = h.kind === 'abutment';
    h.a.heals = (h.a.heals || []).concat(h.quads.a.map(quad => ({ quad, through: onto(h.b), under })));
    h.b.heals = (h.b.heals || []).concat(h.quads.b.map(quad => ({ quad, through: onto(h.a), under })));
    // Two walls, or two slopes at a ridge, become one chain: a face that was given a chain
    // of its own for want of a neighbour goes back into its partner's before it is re-read.
    if ((h.kind === 'corner' || h.kind === 'ridge') && h.a.chain !== h.b.chain) {
        const [lone, host] = h.a.chain.members.length === 1 ? [h.a, h.b] : (h.b.chain.members.length === 1 ? [h.b, h.a] : [null, null]);
        if (lone && !lone.chain.built) {
            state.chains = state.chains.filter(c => c !== lone.chain);
            lone.chain = host.chain; host.chain.members.push(lone);
        }
    }
    for (const e of [h.b, h.a]) {
        e.highlights.forEach(x => { highlightGroup.remove(x); x.geometry.dispose(); });
        e.highlights = pickHighlights(e);
    }
    await runExtraction(h.b);
    await runExtraction(h.a);
    // Healed, the two must still meet everything they met: where the heal undid a junction
    // (a monopitch head, an eaves, a corner) it is taken back and not offered again.
    const after = junctionsOf(state.elevations);
    const lost = [...before].filter(k => { const [x, , y] = k.split('|'); return !after.has(k) && [x, y].some(n => n === h.a.name || n === h.b.name); });
    if (lost.length) {
        for (const w of was) { w.e.heals = w.heals; w.e.trims = w.trims; }
        (state.healNo = state.healNo || new Set()).add(healKey(h.a, h.b, h.trim));
        for (const e of [h.b, h.a]) { e.highlights.forEach(x => { highlightGroup.remove(x); x.geometry.dispose(); }); e.highlights = pickHighlights(e); }
        await runExtraction(h.b);
        await runExtraction(h.a);
        const say = lost.map(k => { const [x, kind, y] = k.split('|'); return `the ${kind} between ${x} and ${y}`; });
        log(`heal of ${h.a.name} and ${h.b.name} taken back: it undid ${say.join(', ')}`);
        setStatus(`Not healed: joining ${h.a.name} and ${h.b.name} would undo ${say.join(' and ')}, so they are left as they were`, 'busy');
    }
}

function dismissHeal(i) {
    const h = state.heals[i];
    if (!h) return;
    (state.healNo = state.healNo || new Set()).add(healKey(h.a, h.b, h.trim));
    state.heals.splice(i, 1);
    showHeals();
}

// The picked surface, ghosted: the faces clicked and the strips healed onto them.
function pickHighlights(e) {
    return e.picks.map(p => highlightFaces(p.mesh, p.faces)).concat((e.heals || []).length ? [quadMesh(e.heals.map(h => h.quad), PICK_COLOR, highlightGroup)] : []);
}

async function linkIntoChain(e) {
    const chain = e.chain;
    const others = chain.members.filter(m => m !== e && m.result && m.result.ok).reverse();
    for (const other of others) {
        let link = null;
        try { link = await pyLink(other.result, e.result); } catch (err) { console.warn('link failed', err); }
        if (link && link.kind === 'ridge' && other.ridge && other.ridge.partner !== e) link = null;   // a slope has one ridge
        if (link) { placeInChain(e, other, link); relayoutChain(chain); return true; }
    }
    if (others.length) {   // not adjacent to anything in this chain: give it a chain of its own
        chain.members = chain.members.filter(m => m !== e);
        e.chain = newChain(); e.chain.members.push(e);
    }
    e.start = 0; e.rev = false; e.link = null; e.cornerLo = 0; e.cornerHi = 0; e.detailLo = e.detailHi = null;
    e.clipLo = 0; e.clipHi = null; e.ridge = null;
    relayoutChain(e.chain);
    return false;
}

// A slope on its own that meets another slope at a ridge goes into that slope's chain, whatever
// was picked in between: one setting-out over both, and the cap. Only chains not yet built,
// so a chain kept apart on purpose stays apart.
async function joinRidgePartner(e) {
    if (e.chain.built || e.chain.members.length !== 1 || !isRoofSlope(e)) return false;
    for (const other of state.elevations) {
        if (other === e || !isRoofSlope(other) || other.chain === e.chain || other.chain.built) continue;
        if (other.ridge && other.ridge.partner && other.ridge.partner !== e) continue;     // a slope has one ridge
        let link = null;
        try { link = await pyLink(other.result, e.result); } catch (err) { console.warn('ridge link failed', err); }
        if (!link || link.kind !== 'ridge') continue;
        state.chains = state.chains.filter(c => c !== e.chain);
        e.chain = other.chain; other.chain.members.push(e);
        placeInChain(e, other, link); relayoutChain(other.chain);
        return true;
    }
    return false;
}

async function relinkChain(chain) {
    const members = chain.members.slice().sort((a, b) => a.start - b.start);
    chain.members = [];
    members.forEach(m => { m.cornerLo = 0; m.cornerHi = 0; m.masterLo = false; m.masterHi = false; m.detailLo = m.detailHi = null;
                           m.link = null; m.clipLo = 0; m.clipHi = null; m.ridge = null; });
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
    setStatus('2D elevation: click a dimension to type over it, a panel joint to dissolve it, a client batten to set it on, fixing only or off, a dashed stagger to put a joint on it · E or Esc for 3D', 'ready');
}

// Cavity barriers on all four sides, from the 2D view. The button sets the chain shown
// (the sidebar's checkbox), and clears what its openings were set to one by one, so it
// does what it says; an opening's own box (a click on its jamb or head) sets just that one.
function toggleBarriers() {
    const box = document.getElementById('barriers'), e = state.elevations[state.active];
    box.checked = !box.checked;
    for (const m of (e && e.chain ? e.chain.members : []))
        for (const k of Object.keys(m.openingDetails || {})) delete m.openingDetails[k].barriers;
    syncTypeUI();
    setStatus(`Cavity barriers on all four sides of every opening${e && e.chain ? ' in ' + e.chain.name : ''}: ${box.checked ? 'on' : 'off'}`, 'ready');
    updatePreview();
}
function setOpeningBarriers(e, key, on) {
    e.openingDetails = e.openingDetails || {};
    e.openingDetails[key] = Object.assign({}, e.openingDetails[key], { barriers: !!on });
    renderElevationList();
    onNumeric();
}
function updateBarriersButton() {
    const b = document.getElementById('barriers-2d');
    if (!b) return;
    const on = document.getElementById('barriers').checked;
    b.style.display = in2D() ? '' : 'none';
    b.textContent = 'Cavity barriers: ' + (on ? 'on' : 'off');
    b.classList.toggle('on', on);
}

function update2DButton() {
    updateBarriersButton();
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
// Three modes for a click in the model: pick a wall face, hide the element under it, or
// nothing (orbit only). true / false still mean pick / orbit.
function setPickMode(mode) {
    if (mode === true) mode = 'pick';
    else if (mode === false) mode = 'orbit';
    state.mode = mode;
    state.pickMode = mode === 'pick';
    document.querySelectorAll('#pick-toggle .turn-btn').forEach(b => b.classList.toggle('active', b.dataset.value === mode));
    if (mode === 'hide') setStatus('Hide elements: click one to hide it, clicks then pass through to what is behind · H to go back to picking', 'ready');
}

function renderHidden() {
    const hidden = hiddenElements(), types = elementTypes();
    document.getElementById('hidden-count').textContent = hidden.length ? `${hidden.length} hidden` : 'None hidden';
    document.getElementById('show-all').disabled = !hidden.length;
    const sel = document.getElementById('hide-type'), keep = sel.value;
    sel.innerHTML = '<option value="">Hide all of a type…</option>'
        + types.map(([t, n]) => `<option value="${t}">${t.replace(/^Ifc/i, '')} (${n})</option>`).join('');
    sel.value = types.some(([t]) => t === keep) ? keep : '';
    document.getElementById('hidden-list').innerHTML = hidden.slice(0, 12).map(m =>
        `<button class="mini" title="Show it again" onclick="showElement(${m.mesh.userData.index})">${(m.name || m.type).slice(0, 24)} ×</button>`).join(' ')
        + (hidden.length > 12 ? ` <span class="hint">and ${hidden.length - 12} more</span>` : '');
}

function showElement(index) {
    const m = meshMeta[index];
    if (m) { m.mesh.visible = true; rebuildModelBatch(); }
    renderHidden();
}

function onHideType(select) {
    if (!select.value) return;
    const n = elementTypes().find(([t]) => t === select.value);
    hideElementType(select.value);
    setStatus(`Hidden every ${select.value.replace(/^Ifc/i, '')} (${n ? n[1] : 0})`, 'ready');
    select.value = '';
    renderHidden();
}

function onShowAll() {
    showAllElements();
    setStatus('Every element shown again', 'ready');
    renderHidden();
}

function onViewportClick(event) {
    if (event.target !== renderer.domElement || !allMeshes.length || state._dragged) return;
    if (inCuttingRun()) return;                // the run is a view: nothing is picked or edited
    if (state.pipette) { pipetteClick(event); return; }
    const dim = pickDim(event);
    if (dim && !state.levels) { openCourseDialog(dim); return; }
    // While the level picker is open every click is a height, wherever it lands.
    if (state.levels) {
        const snap = snapPick(event);
        if (snap) levelPicked(toIfc(snap.point)[2]);
        return;
    }
    if (state.mode === 'hide') {
        const hit = pickAt(event);
        if (!hit) return;
        const meta = meshMeta[hit.mesh.userData.index] || {};
        hideElement(hit.mesh);
        setStatus(`Hidden ${meta.name || meta.type || 'element'}${meta.type ? ' (' + meta.type.replace(/^Ifc/i, '') + ')' : ''} · Show all brings everything back`, 'ready');
        renderHidden();
        return;
    }
    if (!state.pickMode) return;
    const hit = pickAt(event);
    if (!hit) return;
    const faces = coplanarFaces(hit.mesh, hit.faceIndex);
    if (!faces) { setStatus('That face is too small to read — pick another part of it', 'busy'); return; }
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
    if (e.restored) {
        setStatus(`${e.name} was loaded from a saved state, so faces cannot be added to it: delete it and pick its faces again to change its surface`, 'busy');
        return;
    }
    const existing = e.picks.findIndex(p => p.mesh === hit.mesh && p.faces.includes(hit.faceIndex));
    if (existing >= 0) e.picks.splice(existing, 1);
    else e.picks.push({ mesh: hit.mesh, faces, normal: toIfc(hit.normal), point: toIfc(hit.point) });
    e.highlights.forEach(h => { highlightGroup.remove(h); h.geometry.dispose(); });
    if (!e.picks.length) { e.heals = []; e.trims = []; }     // nothing left to heal onto, or to trim
    e.highlights = pickHighlights(e);
    if (!e.storey) { const meta = meshMeta[hit.mesh.userData.index]; e.storey = meta && meta.storey ? meta.storey : null; }
    runExtraction(e);
}

async function runExtraction(e) {
    if (e.restored && !e.picks.length) return;       // loaded from a saved state: its surface stays
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

// How deep the picked wall is behind its face: a window or door counts as the frame of a
// reveal only if its face lies within it (cladding_edges.opening_frames).
function wallDepth(picks) {
    const n = picks[0].normal, p0 = picks[0].point;
    if (!p0) return 0;
    const d = n[0] * p0[0] + n[1] * p0[1] + n[2] * p0[2];
    let lo = 0;
    for (const mesh of new Set(picks.map(q => q.mesh)))
        for (const t of meshTriangles(mesh)) for (const q of t) lo = Math.min(lo, n[0] * q[0] + n[1] * q[1] + n[2] * q[2] - d);
    return Math.min(1000, Math.round(-lo));
}

// A frame position typed on one opening (mm behind the wall face, negative forward), or
// null to go back to the frame in the model or the chain's setting.
function setOpeningFrame(e, key, value) {
    e.openingDetails = e.openingDetails || {};
    const cur = e.openingDetails[key] = Object.assign({}, e.openingDetails[key]);
    if (value === null || value === undefined || value === '') delete cur.frame;
    else cur.frame = Math.max(-300, Math.min(300, parseFloat(value)));
    renderElevationList();
    onNumeric();
}

async function extractOnce(e) {
    state.heals = (state.heals || []).filter(h => h.a !== e && h.b !== e);     // offered for the surface as it was
    showHeals();
    const tris = [];
    for (const p of e.picks) tris.push(...faceTriangles(p.mesh, p.faces));
    // strips healed onto it (applyHeal) are sent apart: they count as its surface, but nothing is cut out of them
    const healed = (e.heals || []).flatMap(({ quad: q }) => [[q[0], q[1], q[2]], [q[0], q[2], q[3]]]);
    const { elements: context, dropped, triangles: nTris } = contextFor(new Set(e.picks.map(p => p.mesh)), tris.concat(healed), 300, e.picks[0].normal);
    setStatus(`Extracting ${e.name}… ${tris.length} faces, ${context.length} nearby elements (${nTris} triangles)`
              + (dropped ? `, ${dropped} too far to fit` : ''), 'busy');
    e.error = null;
    renderElevationList();
    await new Promise(r => setTimeout(r, 30));   // let the status paint before Pyodide blocks the thread
    const payload = { name: e.name, faces: tris, heals: e.heals || [], trims: e.trims || [], outward: e.picks[0].normal, context,
                      seeds: e.picks.map(p => p.point).filter(Boolean),
                      options: { penetrations: document.getElementById('penetrations').checked, wall_depth: wallDepth(e.picks) } };
    const json = JSON.stringify(payload);
    log(`${e.name}: extracting — ${tris.length} faces, ${context.length} context elements, `
        + `${nTris} triangles, ${dropped} dropped, payload ${Math.round(json.length / 1024)} kB`);
    try {
        const t0 = performance.now();
        pyodide.globals.set('_payload_json', json);
        pyodide.globals.set('_params_json', JSON.stringify(getParams()));
        // The engine works out the cladding zone's depth, so the rule lives in one place.
        const out = await pyRun(`
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
            // A corner's clip is never more than a corner's tolerance in from the face's end: one
            // set when the face was narrower (a coplanar wall picked into it since) is stale
            const W = e.result.width, CORNER_TOL = 400;
            if (e.clipHi !== null && e.clipHi !== undefined && W - e.clipHi > CORNER_TOL + 1) e.clipHi = null;
            if ((e.clipLo || 0) > CORNER_TOL + 1) e.clipLo = 0;
            let linked = await linkIntoChain(e);
            if (!linked) linked = await joinRidgePartner(e);
            const valleys = await linkValleys(e);
            const corner = !linked ? e.name + ' extracted'
                : e.link.kind === 'ridge' ? `${e.name} joined ${e.chain.name} at a ridge with ${e.ridge.partner.name}: one setting-out over both slopes, and a ridge cap`
                : `${e.name} joined ${e.chain.name} at a corner (${Math.abs(e.link.angle)}°)`;
            const heals = await offerHeals(e);
            const met = kind => valleys.filter(v => v.kind === kind).map(v => v.face.name).join(' and ');
            const valley = (met('valley') ? ` · a valley with ${met('valley')}: the cladding stands back from it, over a valley gutter` : '')
                + (met('hip') ? ` · a hip with ${met('hip')}: the cladding is cut to it, under a hip cap` : '')
                + (met('rake') ? ` · a raking corner with ${met('rake')}: mitred, or under a corner profile where the job's corner is Profile` : '')
                + (met('eaves') ? (isPitched(e) && valleys.filter(v => v.kind === 'eaves').every(v => isPitched(v.face))
                    ? ` · a mansard with ${met('eaves')}: the flatter slope runs on over the steeper one's cladding with a drip, and the steeper one stops under it`
                    : ` · an eaves with ${met('eaves')}, no overhang: the roof runs on over the wall's cladding with a drip, and the wall stops under it`) : '')
                + (met('head') ? ` · a head verge with ${met('head')}: the monopitch's top runs on over the wall, under a G3 cap that turns down over the wall's cladding` : '')
                + (met('abut') ? ` · a head-wall abutment with ${met('abut')}: the roof runs up to the wall, under a G3 flashing turned up it, or (a standing seam wall) its trays turned up behind the wall's cladding, or mitred to it where the chain says so` : '');
            const withOf = hs => [...new Set(hs.map(h => (h.a === e ? h.b : h.a).name))].join(' and ');
            const short = heals.filter(h => !h.trim), past = heals.filter(h => h.trim);
            const near = (short.length ? ` · nearly meets ${withOf(short)}: tick the red strip to heal the junction` : '')
                + (past.length ? ` · runs past where it meets ${withOf(past)}: tick the red area to trim it back to the line` : '');
            setStatus((e.chain.built ? corner : corner + ' — press Enter to build') + valley + near, 'ready');
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
        // the same stage killed it again: say where (cladding_constants.crumb)
        const where = /fatally failed|Aborted/i.test(err.message || '') ? (window._engineStep || window._engineDiedIn) : '';
        e.result = null; e.error = 'Extraction error: ' + (where ? 'the engine crashed while ' + where + ' — ' : '') + err.message;
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
             'plank_len', 'plank_min', 'plank_splice', 'closer_w', 'barrier_gap', 'board_w', 'board_h', 'kerf', 'board_trim',
             'seam_centres', 'seam_h', 'seam_t'];

// The piece length the splices slider sets out to: the stock length at Fewer, the
// shortest piece at More (cladding_constants._parse, plank_target).
function splicePiece() {
    const len = +val('plank_len') || 3600, min = Math.min(+val('plank_min') || 300, len), s = +val('plank_splice') || 0;
    return Math.round(len - s / 100 * (len - min)) + ' mm';
}
function val(id) { return document.getElementById(id).value; }

// ─── PER-CHAIN SETTINGS ───
// Each chain carries its own cladding (type, sizes, battens, build-up, corner detail) as
// chain.settings, sent on its elevation records; the engine builds each chain with its own
// (cladding_preview.setting_groups). The sidebar shows the active face's chain: selecting a
// face of another chain loads that chain's settings into it, and any edit is kept by the
// chain shown (state.formChain), so one chain's choices never change another's. Live trim,
// panels as walls and shared offcuts stay job-wide.
const CHAIN_CHECKS = ['sheathing', 'insulation', 'reveals', 'set_out_from_openings', 'rotate', 'battens_from_model', 'seam_full_length', 'seam_balanced', 'base_drip', 'match_battens', 'barriers'];
function formSettings() {
    const s = { corner: toggleValue('corner-type') || 'mitre' };
    for (const k of NUM) { const v = parseFloat(val(k)); if (isFinite(v)) s[k] = v; }
    for (const k of CHAIN_CHECKS) s[k] = document.getElementById(k).checked;
    s.cladding_type = toggleValue('cladding-type') || 'plank';
    s.plank_orient = toggleValue('plank-orient') || 'horizontal';
    s.plank_profile = toggleValue('plank-profile') || 'board';
    Object.assign(s, customProfileParams());
    s.seam_deck = val('seam_deck');
    s.seam_metal = val('seam_metal');
    s.counter_batten = val('counter_batten');
    return s;
}
function applySettings(s) {
    if (!s) return;
    for (const k of NUM) if (typeof s[k] === 'number' && isFinite(s[k])) document.getElementById(k).value = s[k];
    for (const k of CHAIN_CHECKS) if (typeof s[k] === 'boolean') document.getElementById(k).checked = s[k];
    if (s.corner) selectToggle('corner-type', s.corner);
    if (s.cladding_type) selectToggle('cladding-type', s.cladding_type);
    if (s.plank_orient) selectToggle('plank-orient', s.plank_orient);
    if (s.plank_profile) selectToggle('plank-profile', s.plank_profile);
    if (s.custom_section) addCustomProfile(s.custom_section, false);
    if (s.custom_profile && document.querySelector('#custom_profile option[value="' + CSS.escape(s.custom_profile) + '"]'))
        document.getElementById('custom_profile').value = s.custom_profile;
    if (s.seam_deck) document.getElementById('seam_deck').value = s.seam_deck;
    if (s.seam_metal) document.getElementById('seam_metal').value = s.seam_metal;
    if (s.counter_batten) document.getElementById('counter_batten').value = s.counter_batten;
}
// ── custom plank profiles (cladding_profiles.py) ──
// The library's are named in the select; one read from a DXF is kept here by its key and
// sent with the parameters, so it travels with its chain's settings and the saved state.
const CUSTOM_LIBRARY = { cedral_click: { name: 'Cedral Click', width: 186, thickness: 12, cover: 172.058 } };
const importedProfiles = {};
function customProfileParams() {
    const key = val('custom_profile') || 'cedral_click';
    return importedProfiles[key] ? { custom_profile: key, custom_section: importedProfiles[key] } : { custom_profile: key };
}
function addCustomProfile(sec, select) {
    const key = 'dxf:' + sec.name;
    importedProfiles[key] = sec;
    const sel = document.getElementById('custom_profile');
    if (!sel.querySelector('option[value="' + CSS.escape(key) + '"]')) {
        const o = document.createElement('option');
        o.value = key; o.textContent = sec.name + ' (imported)';
        sel.appendChild(o);
    }
    if (select) sel.value = key;
    return key;
}
function showCustomProfile() {
    const key = val('custom_profile'), sec = importedProfiles[key] || CUSTOM_LIBRARY[key];
    document.getElementById('custom-profile-info').textContent = sec
        ? `${sec.name}: ${(+sec.width).toFixed(1)} wide, ${(+sec.thickness).toFixed(1)} thick, laid at ${(+sec.cover).toFixed(1)} centres (laps ${(sec.width - sec.cover).toFixed(1)})`
        : '';
}
function onCustomProfile() { showCustomProfile(); updateSliderRange(); updatePreview(); }
async function importProfile(file) {
    if (!file) return;
    const text = await file.text();
    pyodide.globals.set('_profile_dxf', text);
    pyodide.globals.set('_profile_name', file.name.replace(/\.dxf$/i, ''));
    const res = JSON.parse(await enginePy(`
import json as _json
from cladding_profiles import profile_from_dxf as _pfd
_sec, _msg = _pfd(_profile_dxf, _profile_name)
_json.dumps({"section": _sec, "message": _msg})`));
    if (!res.section) { alert(res.message); return; }
    addCustomProfile(res.section, true);
    selectToggle('plank-profile', 'custom');
    onTypeChange();
    document.getElementById('custom-profile-info').textContent = res.message;
}

// ─── COPYING SETTINGS BETWEEN CHAINS ───
// A chain's settings (cladding: chain.settings, the Buildup form; edges: chain.edges) can be
// copied from another chain of this job, or from a chain of a saved state read in as a
// library entry (state.settingsLibrary: [{file, chains: [{name, kind, settings, edges}]}],
// saved with the job). The copy is made once; nothing stays linked. What is not a setting
// (the chain's picked top and bottom, its setting-out, corners, seam links) is never copied.
function chainKind(pitches) {
    const p = pitches.filter(x => typeof x === 'number' && isFinite(x));
    if (!p.length) return 'chain';
    if (p.every(x => x >= 80)) return 'wall';
    if (p.every(x => x < 80)) return 'roof';
    return 'mixed';
}
const liveKind = c => chainKind(c.members.filter(m => m.result && m.result.ok).map(m => m.result.pitch));
const kindLabel = k => (k === 'wall' ? 'walls' : k === 'roof' ? 'roof' : k === 'mixed' ? 'walls and roof' : '');

function renderSettingsLibrary() {
    const sel = document.getElementById('copy-from');
    if (!sel) return;
    const was = sel.value;
    const here = activeChain();
    let html = '<option value="">Choose a chain…</option>';
    const live = state.chains.filter(c => c !== here && c.members.some(m => m.result && m.result.ok));
    if (live.length) {
        html += '<optgroup label="This job">';
        state.chains.forEach((c, i) => {
            if (live.includes(c)) html += `<option value="chain:${i}">${escapeHtml(c.name)} · ${kindLabel(liveKind(c))}</option>`;
        });
        html += '</optgroup>';
    }
    (state.settingsLibrary || []).forEach((f, i) => {
        html += `<optgroup label="${escapeHtml(f.file)}">`;
        f.chains.forEach((c, j) => { html += `<option value="lib:${i}:${j}">${escapeHtml(c.name)} · ${kindLabel(c.kind)}</option>`; });
        html += '</optgroup>';
    });
    html += '<option value="file">From a state file…</option>';
    sel.innerHTML = html;
    if ([...sel.options].some(o => o.value === was)) sel.value = was;
    const files = document.getElementById('copy-files');
    files.innerHTML = (state.settingsLibrary || []).map((f, i) =>
        `<span class="chip" title="${escapeHtml(f.file)}: ${f.chains.length} chain(s) in the list">${escapeHtml(f.file)} <button class="chip-x" onclick="removeSettingsFile(${i})" title="Take this file's chains out of the list">×</button></span>`).join(' ');
}

function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}

function onCopyFrom(sel) {
    if (sel.value === 'file') { sel.value = ''; document.getElementById('copy-file').click(); }
}

// Each file's chains go in the list as their settings and edges only (no surfaces).
async function importSettingsFiles(files) {
    let added = 0;
    for (const file of files || []) {
        const xml = await file.text();
        let res;
        try {
            pyodide.globals.set('_state_xml', xml);
            res = JSON.parse(await enginePy(`
import json as _json
from cladding_state import load as _load
_json.dumps(_load(_state_xml))`));
        } catch (err) { res = { ok: false, error: err.message }; }
        if (!res.ok) { setStatus(`Not read: ${file.name}: ${res.error}`, 'busy'); continue; }
        const chains = (res.state.chains || []).filter(c => c.settings || c.edges).map(c => ({
            name: c.name || 'Chain', kind: chainKind((c.members || []).map(m => m.surface && m.surface.pitch)),
            settings: c.settings ? JSON.parse(JSON.stringify(c.settings)) : null,
            edges: c.edges ? JSON.parse(JSON.stringify(c.edges)) : null }));
        if (!chains.length) { setStatus(`${file.name} has no chain settings to copy`, 'busy'); continue; }
        state.settingsLibrary = (state.settingsLibrary || []).filter(f => f.file !== file.name);    // read again: replaced
        state.settingsLibrary.push({ file: file.name, chains });
        added += chains.length;
    }
    renderSettingsLibrary();
    const sel = document.getElementById('copy-from');
    const last = (state.settingsLibrary || []).length - 1;
    if (added && last >= 0) sel.value = `lib:${last}:0`;
    if (added) setStatus(`${added} chain(s) listed under Copy settings from`, 'ok');
}

function removeSettingsFile(i) {
    const f = (state.settingsLibrary || [])[i];
    if (!f) return;
    state.settingsLibrary.splice(i, 1);
    renderSettingsLibrary();
    setStatus(`${f.file} taken out of the list: the settings already copied from it stay`, 'ok');
}

function settingsSource(value) {
    const [kind, a, b] = (value || '').split(':');
    if (kind === 'chain') {
        const c = state.chains[+a];
        if (!c) return null;
        if (c === state.formChain) c.settings = formSettings();          // as the form has it now
        return { name: c.name, kind: liveKind(c), settings: c.settings, edges: c.edges, chain: c };
    }
    if (kind === 'lib') {
        const f = (state.settingsLibrary || [])[+a], c = f && f.chains[+b];
        return c ? Object.assign({ from: f.file }, c) : null;
    }
    return null;
}

// What copying *src* onto chain *c* changes: [key, from, to] for each setting that differs.
function settingsChanges(src, c, cladding, edges) {
    const out = [];
    const diff = (a, b, label) => {
        for (const k of Object.keys(a || {})) {
            const x = (b || {})[k], y = a[k];
            if (JSON.stringify(x) !== JSON.stringify(y)) out.push([label + k, x, y]);
        }
    };
    if (cladding) diff(src.settings, c.settings, '');
    if (edges) diff(src.edges, c.edges, 'edges.');
    return out;
}

function copyChainSettings() {
    const src = settingsSource(document.getElementById('copy-from').value);
    if (!src) { setStatus('Choose a chain to copy the settings from', 'busy'); return; }
    const cladding = document.getElementById('copy-cladding').checked, edges = document.getElementById('copy-edges').checked;
    if (!cladding && !edges) { setStatus('Tick Cladding, Edges or both', 'busy'); return; }
    if (state.formChain && state.chains.includes(state.formChain)) state.formChain.settings = formSettings();
    const to = document.getElementById('copy-to').value;
    const here = activeChain();
    const targets = (to === 'this' ? [here] : state.chains.filter(c => liveKind(c) === (to === 'walls' ? 'wall' : 'roof')))
        .filter(c => c && c !== src.chain);
    if (!targets.length) { setStatus(to === 'this' ? 'Select a face of the chain to copy onto first' : 'No such chains to copy onto', 'busy'); return; }
    const where = src.from ? `${src.name} (${src.from})` : src.name;
    const odd = targets.filter(c => src.kind !== 'chain' && liveKind(c) !== 'chain' && src.kind !== liveKind(c) && src.kind !== 'mixed' && liveKind(c) !== 'mixed');
    let lines = [], count = 0;
    for (const c of targets) {
        const ch = settingsChanges(src, c, cladding, edges);
        count += ch.length;
        if (ch.length) lines.push(`${c.name}: ` + ch.slice(0, 8).map(([k, a, b]) => `${k} ${a === undefined ? '–' : a} → ${b}`).join(', ') + (ch.length > 8 ? `, and ${ch.length - 8} more` : ''));
    }
    if (!count) { setStatus(`Nothing to copy: ${targets.map(c => c.name).join(', ')} already set as ${where}`, 'ok'); return; }
    const warn = odd.length ? `\n\n${src.name} is a ${src.kind} chain and ${odd.map(c => c.name).join(', ')} ${odd.length > 1 ? 'are' : 'is'} not: details meant for one (verges, heads, the drip at a wall's foot, corners) may not suit the other.` : '';
    if (!confirm(`Copy ${[cladding ? 'cladding' : '', edges ? 'edge' : ''].filter(Boolean).join(' and ')} settings from ${where}?\n\n${lines.join('\n')}${warn}`)) return;
    for (const c of targets) {
        if (cladding && src.settings) c.settings = Object.assign({}, c.settings || {}, JSON.parse(JSON.stringify(src.settings)));
        if (edges && src.edges) c.edges = Object.assign({}, EDGE_DEFAULTS, c.edges || {}, JSON.parse(JSON.stringify(src.edges)));
    }
    if (state.formChain && targets.includes(state.formChain)) {         // the form shows a chain that changed: show it as it is now
        if (state.formChain.settings) { applySettings(state.formChain.settings); syncTypeUI(); }
    }
    syncEdgeFields();
    renderElevationList();
    setStatus(`Copied from ${where} onto ${targets.map(c => c.name).join(', ')}`, 'ok');
    updatePreview();
}

function activeChain() { const e = state.elevations[state.active]; return e ? e.chain : null; }
// Keep the chain shown in the sidebar, then show the active face's chain if it is another.
function showActiveChainSettings() {
    const c = activeChain();
    if (state.formChain && state.chains.includes(state.formChain)) state.formChain.settings = formSettings();
    if (!c || c === state.formChain) { state.formChain = c || state.formChain; return false; }
    state.formChain = c;
    if (c.settings) { applySettings(c.settings); syncTypeUI(); }
    else c.settings = formSettings();
    return true;
}
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
                                                   panel_joints: e.panelJoints || null,
                                                   post_states: e.postStates || null, stagger_joints: e.staggerJoints || null,
                                                   ridge: ridgeRecord(e), valleys: valleyRecords(e),
                                                   seam_from: e.seamFrom && state.elevations.includes(e.seamFrom) && e.seamFrom.result && e.seamFrom.result.ok
                                                       ? e.seamFrom.name : null,
                                                   settings: chain.settings || null,
                                                   course_datum_from: chain.datumFrom ? chain.datumFrom.name : null,
                                                   edge_top: chain.edges.top, edge_side: chain.edges.side,
                                                   edge_bottom: chain.edges.bottom, mitre_gap: chain.edges.gap,
                                                   head_vent: chain.edges.vent, head_air: chain.edges.air,
                                                   profile_gap: chain.edges.pgap === undefined ? 1 : chain.edges.pgap,
                                                   frame_setback: chain.edges.frame === undefined ? 50 : chain.edges.frame,
                                                   seam_fold: chain.edges.fold !== 'off',
                                                   verge: VERGES.includes(chain.edges.verge) ? chain.edges.verge : 'both',
                                                   verge_type: VERGE_TYPES.includes(chain.edges.vergeType) ? chain.edges.vergeType : 'auto',
                                                   verge_last: chain.edges.vergeLast === 'full' ? 'full' : 'vmz',
                                                   verge_oversail: chain.edges.vergeOver == null ? null : chain.edges.vergeOver,
                                                   eaves: chain.edges.eaves === 'none' ? 'none' : 'on',
                                                   window_flashings: chain.edges.windows === 'none' ? 'none' : 'on',
                                                   head: HEADS.includes(chain.edges.head) ? chain.edges.head : 'auto',
                                                   abutment: ABUTS.includes(chain.edges.abutment) ? chain.edges.abutment : 'auto',
                                                   cap_ends: chain.edges.capEnds === 'square' ? 'square' : 'edge',
                                                   ridge_end: RIDGE_ENDS.includes(chain.edges.ridgeEnd) ? chain.edges.ridgeEnd : 'square',
                                                   ridge_v_angle: chain.edges.ridgeAngle == null ? 90 : chain.edges.ridgeAngle,
                                                   seam_top: chain.edges.seamTop === 'flat' ? 'flat' : 'folded',
                                                   seam_bottom: chain.edges.seamBottom === 'flat' ? 'flat' : 'folded',
                                                   opening_details: e.openingDetails || {} }));
        }
    }
    return out;
}

// A picked level is a world height; each elevation reads it in its own v: straight up a
// wall, and up the slope of a pitched face, where a millimetre of v is less than one of
// height. A level face has no height to read, so there its v stands in for one
// (cladding_constants.v_to_z does the same).
function riseOf(e) {
    const v = e.result.frame.v, rise = v ? v[2] : 1;
    return Math.abs(rise) > 1e-6 ? rise : 1;
}
function vLocal(e, z) {
    return (z === null || z === undefined) ? null : (z - e.result.frame.origin[2]) / riseOf(e);
}
function zOfLocal(e, v) { return e.result.frame.origin[2] + v * riseOf(e); }

// How a face lies, for its card: nothing for a wall.
function lieOf(r) {
    if (r.pitch === undefined || r.pitch > 89) return '';
    if (r.pitch < 0.3) return r.faces_down ? ' · level, facing down' : ' · level';
    return ` · pitched ${Math.round(r.pitch * 10) / 10}°${r.faces_down ? ', leaning out' : ''}`;
}

/** The scene's shift from the model's own coordinates, in the IFC's axes (mm). */
function ifcOffset() {
    return [modelOffset[0], -modelOffset[2], modelOffset[1]];
}

function getParams() {
    if (state.formChain && state.chains.includes(state.formChain)) state.formChain.settings = formSettings();
    // The scene's shift (modelOffset) is in the scene's axes, y up; the engine and the exports
    // work in the IFC's, z up: Three (x, y, z) <-> IFC (x, -z, y). Sent as it was, the
    // cladding went out with the model's height and its depth on plan swapped.
    const p = { elevations: elevationRecords(), context: Object.assign({ offset: ifcOffset() }, modelContext),
                corner: toggleValue('corner-type') || 'mitre',
                trim: !state.sliderDragging && document.getElementById('live-trim').checked };
    for (const k of NUM) p[k] = parseFloat(val(k));
    p.sheathing = document.getElementById('sheathing').checked;
    p.insulation = document.getElementById('insulation').checked;
    p.reveals = document.getElementById('reveals').checked;
    p.set_out_from_openings = document.getElementById('set_out_from_openings').checked;
    p.barriers = document.getElementById('barriers').checked;
    p.cladding_type = toggleValue('cladding-type');
    p.rotate = document.getElementById('rotate').checked;
    p.battens_from_model = document.getElementById('battens_from_model').checked;
    p.match_battens = document.getElementById('match_battens').checked;
    p.seam_full_length = document.getElementById('seam_full_length').checked;
    p.seam_balanced = document.getElementById('seam_balanced').checked;
    p.base_drip = document.getElementById('base_drip').checked;
    p.panels_as_walls = document.getElementById('panels_as_walls').checked;
    p.ifc_schema = ifcSchema();
    p.share_offcuts = document.getElementById('share_offcuts').checked;
    p.plank_orient = toggleValue('plank-orient');
    p.plank_profile = toggleValue('plank-profile') || 'board';
    Object.assign(p, customProfileParams());
    p.seam_deck = val('seam_deck');
    p.seam_metal = val('seam_metal');
    p.counter_batten = val('counter_batten');
    return p;
}

function onTypeChange() {
    syncTypeUI();
    renderElevationList();              // the corner rows say which detail is in force
    updateSliderRange();
    updatePreview();
}

// The sidebar's sections and labels for the cladding type and profile shown.
function syncTypeUI() {
    const panel = toggleValue('cladding-type') === 'panel';
    // The master-lap needs a board to run past the corner, so it is a panel detail.
    // Profile is offered either way: with planks it is the Marley-type corner profile.
    // A standing seam chain has a corner seam in its place, and a flashing strip for Profile.
    const seamNow = seamChain();
    for (const v of ['lap']) {
        const b = document.querySelector('#corner-type .turn-btn[data-value="' + v + '"]');
        const ok = panel || seamNow;
        b.disabled = !ok;
        b.title = ok ? '' : 'Panel cladding only';
        b.style.opacity = ok ? '' : '0.45';
        if (!ok && b.classList.contains('active')) selectToggle('corner-type', 'mitre');
    }
    for (const v of ['lap', 'profile'])
        document.querySelector('#corner-type .turn-btn[data-value="' + v + '"]').textContent = cornerLabel(v);
    document.getElementById('panel-section').style.display = panel ? '' : 'none';
    document.getElementById('plank-section').style.display = panel ? 'none' : '';
    // Standing seam is a plank profile: its own fields for the board's, and its stock length is a tray's
    const seam = !panel && toggleValue('plank-profile') === 'seam';
    const custom = !panel && toggleValue('plank-profile') === 'custom';
    document.getElementById('board-fields').style.display = seam || custom ? 'none' : '';
    document.getElementById('custom-fields').style.display = custom ? '' : 'none';
    if (custom) showCustomProfile();
    document.getElementById('seam-fields').style.display = seam ? '' : 'none';
    document.getElementById('seam-edge-fields').style.display = seam ? '' : 'none';
    // full-length trays: one piece a run, so the stock length and the splices do not apply
    const full = seam && document.getElementById('seam_full_length').checked;
    const lf = document.getElementById('plank-length-fields');
    lf.style.opacity = full ? '0.4' : '';
    lf.querySelectorAll('input').forEach(i => { i.disabled = full; });
    document.getElementById('plank-len-label').innerHTML = seam ? 'Longest tray <span class="unit">mm, about 4000 on a facade</span>'
                                                               : 'Stock length <span class="unit">1800–6000, as supplied</span>';
    // kerf and trim serve both: a panel board is squared on all four edges, a plank length at its ends
    document.getElementById('trim-label').innerHTML = panel ? 'Edge trim <span class="unit">mm off each board edge</span>'
                                                            : 'End trim <span class="unit">mm off each end of a length</span>';
    document.getElementById('plank_splice_val').textContent = splicePiece();
    document.getElementById('batten_centres').readOnly = panel;
    document.getElementById('barrier-gap-field').style.display = document.getElementById('barriers').checked ? '' : 'none';
    updateBarriersButton();
}

function updateSliderRange() {
    const p = getParams();
    let half;
    if (p.cladding_type === 'panel') half = (p.panel_w + p.panel_gap) / 2;
    else if (p.plank_orient === 'vertical' && p.plank_profile === 'seam') half = p.seam_centres / 2;
    else if (p.plank_orient === 'vertical' && p.plank_profile === 'custom')
        half = ((importedProfiles[p.custom_profile] || CUSTOM_LIBRARY[p.custom_profile] || { cover: 174 }).cover) / 2;
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
    document.getElementById('plank_splice_val').textContent = splicePiece();
    if (_numTimer) clearTimeout(_numTimer);
    _numTimer = setTimeout(() => { _numTimer = null; updateSliderRange(); updatePreview(); }, 300);
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
        const out = await pyRun(`
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
        renderGeometry(lodGeometry(result));
        renderOutlines(params.elevations, params.splash);
        const act = state.elevations[state.active];
        renderDimensions(result.dimensions, byName, act && act.result ? act.result.name : null);
        renderDims2D();
        renderChecks(result.checks);
        renderInfo(result.info);
        pruneJoints(result.info);
        // the cards say which faces' seams follow another's, which a build can link by itself
        const seamSig = result.info.map(i => i.seam_from && i.seam_from.lines ? i.elevation + '>' + i.seam_from.name : '').join();
        if (seamSig !== state._seamSig) { state._seamSig = seamSig; renderElevationList(); }
        if (params.trim) scheduleNesting();       // not on slider frames: those are untrimmed
    } catch (err) {
        console.error('preview failed', err);
        if (/fatally failed|Aborted/i.test(err.message || '')) {
            if (await restartEngine()) return updatePreview();
            // the same step killed it again: say where, the one thing that helps
            const where = window._engineStep || window._engineDiedIn;
            if (where) { setStatus('Preview error: the engine crashed in ' + where + ' — ' + err.message, 'busy'); return; }
        }
        setStatus('Preview error: ' + err.message, 'busy');
    }
}

// Dissolved panel joints the grid no longer has in the same place (row heights, panel
// width or offset changed under them) are dropped, never moved onto another panel: the
// engine keeps the ones that still match and counts the rest. One message says how many.
function pruneJoints(infos) {
    let dropped = 0;
    for (const i of infos) {
        const e = state.elevations.find(m => m.result && m.result.name === i.elevation);
        if (!e || !i.hjoints || !i.joints_dropped) continue;
        e.panelJoints = i.panel_joints.map(q => [q.row, q.bay, q.u0, q.u1, q.v]);
        dropped += i.joints_dropped;
    }
    if (dropped) renderElevationList();
    if (dropped) setStatus(`${dropped} dissolved joint${dropped > 1 ? 's' : ''} dropped: the panel grid changed under ${dropped > 1 ? 'them' : 'it'}`, 'busy');
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
        ? `${p.panel_w + p.panel_gap} mm bay`
        : p.plank_profile === 'custom' ? `${(+(importedProfiles[p.custom_profile] || CUSTOM_LIBRARY[p.custom_profile] || {}).cover).toFixed(1)} mm`
        : `${p.plank_lap > 0 ? p.plank_w - p.plank_lap : p.plank_w + p.plank_gap} mm`;
    if (p.cladding_type === 'panel') {
        const bay = p.panel_w + p.panel_gap;
        document.getElementById('batten_centres').value = (bay / Math.ceil(bay / 600)).toFixed(0);
    }
}

// Set out from the model's columns, the battens are fixed, and with horizontal planks or
// panels they are all the offset slider would move. Under vertical planks it still moves
// the planks, so it stays.
// The face this one's upright battens follow, if any: the chain directly above or below it
// on the same wall (cladding_match), as the last build found it.
function matchedTo(e) {
    const info = ((window._lastPreview || {}).info || []).find(i => e.result && i.elevation === e.result.name);
    return (info && info.matched_to) || null;
}

function lockOffsetToModel(i) {
    const to = i && i.matched_to;
    const fixed = !!(i && (i.model_posts || to)) && !(toggleValue('cladding-type') === 'plank' && toggleValue('plank-orient') === 'vertical');
    for (const id of ['offset', 'edit-offset']) {
        const s = document.getElementById(id);
        s.disabled = fixed;
        s.title = !fixed ? '' : to ? `Matched to the battens of ${to.chain || to.name} ${to.side}: move them there, or untick "Match the battens" for this chain`
                                   : "Set out from the model's columns: the battens follow the client's";
    }
    if (fixed) document.getElementById('offset-val').textContent = to ? 'matched' : 'model';
}

function renderInfo(infos) {
    const e = state.elevations[state.active];
    const i = infos.find(x => e && x.elevation === e.name) || infos[0];
    const set = (id, v) => document.getElementById(id).textContent = v;
    if (!i) { ['dim-size', 'dim-centres', 'dim-courses', 'dim-cuts', 'dim-boards'].forEach(id => set(id, '--')); return; }
    set('dim-size', `${Math.round(i.width)} × ${Math.round(i.height)} mm`);
    set('dim-centres', i.model_posts ? `on ${i.model_posts} model columns`
        : i.matched_to ? `matched to ${i.matched_to.chain || i.matched_to.name} ${i.matched_to.side} (${i.matched_to.count})` : `${i.batten_centres.toFixed(0)} mm`);
    lockOffsetToModel(i);
    set('dim-courses', `${i.n_courses} @ ${i.cover.toFixed(0)}`);
    set('dim-cuts', `L ${Math.round(i.closing_cut_left)} · R ${Math.round(i.closing_cut_right)} · T ${Math.round(i.closing_cut_top)}`);
    set('dim-boards', `${i.n_boards} (setting-out only)`);
}

function setStatus(text, cls) {
    const chip = document.getElementById('status-chip');
    chip.textContent = text; chip.className = 'status-chip ' + (cls || '');
}

// ─── SAVED STATE ───
// Save writes every chain, its elevations, the wall surfaces they are attached to (as
// extracted: frame, outline, openings, abutments) and every setting to an XML file
// (cladding_state.py); Load puts them all back. The surfaces travel in the file, so a
// state loads without the IFC and without extracting again. Open the IFC first to see
// the host model: opening a model starts afresh and clears the chains.
const STATE_CHECKS = ['sheathing', 'insulation', 'reveals', 'set_out_from_openings', 'rotate', 'live-trim', 'penetrations', 'share_offcuts', 'battens_from_model', 'panels_as_walls', 'seam_full_length', 'seam_balanced', 'base_drip', 'match_battens', 'barriers'];
const STATE_TOGGLES = ['corner-type', 'cladding-type', 'plank-orient', 'plank-profile'];

function snapshotState() {
    const settings = {};
    for (const k of NUM) settings[k] = parseFloat(val(k));
    for (const k of STATE_CHECKS) settings[k] = document.getElementById(k).checked;
    for (const k of STATE_TOGGLES) settings[k] = toggleValue(k);
    settings.counter_batten = val('counter_batten');
    settings.seam_deck = val('seam_deck');
    settings.seam_metal = val('seam_metal');
    settings.ifc_schema = ifcSchema();
    Object.assign(settings, customProfileParams());
    const elevation = e => ({
        name: e.name, storey: e.storey || null, start: e.start || 0, rev: !!e.rev, link: e.link || null, offset: e.offset || 0,
        cornerLo: e.cornerLo || 0, cornerHi: e.cornerHi || 0, masterLo: !!e.masterLo, masterHi: !!e.masterHi,
        detailLo: e.detailLo || null, detailHi: e.detailHi || null, clipLo: e.clipLo || 0,
        clipHi: e.clipHi === undefined ? null : e.clipHi, cover: e.cover || null,
        panelRows: e.panelRows || null, panelJoints: e.panelJoints || null, postStates: e.postStates || null, staggerJoints: e.staggerJoints || null,
        openingDetails: e.openingDetails || {}, ridge: ridgeRecord(e), valleys: valleyRecords(e),
        seamFrom: e.seamFrom && state.elevations.includes(e.seamFrom) ? e.seamFrom.name : null,
        manual: e.manual || [], disabled: e.disabled || {},
        // the faces it was picked from, for reference: the surface below is what loads
        picks: e.picks.length ? e.picks.map(q => ({ element: (q.mesh && q.mesh.userData.name) || null, faces: q.faces.length,
                                                    normal: q.normal, point: q.point }))
                              : (e.savedPicks || []),
        surface: e.result });
    const chains = state.chains.map(c => ({
        name: c.name, built: !!c.built, topZ: c.topZ, bottomZ: c.bottomZ, edges: Object.assign({}, c.edges),
        settings: c.settings ? Object.assign({}, c.settings) : null,
        datumFrom: c.datumFrom ? c.datumFrom.name : null,
        members: c.members.filter(e => e.result && e.result.ok).map(elevation) }));
    const act = state.elevations[state.active];
    return { app: 'CladForge', saved: new Date().toISOString(),
             model: { file: (state.file && state.file.name) || state.savedModelFile || null, offset: modelOffset.slice(),
                      context: Object.assign({}, modelContext) },
             seq: state.seq, active: act ? act.name : null, settings, chains,
             library: (state.settingsLibrary || []).map(f => ({ file: f.file, chains: f.chains })) };
}

// A state file is named after the chains in it, so it says what it holds: one chain's own
// name, two or three joined, or the first and how many more. Chains never named (a state
// from before chains had names) fall back on the dated name every other download has.
function stateFileName(snap) {
    const names = snap.chains.filter(c => c.members.length).map(c => c.name);
    if (!names.length || names.every(n => /^Chain \d+$/.test(n))) return downloadName('state.xml');
    const label = names.length <= 3 ? names.join(' + ') : `${names[0]} + ${names.length - 1} more`;
    return label.replace(/[^A-Za-z0-9 _.,()+-]/g, ' ').replace(/\s+/g, ' ').trim() + '.state.xml';
}

async function saveState() {
    if (!pyReady) { setStatus('The engine is still loading — try again in a moment', 'busy'); return; }
    const snap = snapshotState();
    if (!snap.chains.some(c => c.members.length)) { setStatus('Nothing to save yet: pick a wall face first', 'busy'); return; }
    const skipped = state.elevations.filter(e => !(e.result && e.result.ok)).length;
    try {
        pyodide.globals.set('_state_json', JSON.stringify(snap));
        const xml = await enginePy(`
import json as _json
from cladding_state import to_xml as _to_xml
_to_xml(_json.loads(_state_json))`);
        const url = URL.createObjectURL(new Blob([xml], { type: 'application/xml' })), a = document.createElement('a');
        a.href = url; a.download = stateFileName(snap); document.body.appendChild(a); a.click();
        document.body.removeChild(a); URL.revokeObjectURL(url);
        const n = snap.chains.reduce((k, c) => k + c.members.length, 0);
        setStatus(`State saved: ${snap.chains.filter(c => c.members.length).length} chain(s), ${n} elevation(s)`
                  + (skipped ? ` · ${skipped} not yet extracted left out` : ''), 'ready');
    } catch (err) {
        console.error(err);
        setStatus('Save failed: ' + err.message, 'busy');
    }
}

// One state file or several. Load state replaces the job with the first and adds the rest
// to it; Add states adds every one to the job that is open (restoreState, add).
async function onStateChosen(input, add) {
    if (!input.files.length) return;
    const files = Array.from(input.files);
    input.value = '';                        // choosing the same file again still loads it
    const notes = [];
    let ok = 0;
    for (const [i, file] of files.entries()) {
        const merging = add || i > 0;
        const done = await loadState(await file.text(), file.name, merging);
        if (!done) { if (files.length === 1) return; notes.push(`${file.name}: not loaded`); continue; }
        ok++;
        if (merging && done.note) notes.push(`${file.name}: ${done.note}`);
    }
    if (files.length > 1 || add)
        setStatus(`${add ? 'Added' : 'Loaded'} ${ok} of ${files.length} state file${files.length > 1 ? 's' : ''}: `
                  + `${state.chains.length} chain(s), ${state.elevations.length} elevation(s) in the job`
                  + (notes.length ? ' · ' + notes.join(' · ') : ''), notes.some(n => /not loaded/.test(n)) ? 'busy' : 'ready');
}

async function loadState(xml, fileName, add) {
    if (!pyReady) { setStatus('The engine is still loading — try again in a moment', 'busy'); return false; }
    let res;
    try {
        pyodide.globals.set('_state_xml', xml);
        res = JSON.parse(await enginePy(`
import json as _json
from cladding_state import load as _load
_json.dumps(_load(_state_xml))`));
    } catch (err) { res = { ok: false, error: err.message }; }
    if (!res.ok) { setStatus('Not loaded: ' + res.error, 'busy'); return false; }
    const merged = restoreState(res.state, !!add && state.elevations.length > 0, fileName);
    await updatePreview();
    if (!allMeshes.length) fitCameraTo(cladGroup.children.length ? cladGroup : outlineGroup);
    const n = state.elevations.length;
    if (merged) {
        const parts = [`${merged.elevations} elevation(s) in ${merged.chains} chain(s) added`];
        if (merged.renamed.length) parts.push(`renamed ${merged.renamed.join(', ')}`);
        if (merged.skipped.length) parts.push(`${merged.skipped.length} already in the job, left out (${merged.skipped.join(', ')})`);
        if (merged.otherModel) parts.push(`saved from ${merged.otherModel}: placed by its coordinates`);
        if (merged.near.length) parts.push(nearNote(merged.near, res.state.saved, fileName));
        merged.note = parts.join(' · ');
        setStatus(`Added ${fileName || 'state'}: ${merged.note}`, merged.near.length ? 'busy' : 'ready');
        return merged;
    }
    setStatus(`Loaded ${fileName || 'state'}: ${state.chains.length} chain(s), ${n} elevation(s)`
              + (allMeshes.length ? '' : ' · no host model open'), 'ready');
    return {};
}

// What to say of faces left out because the job has nearly the same ones: which chains, and
// which of the two is the later save, so the latest file is the one that gets used.
function nearNote(near, saved, fileName) {
    const pairs = [...new Set(near.map(q => `${q.chain} ≈ ${q.inJob.name}`))];
    const mine = near[0].inJob.source, theirs = saved ? new Date(saved) : null, ours = mine && mine.saved ? new Date(mine.saved) : null;
    const file = fileName || 'the file being added', job = mine && mine.file ? mine.file : 'the job as it stands';
    let advice;
    if (!ours || isNaN(ours)) advice = `the job's own has not come from a saved file, so it is the latest: keep it, or save it over ${file}`;
    else if (!theirs || isNaN(theirs)) advice = `${file} does not say when it was saved: check which is the latest before using it`;
    else if (theirs > ours) advice = `${file} is the later save (${savedWhen(saved)}, against ${savedWhen(mine.saved)} for ${job}): use it instead. Remove the chain from the job, or Load state with ${file}`;
    else advice = `${job} is the later save (${savedWhen(mine.saved)}, against ${savedWhen(saved)}): keep it and leave ${file} out`;
    return `WARNING: ${near.length} face(s) left out because the job has nearly the same surfaces, but not the same (${pairs.join(', ')}). `
        + `This looks like two saves of one chain. Use the latest file: ${advice}`;
}

// A saved state's job-wide settings (keyed by form field) as a chain's own settings: what a
// chain from a state saved before chains had their own takes with it when it is added.
function chainSettingsOf(s) {
    if (!s) return null;
    const out = {};
    for (const k of NUM) if (typeof s[k] === 'number' && isFinite(s[k])) out[k] = s[k];
    for (const k of CHAIN_CHECKS) if (typeof s[k] === 'boolean') out[k] = s[k];
    const toggles = { 'corner-type': 'corner', 'cladding-type': 'cladding_type', 'plank-orient': 'plank_orient', 'plank-profile': 'plank_profile' };
    for (const [from, to] of Object.entries(toggles)) if (s[from]) out[to] = s[from];
    for (const k of ['seam_deck', 'seam_metal', 'counter_batten', 'custom_profile', 'custom_section']) if (s[k]) out[k] = s[k];
    return Object.keys(out).length ? out : null;
}

// Two surfaces that are nearly the same wall face but not the same: on one plane, and one
// covering at least half of the other. A chain saved, changed and saved again comes back
// like this: adding both would clad the wall twice.
function nearSurface(a, b) {
    if (!a || !b || !a.frame || !b.frame || sameSurface(a, b)) return false;
    const dot = (p, q) => p.reduce((t, x, k) => t + x * q[k], 0);
    if (dot(a.frame.n, b.frame.n) < 0.999 || dot(a.frame.u, b.frame.u) < 0.99) return false;
    const off = b.frame.origin.map((x, k) => x - a.frame.origin[k]);
    if (Math.abs(dot(off, a.frame.n)) > 5) return false;
    const v = a.frame.v || [0, 0, 1], u0 = dot(off, a.frame.u), v0 = dot(off, v);
    const lap = (p0, p1, q0, q1) => Math.max(0, Math.min(p1, q1) - Math.max(p0, q0));
    const shared = lap(0, a.width, u0, u0 + b.width) * lap(0, a.height, v0, v0 + b.height);
    return shared >= 0.5 * Math.min(a.width * a.height, b.width * b.height);
}
const savedWhen = iso => { const d = new Date(iso); return isNaN(d) ? '' : d.toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }); };

// Two surfaces are the same wall face: the same place, direction and size.
function sameSurface(a, b) {
    if (!a || !b || !a.frame || !b.frame) return false;
    const d = Math.hypot(...a.frame.origin.map((x, k) => x - b.frame.origin[k]));
    const dot = a.frame.u.reduce((t, x, k) => t + x * b.frame.u[k], 0);
    const facing = a.frame.n.reduce((t, x, k) => t + x * b.frame.n[k], 0);     // two slopes can share a foot
    return d < 2 && dot > 0.999 && facing > 0.999 && Math.abs(a.width - b.width) < 2 && Math.abs(a.height - b.height) < 2;
}

// *add*: the state's chains join the job that is open instead of replacing it. Each keeps
// its own settings (its own, or the settings its state was saved with); the job's settings
// and the model stay as they are. A chain or elevation whose name is taken gets the next
// free one, and a wall face the job already has is left out, so the same state added twice
// adds nothing. Returns what was added ({chains, elevations, renamed, skipped}), or null
// for a plain load.
function restoreState(st, add, fileName) {
    closeEditWidget();
    if (in2D()) { exit2D(); update2DButton(); }
    const merged = add ? { chains: 0, elevations: 0, renamed: [], skipped: [], near: [], otherModel: null } : null;
    const source = { file: fileName || null, saved: st.saved || null };       // where each chain it brings came from
    // the chains listed to copy settings from: the job's own, or added to those already listed
    const lib = (st.library || []).filter(f => f && f.file && Array.isArray(f.chains));
    if (add) state.settingsLibrary = (state.settingsLibrary || []).filter(f => !lib.some(g => g.file === f.file)).concat(lib);
    else state.settingsLibrary = lib;
    if (!add) {
        state.elevations.forEach(e => e.highlights.forEach(h => highlightGroup.remove(h)));
        state.heals = []; state.healNo = new Set(); renderHeals([]);      // a new job: nothing offered, nothing declined
        state.elevations = []; state.chains = []; state.active = -1;
    }
    // Surfaces are stored in scene coordinates, which sit on the model offset they were
    // saved with. With no model open that offset comes back too, so exports land on the
    // host model's coordinates; with one open (or a job already loaded, when adding), the
    // surfaces move onto its offset.
    const saved = (st.model && st.model.offset) || [0, 0, 0];
    let d = [0, 0, 0];
    if (allMeshes.length || add) d = saved.map((x, k) => x - modelOffset[k]);
    else { modelOffset = saved.slice(); modelContext = (st.model && st.model.context) || {}; }
    const s = st.settings || {};
    if (add) {
        const here = (state.file && state.file.name) || state.savedModelFile, there = st.model && st.model.file;
        if (here && there && here !== there) merged.otherModel = there;
        if (s.custom_section) addCustomProfile(s.custom_section, false);
    } else {
        state.savedModelFile = st.model && st.model.file;
        for (const k of NUM) if (s[k] !== undefined && s[k] !== null) document.getElementById(k).value = s[k];
        for (const k of STATE_CHECKS) if (typeof s[k] === 'boolean') document.getElementById(k).checked = s[k];
        for (const k of STATE_TOGGLES) if (s[k]) selectToggle(k, s[k]);
        if (s.counter_batten) document.getElementById('counter_batten').value = s.counter_batten;
        if (s.seam_deck) document.getElementById('seam_deck').value = s.seam_deck;
        if (s.seam_metal) document.getElementById('seam_metal').value = s.seam_metal;
        if (s.ifc_schema) { document.getElementById('ifc_schema').value = s.ifc_schema; onIfcSchema(); }
        if (s.custom_section) addCustomProfile(s.custom_section, true);
        else if (s.custom_profile && CUSTOM_LIBRARY[s.custom_profile]) document.getElementById('custom_profile').value = s.custom_profile;
    }
    const label = k => 'Elevation ' + String.fromCharCode(65 + (k % 26)) + (k >= 26 ? Math.floor(k / 26) : '');
    const freeElevation = () => { let k = 0; while (state.elevations.some(m => m.name === label(k))) k++; return label(k); };
    const byName = {};
    for (const c of st.chains || []) {
        const chain = { name: c.name, members: [], length: 0, built: !!c.built,
                        topZ: c.topZ === null || c.topZ === undefined ? null : c.topZ + d[2],
                        bottomZ: c.bottomZ === null || c.bottomZ === undefined ? null : c.bottomZ + d[2],
                        edges: Object.assign({}, EDGE_DEFAULTS, c.edges || {}), source,
                        settings: c.settings ? Object.assign({}, c.settings) : (add ? chainSettingsOf(s) : null) };
        for (const m of c.members || []) {
            const surface = m.surface;
            if (!surface || !surface.ok) continue;
            surface.frame.origin = surface.frame.origin.map((x, k) => x + d[k]);
            let name = m.name;
            if (add) {
                const twin = state.elevations.find(x => sameSurface(x.result, surface));
                if (twin) { merged.skipped.push(`${m.name.replace('Elevation ', '')} = ${twin.name.replace('Elevation ', '')}`); continue; }
                // Nearly the same face as one the job has, but not the same: an earlier or later
                // save of the same chain. Not added on top of it; the newer of the two is named.
                const close = state.elevations.find(x => x.result && x.result.ok && nearSurface(x.result, surface));
                if (close) { merged.near.push({ face: m.name, chain: c.name, twin: close.name, inJob: close.chain }); continue; }
                if (state.elevations.some(x => x.name === name)) {
                    name = freeElevation();
                    merged.renamed.push(`${m.name.replace('Elevation ', '')} → ${name.replace('Elevation ', '')}`);
                }
                surface.name = name;              // the engine and the views know a face by this
                merged.elevations++;
            }
            const e = { name, picks: [], savedPicks: m.picks || [], restored: true, result: surface,
                        manual: m.manual || [], disabled: m.disabled || {}, storey: m.storey || null, highlights: [], chain,
                        start: m.start || 0, rev: !!m.rev, link: m.link || null, offset: m.offset || 0,
                        cornerLo: m.cornerLo || 0, cornerHi: m.cornerHi || 0, masterLo: !!m.masterLo, masterHi: !!m.masterHi,
                        detailLo: m.detailLo || null, detailHi: m.detailHi || null,
                        // a corner's clip, never more than its tolerance in from the end (see extractOnce): a stale one is dropped
                        clipLo: (m.clipLo || 0) > 401 ? 0 : (m.clipLo || 0),
                        clipHi: m.clipHi === undefined || m.clipHi === null || surface.width - m.clipHi > 401 ? null : m.clipHi, cover: m.cover || null,
                        panelRows: m.panelRows || null, panelJoints: m.panelJoints || null,
                        postStates: m.postStates || null, staggerJoints: m.staggerJoints || null, openingDetails: m.openingDetails || {} };
            chain.members.push(e);
            state.elevations.push(e);
            byName[m.name] = e;                   // by its name in the file: what datumFrom says
        }
        // A ridge names its partner; both slopes have to have come in for it to stand.
        for (const m of c.members || []) {
            const e = byName[m.name], other = m.ridge && byName[m.ridge.with];
            if (e && e.chain === chain && other && other.chain === chain) e.ridge = Object.assign({}, m.ridge, { partner: other });
        }
        for (const e of chain.members) if (e.ridge && !(e.ridge.partner.ridge && e.ridge.partner.ridge.partner === e)) e.ridge = null;
        chain.datumFrom = byName[c.datumFrom] && byName[c.datumFrom].chain === chain ? byName[c.datumFrom] : null;
        if (!chain.members.length) continue;      // every face of it left out: no chain, and no name used up
        if (add && state.chains.some(x => x.name === chain.name)) {
            // a numbered chain takes the next number; a named one keeps its name with a number after it
            if (/^Chain \d+$/.test(chain.name)) do { chain.name = 'Chain ' + (++state.seq); } while (state.chains.some(x => x.name === chain.name));
            else chain.name = freeChainName(chain.name, chain);
            merged.renamed.push(`${c.name} → ${chain.name}`);
        }
        relayoutChain(chain);
        state.chains.push(chain);
        if (add) merged.chains++;
    }
    // A valley names its partner, which may be in another chain: both slopes have to have come in.
    for (const c of st.chains || []) for (const m of c.members || []) {
        const e = byName[m.name];
        if (e) e.valleys = (m.valleys || []).filter(v => byName[v.with]).map(v => Object.assign({}, v, { partner: byName[v.with] }));
        if (e) e.seamFrom = (m.seamFrom && byName[m.seamFrom] !== e && byName[m.seamFrom]) || null;
    }
    state.seq = Math.max(add ? state.seq : 0, st.seq || 0, state.chains.length);
    // A state saved before chains had their own settings: each chain takes the job's.
    const job = formSettings();
    for (const ch of state.chains) if (!ch.settings) ch.settings = Object.assign({}, job);
    if (add) {                         // the job stays on the face it was on
        renderElevationList();
        if (state.active < 0 && state.elevations.length) setActive(0);
        return merged;
    }
    state.formChain = null;            // setActive shows the active face's chain
    onTypeChange();
    const active = state.elevations.findIndex(e => e.name === st.active);
    setActive(active >= 0 ? active : state.elevations.length ? 0 : -1);
    return null;
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
        state.heals = []; state.healNo = new Set(); renderHeals([]);      // a new job: nothing offered, nothing declined
        state.elevations = []; state.chains = []; state.active = -1; state.seq = 0; renderElevationList();
        const summary = await loadIFC(file, t => setStatus(t, 'busy'), force);
        state.model = summary;
        renderHidden();                     // a new model starts with nothing hidden
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
// *blob*: one file, or several as [{blob, tag}] (an IFC at each level of development): they
// share a name and number, each with its tag before the extension.
function showReminder(blob, ext) { _pendingBlob = blob; _pendingExt = ext; document.getElementById('download-reminder').classList.add('open'); }
function confirmDownload() {
    document.getElementById('download-reminder').classList.remove('open');
    if (!_pendingBlob) return;
    const files = Array.isArray(_pendingBlob) ? _pendingBlob : [{ blob: _pendingBlob, tag: '' }];
    const name = downloadName(_pendingExt), dot = name.lastIndexOf('.');
    _pendingBlob = null;
    window._lastDownloads = files.map(f => f.tag ? name.slice(0, dot) + '_' + f.tag + name.slice(dot) : name);
    files.forEach((f, k) => setTimeout(() => {          // spaced out, or a browser keeps only the last
        const url = URL.createObjectURL(f.blob), a = document.createElement('a');
        a.href = url; a.download = window._lastDownloads[k]; document.body.appendChild(a); a.click();
        document.body.removeChild(a); setTimeout(() => URL.revokeObjectURL(url), 2000);
    }, 400 * k));
}

function busy(btn, on, label) {
    btn.disabled = on; btn.classList.toggle('btn-generating', on);
    btn.innerHTML = on ? '<span class="btn__text">' + label + '</span>' : btn.dataset.label;
}

// ─── LEVEL OF DEVELOPMENT ───
// The same build shown at LOD 100 (one plain mass per face, exact at its extremities), 200 (the
// boards, battens and layers, plain: no seam folds or junction metalwork) or 300 (everything).
// The engine tags every mesh with the lowest level it is there at and sends LOD 100's sheets
// beside them (cladding_lod.py: this is its at_lod), so switching redraws without rebuilding.
// The cutting list, the checks and the setting-out always come from the full build.
const LODS = [100, 200, 300];
let viewLod = 300;
function lodGeometry(result, lod) {
    lod = lod || viewLod;
    if (!result) return [];
    if (lod === 100) return result.masses || [];
    if (lod === 200) return (result.geometry || []).filter(m => (m.lod || 300) <= 200).map(m =>
        m.ifc_type === 'seam' && m.vshift ? Object.assign({}, m, { vshift: null })
        : (m.parts || m.section_ring) ? Object.assign({}, m, { parts: null, section_ring: null }) : m);
    return result.geometry || [];
}
function setViewLod(lod) {
    viewLod = LODS.includes(+lod) ? +lod : 300;
    document.querySelectorAll('#lod-switch button').forEach(b => b.classList.toggle('active', +b.dataset.lod === viewLod));
    if (window._lastPreview) {
        renderGeometry(lodGeometry(window._lastPreview));
        if (typeof tintPanels === 'function') tintPanels();
    }
}
// What the IFC is written at: one level, or all three (three files).
function ifcLods() {
    const v = document.getElementById('ifc_lod').value;
    return v === 'all' ? LODS.slice() : [LODS.includes(+v) ? +v : 300];
}

// The schema the IFC is written in (ifc_generator.IFC_SCHEMA_CHOICES): the button says which.
function ifcSchema() {
    const v = document.getElementById('ifc_schema').value;
    return ['IFC4X3', 'IFC4', 'IFC2X3'].includes(v) ? v : 'IFC4X3';
}
function onIfcSchema() {
    const btn = document.getElementById('ifc-btn'), label = 'Download ' + ifcSchema().replace('2X3', '2x3');
    btn.dataset.label = label;
    if (!btn.disabled) btn.textContent = label;
}

async function downloadIFC() {
    const params = getParams(); params.trim = true;
    // a cross at the IFC's origin, to snap the cladding back onto the host model by (ifc_generator._reference_point)
    params.context = Object.assign({}, params.context || {}, { reference_point: true });
    if (!params.elevations.length) { alert('Pick some faces and build the chain first (press Enter).'); return; }
    const btn = document.getElementById('ifc-btn');
    busy(btn, true, `Generating ${ifcSchema().replace('2X3', '2x3')}…`);
    try {
        await ensureIfcOpenShell(btn);
        pyodide.globals.set('_params_json', JSON.stringify(params));
        // One build, written at each level asked for (cladding_lod.at_lod): a file per level.
        const lods = ifcLods(), files = [];
        for (const lod of lods) {
            if (lods.length > 1) busy(btn, true, `Generating LOD ${lod}…`);
            pyodide.globals.set('_ifc_lod', lod);
            const proxy = await enginePy(`
import json as _json, os as _os
from cladding_preview import generate_preview as _gp, at_lod as _at_lod
from ifc_generator import meshes_to_ifc as _to_ifc
_p = _json.loads(_params_json)
if int(_ifc_lod) == ${lods[0]}:
    _o = _gp(_p)
_path = _to_ifc(_at_lod(_o["geometry"], _o["masses"], int(_ifc_lod)), dict(_p, lod=int(_ifc_lod)), _o["info"])
with open(_path, 'rb') as _f:
    _data = _f.read()
_os.unlink(_path)
_data`);
            files.push({ blob: new Blob([proxy.toJs()], { type: 'application/x-step' }),
                         tag: lods.length > 1 || lod !== 300 ? 'LOD' + lod : '' });
            if (proxy.destroy) proxy.destroy();
        }
        showReminder(files, 'ifc');
    } catch (err) { alert('IFC export failed: ' + err.message); }
    finally { busy(btn, false); }
}

// One solid per element in the IFC (cladding_union.py): a tray with its seam folds and
// drip, a flashing with its end pieces, unioned by Manifold's WebAssembly build before the
// file is written. Offered to the Python engine as globalThis._cfUnion([[verts, tris], ...]):
// flat vertex and triangle lists per closed piece in, their union out ({v, t, np}), or null
// if a piece is not a closed solid or the union fails. If the module will not load, the
// export carries on and writes the pieces as separate solids, as before.
const MANIFOLD_URL = 'https://cdn.jsdelivr.net/npm/manifold-3d@3.5.4/manifold.js';
async function ensureManifold() {
    if (globalThis._cfUnion !== undefined) return;
    globalThis._cfUnion = null;
    try {
        const wasm = await (await import(MANIFOLD_URL)).default();
        wasm.setup();
        const { Manifold, Mesh } = wasm;
        globalThis._cfUnion = pieces => {
            const made = [];
            let whole = null;
            try {
                for (const [v, t] of pieces) {
                    const mesh = new Mesh({ numProp: 3, vertProperties: Float32Array.from(v), triVerts: Uint32Array.from(t) });
                    mesh.merge();
                    const solid = new Manifold(mesh);                 // throws if the piece is not a closed solid
                    made.push(solid);
                    if (solid.status() !== 'NoError') return null;
                }
                whole = Manifold.union(made);
                const out = whole.getMesh();
                return { v: Array.from(out.vertProperties), t: Array.from(out.triVerts), np: out.numProp };
            } catch (err) {
                return null;
            } finally {
                made.forEach(m => m.delete());
                if (whole) whole.delete();
            }
        };
    } catch (err) {
        console.warn('Manifold did not load: the IFC writes each piece as its own solid.', err);
    }
}

async function ensureIfcOpenShell(btn) {
    await ensureManifold();
    if (ifcReady) return;
    busy(btn, true, 'Installing IfcOpenShell (one-off, ~40 MB)…');
    await pyodide.loadPackage(['numpy']);
    await enginePy(`
import micropip
await micropip.install(["lark", "isodate", "python-dateutil", "typing_extensions"])
await micropip.install("https://ifcopenshell.github.io/wasm-wheels/ifcopenshell-0.8.5-cp313-cp313-pyodide_2025_0_wasm32.whl")
import ifc_generator`);
    ifcReady = true;
}

// ─── BOARDS AND WASTE ───
// Every panel of every built chain is packed onto stock boards (cladding_nesting.py),
// each elevation on its own so every board is cut for one elevation; the readout says
// how many more that takes than packing the whole job together. The quick check (16
// strategies) runs when Update is pressed; with "update automatically" ticked, after each
// finished rebuild, debounced like a typed field and never on a slider frame. On a big
// model packing takes seconds, so by default a rebuild only marks the list out of date.
// Optimise boards runs the full search on demand; the cutting plan and the cutting run
// always work out their own.
let _nestTimer = null, _nestSeq = 0, autoNesting = false;
try { autoNesting = localStorage.getItem('cladforge.autoNesting') === '1'; } catch (err) { /* private window */ }
function setAutoNesting(on) {
    autoNesting = !!on;
    try { localStorage.setItem('cladforge.autoNesting', autoNesting ? '1' : '0'); } catch (err) { /* private window */ }
    const box = document.getElementById('wr-auto');
    if (box) box.checked = autoNesting;
    if (!autoNesting && _nestTimer) { clearTimeout(_nestTimer); _nestTimer = null; }   // nothing queued runs after it is switched off
    if (autoNesting && window._planStale) runNesting(false);
}
function scheduleNesting() {
    if (_nestTimer) clearTimeout(_nestTimer);
    if (!autoNesting) { markPlanStale(); return; }
    _nestTimer = setTimeout(() => { _nestTimer = null; runNesting(false); }, 300);
}
// The model has changed since the list was worked out (or it never was): say so, keep the
// last numbers greyed, and offer to work it out.
function markPlanStale() {
    const box = document.getElementById('waste-readout');
    if (!pyReady || !window._lastPreview || !state.chains.some(c => c.built)) {
        box.style.display = 'none';
        document.getElementById('run-open').style.display = 'none';
        window._lastPlan = null; window._planStale = false;
        tintPanels();
        return;
    }
    window._planStale = true;
    _nestSeq++;                                         // a packing still on its way is for the model as it was
    box.style.display = '';
    document.getElementById('run-open').style.display = '';
    box.classList.add('stale');
    box.classList.toggle('empty', !window._lastPlan);
    if (!window._lastPlan) document.getElementById('wr-title').textContent = jobRecords().title + ' · cutting list';
    document.getElementById('wr-stale-text').textContent = window._lastPlan ? 'Out of date: the model has changed' : 'Not worked out yet';
    document.getElementById('wr-update').textContent = window._lastPlan ? 'Update' : 'Work it out';
    document.getElementById('wr-stale').style.display = '';
    tintPanels();
}
async function updateCuttingList() {
    const btn = document.getElementById('wr-update');
    btn.disabled = true; btn.textContent = 'Packing…';
    await new Promise(r => setTimeout(r, 30));          // let the label paint before Python blocks
    try { await runNesting(false); } finally { btn.disabled = false; }
}

// The cutting job: the active chain and every other built chain cut from the same material
// (cladding, profile and stock sizes alike), packed together so they share offcuts. A chain
// in another material has its own job, shown when one of its faces is active.
const MATERIAL_KEYS = ['cladding_type', 'plank_profile', 'custom_profile', 'board_w', 'board_h', 'panel_t', 'plank_w', 'plank_t',
                       'plank_len', 'seam_centres', 'seam_t'];
function materialOf(c) {
    const s = c.settings || {};
    return MATERIAL_KEYS.map(k => s.cladding_type === 'panel' && /^(plank|seam)/.test(k) ? '' :
                                  s.cladding_type !== 'panel' && /^(board_|panel_)/.test(k) ? '' : String(s[k])).join('|');
}
function jobRecords() {
    const built = state.chains.filter(c => c.built && c.members.some(m => m.result && m.result.ok));
    const act = activeChain(), here = act && built.includes(act) ? act : built[0];
    const chains = here ? built.filter(c => materialOf(c) === materialOf(here)) : [];
    const names = chains.flatMap(c => c.members.filter(m => m.result && m.result.ok).map(m => m.result.name));
    const area = (window._lastPreview.info || []).filter(i => names.includes(i.elevation)).reduce((a, i) => a + (i.clad_area || 0), 0);
    const title = chains.length === 1 ? chains[0].name
        : chains.length === built.length ? `All ${chains.length} chains` : chains.map(c => c.name).join(' + ');
    return { names, area, title };
}

async function runNesting(deep) {
    const box = document.getElementById('waste-readout');
    if (!pyReady || !window._lastPreview || !state.chains.some(c => c.built)) {
        box.style.display = 'none';
        document.getElementById('run-open').style.display = 'none';
        window._lastPlan = null;
        tintPanels();
        return null;
    }
    const seq = ++_nestSeq, job = jobRecords();
    try {
        pyodide.globals.set('_nest_json', JSON.stringify({ names: job.names, area: job.area, deep: !!deep }));
        const out = await enginePy(`
import json as _json, time as _time
from cladding_nesting import chain_plan as _cp
from cladding_preview import parse_for as _pf
_n = _json.loads(_nest_json)
_t0 = _time.perf_counter()
_plan = _cp(_o["geometry"], _pf(_p, _n["names"]), _n["names"], _n["area"], _n["deep"])
_plan["ms"] = round((_time.perf_counter() - _t0) * 1000)
_plan.pop("boards")
_json.dumps(_plan)`);
        if (seq !== _nestSeq) return null;
        const plan = JSON.parse(out);
        plan.title = job.title;
        window._lastPlan = plan;
        window._planStale = false;
        box.classList.remove('stale', 'empty');
        document.getElementById('wr-stale').style.display = 'none';
        renderReadout(plan);
        return plan;
    } catch (err) {
        console.warn('packing failed', err);
        box.style.display = 'none';
        document.getElementById('run-open').style.display = 'none';
        return null;
    }
}

async function optimiseBoards() {
    const btn = document.getElementById('wr-optimise');
    btn.disabled = true; btn.textContent = 'Optimising…';
    await new Promise(r => setTimeout(r, 30));          // let the label paint before Python blocks
    const before = window._lastPlan;
    const plan = await runNesting(true);
    btn.disabled = false; btn.textContent = 'Optimise boards';
    if (plan) setStatus(`Optimised: ${plan.n_boards} board${plan.n_boards === 1 ? '' : 's'}, ${(100 * plan.waste).toFixed(1)}% waste`
        + (before && !before.deep && before.n_boards > plan.n_boards ? ` — ${before.n_boards - plan.n_boards} fewer than the quick check` : '')
        + ` (${plan.strategies} strategies, ${(plan.ms / 1000).toFixed(1)} s)`, 'ready');
}

function renderReadout(plan) {
    const box = document.getElementById('waste-readout'), set = (id, v) => document.getElementById(id).textContent = v;
    box.style.display = '';
    document.getElementById('run-open').style.display = '';
    const plank = plan.kind === 'plank';
    const seam = plank && toggleValue('plank-profile') === 'seam';
    set('wr-title', `${plan.title || 'Panels'} · ${seam ? 'standing seam' : plank ? 'planks' : 'panels'} · ${plan.deep ? 'optimised' : 'quick check'}`);
    set('wr-area', `${plan.clad_area.toFixed(1)} m²`);
    set('wr-boards-label', seam ? 'Trays' : plank ? 'Lengths' : 'Boards');
    set('wr-boards', plank ? `${plan.n_boards} of ${plan.board[0]}` : `${plan.n_boards} of ${plan.board[0]} × ${plan.board[1]}`);
    // Packed per elevation; the pooled count shows what keeping elevations apart costs.
    const extra = plan.n_boards - (plan.whole_job_boards || plan.n_boards);
    const unit = plan.kind === 'plank' ? 'length' : 'board';
    set('wr-lower', `lower bound ${plan.lower_bound}`
        + (plan.share_offcuts && plan.saved > 0 ? ` · ${plan.saved} saved with offcuts` : '')
        + (extra > 0 ? ` · +${extra} ${plan.share_offcuts ? 'over packing it all together' : 'kept per elevation'}` : ''));
    document.getElementById('wr-lower').title = (plan.share_offcuts
        ? `Each elevation cuts what it can from the offcuts the elevations before it leave: ${plan.from_offcuts} piece${plan.from_offcuts === 1 ? '' : 's'}, ${plan.saved} ${unit}${plan.saved === 1 ? '' : 's'} fewer to buy.`
        : `Each elevation is packed on its own, so every ${unit} is cut for one elevation.`)
        + (extra > 0 ? ` Packing the whole job together would need about ${plan.whole_job_boards}.` : '');
    const w = document.getElementById('wr-waste');
    w.textContent = `${(100 * plan.waste).toFixed(1)}%`;
    box.classList.toggle('over', plan.waste > 0.05);          // red above 5%
    const notes = [];
    if (plan.oversize.length) notes.push(plank ? `${plan.oversize.length} piece(s) longer than the stock less trim`
                                               : `${plan.oversize.length} panel(s) larger than the board`);
    if (plan.capped) notes.push(`only the first ${plan.n_pieces} pieces packed`);
    if (plan.trim) notes.push(`${plan.trim} mm ${plank ? 'end' : 'edge'} trim`);
    set('wr-note', notes.join(' · '));
    // Cut sizes that divide the board exactly, kerf and trim allowed for: where the design
    // can move, these are the sizes that waste nothing.
    const f = plan.friendly;
    set('wr-sizes', !f ? '' : plank ? `Divides the length: ${f.along.join(' · ')}` : `Divides the board: ${f.across.join(' · ')} × ${f.along.join(' · ')}`);
    document.getElementById('wr-sizes').title = plank ? 'Piece lengths that divide a stock length with nothing over, kerf and end trim allowed for'
        : 'Cut widths (across) and heights (along) that divide the board with nothing over, kerf and edge trim allowed for';
    document.getElementById('wr-optimise').style.display = plan.deep ? 'none' : '';
    // Optimise splices is for planks; its proposals are for the setting-out they were
    // worked out on, so a rebuild clears them.
    document.getElementById('wr-splice-row').style.display = plank ? '' : 'none';
    document.getElementById('wr-splices').innerHTML = '';
    box.title = `Packed ${plan.heuristic}, kerf ${plan.kerf} mm${plan.rotate ? ', rotation allowed' : ''}, `
        + `${plan.strategies} strateg${plan.strategies === 1 ? 'y' : 'ies'}, ${plan.ms} ms`;
    tintPanels();
}

// The cutting plan exactly as the download makes it: the export geometry (trimmed at
// openings) and the full search. The cutting run plays this same plan, so what it shows
// is what the DXF says. asDxf: the DXF text, otherwise the plan with its boards.
async function cuttingPlan(asDxf) {
    const params = getParams(); params.trim = true;
    const job = jobRecords();
    pyodide.globals.set('_params_json', JSON.stringify(params));
    pyodide.globals.set('_job_json', JSON.stringify(job));
    pyodide.globals.set('_as_dxf', !!asDxf);
    const out = await enginePy(`
import json as _json
from cladding_preview import generate_preview as _gp, parse_for as _pf
from cladding_nesting import chain_plan as _cp, plan_dxf as _pd
_xp = _json.loads(_params_json)
_job = _json.loads(_job_json)
_xplan = _cp(_gp(_xp)["geometry"], _pf(_xp, _job["names"]), _job["names"], _job["area"], True)
_pd([(_job["title"], _xplan)]) if _as_dxf else _json.dumps(_xplan)`);
    if (asDxf) return out;
    const plan = JSON.parse(out);
    plan.title = job.title;
    return plan;
}

// ─── OPTIMISE SPLICES ───
// Planks only: the Splices slider tried from Fewer to More in 10% steps (and where it is
// now), each a full rebuild of the setting-out packed onto stock lengths by the quick
// check (cladding_preview.splice_trial). Stock length and shortest piece stay as set.
// One trial per call, so the button can say how far it has got. The settings that do
// better than the current one are proposed, each with Apply.
const SPLICE_STEPS = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100];

async function optimiseSplices() {
    if (!pyReady || toggleValue('cladding-type') !== 'plank' || !window._lastPreview) return;
    const btn = document.getElementById('wr-splices-btn'), box = document.getElementById('wr-splices');
    const params = getParams(), job = jobRecords(), current = parseFloat(val('plank_splice')) || 0;
    const settings = [...new Set(SPLICE_STEPS.concat([current]))].sort((a, b) => a - b);
    btn.disabled = true;
    box.innerHTML = '';
    const trials = [];
    try {
        for (let i = 0; i < settings.length; i++) {
            btn.textContent = `Trying splices ${settings[i]}% (${i + 1} of ${settings.length})…`;
            await new Promise(r => setTimeout(r, 20));          // let the label paint before Python blocks
            const tp = Object.assign({}, params, { plank_splice: settings[i] });
            tp.elevations = params.elevations.map(r => job.names.includes(r.name) && r.settings
                ? Object.assign({}, r, { settings: Object.assign({}, r.settings, { plank_splice: settings[i] }) }) : r);
            pyodide.globals.set('_st_json', JSON.stringify({ params: tp,
                                                             names: job.names, area: job.area }));
            trials.push(JSON.parse(await enginePy(`
import json as _json
from cladding_preview import splice_trial as _st
_stj = _json.loads(_st_json)
_json.dumps(_st(_stj["params"], _stj["names"], _stj["area"]))`)));
        }
        pyodide.globals.set('_st_all', JSON.stringify({ trials, current }));
        const ranked = JSON.parse(await enginePy(`
import json as _json
from cladding_preview import rank_splices as _rs
_sta = _json.loads(_st_all)
_json.dumps(_rs(_sta["trials"], _sta["current"]))`));
        renderSpliceOptions(ranked, current);
    } catch (err) {
        setStatus('Optimise splices failed: ' + err.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Optimise splices';
    }
}

// The current setting, then up to three that do better: fewer planks too long for the
// stock, else fewer lengths, else the same with less waste. Fewer lengths can cost more
// pieces to cut and fix, so each offer shows both.
function renderSpliceOptions(ranked, current) {
    const box = document.getElementById('wr-splices');
    const now = ranked.find(t => t.splice === current) || ranked[0];
    const better = t => t.too_long < now.too_long || (t.too_long === now.too_long
        && (t.n_boards < now.n_boards || (t.n_boards === now.n_boards && t.waste < now.waste - 0.0005)));
    const line = t => `<b>${t.splice}%</b> · ${t.piece} mm: ${t.n_boards} lengths`
        + (t === now ? '' : ` (${t.n_boards - now.n_boards >= 0 ? '+' : '−'}${Math.abs(t.n_boards - now.n_boards)})`)
        + ` · ${(100 * t.waste).toFixed(1)}% · ${t.n_pieces} pieces`
        + (t.too_long ? ` · <span class="wr-warn">${t.too_long} too long for the stock</span>` : '');
    // Each offer trades something for something: one no better than an earlier offer on
    // lengths, waste and pieces all at once (the same outcome, or more pieces for nothing)
    // is left out, so the list runs from fewest lengths to fewest pieces.
    const offers = [];
    for (const t of ranked) {
        if (offers.length === 3) break;
        if (t === now || !better(t)) continue;
        if (offers.some(o => o.too_long <= t.too_long && o.n_boards <= t.n_boards
                             && o.waste <= t.waste + 0.0005 && o.n_pieces <= t.n_pieces)) continue;
        offers.push(t);
    }
    box.innerHTML = `<div class="wr-muted">Now ${line(now)}</div>` + (offers.length
        ? offers.map(t => `<div>${line(t)} <button class="mini" onclick="applySplice(${t.splice})">Apply</button></div>`).join('')
        : `<div class="wr-muted">Your setting is the best of the ${ranked.length} tried.</div>`);
    window._spliceOptions = { ranked, current, offers };
}

function applySplice(s) {
    document.getElementById('plank_splice').value = s;
    onNumeric();                                       // rebuilds, repacks, and clears the proposals
    setStatus(`Splices set to ${s}%: the pieces are now set out to ${splicePiece()}`, 'ready');
}

async function downloadCuttingPlan() {
    if (!pyReady) { alert('The engine is still loading.'); return; }
    const params = getParams();
    if (!params.elevations.length) { alert('Build a chain first: the cutting plan packs its boards or planks.'); return; }
    const btn = document.getElementById('plan-btn');
    busy(btn, true, 'Optimising boards…');
    await new Promise(r => setTimeout(r, 30));
    try {
        showReminder(new Blob([await cuttingPlan(true)], { type: 'application/dxf' }), 'cutting-plan.dxf');
    } catch (err) { alert('Cutting plan failed: ' + err.message); }
    finally { busy(btn, false); }
}

async function downloadDXF() {
    if (!pyReady) { alert('The engine is still loading.'); return; }
    const params = getParams(); params.trim = true;
    if (!params.elevations.length) { alert('Pick some faces and build the chain first (press Enter).'); return; }
    const btn = document.getElementById('dxf-btn');
    busy(btn, true, 'Generating DXF…');
    try {
        pyodide.globals.set('_params_json', JSON.stringify(params));
        const dxf = await enginePy(`
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
// Run Python, but never wait forever on a dead engine. A C++ abort inside WASM (GEOS,
// IfcOpenShell, the heap) can leave the call's promise unsettled, so the elevation sat
// "extracting" for good and the restart below never ran. JavaScript cannot run while
// Python computes, so the check only gets a turn once Python has stopped: if the engine
// then answers a trivial call with "fatally failed", the stuck call is rejected with that
// error and the caller's restart path takes over.
// Is anything still on its way? A typed number waiting out its debounce, a face being
// extracted, a rebuild or a cutting plan with the engine (or waiting to start), the engine
// itself restarting, or the view easing between 3D and 2D. The browser test waits on this
// rather than guessing how long a rebuild takes.
let _engineCalls = 0;
function appIdle() {
    return pyReady && !_restarting && !_numTimer && !_nestTimer && _engineCalls === 0 && !view2d.anim
        && !state.elevations.some(e => e._running || e.pending);
}

// A call straight to the engine (no watchdog: pyRun has that), counted like pyRun's, so
// appIdle sees the cutting plan, an export or an import while it is being worked out.
function enginePy(code) {
    _engineCalls++;
    const over = () => { _engineCalls--; window._engineEndedAt = performance.now(); requestRedraw(300); };
    return pyodide.runPythonAsync(code).then(v => { over(); return v; }, e => { over(); throw e; });
}

function pyRun(code) {
    const py = pyodide;
    _engineCalls++;
    return new Promise((resolve, reject) => {
        let done = false;
        const timer = setInterval(() => {
            if (done) return;
            try { py.runPython('0'); }
            catch (err) {
                if (/fatally failed|Aborted/i.test(err.message || '')) { done = true; _engineCalls--; window._engineEndedAt = performance.now(); clearInterval(timer); reject(err); }
            }
        }, 1500);
        // whatever the answer changes on screen is drawn (viewer.requestRedraw)
        const over = () => { if (!done) { done = true; _engineCalls--; window._engineEndedAt = performance.now(); clearInterval(timer); requestRedraw(300); } };
        py.runPythonAsync(code).then(v => { over(); resolve(v); }, e => { over(); reject(e); });
    });
}

async function restartEngine() {
    if (_restarting) return false;          // one rebuild at a time
    _restarting = true;
    // the engine leaves where it is on the page as it goes (cladding_preview._crumb)
    window._engineDiedIn = window._engineStep || '';
    log('engine died' + (window._engineDiedIn ? ' in ' + window._engineDiedIn : '') + ' — rebuilding');
    if (window._engineDiedIn) console.error('engine died in ' + window._engineDiedIn);
    setStatus('Engine stopped' + (window._engineDiedIn ? ' in ' + window._engineDiedIn : '') + ' — rebuilding it…', 'busy');
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
        const modules = ['cladding_constants', 'cladding_window', 'cladding_lod', 'cladding_primitives', 'cladding_edges', 'cladding_corners', 'cladding_ridge', 'cladding_verge', 'cladding_valley', 'cladding_greencoat', 'cladding_hip', 'cladding_eaves', 'cladding_head', 'cladding_vergehead', 'cladding_abut', 'cladding_rake', 'cladding_match', 'cladding_nesting', 'cladding_state', 'cladding_geometry', 'cladding_booleans', 'cladding_profiles', 'cladding_union',
                         'cladding_checks', 'cladding_preview', 'fabric_extract', 'dxf_generator', 'cladding_elevations',
                         'ifc_generator'];
        const v = Date.now();
        for (const mod of modules) {
            const src = await (await fetch(mod + '.py?v=' + v)).text();
            pyodide.FS.writeFile('/home/pyodide/' + mod + '.py', src);
        }
        await enginePy(`
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
    document.getElementById('wr-auto').checked = autoNesting;
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
        if ((e.key === 'g' || e.key === 'G') && !typing && !modal && !e.ctrlKey && !e.metaKey && !e.altKey) {
            const box = document.getElementById('layer-grid');
            box.checked = !box.checked;
            setLayerVisible('grid', box.checked);
            return;
        }
        if ((e.key === 'h' || e.key === 'H') && !typing && !modal && !e.ctrlKey && !e.metaKey && !e.altKey) {
            setPickMode(state.mode === 'hide' ? 'pick' : 'hide');
            return;
        }
        if (e.key !== 'Escape' || document.getElementById('chain-wizard').classList.contains('open')) return;
        if (state.pipette) { endPipette(); setStatus('Pipette cancelled', 'ready'); return; }
        if (document.getElementById('course-dialog').classList.contains('open')) { closeCourseDialog(); return; }
        if (state.levels) { dismissLevels(); return; }
        // Escape unwinds one thing at a time: the edit widget first, then the flat view.
        if (state.editing) { closeEditWidget(); return; }
        if (in2D()) { exit2D(); update2DButton(); renderDims2D(); }
    });
    document.getElementById('download-reminder').addEventListener('click', e => { if (e.target.id === 'download-reminder') e.currentTarget.classList.remove('open'); });
}
