/* CladForge cutting run — the cutting plan played board by board, beside the model.
 *
 * The screen splits. The 3D view on the left keeps the building in context; the right
 * shows the cutting plan exactly as the DXF draws it (cladding_nesting.chain_plan: one
 * row per elevation, boards left to right in wall order). Each board has its own pastel
 * hue, shared by its pieces in both views, so even paused the wall reads as a patchwork
 * of boards. Playing steps through the boards a piece at a time: the piece goes full
 * colour with a glowing edge on its board and on the wall, the strip scrolls to keep the
 * board in view, and the camera swings round the chain to follow it. Nothing is rebuilt:
 * it is a view of the plan the download uses and the panels already drawn.
 */

const RUN_DWELL = 1600;           // ms a piece holds the focus at 1× speed
const RUN_MOVE = 900;             // ms the camera may take to reach it at 1×
const RUN_GOLDEN = 137.508;       // degrees between neighbouring boards' hues
const RUN_GLOW = [[0, 30, 0.95], [30, 110, 0.35]];   // glow bands round the piece: from, to (mm), opacity
const RUN_BOARD_PX = 210;         // height a board is drawn on the strip
const RUN_LENGTH_PX = 440;        // height a plank's stock length is drawn on the strip, standing up

const run = { on: false, plan: null, steps: [], i: -1, playing: false, speed: 1, timer: null,
              meshes: {}, group: null, glow: null, saved: null };

// Neighbouring boards land far apart on the colour wheel, however many there are.
const runHue = k => (200 + k * RUN_GOLDEN) % 360;
const runColor = (h, s, l) => new THREE.Color().setHSL(h / 360, s / 100, l / 100);
const runCss = (h, s, l) => `hsl(${h.toFixed(1)}, ${s}%, ${l}%)`;
const PASTEL = [48, 72], BOARD_ON = [70, 70], FOCUS = [88, 58];   // saturation, lightness

function inCuttingRun() { return run.on; }

// ─── OPEN AND CLOSE ───
async function openCuttingRun() {
    if (run.on) return;
    if (!pyReady || !state.chains.some(c => c.built)) {
        setStatus('Build a chain first: the cutting run plays its cutting plan.', 'busy');
        return;
    }
    const btn = document.getElementById('run-open');
    btn.disabled = true; btn.textContent = 'Optimising boards…';
    setStatus('Packing the boards for the cutting run (the same plan as the download)…', 'busy');
    await new Promise(r => setTimeout(r, 30));          // let the label paint before Python blocks
    let plan = null;
    try { plan = await cuttingPlan(false); }
    catch (err) { setStatus('Cutting run failed: ' + err.message, 'error'); }
    finally { btn.disabled = false; btn.textContent = 'Cutting run'; }
    if (!plan) return;
    if (!plan.boards.length) { setStatus('Nothing to cut: no panels were packed.', 'busy'); return; }
    startRun(plan);
}

function startRun(plan) {
    reset2D();
    if (state.editing) closeEditWidget();
    run.plan = plan;
    run.steps = plan.boards.flatMap((b, k) => b.pieces.map((q, j) => ({ k, j, board: b, piece: q })));
    run.saved = { pos: camera.position.clone(), target: controls.target.clone(), dims: dimGroup.visible };
    run.on = true;
    document.body.classList.add('cut-run');
    document.getElementById('run-pane').hidden = false;
    window.dispatchEvent(new Event('resize'));          // the view is half as wide now
    dimGroup.visible = false;
    run.meshes = {};
    for (const layer of cladGroup.children) for (const o of layer.children)
        if (o.isMesh && o.userData.name) run.meshes[o.userData.name] = o;
    for (const m of Object.values(run.meshes)) {
        m.userData.runColor = m.material.color.getHex();
        m.userData.runEmissive = m.material.emissive.getHex();
    }
    run.group = new THREE.Group();
    scene.add(run.group);
    run.steps.forEach((s, n) => {
        const mesh = run.meshes[s.piece.name];
        if (mesh) run.group.add(runLabel(`${s.board.no}·${s.j + 1}`, mesh));
    });
    renderStrip(plan);
    runGo(0);
    runPlay(true);
}

