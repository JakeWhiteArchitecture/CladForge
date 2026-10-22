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
 *
 * Each elevation ends with its rack: the offcuts it leaves, gathered at the end of its
 * row on the strip and laid flat on the ground in front of its cladding. With offcuts
 * passed on, the next elevation starts with what it cuts from them: the rack is carried
 * round in front of it, and each offcut lights up there and on the strip as its pieces
 * light up on the wall, in the hue of the board the offcut came from.
 */

const RUN_DWELL = 1600;           // ms a piece holds the focus at 1× speed
const RUN_MOVE = 900;             // ms the camera may take to reach it at 1×
const RUN_GOLDEN = 137.508;       // degrees between neighbouring boards' hues
const RUN_GLOW = [[0, 30, 0.95], [30, 110, 0.35]];   // glow bands round the piece: from, to (mm), opacity
const RUN_BOARD_PX = 210;         // height a board is drawn on the strip
const RUN_LENGTH_PX = 440;        // height a plank's stock length is drawn on the strip, standing up

const run = { on: false, plan: null, steps: [], i: -1, playing: false, speed: 1, timer: null,
              meshes: {}, group: null, glow: null, saved: null, views: {}, rack: null };
const RUN_RACK_T = 25;            // mm: how thick an offcut is drawn on the rack
const RUN_RACK_GAP = 80;          // mm between offcuts on the rack
const RUN_RACK_OUT = 900;         // mm in front of the cladding the rack starts

// Looking round obstacles: turns of the view to either side (degrees) and rises of the
// eye, tried nearest first, until every focal piece can be seen.
const RUN_YAWS = [0, 15, -15, 30, -30, 45, -45, 60, -60, 75, -75];
const RUN_RISES = [0.3, 0.75, 1.3];
const RUN_CLOSER = [1, 0.7, 0.45];    // and, if nothing is clear from there, closer in

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
    run.steps = runSteps(plan);
    run.saved = { pos: camera.position.clone(), target: controls.target.clone(), dims: dimGroup.visible,
                  faces: highlightGroup.visible, outline: outlineGroup.visible };
    run.views = {};
    run.on = true;
    document.body.classList.add('cut-run');
    document.getElementById('run-pane').hidden = false;
    window.dispatchEvent(new Event('resize'));          // the view is half as wide now
    dimGroup.visible = false;
    highlightGroup.visible = false;                     // the picked faces and outlines would sit in front
    outlineGroup.visible = false;
    run.meshes = {};
    for (const layer of cladGroup.children) for (const o of layer.children)
        if (o.isMesh && o.userData.name) run.meshes[o.userData.name] = o;
    for (const m of Object.values(run.meshes)) {
        m.userData.runColor = m.material.color.getHex();
        m.userData.runEmissive = m.material.emissive.getHex();
    }
    // The cladding is drawn see-through so the buildup reads behind it, and see-through
    // surfaces are drawn back to front by each object's centre: from far off that order
    // can put the big translucent layers behind (insulation, sheathing) over the boards
    // and wash their colours out. While the run is open the pieces it colours are solid.
    const solid = new Set(run.steps.filter(t => t.piece).map(t => run.meshes[t.piece.name]).filter(Boolean));
    for (const m of solid) {
        m.userData.runLook = [m.material.transparent, m.material.opacity, m.material.depthWrite];
        Object.assign(m.material, { transparent: false, opacity: 1, depthWrite: true });
        m.material.needsUpdate = true;
    }
    run.group = new THREE.Group();
    scene.add(run.group);
    run.steps.forEach(s => {
        const mesh = s.piece && run.meshes[s.piece.name];
        if (mesh) run.group.add(runLabel(stepLabel(s), mesh));
    });
    renderStrip(plan);
    runGo(0);
    runPlay(true);
    setStatus('Cutting run · Space plays or pauses · ← → step · Esc closes', 'ready');
}

// The order the run plays, elevation by elevation: the pieces it cuts from offcuts
// carried on from earlier elevations (grouped by offcut), its own boards a piece at a
// time, then its rack of offcuts. *key* names the figure on the strip, *hue* its colour.
function runSteps(plan) {
    const steps = [];
    (plan.rows || [{ elevation: null }]).forEach((row, r) => {
        for (const g of (plan.reuse || []).filter(g => g.elevation === row.elevation)) {
            const board = { no: g.no, elevation: row.elevation, pieces: g.pieces, reuse: g };
            g.pieces.forEach((q, j) => steps.push({ kind: 'piece', key: 'o' + g.id, hue: runHue(g.k), j, board, piece: q,
                                                     row: r, rack: r - 1, oid: g.id }));
        }
        plan.boards.forEach((b, k) => {
            if (row.elevation !== null && b.elevation !== row.elevation) return;
            b.pieces.forEach((q, j) => steps.push({ kind: 'piece', key: 'b' + k, hue: runHue(k), j, board: b, piece: q, row: r }));
        });
        const rack = plan.racks && plan.racks[r];
        if (rack && rack.offcuts.length) steps.push({ kind: 'rack', key: 'r' + r, hue: 210, j: 0, row: r, rack: r, elevation: row.elevation });
    });
    return steps;
}

