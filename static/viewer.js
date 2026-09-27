/* CladForge viewer — Three.js scene, web-ifc import, face picking, prism rendering.
 *
 * Scene units are millimetres, Y-up (Three.js). IFC coordinates are Z-up, so
 * Three (x, y, z) <-> IFC (x, -z, y). web-ifc returns metres, scaled here.
 */

let scene, camera, renderer, controls, ifcApi = null;
let modelGroup = null, cladGroup = null, outlineGroup = null, dimGroup = null, highlightGroup = null, cornerGroup = null;
let allMeshes = [], meshMeta = [], modelContext = {};
// The scene works near the origin; modelOffset (IFC mm) puts exports back on the host model.
let modelOffset = [0, 0, 0];
const layerVisible = { model: true, sheathing: true, insulation: true, counter_batten: true,
                       batten: true, cladding: true, closer: true, dims: true,
                       faces: true, outline: true };
// One colour for every picked face and one for every wall outline: which elevation a
// face belongs to is the list's job, not the viewport's.
const PICK_COLOR = 0x2a9d8f, OUTLINE_COLOR = 0x8ea3b8;
const VERT_TOL = Math.sin(Math.PI / 180);   // 1 degree: what counts as a vertical face

// ─── SCENE ───
function initThree() {
    const container = document.getElementById('viewport');
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0a0a1a);
    camera = new THREE.PerspectiveCamera(45, container.clientWidth / container.clientHeight, 10, 2000000);
    camera.position.set(15000, 12000, 15000);
    renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.setSize(container.clientWidth, container.clientHeight);
    container.appendChild(renderer.domElement);
    controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.target.set(0, 1500, 0);
    scene.add(new THREE.AmbientLight(0x404040, 0.9));
    const dir1 = new THREE.DirectionalLight(0xffffff, 0.8);
    dir1.position.set(20000, 30000, 10000);
    scene.add(dir1);
    const dir2 = new THREE.DirectionalLight(0x8888ff, 0.3);
    dir2.position.set(-10000, 10000, -20000);
    scene.add(dir2);
    scene.add(new THREE.GridHelper(40000, 40, 0x222244, 0x111133));
    modelGroup = new THREE.Group(); cladGroup = new THREE.Group(); outlineGroup = new THREE.Group();
    dimGroup = new THREE.Group(); highlightGroup = new THREE.Group(); cornerGroup = new THREE.Group();
    scene.add(modelGroup, cladGroup, outlineGroup, dimGroup, highlightGroup, cornerGroup);
    window.addEventListener('resize', () => {
        camera.aspect = container.clientWidth / container.clientHeight;
        camera.updateProjectionMatrix();
        if (orthoCamera) {         // keep the flat view's height, widen or narrow it
            const h = orthoCamera.top - orthoCamera.bottom;
            orthoCamera.left = -h * camera.aspect / 2; orthoCamera.right = h * camera.aspect / 2;
            orthoCamera.updateProjectionMatrix();
        }
        renderer.setSize(container.clientWidth, container.clientHeight);
    });
    (function animate() {
        requestAnimationFrame(animate);
        controls.update();
        if (view2d.on && view2d.onFrame) view2d.onFrame();     // the 2D labels follow the view
        renderer.render(scene, activeCamera());
    })();
}

function clearGroup(group) {
    while (group.children.length) {
        const c = group.children.pop();
        c.traverse(o => { if (o.geometry) o.geometry.dispose(); if (o.material && o.material.dispose) o.material.dispose(); });
    }
}

// ─── WEB-IFC IMPORT ───
async function initWebIfc() {
    try { ifcApi = new WebIFC.IfcAPI(); await ifcApi.Init(); }
    catch (err) { console.error('web-ifc init failed', err); ifcApi = null; }
}

let _typeNames = null;
function ifcTypeName(code) {
    if (!_typeNames) {
        _typeNames = {};
        for (const k of Object.keys(WebIFC)) if (typeof WebIFC[k] === 'number' && k.startsWith('IFC')) _typeNames[WebIFC[k]] = k;
    }
    return _typeNames[code] || '';
}

function attr(line, name) {
    const v = line && line[name];
    return (v && typeof v === 'object' && 'value' in v) ? v.value : (v === undefined ? null : v);
}

