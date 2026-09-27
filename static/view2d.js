/* CladForge 2D elevation view — the active elevation seen flat and square-on.
 *
 * The only way in is a smooth move from wherever the 3D camera is: the perspective
 * camera swings round on a sphere to face the elevation, then hands over to an
 * orthographic camera sized to what it saw, so there is no jump. Out is the reverse.
 * Nothing is recomputed either way: this is a view of the geometry already built.
 */

const VIEW2D_MS = 600;          // one transition
const VIEW2D_DIMS = 1500;       // mm around the clad region kept clear for the dimensions
const VIEW2D_MARGIN = 1.1;      // and a little more round that
const VIEW2D_FADE = 0.2;        // host model opacity while flat

let orthoCamera = null;
const view2d = { on: false, name: null, saved: null, anim: null, faded: [], onFrame: null };

// The camera the controls drive is the one on screen: perspective in 3D, orthographic in 2D.
function activeCamera() { return controls ? controls.object : camera; }

function in2D() { return view2d.on; }

const easeInOut = t => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

function currentPose() {
    const off = camera.position.clone().sub(controls.target);
    return { target: controls.target.clone(), dir: off.clone().normalize(), dist: off.length() };
}

// Square-on pose for an elevation: on its outward normal, looking at the middle of the clad
// region {u0, u1, v0, v1}, far enough back that the region and its dimensions fit.
function elevationPose(frame, box) {
    const M = frameMatrix(frame, 0);
    const target = new THREE.Vector3((box.u0 + box.u1) / 2, (box.v0 + box.v1) / 2, 0).applyMatrix4(M);
    const dir = new THREE.Vector3(frame.n[0], 0, -frame.n[1]).normalize();
    const w = box.u1 - box.u0 + 2 * VIEW2D_DIMS, h = box.v1 - box.v0 + 2 * VIEW2D_DIMS;
    const tan = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
    const dist = Math.max(h / 2 / tan, w / 2 / (tan * camera.aspect)) * VIEW2D_MARGIN;
    return { target, dir, dist, w, h };
}

// Move the perspective camera from pose a to pose b. The direction turns on the sphere
// round the target (a slerp), so the camera swings round the model instead of cutting
// through it; target and distance ease across in step.
function animatePose(a, b, done) {
    if (view2d.anim) cancelAnimationFrame(view2d.anim);
    const q = new THREE.Quaternion();
    if (a.dir.dot(b.dir) < -0.999) q.setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI);   // straight behind: go round
    else q.setFromUnitVectors(a.dir, b.dir);
    // A transition can cut in on another, so keep the damping setting from before the first.
    if (view2d.damping === undefined) view2d.damping = controls.enableDamping;
    const start = performance.now();
    controls.enabled = false;
    controls.enableDamping = false;
    const step = now => {
        const t = easeInOut(Math.min(1, (now - start) / VIEW2D_MS));
        const dir = a.dir.clone().applyQuaternion(new THREE.Quaternion().slerp(q, t));
        const target = a.target.clone().lerp(b.target, t);
        camera.position.copy(target).addScaledVector(dir, a.dist + (b.dist - a.dist) * t);
        camera.up.set(0, 1, 0);
        controls.target.copy(target);
        camera.lookAt(target);
        if (t < 1) { view2d.anim = requestAnimationFrame(step); return; }
        view2d.anim = null;
        controls.enableDamping = view2d.damping;
        view2d.damping = undefined;
        controls.enabled = true;
        done && done();
    };
    view2d.anim = requestAnimationFrame(step);
}