// "12·3": the board and its third piece; "12·o1": the first piece cut from board 12's offcut.
const stepLabel = s => s.oid !== undefined ? `${s.board.no}·o${s.j + 1}` : `${s.board.no}·${s.j + 1}`;

function closeCuttingRun() {
    if (!run.on) return;
    runPlay(false);
    run.on = false;
    clearGlow();
    clearRack();
    if (run.group) { clearGroup(run.group); scene.remove(run.group); run.group = null; }
    for (const m of Object.values(run.meshes)) {
        if (m.userData.runColor !== undefined) m.material.color.setHex(m.userData.runColor);
        if (m.userData.runEmissive !== undefined) m.material.emissive.setHex(m.userData.runEmissive);
        if (m.userData.runLook) {
            const [transparent, opacity, depthWrite] = m.userData.runLook;
            Object.assign(m.material, { transparent, opacity, depthWrite });
            m.material.needsUpdate = true;
            delete m.userData.runLook;
        }
    }
    run.meshes = {};
    document.body.classList.remove('cut-run');
    document.getElementById('run-pane').hidden = true;
    window.dispatchEvent(new Event('resize'));
    dimGroup.visible = run.saved.dims;
    highlightGroup.visible = run.saved.faces;
    outlineGroup.visible = run.saved.outline;
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
    showRack(s);
    paintWall(s);
    paintStrip(s);
    const plank = run.plan.kind === 'plank', unit = plank ? 'length' : 'board';
    const set = (id, t) => { document.getElementById(id).textContent = t; };
    const sw = document.getElementById('run-swatch');
    if (s.kind === 'rack') {
        const rack = run.plan.racks[s.rack], n = rack.offcuts.length, used = rack.offcuts.filter(o => o.used_by).length;
        const last = s.row === run.plan.rows.length - 1;
        set('run-board', 'Offcuts');
        set('run-of', `after ${s.elevation}`);
        set('run-piece', plank
            ? `${n} offcut${n === 1 ? '' : 's'}, ${(rack.offcuts.reduce((a, o) => a + o.rect[2], 0) / 1000).toFixed(1)} m in all`
            : `${n} offcut${n === 1 ? '' : 's'}, ${(rack.offcuts.reduce((a, o) => a + o.rect[2] * o.rect[3], 0) / 1e6).toFixed(2)} m² in all`);
        set('run-where', !run.plan.share_offcuts ? `Not passed on: each elevation is cut from its own ${unit}s`
            : last ? 'Left over at the end of the job'
            : used ? `${used} used by the elevations after it, the rest carried on` : 'None fits what comes next: carried on');
        sw.style.background = sw.style.color = '#8ea3b8';
    } else {
        const pieces = b.pieces.length;
        set('run-board', s.oid !== undefined ? `Offcut of ${unit} ${b.no}` : `${plank ? 'Length' : 'Board'} ${b.no}`);
        set('run-of', s.oid !== undefined ? `from ${run.plan.boards[b.reuse.k].elevation}` : `of ${run.plan.n_boards}`);
        set('run-piece', plank
            ? `Piece ${s.j + 1} of ${pieces}: ${Math.round(q.cut_w)} mm${q.ripped ? `, ripped to ${Math.round(q.cut_h)} wide` : ''}`
            : `Piece ${s.j + 1} of ${pieces}: ${Math.round(q.cut_w)} × ${Math.round(q.cut_h)} mm${q.rotated ? ', turned on the board' : ''}`);
        set('run-where', `${b.elevation} · ${q.name}`);
        sw.style.background = sw.style.color = runCss(s.hue, FOCUS[0], FOCUS[1]);
    }
    document.getElementById('run-progress').style.width = `${100 * (i + 1) / run.steps.length}%`;
    followPiece(s);
}

// ─── THE WALL ───
// Within a board the focus builds up: at piece j, pieces 1 to j of that board are all lit
// (full colour, glowing, as a group), the rest of the board a little stronger than the
// other boards. A new board starts again from its first piece.
const runLit = (t, s) => t.key === s.key && t.j <= s.j;