function closeCuttingRun() {
    if (!run.on) return;
    runPlay(false);
    run.on = false;
    clearGlow();
    if (run.group) { clearGroup(run.group); scene.remove(run.group); run.group = null; }
    for (const m of Object.values(run.meshes)) {
        if (m.userData.runColor !== undefined) m.material.color.setHex(m.userData.runColor);
        if (m.userData.runEmissive !== undefined) m.material.emissive.setHex(m.userData.runEmissive);
    }
    run.meshes = {};
    document.body.classList.remove('cut-run');
    document.getElementById('run-pane').hidden = true;
    window.dispatchEvent(new Event('resize'));
    dimGroup.visible = run.saved.dims;
    const s = run.saved, off = s.pos.clone().sub(s.target);
    animatePose(currentPose(), { target: s.target, dir: off.clone().normalize(), dist: off.length() }, null);
    run.i = -1;
    setStatus('Back to the model', 'ready');
}

// ─── PLAYBACK ───
function runPlay(on) {
    run.playing = on;
    clearTimeout(run.timer);
    const b = document.getElementById('run-play');
    b.textContent = on ? 'Pause' : 'Play';
    b.setAttribute('aria-pressed', String(on));
    if (on) {
        if (run.i >= run.steps.length - 1) runGo(0);      // played to the end: start again
        tick();
    }
}

function tick() {
    clearTimeout(run.timer);
    if (!run.playing) return;
    run.timer = setTimeout(() => {
        if (!run.on || !run.playing) return;
        if (run.i < run.steps.length - 1) { runGo(run.i + 1); tick(); }
        else { runPlay(false); setStatus(`Cutting run finished: ${run.plan.n_boards} boards`, 'ready'); }
    }, RUN_DWELL / run.speed);
}

function runStep(d) {
    runPlay(false);
    runGo(Math.max(0, Math.min(run.steps.length - 1, run.i + d)));
}

function runSpeed(v) {
    run.speed = parseFloat(v) || 1;
    document.getElementById('run-speed-val').textContent = run.speed + '×';
    if (run.playing) tick();
}

// Focus step i: its piece on the strip and on the wall, the counter, the camera.
function runGo(i) {
    if (!run.on || !run.steps.length) return;
    run.i = i;
    const s = run.steps[i], b = s.board, q = s.piece;
    paintWall(s);
    paintStrip(s);
    const pieces = b.pieces.length;
    const plank = run.plan.kind === 'plank';
    document.getElementById('run-board').textContent = `${plank ? 'Length' : 'Board'} ${b.no}`;
    document.getElementById('run-of').textContent = `of ${run.plan.n_boards}`;
    document.getElementById('run-piece').textContent = plank
        ? `Piece ${s.j + 1} of ${pieces}: ${Math.round(q.cut_w)} mm${q.ripped ? `, ripped to ${Math.round(q.cut_h)} wide` : ''}`
        : `Piece ${s.j + 1} of ${pieces}: ${Math.round(q.cut_w)} × ${Math.round(q.cut_h)} mm${q.rotated ? ', turned on the board' : ''}`;
    document.getElementById('run-where').textContent = `${b.elevation} · ${q.name}`;
    const sw = document.getElementById('run-swatch');
    sw.style.background = sw.style.color = runCss(runHue(s.k), FOCUS[0], FOCUS[1]);
    document.getElementById('run-progress').style.width = `${100 * (i + 1) / run.steps.length}%`;
    followPiece(s);
}

// ─── THE WALL ───
function paintWall(s) {
    run.steps.forEach(t => {
        const m = run.meshes[t.piece.name];
        if (!m) return;
        const h = runHue(t.k), sl = t === s ? FOCUS : (t.k === s.k ? BOARD_ON : PASTEL);
        m.material.color.copy(runColor(h, sl[0], sl[1]));
        m.material.emissive.copy(t === s ? runColor(h, 80, 22) : new THREE.Color(0x000000));
    });
    clearGlow();
    const m = run.meshes[s.piece.name];
    if (m) run.glow = glowFor(m, runHue(s.k));
}