function readSpatial(modelID, unitScale) {
    const elemStorey = {}, storeys = {}, ctx = {};
    try {
        const names = { IFCPROJECT: 'project', IFCSITE: 'site', IFCBUILDING: 'building' };
        for (const key of Object.keys(names)) {
            const ids = ifcApi.GetLineIDsWithType(modelID, WebIFC[key]);
            if (ids.size() > 0) ctx[names[key]] = attr(ifcApi.GetLine(modelID, ids.get(0)), 'Name') || '';
        }
        const rels = ifcApi.GetLineIDsWithType(modelID, WebIFC.IFCRELCONTAINEDINSPATIALSTRUCTURE);
        for (let i = 0; i < rels.size(); i++) {
            const rel = ifcApi.GetLine(modelID, rels.get(i));
            const sid = attr(rel, 'RelatingStructure');
            if (!storeys[sid]) {
                const st = ifcApi.GetLine(modelID, sid);
                storeys[sid] = { name: attr(st, 'Name') || ('Storey ' + sid), elevation: (parseFloat(attr(st, 'Elevation')) || 0) * unitScale };
            }
            for (const r of (rel.RelatedElements || [])) elemStorey[r.value] = storeys[sid];
        }
    } catch (e) { console.warn('spatial read failed', e); }
    return { elemStorey, ctx };
}

function readLengthUnitScale(modelID) {
    // mm per model length unit, from IfcSIUnit (default: metres).
    try {
        const ids = ifcApi.GetLineIDsWithType(modelID, WebIFC.IFCSIUNIT);
        for (let i = 0; i < ids.size(); i++) {
            const u = ifcApi.GetLine(modelID, ids.get(i));
            if (attr(u, 'UnitType') === 'LENGTHUNIT') {
                const prefix = attr(u, 'Prefix');
                return prefix === 'MILLI' ? 1 : (prefix === 'CENTI' ? 10 : (prefix === 'DECI' ? 100 : 1000));
            }
        }
    } catch (e) { /* fall through */ }
    return 1000;
}

// ─── IMPORT ───
// Two readers produce the same raw form: {verts (IFC mm, Z-up), idx, expressID,
// type, name, storey}. web-ifc runs in the browser and keeps the file private;
// the server reader uses IfcOpenShell and builds geometry web-ifc cannot.

async function readWithWebIfc(file, onStatus) {
    if (!ifcApi) throw new Error('web-ifc is still loading');
    onStatus('Parsing IFC in the browser…');
    const data = new Uint8Array(await file.arrayBuffer());
    let modelID = null;
    try {
        modelID = ifcApi.OpenModel(data, { COORDINATE_TO_ORIGIN: false });
    } catch (err) {
        throw new Error('web-ifc could not open the file: ' + err.message);
    }
    const spatial = readSpatial(modelID, 1000);
    const raw = [];
    let failed = 0;
    try {
        ifcApi.StreamAllMeshes(modelID, (mesh) => {
            // One unbuildable element must not abandon the file.
            try {
                const verts = [], idx = [];
                let off = 0;
                for (let i = 0; i < mesh.geometries.size(); i++) {
                    const pg = mesh.geometries.get(i);
                    const g = ifcApi.GetGeometry(modelID, pg.geometryExpressID);
                    const v = ifcApi.GetVertexArray(g.GetVertexData(), g.GetVertexDataSize());
                    const ix = ifcApi.GetIndexArray(g.GetIndexData(), g.GetIndexDataSize());
                    g.delete();
                    if (!v.length || !ix.length) continue;
                    const m = new THREE.Matrix4().fromArray(pg.flatTransformation);
                    const n = v.length / 6;
                    const p = new THREE.Vector3();
                    for (let k = 0; k < n; k++) {
                        p.set(v[k * 6], v[k * 6 + 1], v[k * 6 + 2]).applyMatrix4(m);
                        verts.push(p.x * 1000, p.y * 1000, p.z * 1000);   // web-ifc emits metres
                    }
                    for (let k = 0; k < ix.length; k++) idx.push(ix[k] + off);
                    off += n;
                }
                if (!verts.length) return;
                let type = '', name = '';
                try { type = ifcTypeName(ifcApi.GetLineType(modelID, mesh.expressID)); } catch (e) { /* unknown */ }
                try { name = attr(ifcApi.GetLine(modelID, mesh.expressID), 'Name') || ''; } catch (e) { /* unnamed */ }
                raw.push({ verts, idx, expressID: mesh.expressID, type, name,
                           storey: spatial.elemStorey[mesh.expressID] || null });
            } catch (err) { failed++; }
        });
    } catch (err) {
        // The stream itself died: keep whatever was collected and let the caller decide.
        if (!raw.length) { try { ifcApi.CloseModel(modelID); } catch (e) { /* gone */ } ifcApi = null; initWebIfc();
                           throw new Error('web-ifc failed while reading geometry: ' + err.message); }
        failed++;
    }
    try { ifcApi.CloseModel(modelID); } catch (e) { /* already closed */ }
    if (!raw.length) throw new Error('web-ifc found no geometry in this file');
    return { raw, context: spatial.ctx, failed, reader: 'web-ifc' };
}

