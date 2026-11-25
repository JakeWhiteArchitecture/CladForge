// Browser smoke test: load the app in Chromium, import the sample IFC, pick the
// south wall, and check that an elevation is extracted and the exports work.
// Run: node tests/smoke.js  (needs the Flask server on :8080 and the proxy env)
// Several at once: start each app.py with its own PORT and give its test SMOKE_URL.
const path = require('path');
const fs = require('fs');
let page = null, logs = [];
// every line says when it was logged, so a slow step shows itself
const T0 = Date.now(), _log = console.log;
console.log = (...a) => _log('[' + ((Date.now() - T0) / 1000).toFixed(1).padStart(6) + 's]', ...a);
// Wait for the app to finish what it is doing (app.appIdle: a typed number's debounce, an
// extraction, the rebuild, the cutting plan, a view transition) and stay finished for a
// moment, rather than guess how long that takes.
async function settle(min = 120) {
    await page.waitForTimeout(min);
    await page.waitForFunction(() => {
        const now = performance.now();
        if (!appIdle()) { window._idleSince = null; return false; }
        window._idleSince = window._idleSince || now;
        return now - window._idleSince >= 200;
    }, null, { polling: 50, timeout: 180000 });
}

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
            [/^https:\/\/cdn\.jsdelivr\.net\/pyodide\/v0\.29\.5\/full\/(.+)$/, m => path.join(vendor, 'pyodide', m[1])],
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
    // the same wait, for use inside the page: await settled()
    await page.addInitScript(() => {
        window.settled = async (min = 120) => {
            await new Promise(r => setTimeout(r, min));
            for (let since = null; ;) {
                const now = performance.now();
                if (appIdle()) { since = since || now; if (now - since >= 200) return; } else since = null;
                await new Promise(r => setTimeout(r, 50));
            }
        };
    });
    page.on('console', m => { if (m.type() === 'error' || m.type() === 'warning') logs.push(m.type() + ': ' + m.text().slice(0, 300)); });
    page.on('pageerror', e => logs.push('pageerror: ' + e.message));
    page.on('requestfailed', r => logs.push('requestfailed: ' + r.url() + ' ' + (r.failure() && r.failure().errorText)));
    await page.goto(process.env.SMOKE_URL || 'http://127.0.0.1:8080/', { waitUntil: 'load' });

    await page.setInputFiles('#ifc-file', path.join(__dirname, 'sample_house.ifc'));
    await page.waitForFunction(() => typeof allMeshes !== "undefined" && allMeshes.length > 0, null, { timeout: 120000 });
    const modelInfo = await page.evaluate(() => document.getElementById('model-info').textContent);
    console.log('model:', modelInfo);
    // The file goes in the moment the page has loaded, while web-ifc may still be starting:
    // it has to wait for the browser importer, not fall back to the server.
    if (!/Read by web-ifc/.test(modelInfo)) throw new Error('the model was not read in the browser: ' + modelInfo);
    await page.waitForFunction(() => (typeof pyReady !== "undefined" && pyReady === true) || (document.getElementById('status-chip').classList.contains('ready')), null, { timeout: 300000 });
    // The cutting list is worked out on demand by default; most steps below check it after
    // each rebuild, so it is switched to update automatically (the manual mode has its own step).
    await page.evaluate(() => setAutoNesting(true));
    // The drawing resolution: a still view never below the screen's CSS pixels however slow
    // the frames; a moving camera drops only where frames are slow; a frame the engine held
    // up is not measured.
    const ratio = await page.evaluate(() => {
        const dpr = window.devicePixelRatio || 1, out = {};
        viewRatio = null; _redraw.cost = 0;
        out.fastMoving = movingRatio() === stillRatio();
        for (let i = 0; i < 12; i++) frameCost(800);
        out.stillFloor = stillRatio() >= Math.min(dpr, 1) - 1e-9;
        _redraw.cost = 800;
        out.slowMoving = movingRatio() < stillRatio() || stillRatio() <= 0.75;
        _engineCalls++; out.busy = engineBusySince(performance.now()); _engineCalls--;
        window._engineEndedAt = performance.now(); out.busyAfter = engineBusySince(performance.now() - 50);
        out.idle = !engineBusySince(performance.now() + 1);
        viewRatio = null; _redraw.cost = 0; setRatio(stillRatio());
        // the near plane follows the distance to what the camera looks at (depth precision)
        const keep = camera.position.clone(), t = controls.target.clone(), dir = keep.clone().sub(t).normalize();
        camera.position.copy(t).addScaledVector(dir, 15000); fitNearPlane(); out.near15m = Math.abs(camera.near - 100) < 1;
        camera.position.copy(t).addScaledVector(dir, 500); fitNearPlane(); out.nearClose = camera.near === 10;
        camera.position.copy(keep); fitNearPlane();
        return out;
    });
    console.log('resolution rules:', JSON.stringify(ratio));
    if (Object.values(ratio).some(v => !v)) throw new Error('the resolution rules broke: ' + JSON.stringify(ratio));
    console.log('engine ready');
    // A GEOS error is a Python exception, not the end of the engine: Pyodide 0.29.0's Shapely
    // aborted the whole runtime on one (a bad shape from the model killed the page's engine);
    // 0.29.5's catches it. Raise one on purpose and check the engine is still there after.
    const geos = await page.evaluate(async () => {
        try {
            const r = await pyodide.runPythonAsync(`
import shapely
try:
    shapely.from_wkt('POLYGON((0 0,10 0,0 10,10 10,0 0))').intersection(shapely.from_wkt('POLYGON((0 0,10 0,10 10,0 10,0 0))'))
    _r = 'no error'
except Exception as _e:
    _r = type(_e).__name__
_r`);
            return r + ' / alive: ' + (await pyodide.runPythonAsync('1 + 1'));
        } catch (err) { return 'engine died: ' + (err.message || '').slice(0, 120); }
    });
    console.log('GEOS error:', geos);
    if (geos !== 'GEOSException / alive: 2') throw new Error('a GEOS error is not caught as a Python exception: ' + geos);

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
    // Which faces share a chain, whatever the chains are called: [a, a, b] reads 0,0,1.
    const grouping = names => { const seen = []; return names.map(n => { if (!seen.includes(n)) seen.push(n); return seen.indexOf(n); }).join(); };
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
        // Once the cladding is chosen the chain is asked its name, and offered one: the way it
        // faces and what it is. Next takes what is offered.
        const naming = await page.evaluate(() => [document.getElementById('wiz-title').textContent,
            Array.from(document.querySelectorAll('#wiz-body input[type=text]')).map(n => n.value).join(' / ')]);
        console.log('  wizard name:', naming.join(' | '));
        if (!/^Name the chain/.test(naming[0]) || !naming[1] || !naming[1].split(' / ').every(n => /(Plank|Panel|Standing seam) (cladding|roofing)( \d+)?$/.test(n)))
            throw new Error('the wizard did not offer the chain a name: ' + JSON.stringify(naming));
        await page.click('#wiz-next');
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
        await settle();
    }

    await lookAt('South wall', [0, 0.35, 1]);
    await page.waitForFunction(() => typeof state !== "undefined" && state.elevations.length && state.elevations[0].result && state.elevations[0].result.ok, null, { timeout: 60000 });
    const result = await page.evaluate(() => state.elevations[0].result);
    console.log('elevation:', result.name, result.width, 'x', result.height, 'holes', result.n_holes,
                'abutments', result.abutments.map(a => `${a.source}@${a.v}[${a.u0}-${a.u1}]`).join(', '), 'warnings', result.warnings);
    console.log('nothing built yet, preview empty:', await page.evaluate(() => cladGroup.children.length === 0));
    await buildChain('plank', 'horizontal');
    await page.waitForFunction(() => cladGroup.children.length > 0, null, { timeout: 60000 });
    // The chain took the name it was offered: the south wall's, in planks.
    const firstName = await page.evaluate(() => [state.chains[0].name, document.querySelector('.chain-head') ? document.querySelector('.chain-head').textContent : ''].join(' | '));
    console.log('chain name:', firstName);
    if (!/^South Plank cladding \| South Plank cladding/.test(firstName)) throw new Error('the chain was not named after its face and cladding: ' + firstName);
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
        await settled();
        return { offset: state.elevations[0].chain.offset, label: document.getElementById('edit-offset-val').textContent,
                 battens: window.battenEdges() };
    });
    console.log('edit widget shift:', JSON.stringify(shifted), '| battens were at', JSON.stringify(before.battens));
    await page.keyboard.press('Escape');
    await page.evaluate(async () => { document.getElementById('edit-offset').value = 0; onEditSlide(0); await settled(); });
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
    await settle();
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
        await settled();
        const after = { rows: rows(), panels: panels(), stored: state.elevations[0].panelRows.join('/') };
        openCourseDialog(window._lastPreview.dimensions.find(x => x.kind === 'row'));
        resetCourse();
        await settled();
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
    await settle();
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
    await settle();        // 300 ms debounce, then the preview
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
    await settle();
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
    await settle();
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
    await settle();
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
    await settle();
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
    await settle();
    const toAll = await page.evaluate(() => Object.values(state.elevations[0].openingDetails).map(d => d.jamb).join(','));
    await (await jambBadges())[1].click();
    await page.selectOption('#o2-detail', 'profile');
    await page.click('#o2-no');
    await settle();
    const openingsNow = await page.evaluate(() => ({
        jambs: openingsOf(state.elevations[0]).map(o => o.jamb).join(','),
        profiles: window._lastPreview.geometry.filter(m => m.ifc_type === 'corner_profile').map(m => m.name.replace('Elevation ', '')).join(','),
        badges: Array.from(document.querySelectorAll('.opening-badge')).map(b => b.textContent).join(','),
        others: state.elevations.slice(1).map(e => Object.keys(e.openingDetails || {}).length).join(',') }));
    console.log('2D openings:', JSON.stringify({ asked, toAll, ...openingsNow }));
    if (!asked || toAll !== 'lap,lap' || openingsNow.jambs.split(',').sort().join() !== 'lap,profile'
        || openingsNow.profiles.split(',').length !== 2 || /[1-9]/.test(openingsNow.others))
        throw new Error('opening corner details did not apply as asked: ' + JSON.stringify({ asked, toAll, openingsNow }));
    // Each opening's box says where its linings run back to (no window in the sample model:
    // the chain's setback, 50 mm behind the wall face); a value typed there wins, and Clear
    // goes back. The linings follow.
    const reach = () => page.evaluate(() => {
        const m = window._lastPreview.geometry.find(g => g.ifc_type === 'reveal' && / Reveal 1L$/.test(g.name) && g.elevation === 'Elevation A');
        return m ? Math.round(Math.max(...m.profile.map(q => q[0]))) : null;
    });
    const face0 = await page.evaluate(() => Math.round(window._lastPreview.info[0].total_depth));
    const reachDefault = await reach();
    await (await jambBadges())[0].click();
    const frameText = await page.evaluate(() => document.querySelector('#dim2d-editor .d2-row').textContent.trim());
    await page.fill('#o2-frame', '20');
    await page.press('#o2-frame', 'Enter');
    await settle();
    const reachSet = await reach();
    await (await jambBadges())[0].click();
    await page.click('#o2-frame-clear');
    await settle();
    const reachBack = await reach();
    console.log('opening frame:', JSON.stringify({ frameText, face0, reachDefault, reachSet, reachBack }));
    if (!/50 mm behind the wall face · .+'s frame setback/.test(frameText) || reachDefault !== face0 + 50
        || reachSet !== face0 + 20 || reachBack !== reachDefault)
        throw new Error('opening frame position wrong: ' + JSON.stringify({ frameText, face0, reachDefault, reachSet, reachBack }));
    // Cavity barriers on all four sides: the 2D view's button sets the chain (and the
    // sidebar's box with it); an opening's own box sets just that one. The head and sill
    // barriers arrive, all four timbers carry a membrane, and no upright batten comes
    // within 200 mm of them.
    const barrierParts = () => page.evaluate(() => {
        const mine = window._lastPreview.geometry.filter(g => g.elevation === 'Elevation A');
        const tags = t => mine.filter(g => g.ifc_type === t).map(g => g.name.split(' ').pop()).sort().join(',');
        const info = window._lastPreview.info.find(i => i.elevation === 'Elevation A') || {};
        const zones = mine.filter(g => g.ifc_type === 'closer' && /Barrier \d+[HS]$/.test(g.name)).map(g => {
            const us = g.profile.map(q => q[0]), vs = g.profile.map(q => q[1]);
            return [Math.min(...us), Math.max(...us), Math.min(...vs) - 199.9, Math.max(...vs) + 199.9]; });
        const near = mine.filter(g => g.ifc_type === 'batten').filter(g => {
            const us = g.profile.map(q => q[0]), vs = g.profile.map(q => q[1]);
            if (Math.max(...vs) - Math.min(...vs) < Math.max(...us) - Math.min(...us)) return false;
            return zones.some(z => Math.min(...us) < z[1] && Math.max(...us) > z[0] && Math.min(...vs) < z[3] && Math.max(...vs) > z[2]); }).length;
        return { closers: tags('closer'), dpm: tags('dpm'), tops: mine.filter(g => g.ifc_type === 'dpm' && / (Top|Side) /.test(g.name)).length, near, cut: (info.barriers || {}).cut || 0,
                 button: document.getElementById('barriers-2d').textContent, shown: document.getElementById('barriers-2d').style.display !== 'none',
                 box: document.getElementById('barriers').checked,
                 check: [...document.querySelectorAll('#checks-container > div')].some(x => /cavity barriers on all four sides/.test(x.textContent)) };
    });
    const barriersOff = await barrierParts();
    await page.click('#barriers-2d');
    await settle();
    const barriersOn = await barrierParts();
    await (await jambBadges())[0].click();
    await page.click('#o2-barriers');                       // this opening on its own: off
    await settle();
    const barriersOne = await barrierParts();
    await page.click('#barriers-2d');                       // the chain off again: the one opening's own say goes with it
    await settle();
    const barriersBack = await barrierParts();
    console.log('cavity barriers:', JSON.stringify({ barriersOff, barriersOn, barriersOne, barriersBack }));
    if (!barriersOff.shown || barriersOff.box || /[HS]/.test(barriersOff.closers) || barriersOff.dpm
        || !barriersOn.box || !/on$/.test(barriersOn.button) || !/1H/.test(barriersOn.closers) || !/1S/.test(barriersOn.closers)
        || barriersOn.dpm.split(',').length - barriersOn.tops !== barriersOn.closers.split(',').length || !barriersOn.tops || barriersOn.near || !barriersOn.cut || !barriersOn.check
        || barriersOne.closers.split(',').length >= barriersOn.closers.split(',').length || !barriersOne.box
        || barriersBack.box || barriersBack.closers !== barriersOff.closers || barriersBack.dpm || barriersBack.check)
        throw new Error('cavity barriers did not follow the 2D button: ' + JSON.stringify({ barriersOff, barriersOn, barriersOne, barriersBack }));
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
    // Not updating automatically: a rebuild only marks the list out of date, and Update repacks it.
    const manual = await page.evaluate(async () => {
        setAutoNesting(false);
        await new Promise(r => setTimeout(r, 900));      // anything already packing lands first
        const plan = window._lastPlan;
        d2Elev(view2d.name).panelRows = [1000, 1000, 1000]; await updatePreview();
        await new Promise(r => setTimeout(r, 700));
        const box = document.getElementById('waste-readout');
        const stale = { same: window._lastPlan === plan, flag: !!window._planStale, cls: box.classList.contains('stale'),
                        text: document.getElementById('wr-stale-text').textContent, shown: document.getElementById('wr-stale').style.display !== 'none' };
        await updateCuttingList();
        const fresh = { changed: window._lastPlan !== plan, flag: !!window._planStale, cls: box.classList.contains('stale'),
                        shown: document.getElementById('wr-stale').style.display !== 'none' };
        d2Elev(view2d.name).panelRows = [1000, 1000];
        setAutoNesting(true); await updatePreview();
        return { stale, fresh, auto: document.getElementById('wr-auto').checked };
    });
    console.log('cutting list on demand:', JSON.stringify(manual));
    if (!manual.stale.same || !manual.stale.flag || !manual.stale.cls || !manual.stale.shown || !/Out of date/.test(manual.stale.text)
        || !manual.fresh.changed || manual.fresh.flag || manual.fresh.cls || manual.fresh.shown || !manual.auto)
        throw new Error('the cutting list did not wait for Update: ' + JSON.stringify(manual));
    await page.waitForFunction(() => window._lastPlan && window._lastPlan.n_pieces && !window._planStale, null, { timeout: 20000 });
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
    await settle();
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
    await page.evaluate(async () => { window._smokePlan = window._lastPlan; const e = d2Elev(view2d.name); e.panelRows = null; e.panelJoints = null; await updatePreview(); });
    // Flat, every panel is tinted by how well its board is used; back in 3D they are not.
    await page.waitForFunction(() => window._lastPlan && window._lastPlan !== window._smokePlan
                                     && document.getElementById('wr-key').style.display !== 'none', null, { timeout: 20000 });
    const tint = await page.evaluate(() => {
        const panels = cladGroup.children.flatMap(l => l.children).filter(o => o.isMesh && / Panel R\d/.test(o.userData.name || '') && o.visible);
        const hex = panels.map(o => o.material.color.getHex());
        const bands = [0x22c55e, 0xf59e0b, 0xef4444];
        const right = panels.filter(o => { const f = window._lastPlan.fills[o.userData.name];
            return f === undefined || o.material.color.getHex() === (f >= 0.9 ? bands[0] : f >= 0.75 ? bands[1] : bands[2]); }).length;
        const oversize = panels.filter(o => window._lastPlan.oversize.includes(o.userData.name)).length;
        return { panels: panels.length, tinted: hex.filter(h => bands.includes(h)).length, right, oversize };
    });
    console.log('board use tint:', JSON.stringify(tint));
    if (!tint.panels || tint.tinted !== tint.panels - tint.oversize || tint.right !== tint.panels) throw new Error('panels not tinted by board use: ' + JSON.stringify(tint));
    await page.keyboard.press('Escape');
    await settle();
    const back = await page.evaluate(() => ({ ortho: !!activeCamera().isOrthographicCamera, rotate: controls.enableRotate,
        pose: [camera.position.toArray(), controls.target.toArray()].map(v => v.map(Math.round).join(',')).join(' → '),
        opacity: modelGroup.children[0].material.opacity }));
    back.tinted = await page.evaluate(() => cladGroup.children.flatMap(l => l.children)
        .filter(o => o.isMesh && / Panel R\d/.test(o.userData.name || '') && [0x22c55e, 0xf59e0b, 0xef4444].includes(o.material.color.getHex())).length);
    back.key = await page.evaluate(() => document.getElementById('wr-key').style.display);
    console.log('back to 3D:', JSON.stringify(back), '| was', pose3d);
    if (back.tinted || back.key !== 'none') throw new Error('panels still tinted in 3D: ' + JSON.stringify(back));
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
        renderReadout(Object.assign({}, plan, { waste: 0.04 }));
        const at4 = { over: box.classList.contains('over'), colour: red() };
        renderReadout(Object.assign({}, plan, { waste: 0.06 }));
        const at6 = { over: box.classList.contains('over'), colour: red() };
        renderReadout(plan);
        return { real, at4, at6 };
    });
    console.log('waste readout:', JSON.stringify(readout));
    if (readout.real.boards < readout.real.lower || readout.real.pieces < 1 || readout.at4.over || !readout.at6.over
        || readout.at6.colour !== 'rgb(239, 68, 68)' || readout.at4.colour === readout.at6.colour
        || readout.real.over !== (readout.real.waste > 5) || readout.real.ms > 1000)
        throw new Error('waste readout wrong: ' + JSON.stringify(readout));
    // Every built chain is packed (each elevation on its own), and Optimise boards runs the
    // full search: never more boards than the quick check, and the readout says which it is.
    const optimised = await page.evaluate(async () => {
        const quick = window._lastPlan, builtNames = state.chains.filter(c => c.built)
            .flatMap(c => c.members.filter(m => m.result && m.result.ok).map(m => m.result.name));
        const pieces = window._lastPreview.geometry.filter(m => m.ifc_type === 'panel' && builtNames.includes(m.elevation)).length;
        await optimiseBoards();
        const deep = window._lastPlan;
        return { quick: [quick.n_boards, quick.strategies, quick.deep], deep: [deep.n_boards, deep.strategies, deep.deep],
                 pooled: quick.n_pieces === pieces, chains: state.chains.filter(c => c.built).length, ms: deep.ms,
                 title: document.getElementById('wr-title').textContent, sizes: document.getElementById('wr-sizes').textContent,
                 button: getComputedStyle(document.getElementById('wr-optimise')).display, status: document.getElementById('status-chip').textContent };
    });
    console.log('optimise boards:', JSON.stringify(optimised));
    if (!optimised.pooled || optimised.quick[1] !== 16 || optimised.quick[2] || !optimised.deep[2] || optimised.deep[0] > optimised.quick[0]
        || !/optimised$/.test(optimised.title) || !/^Divides the board: 1250 · 623 · 414 × 2500 · 1248/.test(optimised.sizes)
        || optimised.button !== 'none' || !/^Optimised: /.test(optimised.status) || optimised.ms > 15000)
        throw new Error('Optimise boards wrong: ' + JSON.stringify(optimised));
    // Edge trim comes off the board before packing: never fewer boards, and the readout says so.
    const trimmed = await page.evaluate(async () => {
        const before = window._lastPlan.n_boards;
        document.getElementById('board_trim').value = 10; await updatePreview();
        await new Promise(r => setTimeout(r, 900));
        const plan = window._lastPlan, note = document.getElementById('wr-note').textContent;
        document.getElementById('board_trim').value = 0; await updatePreview();
        await new Promise(r => setTimeout(r, 900));
        return { before, after: plan.n_boards, trim: plan.trim, note };
    });
    console.log('edge trim:', JSON.stringify(trimmed));
    if (trimmed.trim !== 10 || !/10 mm edge trim/.test(trimmed.note)) throw new Error('edge trim not applied: ' + JSON.stringify(trimmed));
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
    // One row per elevation, each board cut for one elevation.
    if (!/Elevation A  -  \d+ board\(s\), waste/.test(planText) || !planText.includes('Each elevation packed on its own'))
        throw new Error('cutting plan DXF is not laid out per elevation');

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
    // IFC version: the default is IFC4X3; choosing IFC4 relabels the button and writes that schema.
    const schemaOf = file => (fs.readFileSync(file, 'latin1').match(/FILE_SCHEMA\s*\(\s*\(\s*'([^']+)'/) || [])[1];
    const firstSchema = schemaOf(path.join(__dirname, 'smoke_out.ifc'));
    await page.selectOption('#ifc_schema', 'IFC4');
    const ifcLabel = await page.textContent('#ifc-btn');
    await page.click('#ifc-btn');
    await page.waitForSelector('#download-reminder.open', { timeout: 300000 });
    const [ifc4] = await Promise.all([page.waitForEvent('download'), page.click('#download-reminder .btn-primary')]);
    await ifc4.saveAs(path.join(__dirname, 'smoke_out.4.ifc'));
    const secondSchema = schemaOf(path.join(__dirname, 'smoke_out.4.ifc'));
    console.log('IFC version:', firstSchema, 'then', JSON.stringify(ifcLabel), '->', secondSchema);
    if (!/^IFC4X3/.test(firstSchema || '') || secondSchema !== 'IFC4' || ifcLabel.trim() !== 'Download IFC4')
        throw new Error('IFC version choice: ' + firstSchema + ' / ' + ifcLabel + ' / ' + secondSchema);
    await page.selectOption('#ifc_schema', 'IFC4X3');
    // Level of development: the LOD button redraws the same build as flat sheets (100), plain
    // boards and battens (200) or everything (300), without rebuilding; and the IFC can be
    // written at any one of them, or all three at once as three files.
    const lod = await page.evaluate(() => {
        const count = () => { let n = 0; cladGroup.children.forEach(g => { n += g.children.filter(c => c.isMesh).length; }); return n; };
        const full = count();
        document.querySelector('#lod-switch button[data-lod="200"]').click(); const plain = count();
        document.querySelector('#lod-switch button[data-lod="100"]').click(); const sheets = count();
        const active = document.querySelector('#lod-switch button.active').dataset.lod;
        document.querySelector('#lod-switch button[data-lod="300"]').click();
        return { full, plain, sheets, active, back: count(), masses: window._lastPreview.masses.length, faces: state.elevations.length,
                 tagged: window._lastPreview.geometry.every(m => m.lod === 200 || m.lod === 300),
                 options: [...document.getElementById('ifc_lod').options].map(o => o.value).join() };
    });
    console.log('LOD view:', JSON.stringify(lod));
    await page.evaluate(() => { frameElevation(state.elevations[0].result); setLayerVisible('faces', false); setLayerVisible('dims', false); });
    for (const level of [100, 200, 300]) {
        await page.evaluate(n => document.querySelector(`#lod-switch button[data-lod="${n}"]`).click(), level);
        await page.waitForTimeout(500);
        await page.screenshot({ path: path.join(__dirname, `smoke_lod${level}.png`) });
    }
    await page.evaluate(() => { setLayerVisible('faces', true); setLayerVisible('dims', true); });
    if (lod.sheets !== lod.masses || lod.sheets < 1 || !(lod.plain < lod.full && lod.plain > lod.sheets) || lod.back !== lod.full
        || lod.active !== '100' || !lod.tagged || lod.options !== '300,200,100,all')
        throw new Error('the LOD switch did not redraw the build at each level: ' + JSON.stringify(lod));
    await page.selectOption('#ifc_lod', 'all');
    const lodFiles = [];
    const keep = d => lodFiles.push(d);
    page.on('download', keep);
    await page.click('#ifc-btn');
    await page.waitForSelector('#download-reminder.open', { timeout: 300000 });
    await page.click('#download-reminder .btn-primary');
    for (let i = 0; i < 100 && lodFiles.length < 3; i++) await page.waitForTimeout(100);
    page.off('download', keep);
    const lodSaid = [];
    for (const d of lodFiles) {
        const out = path.join(__dirname, 'smoke_out.' + d.suggestedFilename().replace(/^.*_(LOD\d+)\.ifc$/, '$1') + '.ifc');
        await d.saveAs(out);
        const text = fs.readFileSync(out, 'latin1');
        lodSaid.push([d.suggestedFilename().replace(/^.*_(LOD\d+)\.ifc$/, '$1'), (text.match(/'LevelOfDevelopment',\$,IFCLABEL\('(LOD \d+)'\)/) || [])[1],
                      (text.match(/IFCCOVERING|IFCMEMBER|IFCPLATE/g) || []).length].join(':'));
    }
    console.log('LOD export, all three:', lodSaid.join(' | '));
    const sizes = lodSaid.map(s => +s.split(':')[2]);
    if (lodSaid.length !== 3 || !/^LOD100:LOD 100:/.test(lodSaid[0]) || !/^LOD200:LOD 200:/.test(lodSaid[1]) || !/^LOD300:LOD 300:/.test(lodSaid[2])
        || !(sizes[0] < sizes[1] && sizes[1] < sizes[2]))
        throw new Error('the IFC was not written at all three levels: ' + lodSaid.join(' | '));
    await page.selectOption('#ifc_lod', '300');
    // Corners: the chain's corner row, the master-lap detail and the master swap.
    await page.click('#cladding-type .turn-btn[data-value="panel"]');
    await settle();
    console.log('corner row (mitred):', await page.evaluate(() => (document.querySelector('.corner-row') || {}).textContent));
    const mitred = await page.evaluate(() => window._lastPreview.geometry.filter(m => m.corner).length);
    await page.click('#corner-type .turn-btn[data-value="lap"]');
    await settle();
    const lapped = await page.evaluate(() => ({
        row: (document.querySelector('.corner-row') || {}).textContent,
        marked: window._lastPreview.geometry.filter(m => m.corner).length,
        types: Array.from(new Set(window._lastPreview.geometry.filter(m => m.corner).map(m => m.ifc_type))),
        exts: Array.from(new Set(window._lastPreview.geometry.filter(m => m.corner)
            .map(m => Math.round((m.corner.ext_r !== undefined ? m.corner.ext_r : m.corner.ext_l))))).sort((a, b) => a - b),
    }));
    console.log('mitred elements:', mitred, '| lapped:', JSON.stringify(lapped));
    await page.click('.corner-row button');
    await settle();
    console.log('after swap:', await page.evaluate(() => (document.querySelector('.corner-row') || {}).textContent));
    console.log('lap check:', await page.evaluate(() => (Array.from(document.querySelectorAll('.check-item span'))
        .map(s => s.textContent).find(t => t.indexOf('corner end') >= 0) || 'none')));
    // Corner detail per corner: add the wing's north wall to the wing chain so it turns
    // two corners, then lap one and check the other stays mitred.
    await page.click('#corner-type .turn-btn[data-value="mitre"]');
    await page.evaluate(() => setActive(state.elevations.findIndex(e => e.result && e.result.name === 'Elevation C')));
    await lookAt('Wing north wall', [0, 0.3, -1]);
    await page.waitForFunction(() => state.elevations.length === 5 && state.elevations[4].result && state.elevations[4].result.ok && state.elevations[4].link, null, { timeout: 60000 });
    await settle();
    const perCorner = await page.evaluate(async () => {
        const chain = state.elevations[4].chain;
        // Each chain has its own settings: the master lap is a panel detail, so make this
        // chain panels (with one of its faces active, so the sidebar edits this chain).
        setActive(state.elevations.indexOf(chain.members[0]));
        selectToggle('cladding-type', 'panel'); onTypeChange();
        await updatePreview();
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
    await settle();
    const badges = await page.evaluate(() => Array.from(document.querySelectorAll('.corner-badge')).map(b => b.textContent));
    await page.click('.corner-badge >> nth=1');
    await page.selectOption('#d2-corner', 'lap');
    await settle();
    const viaBadge = await page.evaluate(() => ({
        badges: Array.from(document.querySelectorAll('.corner-badge')).map(b => b.textContent),
        rows: Array.from(document.querySelectorAll('.corner-row')).map(r => r.dataset.detail) }));
    console.log('2D corner badges:', JSON.stringify(badges), '→', JSON.stringify(viaBadge));
    if (badges.join() !== 'M,M' || viaBadge.rows.filter(d => d === 'lap').length !== 1 || viaBadge.rows.filter(d => d === 'mitre').length !== 1)
        throw new Error('corner badge did not set one corner: ' + JSON.stringify(viaBadge));
    await page.evaluate(async () => { const ch = state.elevations[4].chain; setCornerDetail(ch.name, 0, ''); setCornerDetail(ch.name, 1, ''); toggle2D(); await updatePreview(); });
    await settle();
    // Profile at the B–C chain corner (external, right-angled): a profile element and a P
    // badge. Never offered at a re-entrant corner.
    const profiled = await page.evaluate(async () => {
        const ch = state.elevations[4].chain, c = chainCorners(ch)[0];
        setCornerDetail(ch.name, 0, 'profile');
        await updatePreview();
        setActive(state.elevations.findIndex(e => e.name === 'Elevation C')); toggle2D();
        await settled();
        const out = { detail: cornerDetailInForce(c),
                      meshes: window._lastPreview.geometry.filter(m => m.ifc_type === 'corner_profile').map(m => m.name),
                      badges: Array.from(document.querySelectorAll('.corner-badge:not(.opening-badge)')).map(b => b.textContent),
                      // the battens there meet in a solid L of timber, one twice as wide
                      timbers: window._lastPreview.geometry.filter(m => m.corner_timber).map(m => m.corner_timber).sort().join(','),
                      flanges: (window._lastPreview.geometry.find(m => m.ifc_type === 'corner_profile') || {}).profile_info,
                      gaskets: window._lastPreview.geometry.filter(m => m.ifc_type === 'gasket').length,
                      pgap: document.getElementById('edge-pgap').value,
                      reentrantOffered: !/value="profile"[^>]*disabled/.test(cornerOptions('', profileOffered(-1), true)),
                      externalOffered: !/value="profile"[^>]*disabled/.test(cornerOptions('', profileOffered(1), true)) };
        toggle2D();
        setCornerDetail(ch.name, 0, '');
        await updatePreview();
        return out;
    });
    console.log('corner profile:', JSON.stringify(profiled));
    if (profiled.detail !== 'profile' || profiled.meshes.length !== 1 || !profiled.badges.includes('P')
        || profiled.reentrantOffered || !profiled.externalOffered || !/narrow/.test(profiled.timbers) || !/wide/.test(profiled.timbers)
        || !profiled.flanges || profiled.flanges.flange_a !== 35 || profiled.flanges.flange_b !== 35
        || !(profiled.gaskets > 0) || profiled.pgap !== '1')
        throw new Error('corner profile not placed or offered wrongly: ' + JSON.stringify(profiled));
    await settle();
    await page.evaluate(async () => { setActive(4); deleteElevation(); await settled(); });
    await page.click('#corner-type .turn-btn[data-value="lap"]');
    await settle();
    // Vertical planks need counter-battens, so the wizard grows a step for them.
    await buildChain('plank', 'vertical');
    console.log('counter-battens built:', await page.evaluate(() =>
        window._lastPreview.geometry.filter(m => m.ifc_type === 'counter_batten').length + ' at '
        + document.getElementById('cb_centres').value + ' c/c'));

    // Dimensions belong to the active elevation, and a course one can be typed over.
    // Vertical planks label their first course "Cut" when it starts off the face edge,
    // so put the job back on horizontal planks where a Course dimension exists.
    await page.click('#plank-orient .turn-btn[data-value="horizontal"]');
    await settle();
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
        await settled();
        return { cover: window._lastPreview.info[0].cover, courses: window._lastPreview.info[0].n_courses };
    });
    console.log('after applying 300:', JSON.stringify(applied));
    if (course) {
        await page.evaluate(async () => {
            openCourseDialog(window._lastPreview.dimensions.find(x => x.kind === 'course'));
            resetCourse();
            await settled();
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
        await settled();
        return { offered, chain: b.chain.name,
                 members: b.chain.members.map(m => `${m.name}=${m.cover}`).join(' '),
                 covers: window._lastPreview.info.map(i => `${i.elevation.replace('Elevation ', '')}:${i.cover}`).join(' ') };
    });
    console.log('chain scope:', JSON.stringify(chainScope));
    await page.evaluate(async () => {
        const b = state.elevations.find(e => e.chain.members.length > 1);
        openCourseDialog(window._lastPreview.dimensions.find(x => x.kind === 'course' && x.elevation === b.result.name));
        resetCourse();
        await settled();
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
    await settle();
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
    await settle();

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

    // Planks cannot lap, so Master disables itself and falls back to a mitre. Profile stays
    // on offer: with planks it is the square timber corner trim.
    await page.click('#cladding-type .turn-btn[data-value="plank"]');
    await settle();
    const plankCorners = await page.evaluate(() => ({
        lap: document.querySelector('#corner-type .turn-btn[data-value="lap"]').disabled,
        profile: document.querySelector('#corner-type .turn-btn[data-value="profile"]').disabled,
        active: document.querySelector('#corner-type .turn-btn.active').dataset.value,
        options: !/value="profile"[^>]*disabled/.test(cornerOptions('', profileOffered(1), true))
                 && /value="lap"[^>]*disabled/.test(cornerOptions('', profileOffered(1), true)) }));
    console.log('Master disabled, Profile (the trim) offered for planks:', JSON.stringify(plankCorners));
    if (!plankCorners.lap || plankCorners.profile || !plankCorners.options || plankCorners.active !== 'mitre')
        throw new Error('plank corner options wrong: ' + JSON.stringify(plankCorners));

    // Save state: every chain, its surfaces and the settings to XML. Change things, load the
    // file back, and the cladding is exactly what it was, from the surfaces in the file.
    const snapshotOf = () => page.evaluate(() => ({
        geometry: JSON.stringify(window._lastPreview.geometry.map(m => [m.name, m.profile, m.frame.origin])),
        panel_w: document.getElementById('panel_w').value, type: toggleValue('cladding-type'),
        elevations: state.elevations.map(e => `${e.name}@${e.chain.name}${e.chain.built ? '+' : ''}`).join(','),
        offsets: state.elevations.map(e => e.offset).join(',') }));
    const savedLook = await snapshotOf();
    const [stateFile] = await Promise.all([page.waitForEvent('download'), page.click('#save-state')]);
    const statePath = path.join(__dirname, 'smoke_out.state.xml');
    await stateFile.saveAs(statePath);
    // the file is named after the chains in it
    const stateName = stateFile.suggestedFilename(), chainNames = await page.evaluate(() => state.chains.map(c => c.name));
    console.log('state file name:', stateName, '| chains:', chainNames.join(', '));
    if (!/\.state\.xml$/.test(stateName) || stateName.indexOf(chainNames[0]) !== 0)
        throw new Error('the state file is not named after its chains: ' + stateName + ' / ' + chainNames.join(', '));
    const stateText = fs.readFileSync(statePath, 'utf8');
    await page.evaluate(async () => {
        document.getElementById('panel_w').value = 900;
        selectToggle('cladding-type', 'panel'); onTypeChange();
        state.elevations.forEach(e => { e.offset = 321; });
        await updatePreview();
    });
    const changedLook = await snapshotOf();
    await page.setInputFiles('#state-file', statePath);
    await page.waitForFunction(() => /^Loaded /.test(document.getElementById('status-chip').textContent), null, { timeout: 30000 });
    const loadedLook = await snapshotOf();
    const loaded = await page.evaluate(() => ({ status: document.getElementById('status-chip').textContent,
        restored: state.elevations.every(e => e.restored && !e.picks.length && e.savedPicks.length && e.result.ok) }));
    // A file that is not a state is refused with a reason, and leaves the session alone.
    await page.setInputFiles('#state-file', { name: 'drawing.xml', mimeType: 'application/xml', buffer: Buffer.from('<svg/>') });
    await page.waitForFunction(() => /^Not loaded/.test(document.getElementById('status-chip').textContent), null, { timeout: 30000 });
    const refusedState = await page.evaluate(() => document.getElementById('status-chip').textContent);
    // With no host model open the surfaces still load, onto the offset they were saved with.
    const noModel = await page.evaluate(async xml => {
        const meshes = allMeshes, offset = modelOffset.slice();
        allMeshes = []; modelOffset = [0, 0, 0];
        const ok = await loadState(xml, 'no-model.xml');
        const back = modelOffset.join(',') === offset.join(',');
        allMeshes = meshes;
        return { ok, back, panels: window._lastPreview.geometry.length };
    }, stateText);
    // Saved on a model whose offset was another: the surfaces move by the difference, in the
    // engine's axes (x, y, up) though the offsets are the scene's (x, up, -y).
    const moved = await page.evaluate(async xml => {
        const before = state.elevations.map(e => e.result.frame.origin.slice());
        const mo = modelOffset, so = [mo[0] + 10, mo[1] + 20, mo[2] + 30];
        const other = xml.replace(/(<model t="object">[\s\S]*?<offset t="nums">)[^<]*(<\/offset>)/, `$1${so.join(' ')}$2`);
        await loadState(other, 'other-offset.xml');
        const d = state.elevations.map((e, i) => e.result.frame.origin.map((x, k) => Math.round((x - before[i][k]) * 10) / 10));
        await loadState(xml, 'back.xml');
        return d;
    }, stateText);
    if (!moved.length || !moved.every(d => d[0] === 10 && d[1] === -30 && d[2] === 20))
        throw new Error('a state saved on another offset did not move onto this one in the engine\'s axes: ' + JSON.stringify(moved));
    const sameAgain = (await snapshotOf()).geometry === savedLook.geometry;
    console.log('save state:', stateFile.suggestedFilename(), stateText.length, 'bytes |', loaded.status, '|', refusedState,
                '| no model:', JSON.stringify(noModel), '| elevations', savedLook.elevations, '→', loadedLook.elevations);
    if (!/^<\?xml[^>]*>\s*<CladForgeState format="cladforge-state" version="1">/.test(stateText) || !stateText.includes('<surface t="object">')
        || changedLook.geometry === savedLook.geometry || loadedLook.geometry !== savedLook.geometry
        || loadedLook.panel_w !== savedLook.panel_w || loadedLook.type !== savedLook.type
        || loadedLook.elevations !== savedLook.elevations || loadedLook.offsets !== savedLook.offsets
        || !loaded.restored || !/Not loaded: not a CladForge state file/.test(refusedState) || !noModel.ok || !noModel.back || !sameAgain)
        throw new Error('save and load state did not round-trip: ' + JSON.stringify({ savedLook: savedLook.elevations, loadedLook, loaded, refusedState, noModel, sameAgain }));

    // Add states: a saved state's chains join the job that is open. The state the job was
    // loaded from adds nothing (every face is already there); with a face deleted first it
    // brings that face back, under a name that is free, and the rest are left out.
    const jobNow = () => page.evaluate(() => ({ chains: state.chains.length, elevations: state.elevations.length,
        names: state.elevations.map(e => e.name).sort().join(','), unique: new Set(state.elevations.map(e => e.name)).size === state.elevations.length,
        chainNames: new Set(state.chains.map(c => c.name)).size === state.chains.length,
        resultNames: state.elevations.every(e => e.result.name === e.name), status: document.getElementById('status-chip').textContent }));
    const beforeAdd = await jobNow();
    await page.setInputFiles('#state-add', statePath);
    await page.waitForFunction(() => /^Added 1 of 1 state file/.test(document.getElementById('status-chip').textContent), null, { timeout: 30000 });
    const addedSame = await jobNow();
    await page.evaluate(() => { setActive(state.elevations.length - 1); deleteElevation(); });
    await settle();
    const afterDelete = await jobNow();
    await page.setInputFiles('#state-add', [{ name: 'smoke_out.state.xml', mimeType: 'application/xml', buffer: Buffer.from(stateText) },
                                            { name: 'drawing.xml', mimeType: 'application/xml', buffer: Buffer.from('<svg/>') }]);
    await page.waitForFunction(() => /^Added 1 of 2 state files/.test(document.getElementById('status-chip').textContent), null, { timeout: 30000 });
    await settle();
    const addedBack = await jobNow();
    console.log('add states:', JSON.stringify({ before: [beforeAdd.chains, beforeAdd.elevations], same: addedSame.status,
                                                afterDelete: afterDelete.elevations, back: addedBack }));
    if (addedSame.elevations !== beforeAdd.elevations || addedSame.chains !== beforeAdd.chains || !/already in the job/.test(addedSame.status)
        || /renamed/.test(addedSame.status)        // nothing was added, so nothing was renamed
        || afterDelete.elevations !== beforeAdd.elevations - 1 || addedBack.elevations !== beforeAdd.elevations
        || !addedBack.unique || !addedBack.chainNames || !addedBack.resultNames || !/drawing\.xml: not loaded/.test(addedBack.status))
        throw new Error('adding states did not merge as it should: ' + JSON.stringify({ beforeAdd, addedSame, afterDelete, addedBack }));
    // A state whose faces are nearly the job's but not the same (the same chain, saved again after
    // its wall changed): left out, with a warning that says which of the two is the later save.
    const nudged = stateText.replace(/(<saved[^>]*>)[^<]*(<\/saved>)/, '$12001-01-01T00:00:00.000Z$2');
    const widened = await page.evaluate(() => state.elevations.map(e => e.result.width));
    await page.evaluate(() => { for (const e of state.elevations) e.result.width += 40; });
    await page.setInputFiles('#state-add', { name: 'older.state.xml', mimeType: 'application/xml', buffer: Buffer.from(nudged) });
    await page.waitForFunction(() => /^Added 1 of 1 state file/.test(document.getElementById('status-chip').textContent), null, { timeout: 30000 });
    const nearAdd = await jobNow();
    await page.evaluate(w => { state.elevations.forEach((e, k) => { e.result.width = w[k]; }); }, widened);
    console.log('add a near-duplicate:', nearAdd.status.slice(0, 420));
    if (nearAdd.elevations !== addedBack.elevations || nearAdd.chains !== addedBack.chains || !/WARNING: \d+ face\(s\) left out because the job has nearly the same surfaces/.test(nearAdd.status)
        || !/Use the latest file/.test(nearAdd.status) || !/is the later save/.test(nearAdd.status))
        throw new Error('a near-duplicate state was not warned about and left out: ' + JSON.stringify(nearAdd));
    // back to the job as it was saved, for the steps that follow
    await page.setInputFiles('#state-file', statePath);
    await page.waitForFunction(() => /^Loaded /.test(document.getElementById('status-chip').textContent), null, { timeout: 30000 });
    await settle();

    // Hide elements (H): a click hides the element under it, and clicks then pass through
    // it; a whole type can be hidden; Show all brings everything back. Picking stays off.
    await page.keyboard.press('Escape');
    await page.evaluate(() => document.activeElement && document.activeElement.blur());
    await page.keyboard.press('h');
    const hideBefore = await page.evaluate(() => ({ mode: state.mode, elevations: state.elevations.length }));
    await lookAt('Wing south wall', [0, 0.3, 1]);
    const hid = await page.evaluate(() => {
        const r = renderer.domElement.getBoundingClientRect();
        const hit = pickAt({ clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 });
        return { hidden: hiddenElements().map(m => m.name), elevations: state.elevations.length,
                 through: hit ? meshMeta[hit.mesh.userData.index].name : null, count: document.getElementById('hidden-count').textContent };
    });
    const slabs = await page.evaluate(() => meshMeta.filter(m => /slab/i.test(m.type)).length);
    const slabType = await page.evaluate(() => (meshMeta.find(m => /slab/i.test(m.type)) || {}).type);
    await page.selectOption('#hide-type', slabType);
    const byType = await page.evaluate(() => ({ hidden: hiddenElements().length, slabsHidden: hiddenElements().filter(m => /slab/i.test(m.type)).length }));
    await page.click('#show-all');
    const shown = await page.evaluate(() => hiddenElements().length);
    await page.keyboard.press('h');
    const modeBack = await page.evaluate(() => state.mode);
    console.log('hide elements:', JSON.stringify({ hideBefore, hid, slabs, byType, shown, modeBack }));
    if (hideBefore.mode !== 'hide' || hid.hidden.join() !== 'Wing south wall' || hid.elevations !== hideBefore.elevations
        || hid.through === 'Wing south wall' || hid.count !== '1 hidden' || !slabs || byType.slabsHidden !== slabs
        || byType.hidden !== slabs + 1 || shown !== 0 || modeBack !== 'pick')
        throw new Error('hiding elements wrong: ' + JSON.stringify({ hideBefore, hid, slabs, byType, shown, modeBack }));

    // Cutting run: the cutting plan played board by board beside the model. Every built
    // chain in panels, so the run crosses elevations, chains and corners.
    await page.evaluate(async () => {
        if (toggleValue('cladding-type') !== 'panel') { selectToggle('cladding-type', 'panel'); onTypeChange(); }
        await updatePreview();
    });
    await page.waitForFunction(() => window._lastPlan && document.getElementById('run-open').style.display !== 'none', null, { timeout: 30000 });
    await page.click('#run-open');
    await page.waitForFunction(() => inCuttingRun() && run.i >= 0, null, { timeout: 120000 });
    await page.evaluate(() => { runPlay(false); runGo(0); });
    await page.waitForTimeout(1200);
    await page.screenshot({ path: path.join(__dirname, 'smoke_run.png') });
    const runOpen = await page.evaluate(() => {
        const plan = run.plan, view = document.getElementById('viewport').getBoundingClientRect(), pane = document.getElementById('run-pane').getBoundingClientRect();
        const names = run.steps.filter(s => s.piece).map(s => s.piece.name);
        return { boards: plan.n_boards, figures: document.querySelectorAll('#run-strip .run-board:not(.run-rack):not(.run-reuse)').length,
                 racks: document.querySelectorAll('#run-strip .run-rack').length, rackSteps: run.steps.filter(s => s.kind === 'rack').length,
                 planRacks: plan.racks.filter(r => r.offcuts.length).length,
                 rows: [...document.querySelectorAll('#run-strip .run-row h3')].map(h => h.firstChild.textContent.trim()),
                 planRows: plan.rows.map(r => r.elevation),
                 oneElevationPerBoard: plan.boards.every(b => b.pieces.every(q => q.name.startsWith(b.elevation + ' '))),
                 steps: names.length, pieces: plan.n_pieces, onWall: names.filter(n => run.meshes[n]).length,
                 whole: plan.whole_job_boards, view: Math.round(view.width), pane: Math.round(pane.width),
                 sidePanel: getComputedStyle(document.getElementById('panel')).display,
                 counter: document.getElementById('run-board').textContent + ' ' + document.getElementById('run-of').textContent,
                 labels: run.group.children.length, summary: document.getElementById('run-summary').textContent };
    });
    console.log('cutting run:', JSON.stringify(runOpen));
    if (runOpen.figures !== runOpen.boards || runOpen.rows.join() !== runOpen.planRows.join() || runOpen.planRows.length < 2
        || !runOpen.oneElevationPerBoard || runOpen.steps !== runOpen.pieces || runOpen.onWall !== runOpen.steps
        || runOpen.sidePanel !== 'none' || Math.abs(runOpen.view - runOpen.pane) > 40 || runOpen.counter !== `Board 1 of ${runOpen.boards}`
        || runOpen.labels !== runOpen.steps || runOpen.boards < runOpen.whole || !/^\d+ boards of 1250 × 2500 · waste /.test(runOpen.summary)
        || runOpen.racks !== runOpen.planRacks || runOpen.rackSteps !== runOpen.racks)
        throw new Error('cutting run did not open right: ' + JSON.stringify(runOpen));
    // The last piece: the same piece glows on its board and on the wall, in the board's
    // hue and no other board's, the strip has scrolled to it and the camera looks at it.
    const linked = await page.evaluate(async () => {
        const i = run.steps.map(t => !!t.piece).lastIndexOf(true);         // the last piece (a rack step follows it)
        runGo(i);
        await new Promise(r => setTimeout(r, 1500));
        const s = run.steps[i], mesh = run.meshes[s.piece.name];
        const focus = document.querySelectorAll('#run-strip .run-piece.focus'), fig = document.querySelector('#run-strip .run-board.focus');
        const want = runColor(s.hue, FOCUS[0], FOCUS[1]).getHex();
        const others = run.steps.filter(t => t.piece && t.key !== s.key && run.meshes[t.piece.name]).map(t => run.meshes[t.piece.name].material.color.getHex());
        const c = mesh.geometry.boundingBox.getCenter(new THREE.Vector3()).applyMatrix4(mesh.matrix), p = c.clone().project(camera);
        // every piece cut from the board in focus is on screen at once
        const seen = run.steps.filter(t => t.key === s.key).map(t => run.meshes[t.piece.name].geometry.boundingBox
            .getCenter(new THREE.Vector3()).applyMatrix4(run.meshes[t.piece.name].matrix).project(camera))
            .every(q => Math.abs(q.x) < 1 && Math.abs(q.y) < 1 && q.z < 1);
        const a = fig.getBoundingClientRect(), b = document.getElementById('run-strip').getBoundingClientRect();
        const newest = document.querySelector('#run-strip .run-piece.newest');
        return { focus: focus.length, newest: newest && +newest.dataset.step, i, j: s.j, boardK: fig && fig.dataset.key, k: s.key,
                 wall: mesh.material.color.getHex() === want, emissive: mesh.material.emissive.getHex() !== 0, othersDiffer: others.every(h => h !== want),
                 glow: run.glow ? run.glow.children.length : 0, seen,
                 onScreen: Math.abs(p.x) < 1 && Math.abs(p.y) < 1 && p.z < 1, stripShows: a.left >= b.left - 1 && a.right <= b.right + 1,
                 counter: document.getElementById('run-board').textContent, no: s.board.no, where: document.getElementById('run-where').textContent,
                 elevation: s.board.elevation, name: s.piece.name };
    });
    console.log('cutting run focus:', JSON.stringify(linked));
    // Every board looked at so far was framed from a clear direction where there is one:
    // the sample house's balcony and roofs stand in front of parts of the walls.
    const views = await page.evaluate(() => Object.values(run.views).map(v => [v.blocked, v.tried]));
    console.log('cutting run views (blocked sight lines, directions tried):', JSON.stringify(views));
    if (!views.length || views.some(([b, t]) => b > 0 && t < 33))
        throw new Error('a board was framed blocked without trying every direction: ' + JSON.stringify(views));
    if (linked.focus !== linked.j + 1 || linked.newest !== linked.i || linked.boardK !== linked.k || !linked.wall || !linked.emissive || !linked.othersDiffer
        || linked.glow !== linked.j + 1 || !linked.seen || !linked.onScreen || !linked.stripShows || linked.counter !== `Board ${linked.no}`
        || linked.where !== `${linked.elevation} · ${linked.name}`)
        throw new Error('cutting run focus not linked: ' + JSON.stringify(linked));
    // Boards already inspected are cross-hatched in their own colour: at the last piece,
    // exactly the pieces of the boards before its own; back at the start, none.
    const hatched = await page.evaluate(() => {
        const i = run.steps.map(t => !!t.piece).lastIndexOf(true), key = run.steps[i].key;
        runGo(i);
        const first = run.steps.findIndex(t => t.key === key);
        const want = new Set(run.steps.slice(0, first).filter(t => t.piece).map(t => t.piece.name));
        const shown = Object.entries(run.hatch.skins).filter(([, m]) => m.visible).map(([n]) => n);
        const ok = shown.length === want.size && shown.every(n => want.has(n));
        runGo(0);
        return { ok, n: shown.length, none: Object.values(run.hatch.skins).every(m => !m.visible) };
    });
    console.log('cutting run hatch:', JSON.stringify(hatched));
    if (!hatched.ok || !hatched.n || !hatched.none) throw new Error('inspected boards not hatched right: ' + JSON.stringify(hatched));
    // Each elevation ends with its rack: the offcuts gathered on the strip and laid out on
    // the ground in front of the cladding, all in focus, and the camera on them.
    const racked = await page.evaluate(async () => {
        const i = run.steps.findIndex(t => t.kind === 'rack');
        runGo(i);
        await new Promise(r => setTimeout(r, 1500));
        const s = run.steps[i], want = run.plan.racks[s.rack].offcuts.length;
        const items = run.rack ? run.rack.items : [];
        const onScreen = items.every(it => { const p = it.mesh.geometry.boundingBox.getCenter(new THREE.Vector3()).applyMatrix4(it.mesh.matrix).project(camera);
                                             return Math.abs(p.x) < 1 && Math.abs(p.y) < 1 && p.z < 1; });
        return { want, items: items.length, onStrip: document.querySelectorAll('#run-strip .run-rack.focus .run-rack-item.focus').length,
                 counter: document.getElementById('run-board').textContent, onScreen };
    });
    console.log('cutting run rack:', JSON.stringify(racked));
    if (!racked.want || racked.items !== racked.want || racked.onStrip !== racked.want || racked.counter !== 'Offcuts' || !racked.onScreen)
        throw new Error('cutting run rack wrong: ' + JSON.stringify(racked));
    // Playing moves on by itself; the keys step, pause and close, and nothing else in the
    // app sees them (E would go flat, Enter would open the wizard).
    const played = await page.evaluate(async () => {
        runSpeed(4); runGo(0); runPlay(true);
        await new Promise(r => setTimeout(r, 1300));
        const moved = run.i;
        runPlay(false);
        return { moved };
    });
    await page.evaluate(() => document.activeElement && document.activeElement.blur());
    const iBefore = await page.evaluate(() => run.i);
    await page.keyboard.press('ArrowRight');
    await page.keyboard.press('ArrowRight');
    await page.keyboard.press('ArrowLeft');
    await page.keyboard.press('e');
    await page.keyboard.press('Enter');
    await page.keyboard.press(' ');
    const keys = await page.evaluate(() => ({ i: run.i, playing: run.playing, flat: in2D(), wizard: document.getElementById('chain-wizard').classList.contains('open') }));
    await page.keyboard.press(' ');
    await page.keyboard.press('Escape');
    await page.waitForTimeout(900);
    const closed = await page.evaluate(() => {
        const meshes = []; for (const l of cladGroup.children) for (const o of l.children) if (o.isMesh && o.userData.runColor !== undefined) meshes.push(o);
        return { on: inCuttingRun(), pane: document.getElementById('run-pane').hidden, sidePanel: getComputedStyle(document.getElementById('panel')).display,
                 restored: meshes.every(o => o.material.color.getHex() === o.userData.runColor && o.material.emissive.getHex() === o.userData.runEmissive),
                 glow: !!run.glow, labels: !!run.group, view: Math.round(document.getElementById('viewport').getBoundingClientRect().width) };
    });
    console.log('cutting run playback:', JSON.stringify({ played, iBefore, keys, closed }));
    if (played.moved < 2 || keys.i !== iBefore + 1 || !keys.playing || keys.flat || keys.wizard || closed.on || !closed.pane
        || closed.sidePanel === 'none' || !closed.restored || closed.glow || closed.labels || closed.view < 1000)
        throw new Error('cutting run playback wrong: ' + JSON.stringify({ played, iBefore, keys, closed }));

    // Planks: the same run over stock lengths, a 1200 shortest piece with some splicing,
    // and Profile as the Marley-type corner profile wherever a chain turns a right angle,
    // and on the jambs and head of A's first window: offered there with planks too.
    const planks = await page.evaluate(async () => {
        // A's chain has the windows: work on it (each chain keeps its own settings), planks
        const e0 = state.elevations.find(m => openingsOf(m).length) || state.elevations[0], key = openingsOf(e0)[0].key;
        setActive(state.elevations.indexOf(e0));
        setOpeningDetail(e0, key, 'jamb', 'profile', 'face', 'one');
        setOpeningDetail(e0, key, 'head', 'profile', 'face', 'one');
        selectToggle('cladding-type', 'plank'); onTypeChange();
        selectToggle('corner-type', 'profile');
        const offered = !/value="profile"[^>]*disabled/.test(
            cornerOptions('', !(toggleValue('cladding-type') !== 'panel' && toggleValue('plank-profile') === 'seam'), false));
        document.getElementById('plank_min').value = 1200;
        document.getElementById('plank_splice').value = 30;
        await updatePreview();
        // the chain's plan follows the rebuild: wait for the planks' one
        for (let i = 0; i < 60 && !(window._lastPlan && window._lastPlan.kind === 'plank'); i++)
            await new Promise(r => setTimeout(r, 500));
        await settled();
        const geo = window._lastPreview.geometry, plan = window._lastPlan;
        const short = geo.filter(m => m.ifc_type === 'plank').map(m => {
            const us = m.profile.map(q => q[0]), vs = m.profile.map(q => q[1]);
            return Math.max(Math.max(...us) - Math.min(...us), Math.max(...vs) - Math.min(...vs));
        }).filter(L => L < 1199).length;
        return { kind: plan && plan.kind, board: plan && plan.board, label: document.getElementById('wr-boards-label').textContent,
                 trims: geo.filter(m => m.ifc_type === 'corner_trim').length, profiles: geo.filter(m => m.ifc_type === 'corner_profile').length,
                 reveal: geo.filter(m => m.ifc_type === 'corner_trim' && / (Jamb|Head) Profile 1/.test(m.name) && m.elevation === e0.result.name).length,
                 offered,
                 short, piece: document.getElementById('plank_splice_val').textContent,
                 trimLabel: document.getElementById('trim-label').textContent };
    });
    await page.click('#run-open');
    await page.waitForFunction(() => inCuttingRun() && run.i >= 0 && run.plan.kind === 'plank', null, { timeout: 120000 });
    const plankRun = await page.evaluate(() => {
        runPlay(false); runGo(0);
        const r = { counter: document.getElementById('run-board').textContent + ' ' + document.getElementById('run-of').textContent,
                    steps: run.steps.filter(s => s.piece).length, onWall: run.steps.filter(s => s.piece && run.meshes[s.piece.name]).length,
                    figures: document.querySelectorAll('#run-strip .run-board:not(.run-rack):not(.run-reuse)').length, boards: run.plan.n_boards,
                    summary: document.getElementById('run-summary').textContent };
        closeCuttingRun();
        return r;
    });
    console.log('planks:', JSON.stringify({ planks, plankRun }));
    if (planks.kind !== 'plank' || planks.label !== 'Lengths' || planks.board[0] !== 3600 || !planks.trims || planks.profiles
        || planks.reveal !== 3 || !planks.offered
        || !/^\d+ mm$/.test(planks.piece) || !/^End trim/.test(planks.trimLabel)
        || !/^Length 1 of \d+$/.test(plankRun.counter) || plankRun.onWall !== plankRun.steps || plankRun.figures !== plankRun.boards
        || !/lengths of 3600/.test(plankRun.summary))
        throw new Error('plank cutting list or trim wrong: ' + JSON.stringify({ planks, plankRun }));
    // A custom profile: Cedral Click from the library, every plank drawn with its section
    // (one body each); dragging the slider draws plain boards; a DXF of two copies imports.
    const custom = await page.evaluate(async () => {
        selectToggle('plank-profile', 'custom'); onTypeChange();
        await updatePreview();
        await new Promise(r => setTimeout(r, 1500));
        // the active chain's planks: each chain keeps its own settings
        const mine = new Set(activeChain().members.map(m => m.result && m.result.name));
        const geo = window._lastPreview.geometry.filter(m => m.ifc_type === 'plank' && mine.has(m.elevation));
        const r = { planks: geo.length, withParts: geo.filter(m => m.parts && m.parts.length === 3).length,
                    cover: document.getElementById('d-cover').textContent, info: document.getElementById('custom-profile-info').textContent,
                    shown: document.getElementById('custom-fields').style.display, boardFields: document.getElementById('board-fields').style.display };
        state.sliderDragging = true; await updatePreview();
        r.dragParts = window._lastPreview.geometry.filter(m => m.parts).length;
        state.sliderDragging = false;
        const sq = [[0, 0], [140, 0], [140, 18], [0, 18]], dxf = ['0', 'SECTION', '2', 'ENTITIES'];
        for (const dx of [0, 130]) { dxf.push('0', 'LWPOLYLINE', '8', '0', '90', '4', '70', '1'); for (const [x, y] of sq) dxf.push('10', String(x + dx), '20', String(y)); }
        dxf.push('0', 'ENDSEC', '0', 'EOF');
        await importProfile(new File([dxf.join('\n')], 'Shiplap.dxf'));
        await new Promise(r => setTimeout(r, 2500));
        r.imported = val('custom_profile'); r.importedInfo = document.getElementById('custom-profile-info').textContent;
        r.importedParts = window._lastPreview.geometry.filter(m => m.ifc_type === 'plank' && mine.has(m.elevation) && m.parts && m.parts.length === 1).length;
        r.saved = snapshotState().settings.custom_section && snapshotState().settings.custom_section.cover;
        selectToggle('plank-profile', 'board'); onTypeChange();
        await updatePreview();
        await new Promise(r => setTimeout(r, 1500));
        return r;
    });
    console.log('custom profile:', JSON.stringify(custom));
    if (!custom.planks || custom.withParts < custom.planks / 2 || custom.cover !== '172.1 mm' || !/Cedral Click: 186\.0 wide, 12\.0 thick/.test(custom.info)
        || custom.shown !== '' || custom.boardFields !== 'none' || custom.dragParts || custom.imported !== 'dxf:Shiplap'
        || !/130\.0 centres/.test(custom.importedInfo) || !custom.importedParts || custom.saved !== 130)
        throw new Error('custom plank profile wrong: ' + JSON.stringify(custom));
    // Offcuts passed on: never more lengths than without, and what each elevation cuts
    // from them is played from the offcut, which glows on the rack carried round to it.
    const shared = await page.evaluate(async () => {
        const before = window._lastPlan.n_boards;
        document.getElementById('share_offcuts').checked = true;
        await updatePreview();
        await settled();
        const plan = window._lastPlan;
        return { before, after: plan.n_boards, share: plan.share_offcuts, from: plan.from_offcuts,
                 lower: document.getElementById('wr-lower').textContent };
    });
    await page.click('#run-open');
    await page.waitForFunction(() => inCuttingRun() && run.i >= 0 && run.plan.share_offcuts, null, { timeout: 120000 });
    const sharedRun = await page.evaluate(async () => {
        runPlay(false);
        const i = run.steps.findIndex(t => t.oid !== undefined);
        if (i < 0) { closeCuttingRun(); return { reused: 0 }; }
        runGo(i);
        const flying = !!document.querySelector('#run-strip .run-ghost') && !!document.querySelector('#run-strip .run-reuse.arriving');
        await new Promise(r => setTimeout(r, 1500));
        const landed = !document.querySelector('#run-strip .run-ghost') && !document.querySelector('#run-strip .run-reuse.arriving');
        const s = run.steps[i], lit = run.rack && run.rack.items.filter(it => it.oid === s.oid);
        const r = { reused: run.steps.filter(t => t.oid !== undefined).length, counter: document.getElementById('run-board').textContent,
                    rackLit: lit ? lit.length : 0, stripLit: document.querySelectorAll(`#run-strip .run-rack-item.focus[data-oid="${s.oid}"]`).length,
                    figure: !!document.querySelector('#run-strip .run-reuse.focus'), wall: !!run.meshes[s.piece.name], flying, landed };
        closeCuttingRun();
        return r;
    });
    console.log('offcuts passed on:', JSON.stringify({ shared, sharedRun }));
    if (!shared.share || shared.after > shared.before
        || (sharedRun.reused && (!/^Offcut of length \d+$/.test(sharedRun.counter) || sharedRun.rackLit !== 1 || sharedRun.stripLit !== 1
                                 || !sharedRun.figure || !sharedRun.wall || !sharedRun.flying || !sharedRun.landed)))
        throw new Error('offcuts passed on wrong: ' + JSON.stringify({ shared, sharedRun }));
    await page.evaluate(async () => { document.getElementById('share_offcuts').checked = false; await updatePreview(); });
    // Optimise splices: every Splices step tried, the current one shown, Apply moves the slider.
    const splices = await page.evaluate(async () => {
        await optimiseSplices();
        const o = window._spliceOptions;
        return { tried: o.ranked.length, offers: o.offers.map(t => t.splice), now: o.current,
                 text: document.getElementById('wr-splices').innerText.replace(/\n/g, ' | ') };
    });
    console.log('optimise splices:', JSON.stringify(splices));
    if (splices.tried < 11 || !/^Now 30% · \d+ mm: \d+ lengths/.test(splices.text))
        throw new Error('optimise splices wrong: ' + JSON.stringify(splices));
    if (splices.offers.length) {
        await page.click('#wr-splices button');
        await page.waitForFunction(s => +document.getElementById('plank_splice').value === s, splices.offers[0], { timeout: 10000 });
        await page.waitForFunction(() => document.getElementById('wr-splices').innerHTML === '', null, { timeout: 30000 });
    }
    // The floor grid toggles from the legend and with G.
    await page.evaluate(() => document.activeElement && document.activeElement.blur());
    await page.keyboard.press('g');
    const gridOff = await page.evaluate(() => [gridHelper.visible, document.getElementById('layer-grid').checked]);
    await page.keyboard.press('g');
    const gridOn = await page.evaluate(() => [gridHelper.visible, document.getElementById('layer-grid').checked]);
    console.log('grid:', JSON.stringify({ gridOff, gridOn }));
    if (gridOff.join() !== 'false,false' || gridOn.join() !== 'true,true') throw new Error('grid toggle wrong: ' + JSON.stringify({ gridOff, gridOn }));

    // A roof slope is picked like a wall face: it keeps its own plane, stands in a chain of
    // its own, and trays built on it run up the slope with the pitch checked.
    await page.evaluate(() => { if (in2D()) exit2D(); setPickMode('pick'); });
    const facesBefore = await page.evaluate(() => state.elevations.length);
    await lookAt('Wing roof south', [0.2, 0.85, 0.6]);
    await page.waitForFunction(n => state.elevations.length > n && state.elevations.every(e => !e._running && (e.result || e.error)),
                               facesBefore, { timeout: 120000 });
    await page.evaluate(() => { openWizard(); WIZ.draft.type = 'seam'; WIZ.draft.orient = 'vertical'; wizBuild(); dismissLevels(); });
    await settle();
    const slope = await page.evaluate(() => {
        const e = state.elevations[state.elevations.length - 1], r = e.result || {};
        const own = cladGroup.children.flatMap(l => l.children).filter(m => m.isMesh && m.userData.elevation === e.name);
        const up = new THREE.Vector3().setFromMatrixColumn((own[0] || { matrix: new THREE.Matrix4() }).matrix, 2);
        return { error: e.error, pitch: r.pitch, size: [Math.round(r.width), Math.round(r.height)], rise: r.frame && r.frame.v[2],
                 alone: e.chain.members.length, card: document.querySelectorAll('.elev-meta')[state.elevations.length - 1].textContent,
                 parts: own.length, tilt: Math.round(up.y * 1000) / 1000,
                 check: [...document.querySelectorAll('#checks-container > div')].map(d => d.textContent).filter(t => /pitched/.test(t)) };
    });
    console.log('roof slope:', JSON.stringify(slope));
    if (slope.error || Math.abs(slope.pitch - 31.4) > 0.3 || slope.size.join() !== '3400,2110' || Math.abs(slope.rise - 0.521) > 0.005
        || slope.alone !== 1 || !/pitched 31\.4°/.test(slope.card) || !slope.parts || Math.abs(slope.tilt - 0.853) > 0.005
        || !slope.check.some(t => /run up the slope/.test(t)))
        throw new Error('roof slope not picked and built on its own plane: ' + JSON.stringify(slope));
    // The slope the other side of the ridge joins the first: one chain, set out from one
    // line so the seams carry over the ridge, with a ridge cap and fillers under it.
    await lookAt('Wing roof north', [0.2, 0.85, -0.6]);
    await page.waitForFunction(n => state.elevations.length > n + 1 && state.elevations.every(e => !e._running && (e.result || e.error)),
                               facesBefore, { timeout: 120000 });
    await settle();
    const ridge = await page.evaluate(() => {
        const [a, b] = state.elevations.slice(-2);
        const parts = cladGroup.children.flatMap(l => l.children).filter(m => m.isMesh);
        const named = (e, re) => parts.filter(m => m.userData.elevation === e.name && re.test(m.userData.name || ''));
        // where each slope's seams stand along the ridge, in the model's own x (the ridge runs along it)
        const seams = e => [...new Set(named(e, /Seam overcloak$/).map(m => Math.round(new THREE.Box3().setFromObject(m).getCenter(new THREE.Vector3()).x / 20)))].sort((p, q) => p - q).map(x => x * 20);
        return { error: a.error || b.error, chain: a.chain === b.chain, members: a.chain.members.length, turned: a.rev !== b.rev,
                 status: document.getElementById('status-chip').textContent,
                 cards: [...document.querySelectorAll('.elev-meta')].slice(-2).map(c => c.textContent.replace(/^.*openings?/, '')),
                 caps: named(a, /Ridge Cap/).length + named(b, /Ridge Cap/).length,
                 fillers: [named(a, /Ridge Filler/).length, named(b, /Ridge Filler/).length],
                 upstands: named(a, /Ridge Upstand/).length, verges: named(a, /Verge Support [LR]$/).length + named(b, /Verge Support [LR]$/).length,
                 // Verge type Auto (the default): an end standing over a picked, clad wall (the wing's east wall)
                 // takes the verge with cladding (ZI-05), its flashing in place of the G3 strip
                 flashings: named(a, /Verge Flashing [LR]$/).length + named(b, /Verge Flashing [LR]$/).length,
                 seams: [seams(a), seams(b)],
                 check: [...document.querySelectorAll('#checks-container > div')].map(d => d.textContent).filter(t => /meet at a ridge/.test(t)) };
    });
    console.log('ridge:', JSON.stringify(ridge));
    if (ridge.error || !ridge.chain || ridge.members !== 2 || !ridge.turned || !/at a ridge with/.test(ridge.status)
        || !/ridge with/.test(ridge.cards[0]) || !/carries the cap/.test(ridge.cards.join()) || ridge.caps !== 1
        || ridge.fillers[0] < 4 || ridge.fillers[0] !== ridge.fillers[1] || ridge.seams[0].length < 4
        || ridge.seams[0].length !== ridge.seams[1].length || ridge.seams[0].some((x, k) => Math.abs(x - ridge.seams[1][k]) > 20)
        || ridge.check.length !== 1 || ridge.upstands < 4 || ridge.verges + ridge.flashings !== 4 || ridge.flashings > 2)
        throw new Error('the two roof slopes did not join at a ridge: ' + JSON.stringify(ridge));
    // The verge with cladding (VMZ-SN-MA-ZI-05), the chain's Verge type: no strip and no tray
    // welted round a nose, but a seam at the roof's edge and a flashing down over the wall's
    // cladding on a continuous strip. Back to the G3 strip afterwards.
    await page.selectOption('#edge-vergeType', 'cladding');
    await settle();
    const zi05 = await page.evaluate(() => {
        const names = cladGroup.children.flatMap(l => l.children).filter(m => m.isMesh).map(m => m.userData.name || '');
        return { strips: names.filter(n => /Verge Support/.test(n)).length, flashings: names.filter(n => /Verge Flashing [LR]$/.test(n)).length,
                 clips: names.filter(n => /Verge Clip [LR]$/.test(n)).length, seams: names.filter(n => /Verge Seam/.test(n)).length, type: state.elevations[state.elevations.length - 1].chain.edges.vergeType,
                 check: [...document.querySelectorAll('#checks-container > div')].map(d => d.textContent).filter(t => /verge flashing over the cladding/.test(t)).length };
    });
    console.log('verge with cladding:', JSON.stringify(zi05));
    // each of the four verges is a true double-lock seam: eight sheets, as between two trays
    if (zi05.strips || zi05.flashings !== 4 || zi05.clips !== 4 || zi05.seams !== 32 || zi05.type !== 'cladding' || zi05.check < 1)
        throw new Error('the verge with cladding was not built: ' + JSON.stringify(zi05));
    await page.selectOption('#edge-vergeType', 'g3');
    await settle();
    const g3Again = await page.evaluate(() => cladGroup.children.flatMap(l => l.children).filter(m => m.isMesh && /Verge Support [LR]$/.test(m.userData.name || '')).length);
    if (g3Again !== 4) throw new Error('the G3 verge strips did not come back: ' + g3Again);
    await page.evaluate(async () => { for (let k = 0; k < 2; k++) { setActive(state.elevations.length - 1); deleteElevation(); await settled(); } });

    await page.evaluate(() => hideElement(allMeshes[0]));           // a new model starts with none hidden

    // The server importer, the fallback for models web-ifc cannot build.
    await page.evaluate(() => loadModel(state.file, 'server'));
    await page.waitForFunction(() => state.model && state.model.reader.indexOf('server') >= 0, null, { timeout: 180000 });
    console.log('camera moved on a pick:', cameraMoves.length ? cameraMoves : 'never');
    console.log('server import:', await page.evaluate(() => `${state.model.meshes} elements, ${state.model.storeys} storeys, reader ${state.model.reader}`));
    const hiddenAfterImport = await page.evaluate(() => hiddenElements().length + ' / ' + document.getElementById('hidden-count').textContent);
    console.log('hidden after re-import:', hiddenAfterImport);
    if (hiddenAfterImport !== '0 / None hidden') throw new Error('hidden elements survived a new model: ' + hiddenAfterImport);

    // A second model: two roof slopes falling towards each other. They stay in their own
    // chains, are joined by the valley they share, and one of them carries its gutter.
    await page.setInputFiles('#ifc-file', path.join(__dirname, 'valley_roof.ifc'));
    await page.waitForFunction(() => state.model && state.model.meshes === 2 && allMeshes.length === 2
                               && state.elevations.length === 1 && !state.elevations[0].picks.length
                               && document.getElementById('status-chip').classList.contains('ready'), null, { timeout: 120000 });
    await page.evaluate(() => setPickMode('pick'));
    const seamRoof = () => page.evaluate(() => { openWizard(); WIZ.draft.type = 'seam'; WIZ.draft.orient = 'vertical'; wizBuild(); dismissLevels(); });
    const picked = n => page.waitForFunction(n => state.elevations.length === n && state.elevations.every(e => !e._running && e.picks.length && (e.result || e.error)),
                                             n, { timeout: 120000 });
    await lookAt('Roof A', [0.05, 0.87, 0.5]);
    await picked(1);
    await seamRoof();
    await settle();
    await lookAt('Roof C', [-0.5, 0.87, 0.05]);
    await picked(2);
    const valleyStatus = await page.evaluate(() => document.getElementById('status-chip').textContent);
    await seamRoof();
    await settle();
    const valley = await page.evaluate(() => {
        const [a, c] = state.elevations;
        const parts = cladGroup.children.flatMap(l => l.children).filter(m => m.isMesh);
        return { error: a.error || c.error, chains: a.chain !== c.chain, pitch: [a.result.pitch, c.result.pitch],
                 records: [valleyRecords(a), valleyRecords(c)].map(v => v.map(x => [x.with, x.master, Math.round(x.length)])),
                 cards: [...document.querySelectorAll('.elev-meta')].map(x => x.textContent.replace(/^.*openings?/, '')),
                 gutters: parts.filter(m => /Valley Gutter/.test(m.userData.name || '')).map(m => m.userData.name),
                 trays: parts.filter(m => / Tray /.test(m.userData.name || '') && !/Seam|Upstand/.test(m.userData.name)).length,
                 check: [...document.querySelectorAll('#checks-container > div')].map(d => d.textContent).filter(t => /meet at a valley/.test(t)) };
    });
    console.log('valley:', JSON.stringify(valley), '|', valleyStatus);
    if (valley.error || !valley.chains || valley.records[0].join() !== 'Elevation B,true,4583' || valley.records[1].join() !== 'Elevation A,false,4583'
        || !/a valley with Elevation A/.test(valleyStatus) || !/valley with B/.test(valley.cards[0]) || !/valley with A/.test(valley.cards[1])
        || valley.gutters.join() !== 'Elevation A Valley Gutter' || valley.trays < 8 || valley.check.length !== 1 || !/200 mm clear/.test(valley.check[0]))
        throw new Error('the two roof slopes were not joined at their valley: ' + JSON.stringify(valley));

    // A third model: a hipped corner, two roof slopes falling away from each other. They too
    // stay in their own chains and are joined by the hip they share: cut to it under a hip
    // cap, a leg of it laid by each. Each has an eaves apron along its level bottom edge.
    await page.setInputFiles('#ifc-file', path.join(__dirname, 'hip_roof.ifc'));
    await page.waitForFunction(() => state.model && state.model.meshes === 2 && allMeshes.length === 2
                               && state.elevations.length === 1 && !state.elevations[0].picks.length
                               && document.getElementById('status-chip').classList.contains('ready'), null, { timeout: 120000 });
    await page.evaluate(() => setPickMode('pick'));
    await lookAt('Roof A', [0.05, 0.87, 0.5]);
    await picked(1);
    await seamRoof();
    await settle();
    await lookAt('Roof C', [-0.5, 0.87, 0.05]);
    await picked(2);
    const hipStatus = await page.evaluate(() => document.getElementById('status-chip').textContent);
    await seamRoof();
    await settle();
    const roofParts = () => page.evaluate(() => {
        const [a, c] = state.elevations;
        const names = cladGroup.children.flatMap(l => l.children).filter(m => m.isMesh).map(m => m.userData.name || '');
        return { error: a.error || c.error, chains: a.chain !== c.chain,
                 records: [valleyRecords(a), valleyRecords(c)].map(v => v.map(x => [x.kind, x.with, x.master, Math.round(x.length)])),
                 cards: [...document.querySelectorAll('.elev-meta')].map(x => x.textContent.replace(/^.*openings?/, '')),
                 caps: names.filter(n => /Hip Cap/.test(n)), gutters: names.filter(n => /Valley Gutter/.test(n)).length,
                 upstands: names.filter(n => /Hip Upstand/.test(n)).length, aprons: names.filter(n => /Eaves Support \d+$/.test(n)),
                 checks: [...document.querySelectorAll('#checks-container > div')].map(d => d.textContent).filter(t => /meet at a hip|: an eaves \d/.test(t)).length };
    });
    const hip = await roofParts();
    console.log('hip:', JSON.stringify(hip), '|', hipStatus);
    if (hip.error || !hip.chains || hip.records[0].join() !== 'hip,Elevation B,true,4583' || hip.records[1].join() !== 'hip,Elevation A,false,4583'
        || !/a hip with Elevation A/.test(hipStatus) || !/hip with B/.test(hip.cards[0]) || !/hip with A/.test(hip.cards[1])
        || hip.caps.join() !== 'Elevation A Hip Cap B,Elevation B Hip Cap A' || hip.gutters || hip.upstands < 8 || hip.aprons.length !== 2 || hip.checks !== 3)
        throw new Error('the two roof slopes were not joined at their hip: ' + JSON.stringify(hip));
    // The eaves is the chain's to switch off: the active chain's apron goes, the other's stays.
    await page.evaluate(() => { const s = document.getElementById('edge-eaves'); s.value = 'none'; s.onchange(); });
    await settle();
    const noEaves = await roofParts();
    console.log('eaves off on the active chain:', JSON.stringify(noEaves.aprons));
    if (noEaves.aprons.length !== 1 || noEaves.caps.length !== 2) throw new Error('the eaves apron did not follow its chain setting: ' + JSON.stringify(noEaves));

    // A fourth model: a whole hipped roof. The two long slopes join at a ridge only as long
    // as they share (not the length of their eaves), each hip end meets both at a hip, and
    // where the hips run into the ridge the caps are mitred: every cap is a leg on each slope.
    await page.setInputFiles('#ifc-file', path.join(__dirname, 'hipped_roof.ifc'));
    await page.waitForFunction(() => state.model && state.model.meshes === 4 && allMeshes.length === 4
                               && state.elevations.length === 1 && !state.elevations[0].picks.length
                               && document.getElementById('status-chip').classList.contains('ready'), null, { timeout: 120000 });
    await page.evaluate(() => setPickMode('pick'));
    const slopes = [['Roof South', [0.05, 0.87, 0.5]], ['Roof North', [0.05, 0.87, -0.5]], ['Roof West', [-0.5, 0.87, 0.05]], ['Roof East', [0.5, 0.87, 0.05]]];
    for (let i = 0; i < slopes.length; i++) {
        await lookAt(slopes[i][0], slopes[i][1]);
        await picked(i + 1);
        await seamRoof();
        await settle();
    }
    const hipped = await page.evaluate(() => {
        const names = cladGroup.children.flatMap(l => l.children).filter(m => m.isMesh).map(m => m.userData.name || '');
        return { chains: state.elevations.map(e => e.chain.name), ridge: state.elevations.slice(0, 2).map(e => e.ridge && [e.ridge.u0, e.ridge.u1]),
                 hips: state.elevations.map(e => valleyRecords(e).filter(v => v.kind === 'hip').length),
                 ridgeCaps: names.filter(n => /Ridge Cap/.test(n)).length, hipCaps: names.filter(n => /Hip Cap/.test(n)).length,
                 aprons: names.filter(n => /Eaves Support \d+$/.test(n)).length, verges: names.filter(n => /Verge/.test(n)).length,
                 ridgeCheck: [...document.querySelectorAll('#checks-container > div')].map(d => d.textContent).filter(t => /meet at a ridge/.test(t)).map(t => (t.match(/cap ([\d.]+) m long/) || [])[1]) };
    });
    console.log('hipped roof:', JSON.stringify(hipped));
    // The floor grid lies just below the lowest point of everything drawn, model or cladding.
    const floor = await page.evaluate(() => {
        const low = g => new THREE.Box3().setFromObject(g).min.y;
        return { grid: gridHelper.position.y, model: low(modelGroup), clad: low(cladGroup) };
    });
    console.log('grid:', JSON.stringify(floor));
    const lowest = Math.min(floor.model, floor.clad);
    if (!(floor.grid < lowest) || lowest - floor.grid > 60)
        throw new Error('the grid is not just below the lowest geometry: ' + JSON.stringify(floor));
    // and it follows the cladding down when that reaches lower than the model, and back up after
    const dipped = await page.evaluate(() => {
        const probe = new THREE.Mesh(new THREE.BoxGeometry(10, 10, 10));
        probe.position.y = gridHelper.position.y - 500;
        cladGroup.add(probe); placeGrid();
        const out = { grid: gridHelper.position.y, low: new THREE.Box3().setFromObject(probe).min.y };
        cladGroup.remove(probe); placeGrid();
        return Object.assign(out, { back: gridHelper.position.y });
    });
    console.log('grid under lower cladding:', JSON.stringify(dipped));
    if (!(dipped.grid < dipped.low) || dipped.low - dipped.grid > 60 || dipped.back !== floor.grid)
        throw new Error('the grid did not follow the cladding down: ' + JSON.stringify(dipped));
    if (grouping(hipped.chains) !== '0,0,1,2' || hipped.ridge.map(r => r.join('-')).join() !== '3000-6000,3000-6000'
        || hipped.hips.join() !== '2,2,2,2' || hipped.ridgeCaps !== 2 || hipped.hipCaps !== 8 || hipped.aprons !== 4 || hipped.verges
        || hipped.ridgeCheck.join() !== '3.00')
        throw new Error('the hipped roof was not joined at its ridge and hips: ' + JSON.stringify(hipped));

    // A fifth model: the hipped corner again, each slab modelled 150 mm short of the hip. The
    // two slopes are not joined as picked; the strips that would join them are ghosted in red
    // under a tick and a cross. The cross leaves the pair alone for good; the tick extends
    // both faces to the hip line, and they are joined at a hip.
    await page.setInputFiles('#ifc-file', path.join(__dirname, 'hip_short_roof.ifc'));
    await page.waitForFunction(() => state.model && state.model.meshes === 2 && allMeshes.length === 2
                               && state.elevations.length === 1 && !state.elevations[0].picks.length
                               && document.getElementById('status-chip').classList.contains('ready'), null, { timeout: 120000 });
    await page.evaluate(() => setPickMode('pick'));
    await lookAt('Roof A', [0.05, 0.87, 0.5]);
    await picked(1);
    await seamRoof();
    await settle();
    await lookAt('Roof C', [-0.5, 0.87, 0.05]);
    await picked(2);
    await page.waitForFunction(() => (state.heals || []).length === 1, null, { timeout: 60000 });
    const healOffer = () => page.evaluate(() => ({
        offers: (state.heals || []).map(h => [h.a.name, h.b.name, h.kind, Math.round(h.gap), h.quads.a.length, h.quads.b.length].join()),
        marks: document.querySelectorAll('.heal-mark').length, ghosts: healGroup.children.length,
        hips: state.elevations.map(e => valleyRecords(e).length).join(), widths: state.elevations.map(e => e.result && e.result.width).join(),
        status: document.getElementById('status-chip').textContent }));
    const offered = await healOffer();
    console.log('heal offered:', JSON.stringify(offered));
    if (offered.offers.join() !== 'Elevation B,Elevation A,hip,113,1,1' || offered.marks !== 1 || offered.ghosts !== 1 || offered.hips !== '0,0'
        || offered.widths !== '5850,5850' || !/nearly meets Elevation A/.test(offered.status))
        throw new Error('the near miss was not offered for healing: ' + JSON.stringify(offered));
    // the cross: gone, and not offered again when the face is read again
    await page.evaluate(() => document.querySelector('.heal-mark .heal-no').click());
    await page.evaluate(() => runExtraction(state.elevations[1]));
    await page.waitForFunction(() => state.elevations.every(e => !e._running && e.result), null, { timeout: 60000 });
    const declined = await healOffer();
    if (declined.offers.length || declined.marks || declined.ghosts) throw new Error('a declined heal came back: ' + JSON.stringify(declined));
    // asked again from scratch, then the tick
    await page.evaluate(async () => { state.healNo.clear(); await offerHeals(state.elevations[1]); });
    await page.evaluate(() => document.querySelector('.heal-mark .heal-yes').click());
    await page.waitForFunction(() => state.elevations.length === 2 && state.elevations.every(e => !e._running && e.result && (e.heals || []).length === 1)
                               && valleyRecords(state.elevations[1]).length === 1, null, { timeout: 120000 });
    await seamRoof();
    await settle();
    const healed = await healOffer();
    const healedCaps = await page.evaluate(() => cladGroup.children.flatMap(l => l.children).filter(m => m.isMesh && /Hip Cap/.test(m.userData.name || '')).length);
    console.log('healed:', JSON.stringify(healed), 'hip caps', healedCaps);
    if (healed.offers.length || healed.marks || healed.hips !== '1,1' || healed.widths !== '6000,6000' || healedCaps !== 2)
        throw new Error('the heal did not join the two slopes: ' + JSON.stringify(healed));

    // Faces that run on past each other: two walls at a corner, A drawn 600 mm on past it
    // and B stopped against A's back. B can be extended up to A as before (an abutment); or,
    // the new offer, what of A is past the line is ghosted in red under its own tick and
    // cross, and the tick trims it off and brings B to the line: a corner, one chain.
    await page.setInputFiles('#ifc-file', path.join(__dirname, 'overrun_corner.ifc'));
    await page.waitForFunction(() => state.model && state.model.meshes === 2 && allMeshes.length === 2
                               && state.elevations.length === 1 && !state.elevations[0].picks.length
                               && document.getElementById('status-chip').classList.contains('ready'), null, { timeout: 120000 });
    await page.evaluate(() => setPickMode('pick'));
    await lookAt('Wall A', [0.1, 0.3, 1]);
    await picked(1);
    await page.evaluate(() => { openWizard(); WIZ.draft.type = 'panel'; wizBuild(); dismissLevels(); });
    await settle();
    await lookAt('Wall B', [1, 0.3, -0.1]);
    await picked(2);
    await page.waitForFunction(() => (state.heals || []).some(h => h.trim), null, { timeout: 60000 });
    const trimOffer = () => page.evaluate(() => ({
        offers: (state.heals || []).map(h => [h.a.name, h.b.name, h.kind, h.trim ? 'trim' : 'extend', Math.round(h.trim ? h.over : h.gap),
                                              h.cut ? h.cut.a.length : 0, h.cut ? h.cut.b.length : 0, h.quads.a.length, h.quads.b.length].join()),
        marks: document.querySelectorAll('.heal-mark').length, ghosts: healGroup.children.length,
        titles: [...document.querySelectorAll('.heal-mark .heal-yes')].map(b => b.title),
        chains: state.elevations.map(e => state.chains.indexOf(e.chain)).join(), widths: state.elevations.map(e => e.result && Math.round(e.result.width)).join(),
        trims: state.elevations.map(e => (e.trims || []).length).join(), heals: state.elevations.map(e => (e.heals || []).length).join(),
        status: document.getElementById('status-chip').textContent }));
    const over = await trimOffer();
    console.log('trim offered:', JSON.stringify(over));
    if (over.offers.join(' / ') !== 'Elevation B,Elevation A,abutment,extend,300,0,0,1,0 / Elevation B,Elevation A,corner,trim,600,0,1,1,0'
        || over.marks !== 2 || over.ghosts !== 2 || over.widths !== '4600,3000' || over.chains !== '0,1'
        || !/^Trim Elevation A back by up to 600 mm.*meet at a corner; Elevation B is extended to it$/.test(over.titles[1])
        || !/nearly meets Elevation A/.test(over.status) || !/runs past where it meets Elevation A/.test(over.status))
        throw new Error('the overshoot was not offered for trimming: ' + JSON.stringify(over));
    // the trim's cross: that offer goes, for good; the other stays
    await page.evaluate(() => document.querySelectorAll('.heal-mark .heal-no')[1].click());
    await page.evaluate(() => runExtraction(state.elevations[1]));
    await page.waitForFunction(() => state.elevations.every(e => !e._running && e.result), null, { timeout: 60000 });
    const left = await trimOffer();
    if (left.offers.length !== 1 || !/extend/.test(left.offers[0]) || left.marks !== 1) throw new Error('a declined trim came back: ' + JSON.stringify(left));
    // asked afresh, then the trim's tick: A is cut back to the corner, B brought to it, and the two are one chain
    await page.evaluate(async () => { state.healNo.clear(); await offerHeals(state.elevations[1]); });
    await page.evaluate(() => document.querySelectorAll('.heal-mark .heal-yes')[1].click());
    await page.waitForFunction(() => state.elevations.length === 2 && state.elevations.every(e => !e._running && e.result)
                               && (state.elevations[0].trims || []).length === 1 && state.elevations[0].chain === state.elevations[1].chain
                               && Math.round(state.elevations[0].result.width) === 4000, null, { timeout: 120000 });
    await settle();
    const trimDone = await trimOffer();
    console.log('trimmed:', JSON.stringify(trimDone));
    if (trimDone.offers.length || trimDone.marks || trimDone.ghosts || trimDone.widths !== '4000,3300' || trimDone.trims !== '1,0'
        || trimDone.heals !== '0,1' || trimDone.chains !== '0,0')
        throw new Error('the trim did not join the two walls at their corner: ' + JSON.stringify(trimDone));

    // A sixth model: a wall leaning back 15° and an upright wall round the corner from it. Two
    // chains, joined at a raking corner: mitred, then under a corner profile when the job's
    // corner is set to Profile.
    await page.setInputFiles('#ifc-file', path.join(__dirname, 'leaning_corner.ifc'));
    await page.waitForFunction(() => state.model && state.model.meshes === 2 && allMeshes.length === 2
                               && state.elevations.length === 1 && !state.elevations[0].picks.length
                               && document.getElementById('status-chip').classList.contains('ready'), null, { timeout: 120000 });
    await page.evaluate(() => setPickMode('pick'));
    const panelWall = () => page.evaluate(() => { openWizard(); WIZ.draft.type = 'panel'; wizBuild(); dismissLevels(); });
    await lookAt('Leaning wall', [0.05, 0.26, 0.97]);
    await picked(1);
    await panelWall();
    await settle();
    await lookAt('Upright wall', [1, 0.05, 0.05]);
    await picked(2);
    const rakeStatus = await page.evaluate(() => document.getElementById('status-chip').textContent);
    await panelWall();
    await settle();
    const raking = () => page.evaluate(() => {
        const names = cladGroup.children.flatMap(l => l.children).filter(m => m.isMesh).map(m => m.userData.name || '');
        return { chains: state.elevations.map(e => e.chain.name).join(), pitch: state.elevations.map(e => e.result.pitch).join(),
                 records: state.elevations.map(e => valleyRecords(e).map(v => [v.kind, v.with, v.master, Math.round(v.length), v.k, v.detail, v.match].join())).join(' / '),
                 cards: [...document.querySelectorAll('.elev-meta')].map(x => x.textContent.replace(/^.*openings?/, '')).join(' / '),
                 profiles: names.filter(n => /Corner Profile/.test(n)).join(),
                 check: [...document.querySelectorAll('#checks-container > div')].map(d => d.textContent).filter(t => /raking corner/.test(t)).join(' | ') };
    });
    const mitredRake = await raking();
    console.log('raking corner:', JSON.stringify(mitredRake), '|', rakeStatus);
    if (grouping(mitredRake.chains.split(',')) !== '0,1' || mitredRake.pitch !== '75,90'
        || mitredRake.records !== 'rake,Elevation B,true,3106,1,mitre,true / rake,Elevation A,false,3106,1,mitre,true'
        || !/raking corner with B/.test(mitredRake.cards) || !/a raking corner with Elevation A/.test(rakeStatus)
        || mitredRake.profiles || !/10 mm apart across the mitre/.test(mitredRake.check))
        throw new Error('the leaning wall and the upright one were not joined at a raking corner: ' + JSON.stringify(mitredRake));
    await page.click('#corner-type .turn-btn[data-value="profile"]');
    await settle();
    const profiledRake = await raking();
    console.log('raking corner, profile:', JSON.stringify(profiledRake));
    if (profiledRake.profiles !== 'Elevation A Corner Profile B' || !/corner profile run up the corner/.test(profiledRake.check))
        throw new Error('the raking corner did not take its corner profile: ' + JSON.stringify(profiledRake));

    // A seventh model: a wall, and a roof slope that comes down to its head with no overhang
    // (detail DL-41). Two chains, joined as an eaves: the roof's plywood and trays run on past
    // the wall's cladding, the zinc turns round the plywood's end and drips in front of the
    // wall's panels, and every layer of the wall stops level under the roof.
    await page.setInputFiles('#ifc-file', path.join(__dirname, 'eaves_flush.ifc'));
    await page.waitForFunction(() => state.model && state.model.meshes === 2 && allMeshes.length === 2
                               && state.elevations.length === 1 && !state.elevations[0].picks.length
                               && document.getElementById('status-chip').classList.contains('ready'), null, { timeout: 120000 });
    await page.evaluate(() => setPickMode('pick'));
    await page.click('#corner-type .turn-btn[data-value="mitre"]');
    await lookAt('Wall', [0.05, 0.1, 1]);
    await picked(1);
    await panelWall();
    await settle();
    await lookAt('Roof', [0.02, 0.87, 0.5]);
    await picked(2);
    const eavesStatus = await page.evaluate(() => document.getElementById('status-chip').textContent);
    await page.evaluate(() => { openWizard(); WIZ.draft.type = 'seam'; WIZ.draft.orient = 'vertical'; wizBuild(); dismissLevels(); });
    await settle();
    const flush = await page.evaluate(() => {
        const g = window._lastPreview.geometry, info = window._lastPreview.info;
        const tops = t => Math.round(Math.max(...g.filter(m => m.elevation === 'Elevation A' && m.ifc_type === t).flatMap(m => m.profile.map(q => q[1]))) * 10) / 10;
        const deck = g.find(m => m.elevation === 'Elevation B' && m.ifc_type === 'deck');
        return { records: state.elevations.map(e => valleyRecords(e).map(v => [v.kind, v.with, v.roof].join())).join(' / '),
                 cards: [...document.querySelectorAll('.elev-meta')].map(x => x.textContent.replace(/^.*openings?/, '')).join(' / '),
                 zinc: g.filter(m => /Eaves Drip/.test(m.name)).length, support: g.filter(m => /Eaves Support/.test(m.name)).length,
                 panelTop: tops('panel'), battenTop: tops('batten'), wallHeight: state.elevations[0].result.height,
                 deckEnd: Math.round(Math.min(...deck.profile.map(q => q[1]))),
                 under: (info.find(i => i.elevation === 'Elevation A').eaves_under || []).map(u => [u.with, u.stop, u.gap].join()).join(' / '),
                 over: (info.find(i => i.elevation === 'Elevation B').eaves || []).map(e => [e.over, e.run > 0].join()).join(' / '),
                 checks: [...document.querySelectorAll('#checks-container > div')].map(d => d.textContent).filter(t => /an eaves \d|under the eaves of/.test(t)).length };
    });
    console.log('eaves, no overhang:', JSON.stringify(flush), '|', eavesStatus);
    if (flush.records !== 'eaves,Elevation B,false / eaves,Elevation A,true' || !/eaves \(no overhang\) with B/.test(flush.cards)
        || !/an eaves with Elevation A, no overhang/.test(eavesStatus) || flush.zinc < 2 || flush.support !== 1
        || flush.panelTop !== flush.battenTop || flush.panelTop > flush.wallHeight - 9.9 || flush.deckEnd >= 0
        || flush.under !== 'Elevation B,-10,10' || flush.over !== 'Elevation A,true' || flush.checks !== 2)
        throw new Error('the roof and the wall were not joined at an eaves with no overhang: ' + JSON.stringify(flush));

    // Windows in standing seam (VMZ-SN-MA-RA-07): the sample house's south wall, clad in standing
    // seam. Its window gets a sill capping on a board, dropping over the cladding below, whose
    // top edge is turned out, and a drip strip at the head. The chain's Edges can switch them off.
    await page.setInputFiles('#ifc-file', path.join(__dirname, 'sample_house.ifc'));
    await page.waitForFunction(() => state.model && allMeshes.length > 4 && state.elevations.length === 1 && !state.elevations[0].picks.length
                               && document.getElementById('status-chip').classList.contains('ready'), null, { timeout: 120000 });
    await page.evaluate(() => setPickMode('pick'));
    await lookAt('South wall', [0, 0.35, 1]);
    await picked(1);
    await seamRoof();
    await settle();
    const windowParts = () => page.evaluate(() => {
        const names = cladGroup.children.flatMap(l => l.children).filter(m => m.isMesh).map(m => m.userData.name || '');
        const count = re => names.filter(n => re.test(n)).length;
        return { caps: count(/Sill Capping/), boards: count(/Sill Board/), strips: count(/Sill Strip/), lips: count(/Turn-out/),
                 heads: count(/Head Strip/), setting: document.getElementById('edge-windows').value,
                 check: [...document.querySelectorAll('#checks-container > div')].map(d => d.textContent).filter(t => /VMZ-SN-MA-RA-07/.test(t)).length };
    });
    const win = await windowParts();
    console.log('windows in standing seam:', JSON.stringify(win));
    // one sill, the window's; two heads, the window's and the door's (a door has a head and no sill)
    if (win.caps !== 1 || win.boards !== 1 || win.strips !== 1 || win.lips < 2 || win.heads !== 2 || win.setting !== 'on' || win.check !== 1)
        throw new Error('the window sill and head were not built: ' + JSON.stringify(win));
    await page.selectOption('#edge-windows', 'none');
    await settle();
    const winOff = await windowParts();
    if (winOff.caps || winOff.boards || winOff.lips || winOff.heads || winOff.check) throw new Error('the window sill and head did not switch off: ' + JSON.stringify(winOff));
    await page.selectOption('#edge-windows', 'on');
    await settle();
    if ((await windowParts()).caps !== 1) throw new Error('the window sill did not come back');
    // An eighth model: a monopitch roof whose top edge is the head of a wall it falls away behind
    // (VMZINC's Head verge G3). Two chains, joined at a head verge: the roof runs on over the
    // wall's build-up under a cap that turns down over the wall's cladding.
    await page.setInputFiles('#ifc-file', path.join(__dirname, 'head_mono.ifc'));
    await page.waitForFunction(() => state.model && state.model.meshes === 2 && allMeshes.length === 2
                               && state.elevations.length === 1 && !state.elevations[0].picks.length
                               && document.getElementById('status-chip').classList.contains('ready'), null, { timeout: 120000 });
    await page.evaluate(() => setPickMode('pick'));
    const seamChain2 = () => page.evaluate(() => { openWizard(); WIZ.draft.type = 'seam'; WIZ.draft.orient = 'vertical'; wizBuild(); dismissLevels(); });
    await lookAt('Wall', [0.05, 0.1, 1]);
    await picked(1);
    await seamChain2();
    await settle();
    await lookAt('Roof', [0.02, 0.96, -0.26]);
    await picked(2);
    // The wall stands under the roof slab, a slab's thickness short of the roof's top edge: the two
    // nearly meet, and the tick brings the wall up to the line. Then they are joined at a head verge.
    await page.waitForFunction(() => (state.heals || []).length === 1 && document.querySelectorAll('.heal-mark').length === 1, null, { timeout: 30000 });
    const headHeal = await page.evaluate(() => state.heals.map(h => [h.kind, Math.round(h.gap)].join()).join());
    await page.evaluate(() => document.querySelector('.heal-mark .heal-yes').click());
    await page.waitForFunction(() => state.elevations.every(e => !e._running && e.result) && !(state.heals || []).length
                                     && state.elevations.some(e => (e.valleys || []).length), null, { timeout: 60000 });
    await settle();
    const headStatus = 'healed ' + headHeal + ' · ' + await page.evaluate(() => state.elevations.map(e => valleyRecords(e).map(v => v.kind).join()).join('/'));
    await seamChain2();
    await settle();
    const headVerge = () => page.evaluate(() => {
        const g = window._lastPreview.geometry, info = window._lastPreview.info.find(i => i.elevation === 'Elevation B') || {};
        return { records: state.elevations.map(e => valleyRecords(e).map(v => [v.kind, v.with, v.roof].join())).join(' / '),
                 cards: [...document.querySelectorAll('.elev-meta')].map(x => x.textContent.replace(/^.*openings?/, '')).join(' / '),
                 caps: g.filter(m => /Head Verge Cap/.test(m.name)).length, fillers: g.filter(m => m.ifc_type === 'ridge_filler').length,
                 upstands: g.filter(m => /Ridge Upstand/.test(m.name)).length, head: info.head ? [info.head.over, info.head.drop, info.head.run > 0].join() : '',
                 setting: document.getElementById('edge-head').value,
                 check: [...document.querySelectorAll('#checks-container > div')].map(d => d.textContent).filter(t => /G3 head verge/.test(t)).length };
    });
    const head = await headVerge();
    console.log('head verge:', JSON.stringify(head), '|', headStatus);
    if (head.records !== 'head,Elevation B,false / head,Elevation A,true' || !/head verge with B/.test(head.cards) || !/^healed edge,2\d\d · head\/head$/.test(headStatus)
        || head.caps !== 1 || head.fillers < 6 || head.upstands < 6 || head.head !== 'Elevation A,60,true' || head.setting !== 'auto' || head.check !== 1)
        throw new Error('the monopitch and the wall were not joined at a head verge: ' + JSON.stringify(head));
    await page.selectOption('#edge-head', 'none');
    await settle();
    const noHead = await headVerge();
    if (noHead.caps || noHead.upstands || noHead.check) throw new Error('the head verge did not follow its chain setting: ' + JSON.stringify(noHead));

    // A ninth model: a lean-to roof run up to a higher wall (VMZINC's head-wall abutments). The roof
    // alone first: the wall is not picked, but it is found standing over the roof, and the G3 flashing
    // turns up it into a chase. Then the wall is picked in standing seam: the roof's trays turn up
    // behind its cladding, which stops over the roof and laps the upstand.
    await page.setInputFiles('#ifc-file', path.join(__dirname, 'lean_to.ifc'));
    await page.waitForFunction(() => state.model && state.model.meshes === 2 && allMeshes.length === 2
                               && state.elevations.length === 1 && !state.elevations[0].picks.length
                               && document.getElementById('status-chip').classList.contains('ready'), null, { timeout: 120000 });
    await page.evaluate(() => setPickMode('pick'));
    await lookAt('Roof', [0.02, 0.96, 0.26]);
    await picked(1);
    await seamChain2();
    await settle();
    const abutment = () => page.evaluate(() => {
        const g = window._lastPreview.geometry, info = name => window._lastPreview.info.find(i => i.elevation === name) || {};
        const roof = info('Elevation A').abut || {}, wall = (info('Elevation B').abut_over || [])[0] || {};
        return { records: state.elevations.map(e => valleyRecords(e).map(v => [v.kind, v.with, v.roof].join())).join(' / '),
                 walls: (state.elevations[0].result.walls_above || []).map(w => w.name).join(),
                 roof: [roof.kind, roof.over, roof.wall, roof.chase].join(), wall: [wall.kind, wall.with, wall.edge].join(), stop: wall.stop,
                 flashings: g.filter(m => /Abutment Flashing/.test(m.name)).length, fillers: g.filter(m => m.ifc_type === 'ridge_filler').length,
                 upstands: g.filter(m => /Abutment Upstand/.test(m.name)).length, saddles: new Set(g.filter(m => m.abut_saddle).map(m => m.abut_saddle)).size,
                 edges: g.filter(m => m.abut_edge).length, backing: g.filter(m => /Abutment Backing/.test(m.name)).length,
                 low: Math.round(Math.min(...g.filter(m => m.elevation === 'Elevation B' && m.ifc_type === 'plank').flatMap(m => m.profile.map(q => q[1])))),
                 setting: document.getElementById('edge-abutment').value,
                 check: [...document.querySelectorAll('#checks-container > div')].map(d => d.textContent).filter(t => /head-wall abutment|over the roof Elevation A/.test(t)).length };
    });
    const g3 = await abutment();
    console.log('abutment, wall not picked:', JSON.stringify(g3));
    if (g3.walls !== 'Wall' || g3.roof !== 'g3,,Wall,true' || g3.flashings !== 1 || g3.fillers < 6 || g3.upstands || g3.setting !== 'auto' || g3.check !== 1)
        throw new Error('the G3 flashing was not built against the wall that was not picked: ' + JSON.stringify(g3));
    await lookAt('Wall', [0.05, 0.1, 1]);
    await picked(2);
    const abutStatus = await page.evaluate(() => document.getElementById('status-chip').textContent);
    await seamChain2();
    await settle();
    const below = await abutment();
    console.log('abutment, standing seam wall above:', JSON.stringify(below), '|', abutStatus);
    if (below.records !== 'abut,Elevation B,true / abut,Elevation A,false' || !/head-wall abutment with Elevation A/.test(abutStatus)
        || below.roof !== 'below,Elevation B,,' || below.wall !== 'below,Elevation A,40' || below.flashings || below.fillers
        || below.upstands < 6 || below.saddles !== below.upstands - 1 || below.edges !== 1 || below.backing !== 1
        || !(below.stop > 60 && below.stop < 300) || Math.abs(below.low - 2700 - below.stop) > 1 || below.check !== 2)
        throw new Error('the roof\'s trays were not turned up under the standing seam wall: ' + JSON.stringify(below));
    await page.evaluate(() => setActive(0));
    await page.selectOption('#edge-abutment', 'g3');
    await settle();
    const forced = await abutment();
    if (forced.roof !== 'g3,Elevation B,,false' || forced.flashings !== 1 || forced.upstands || forced.edges || forced.wall !== 'g3,Elevation A,')
        throw new Error('the abutment did not follow its chain setting: ' + JSON.stringify(forced));
    // Mitred: the trays and seams of both cut on the bisecting plane, and the wall's seams follow the roof's.
    await page.selectOption('#edge-abutment', 'mitre');
    await settle();
    const mitreState = () => page.evaluate(() => {
        const info = name => window._lastPreview.info.find(i => i.elevation === name) || {};
        const g = window._lastPreview.geometry, a = info('Elevation A'), b = info('Elevation B');
        const world = (name, info_) => { const m = g.find(q => q.elevation === name && q.ifc_type === 'plank');
            return (info_.seam_lines || []).map(u => Math.round(m.frame.origin[0] + m.frame.u[0] * u)).sort((x, y) => x - y).join(); };
        return { roof: (a.abut || {}).kind, wall: ((b.abut_over || [])[0] || {}).kind, cut: g.filter(m => m.abut_mitre).length,
                 follows: [(a.seam_from || {}).name || '', (b.seam_from || {}).name || ''].join('/'),
                 same: world('Elevation A', a) === world('Elevation B', b), parts: g.filter(m => /Abutment/.test(m.name)).length,
                 cards: [...document.querySelectorAll('.elev-meta')].map(x => /seams follow/.test(x.textContent)).join(),
                 seamFrom: state.elevations.map(e => e.seamFrom ? e.seamFrom.name : '').join('/'),
                 list: document.getElementById('edge-seamFrom').value, pipette: !!state.pipette };
    });
    const abutMitred = await mitreState();
    console.log('abutment, mitred:', JSON.stringify(abutMitred));
    if (abutMitred.roof !== 'mitre' || abutMitred.wall !== 'mitre' || abutMitred.cut < 20 || abutMitred.parts || abutMitred.follows !== '/Elevation A' || !abutMitred.same
        || abutMitred.cards !== 'false,true')
        throw new Error('the roof and the wall were not mitred with their seams lined up: ' + JSON.stringify(abutMitred));
    // The pipette: on the roof, press it and click the wall: now the roof follows the wall instead.
    await page.evaluate(() => { setActive(0); startPipette(); });
    if (!(await page.evaluate(() => state.pipette === state.elevations[0] && document.getElementById('seam-pipette').classList.contains('active'))))
        throw new Error('the pipette did not start');
    await lookAt('Wall', [0.05, -1, 0.1]);
    await settle();
    const piped = await mitreState();
    console.log('pipette:', JSON.stringify(piped));
    if (piped.seamFrom !== 'Elevation B/' || piped.pipette || piped.list !== 'Elevation B' || piped.follows !== 'Elevation B/' || !piped.same || piped.cards !== 'true,false')
        throw new Error('the pipette did not link the roof\'s seams to the wall\'s: ' + JSON.stringify(piped));
    await page.evaluate(() => startPipette());
    await page.keyboard.press('Escape');
    if (await page.evaluate(() => !!state.pipette)) throw new Error('Esc did not cancel the pipette');
    await page.selectOption('#edge-seamFrom', '');
    await settle();
    if ((await mitreState()).follows !== '/Elevation A') throw new Error('clearing the link did not hand it back to the abutment');
    await page.selectOption('#edge-abutment', 'none');
    await settle();
    const noAbut = await abutment();
    if (noAbut.flashings || noAbut.upstands || noAbut.check || noAbut.low > 100) throw new Error('the abutment was not switched off: ' + JSON.stringify(noAbut));
    // A tenth model: a mansard (VMZINC KN-b1). A 15 degree upper slope runs on over a 70 degree
    // lower one, each its own chain: an eaves over the lower slope's cladding, which stops under it.
    await page.setInputFiles('#ifc-file', path.join(__dirname, 'mansard.ifc'));
    await page.waitForFunction(() => state.model && state.model.meshes === 2 && allMeshes.length === 2
                               && state.elevations.length === 1 && !state.elevations[0].picks.length
                               && document.getElementById('status-chip').classList.contains('ready'), null, { timeout: 120000 });
    await page.evaluate(() => setPickMode('pick'));
    await lookAt('Lower', [0.02, 0.34, 0.94]);
    await picked(1);
    await seamRoof();
    await settle();
    await lookAt('Upper', [0.02, 0.97, 0.26]);
    await picked(2);
    const mansardStatus = await page.evaluate(() => document.getElementById('status-chip').textContent);
    await seamRoof();
    await settle();
    const mansard = await page.evaluate(() => {
        const g = window._lastPreview.geometry, info = name => window._lastPreview.info.find(i => i.elevation === name) || {};
        return { records: state.elevations.map(e => valleyRecords(e).map(v => [v.kind, v.with, v.roof].join())).join(' / '),
                 cards: [...document.querySelectorAll('.elev-meta')].map(x => x.textContent.replace(/^.*openings?/, '')).join(' / '),
                 zinc: g.filter(m => /Eaves Drip/.test(m.name) && m.elevation === 'Elevation B').length,
                 under: (info('Elevation A').eaves_under || []).map(u => [u.with, u.mansard].join()).join(' / '),
                 over: (info('Elevation B').eaves || []).map(e => [e.over, e.mansard, e.run > 0].join()).join(' / '),
                 checks: [...document.querySelectorAll('#checks-container > div')].map(d => d.textContent).filter(t => /mansard/.test(t)).length };
    });
    console.log('mansard:', JSON.stringify(mansard), '|', mansardStatus);
    if (mansard.records !== 'eaves,Elevation B,false / eaves,Elevation A,true' || !/mansard with B/.test(mansard.cards) || !/mansard with A/.test(mansard.cards)
        || !/a mansard with Elevation A/.test(mansardStatus) || mansard.zinc < 2 || mansard.under !== 'Elevation B,true'
        || mansard.over !== 'Elevation A,true,true' || mansard.checks !== 2)
        throw new Error('the two slopes were not joined at a mansard: ' + JSON.stringify(mansard));

    // Copy settings from: the mansard's two chains, one onto the other; then a chain of a saved
    // state listed from its file, copied, and the file taken out of the list again.
    page.on('dialog', d => d.accept());
    const copied = await page.evaluate(async () => {
        const a = state.elevations.find(e => e.name === 'Elevation A'), b = state.elevations.find(e => e.name === 'Elevation B');
        setActive(state.elevations.indexOf(b));
        a.chain.settings = Object.assign({}, a.chain.settings, { seam_full_length: true, seam_balanced: true });
        renderSettingsLibrary();
        const sel = document.getElementById('copy-from');
        const listed = [...sel.options].map(o => o.value);
        sel.value = 'chain:' + state.chains.indexOf(a.chain);
        document.getElementById('copy-to').value = 'this';
        copyChainSettings();
        const snap = snapshotState();
        pyodide.globals.set('_state_json', JSON.stringify(snap));
        const xml = await enginePy('import json as _json\nfrom cladding_state import to_xml as _to_xml\n_to_xml(_json.loads(_state_json))');
        return { listed, own: listed.includes('chain:' + state.chains.indexOf(b.chain)), b: [b.chain.settings.seam_full_length, b.chain.settings.seam_balanced],
                 form: document.getElementById('seam_full_length').checked, xml };
    });
    if (copied.own || !copied.listed.includes('file') || copied.b.join() !== 'true,true' || !copied.form)
        throw new Error('settings were not copied from one chain to another: ' + JSON.stringify(Object.assign({}, copied, { xml: copied.xml.length })));
    const tmp = path.join(require('os').tmpdir(), 'cladforge-settings-source.xml');
    fs.writeFileSync(tmp, copied.xml);
    await page.setInputFiles('#copy-file', tmp);
    await page.waitForFunction(() => (state.settingsLibrary || []).length === 1, null, { timeout: 60000 });
    const library = await page.evaluate(() => {
        const f = state.settingsLibrary[0];
        const opts = [...document.getElementById('copy-from').options].filter(o => o.value.startsWith('lib:')).length;
        const saved = (snapshotState().library || []).length;
        removeSettingsFile(0);
        return { file: f.file, chains: f.chains.length, opts, saved, after: state.settingsLibrary.length,
                 optsAfter: [...document.getElementById('copy-from').options].filter(o => o.value.startsWith('lib:')).length,
                 chips: document.getElementById('copy-files').children.length };
    });
    console.log('copy settings:', JSON.stringify(library));
    if (library.chains !== 2 || library.opts !== 2 || library.saved !== 1 || library.after !== 0 || library.optsAfter !== 0 || library.chips !== 0)
        throw new Error('a state file\'s chains were not listed, saved and taken out again: ' + JSON.stringify(library));

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
