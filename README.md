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
| 4 | Subtract openings and penetrations as interior holes | Pyodide (`fabric_extract.py`) |
| 5 | Detect slab and roof abutments, set out the splash zone | Pyodide (`fabric_extract.py`) |
| 6 | Define buildup and cladding type | UI |
| 7 | Generate battens, counter-battens, boards or panels | Pyodide, per frame (`cladding_geometry.py`) |
| 8 | Export IFC4X3 and DXF | Flask or Pyodide (IFC), Pyodide (DXF) |

Extraction runs once per selection and is cached on the elevation. Coursing
and buildup run in Pyodide on every parameter change, so the offset slider is
live with no debounce; typed numbers are debounced at 300 ms. Nothing in the
coursing path touches the server.

## Running it

```bash
pip install -r requirements.txt
python app.py            # http://localhost:8080
```

Flask serves the page, the Python sources for Pyodide, and a JSON mirror of
the engine (`/api/extract`, `/api/preview`, `/api/check`, `/api/download`,
`/api/download_dxf`). The page also works on static hosting: copy
`templates/index.html` to the root next to `static/` and the `.py` files. In
that mode IFC export falls back to the IfcOpenShell WASM wheel in the browser.

A demo model is in `tests/sample_house.ifc` (regenerate with
`python tests/make_sample.py`).

### Using the tool

1. Drop an IFC on the panel. The file is parsed in your browser and never uploaded.
2. With **Pick faces** on, click a wall face. The coplanar patch joins the active
   elevation; click again to remove it. Click more patches on the same plane to
   merge them. Click a face round the corner and it becomes the next elevation
   in the chain. **New elevation** starts a separate chain.
3. Check the detected abutments on the elevation card. Pitched ones say so and
   the splash band follows the roof line. Untick a false one, or type a level and
   **Add level** where detection fails.
4. Set the buildup: sheathing, insulation, plank or panel, batten section and
   centres, counter-battens, splash zone.
5. Drag the **horizontal offset** slider to control where the closing cuts land.
6. Read the checks, then download IFC4X3 or DXF.

## Decisions on open items

Items the requirements marked **[OPEN]** or **[ASSUMED]** now have a working
default. Each is one place in the code, so any of them can be flipped.

