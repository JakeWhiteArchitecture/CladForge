# CladForge

**Cladding buildup and board setting-out from an IFC model, in the browser.**

Import an IFC, click the wall faces you want to clad, and CladForge grows each
selection into a named elevation, subtracts the openings, finds the slab and
roof abutments, and generates the batten buildup and board setting-out on
those faces. Output is IFC4X3 geometry placed in the host model's storeys, and
a DXF with one flattened, dimensioned elevation per region.

Forked from [StairSmith](https://github.com/JakeWhiteArchitecture/stairsmith)
(geometry engine, export pipeline, UI shell) and
[SunForm](https://github.com/JakeWhiteArchitecture/sunform) (web-ifc import,
Three.js viewer). The extraction layer, `fabric_extract.py`, is the only
genuinely new code in the stack.

> Status: first build against the draft requirements. Every open item has a
> working default; see **Decisions on open items** below for what was chosen
> and how to flip it.

## Pipeline

| Stage | Action | Where it runs |
|---|---|---|
| 1 | Import IFC, render in viewer | browser (web-ifc + Three.js, from SunForm) |
| 2 | Click wall faces | browser (`static/viewer.js`) |
| 3 | Grow picks into coplanar regions, name Elevation A, B, C | Pyodide (`fabric_extract.py`) |
| 4 | Subtract openings and penetrations: interior holes, plus the notches openings cut in the outline | Pyodide (`fabric_extract.py`) |
| 5 | Detect slab and roof abutments, set out the splash zone | Pyodide (`fabric_extract.py`) |
| 6 | Build the chain: plank or panel, orientation, board, batten and counter-batten sizes, base of the cladding | UI wizard (`static/wizard.js`) |
| 7 | Pick the top and bottom of the cladding | UI, two clicks in the model |
| 8 | Refine buildup, corners, openings, setting-out | UI |
| 9 | Generate battens, counter-battens, boards or panels | Pyodide, per frame (`cladding_geometry.py`) |
| 10 | Export IFC4X3 and DXF | Pyodide (both) |

Extraction runs once per selection and is cached on the elevation. Coursing
and buildup run in Pyodide on every parameter change, so the offset slider is
live with no debounce; typed numbers are debounced at 300 ms. Nothing in the
coursing path touches the server.

Picking and generating are separate. Clicking faces grows elevations and chains
and nothing else: no cladding exists until the chain is built. Once a face is
extracted, **Make chain** appears at the top right of the view and **Enter**
opens the wizard — plank or panel, horizontal or vertical (planks only), the
board dimensions, the battens, the counter-battens where the buildup has them,
and where the cladding starts at the foot of the wall — and **Build** generates
that chain. Build hands straight over to two clicks in the model: one sets the
height of the **top of the cladding**, one the height of the **baserail**. Only
the height of each point is used, the pair applies to the whole chain, and each
face is clamped to its own extent, so a lower wing in the same run never gets
cladding above it. **Dismiss** (or Escape) closes the picker and keeps whatever
has not been set, so a top clicked before dismissing still applies. A step that does not apply is not
asked: panels never course, so they skip the orientation, and a buildup with
no counter-battens skips their step. In panel mode the batten centres are
shown but not editable, because the panel bay sets them. Every pending chain
is built together. After a chain is built the whole panel edits it live, and a
face picked round a corner joins the built chain and is clad straight away.

## Running it

```bash
pip install -r requirements.txt
python app.py            # http://localhost:8080
```

Flask serves two things: the page, and the Python sources for Pyodide to
import. The only route that does work is `/api/import`, the IfcOpenShell
fallback for models web-ifc cannot build. Everything else — extraction,
coursing, checks, and both exports — runs in the browser, so the page also
works on static hosting: copy `templates/index.html` to the root next to
`static/` and the `.py` files, and the only thing lost is that import fallback.

**Runtimes and schema.** IFC export runs through the IfcOpenShell WASM wheel
in the browser, so the runtime pins matter: **Pyodide 0.29.0** (CPython 3.13,
`pyodide_2025_0`) with **IfcOpenShell 0.8.5**, which carries IFC2X3, IFC4 and
IFC4X3_ADD2. The export writes IFC4X3, with no server involved.

The pins are a matched set, not three independent choices. The wheel's ABI tag
has to match the Pyodide build, and Shapely — which the whole engine rests on —
has to exist for that build. Pyodide 0.29 ships Shapely 2.0.7 and numpy 2.2.5,
which is what makes this combination work. An earlier pairing (Pyodide 0.27.4
with IfcOpenShell 0.8.2) had no IFC4X3 at all, and asking that build for one
killed the runtime rather than raising something Python could catch — so
`_create_file` asks `schema_names()` which schemas the build has and takes the
newest, instead of trying them and hoping to catch the failure.

A demo model is in `tests/sample_house.ifc` (regenerate with
`python tests/make_sample.py`).

### Using the tool

1. Drop an IFC on the panel. The file is parsed in your browser and never uploaded.
2. With **Pick faces** on, click a wall face. The coplanar patch joins the active
   elevation; click again to remove it. Click more patches on the same plane to
   merge them. Click a face round the corner and it becomes the next elevation
   in the chain. **New elevation** starts a separate chain. Where a slab or roof
   cuts clean through the face, only the patch you clicked is taken: the click
   position is the seed, so the piece beyond the junction is left alone.
3. Check the detected abutments on the elevation card. Pitched ones say so and
   the splash band follows the roof line. Untick a false one, or type a level and
   **Add level** where detection fails.
4. Press **Enter** (or **Make chain**, top right) to build the chain: the wizard
   asks for plank or panel, the orientation, the board sizes, the battens, the
   counter-battens if the buildup has them, and whether the cladding starts at
   the foot of the wall or above a splash zone. Nothing is generated before this.
   Build then asks for two points in the model: the height of the cladding top,
   then the height of the baserail. Dismiss to keep either as it is.
5. Refine anything in the panel — sheathing, insulation, splash zone, corners,
   openings, batten section and centres. It all previews live from here on.
6. Click a **course dimension** in the view to type a course height over it; in panel
   mode each **row** has its own dimension, so rows can differ in height. A chain is
   set out as one run, so the dialog asks whether to apply it to the whole chain or to
   that elevation alone. Only the active elevation's dimensions are drawn.
7. Press **E** (or **2D elevation**, top right, or in the edit widget) to look at the
   active elevation flat and square-on. The camera swings round to face it and hands
   over to an orthographic view fitted to the cladding and its dimensions: pan and zoom
   work, rotation is locked, the host model fades back and the other elevations'
   cladding is hidden. Choosing another elevation swings across to it; **E**, **Esc** or
   **3D** swings back to where you were. Nothing is recomputed either way.
8. In 2D, **click a dimension to type over it** where it sits: Enter applies, Esc
   cancels, Tab moves to the next. Each writes to what it measures — a panel row (with
   a chain / this-elevation toggle, **Split row** and **Merge with row above**), the
   plank course, the left closing cut (which solves for the offset) and the first panel
   width when the setting-out is centred, the plank batten centres, the splash zone,
   and the top and baserail levels. Greyed labels are driven by something else and say
   what: the batten centres in panel mode, the openings, and the bays when set out from
   the openings. A badge at each cornered end shows the detail (M, L with the master,
   S); click it to change that corner or swap its master.
9. Drag the **horizontal offset** slider to control where the closing cuts land,
   or click the cladding itself: a face that is already clad is not re-picked,
   it opens its chain's setting-out over the view.
10. Read the checks, then download IFC4X3 or DXF. The legend toggles every layer,
   including the picked wall faces and the outline, so the buildup can be read on
   its own.

## Decisions on open items

Items the requirements marked **[OPEN]** or **[ASSUMED]** now have a working
default. Each is one place in the code, so any of them can be flipped.

| Item | Decision | Where |
|---|---|---|
| Region definition [ASSUMED] | Yes. A region is coplanar; openings are interior holes and never split a region. Separate patches on one plane merge into one elevation (one frame, one coursing, boards clipped to the union). | `fabric_extract._union_faces` |
| Selection mode [OPEN] | Both. One click grows the connected coplanar patch (SunForm's flood fill), and further clicks merge more patches into the same elevation. Each click's position is kept as a seed: if the cuts leave the region in pieces, only the pieces a seed falls in are clad, so a slab or roof crossing a face does not carry the cladding past it. A face that is already clad is not a selection any more — clicking it opens that chain's setting-out over the view instead. | `viewer.coplanarFaces`, `app.onViewportClick` |
| Openings source [OPEN] | Mesh voids. web-ifc punches `IfcRelVoidsElement` openings into the wall mesh, so they arrive free as holes. Penetrations (anything else crossing the face plane: pipes, beams, windows if the void was not punched) are sectioned and subtracted as convex-hull holes. Switch off with the *Subtract penetrations* checkbox. | `fabric_extract.extract_elevation` |
| Splash zone at the base [ASSUMED] | The synthetic "Elevation base" line is an assumption, not a detected intersection, so the wizard asks: **at the foot of the wall** (the default — the boards run all the way down) or **above a splash zone**. Every detected slab or roof keeps its own splash zone either way, and the base line stays on the elevation card to tick back on. | `cladding_primitives.base_level`, `wizard.wizBuild` |
| Abutments | Any `IfcSlab`/`IfcRoof` that reaches the face plane **or stands in the cladding zone in front of it** is sectioned and its upper edge becomes the abutment *line*. The zone matters because the cladding stands off the face by its whole buildup, so a roof finish that stops short of the wall still sits where the boards go — a plane section cannot see it. The line is: level for a flat roof or slab, pitched where a roof meets a gable (two slopes meeting at the ridge, say). The splash zone clears that line by the splash setting measured **perpendicular to it**, so the band opens up by 1/cos(pitch) on a slope: 150 mm off a 30 degree roof is 173 mm of vertical band. Measured vertically instead, a 150 mm band leaves only 130 mm of actual clearance off a 30 degree roof. A slab or roof meeting the face **along its foot** — the roof a dormer stands on — counts too, but only when it carries on out under the cladding; a ground slab whose edge stops at the wall has nothing in front of the boards, so the wizard's answer about the foot stands. A slab that passes through the face is also cut out of the region. Manual levels can be added per elevation. | `fabric_extract._section`, `_top_line`, `cladding_primitives.splash_rings` |
| Corner detail | Three details. The job toggle sets the default, and **each corner can override it**: the corner row under its chain has a selector (Job default / Mitred / Master lap / Square) and says which detail is in force, marked *(job)* when it is the default. The two faces meeting at a corner always hold the same override (`detail_hi` on the one before it in the run, `detail_lo` on the one after), and each end of a face is built with its own corner's detail, so one face can be mitred at one end and lapped at the other. A lap set on a corner still falls back to a mitre in plank mode, and a square corner adds no wrap to the chain run. **Mitred** (default) cuts the whole buildup on the corner's bisector plane, so every layer wraps. **Master-lap, open joint** is a panel detail: at an external corner the master board wraps past and runs out to the far face of the other side's cladding while the board behind stops a joint gap short of the master's back; at a re-entrant corner nothing wraps, so the master runs into the corner and the other board stops a joint gap clear of the master's whole buildup. The layers behind a lap stay square at the corner. **Square** stops everything at the wall corner. Away from a right angle both lap ends slope with depth. Each corner gets a row under its chain naming the two faces, the angle, whether it is external or re-entrant, and which face masters, with a Swap button; clicking the row highlights that corner in the model. A corner bead or profile is not modelled yet. | `cladding_primitives.corner_detail`, `corner_ends`, `chain_layout`, `cladding_geometry.build_elevation`, `app.cornerRows`, `app.setCornerDetail` |
| Chains (corners) | A click that is coplanar with the active elevation merges into it. A click on a face that turns a corner from any elevation in the active chain becomes the next elevation in that chain: Elevation A becomes "Chain 1 · A → B → C". A face that meets nothing starts a new chain. Coursing is centred on the whole run, so panel joints and batten centres carry round the corner (the run reverses through re-entrant corners). The offset slider is **per elevation**: it shifts the face you are working on and leaves the rest of the chain alone, which is what you want to line boards up with something on that face — at the cost of the joints carrying round, once two faces of a chain are offset differently. Corner allowances and trims are not modelled: the run length is the sum of the face widths. | `fabric_extract.chain_link`, `app.linkIntoChain`, `cladding_geometry.build_elevation` |
| Openings drive the setting-out | On by default in panel mode. Panel edges land on the structural jambs of every window and door, and each span between jambs is split into equal bays no wider than the maximum panel, so an opening that does not suit the panel centres still sets the joints. Bay widths then vary and there is no closing cut; a bay under 100 mm is flagged. Untick *Set out from the structural openings* for a centred array that ignores them. Only holes at least 300 mm both ways count as openings, so a pipe penetration does not move the joints. | `cladding_primitives.openings`, `bays_between` |
| Cavity closer | A solid timber closer goes to both vertical sides of every opening whatever the cladding is, the full height of the opening, filling the cavity from the sheathing or insulation face out to the back of the boards. In panel mode it also backs the board edge at the jamb, so no batten is placed there and it counts as support when spans are checked. Width is set in the Openings section. | `cladding_geometry._openings_extras` |
| Corners trim the face | A wall face that runs past a corner is clad only up to it. Both faces are cut back to the corner line when they chain, so nothing projects through into the other face, and the chain run is measured on the clad part. | `cladding_primitives.clip_bounds`, `cladding_booleans.clip_elevation` |
| Plank seams | A course is set out along the part of the face it actually crosses, found by intersecting the course band with the region, so a run broken by a gable, a splash zone or an opening is treated as separate runs. A run one board or shorter is a single piece with no seam; the staggered half-length start only applies where a run genuinely needs more than one board. | `cladding_booleans.strip_intervals`, `cladding_primitives.split_run` |
| Reveals | The face board is always mitred to the reveal lining, whatever detail the corners use, cut on the bisector of the arris so the outer face stops at the opening edge and the back runs into the reveal by the board thickness. The lining runs from the cladding face back to the wall face in its own frame. Heads and sills are not lined: a frame whose v is world Z cannot describe a surface that faces up or down. With *Reveal linings* off there is nothing to mitre to, so the panel stays square. | `cladding_geometry._reveal_frame`, `_openings_extras` |
| Splash zone applies to battens and cladding only [ASSUMED] | Yes. Sheathing and insulation follow the full outline. | `cladding_booleans.TRIMMABLE` |
| Ground splash zone [OPEN] | Same rule. The elevation base is always an abutment ("Elevation base"); untick it to start boards at the base. | `fabric_extract._merge_abutments` |
| Panel centres dependency [OPEN] | Width drives centres. Batten centres = (panel width + gap) / n, with n chosen so no span exceeds 600 mm. The centres field is locked in panel mode. Closing cuts are reported at both ends and the top. | `cladding_constants._parse` |
| Planks lapped or butt-jointed [OPEN] | Both. Lap = 0 is open-jointed: cover = face + gap. Lap > 0 is lapped: cover = face − lap, and courses overlap by the lap. Planks are modelled flat (boxes only). | `cladding_constants._parse` |
| IFC import | Two readers behind one button. web-ifc runs in the browser and keeps the file private; each element is built inside its own guard so one unbuildable element cannot abandon the file. If it fails or finds nothing, the server reader takes over using IfcOpenShell, which builds the swept solids, clippings and mapped items that defeat web-ifc. The panel names the reader used, lists what was skipped, and offers a re-import on the server. The length unit is judged by the model's size, never by how far it sits from the origin, and the scene is recentred so float32 keeps its millimetres on a georeferenced model; exports are put back on the host model. | `viewer.loadIFC`, `ifc_import.py` |
| Slider scope [OPEN] | Per chain, so joints align around corners. Default 0 centres the coursing on the run. | `app.onSlider` |
| Plank vertical setting-out [OPEN] | Starts at the top of the ground splash zone and works up; the closing cut lands at the top. Within a chain every face is set out from **one datum** — the base of the elevation you last clicked in that chain, or until you click one, the lowest level any member starts cladding at — so horizontal joints (plank courses and panel seams) run level round the corners even where the faces' bottoms differ; a face that starts higher gets its first course cut at its base. The slider only shifts along the wall. | `cladding_geometry._horizontal_planks` |
| End joints [OPEN] | Must land on a batten, staggered course to course (odd courses start with a half-length board). Joints that cannot reach a batten are cut at max length and counted as a warning. | `cladding_primitives.split_run` |
| Coursing at openings [OPEN] | Straight through and cut. Coursing never resets at a reveal. | `cladding_booleans.apply_boolean_ops` |
| 2D elevation | A view, not a mode of the model: nothing is rebuilt on the way in or out. The perspective camera turns on a sphere about the target (a slerp, eased over 600 ms) so it swings round the model rather than through it, then an orthographic camera takes over with the frustum the perspective one saw at that distance, so the swap does not jump. Everything that picks or projects goes through the active camera. Flat, dimensions are HTML labels placed from projected points each frame, so they stay readable at any zoom; in 3D they stay sprites, and only courses and rows open the course dialog. Every dimension carries its kind and value (and a row its index, a cut its bay), or a *lock* naming what drives it. | `view2d.enter2D`, `exit2D`, `activeCamera`, `dims2d.applyDim`, `cladding_geometry._dim` |
| Panel rows | Panel courses are a list of **row heights**, bottom row first, carried on the elevation (`panel_rows`). With no list every row is the panel height, as before. Rows stack up with the joint gap between them from the chain's course datum (the base of the elevation last clicked, as for plank courses), so seams run level round the corners: a face that starts higher cuts the row at its base, and one that starts lower carries on down at the panel height. Rows cut at the base or below the datum are dimensioned read-only on that face; once the list runs out they carry on at the panel height, and the top row is always the closing cut, taking whatever is left. Each listed row is held to 150–3000 mm: rows down to 150 are deliberate tiers, and a row asked for below that, or a closing row under 100 mm, is flagged in the checks. Every row gets its own dimension up the right-hand side, which can be typed over for this elevation or the whole chain; the closing row is dimensioned read-only as the cut. Noggins go behind every row joint when counter-battens are on. Panels are named by row, *Panel R2-3*, and the DXF dimensions every row and lists the heights in its schedule. | `cladding_primitives.panel_rows`, `cladding_geometry._panels`, `cladding_checks.check_rules` |
| Horizontal panel joints [ASSUMED] | Open joints at the gap. Noggins behind them only where counter-battens are on: a noggin between vertical battens sits on the drainage plane and dams it, so by default the seams are left to a proprietary horizontal profile and the checks say so. | `cladding_geometry._panels` |
| Batten orientation | Derived, never a free choice. Horizontal planks → vertical battens. Vertical planks → horizontal battens on vertical counter-battens. Panels → vertical battens, with seam noggins only when counter-battens are on. The only override is *Counter-battens: force on/off*, and the checks flag the buildups that then fail to drain. | `cladding_constants._parse`, `cladding_preview.check_rules` |
| IFC container [OPEN] | `IfcElementAssembly` per elevation (`PredefinedType=USERDEFINED`, `ObjectType="Cladding system"`), placed in the host wall's storey. | `ifc_generator.meshes_to_ifc` |

Two additions beyond the table: a closing cut narrower than 100 mm raises a
warning (the slider is there to move it), and a batten section is given as
width × depth where depth is the cavity.

## Validation rules

| Check | Pass | Warn | Fail |
|---|---|---|---|
| Batten centres vs plank thickness | under the span table (12→400, 16→500, 20→600) | at the limit | over |
| Panel joint position | on a batten (by construction) | | |
| Splash zone | ≥ 150 | 100–150 | < 100 |
| Fixing through insulation | first batten layer depth ≥ insulation + 25 | | less |
| Cavity depth | ≥ 25 | | less |
| Panel size | within max W and H (by construction) | | |
| Buildup | derived | counter-battens forced on | horizontal battens with no counter-battens |
| End joints / closing cut | on battens, cuts ≥ 100 | otherwise | |

**Scope limits, stated in the UI and both export headers.** CladForge does not
check compliance with Approved Document B and does not generate, position or
verify cavity barriers. Cavity barrier provision remains a design decision
outside the tool. Any schedule the tool produces is setting-out information,
not a quantity take-off for pricing.

## Geometry model

Every element is a *prism*: a 2D profile in the elevation's local frame (u
along the wall, v up, depth outward along the normal) extruded by its
thickness. Battens, boards, sheathing and insulation are all rectangles in
that frame, so the whole stack has one mesh type, one Three.js renderer path
and one IFC converter. The preview draws untrimmed rectangles while the slider
is dragged and trims them with Shapely otherwise; export always trims.

```
{"type": "prism", "profile": [[u,v],...], "holes": [...], "depth": d, "thickness": t,
 "frame": {"origin": [x,y,z], "u": [ux,uy,0], "n": [nx,ny,0]},
 "color": "#..", "opacity": 1.0, "name": "...", "ifc_type": "batten", "elevation": "Elevation A"}
```

Coordinates are IFC millimetres, Z-up. The viewer swaps to Three.js Y-up.

## Exports

Cavity closers export as `IfcMember` with ObjectType "Cavity closer"; reveal
linings as `IfcCovering` with "Reveal lining". The DXF gains a `CLOSER` layer;
reveal linings are left out of the flattened elevation because they are
perpendicular to that view, and the schedule counts them instead.

**IFC4X3.** `IfcProject → IfcSite → IfcBuilding → IfcBuildingStorey` named
after the host model, one `IfcElementAssembly` per elevation in the storey of
the wall that was picked. Battens and counter-battens are `IfcMember`,
sheathing `IfcPlate`, insulation and boards `IfcCovering`. `Pset_MemberCommon`,
`Pset_PlateCommon`, `Pset_CoveringCommon`, plus `CladForge_SettingOut` on each
assembly (centres, cover, offset, closing cuts, splash zone) and
`CladForge_Disclaimer` on the project. GUIDs are deterministic: re-export the
same design and every unchanged element keeps its GlobalId.

**DXF.** R12 (AC1009). One flattened elevation per region, moved to origin,
laid out left to right. Layers `WALL`, `OPENING`, `SPLASH_ZONE`, `SHEATHING`,
`INSULATION`, `COUNTER_BATTEN`, `BATTEN`, `CLADDING`, `DIMS`, `NOTES`.
Dimensions: batten centres, splash zone, closing cut, course height, overall
size. A setting-out schedule under each elevation and the disclaimer block in
the title area.

## File budget

| File | Lines | Budget |
|---|---|---|
| fabric_extract.py | 514 | 400 |
| cladding_constants.py | 82 | 80 |
| cladding_geometry.py | 314 | 400 |
| cladding_primitives.py | 327 | 300 |
| cladding_booleans.py | 208 | 200 |
| cladding_preview.py | 45 | 100 |
| ifc_generator.py | 396 | 400 |
| dxf_generator.py | 248 | 500 |
| app.py | 78 | 150 |
| templates/index.html | 248 | 500 |

`fabric_extract.py` and `cladding_booleans.py` are over their budgets (by 114 and
6 lines); splitting the region clean-up — notches, seeded patches — into its own
module would bring both back inside.

The frontend logic lives beside the template in `static/viewer.js` (Three.js,
web-ifc, picking, rendering), `static/app.js` (state, Pyodide, downloads) and
`static/wizard.js` (the build gate and its wizard), with the design system in
`static/style.css`.

## Tests

```bash
pytest                                # engine + export tests on a synthetic wall
python tests/make_sample.py           # rebuild the demo IFC
VENDOR_DIR=... node tests/smoke.js    # browser smoke test against a running app.py
```

The smoke test drives Chromium through Playwright: loads the sample house,
picks the south and east walls, builds each chain through the wizard (by Enter
and by the button), moves the slider, switches to panels, and downloads both
exports. It also checks that picking alone generates nothing. `VENDOR_DIR` is only needed where the CDNs are
unreachable; it serves Pyodide, Three.js, web-ifc, the IfcOpenShell wheel and the
two PyPI deps that are not in the Pyodide distribution from local copies
(`<dir>/{pyodide,three,web-ifc,wasm-wheels,pypi}`).

## Limitations

- Pyodide runs on the page's main thread, so while an extraction is running nothing
  repaints and nothing can be cancelled — a slow one looks exactly like a hang and there
  is no way out but a reload. Extraction therefore runs to a deadline
  (`DEFAULT_BUDGET_S`, 20 s): past it the context loop stops and the elevation comes back
  from what was read, warning which elements were skipped. The proper fix is to move the
  engine into a Web Worker, where a hang neither freezes the page nor survives a
  terminate; the deadline is the bound until that happens. The engine prints each stage to the
  console as it starts (`[extract] ...`), so the last line names the stage that did not
  finish, and the result carries `timings_ms` for the slowest context elements.
- Sampling the upper edge of an abutment costs one ray cast per probe, so probing every
  vertex is quadratic in the section's complexity: a faceted roof with 6,000 vertices took
  22 seconds. Probes are capped at `MAX_TOP_SAMPLES` (240), which brings that to 0.85 s.
- The engine runs in a fixed WASM heap, and overrunning it kills the runtime outright
  rather than raising something Python can catch. Context is therefore capped at
  `CONTEXT_TRI_BUDGET` triangles (60,000), sending the elements that come closest to the
  face plane first and reporting the rest on the elevation card. If the runtime does die,
  the app rebuilds it and retries once instead of leaving the session unusable.
- Walls only: faces within 5° of vertical. Pitched abutment lines come from the
  upper edge of the roof's section through the face plane; a roof that is an open
  or broken mesh falls back to the convex hull of its section.
- Chains join at vertical corners only, and the corner has to sit within 400 mm
  of both faces' ends. Non-vertical junctions (a wall meeting a sloping face)
  are not chained.
- Corner beads and profiles are not modelled, and nothing supports the boards
  that overhang a corner: a corner batten or angle is the designer's to add.
- Opening heads and sills get a cavity closer only at the jambs; head and sill
  linings, cills and flashings are not modelled.
- An opening that breaks the face outline rather than leaving a hole — a door to
  the ground, a window at a wall end — is recovered as a notch: a rectangular
  bite out of the patch's bounding box, open on exactly one side. A gable is
  triangular and a stepped wall is open on two sides, so neither is mistaken for
  one, but a genuine rectangular step in the top of a wall would be.
- Opening-driven setting-out is a panel rule. Plank coursing still runs
  straight through an opening and is cut.
- Mitred elements are written to IFC as an explicit brep rather than a swept
  solid, because the end faces slope with depth. IfcOpenShell's boolean against
  an infinite half space was not dependable enough to cut the joint.
- Penetrations are subtracted as convex hulls of their section through the
  face plane.
- Planks and panels are flat boxes. Lapped profiles overlap in the plane
  rather than tilt.
- No cavity barriers, no fixings, no trims or flashings, no corner details.
- No georeferencing is added; coordinates stay in the host model's system.

## Licence

MIT. Copyright 2026 Jake White Architecture. See `NOTICE` for the
StairSmith and SunForm attribution.