function paintWall(s) {
    clearGlow();
    run.glow = new THREE.Group();
    scene.add(run.glow);
    run.steps.forEach(t => {
        const m = t.piece && run.meshes[t.piece.name];
        if (!m) return;
        const lit = runLit(t, s), h = t.hue, sl = lit ? FOCUS : (t.key === s.key ? BOARD_ON : PASTEL);
        m.material.color.copy(runColor(h, sl[0], sl[1]));
        m.material.emissive.copy(lit ? runColor(h, 80, 22) : new THREE.Color(0x000000));
        if (lit) run.glow.add(glowFor(m, h));
    });
    if (!run.rack) return;
    // On the rack: every offcut in focus at the rack step; at a piece cut from an offcut,
    // that offcut, glowing, the rest pastel.
    for (const it of run.rack.items) {
        const lit = s.kind === 'rack' || it.oid === s.oid, sl = lit ? FOCUS : PASTEL;
        it.mesh.material.color.copy(runColor(it.hue, sl[0], sl[1]));
        it.mesh.material.emissive.copy(lit ? runColor(it.hue, 80, 22) : new THREE.Color(0x000000));
        if (lit && s.kind !== 'rack') run.glow.add(rackGlow(it));
    }
}

// ─── THE RACK ───
// The offcuts left after an elevation, laid flat side by side on the ground in front of
// its cladding (rows of them working outwards from the wall), each in the hue of the
// board it was cut from. Shown at the elevation's rack step, and carried round in front
// of the next elevation while it cuts from them.
function showRack(s) {
    const want = s.kind === 'rack' ? [s.rack, s.elevation]
        : (s.oid !== undefined && s.rack >= 0 ? [s.rack, run.plan.rows[s.row].elevation] : null);
    const key = want ? want.join('@') : null;
    if (run.rack && run.rack.key === key) return;
    clearRack();
    if (want) run.rack = buildRack(run.plan.racks[want[0]], want[1], key);
}

function clearRack() {
    if (run.rack) { clearGroup(run.rack.group); scene.remove(run.rack.group); run.rack = null; }
}

function buildRack(rack, elevation, key) {
    const wall = run.steps.filter(t => t.piece && t.board.elevation === elevation).map(t => run.meshes[t.piece.name]).filter(Boolean);
    if (!rack || !wall.length) return null;
    const u = new THREE.Vector3().setFromMatrixColumn(wall[0].matrix, 0).setY(0).normalize();
    const n = new THREE.Vector3().setFromMatrixColumn(wall[0].matrix, 2).setY(0).normalize();
    const up = new THREE.Vector3(0, 1, 0);
    let uMin = Infinity, face = -Infinity, base = Infinity;
    for (const m of wall) {
        const bb = (m.geometry.boundingBox || (m.geometry.computeBoundingBox(), m.geometry.boundingBox)).clone().applyMatrix4(m.matrix);
        for (const x of [bb.min.x, bb.max.x]) for (const y of [bb.min.y, bb.max.y]) for (const z of [bb.min.z, bb.max.z]) {
            const c = new THREE.Vector3(x, y, z);
            uMin = Math.min(uMin, c.dot(u)); face = Math.max(face, c.dot(n)); base = Math.min(base, y);
        }
    }
    const origin = u.clone().multiplyScalar(uMin).addScaledVector(n, face + RUN_RACK_OUT).addScaledVector(up, base);
    const plank = run.plan.kind === 'plank';
    const items = rack.offcuts.map(o => ({ oid: o.id, no: o.no, hue: runHue(o.k),
                                           len: plank ? o.rect[2] : Math.max(o.rect[2], o.rect[3]),
                                           wid: plank ? o.rect[3] : Math.min(o.rect[2], o.rect[3]) }))
        .sort((a, b) => b.len - a.len || a.no - b.no);
    const rowMax = Math.max(4000, ...items.map(it => it.len));
    const group = new THREE.Group(), basis = new THREE.Matrix4().makeBasis(u, up, u.clone().cross(up));
    let a = 0, b = 0, depth = 0;
    for (const it of items) {
        if (a > 0 && a + it.len > rowMax) { a = 0; b += depth + RUN_RACK_GAP; depth = 0; }
        const centre = origin.clone().addScaledVector(u, a + it.len / 2).addScaledVector(n, b + it.wid / 2).addScaledVector(up, RUN_RACK_T / 2);
        it.mesh = new THREE.Mesh(new THREE.BoxGeometry(it.len, RUN_RACK_T, it.wid),
                                 new THREE.MeshStandardMaterial({ color: runColor(it.hue, PASTEL[0], PASTEL[1]), roughness: 0.8 }));
        it.mesh.matrixAutoUpdate = false;
        it.mesh.matrix.copy(basis).setPosition(centre);
        it.mesh.geometry.computeBoundingBox();
        group.add(it.mesh);
        const label = runLabel(plank ? `L${it.no}` : `${it.no}`, it.mesh);
        const t = label.scale.y;
        label.scale.multiplyScalar(Math.min(220, 0.8 * it.wid) / t);
        label.position.copy(centre).addScaledVector(up, RUN_RACK_T / 2 + 40);
        group.add(label);
        a += it.len + RUN_RACK_GAP;
        depth = Math.max(depth, it.wid);
    }
    scene.add(group);
    return { key, group, items, u, n };
}