function b64ToArray(b64, Type) {
    const bin = atob(b64), bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new Type(bytes.buffer);
}

async function readWithServer(file, onStatus) {
    onStatus('Building geometry on the server…');
    const body = new FormData();
    body.append('file', file, file.name);
    const resp = await fetch('api/import', { method: 'POST', body });
    if (!resp.ok) throw new Error('server importer returned ' + resp.status);
    const data = await resp.json();
    if (!data.success) throw new Error(data.error || 'server importer failed');
    const raw = data.elements.map(e => ({
        verts: b64ToArray(e.verts, Float32Array), idx: b64ToArray(e.idx, Uint32Array),
        expressID: e.expressID, type: e.type, name: e.name, storey: e.storey }));
    if (!raw.length) throw new Error((data.warnings || []).join(' ') || 'no geometry in this file');
    return { raw, context: data.context || {}, failed: (data.failed || []).length,
             warnings: data.warnings || [], reader: 'IfcOpenShell (server)' };
}

function modelScale(raw) {
    // Judge the unit by the model's SIZE, never by how far it sits from the origin:
    // a georeferenced model has huge coordinates but ordinary dimensions.
    let lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    for (const r of raw) for (let k = 0; k < r.verts.length; k += 3)
        for (let a = 0; a < 3; a++) { const v = r.verts[k + a]; if (v < lo[a]) lo[a] = v; if (v > hi[a]) hi[a] = v; }
    const diag = Math.hypot(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]);
    for (const s of [1, 1000, 0.001]) if (diag * s > 1000 && diag * s < 5e6) return { scale: s, lo, hi, diag };
    return { scale: 1, lo, hi, diag };    // implausible either way: leave it alone and say so
}

function buildScene(raw, context, warnings) {
    clearGroup(modelGroup); clearGroup(highlightGroup);
    allMeshes = []; meshMeta = [];
    modelContext = context || {};
    const { scale, lo, hi, diag } = modelScale(raw);
    if (scale !== 1) warnings.push('Model read as ' + (scale === 1000 ? 'metres' : 'kilometres') + ' and scaled to mm.');
    if (diag * scale <= 1000 || diag * scale >= 5e6)
        warnings.push('Model is ' + Math.round(diag * scale) + ' mm across, which looks wrong for a building.');
    // Work near the origin: float32 loses millimetres on site coordinates in the
    // hundreds of thousands, which wrecks picking and extraction.
    modelOffset = [Math.round((lo[0] + hi[0]) / 2 * scale), Math.round((lo[1] + hi[1]) / 2 * scale),
                   Math.round(lo[2] * scale)];
    for (const r of raw) {
        const pos = new Float32Array(r.verts.length);
        for (let k = 0; k < r.verts.length; k += 3) {
            pos[k] = r.verts[k] * scale - modelOffset[0];
            pos[k + 1] = r.verts[k + 1] * scale - modelOffset[1];
            pos[k + 2] = r.verts[k + 2] * scale - modelOffset[2];
        }
        const geo = new THREE.BufferGeometry();
        geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
        geo.setIndex(new THREE.BufferAttribute(new Uint32Array(r.idx), 1));
        geo.computeVertexNormals();
        geo.computeBoundingBox();
        const isWall = /WALL/i.test(r.type);
        const mat = new THREE.MeshPhongMaterial({ color: isWall ? 0xd8d8d8 : 0xa8a8b8, flatShading: true,
                                                  side: THREE.DoubleSide, transparent: true, opacity: 0.95 });
        const th = new THREE.Mesh(geo, mat);
        th.userData.index = allMeshes.length;
        modelGroup.add(th);
        allMeshes.push(th);
        meshMeta.push({ mesh: th, expressID: r.expressID, type: r.type, name: r.name, storey: r.storey });
    }
    fitCameraTo(modelGroup);
    return new Set(meshMeta.filter(m => m.storey).map(m => m.storey.name)).size;
}