function clearGlow() {
    if (run.glow) { clearGroup(run.glow); scene.remove(run.glow); run.glow = null; }
}

// A glowing edge round a panel: bright bands just outside its outline, drawn over its
// neighbours, in the board's hue. Flat rings on the panel's outer face, so no post-processing.
function glowFor(mesh, hue) {
    const g = new THREE.Group();
    const bb = mesh.geometry.boundingBox || (mesh.geometry.computeBoundingBox(), mesh.geometry.boundingBox);
    const rect = pad => {
        const s = new THREE.Shape();
        s.moveTo(bb.min.x - pad, bb.min.y - pad); s.lineTo(bb.max.x + pad, bb.min.y - pad);
        s.lineTo(bb.max.x + pad, bb.max.y + pad); s.lineTo(bb.min.x - pad, bb.max.y + pad);
        return s;
    };
    for (const [from, to, opacity] of RUN_GLOW) {
        const shape = rect(to), hole = rect(from);
        shape.holes.push(new THREE.Path(hole.getPoints().reverse()));
        const geo = new THREE.ShapeGeometry(shape);
        geo.translate(0, 0, bb.max.z + 2);
        const ring = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: runColor(hue, 95, 62), transparent: true, opacity,
                                                                        depthTest: false, depthWrite: false, side: THREE.DoubleSide }));
        ring.matrixAutoUpdate = false;
        ring.matrix.copy(mesh.matrix);
        ring.renderOrder = 10;
        g.add(ring);
    }
    scene.add(g);
    return g;
}

// "3·1" on each panel: the board it comes off and its place on that board, as on the strip.
const _runLabelTex = {};
function runLabel(text, mesh) {
    if (!_runLabelTex[text]) {
        const c = document.createElement('canvas'), ctx = c.getContext('2d');
        ctx.font = 'bold 64px sans-serif';
        c.width = Math.ceil(ctx.measureText(text).width) + 16; c.height = 80;
        ctx.font = 'bold 64px sans-serif'; ctx.textBaseline = 'middle'; ctx.fillStyle = '#10202a';
        ctx.fillText(text, 8, 42);
        _runLabelTex[text] = { tex: new THREE.CanvasTexture(c), w: c.width, h: c.height };
    }
    const t = _runLabelTex[text];
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: t.tex, transparent: true }));
    const bb = mesh.geometry.boundingBox || (mesh.geometry.computeBoundingBox(), mesh.geometry.boundingBox);
    const narrow = Math.min(bb.max.x - bb.min.x, bb.max.y - bb.min.y);
    const k = Math.min(240, narrow < 400 ? 0.7 * narrow : 0.22 * narrow) / t.h;   // text height, mm: planks are narrow
    sprite.scale.set(t.w * k, t.h * k, 1);
    // a tall, narrow piece (a vertical plank) has its number running up it
    if (bb.max.y - bb.min.y > 2 * (bb.max.x - bb.min.x)) sprite.material.rotation = Math.PI / 2;
    sprite.position.copy(new THREE.Vector3((bb.min.x + bb.max.x) / 2, (bb.min.y + bb.max.y) / 2, bb.max.z + 15).applyMatrix4(mesh.matrix));
    return sprite;
}

