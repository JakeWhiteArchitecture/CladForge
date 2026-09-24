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
    // Expected layout: <dir>/pyodide/*, <dir>/three/{build,examples}, <dir>/web-ifc/*.
    const vendor = process.env.VENDOR_DIR;
    if (vendor) {
        const mime = { js: 'application/javascript', wasm: 'application/wasm', json: 'application/json', zip: 'application/zip', whl: 'application/octet-stream' };
        const map = [
            [/^https:\/\/cdn\.jsdelivr\.net\/pyodide\/v0\.27\.4\/full\/(.+)$/, m => path.join(vendor, 'pyodide', m[1])],
            [/^https:\/\/cdnjs\.cloudflare\.com\/ajax\/libs\/three\.js\/r128\/three\.min\.js$/, () => path.join(vendor, 'three', 'build', 'three.min.js')],
            [/^https:\/\/cdn\.jsdelivr\.net\/npm\/three@0\.128\.0\/(.+)$/, m => path.join(vendor, 'three', m[1])],
            [/^https:\/\/cdn\.jsdelivr\.net\/npm\/web-ifc@[\d.]+\/(.+)$/, m => path.join(vendor, 'web-ifc', m[1])],
        ];
        await page.route(/^https:\/\/(cdn\.jsdelivr\.net|cdnjs\.cloudflare\.com)\//, route => {
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
        await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.5);
        await page.waitForTimeout(1200);
    }
    // Picking generates nothing: the chain has to be built through the wizard first.
    async function buildChain(type, orient, useButton) {
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
    console.log('pick seeds recorded:', await page.evaluate(() =>
        state.elevations[0].picks.map(p => p.point && p.point.map(c => Math.round(c)).join(',')).join(' | ')));

    // Live slider: time one full preview round trip (Pyodide coursing + trimming + render).
    const ms = await page.evaluate(async () => {
        const e = state.elevations[state.active]; e.offset = 120;
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
    // Vertical planks need counter-battens, so the wizard grows a step for them.
    await buildChain('plank', 'vertical');
    console.log('counter-battens built:', await page.evaluate(() =>
        window._lastPreview.geometry.filter(m => m.ifc_type === 'counter_batten').length + ' at '
        + document.getElementById('cb_centres').value + ' c/c'));

    // Planks cannot lap, so the option disables itself and falls back to a mitre.
    await page.click('#cladding-type .turn-btn[data-value="plank"]');
    await page.waitForTimeout(1200);
    console.log('lap disabled for planks:', await page.evaluate(() =>
        document.querySelector('#corner-type .turn-btn[data-value="lap"]').disabled
        + ' active=' + document.querySelector('#corner-type .turn-btn.active').dataset.value));

    // The server importer, the fallback for models web-ifc cannot build.
    await page.evaluate(() => loadModel(state.file, 'server'));
    await page.waitForFunction(() => state.model && state.model.reader.indexOf('server') >= 0, null, { timeout: 180000 });
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