| Item | Decision | Where |
|---|---|---|
| Region definition [ASSUMED] | Yes. A region is coplanar; openings are interior holes and never split a region. Separate patches on one plane merge into one elevation (one frame, one coursing, boards clipped to the union). | `fabric_extract._union_faces` |
| Selection mode [OPEN] | Both. One click grows the connected coplanar patch (SunForm's flood fill), and further clicks merge more patches into the same elevation. | `viewer.coplanarFaces`, `app.onViewportClick` |
| Openings source [OPEN] | Mesh voids. web-ifc punches `IfcRelVoidsElement` openings into the wall mesh, so they arrive free as holes. Penetrations (anything else crossing the face plane: pipes, beams, windows if the void was not punched) are sectioned and subtracted as convex-hull holes. Switch off with the *Subtract penetrations* checkbox. | `fabric_extract.extract_elevation` |
| Abutments | Any `IfcSlab`/`IfcRoof` that reaches the face plane inside the region is sectioned on the plane and its upper edge becomes the abutment *line*: level for a flat roof or slab, pitched where a roof meets a gable (two slopes meeting at the ridge, say). The splash zone is a band of constant vertical height above that line, so it follows the roof. A slab that passes through the face is also cut out of the region. Manual levels can be added per elevation. | `fabric_extract._section`, `_top_line`, `cladding_primitives.splash_rings` |
| Corner detail | Three details, set for the whole job and reported per corner in the panel. **Mitred** (default) cuts the whole buildup on the corner's bisector plane, so every layer wraps. **Master-lap, open joint** is a panel detail: at an external corner the master board wraps past and runs out to the far face of the other side's cladding while the board behind stops a joint gap short of the master's back; at a re-entrant corner nothing wraps, so the master runs into the corner and the other board stops a joint gap clear of the master's whole buildup. The layers behind a lap stay square at the corner. **Square** stops everything at the wall corner. Away from a right angle both lap ends slope with depth. Each corner gets a row under its chain naming the two faces, the angle, whether it is external or re-entrant, and which face masters, with a Swap button; clicking the row highlights that corner in the model. A corner bead or profile is not modelled yet. | `cladding_primitives.corner_ends`, `app.cornerRows` |
| Chains (corners) | A click that is coplanar with the active elevation merges into it. A click on a face that turns a corner from any elevation in the active chain becomes the next elevation in that chain: Elevation A becomes "Chain 1 · A → B → C". A face that meets nothing starts a new chain. Coursing is centred on the whole run and the offset slider is per chain, so panel joints and batten centres carry round the corner (the run reverses through re-entrant corners). Corner allowances and trims are not modelled: the run length is the sum of the face widths. | `fabric_extract.chain_link`, `app.linkIntoChain`, `cladding_geometry.build_elevation` |
| Splash zone applies to battens and cladding only [ASSUMED] | Yes. Sheathing and insulation follow the full outline. | `cladding_booleans.TRIMMABLE` |
| Ground splash zone [OPEN] | Same rule. The elevation base is always an abutment ("Elevation base"); untick it to start boards at the base. | `fabric_extract._merge_abutments` |
| Panel centres dependency [OPEN] | Width drives centres. Batten centres = (panel width + gap) / n, with n chosen so no span exceeds 600 mm. The centres field is locked in panel mode. Closing cuts are reported at both ends and the top. | `cladding_constants._parse` |
| Planks lapped or butt-jointed [OPEN] | Both. Lap = 0 is open-jointed: cover = face + gap. Lap > 0 is lapped: cover = face − lap, and courses overlap by the lap. Planks are modelled flat (boxes only). | `cladding_constants._parse` |
| IFC import | Two readers behind one button. web-ifc runs in the browser and keeps the file private; each element is built inside its own guard so one unbuildable element cannot abandon the file. If it fails or finds nothing, the server reader takes over using IfcOpenShell, which builds the swept solids, clippings and mapped items that defeat web-ifc. The panel names the reader used, lists what was skipped, and offers a re-import on the server. The length unit is judged by the model's size, never by how far it sits from the origin, and the scene is recentred so float32 keeps its millimetres on a georeferenced model; exports are put back on the host model. | `viewer.loadIFC`, `ifc_import.py` |
| Slider scope [OPEN] | Per chain, so joints align around corners. Default 0 centres the coursing on the run. | `app.onSlider` |
| Plank vertical setting-out [OPEN] | Starts at the top of the ground splash zone and works up; the closing cut lands at the top. The slider only shifts along the wall. | `cladding_geometry._horizontal_planks` |
| End joints [OPEN] | Must land on a batten, staggered course to course (odd courses start with a half-length board). Joints that cannot reach a batten are cut at max length and counted as a warning. | `cladding_primitives.split_run` |
| Coursing at openings [OPEN] | Straight through and cut. Coursing never resets at a reveal. | `cladding_booleans.apply_boolean_ops` |
| Horizontal panel joints [ASSUMED] | Open joints at the gap, noggins behind every horizontal joint whenever an elevation runs to more than one course. | `cladding_geometry._panels` |
| Batten orientation | Derived, never a free choice. Horizontal planks → vertical battens. Vertical planks → horizontal battens on vertical counter-battens. Panels → vertical battens with noggins. The only override is *Counter-battens: force on/off*, and the checks flag the buildups that then fail to drain. | `cladding_constants._parse`, `cladding_preview.check_rules` |
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
| fabric_extract.py | 335 | 400 |
| cladding_constants.py | 79 | 80 |
| cladding_geometry.py | 175 | 400 |
| cladding_primitives.py | 150 | 300 |
| cladding_booleans.py | 150 | 200 |
| cladding_preview.py | 100 | 100 |
| ifc_generator.py | 317 | 400 |
| dxf_generator.py | 229 | 500 |
| app.py | 115 | 150 |
| templates/index.html | 176 | 500 |

The frontend logic lives beside the template in `static/viewer.js` (Three.js,
web-ifc, picking, rendering) and `static/app.js` (state, Pyodide, downloads),
with the design system in `static/style.css`.

## Tests

```bash
pytest                                # engine + export tests on a synthetic wall
python tests/make_sample.py           # rebuild the demo IFC
VENDOR_DIR=... node tests/smoke.js    # browser smoke test against a running app.py
```

The smoke test drives Chromium through Playwright: loads the sample house,
picks the south and east walls, moves the slider, switches to panels, and
downloads both exports. `VENDOR_DIR` is only needed where the CDNs are
unreachable; it serves Pyodide, Three.js and web-ifc from local copies.

## Limitations

- Walls only: faces within 5° of vertical. Pitched abutment lines come from the
  upper edge of the roof's section through the face plane; a roof that is an open
  or broken mesh falls back to the convex hull of its section.
- Chains join at vertical corners only, and the corner has to sit within 400 mm
  of both faces' ends. Non-vertical junctions (a wall meeting a sloping face)
  are not chained.
- Corner beads and profiles are not modelled, and nothing supports the boards
  that overhang a corner: a corner batten or angle is the designer's to add.
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