// Swing the camera to face the board's pieces from outside, a little above and to the
// side, far enough back that every piece cut from the board in focus is on screen at
// once (and a good stretch of wall round them), so stepping through a board's pieces
// keeps the camera still and it moves on with the next board. Between elevations the
// direction turns round the target, so the camera follows the chain round its corners.
function followPiece(s) {
    const m = run.meshes[s.piece.name];
    if (!m) return;
    const box = new THREE.Box3();
    for (const t of run.steps) {
        const mesh = t.k === s.k && run.meshes[t.piece.name];
        if (!mesh) continue;
        const bb = mesh.geometry.boundingBox || (mesh.geometry.computeBoundingBox(), mesh.geometry.boundingBox);
        box.union(bb.clone().applyMatrix4(mesh.matrix));
    }
    const target = box.getCenter(new THREE.Vector3());
    const u = new THREE.Vector3().setFromMatrixColumn(m.matrix, 0).normalize();
    const n = new THREE.Vector3().setFromMatrixColumn(m.matrix, 2).normalize();
    const dir = n.clone().add(new THREE.Vector3(0, 0.3, 0)).addScaledVector(u, 0.18).normalize();
    // fit the pieces' bounding sphere in the narrower of the two fields of view
    const r = box.getSize(new THREE.Vector3()).length() / 2;
    const vfov = THREE.MathUtils.degToRad(camera.fov), hfov = 2 * Math.atan(Math.tan(vfov / 2) * camera.aspect);
    const dist = Math.max(8000, 1.15 * r / Math.sin(Math.min(vfov, hfov) / 2));
    animatePose(currentPose(), { target, dir, dist }, null, Math.min(RUN_MOVE, 0.7 * RUN_DWELL) / run.speed);
}

// ─── THE STRIP ───
// One row per elevation, boards (or plank lengths, standing up) left to right, drawn in
// board millimetres the way the DXF draws them, pieces in the board's hue, offcuts
// hatched grey.
function renderStrip(plan) {
    const ns = 'http://www.w3.org/2000/svg', [bw, bh] = plan.board;
    const extra = plan.n_boards - (plan.whole_job_boards || plan.n_boards), plank = plan.kind === 'plank';
    const unit = plank ? 'length' : 'board';
    document.getElementById('run-summary').textContent =
        (plank ? `${plan.n_boards} lengths of ${bw} (${bh} wide)` : `${plan.n_boards} boards of ${bw} × ${bh}`)
        + ` · waste ${(100 * plan.waste).toFixed(1)}% · `
        + (extra > 0 ? `${extra} more than packing the whole job together` : 'no more than packing the whole job together');
    const strip = document.getElementById('run-strip');
    strip.innerHTML = `<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
        <pattern id="run-hatch" patternUnits="userSpaceOnUse" width="70" height="70" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="70" stroke="#5b6b7a" stroke-width="10"/></pattern></defs></svg>`;
    let step = 0;
    for (const row of plan.rows) {
        const boards = plan.boards.filter(b => b.elevation === row.elevation);
        const sec = document.createElement('section');
        sec.className = 'run-row';
        sec.innerHTML = `<h3>${esc(row.elevation)} <span>${row.n_boards} ${unit}${row.n_boards === 1 ? '' : 's'} · waste ${(100 * row.waste).toFixed(1)}%</span></h3>`;
        const line = document.createElement('div');
        line.className = 'run-boards' + (plank ? ' planks' : '');
        for (const b of boards) {
            const k = plan.boards.indexOf(b), h = runHue(k);
            const fig = document.createElement('figure');
            fig.className = 'run-board';
            fig.dataset.k = k;
            fig.style.setProperty('--hue', h.toFixed(1));
            // As the DXF draws it: a board flat, a plank's stock length standing up, a plank
            // wide, its pieces from the bottom. W x H is the drawing; R maps a rectangle in
            // board millimetres (x along the board, y up it) to the SVG's (y down).
            const W = plank ? bh : bw, H = plank ? bw : bh;
            const R = plank ? (x, y, w, hh) => ({ x: y, y: bw - x - w, width: hh, height: w })
                            : (x, y, w, hh) => ({ x, y: bh - y - hh, width: w, height: hh });
            const svg = document.createElementNS(ns, 'svg');
            svg.setAttribute('viewBox', `-20 -20 ${W + 40} ${H + 40}`);
            const px = (plank ? RUN_LENGTH_PX : RUN_BOARD_PX) / (H + 40);
            svg.style.height = (px * (H + 40)).toFixed(1) + 'px';
            svg.style.width = (px * (W + 40)).toFixed(1) + 'px';
            const add = (tag, attrs, parent = svg) => {
                const el = document.createElementNS(ns, tag);
                for (const [a, v] of Object.entries(attrs)) el.setAttribute(a, v);
                parent.appendChild(el);
                return el;
            };
            add('rect', { x: 0, y: 0, width: W, height: H, class: 'run-stock' });
            for (const [x, y, w, hh] of b.offcuts) add('rect', Object.assign(R(x, y, w, hh), { class: 'run-offcut' }));
            const t = plan.trim || 0;
            if (t) add('rect', Object.assign(plank ? R(t, 0, bw - 2 * t, bh) : R(t, t, bw - 2 * t, bh - 2 * t), { class: 'run-trim' }));
            b.pieces.forEach((q, j) => {
                const g = add('g', { class: 'run-piece', 'data-step': step + j });
                g.style.setProperty('--hue', h.toFixed(1));
                const r = R(q.x, q.y, q.w, q.h);
                add('rect', r, g);
                const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
                if (plank) {                                 // one line, running up the length
                    const fs = Math.min(0.55 * bh, 0.09 * q.w);
                    add('text', { x: cx, y: cy, 'font-size': fs, class: 'run-no', transform: `rotate(-90 ${cx} ${cy})` }, g).textContent =
                        `${b.no}·${j + 1}  ${Math.round(q.cut_w)}${q.ripped ? ' ✂' + Math.round(q.cut_h) : ''}`;
                } else {
                    const fs = Math.max(55, Math.min(150, 0.16 * Math.min(q.w, q.h)));
                    add('text', { x: cx, y: cy - 0.15 * fs, 'font-size': fs * 1.25, class: 'run-no' }, g).textContent = `${b.no}·${j + 1}`;
                    add('text', { x: cx, y: cy + 1.05 * fs, 'font-size': fs, class: 'run-size' }, g).textContent =
                        `${Math.round(q.cut_w)} × ${Math.round(q.cut_h)}${q.rotated ? ' ↻' : ''}`;
                }
                g.addEventListener('click', ev => { ev.stopPropagation(); runPlay(false); runGo(+g.dataset.step); });
            });
            step += b.pieces.length;
            fig.appendChild(svg);
            const cap = document.createElement('figcaption');
            const used = Math.round(100 * (1 - b.waste));
            if (plank) { cap.innerHTML = `L${b.no}<br>${used}%`; fig.title = `Length ${b.no} · ${used}% used`; }
            else cap.textContent = `Board ${b.no} · ${used}% used`;
            fig.appendChild(cap);
            fig.addEventListener('click', () => { runPlay(false); runGo(run.steps.findIndex(t => t.k === k)); });
            line.appendChild(fig);
        }
        sec.appendChild(line);
        strip.appendChild(sec);
    }
}