async function loadIFC(file, onStatus, force) {
    // Browser first, server second. Either reader alone is enough to work with.
    const warnings = [];
    let read = null;
    if (force !== 'server') {
        try { read = await readWithWebIfc(file, onStatus); }
        catch (err) { warnings.push('Browser importer: ' + err.message); }
    }
    if (!read) {
        try { read = await readWithServer(file, onStatus); }
        catch (err) {
            warnings.push('Server importer: ' + err.message);
            throw new Error(warnings.join(' | '));
        }
    }
    warnings.push(...(read.warnings || []));
    if (read.failed) warnings.push(read.failed + ' element(s) could not be built and were skipped.');
    const storeys = buildScene(read.raw, read.context, warnings);
    return { meshes: allMeshes.length, storeys, context: modelContext, reader: read.reader,
             warnings, offset: modelOffset };
}

function fitCameraTo(obj) {
    reset2D();
    const bb = new THREE.Box3().setFromObject(obj);
    if (bb.isEmpty()) return;
    const c = bb.getCenter(new THREE.Vector3());
    const size = bb.getSize(new THREE.Vector3()).length();
    camera.position.set(c.x + size * 0.7, c.y + size * 0.5, c.z + size * 0.7);
    controls.target.copy(c);
    controls.update();
}

function setModelVisible(on) { layerVisible.model = on; modelGroup.visible = on; }

// ─── PICKING ───
function pickDim(event) {
    if (!dimLabels.length || !dimGroup.visible || view2d.on) return null;    // flat, labels are HTML
    const rect = renderer.domElement.getBoundingClientRect();
    const mouse = new THREE.Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1,
                                    -((event.clientY - rect.top) / rect.height) * 2 + 1);
    const rc = new THREE.Raycaster();
    rc.setFromCamera(mouse, activeCamera());
    const hits = rc.intersectObjects(dimLabels, false);
    return hits.length ? hits[0].object.userData.dim : null;
}

// ─── SNAPPING ───
// The level picker shows where a click will land: a red dot on a surface, a green one
// when it has snapped to a corner of the face under the cursor.
const SNAP_PX = 14;          // how close, on screen, a corner has to be to snap
let snapMarker = null;

function makeDot(hex) {
    const c = document.createElement('canvas'); c.width = c.height = 64;
    const g = c.getContext('2d');
    g.beginPath(); g.arc(32, 32, 24, 0, Math.PI * 2);
    g.fillStyle = hex; g.fill(); g.lineWidth = 6; g.strokeStyle = '#ffffff'; g.stroke();
    const m = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), depthTest: false, sizeAttenuation: false }));
    m.scale.set(0.035, 0.035, 1);
    m.renderOrder = 999;
    return m;
}

function snapPick(event) {
    const hit = pickAt(event);
    if (!hit) return null;
    const rect = renderer.domElement.getBoundingClientRect();
    const mx = event.clientX - rect.left, my = event.clientY - rect.top;
    const pos = hit.mesh.geometry.attributes.position;
    let best = null, bestD = SNAP_PX;
    for (const k of ['a', 'b', 'c']) {
        const vi = hit.face[k];
        const v = new THREE.Vector3(pos.getX(vi), pos.getY(vi), pos.getZ(vi)).applyMatrix4(hit.mesh.matrixWorld);
        const sp = v.clone().project(activeCamera());
        const d = Math.hypot((sp.x + 1) / 2 * rect.width - mx, (1 - sp.y) / 2 * rect.height - my);
        if (d < bestD) { bestD = d; best = v; }
    }
    return { point: best || hit.point, snapped: !!best };
}

function showSnap(snap) {
    if (snapMarker) { scene.remove(snapMarker); snapMarker = null; }
    if (!snap) return;
    snapMarker = makeDot(snap.snapped ? '#22c55e' : '#ef4444');
    snapMarker.position.copy(snap.point);
    scene.add(snapMarker);
}

