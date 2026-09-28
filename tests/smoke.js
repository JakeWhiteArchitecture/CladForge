// Browser smoke test: load the app in Chromium, import the sample IFC, pick the
// south wall, and check that an elevation is extracted and the exports work.
// Run: node tests/smoke.js  (needs the Flask server on :8080 and the proxy env)
const path = require('path');
const fs = require('fs');
let page = null, logs = [];

async function main() {
    const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
    // Only https:// goes through the egress proxy (CDNs); the local Flask app is plain http.
    const proxyHost = (process.env.HTTPS_PROXY || '').replace(/^https?:\/\//, '');
    const args = ['--use-gl=swiftshader', '--enable-unsafe-swiftshader'];
    if (proxyHost) args.push('--proxy-server=https=' + proxyHost + ';http=direct://');
    const browser = await chromium.launch({ headless: true, args });
    page = await browser.newPage({ viewport: { width: 1500, height: 900 }, acceptDownloads: true });
    logs = [];
    // VENDOR_DIR: serve the CDN runtimes from local copies (sandboxes that block CDNs).
    // Expected layout: <dir>/pyodide/*, <dir>/three/{build,examples}, <dir>/web-ifc/*,
    // <dir>/wasm-wheels/* (the IfcOpenShell wheel the browser installs for IFC export).
    const vendor = process.env.VENDOR_DIR;
    if (vendor) {
        const mime = { js: 'application/javascript', wasm: 'application/wasm', json: 'application/json', zip: 'application/zip', whl: 'application/octet-stream' };
        const map = [
            [/^https:\/\/cdn\.jsdelivr\.net\/pyodide\/v0\.29\.0\/full\/(.+)$/, m => path.join(vendor, 'pyodide', m[1])],
            [/^https:\/\/cdnjs\.cloudflare\.com\/ajax\/libs\/three\.js\/r128\/three\.min\.js$/, () => path.join(vendor, 'three', 'build', 'three.min.js')],
            [/^https:\/\/cdn\.jsdelivr\.net\/npm\/three@0\.128\.0\/(.+)$/, m => path.join(vendor, 'three', m[1])],
            [/^https:\/\/cdn\.jsdelivr\.net\/npm\/web-ifc@[\d.]+\/(.+)$/, m => path.join(vendor, 'web-ifc', m[1])],
            [/^https:\/\/ifcopenshell\.github\.io\/wasm-wheels\/(.+)$/, m => path.join(vendor, 'wasm-wheels', m[1])],
            [/^https:\/\/files\.pythonhosted\.org\/vendored\/(.+)$/, m => path.join(vendor, 'pypi', m[1])],
        ];
        // micropip resolves the IFC export's pure-Python deps through the PyPI simple
        // index; answer it from <dir>/pypi so the install needs no network either.
        await page.route(/^https:\/\/pypi\.org\/simple\/([^/]+)\//, route => {
            const name = route.request().url().match(/simple\/([^/]+)\//)[1];
            const files = fs.existsSync(path.join(vendor, 'pypi'))
                ? fs.readdirSync(path.join(vendor, 'pypi')).filter(f => f.toLowerCase().startsWith(name.replace(/-/g, '_').toLowerCase() + '-')) : [];
            if (!files.length) { logs.push('vendor miss: pypi ' + name); return route.fulfill({ status: 404, body: '' }); }
            return route.fulfill({ contentType: 'application/vnd.pypi.simple.v1+json', body: JSON.stringify({
                meta: { 'api-version': '1.0' }, name,
                files: files.map(f => ({ filename: f, url: 'https://files.pythonhosted.org/vendored/' + f, hashes: {} })) }) });
        });
        await page.route(/^https:\/\/(cdn\.jsdelivr\.net|cdnjs\.cloudflare\.com|ifcopenshell\.github\.io|files\.pythonhosted\.org)\//, route => {
            const url = route.request().url().split('?')[0];
            for (const [re, fn] of map) {
                const m = url.match(re);
                if (!m) continue;
                const file = fn(m);
                if (!fs.existsSync(file)) { logs.push('vendor miss: ' + url); return route.fulfill({ status: 404, body: '' }); }
                const ext = file.split('.').pop();
                return route.fulfill({ path: file, contentType: mime[ext] || 'application/octet-stream' });
            }
            logs.push('unmapped cdn: ' + url);
            return route.fulfill({ status: 404, body: '' });
        });
    }
    page.on('console', m => { if (m.type() === 'error' || m.type() === 'warning') logs.push(m.type() + ': ' + m.text().slice(0, 300)); });
    page.on('pageerror', e => logs.push('pageerror: ' + e.message));
    page.on('requestfailed', r => logs.push('requestfailed: ' + r.url() + ' ' + (r.failure() && r.failure().errorText)));
    await page.goto('http://127.0.0.1:8080/', { waitUntil: 'load' });

    await page.setInputFiles('#ifc-file', path.join(__dirname, 'sample_house.ifc'));
    await page.waitForFunction(() => typeof allMeshes !== "undefined" && allMeshes.length > 0, null, { timeout: 120000 });
    console.log('model:', await page.evaluate(() => document.getElementById('model-info').textContent));
    await page.waitForFunction(() => (typeof pyReady !== "undefined" && pyReady === true) || (document.getElementById('status-chip').classList.contains('ready')), null, { timeout: 300000 });
    console.log('engine ready');

    // Aim at a named element's face and click the middle of the canvas. Named rather
    // than positioned, because the viewer recentres the model on import.
    const box = await page.locator('#viewport canvas').boundingBox();
    const cameraMoves = [];
    async function lookAt(name, dir) {
        const ok = await page.evaluate(([name, dir]) => {
            const meta = meshMeta.find(m => m.name === name);
            if (!meta) return false;
            const bb = new THREE.Box3().setFromObject(meta.mesh);
            const c = bb.getCenter(new THREE.Vector3());
            const size = bb.getSize(new THREE.Vector3()).length();
            camera.position.copy(c.clone().add(new THREE.Vector3(dir[0], dir[1], dir[2]).normalize().multiplyScalar(size * 1.4)));
            controls.target.copy(c);
            controls.update();
            return true;
        }, [name, dir]);
        if (!ok) throw new Error('element not found: ' + name);
        await page.waitForTimeout(350);
        const eye = () => page.evaluate(() => camera.position.toArray().map(Math.round).join(','));
        const before = await eye();
        await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.5);
        await page.waitForTimeout(1200);
        // Picking a face must leave the view where the user put it.
        const after = await eye();
        if (before !== after) cameraMoves.push(`${name}: ${before} -> ${after}`);
    }
    // Picking generates nothing: the chain has to be built through the wizard first.
    async function buildChain(type, orient, useButton, levels) {
        // The button only shows while a chain is unbuilt; Enter reopens the wizard either way.
        const pending = await page.evaluate(() => pendingChains().length > 0);
        if (pending) {
            await page.waitForSelector('#make-chain', { state: 'visible', timeout: 60000 });
            console.log('make chain button:', await page.evaluate(() => document.getElementById('make-chain').textContent.trim()));
        }
        if (useButton && pending) await page.click('#make-chain');
        else { await page.evaluate(() => document.activeElement && document.activeElement.blur()); await page.keyboard.press('Enter'); }
        await page.waitForSelector('#chain-wizard.open', { timeout: 10000 });
        console.log('wizard:', await page.evaluate(() => [document.getElementById('wiz-title').textContent,
                                                          document.getElementById('wiz-step').textContent,
                                                          document.getElementById('wiz-chain').textContent].join(' | ')));
        await page.click(`#wiz-body .wiz-option >> nth=${type === 'panel' ? 1 : 0}`);
        if (type !== 'panel') await page.click(`#wiz-body .wiz-option >> nth=${orient === 'vertical' ? 1 : 0}`);
        // The remaining steps are all number fields; walk them to Build.
        for (let i = 0; i < 6; i++) {
            const step = await page.evaluate(() => [document.getElementById('wiz-title').textContent,
                document.getElementById('wiz-step').textContent,
                Array.from(document.querySelectorAll('#wiz-body input')).map(n => n.id.replace('wiz-', '') + '=' + n.value + (n.disabled ? '(derived)' : '')).join(' '),
                document.getElementById('wiz-next').textContent]);
            console.log('  wizard step:', step.slice(0, 3).join(' | '));
            await page.click('#wiz-next');
            if (step[3] === 'Build') break;
            await page.waitForTimeout(150);
        }
        await page.waitForFunction(() => !document.getElementById('chain-wizard').classList.contains('open'), null, { timeout: 10000 });
        await page.waitForTimeout(400);
        // Build hands over to the level picker: two clicks for top and bottom, or skip.
        if (await page.isVisible('#level-picker')) {
            console.log('  level picker:', await page.evaluate(() => document.getElementById('level-title').textContent));
            if (levels) {
                await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * (1 - levels[0]));
                await page.waitForTimeout(300);
                await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * (1 - levels[1]));
            } else {
                await page.click('#level-picker .wiz-actions button');   // Dismiss
            }
            await page.waitForFunction(() => !state.levels, null, { timeout: 10000 });
        }
        await page.waitForTimeout(1200);
    }

    await lookAt('South wall', [0, 0.35, 1]);
    await page.waitForFunction(() => typeof state !== "undefined" && state.elevations.length && state.elevations[0].result && state.elevations[0].result.ok, null, { timeout: 60000 });
    const result = await page.evaluate(() => state.elevations[0].result);
    console.log('elevation:', result.name, result.width, 'x', result.height, 'holes', result.n_holes,
                'abutments', result.abutments.map(a => `${a.source}@${a.v}[${a.u0}-${a.u1}]`).join(', '), 'warnings', result.warnings);
    console.log('nothing built yet, preview empty:', await page.evaluate(() => cladGroup.children.length === 0));
    await buildChain('plank', 'horizontal');
    await page.waitForFunction(() => cladGroup.children.length > 0, null, { timeout: 60000 });
    const counts = await page.evaluate(() => { const c = {}; cladGroup.children.forEach(g => c[g.name] = g.children.length); return c; });
    console.log('preview groups:', JSON.stringify(counts));
    // The south wall has a window (an interior hole) and a door (a notch in the outline):
    // both are openings, so both get a closer at each jamb.
    console.log('openings and closers:', await page.evaluate(() =>
        window._lastPreview.info.map(i => `${i.elevation}: ${i.openings} opening(s), `
            + window._lastPreview.geometry.filter(m => m.ifc_type === 'closer' && m.elevation === i.elevation).length + ' closers').join(' | ')));
    console.log('notches:', await page.evaluate(() => JSON.stringify(state.elevations[0].result.notches)));
    console.log('checks:', await page.evaluate(() => Array.from(document.querySelectorAll('.check-item span')).map(s => s.textContent)));
    console.log('overlay:', await page.evaluate(() => document.getElementById('dim-overlay').innerText.replace(/\n/g, ' | ')));
    await page.evaluate(() => frameElevation(state.elevations[0].result));
    await page.waitForTimeout(500);
    await page.screenshot({ path: path.join(__dirname, 'smoke_plank.png') });

    // Clicking a face that is already clad edits its chain instead of picking it again.
    await page.evaluate(() => { window.battenEdges = () => window._lastPreview.geometry
        .filter(m => m.ifc_type === 'batten' && m.elevation === 'Elevation A')
        .map(m => Math.round(Math.min(...m.profile.map(q => q[0])))).sort((a, b) => a - b).slice(0, 4); });
    const before = await page.evaluate(() => ({ n: state.elevations.length,
        battens: window.battenEdges() }));
    await lookAt('South wall', [0, 0.35, 1]);
    console.log('re-click on built cladding:', await page.evaluate(() => JSON.stringify({
        elevations: state.elevations.length, editing: state.editing && state.editing.name,
        widget: document.getElementById('edit-widget').style.display !== 'none' })), '| was', before.n, 'elevation(s)');
    const shifted = await page.evaluate(async () => {
        document.getElementById('edit-offset').value = 60;
        onEditSlide(60);
        await new Promise(r => setTimeout(r, 1500));
        return { offset: state.elevations[0].chain.offset, label: document.getElementById('edit-offset-val').textContent,
                 battens: window.battenEdges() };
    });
    console.log('edit widget shift:', JSON.stringify(shifted), '| battens were at', JSON.stringify(before.battens));
    await page.keyboard.press('Escape');
    await page.evaluate(async () => { document.getElementById('edit-offset').value = 0; onEditSlide(0); await new Promise(r => setTimeout(r, 1200)); });
    console.log('widget closed:', await page.evaluate(() => document.getElementById('edit-widget').style.display === 'none'));
    // The engine runs in a fixed heap: killing it must not brick the session.
    console.log('context budget:', await page.evaluate(() => {
        const e = state.elevations[0];
        const tris = [];
        for (const p of e.picks) tris.push(...faceTriangles(p.mesh, p.faces));
        const c = contextFor(new Set(e.picks.map(p => p.mesh)), tris, 300, e.picks[0].normal);
        return `${c.elements.length} elements, ${c.triangles} triangles, ${c.dropped} dropped (budget ${CONTEXT_TRI_BUDGET})`;
    }));
    const recovered = await page.evaluate(async () => {
        const before = pyReady;
        // A pick made while the engine is down must be queued, not silently dropped.
        const e = state.elevations[0];
        const kept = e.result;
        e.result = null;
        pyReady = false;
        await runExtraction(e);
        const whileDown = { pending: !!e.pending, state: e.result ? 'extracted' : (e.error ? 'error' : 'waiting') };
        pyReady = true;
        e.result = kept;
        const ok = await restartEngine();   // rebuilds, then resumes anything queued
        return { before, whileDown, rebuilt: ok, ready: pyReady };
    });
    console.log('engine rebuild:', JSON.stringify(recovered));
    await page.waitForTimeout(3000);
    // A second extraction arriving while one is in flight must not be dropped or lock the
    // elevation: it is remembered and run when the first ends.
    console.log('re-entrant extraction:', await page.evaluate(async () => {
        const e = state.elevations[0];
        e.result = null;
        const first = runExtraction(e);
        const second = runExtraction(e);          // arrives mid-flight
        await first; await second;
        for (let i = 0; i < 40 && (e._running || !e.result); i++) await new Promise(r => setTimeout(r, 250));
        return JSON.stringify({ extracted: !!(e.result && e.result.ok), running: !!e._running, queued: !!e._again });
    }));
    console.log('nothing left stuck:', await page.evaluate(() =>
        state.elevations.filter(e => e.picks.length && !e.result && !e.error).map(e => e.name).join(',') || 'none'));
    console.log('diagnostics:', await page.evaluate(() => {
        const d = cladforge();
        return `${d.elevations.length} elevations, states ${d.elevations.map(x => x.state).join('/')}, ${d.log.length} log lines`;
    }));
    await page.waitForTimeout(500);
    console.log('preview still works after rebuild:', await page.evaluate(async () => {
        await updatePreview();
        return window._lastPreview.geometry.length > 0;
    }));

    console.log('pick seeds recorded:', await page.evaluate(() =>
        state.elevations[0].picks.map(p => p.point && p.point.map(c => Math.round(c)).join(',')).join(' | ')));

    // Live slider: time one full preview round trip (Pyodide coursing + trimming + render).
    const ms = await page.evaluate(async () => {
        const t = performance.now(); await updatePreview(); return Math.round(performance.now() - t);
    });
    const msDrag = await page.evaluate(async () => {
        state.sliderDragging = true;
        const t = performance.now(); await updatePreview(); state.sliderDragging = false; return Math.round(performance.now() - t);
    });
    console.log('preview round trip ms: trimmed', ms, '| untrimmed (during drag)', msDrag);
    const parts = await page.evaluate(async () => {
        const p = getParams(); p.trim = false;
        pyodide.globals.set('_params_json', JSON.stringify(p));
        let t = performance.now();
        const out = await pyodide.runPythonAsync('import json as _json, time as _time\nfrom cladding_preview import generate_preview as _gp, check_rules as _cr\n_t0 = _time.perf_counter()\n_p = _json.loads(_params_json)\n_o = _gp(_p)\n_o["checks"] = _cr(_p, _o["info"])\n_o["_py_ms"] = round((_time.perf_counter() - _t0) * 1000)\n_json.dumps(_o)');
        const py = performance.now() - t;
        t = performance.now(); const r = JSON.parse(out); const parse = performance.now() - t;
        t = performance.now(); renderGeometry(r.geometry); const render = performance.now() - t;
        return { await_ms: Math.round(py), inside_python_ms: r._py_ms, json_parse_ms: Math.round(parse), render_ms: Math.round(render), elements: r.geometry.length, json_kb: Math.round(out.length / 1024) };
    });
    console.log('breakdown (untrimmed):', JSON.stringify(parts));
    // Same round trip with the WebGL draw stubbed out: isolates the headless software renderer.
    const noRender = await page.evaluate(async () => {
        const real = renderer.render; renderer.render = () => {};
        await new Promise(r => setTimeout(r, 50));
        const t = performance.now(); await updatePreview(); const ms = Math.round(performance.now() - t);
        renderer.render = real; return ms;
    });
    console.log('preview round trip ms with draw stubbed (trimmed):', noRender);

    // Panel mode with sheathing + insulation.
    await page.click('#cladding-type .turn-btn[data-value="panel"]');
    await page.check('#sheathing'); await page.check('#insulation');
    await page.waitForTimeout(1500);
    await page.screenshot({ path: path.join(__dirname, 'smoke_panel.png') });
    console.log('panel overlay:', await page.evaluate(() => document.getElementById('dim-overlay').innerText.replace(/\n/g, ' | ')));
    // Panel rows: each row has its own dimension, and typing over one sets that row only.
    const rowEdit = await page.evaluate(async () => {
        const rows = () => window._lastPreview.info[0].rows.join('/');
        const panels = () => window._lastPreview.geometry.filter(m => m.ifc_type === 'panel').length;
        const d = window._lastPreview.dimensions.find(x => x.kind === 'row' && x.row === 0);
        const before = { rows: rows(), panels: panels(), labels: window._lastPreview.dimensions
            .filter(x => x.kind === 'row' || /^Cut/.test(x.label)).map(x => x.label).join(' ') };
        if (!d) return { before };
        openCourseDialog(d);
        const title = document.getElementById('course-title').textContent;
        document.getElementById('course-value').value = 900;
        applyCourse('one');
        await new Promise(r => setTimeout(r, 1500));
        const after = { rows: rows(), panels: panels(), stored: state.elevations[0].panelRows.join('/') };
        openCourseDialog(window._lastPreview.dimensions.find(x => x.kind === 'row'));
        resetCourse();
        await new Promise(r => setTimeout(r, 1500));
        return { title, before, after, reset: rows() };
    });
    console.log('panel rows:', JSON.stringify(rowEdit));
    if (!rowEdit.after || !rowEdit.after.rows.startsWith('900/') || rowEdit.reset !== rowEdit.before.rows)
        throw new Error('row edit did not apply or reset: ' + JSON.stringify(rowEdit));

    // 2D elevation: E swings the camera round to face the active elevation, then hands
    // over to an orthographic camera; Escape brings the 3D pose back.
    await page.evaluate(() => { setActive(0); document.activeElement && document.activeElement.blur(); });
    await page.waitForTimeout(800);
    const pose3d = await page.evaluate(() => [camera.position.toArray(), controls.target.toArray()].map(v => v.map(Math.round).join(',')).join(' → '));
    await page.keyboard.press('e');
    await page.waitForTimeout(1200);
    const flat = await page.evaluate(() => {
        const cam = activeCamera(), f = state.elevations[0].result.frame;
        const look = cam.position.clone().sub(controls.target).normalize();
        const n = new THREE.Vector3(f.n[0], 0, -f.n[1]).normalize();
        const faded = modelGroup.children.every(m => Math.abs(m.material.opacity - VIEW2D_FADE) < 1e-6);
        return { ortho: !!cam.isOrthographicCamera, square: +look.dot(n).toFixed(6), up: cam.up.toArray().join(','),
                 zoom: +cam.zoom.toFixed(3), rotate: controls.enableRotate, faded, dims: dimGroup.visible,
                 button: document.getElementById('view-2d').textContent };
    });
    console.log('2D view:', JSON.stringify(flat));
    await page.screenshot({ path: path.join(__dirname, 'smoke_2d.png') });
    if (!flat.ortho || flat.square < 0.99999 || flat.rotate || !flat.faded || flat.button !== '3D')
        throw new Error('2D view is not flat and square-on: ' + JSON.stringify(flat));

    // Flat, the dimensions are HTML labels: editable ones type over, the rest say why not.
    console.log('2D labels:', await page.evaluate(() => JSON.stringify({
        editable: Array.from(document.querySelectorAll('.dim2d.editable')).map(b => b.textContent),
        locked: Array.from(document.querySelectorAll('.dim2d.locked')).map(b => `${b.textContent} (${b.title})`) })));
    const panelsBefore = await page.evaluate(() => window._lastPreview.geometry.filter(m => m.ifc_type === 'panel').length);
    await page.click('.dim2d[data-key="row:0"]');
    await page.waitForSelector('#d2-input', { timeout: 5000 });
    await page.fill('#d2-input', '900');
    await page.keyboard.press('Enter');
    await page.waitForTimeout(1800);        // 300 ms debounce, then the preview
    const typed = await page.evaluate(() => {
        pyodide.globals.set('_dxf_params', JSON.stringify(getParams()));
        const dxf = pyodide.runPython(`
import json as _json
from cladding_preview import generate_preview as _gp
from dxf_generator import meshes_to_dxf_string as _dxf
_p = _json.loads(_dxf_params)
_o = _gp(_p)
_dxf(_o["geometry"], _p, _o["info"])`);
        return { rows: window._lastPreview.info[0].rows.join('/'), editorOpen: !!document.getElementById('d2-input'),
                 panels: window._lastPreview.geometry.filter(m => m.ifc_type === 'panel').length,
                 label: (document.querySelector('.dim2d[data-key="row:0"]') || {}).textContent,
                 dxfRow: dxf.indexOf('R1 900') >= 0, still2D: in2D() };
    });
    console.log('2D row edit:', JSON.stringify(typed), '| panels were', panelsBefore);
    if (!typed.rows.startsWith('900/') || typed.panels === panelsBefore || !typed.dxfRow || typed.editorOpen || !typed.still2D)
        throw new Error('typing a row height in 2D did not apply: ' + JSON.stringify(typed));
    // Tab moves to the next editable dimension; Escape cancels without leaving 2D.
    await page.click('.dim2d[data-key="row:0"]');
    await page.waitForSelector('#d2-input');
    const first = await page.evaluate(() => document.querySelector('#dim2d-editor .d2-title').textContent);
    await page.keyboard.press('Tab');
    const second = await page.evaluate(() => document.querySelector('#dim2d-editor .d2-title').textContent);
    await page.keyboard.press('Escape');
    const afterEsc = await page.evaluate(() => ({ editor: !!document.getElementById('d2-input'), flat: in2D() }));
    console.log('2D tab/escape:', first, '→', second, JSON.stringify(afterEsc));
    if (first === second || afterEsc.editor || !afterEsc.flat) throw new Error('Tab/Escape in the 2D editor misbehaved');
    // Split row halves it with the joint gap between.
    await page.click('.dim2d[data-key="row:0"]');
    await page.click('#d2-split');
    await page.waitForTimeout(1800);
    const split = await page.evaluate(() => window._lastPreview.info[0].rows.join('/'));
    console.log('2D split row:', split);
    if (!split.startsWith('445/445/')) throw new Error('split row: ' + split);
    // The top row is a row too: its box offers Split row, its height is not typed, and
    // splitting it adds a row.
    const topRow = await page.evaluate(() => {
        const d = window._lastPreview.dimensions.find(x => x.kind === 'row' && x.fixed && x.elevation === view2d.name);
        return d && { key: 'row:' + d.row, label: d.label, n: window._lastPreview.info[0].n_courses };
    });
    if (!topRow || /Cut/.test(topRow.label)) throw new Error('top row is not a row: ' + JSON.stringify(topRow));
    await page.click(`.dim2d[data-key="${topRow.key}"]`);
    const topBox = await page.evaluate(() => ({ disabled: document.getElementById('d2-input').disabled,
        split: !!document.getElementById('d2-split'), merge: document.getElementById('d2-merge').disabled,
        why: (document.querySelector('#dim2d-editor .d2-hint') || {}).textContent }));
    await page.click('#d2-split');
    await page.waitForTimeout(1800);
    topRow.after = await page.evaluate(() => window._lastPreview.info[0].n_courses);
    console.log('2D top row:', JSON.stringify(topRow), JSON.stringify(topBox));
    if (!topBox.disabled || !topBox.split || !topBox.merge || topRow.after !== topRow.n + 1)
        throw new Error('top row box: ' + JSON.stringify([topRow, topBox]));
    await page.evaluate(async () => { state.elevations[0].panelRows = null; await updatePreview(); });
    // Centred instead of set out from the openings, the left cut is typed and the offset
    // solved for it; the top level is typed straight onto the chain.
    const solved = await page.evaluate(async () => {
        const wait = ms => new Promise(r => setTimeout(r, ms));
        document.getElementById('set_out_from_openings').checked = false;
        await updatePreview();
        const cut = () => window._lastPreview.dimensions.find(d => d.kind === 'cut_left' && d.elevation === 'Elevation A');
        const was = cut() && { value: cut().value, locked: !!cut().lock,
                               html: (document.querySelector('.dim2d[data-key^="cut_left"]') || {}).className };
        applyDim(cut(), 300, 'one');
        await wait(1500);
        const now = { cut: Math.round(cut().value), offset: state.elevations[0].offset };
        applyDim(window._lastPreview.dimensions.find(d => d.kind === 'level_top' && d.elevation === 'Elevation A'), 5000, 'one');
        await wait(1500);
        const top = window._lastPreview.info[0].clad_top;
        state.elevations[0].offset = 0; state.elevations[0].chain.topZ = null;
        document.getElementById('set_out_from_openings').checked = true;
        await updatePreview();
        return { was, now, top };
    });
    console.log('2D cut and level:', JSON.stringify(solved));
    if (!solved.was || solved.was.locked || solved.now.cut !== 300 || Math.round(solved.top) !== 5000)
        throw new Error('cut/level write-back failed: ' + JSON.stringify(solved));

    // Coloured edges over the outline. Click a purple (top) edge and type an offset: the
    // panels stand further back from the top, on the whole chain, and the panel field agrees.
    await page.waitForTimeout(800);
    const edgeKinds = await page.evaluate(() => {
        const c = {};
        document.querySelectorAll('.edge2d-svg line').forEach(l => { c[l.dataset.kind] = (c[l.dataset.kind] || 0) + 1; });
        const lines = Array.from(document.querySelectorAll('.edge2d-svg line[data-kind="top"]'));
        const len = l => Math.hypot(l.x2.baseVal.value - l.x1.baseVal.value, l.y2.baseVal.value - l.y1.baseVal.value);
        lines.sort((a, b) => len(b) - len(a));
        if (lines[0]) lines[0].id = 'smoke-top-edge';
        return c;
    });
    console.log('2D edges:', JSON.stringify(edgeKinds));
    const topOf = () => page.evaluate(() => Math.round(Math.max(...window._lastPreview.geometry
        .filter(m => m.ifc_type === 'panel' && m.elevation === 'Elevation A').flatMap(m => m.profile.map(q => q[1])))));
    // An SVG line's box has no height (Chromium leaves the stroke out), so click it as a person
    // does: at a point on the line where the line is what is under the pointer.
    const onLine = id => page.evaluate(id => {
        const l = document.getElementById(id), r = l.ownerSVGElement.getBoundingClientRect();
        const [x1, y1, x2, y2] = ['x1', 'y1', 'x2', 'y2'].map(a => l[a].baseVal.value);
        for (const t of [0.5, 0.35, 0.65, 0.2, 0.8]) {
            const x = r.left + x1 + (x2 - x1) * t, y = r.top + y1 + (y2 - y1) * t;
            if (document.elementFromPoint(x, y) === l) return { x, y };
        }
        return null;
    }, id);
    const topBefore = await topOf();
    const topAt = await onLine('smoke-top-edge');
    if (!topAt) throw new Error('the top edge is covered everywhere along it');
    await page.mouse.click(topAt.x, topAt.y);
    await page.waitForSelector('#e2-input', { timeout: 5000 });
    await page.fill('#e2-input', '25');
    await page.keyboard.press('Enter');
    await page.waitForTimeout(1800);
    const topEdit = await page.evaluate(() => ({ chain: state.elevations[0].chain.edges.top,
        field: document.getElementById('edge-top').value, still2D: in2D() }));
    const topAfter = await topOf();
    console.log('2D top edge:', JSON.stringify(topEdit), 'panel top', topBefore, '→', topAfter);
    if (topEdit.chain !== 25 || topEdit.field !== '25' || topBefore - topAfter !== 15 || !topEdit.still2D)
        throw new Error('top edge offset did not apply: ' + JSON.stringify({ topEdit, topBefore, topAfter }));
    // The red head edge: switch the ventilation to the back and the head lining drops by the air space.
    const headZ = () => page.evaluate(() => {
        const m = window._lastPreview.geometry.find(g => g.ifc_type === 'reveal' && / Reveal \dH$/.test(g.name) && g.elevation === 'Elevation A');
        return m ? Math.round(m.frame.origin[2]) : null;
    });
    const zFront = await headZ();
    await page.evaluate(() => { const l = document.querySelector('.edge2d-svg line[data-kind="head"]'); if (l) l.id = 'smoke-head-edge'; });
    const headAt = await onLine('smoke-head-edge');
    if (!headAt) throw new Error('the head edge is covered everywhere along it');
    await page.mouse.click(headAt.x, headAt.y);
    await page.click('#e2-vent [data-vent="back"]');
    await page.waitForTimeout(1800);
    const zBack = await headZ();
    console.log('2D head edge: lining at', zFront, '→', zBack, JSON.stringify(await page.evaluate(() => state.elevations[0].chain.edges)));
    if (zFront === null || zFront - zBack !== 10) throw new Error('head ventilation did not lower the lining: ' + zFront + ' → ' + zBack);
    await page.keyboard.press('Escape');
    await page.evaluate(async () => { Object.assign(state.elevations[0].chain.edges, EDGE_DEFAULTS); syncEdgeFields(); await updatePreview(); });

    // Window and door corners. Changing a jamb asks whether it goes to every window and door
    // in the chain: Yes sets both of A's openings; No sets just the one clicked.
    await page.waitForTimeout(600);
    const jambBadges = () => page.$$('.opening-badge[data-key$=":jamb"]');
    await (await jambBadges())[0].click();
    await page.selectOption('#o2-detail', 'lap');
    const asked = await page.isVisible('#o2-ask');
    await page.click('#o2-yes');
    await page.waitForTimeout(1800);
    const toAll = await page.evaluate(() => Object.values(state.elevations[0].openingDetails).map(d => d.jamb).join(','));
    await (await jambBadges())[1].click();
    await page.selectOption('#o2-detail', 'profile');
    await page.click('#o2-no');
    await page.waitForTimeout(1800);
    const openingsNow = await page.evaluate(() => ({
        jambs: openingsOf(state.elevations[0]).map(o => o.jamb).join(','),
        profiles: window._lastPreview.geometry.filter(m => m.ifc_type === 'corner_profile').map(m => m.name.replace('Elevation ', '')).join(','),
        badges: Array.from(document.querySelectorAll('.opening-badge')).map(b => b.textContent).join(','),
        others: state.elevations.slice(1).map(e => Object.keys(e.openingDetails || {}).length).join(',') }));
    console.log('2D openings:', JSON.stringify({ asked, toAll, ...openingsNow }));
    if (!asked || toAll !== 'lap,lap' || openingsNow.jambs.split(',').sort().join() !== 'lap,profile'
        || openingsNow.profiles.split(',').length !== 2 || /[1-9]/.test(openingsNow.others))
        throw new Error('opening corner details did not apply as asked: ' + JSON.stringify({ asked, toAll, openingsNow }));
    // "Every window and door in this chain" reaches every member of the chain and no other chain.
    const scopeOnly = await page.evaluate(() => {
        const saved = window._lastPreview;
        window._lastPreview = { info: [{ elevation: 'X1', opening_details: [{ key: 'a' }, { key: 'b' }] },
                                       { elevation: 'X2', opening_details: [{ key: 'c' }] },
                                       { elevation: 'Y1', opening_details: [{ key: 'd' }] }] };
        const cx = { members: [] }, cy = { members: [] };
        const mk = (name, chain) => { const m = { result: { ok: true, name }, chain, openingDetails: {} }; chain.members.push(m); return m; };
        const x1 = mk('X1', cx), x2 = mk('X2', cx), y1 = mk('Y1', cy);
        const realNumeric = onNumeric, realList = renderElevationList;
        onNumeric = () => {}; renderElevationList = () => {};
        setOpeningDetail(x1, 'a', 'jamb', 'lap', 'reveal', 'chain');
        setOpeningDetail(x2, 'c', 'head', 'profile', 'face', 'one');
        onNumeric = realNumeric; renderElevationList = realList;
        window._lastPreview = saved;
        return JSON.stringify([x1.openingDetails, x2.openingDetails, y1.openingDetails]);
    });
    console.log('opening scope:', scopeOnly);
    if (scopeOnly !== JSON.stringify([{ a: { jamb: 'lap', jamb_master: 'reveal' }, b: { jamb: 'lap', jamb_master: 'reveal' } },
                                      { c: { jamb: 'lap', jamb_master: 'reveal', head: 'profile', head_master: 'face' } }, {}]))
        throw new Error('apply-to-all reached the wrong openings: ' + scopeOnly);
    await page.evaluate(async () => { state.elevations[0].openingDetails = {}; await updatePreview(); });

    // Panel joints: each horizontal joint, one segment per bay, dissolves when clicked. With
    // 2400 rows the merged panel would be 4810 tall, too big for a 1250 x 2500 board: refused.
    const jointAt = async fits => {
        await page.waitForFunction(() => document.querySelector('.edge2d-svg line[data-kind="joint"]'), null, { timeout: 20000 });
        const picked = await page.evaluate(fits => {
            const e = d2Elev(view2d.name), info = window._lastPreview.info.find(i => i.elevation === view2d.name);
            const names = new Set(window._lastPreview.geometry.map(m => m.name));
            const board = [1250, 2500], ok = s => (s[0] <= board[0] && s[1] <= board[1]) || (s[0] <= board[1] && s[1] <= board[0]);
            // a joint between two whole panels (neither cut in two by an opening)
            const hj = info.hjoints.find(q => !q.dissolved && ok(q.size) === fits
                && names.has(`${e.result.name} Panel R${q.row + 1}-${q.bay + 1}`) && names.has(`${e.result.name} Panel R${q.row + 2}-${q.bay + 1}`));
            if (!hj) return null;
            document.querySelectorAll('#smoke-joint').forEach(l => l.removeAttribute('id'));
            document.querySelector(`.edge2d-svg line[data-key="joint:${hj.row}:${hj.bay}:0"]`).id = 'smoke-joint';
            return hj;
        }, fits);
        return picked && { hj: picked, at: await onLine('smoke-joint') };
    };
    const tooBig = await jointAt(false);
    if (!tooBig || !tooBig.at) throw new Error('no oversize joint to click: ' + JSON.stringify(tooBig));
    await page.mouse.click(tooBig.at.x, tooBig.at.y);
    const refused = await page.evaluate(() => ({ status: document.getElementById('status-chip').textContent,
                                                 joints: (d2Elev(view2d.name).panelJoints || []).length }));
    console.log('joint refused:', JSON.stringify(refused));
    if (refused.joints || !/Not dissolved: the panel would be \d+ × 4810 mm, larger than the 1250 × 2500 board/.test(refused.status))
        throw new Error('an oversize merge was not refused: ' + JSON.stringify(refused));
    // With 1000 rows the merge fits: one panel fewer, and the waste readout is repacked.
    await page.evaluate(async () => { d2Elev(view2d.name).panelRows = [1000, 1000]; await updatePreview(); });
    await page.waitForFunction(() => window._lastPlan && window._lastPlan.n_pieces, null, { timeout: 20000 });
    await page.waitForTimeout(900);
    const fitting = await jointAt(true);
    if (!fitting || !fitting.at) throw new Error('no joint that fits to click: ' + JSON.stringify(fitting));
    const beforeJoint = await page.evaluate(() => {
        window._smokePlan = window._lastPlan;
        return { panels: window._lastPreview.geometry.filter(m => m.ifc_type === 'panel').length, pieces: window._lastPlan.n_pieces };
    });
    await page.mouse.click(fitting.at.x, fitting.at.y);
    await page.waitForFunction(() => window._lastPlan !== window._smokePlan, null, { timeout: 20000 });
    const afterJoint = await page.evaluate(() => {
        const hj = window._lastPreview.info.find(i => i.elevation === view2d.name).panel_joints[0];
        return { panels: window._lastPreview.geometry.filter(m => m.ifc_type === 'panel').length, pieces: window._lastPlan.n_pieces,
                 merged: window._lastPreview.geometry.filter(m => / Panel R\d+-\d+\.\.R\d+-\d+$/.test(m.name)).map(m => m.name),
                 stored: d2Elev(view2d.name).panelJoints, dashed: !!document.querySelector('.edge2d-svg line[data-kind="joint"][stroke-dasharray]'),
                 readout: document.getElementById('wr-waste').textContent, waste: (100 * window._lastPlan.waste).toFixed(1), size: hj && hj.size };
    });
    console.log('joint dissolved:', JSON.stringify(fitting.hj.size), JSON.stringify(beforeJoint), '→', JSON.stringify(afterJoint));
    if (afterJoint.panels !== beforeJoint.panels - 1 || afterJoint.pieces !== beforeJoint.pieces - 1 || afterJoint.merged.length !== 1
        || !afterJoint.dashed || !afterJoint.readout.includes(afterJoint.waste))
        throw new Error('dissolving a joint did not give one panel fewer: ' + JSON.stringify({ beforeJoint, afterJoint }));
    // Clicking the same place again puts the joint back.
    await page.evaluate(() => { document.querySelector('.edge2d-svg line[data-kind="joint"][stroke-dasharray]').id = 'smoke-joint-back'; });
    const backAt = await onLine('smoke-joint-back');
    await page.mouse.click(backAt.x, backAt.y);
    await page.waitForTimeout(1800);
    const restored = await page.evaluate(() => window._lastPreview.geometry.filter(m => m.ifc_type === 'panel').length);
    console.log('joint restored:', restored);
    if (restored !== beforeJoint.panels) throw new Error('clicking again did not restore the joint: ' + restored);
    // A grid change under a dissolved joint drops it, with one message saying how many.
    const pruned = await page.evaluate(async () => {
        const e = d2Elev(view2d.name), info = window._lastPreview.info.find(i => i.elevation === view2d.name);
        e.panelJoints = info.hjoints.filter(q => q.row === 0).slice(0, 2).map(q => [q.row, q.bay, q.u0, q.u1, q.v]);
        await updatePreview();
        const kept = (e.panelJoints || []).length;
        document.getElementById('set_out_from_openings').checked = false;       // the bays move
        await updatePreview();
        const out = { kept, left: (e.panelJoints || []).length, status: document.getElementById('status-chip').textContent };
        document.getElementById('set_out_from_openings').checked = true;
        return out;
    });
    console.log('joints dropped:', JSON.stringify(pruned));
    if (pruned.kept !== 2 || pruned.left !== 0 || !/^2 dissolved joints dropped/.test(pruned.status))
        throw new Error('joints under a changed grid were not dropped and reported: ' + JSON.stringify(pruned));
    await page.evaluate(async () => { const e = d2Elev(view2d.name); e.panelRows = null; e.panelJoints = null; await updatePreview(); });
    await page.keyboard.press('Escape');
    await page.waitForTimeout(1200);
    const back = await page.evaluate(() => ({ ortho: !!activeCamera().isOrthographicCamera, rotate: controls.enableRotate,
        pose: [camera.position.toArray(), controls.target.toArray()].map(v => v.map(Math.round).join(',')).join(' → '),
        opacity: modelGroup.children[0].material.opacity }));
    console.log('back to 3D:', JSON.stringify(back), '| was', pose3d);
    if (back.ortho || !back.rotate || back.pose !== pose3d)
        throw new Error('3D pose not restored: ' + JSON.stringify(back) + ' vs ' + pose3d);

    // Boards and waste: the active panel chain packed onto stock boards after a rebuild.
    await page.evaluate(async () => { await updatePreview(); });
    await page.waitForFunction(() => window._lastPlan && document.getElementById('waste-readout').style.display !== 'none', null, { timeout: 20000 });
    const readout = await page.evaluate(() => {
        const box = document.getElementById('waste-readout'), plan = window._lastPlan;
        const red = () => getComputedStyle(document.getElementById('wr-waste')).color;
        const real = { text: box.innerText.replace(/\n/g, ' | '), waste: +(100 * plan.waste).toFixed(1), over: box.classList.contains('over'),
                       colour: red(), boards: plan.n_boards, lower: plan.lower_bound, pieces: plan.n_pieces, ms: plan.ms };
        // the colour follows the 5% line either way
        const chain = state.elevations[0].chain;
        renderReadout(chain, Object.assign({}, plan, { waste: 0.04 }));
        const at4 = { over: box.classList.contains('over'), colour: red() };
        renderReadout(chain, Object.assign({}, plan, { waste: 0.06 }));
        const at6 = { over: box.classList.contains('over'), colour: red() };
        renderReadout(chain, plan);
        return { real, at4, at6 };
    });
    console.log('waste readout:', JSON.stringify(readout));
    if (readout.real.boards < readout.real.lower || readout.real.pieces < 1 || readout.at4.over || !readout.at6.over
        || readout.at6.colour !== 'rgb(239, 68, 68)' || readout.at4.colour === readout.at6.colour
        || readout.real.over !== (readout.real.waste > 5) || readout.real.ms > 1000)
        throw new Error('waste readout wrong: ' + JSON.stringify(readout));
    // A board as big as two panels side by side packs them two to a board.
    const bigger = await page.evaluate(async () => {
        document.getElementById('board_w').value = 2500; await updatePreview();
        await new Promise(r => setTimeout(r, 900));
        const n = window._lastPlan.n_boards;
        document.getElementById('board_w').value = 1250; await updatePreview();
        await new Promise(r => setTimeout(r, 900));
        return { wide: n, normal: window._lastPlan.n_boards };
    });
    console.log('board size:', JSON.stringify(bigger));
    if (!(bigger.wide < bigger.normal)) throw new Error('a wider board did not save boards: ' + JSON.stringify(bigger));
    // The cutting plan, from the button beside the DXF one.
    await page.click('#plan-btn');
    await page.waitForSelector('#download-reminder.open', { timeout: 120000 });
    const [planFile] = await Promise.all([page.waitForEvent('download'), page.click('#download-reminder .btn-primary')]);
    const planPath = path.join(__dirname, 'smoke_out.plan.dxf');
    await planFile.saveAs(planPath);
    const planText = fs.readFileSync(planPath, 'utf8');
    console.log('cutting plan:', planFile.suggestedFilename(), fs.statSync(planPath).size, 'bytes,',
                (planText.match(/Board \d+  -  waste/g) || []).length, 'boards drawn');
    if (!planText.includes('Cutting plan for setting-out. Not a quantity take-off for pricing.') || !/Panel R\d-\d/.test(planText))
        throw new Error('cutting plan DXF is missing its header or pieces');

    // The wing's south wall: not coplanar with A and not adjacent, so it starts its own chain.
    await lookAt('Wing south wall', [0, 0.3, 1]);
    await page.waitForFunction(() => state.elevations.length === 2 && state.elevations[1].result && state.elevations[1].result.ok && state.elevations[1].chain !== state.elevations[0].chain, null, { timeout: 60000 });
    const r2 = await page.evaluate(() => state.elevations[1].result);
    console.log('elevation B (wing south):', r2.width, 'x', r2.height, 'chain', await page.evaluate(() => state.elevations[1].chain.name));

    // Then the wing's east wall: it turns the corner, so it joins B's chain with the run continued.
    await lookAt('Wing east wall', [1, 0.3, 0]);
    await page.waitForFunction(() => state.elevations.length === 3 && state.elevations[2].result && state.elevations[2].result.ok && state.elevations[2].link, null, { timeout: 60000 });
    const chainInfo = await page.evaluate(() => ({ chain: state.elevations[2].chain.name, members: state.elevations[2].chain.members.map(m => [m.name, Math.round(m.start), m.rev]),
                                                    length: Math.round(state.elevations[2].chain.length), link: state.elevations[2].link }));
    console.log('chain:', JSON.stringify(chainInfo));
    await buildChain('plank', 'horizontal');   // the wing chain, built once both faces are picked
    console.log('corner clip:', await page.evaluate(() => state.elevations.slice(1).map(m =>
        `${m.name}: face ${Math.round(m.result.width)} clad ${Math.round(m.clipLo)}-${Math.round(m.clipHi === null ? m.result.width : m.clipHi)}`).join(' | ')));
    console.log('plank seams per course (max):', await page.evaluate(() => {
        const c = {};
        window._lastPreview.geometry.filter(m => m.ifc_type === 'plank').forEach(m => {
            const key = m.elevation + '@' + Math.round(Math.min(...m.profile.map(q => q[1])));
            c[key] = (c[key] || 0) + 1;
        });
        return Math.max(0, ...Object.values(c));
    }));
    console.log('list header:', await page.evaluate(() => (document.querySelector('.chain-head') || {}).textContent));

    // The main east wall carries the wing's pitched roof: its splash zone must follow the slope.
    await page.click('button:has-text("New elevation")');
    await lookAt('East wall', [1, 0.25, 0]);
    await page.waitForFunction(() => state.elevations.length === 4 && state.elevations[3].result && state.elevations[3].result.ok, null, { timeout: 60000 });
    const r4 = await page.evaluate(() => state.elevations[3].result);
    console.log('elevation D (main east):', r4.width, 'x', r4.height, 'abutments', r4.abutments.map(a => `${a.source}${a.pitched ? '(pitched)' : ''} line=${JSON.stringify(a.line)}`).join(' | '));
    await buildChain('plank', 'horizontal', true);   // this one through the button rather than Enter
    await page.evaluate(() => frameElevation(state.elevations[3].result));
    await page.waitForTimeout(800);
    await page.screenshot({ path: path.join(__dirname, 'smoke_pitched.png') });

    // Exports: DXF via Pyodide, IFC via Flask.
    for (const [btn, ext] of [['#dxf-btn', 'dxf'], ['#ifc-btn', 'ifc']]) {
        await page.click(btn);
        await page.waitForSelector('#download-reminder.open', { timeout: 300000 });
        const [download] = await Promise.all([page.waitForEvent('download'), page.click('#download-reminder .btn-primary')]);
        const out = path.join(__dirname, 'smoke_out.' + ext);
        await download.saveAs(out);
        console.log(ext, 'download:', download.suggestedFilename(), fs.statSync(out).size, 'bytes');
    }
    // Corners: the chain's corner row, the master-lap detail and the master swap.
    await page.click('#cladding-type .turn-btn[data-value="panel"]');
    await page.waitForTimeout(1200);
    console.log('corner row (mitred):', await page.evaluate(() => (document.querySelector('.corner-row') || {}).textContent));
    const mitred = await page.evaluate(() => window._lastPreview.geometry.filter(m => m.corner).length);
    await page.click('#corner-type .turn-btn[data-value="lap"]');
    await page.waitForTimeout(1500);
    const lapped = await page.evaluate(() => ({
        row: (document.querySelector('.corner-row') || {}).textContent,
        marked: window._lastPreview.geometry.filter(m => m.corner).length,
        types: Array.from(new Set(window._lastPreview.geometry.filter(m => m.corner).map(m => m.ifc_type))),
        exts: Array.from(new Set(window._lastPreview.geometry.filter(m => m.corner)
            .map(m => Math.round((m.corner.ext_r !== undefined ? m.corner.ext_r : m.corner.ext_l))))).sort((a, b) => a - b),
    }));
    console.log('mitred elements:', mitred, '| lapped:', JSON.stringify(lapped));
    await page.click('.corner-row button');
    await page.waitForTimeout(1200);
    console.log('after swap:', await page.evaluate(() => (document.querySelector('.corner-row') || {}).textContent));
    console.log('lap check:', await page.evaluate(() => (Array.from(document.querySelectorAll('.check-item span'))
        .map(s => s.textContent).find(t => t.indexOf('corner end') >= 0) || 'none')));
    // Corner detail per corner: add the wing's north wall to the wing chain so it turns
    // two corners, then lap one and check the other stays mitred.
    await page.click('#corner-type .turn-btn[data-value="mitre"]');
    await page.evaluate(() => setActive(state.elevations.findIndex(e => e.result && e.result.name === 'Elevation C')));
    await lookAt('Wing north wall', [0, 0.3, -1]);
    await page.waitForFunction(() => state.elevations.length === 5 && state.elevations[4].result && state.elevations[4].result.ok && state.elevations[4].link, null, { timeout: 60000 });
    await page.waitForTimeout(1500);
    const perCorner = await page.evaluate(async () => {
        const chain = state.elevations[4].chain;
        const rows = () => Array.from(document.querySelectorAll('.corner-row')).map(r => r.dataset.detail);
        const before = rows();
        setCornerDetail(chain.name, 1, 'lap');
        await updatePreview();
        const details = window._lastPreview.info.filter(i => chain.members.some(m => m.name === i.elevation))
            .map(i => `${i.elevation.replace('Elevation ', '')}:${i.corner.details.join('/')}`).join(' ');
        const text = Array.from(document.querySelectorAll('.corner-row')).map(r => r.textContent.replace(/\s+/g, ' ')).join(' || ');
        const after = rows();
        setCornerDetail(chain.name, 1, '');
        await updatePreview();
        return { chain: chain.name, members: chain.members.map(m => m.name).join(','), before, after, details, text, reset: rows() };
    });
    console.log('per-corner detail:', JSON.stringify(perCorner));
    if (perCorner.after.join() !== 'mitre,lap' || perCorner.reset.join() !== 'mitre,mitre' || perCorner.details.indexOf('lap') < 0)
        throw new Error('per-corner detail did not apply: ' + JSON.stringify(perCorner));
    // The same from the 2D view: C turns both corners, so it gets a badge at each end.
    await page.evaluate(() => { setActive(state.elevations.findIndex(e => e.name === 'Elevation C')); toggle2D(); });
    await page.waitForTimeout(1500);
    const badges = await page.evaluate(() => Array.from(document.querySelectorAll('.corner-badge')).map(b => b.textContent));
    await page.click('.corner-badge >> nth=1');
    await page.selectOption('#d2-corner', 'lap');
    await page.waitForTimeout(1500);
    const viaBadge = await page.evaluate(() => ({
        badges: Array.from(document.querySelectorAll('.corner-badge')).map(b => b.textContent),
        rows: Array.from(document.querySelectorAll('.corner-row')).map(r => r.dataset.detail) }));
    console.log('2D corner badges:', JSON.stringify(badges), '→', JSON.stringify(viaBadge));
    if (badges.join() !== 'M,M' || viaBadge.rows.filter(d => d === 'lap').length !== 1 || viaBadge.rows.filter(d => d === 'mitre').length !== 1)
        throw new Error('corner badge did not set one corner: ' + JSON.stringify(viaBadge));
    await page.evaluate(async () => { const ch = state.elevations[4].chain; setCornerDetail(ch.name, 0, ''); setCornerDetail(ch.name, 1, ''); toggle2D(); await updatePreview(); });
    await page.waitForTimeout(1200);
    // Profile at the B–C chain corner (external, right-angled): a profile element and a P
    // badge. Never offered at a re-entrant corner.
    const profiled = await page.evaluate(async () => {
        const ch = state.elevations[4].chain, c = chainCorners(ch)[0];
        setCornerDetail(ch.name, 0, 'profile');
        await updatePreview();
        setActive(state.elevations.findIndex(e => e.name === 'Elevation C')); toggle2D();
        await new Promise(r => setTimeout(r, 1500));
        const out = { detail: cornerDetailInForce(c),
                      meshes: window._lastPreview.geometry.filter(m => m.ifc_type === 'corner_profile').map(m => m.name),
                      badges: Array.from(document.querySelectorAll('.corner-badge:not(.opening-badge)')).map(b => b.textContent),
                      reentrantOffered: !/value="profile"[^>]*disabled/.test(cornerOptions('', profileOffered(-1), true)),
                      externalOffered: !/value="profile"[^>]*disabled/.test(cornerOptions('', profileOffered(1), true)) };
        toggle2D();
        setCornerDetail(ch.name, 0, '');
        await updatePreview();
        return out;
    });
    console.log('corner profile:', JSON.stringify(profiled));
    if (profiled.detail !== 'profile' || profiled.meshes.length !== 1 || !profiled.badges.includes('P')
        || profiled.reentrantOffered || !profiled.externalOffered)
        throw new Error('corner profile not placed or offered wrongly: ' + JSON.stringify(profiled));
    await page.waitForTimeout(1200);
    await page.evaluate(async () => { setActive(4); deleteElevation(); await new Promise(r => setTimeout(r, 1500)); });
    await page.click('#corner-type .turn-btn[data-value="lap"]');
    await page.waitForTimeout(1200);
    // Vertical planks need counter-battens, so the wizard grows a step for them.
    await buildChain('plank', 'vertical');
    console.log('counter-battens built:', await page.evaluate(() =>
        window._lastPreview.geometry.filter(m => m.ifc_type === 'counter_batten').length + ' at '
        + document.getElementById('cb_centres').value + ' c/c'));

    // Dimensions belong to the active elevation, and a course one can be typed over.
    // Vertical planks label their first course "Cut" when it starts off the face edge,
    // so put the job back on horizontal planks where a Course dimension exists.
    await page.click('#plank-orient .turn-btn[data-value="horizontal"]');
    await page.waitForTimeout(1500);
    console.log('dims on screen:', await page.evaluate(() => ({
        active: state.elevations[state.active] && state.elevations[state.active].name,
        labels: dimLabels.length, drawn: dimGroup.children.length,
        elevations: Array.from(new Set(window._lastPreview.dimensions.map(d => d.elevation))).length })));
    console.log('all dims:', await page.evaluate(() => window._lastPreview.dimensions
        .map(d => `${d.elevation.replace('Elevation ', '')}:${d.label}:${d.kind || '-'}`).join(' ')));
    const course = await page.evaluate(() => {
        const d = window._lastPreview.dimensions.find(x => x.kind === 'course');
        if (!d) return null;
        openCourseDialog(d);
        return { title: document.getElementById('course-title').textContent,
                 where: document.getElementById('course-where').textContent,
                 value: document.getElementById('course-value').value,
                 chainButton: document.getElementById('course-chain').style.display !== 'none' };
    });
    console.log('course dialog:', JSON.stringify(course));
    const applied = !course ? 'no course dim' : await page.evaluate(async () => {
        document.getElementById('course-value').value = 300;
        applyCourse('one');
        await new Promise(r => setTimeout(r, 1500));
        return { cover: window._lastPreview.info[0].cover, courses: window._lastPreview.info[0].n_courses };
    });
    console.log('after applying 300:', JSON.stringify(applied));
    if (course) {
        await page.evaluate(async () => {
            openCourseDialog(window._lastPreview.dimensions.find(x => x.kind === 'course'));
            resetCourse();
            await new Promise(r => setTimeout(r, 1500));
        });
        console.log('after reset:', await page.evaluate(() => window._lastPreview.info[0].cover));
    }

    // Chain 2 has two members, so the dialog offers the scope choice.
    const chainScope = await page.evaluate(async () => {
        const b = state.elevations.find(e => e.chain.members.length > 1);
        const d = window._lastPreview.dimensions.find(x => x.kind === 'course' && x.elevation === b.result.name);
        openCourseDialog(d);
        const offered = document.getElementById('course-chain').style.display !== 'none';
        document.getElementById('course-value').value = 250;
        applyCourse('chain');
        await new Promise(r => setTimeout(r, 1500));
        return { offered, chain: b.chain.name,
                 members: b.chain.members.map(m => `${m.name}=${m.cover}`).join(' '),
                 covers: window._lastPreview.info.map(i => `${i.elevation.replace('Elevation ', '')}:${i.cover}`).join(' ') };
    });
    console.log('chain scope:', JSON.stringify(chainScope));
    await page.evaluate(async () => {
        const b = state.elevations.find(e => e.chain.members.length > 1);
        openCourseDialog(window._lastPreview.dimensions.find(x => x.kind === 'course' && x.elevation === b.result.name));
        resetCourse();
        await new Promise(r => setTimeout(r, 1200));
    });

    // The offset belongs to one elevation: shifting B must leave C where it is.
    const offsetScope = await page.evaluate(async () => {
        const b = state.elevations.find(e => e.chain.members.length > 1);
        const c = b.chain.members.find(m => m !== b);
        const battens = name => window._lastPreview.geometry
            .filter(m => m.ifc_type === 'batten' && m.elevation === name)
            .map(m => Math.round(Math.min(...m.profile.map(q => q[0])))).sort((x, y) => x - y).slice(1, 4);
        setActive(state.elevations.indexOf(b));
        await updatePreview();
        const before = { B: battens(b.result.name), C: battens(c.result.name) };
        onSlider(90);                 // the panel slider, on the active elevation
        await updatePreview();
        const after = { B: battens(b.result.name), C: battens(c.result.name) };
        const offsets = b.chain.members.map(m => `${m.name}=${m.offset}`).join(' ');
        onSlider(0);
        await updatePreview();
        return { chain: b.chain.name, moved: b.name, offsets, before, after,
                 restored: battens(b.result.name).join(',') === before.B.join(',') };
    });
    console.log('offset scope:', JSON.stringify(offsetScope));

    // The course datum locks on the first row edit in a chain and never moves on a select.
    // B and C share a chain; give B a splash zone at its foot so it starts 150 higher than
    // C, type a row on B, then select C and B again: no row on either face may move.
    await page.click('#cladding-type .turn-btn[data-value="panel"]');
    await page.waitForTimeout(1500);
    const datum = await page.evaluate(async () => {
        const wait = ms => new Promise(r => setTimeout(r, ms));
        const b = state.elevations.find(e => e.chain.members.length > 1);
        const c = b.chain.members.find(m => m !== b);
        const saved = [b.disabled['base|0|0'], c.disabled['base|0|0']];
        b.disabled['base|0|0'] = false; c.disabled['base|0|0'] = true;
        b.chain.datumFrom = null;
        b.chain.members.forEach(m => { m.panelRows = null; m.cover = null; });
        const rows = () => b.chain.members.map(m => {
            const z0 = m.result.frame.origin[2];
            return m.name.replace('Elevation ', '') + ':' + [...new Set(window._lastPreview.geometry
                .filter(g => g.ifc_type === 'panel' && g.elevation === m.result.name)
                .map(g => Math.round(Math.min(...g.profile.map(q => q[1])) + z0)))].sort((x, y) => x - y).join('/');
        }).join(' ');
        setActive(state.elevations.indexOf(b)); await updatePreview();
        const fallback = rows();
        setActive(state.elevations.indexOf(c)); await updatePreview();
        const unlockedSelect = rows();
        setActive(state.elevations.indexOf(b)); await updatePreview();
        const row = window._lastPreview.dimensions.find(d => d.kind === 'row' && d.elevation === b.result.name);
        if (!row) return { error: 'no row dimension on ' + b.name, type: toggleValue('cladding-type'), fallback,
                           dims: window._lastPreview.dimensions.filter(d => d.elevation === b.result.name).map(d => d.label + ':' + (d.kind || '-')),
                           info: window._lastPreview.info.map(i => `${i.elevation}:${(i.rows || []).join('/')}`) };
        applyDim(row, 700, 'chain');
        await wait(1500);
        const typed = rows(), locked = b.chain.datumFrom && b.chain.datumFrom.name;
        setActive(state.elevations.indexOf(c)); await updatePreview();
        const selectC = rows();
        setActive(state.elevations.indexOf(b)); await updatePreview();
        const selectB = rows();
        // a later edit on C changes C's rows but leaves the datum on B
        applyDim(window._lastPreview.dimensions.find(d => d.kind === 'row' && d.elevation === c.result.name) ||
                 { kind: 'course', value: 0, elevation: c.result.name }, 650, 'one');
        await wait(1500);
        const stillB = b.chain.datumFrom && b.chain.datumFrom.name;
        const hint = !!document.getElementById('edit-datum');
        b.chain.members.forEach(m => { m.panelRows = null; });
        b.chain.datumFrom = null;
        [b.disabled['base|0|0'], c.disabled['base|0|0']] = saved;
        await updatePreview();
        return { b: b.name, fallback, unlockedSelect, typed, locked, selectC, selectB, stillB, hint,
                 bRows: (window._lastPreview.info.find(i => i.elevation === b.result.name) || {}).rows };
    });
    console.log('course datum:', JSON.stringify(datum));
    if (datum.unlockedSelect !== datum.fallback || datum.selectC !== datum.typed || datum.selectB !== datum.typed
        || datum.locked !== datum.b || datum.stillB !== datum.b || datum.hint || datum.typed === datum.fallback)
        throw new Error('selecting an elevation moved the rows, or the datum did not lock: ' + JSON.stringify(datum));
    await page.click('#cladding-type .turn-btn[data-value="plank"]');
    await page.waitForTimeout(1200);

    // The level picker's dot: red on a surface, green when it snaps to a corner.
    await page.evaluate(() => frameElevation(state.elevations[0].result));
    await page.waitForTimeout(500);
    console.log('snap dot:', await page.evaluate(() => {
        const r = renderer.domElement.getBoundingClientRect();
        const mid = { clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 };
        const onFace = snapPick(mid);
        const hit = pickAt(mid);
        const pos = hit.mesh.geometry.attributes.position, vi = hit.face.a;
        const v = new THREE.Vector3(pos.getX(vi), pos.getY(vi), pos.getZ(vi)).applyMatrix4(hit.mesh.matrixWorld).project(camera);
        const near = { clientX: r.left + (v.x + 1) / 2 * r.width + 5, clientY: r.top + (1 - v.y) / 2 * r.height + 4 };
        const atCorner = snapPick(near);
        showSnap(atCorner);
        const colour = snapMarker.material.map.image.getContext('2d').getImageData(32, 32, 1, 1).data.slice(0, 3).join(',');
        showSnap(null);
        return JSON.stringify({ surface: onFace && onFace.snapped, corner: atCorner && atCorner.snapped, cornerDot: colour });
    }));

    // Top and bottom of the cladding, set by two clicks in the model after Build.
    await page.evaluate(() => frameElevation(state.elevations[0].result));
    await page.waitForTimeout(600);
    await buildChain('plank', 'horizontal', false, [0.72, 0.30]);
    console.log('picked levels:', await page.evaluate(() => state.chains.filter(c => c.built)
        .map(c => `${c.name} top ${c.topZ === null ? '-' : Math.round(c.topZ)} bottom ${c.bottomZ === null ? '-' : Math.round(c.bottomZ)}`).join(' | ')));
    console.log('clad band vs face:', await page.evaluate(() => {
        const vs = window._lastPreview.geometry.filter(m => m.ifc_type === 'plank')
            .flatMap(m => m.profile.map(q => q[1]));
        const info = window._lastPreview.info[0];
        return vs.length ? `boards ${Math.round(Math.min(...vs))}–${Math.round(Math.max(...vs))} of face 0–${Math.round(info.height)} (clad top ${Math.round(info.clad_top)})` : 'none';
    }));

    // Planks cannot lap, so the option disables itself and falls back to a mitre.
    await page.click('#cladding-type .turn-btn[data-value="plank"]');
    await page.waitForTimeout(1200);
    const plankCorners = await page.evaluate(() => ({
        lap: document.querySelector('#corner-type .turn-btn[data-value="lap"]').disabled,
        profile: document.querySelector('#corner-type .turn-btn[data-value="profile"]').disabled,
        active: document.querySelector('#corner-type .turn-btn.active').dataset.value,
        options: /value="profile"[^>]*disabled/.test(cornerOptions('', profileOffered(1), true))
                 && /value="lap"[^>]*disabled/.test(cornerOptions('', profileOffered(1), true)) }));
    console.log('Master and Profile disabled for planks:', JSON.stringify(plankCorners));
    if (!plankCorners.lap || !plankCorners.profile || !plankCorners.options || plankCorners.active !== 'mitre')
        throw new Error('Master or Profile offered for planks: ' + JSON.stringify(plankCorners));

    // The server importer, the fallback for models web-ifc cannot build.
    await page.evaluate(() => loadModel(state.file, 'server'));
    await page.waitForFunction(() => state.model && state.model.reader.indexOf('server') >= 0, null, { timeout: 180000 });
    console.log('camera moved on a pick:', cameraMoves.length ? cameraMoves : 'never');
    console.log('server import:', await page.evaluate(() => `${state.model.meshes} elements, ${state.model.storeys} storeys, reader ${state.model.reader}`));

    console.log('SMOKE OK');
    await browser.close();
}

async function dump() {
    console.log('console problems:', logs.length ? logs.slice(0, 15) : 'none');
    if (page) {
        try {
            console.log('page state:', await page.evaluate(() => [document.getElementById('model-info').textContent,
                                                                   document.getElementById('status-chip').textContent]));
        } catch (e) { /* page gone */ }
    }
}

main().then(dump).catch(async e => { console.error('SMOKE FAILED', e.message); await dump(); process.exit(1); });