// A glowing edge round an offcut on the rack: bands just outside it on its top face.
function rackGlow(it) {
    const g = new THREE.Group();
    const rect = pad => {
        const s = new THREE.Shape();
        s.moveTo(-it.len / 2 - pad, -it.wid / 2 - pad); s.lineTo(it.len / 2 + pad, -it.wid / 2 - pad);
        s.lineTo(it.len / 2 + pad, it.wid / 2 + pad); s.lineTo(-it.len / 2 - pad, it.wid / 2 + pad);
        return s;
    };
    for (const [from, to, opacity] of RUN_GLOW) {
        const shape = rect(to);
        shape.holes.push(new THREE.Path(rect(from).getPoints().reverse()));
        const geo = new THREE.ShapeGeometry(shape);
        geo.rotateX(-Math.PI / 2);
        geo.translate(0, RUN_RACK_T / 2 + 2, 0);
        const ring = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: runColor(it.hue, 95, 62), transparent: true, opacity,
                                                                        depthTest: false, depthWrite: false, side: THREE.DoubleSide }));
        ring.matrixAutoUpdate = false;
        ring.matrix.copy(it.mesh.matrix);
        ring.renderOrder = 10;
        g.add(ring);
    }
    return g;
}

function clearGlow() {
    if (run.glow) { clearGroup(run.glow); scene.remove(run.glow); run.glow = null; }
}

// A glowing edge round a panel: bright bands just outside its outline, drawn over its
// neighbours, in the board's hue. Flat rings on the panel's outer face, so no post-processing.
// Returned as a group for the caller to add (paintWall gathers a board's lit pieces).
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
    return g;
}

// A label too small on screen to read is noise: from 12 px tall it fades out, gone by 7.
function fadeSmallLabel(renderer, _scene, cam) {
    if (!cam.isPerspectiveCamera) { this.material.opacity = 1; return; }
    const d = cam.position.distanceTo(this.getWorldPosition(_runLabelAt));
    const px = this.scale.y * renderer.domElement.clientHeight / (2 * d * Math.tan(THREE.MathUtils.degToRad(cam.fov) / 2));
    this.material.opacity = Math.max(0, Math.min(1, (px - 7) / 5));
}
const _runLabelAt = new THREE.Vector3();

// "3·1" on each panel: the board it comes off and its place on that board, as on the strip.
const _runLabelTex = {};
function runLabel(text, mesh) {
    if (!_runLabelTex[text]) {
        const c = document.createElement('canvas'), ctx = c.getContext('2d');
        ctx.font = 'bold 64px sans-serif';
        c.width = Math.ceil(ctx.measureText(text).width) + 16; c.height = 80;
        ctx.font = 'bold 64px sans-serif'; ctx.textBaseline = 'middle'; ctx.fillStyle = '#10202a';
        ctx.fillText(text, 8, 42);
        const tex = new THREE.CanvasTexture(c);
        tex.premultiplyAlpha = true;           // shrunk, the clear background must not average towards black
        _runLabelTex[text] = { tex, w: c.width, h: c.height };
    }
    const t = _runLabelTex[text];
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: t.tex, transparent: true, premultipliedAlpha: true,
                                                                depthWrite: false }));
    sprite.onBeforeRender = fadeSmallLabel;
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
// Anything standing in front of the pieces (a roof, a wing, another face's cladding)
// turns the view away from it: see clearView.
function followPiece(s) {
    if (s.kind === 'rack') {
        if (!run.rack) return;
        if (!run.views[s.key]) run.views[s.key] = rackView(run.rack);
    } else {
        const m = run.meshes[s.piece.name];
        if (!m) return;
        if (!run.views[s.key]) run.views[s.key] = boardView(s, m);
    }
    const v = run.views[s.key];
    animatePose(currentPose(), { target: v.target.clone(), dir: v.dir.clone(), dist: v.dist }, null,
                Math.min(RUN_MOVE, 0.7 * RUN_DWELL) / run.speed);
}