function pickAt(event) {
    const rect = renderer.domElement.getBoundingClientRect();
    const mouse = new THREE.Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1,
                                    -((event.clientY - rect.top) / rect.height) * 2 + 1);
    const rc = new THREE.Raycaster();
    rc.setFromCamera(mouse, activeCamera());
    const hits = rc.intersectObjects(allMeshes, false);
    if (!hits.length) return null;
    const hit = hits[0];
    const normal = hit.face.normal.clone().applyMatrix3(new THREE.Matrix3().getNormalMatrix(hit.object.matrixWorld)).normalize();
    if (normal.dot(activeCamera().position.clone().sub(hit.point)) < 0) normal.negate();   // face the viewer
    return { mesh: hit.object, faceIndex: hit.faceIndex, face: hit.face, point: hit.point, normal };
}

function faceNormals(geo) {
    const pos = geo.attributes.position.array, idx = geo.index ? geo.index.array : null;
    const count = idx ? idx.length / 3 : pos.length / 9, out = new Array(count);
    const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
    for (let f = 0; f < count; f++) {
        const i0 = (idx ? idx[f * 3] : f * 3) * 3, i1 = (idx ? idx[f * 3 + 1] : f * 3 + 1) * 3, i2 = (idx ? idx[f * 3 + 2] : f * 3 + 2) * 3;
        a.set(pos[i0], pos[i0 + 1], pos[i0 + 2]); b.set(pos[i1], pos[i1 + 1], pos[i1 + 2]); c.set(pos[i2], pos[i2 + 1], pos[i2 + 2]);
        out[f] = b.sub(a).cross(c.sub(a)).normalize().clone();
    }
    return out;
}

function coplanarFaces(mesh, hitFaceIndex) {
    // Flood fill from the hit face over faces that share a vertex *position* (web-ifc
    // duplicates vertices per triangle, so index adjacency would stop at every fan),
    // within 1 degree of the hit normal and on the same plane. Vertical faces only.
    const geo = mesh.geometry, pos = geo.attributes.position.array, idx = geo.index ? geo.index.array : null;
    const normals = faceNormals(geo), hitN = normals[hitFaceIndex];
    if (Math.abs(hitN.y) >= VERT_TOL) return null;
    const count = normals.length, vertToFaces = new Map();
    const keyOf = vi => { const p = vi * 3; return Math.round(pos[p] * 10) + ',' + Math.round(pos[p + 1] * 10) + ',' + Math.round(pos[p + 2] * 10); };
    for (let f = 0; f < count; f++) for (let v = 0; v < 3; v++) {
        const k = keyOf(idx ? idx[f * 3 + v] : f * 3 + v);
        if (!vertToFaces.has(k)) vertToFaces.set(k, []);
        vertToFaces.get(k).push(f);
    }
    const vi0 = (idx ? idx[hitFaceIndex * 3] : hitFaceIndex * 3) * 3;
    const d0 = hitN.x * pos[vi0] + hitN.y * pos[vi0 + 1] + hitN.z * pos[vi0 + 2];
    const collected = new Set([hitFaceIndex]), queue = [hitFaceIndex];
    while (queue.length) {
        const f = queue.shift();
        for (let v = 0; v < 3; v++) {
            const k = keyOf(idx ? idx[f * 3 + v] : f * 3 + v);
            for (const nb of vertToFaces.get(k) || []) {
                if (collected.has(nb)) continue;
                const n = normals[nb];
                const p = (idx ? idx[nb * 3] : nb * 3) * 3;
                const d = hitN.x * pos[p] + hitN.y * pos[p + 1] + hitN.z * pos[p + 2];
                if (Math.abs(n.dot(hitN)) >= 1 - VERT_TOL && Math.abs(n.y) < VERT_TOL && Math.abs(d - d0) < 5) {
                    collected.add(nb); queue.push(nb);
                }
            }
        }
    }
    return Array.from(collected);
}

function toIfc(v) { return [v.x, -v.z, v.y]; }

function faceTriangles(mesh, faceIndices) {
    mesh.updateWorldMatrix(true, false);
    const geo = mesh.geometry, pos = geo.attributes.position.array, idx = geo.index ? geo.index.array : null;
    const wm = mesh.matrixWorld, out = [], p = new THREE.Vector3();
    for (const f of faceIndices) {
        const tri = [];
        for (let v = 0; v < 3; v++) {
            const vi = (idx ? idx[f * 3 + v] : f * 3 + v) * 3;
            p.set(pos[vi], pos[vi + 1], pos[vi + 2]).applyMatrix4(wm);
            tri.push(toIfc(p));
        }
        out.push(tri);
    }
    return out;
}