function paintStrip(s) {
    document.querySelectorAll('#run-strip .run-board.focus').forEach(f => f.classList.remove('focus'));
    document.querySelectorAll('#run-strip .run-piece.focus, #run-strip .run-piece.on').forEach(p => p.classList.remove('focus', 'on'));
    const fig = document.querySelector(`#run-strip .run-board[data-k="${s.k}"]`);
    if (!fig) return;
    fig.classList.add('focus');
    fig.querySelectorAll('.run-piece').forEach(p => p.classList.add('on'));
    const g = fig.querySelector(`.run-piece[data-step="${run.i}"]`);
    if (g) { g.classList.remove('on'); g.classList.add('focus'); }
    fig.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'nearest', inline: 'center' });
}

function esc(t) { return String(t).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

// ─── KEYS ───
// While the run is open its keys come first, and nothing else in the app sees them:
// Enter would open the wizard and E the flat view underneath it.
window.addEventListener('keydown', e => {
    if (!run.on) return;
    const el = document.activeElement;
    if (el && el.id === 'run-speed' && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) return;   // the slider's own
    if (el && el.tagName === 'BUTTON' && (e.key === ' ' || e.key === 'Enter')) return;              // the button's own
    if (e.key === ' ' || e.key === 'Spacebar') { e.preventDefault(); runPlay(!run.playing); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); runStep(1); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); runStep(-1); }
    else if (e.key === 'Escape') closeCuttingRun();
    else if (e.ctrlKey || e.metaKey || e.altKey) return;
    e.stopImmediatePropagation();
}, true);