// Where to look at a board from: its pieces' centre, the distance that fits them all,
// and the clearest direction near the square-on one.
function boardView(s, m) {
    const box = new THREE.Box3(), focal = [];
    for (const t of run.steps) {
        const mesh = t.key === s.key && t.piece && run.meshes[t.piece.name];
        if (mesh) focal.push(mesh);
    }
    // a piece cut from an offcut: the offcut on the rack in front of the wall too
    if (s.oid !== undefined && run.rack) focal.push(...run.rack.items.filter(it => it.oid === s.oid).map(it => it.mesh));
    for (const mesh of focal) {
        const bb = mesh.geometry.boundingBox || (mesh.geometry.computeBoundingBox(), mesh.geometry.boundingBox);
        box.union(bb.clone().applyMatrix4(mesh.matrix));
    }
    const target = box.getCenter(new THREE.Vector3());
    const u = new THREE.Vector3().setFromMatrixColumn(m.matrix, 0).normalize();
    const n = new THREE.Vector3().setFromMatrixColumn(m.matrix, 2).normalize();
    // fit the pieces' bounding sphere in the narrower of the two fields of view
    const r = box.getSize(new THREE.Vector3()).length() / 2;
    const vfov = THREE.MathUtils.degToRad(camera.fov), hfov = 2 * Math.atan(Math.tan(vfov / 2) * camera.aspect);
    const dist = Math.max(8000, 1.15 * r / Math.sin(Math.min(vfov, hfov) / 2));
    const view = clearView(target, n.clone().addScaledVector(u, 0.18), dist, focal);
    return Object.assign({ target }, view);
}

// The rack from in front and above, all of it in view.
function rackView(rack) {
    const box = new THREE.Box3();
    for (const it of rack.items) box.union(it.mesh.geometry.boundingBox.clone().applyMatrix4(it.mesh.matrix));
    const r = box.getSize(new THREE.Vector3()).length() / 2;
    const vfov = THREE.MathUtils.degToRad(camera.fov), hfov = 2 * Math.atan(Math.tan(vfov / 2) * camera.aspect);
    return { target: box.getCenter(new THREE.Vector3()),
             dir: rack.n.clone().addScaledVector(rack.u, 0.15).add(new THREE.Vector3(0, 1.1, 0)).normalize(),
             dist: Math.max(4000, 1.2 * r / Math.sin(Math.min(vfov, hfov) / 2)) };
}

// Points to keep in sight on each focal piece: its centre and two points towards its
// ends along its longer side, just off its outer face.
function sightPoints(focal) {
    const pts = [];
    for (const mesh of focal) {
        const bb = mesh.geometry.boundingBox;
        const c = bb.getCenter(new THREE.Vector3()), size = bb.getSize(new THREE.Vector3());
        const along = size.x >= size.y ? new THREE.Vector3(0.35 * size.x, 0, 0) : new THREE.Vector3(0, 0.35 * size.y, 0);
        for (const k of [0, 1, -1]) pts.push(c.clone().addScaledVector(along, k).setZ(bb.max.z + 10).applyMatrix4(mesh.matrix));
    }
    return pts.slice(0, 18);
}