function meshTriangles(mesh) {
    const geo = mesh.geometry, idx = geo.index ? geo.index.array : null;
    const count = idx ? idx.length / 3 : geo.attributes.position.count / 3;
    return faceTriangles(mesh, Array.from({ length: count }, (_, i) => i));
}

const CONTEXT_TRI_BUDGET = 60000;   // the engine runs in a fixed WASM heap: past this it dies

function contextFor(pickedMeshes, tris, margin, outward) {
    // Every other element that could touch the picked face: its bounding box overlaps the
    // faces' box (grown by *margin*) AND some corner of it lies within reach of the face
    // plane, so elements wholly behind or wholly in front of the wall are never sent.
    const bb = new THREE.Box3();
    for (const t of tris) for (const q of t) bb.expandByPoint(new THREE.Vector3(q[0], q[2], -q[1]));
    bb.expandByScalar(margin);
    const n = new THREE.Vector3(outward[0], outward[2], -outward[1]).normalize();
    const d = n.dot(new THREE.Vector3(tris[0][0][0], tris[0][0][2], -tris[0][0][1]));
    const near = [];
    for (const meta of meshMeta) {
        if (pickedMeshes.has(meta.mesh)) continue;
        const mb = meta.mesh.geometry.boundingBox.clone().applyMatrix4(meta.mesh.matrixWorld);
        if (!mb.intersectsBox(bb)) continue;
        let lo = Infinity, hi = -Infinity;
        for (const x of [mb.min.x, mb.max.x]) for (const y of [mb.min.y, mb.max.y]) for (const z of [mb.min.z, mb.max.z]) {
            const s = n.x * x + n.y * y + n.z * z - d; lo = Math.min(lo, s); hi = Math.max(hi, s);
        }
        if (hi < -margin || lo > margin) continue;
        near.push({ meta, reach: Math.min(Math.abs(lo), Math.abs(hi)) });   // how close it comes to the face
    }
    // A big model can offer far more geometry than the engine's heap will hold, and
    // overrunning it kills the runtime outright rather than raising. Send the elements
    // that come closest to the face and report the rest rather than risking that.
    near.sort((a, b) => a.reach - b.reach);
    const elements = [];
    let used = 0, dropped = 0;
    for (const { meta } of near) {
        const t = meshTriangles(meta.mesh);
        if (used && used + t.length > CONTEXT_TRI_BUDGET) { dropped++; continue; }
        used += t.length;
        elements.push({ type: meta.type, name: meta.name, tris: t });
    }
    return { elements, dropped, triangles: used };
}

function highlightFaces(mesh, faceIndices, color = PICK_COLOR) {
    const tris = faceTriangles(mesh, faceIndices), positions = [];
    for (const t of tris) for (const q of t) positions.push(q[0], q[2], -q[1]);
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    const hl = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.45, side: THREE.DoubleSide, depthTest: false }));
    highlightGroup.add(hl);
    return hl;
}

// ─── PRISM RENDERING ───
function frameMatrix(frame, depth) {
    const u = new THREE.Vector3(frame.u[0], 0, -frame.u[1]);
    const n = new THREE.Vector3(frame.n[0], 0, -frame.n[1]);
    const o = new THREE.Vector3(frame.origin[0], frame.origin[2], -frame.origin[1]).addScaledVector(n, depth || 0);
    return new THREE.Matrix4().makeBasis(u, new THREE.Vector3(0, 1, 0), n).setPosition(o);
}

function shapeFromRings(profile, holes) {
    const shape = new THREE.Shape(profile.map(p => new THREE.Vector2(p[0], p[1])));
    for (const h of holes || []) shape.holes.push(new THREE.Path(h.map(p => new THREE.Vector2(p[0], p[1]))));
    return shape;
}

const _layerOf = { sheathing: 'sheathing', insulation: 'insulation', counter_batten: 'counter_batten',
                   batten: 'batten', cross_batten: 'batten', panel: 'cladding', plank: 'cladding',
                   reveal: 'cladding', closer: 'closer' };