// Hand over to the orthographic camera at the flat pose. Its frustum is what the
// perspective camera saw at the target distance, so the swap is invisible; then the
// zoom fits the region plus its dimensions.
function goOrtho(pose) {
    const tan = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
    const H = 2 * pose.dist * tan, W = H * camera.aspect;
    if (!orthoCamera) orthoCamera = new THREE.OrthographicCamera(-1, 1, 1, -1, 10, 2000000);
    Object.assign(orthoCamera, { left: -W / 2, right: W / 2, top: H / 2, bottom: -H / 2 });
    orthoCamera.zoom = Math.min(W / pose.w, H / pose.h) / VIEW2D_MARGIN;
    orthoCamera.position.copy(camera.position);
    orthoCamera.up.set(0, 1, 0);
    orthoCamera.lookAt(pose.target);
    orthoCamera.updateProjectionMatrix();
    controls.object = orthoCamera;
    controls.enableRotate = false;
    controls.target.copy(pose.target);
    controls.update();
}

// Back to perspective at the flat pose the orthographic camera shows now (panned and
// zoomed as it may be), so leaving 2D starts from exactly what is on screen.
function goPerspective() {
    if (controls.object !== orthoCamera) return;
    const tan = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
    const H = (orthoCamera.top - orthoCamera.bottom) / orthoCamera.zoom;
    const dir = orthoCamera.position.clone().sub(controls.target).normalize();
    camera.position.copy(controls.target).addScaledVector(dir, H / 2 / tan);
    camera.up.set(0, 1, 0);
    camera.lookAt(controls.target);
    controls.object = camera;
    controls.enableRotate = true;
    controls.update();
}

// Fade the host model, hide every other elevation's cladding, and show the dimensions
// whatever the legend says. Undone on the way out.
function apply2DLook(on) {
    if (on && !view2d.faded.length) {
        modelGroup.traverse(o => {
            if (!o.material) return;
            view2d.faded.push([o.material, o.material.opacity, o.material.depthWrite]);
            o.material.opacity = VIEW2D_FADE; o.material.depthWrite = false;
        });
    } else if (!on) {
        for (const [mat, opacity, depthWrite] of view2d.faded) { mat.opacity = opacity; mat.depthWrite = depthWrite; }
        view2d.faded = [];
    }
    filter2D();
    dimGroup.visible = on || layerVisible.dims;
    // Flat, the labels are HTML over the view (dims2d.js); the sprites are for 3D.
    for (const o of dimGroup.children) if (o.isSprite) o.visible = !on;
}

// Called after every render of the cladding too, since a preview rebuilds it.
function filter2D() {
    for (const layer of cladGroup.children)
        for (const o of layer.children) o.visible = !view2d.on || o.userData.elevation === view2d.name;
}

function enter2D(name, frame, box) {
    if (view2d.on && name === view2d.name) return;     // already looking at it
    if (view2d.on) return frameTo2D(name, frame, box);
    view2d.on = true;
    view2d.name = name;
    view2d.saved = { pos: camera.position.clone(), target: controls.target.clone() };
    apply2DLook(true);
    const pose = elevationPose(frame, box);
    animatePose(currentPose(), pose, () => goOrtho(pose));
}

// A different elevation while flat: swing across to it, flat again at the end.
function frameTo2D(name, frame, box) {
    if (!view2d.on) return;
    goPerspective();
    view2d.name = name;
    filter2D();
    const pose = elevationPose(frame, box);
    animatePose(currentPose(), pose, () => { if (view2d.on) goOrtho(pose); });
}

function exit2D() {
    if (!view2d.on) return;
    goPerspective();
    view2d.on = false;
    apply2DLook(false);
    const saved = view2d.saved, off = saved.pos.clone().sub(saved.target);
    animatePose(currentPose(), { target: saved.target, dir: off.clone().normalize(), dist: off.length() }, () => {
        camera.position.copy(saved.pos);
        controls.target.copy(saved.target);
        controls.update();
    });
}

// Drop straight back to perspective with no transition: a new model or a scripted
// framing takes the camera over.
function reset2D() {
    if (view2d.anim) { cancelAnimationFrame(view2d.anim); view2d.anim = null; }
    if (view2d.damping !== undefined) { controls.enableDamping = view2d.damping; view2d.damping = undefined; }
    controls.enabled = true;
    if (!view2d.on) return;
    goPerspective();
    view2d.on = false;
    apply2DLook(false);
}