// The view direction nearest the preferred one (horizontal part *facing*, rising a
// little) from which the focal pieces can be seen: turned up to 75° either way about
// the vertical and raised if need be. Every sight line from the eye to a point on the
// pieces is cast against the host model and the rest of the cladding as they stand
// (hidden elements and switched-off layers do not count); the first direction with
// none blocked wins; if none is clear from that distance, the camera comes closer in
// (seeing the pieces matters more than fitting them all); else the fewest blocked. Each sight line is cast from the piece out
// to the eye: a solid in between is entered through a face turned towards the piece, and
// so is one the eye would be inside (a roof, a wing), which a ray from the eye would miss.
function clearView(target, facing, dist, focal) {
    const skip = new Set(focal);
    const shown = o => { for (let p = o; p; p = p.parent) if (!p.visible) return false; return true; };
    const blockers = [];
    modelGroup.traverse(o => { if (o.isMesh && shown(o)) blockers.push(o); });
    cladGroup.traverse(o => { if (o.isMesh && !skip.has(o) && shown(o)) blockers.push(o); });
    scene.updateMatrixWorld();
    const pts = sightPoints(focal), ray = new THREE.Raycaster(), h = facing.clone().setY(0).normalize();
    const options = [];
    for (const rise of RUN_RISES) for (const yaw of RUN_YAWS)
        options.push({ yaw, rise, cost: Math.abs(yaw) + 60 * (rise - RUN_RISES[0]) });
    options.sort((a, b) => a.cost - b.cost);
    let best = null, tried = 0;
    search: for (const scale of RUN_CLOSER) for (const o of options) {
        const dir = h.clone().applyAxisAngle(new THREE.Vector3(0, 1, 0), THREE.MathUtils.degToRad(o.yaw))
            .add(new THREE.Vector3(0, o.rise, 0)).normalize();
        const eye = target.clone().addScaledVector(dir, dist * scale);
        let blocked = 0;
        for (const p of pts) {
            const d = eye.clone().sub(p), len = d.length();
            ray.set(p, d.divideScalar(len));
            ray.far = len;
            if (ray.intersectObjects(blockers, false).length) blocked++;
        }
        tried++;
        if (!best || blocked < best.blocked) best = { dir, dist: dist * scale, blocked };
        if (!blocked) break search;
    }
    return { dir: best.dir, dist: best.dist, blocked: best.blocked, tried };
}