function applyCorner(geo, m, tol = 0.6) {
    // Move the vertices on a corner end to u_end -/+ (ext + k x depth): k shears the
    // end onto the corner's bisector (a mitre), ext runs it past square (a lap).
    // Local z runs 0..thickness along the wall normal, so a vertex is at m.depth + z.
    const c = m.corner;
    if (!c) return;
    const pos = geo.attributes.position;
    for (let i = 0; i < pos.count; i++) {
        const x = pos.getX(i), s = m.depth + pos.getZ(i);
        if ((c.k_l || c.ext_l) && Math.abs(x - c.u_l) < tol) pos.setX(i, x - ((c.ext_l || 0) + (c.k_l || 0) * s));
        else if ((c.k_r || c.ext_r) && Math.abs(x - c.u_r) < tol) pos.setX(i, x + ((c.ext_r || 0) + (c.k_r || 0) * s));
    }
    pos.needsUpdate = true;
    geo.computeVertexNormals();
}

function highlightCorner(frame, u, height) {
    // Flash the corner edge so a row in the panel points at something in the model.
    clearGroup(cornerGroup);
    const M = frameMatrix(frame, 0);
    const pts = [new THREE.Vector3(u, 0, 0), new THREE.Vector3(u, height, 0)].map(v => v.applyMatrix4(M));
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),
                                new THREE.LineBasicMaterial({ color: 0xffd166, depthTest: false, linewidth: 2 }));
    cornerGroup.add(line);
    if (view2d.on) return;            // flat, the view stays square-on; the line is enough
    const mid = pts[0].clone().lerp(pts[1], 0.5);
    controls.target.copy(mid);
    controls.update();
}

function renderGeometry(meshes) {
    clearGroup(cladGroup);
    const byLayer = {};
    for (const m of meshes) {
        const layer = _layerOf[m.ifc_type] || 'cladding';
        if (!byLayer[layer]) { byLayer[layer] = new THREE.Group(); byLayer[layer].name = layer; cladGroup.add(byLayer[layer]); }
        let geo;
        try { geo = new THREE.ExtrudeGeometry(shapeFromRings(m.profile, m.holes), { depth: m.thickness, bevelEnabled: false }); }
        catch (e) { continue; }
        applyCorner(geo, m);
        const mat = new THREE.MeshPhongMaterial({ color: new THREE.Color(m.color), flatShading: true, transparent: m.opacity < 1,
                                                  opacity: m.opacity, side: THREE.DoubleSide, depthWrite: m.opacity >= 1 });
        const mesh = new THREE.Mesh(geo, mat);
        mesh.matrixAutoUpdate = false;
        mesh.matrix.copy(frameMatrix(m.frame, m.depth));
        mesh.userData.name = m.name;
        mesh.userData.elevation = m.elevation;
        byLayer[layer].add(mesh);
        if (m.opacity >= 1 || m.ifc_type === 'panel' || m.ifc_type === 'plank') {
            const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geo, 30), new THREE.LineBasicMaterial({ color: 0x1b2a3a, transparent: true, opacity: 0.6 }));
            edges.matrixAutoUpdate = false; edges.matrix.copy(mesh.matrix);
            edges.userData.elevation = m.elevation;
            byLayer[layer].add(edges);
        }
    }
    for (const key of Object.keys(byLayer)) byLayer[key].visible = layerVisible[key] !== false;
    filter2D();          // flat, only the elevation being looked at is drawn
}

function ringLine(ring, frame, depth, color, dashed) {
    const pts = ring.map(p => new THREE.Vector3(p[0], p[1], 0));
    pts.push(pts[0].clone());
    const geo = new THREE.BufferGeometry().setFromPoints(pts);
    const mat = dashed ? new THREE.LineDashedMaterial({ color, dashSize: 60, gapSize: 40 }) : new THREE.LineBasicMaterial({ color });
    const line = new THREE.Line(geo, mat);
    if (dashed) line.computeLineDistances();
    line.matrixAutoUpdate = false; line.matrix.copy(frameMatrix(frame, depth));
    return line;
}

function renderOutlines(elevations, splash) {
    clearGroup(outlineGroup);
    elevations.forEach(e => {
        for (const poly of e.polygons) {
            outlineGroup.add(ringLine(poly.exterior, e.frame, 2, OUTLINE_COLOR, false));
            for (const h of poly.holes) outlineGroup.add(ringLine(h, e.frame, 2, 0xff5c5c, false));
        }
        for (const ab of e.abutments) {
            if (!ab.enabled || splash <= 0) continue;
            // The band follows the abutment line: level for slabs, sloped where a roof pitches.
            const line = ab.line || [[ab.u0, ab.v], [ab.u1, ab.v]];
            const ring = line.concat(line.slice().reverse().map(p => [p[0], p[1] + splash]));
            outlineGroup.add(ringLine(ring, e.frame, 3, 0xe94560, true));
        }
    });
}

function makeLabel(text, editable) {
    const canvas = document.createElement('canvas'), ctx = canvas.getContext('2d');
    if (editable) text += '  \u270e';
    ctx.font = 'bold 40px sans-serif';
    canvas.width = Math.ceil(ctx.measureText(text).width) + 24; canvas.height = 56;
    ctx.font = 'bold 40px sans-serif'; ctx.fillStyle = editable ? 'rgba(0,80,110,0.85)' : 'rgba(10,10,26,0.7)';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    if (editable) { ctx.strokeStyle = '#00ccff'; ctx.lineWidth = 3; ctx.strokeRect(1.5, 1.5, canvas.width - 3, canvas.height - 3); }
    ctx.fillStyle = '#00ccff'; ctx.textBaseline = 'middle'; ctx.fillText(text, 12, 28);
    const tex = new THREE.CanvasTexture(canvas);
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false }));
    sprite.scale.set(canvas.width * 4, canvas.height * 4, 1);
    return sprite;
}

let dimLabels = [];

// Dimensions belong to one elevation at a time: the whole model's worth at once is
// unreadable, so only the active elevation's are drawn.
function renderDimensions(dims, elevByName, only) {
    clearGroup(dimGroup);
    dimLabels = [];
    for (const d of dims) {
        const e = elevByName[d.elevation];
        if (!e || (only && d.elevation !== only)) continue;
        const M = frameMatrix(e.frame, 60);
        const [nx, ny] = d.norm, off = d.offset;
        const p1 = new THREE.Vector3(d.p1[0], d.p1[1], 0), p2 = new THREE.Vector3(d.p2[0], d.p2[1], 0);
        const q1 = new THREE.Vector3(d.p1[0] + nx * off, d.p1[1] + ny * off, 0), q2 = new THREE.Vector3(d.p2[0] + nx * off, d.p2[1] + ny * off, 0);
        // extension lines p→q at each end, then the dimension line q1→q2
        const pts = [p1, q1, p2, q2, q1, q2].map(v => v.clone().applyMatrix4(M));
        const line = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(pts),
                                            new THREE.LineBasicMaterial({ color: 0x00ccff, depthTest: false }));
        dimGroup.add(line);
        // In 3D only the courses and rows open the course dialog; the rest are edited flat.
        const editable = d.kind === 'course' || d.kind === 'row';
        const label = makeLabel(d.label, editable);
        label.position.copy(new THREE.Vector3((d.p1[0] + d.p2[0]) / 2 + nx * (off + 120), (d.p1[1] + d.p2[1]) / 2 + ny * (off + 120), 0).applyMatrix4(M));
        label.visible = !view2d.on;        // flat, HTML labels take over (dims2d.js)
        dimGroup.add(label);
        if (editable) { label.userData.dim = d; dimLabels.push(label); }
    }
    dimGroup.visible = layerVisible.dims || view2d.on;    // flat, the dimensions are the point
}

function setLayerVisible(key, on) {
    layerVisible[key] = on;
    if (key === 'model') { modelGroup.visible = on; return; }
    if (key === 'dims') { dimGroup.visible = on || view2d.on; return; }
    if (key === 'faces') { highlightGroup.visible = on; return; }     // the picked wall surface
    if (key === 'outline') { outlineGroup.visible = on; return; }     // outline, openings, splash
    for (const g of cladGroup.children) if (g.name === key) g.visible = on;
}

// Nothing in the app calls this: picking a face leaves the camera alone. It stays as a
// viewer utility, used by the browser test to put a known elevation on screen.
function frameElevation(e) {
    // Three-quarter view of one elevation, looking at the face from outside.
    reset2D();
    const M = frameMatrix(e.frame, 0);
    const c = new THREE.Vector3(e.width / 2, e.height / 2, 0).applyMatrix4(M);
    const dist = Math.max(e.width, e.height) * 1.2;
    const eye = new THREE.Vector3(e.width / 2 + dist * 0.55, e.height / 2 + dist * 0.35, dist * 0.8).applyMatrix4(M);
    camera.position.copy(eye);
    controls.target.copy(c);
    controls.update();
}