// ─── THE STRIP ───
// One row per elevation, boards (or plank lengths, standing up) left to right, drawn in
// board millimetres the way the DXF draws them, pieces in the board's hue, offcuts
// hatched grey. A row starts with the offcuts it cuts from (each drawn as the piece of
// board it is) and ends with its rack; a piece cut later from a board's offcut is drawn
// on the board dashed, with the elevation it goes to.
function renderStrip(plan) {
    const ns = 'http://www.w3.org/2000/svg', [bw, bh] = plan.board;
    const extra = plan.n_boards - (plan.whole_job_boards || plan.n_boards), plank = plan.kind === 'plank';
    const unit = plank ? 'length' : 'board';
    document.getElementById('run-summary').textContent =
        (plank ? `${plan.n_boards} lengths of ${bw} (${bh} wide)` : `${plan.n_boards} boards of ${bw} × ${bh}`)
        + ` · waste ${(100 * plan.waste).toFixed(1)}% · `
        + (plan.share_offcuts ? `offcuts passed on: ${plan.from_offcuts || 0} piece${plan.from_offcuts === 1 ? '' : 's'}, ${plan.saved || 0} ${unit}${plan.saved === 1 ? '' : 's'} saved · ` : '')
        + (extra > 0 ? `${extra} more than packing the whole job together` : 'no more than packing the whole job together');
    const strip = document.getElementById('run-strip');
    strip.innerHTML = `<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
        <pattern id="run-hatch" patternUnits="userSpaceOnUse" width="70" height="70" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="70" stroke="#5b6b7a" stroke-width="10"/></pattern></defs></svg>`;
    const stepOf = {};
    run.steps.forEach((t, i) => { stepOf[`${t.key}|${t.j}`] = i; });
    const px = plank ? RUN_LENGTH_PX / (bw + 40) : RUN_BOARD_PX / (bh + 40);     // the same scale for every figure
    // An SVG W x H in board mm (as drawn), with a little margin, at the strip's scale.
    const sheet = (W, H) => {
        const svg = document.createElementNS(ns, 'svg');
        svg.setAttribute('viewBox', `-20 -20 ${W + 40} ${H + 40}`);
        svg.style.height = (px * (H + 40)).toFixed(1) + 'px';
        svg.style.width = (px * (W + 40)).toFixed(1) + 'px';
        return svg;
    };
    const add = (parent, tag, attrs) => {
        const el = document.createElementNS(ns, tag);
        for (const [a, v] of Object.entries(attrs)) el.setAttribute(a, v);
        parent.appendChild(el);
        return el;
    };
    // A piece drawn through R (board mm to the drawing), numbered as on the wall.
    const drawPiece = (svg, R, q, label, hue, key, j) => {
        const g = add(svg, 'g', { class: 'run-piece', 'data-step': stepOf[`${key}|${j}`] });
        g.style.setProperty('--hue', hue.toFixed(1));
        const r = R(q.x, q.y, q.w, q.h);
        add(g, 'rect', r);
        const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
        if (plank) {                                 // one line, running up the length
            const fs = Math.min(0.55 * bh, 0.09 * q.w);
            add(g, 'text', { x: cx, y: cy, 'font-size': fs, class: 'run-no', transform: `rotate(-90 ${cx} ${cy})` }).textContent =
                `${label}  ${Math.round(q.cut_w)}${q.ripped ? ' ✂' + Math.round(q.cut_h) : ''}`;
        } else {
            const fs = Math.max(55, Math.min(150, 0.16 * Math.min(q.w, q.h)));
            add(g, 'text', { x: cx, y: cy - 0.15 * fs, 'font-size': fs * 1.25, class: 'run-no' }).textContent = label;
            add(g, 'text', { x: cx, y: cy + 1.05 * fs, 'font-size': fs, class: 'run-size' }).textContent =
                `${Math.round(q.cut_w)} × ${Math.round(q.cut_h)}${q.rotated ? ' ↻' : ''}`;
        }
        g.addEventListener('click', ev => { ev.stopPropagation(); runPlay(false); runGo(+g.dataset.step); });
    };
    const figure = (key, hue, cls) => {
        const fig = document.createElement('figure');
        fig.className = 'run-board' + (cls ? ' ' + cls : '');
        fig.dataset.key = key;
        fig.style.setProperty('--hue', hue.toFixed(1));
        fig.addEventListener('click', () => { runPlay(false); runGo(run.steps.findIndex(t => t.key === key)); });
        return fig;
    };
    const caption = (fig, html, title) => {
        const cap = document.createElement('figcaption');
        cap.innerHTML = html;
        if (title) fig.title = title;
        fig.appendChild(cap);
    };
    plan.rows.forEach((row, r) => {
        const boards = plan.boards.filter(b => b.elevation === row.elevation);
        const sec = document.createElement('section');
        sec.className = 'run-row';
        sec.innerHTML = `<h3>${esc(row.elevation)} <span>${row.n_boards} ${unit}${row.n_boards === 1 ? '' : 's'} · waste ${(100 * row.waste).toFixed(1)}%`
            + (row.from_offcuts ? ` · ${row.from_offcuts} cut from offcuts` : '') + `</span></h3>`;
        const line = document.createElement('div');
        line.className = 'run-boards' + (plank ? ' planks' : '');
        // the offcuts carried on that this elevation cuts from, each the piece of board it is
        for (const g of (plan.reuse || []).filter(g => g.elevation === row.elevation)) {
            const [ox, oy, ow, oh] = g.rect, hue = runHue(g.k), key = 'o' + g.id;
            const fig = figure(key, hue, 'run-reuse');
            const W = plank ? bh : ow, H = plank ? ow : oh;
            const R = plank ? (x, y, w, hh) => ({ x: y, y: ow - (x - ox) - w, width: hh, height: w })
                            : (x, y, w, hh) => ({ x: x - ox, y: oh - (y - oy) - hh, width: w, height: hh });
            const svg = sheet(W, H);
            add(svg, 'rect', { x: 0, y: 0, width: W, height: H, class: 'run-stock run-offcut-stock' }).style.setProperty('--hue', hue.toFixed(1));
            g.pieces.forEach((q, j) => drawPiece(svg, R, q, `${g.no}·o${j + 1}`, hue, key, j));
            fig.appendChild(svg);
            caption(fig, plank ? `from<br>L${g.no}` : `Offcut of board ${g.no}`,
                    `Offcut of ${unit} ${g.no} (${plan.boards[g.k].elevation}), ${Math.round(ow)}${plank ? '' : ' × ' + Math.round(oh)} mm`);
            line.appendChild(fig);
        }
        for (const b of boards) {
            const k = plan.boards.indexOf(b), h = runHue(k), key = 'b' + k;
            const fig = figure(key, h);
            // As the DXF draws it: a board flat, a plank's stock length standing up, a plank
            // wide, its pieces from the bottom. W x H is the drawing; R maps a rectangle in
            // board millimetres (x along the board, y up it) to the SVG's (y down).
            const W = plank ? bh : bw, H = plank ? bw : bh;
            const R = plank ? (x, y, w, hh) => ({ x: y, y: bw - x - w, width: hh, height: w })
                            : (x, y, w, hh) => ({ x, y: bh - y - hh, width: w, height: hh });
            const svg = sheet(W, H);
            add(svg, 'rect', { x: 0, y: 0, width: W, height: H, class: 'run-stock' });
            for (const [x, y, w, hh] of b.offcuts) add(svg, 'rect', Object.assign(R(x, y, w, hh), { class: 'run-offcut' }));
            const t = plan.trim || 0;
            if (t) add(svg, 'rect', Object.assign(plank ? R(t, 0, bw - 2 * t, bh) : R(t, t, bw - 2 * t, bh - 2 * t), { class: 'run-trim' }));
            b.pieces.forEach((q, j) => drawPiece(svg, R, q, `${b.no}·${j + 1}`, h, key, j));
            for (const q of b.later || []) {           // cut from this board's offcut, for a later elevation
                const rr = R(q.x, q.y, q.w, q.h), cx = rr.x + rr.width / 2, cy = rr.y + rr.height / 2;
                add(svg, 'rect', Object.assign(rr, { class: 'run-later' }));
                const fs = plank ? Math.min(0.5 * bh, 0.08 * q.w) : Math.max(50, Math.min(120, 0.12 * Math.min(q.w, q.h)));
                add(svg, 'text', Object.assign({ x: cx, y: cy, 'font-size': fs, class: 'run-later-to' },
                                               plank ? { transform: `rotate(-90 ${cx} ${cy})` } : {})).textContent = `→ ${q.elevation}`;
            }
            fig.appendChild(svg);
            const used = Math.round(100 * (1 - b.waste));
            if (plank) caption(fig, `L${b.no}<br>${used}%`, `Length ${b.no} · ${used}% used`);
            else caption(fig, `Board ${b.no} · ${used}% used`);
            line.appendChild(fig);
        }
        // the rack: every offcut left after this elevation, side by side at the same scale
        const rack = plan.racks && plan.racks[r];
        if (rack && rack.offcuts.length) {
            const key = 'r' + r, fig = figure(key, 210, 'run-rack');
            const items = rack.offcuts.map(o => ({ o, W: plank ? o.rect[3] : o.rect[2], H: plank ? o.rect[2] : o.rect[3] }));
            const gap = plank ? 40 : 120, W = items.reduce((a, it) => a + it.W + gap, -gap), H = Math.max(...items.map(it => it.H));
            const svg = sheet(W, H);
            let x = 0;
            for (const { o, W: w, H: hh } of items) {
                const g = add(svg, 'g', { class: 'run-rack-item', 'data-oid': o.id });
                g.style.setProperty('--hue', runHue(o.k).toFixed(1));
                add(g, 'rect', { x, y: H - hh, width: w, height: hh });
                add(g, 'rect', { x, y: H - hh, width: w, height: hh, class: 'run-rack-hatch' });
                const cx = x + w / 2, cy = H - hh / 2, fs = plank ? Math.min(0.5 * w, 0.12 * hh) : Math.max(50, Math.min(140, 0.2 * Math.min(w, hh)));
                add(g, 'text', Object.assign({ x: cx, y: cy, 'font-size': fs, class: 'run-no' }, plank ? { transform: `rotate(-90 ${cx} ${cy})` } : {}))
                    .textContent = (plank ? `L${o.no} ${Math.round(o.rect[2])}` : `${o.no}: ${Math.round(o.rect[2])} × ${Math.round(o.rect[3])}`)
                        + (o.used_by ? ` → ${o.used_by}` : '');
                x += w + gap;
            }
            fig.appendChild(svg);
            const used = rack.offcuts.filter(o => o.used_by).length, n = rack.offcuts.length;
            caption(fig, `Offcuts after ${esc(row.elevation)}: ${n}` + (used ? `, ${used} used next` : ''),
                    plan.share_offcuts ? 'Carried on to the elevations after it' : 'Not passed on: each elevation is cut from its own stock');
            line.appendChild(fig);
        }
        sec.appendChild(line);
        strip.appendChild(sec);
    });
}

function paintStrip(s) {
    document.querySelectorAll('#run-strip .run-board.focus').forEach(f => f.classList.remove('focus'));
    document.querySelectorAll('#run-strip .run-piece.focus, #run-strip .run-piece.on, #run-strip .run-piece.newest, #run-strip .run-rack-item.focus')
        .forEach(p => p.classList.remove('focus', 'on', 'newest'));
    const fig = document.querySelector(`#run-strip .run-board[data-key="${s.key}"]`);
    if (!fig) return;
    fig.classList.add('focus');
    if (s.kind === 'rack') fig.querySelectorAll('.run-rack-item').forEach(p => p.classList.add('focus'));
    // pieces 1 to j of the board lit, the one just added outlined; the rest of the board on
    const first = run.i - s.j;
    fig.querySelectorAll('.run-piece').forEach(p => {
        const step = +p.dataset.step;
        p.classList.add(step >= first && step <= run.i ? 'focus' : 'on');
        if (step === run.i) p.classList.add('newest');
    });
    // a piece cut from an offcut: that offcut on the rack it came from lights up as well
    if (s.oid !== undefined) document.querySelectorAll(`#run-strip .run-rack-item[data-oid="${s.oid}"]`).forEach(p => p.classList.add('focus'));
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
