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
11. **Save state (XML)**, under the model, writes the whole session to a file: every chain
   and elevation, the wall surfaces they are attached to, and every setting. **Load
   state** puts it all back, ready to carry on. The file loads without the IFC; open the
   IFC first to see the host model, since opening a model starts afresh.
12. To get at a face behind something, press **H** (or **Hide elements**, over the
   model) and click whatever is in the way: it hides, and clicks pass through to what
   is behind. The row under the model hides a whole type at once (every slab, say),
   lists what is hidden (click one to bring it back) and has **Show all**. **H** again
   returns to picking. Hiding is only for the view: extraction still sees every element.
13. **Cutting run** (top right of the view) plays the cutting plan board by board beside
   the model; with planks, stock length by stock length. The side panel steps aside: the 3D view stays on the left and
   the plan is on the right, one row per elevation, boards left to right as they come off
   the wall. Each board has its own pastel colour, shared by its pieces on the strip and
   on the wall, and every piece is numbered *board·piece* (3·1, 3·2) in both. Playing, each
   piece in turn goes full colour with a glowing edge in both views, the counter reads
   *Board 3 of 18 · piece 1 of 2: 1195 × 1200 mm*, the strip scrolls to the board and the
   camera follows the piece round the chain. **Space** plays or pauses, **← →** step a
   piece, the slider sets the speed, a click on a piece or board jumps to it, and **Esc**
   or **Close** goes back. It is the same plan the cutting plan DXF downloads.
14. With planks, **Stock length** is the length they are bought in, **Shortest piece** is
   the least a splice may leave (1200, say, so a 4.0 m run becomes 2.8 + 1.2 on battens
   rather than 3.6 + 0.4), and the **Splices** slider runs from *Fewer* (pieces up to the
   stock length) to *More* (down to the shortest piece). **Profile** at a right-angled
   chain corner is a square timber corner trim with planks. **G** (or *Grid* in the
   legend) hides the floor grid.

## Decisions on open items

Items the requirements marked **[OPEN]** or **[ASSUMED]** now have a working
default. Each is one place in the code, so any of them can be flipped.

| Item | Decision | Where |
|---|---|---|
| Region definition [ASSUMED] | Yes. A region is coplanar; openings are interior holes and never split a region. Separate patches on one plane merge into one elevation (one frame, one coursing, boards clipped to the union). | `fabric_extract._union_faces` |
| Hiding elements | A third viewer mode beside *Pick faces* and *Orbit only*, toggled by **H**. A click hides the element under it; raycasts skip hidden meshes, so the next click reaches what was behind. Types can be hidden in one go. Hiding changes nothing but the view: context for extraction, openings and frames still come from every element, and a new model starts with nothing hidden. | `viewer.hideElement`, `app.renderHidden` |
| Selection mode [OPEN] | Both. One click grows the connected coplanar patch (SunForm's flood fill), and further clicks merge more patches into the same elevation. Each click's position is kept as a seed: if the cuts leave the region in pieces, only the pieces a seed falls in are clad, so a slab or roof crossing a face does not carry the cladding past it. A face that is already clad is not a selection any more — clicking it opens that chain's setting-out over the view instead. | `viewer.coplanarFaces`, `app.onViewportClick` |
| Openings source [OPEN] | Mesh voids. web-ifc punches `IfcRelVoidsElement` openings into the wall mesh, so they arrive free as holes. A window or door is an opening wherever it sits: an interior hole, a notch where a door breaks the outline at the foot, or a gap open at top and bottom where one runs the full height of the face and splits it. They are found on the face as picked, before the chain's top and bottom levels clip it, so one that crosses the clad top or bottom keeps its jambs (closed, lined, mitred, never taken for a free end); its closers and jamb linings stop at the clad band, and it has a head lining only if its head is within the cladding. Penetrations (anything else crossing the face plane: pipes, beams, windows if the void was not punched) are sectioned and subtracted as convex-hull holes. Switch off with the *Subtract penetrations* checkbox. | `fabric_extract.extract_elevation` |
| Splash zone at the base [ASSUMED] | The synthetic "Elevation base" line is an assumption, not a detected intersection, so the wizard asks: **at the foot of the wall** (the default — the boards run all the way down) or **above a splash zone**. Every detected slab or roof keeps its own splash zone either way, and the base line stays on the elevation card to tick back on. | `cladding_primitives.base_level`, `wizard.wizBuild` |
| Abutments | Any `IfcSlab`/`IfcRoof` that reaches the face plane **or stands in the cladding zone in front of it** is sectioned and its upper edge becomes the abutment *line*. The zone matters because the cladding stands off the face by its whole buildup, so a roof finish that stops short of the wall still sits where the boards go — a plane section cannot see it. The line is: level for a flat roof or slab, pitched where a roof meets a gable (two slopes meeting at the ridge, say). The splash zone clears that line by the splash setting measured **perpendicular to it**, so the band opens up by 1/cos(pitch) on a slope: 150 mm off a 30 degree roof is 173 mm of vertical band. Measured vertically instead, a 150 mm band leaves only 130 mm of actual clearance off a 30 degree roof. A slab or roof meeting the face **along its foot** — the roof a dormer stands on — counts too, but only when it carries on out under the cladding; a ground slab whose edge stops at the wall has nothing in front of the boards, so the wizard's answer about the foot stands. A slab that passes through the face is also cut out of the region. Manual levels can be added per elevation. | `fabric_extract._section`, `_top_line`, `cladding_primitives.splash_rings` |
| Corner detail | Four details, and **every orange corner can take its own**: chain external and re-entrant corners, window and door jambs, and heads. The job toggle sets the default for chain corners; a chain corner is set on its corner row or by clicking its edge or badge in 2D, a window or door by clicking its jamb or head edge (or badge) in 2D. **Mitred** (the default) cuts the whole buildup on the bisector plane, with the chain's mitre gap between the boards. **Master** is a panel detail with two arrangements (Swap): the master board comes out flush to the outside face of the adjoining surface's board and the other stops a panel joint gap short, clear of the master's side; at a re-entrant corner the master runs into the corner and the other stops a joint gap clear of the master's whole buildup; the layers behind stay square. **Profile** is an aluminium outer corner profile (below), right-angled external corners of panels only, never at a re-entrant corner. **Square** is a placeholder that stops both at the corner line. With planks, Master and Profile are unavailable and the corner stays mitred. The two faces meeting at a chain corner hold the same override (`detail_hi` on the one before it in the run, `detail_lo` on the one after), and each end of a face is built with its own corner's detail. Both jambs of a window or door always share one detail and arrangement, mirrored; the head has its own; changing either asks *Apply this to every window and door in this chain?* (Yes: every opening in the chain; No: this one). Choices are kept on the elevation by opening (`opening_details`, keyed by the structural opening's corner), so they survive rebuilds. The mitre gap stays separate from the panel joint gap. Away from a right angle a master end slopes with depth. | `cladding_primitives.corner_detail`, `corner_ends`, `cladding_edges.opening_details`, `reveal_treatment`, `app.setCornerDetail`, `app.setOpeningDetail`, `dims2d.detailBlock` |
| Corner profile | An aluminium outer corner profile of the Rockpanel Profile D type, 1.1 mm thick: in plan, a hollow D × D nose at the outer corner with a small outer radius, its outside faces flush with both panel faces, and two flanges in the plane of the back of the panels on the batten face, **both 35 mm**: flange A behind the panel before the corner in the run, flange B behind the one after. D is the panel thickness. Each panel stops D plus the chain's **profile gap** (1 mm by default, set in the Edges section) short of the outer corner line. Behind the panels the insulation and sheathing stay mitred, but the battens meet in a solid timber L (see *Corner timbers*). It is a prism in the general frame: section in plan, extruded along world Z over the corner's clad height less the top and bottom offsets, or along the head under a head. At a jamb or head flange A is on the elevation face; the face board stops the profile gap back from the opening line and the lining stops D plus the gap short of the arris; a jamb's profile runs up to the underside of the head lining and a head's runs between the jamb linings. Exported to IFC4X3 as an `IfcMember` (ObjectType *Corner profile*) with a `CladForge_CornerProfile` property set (name, D, flanges, thickness, length); drawn in the DXF elevation as a strip D wide on layer `CORNER_PROFILE`; badged *P* in 2D. The checks warn where a flange is not over a batten (or closer; at a chain corner the timber L carries both by construction), and where the panels are not 6, 8 or 10 mm (confirm the profile size with the manufacturer). | `cladding_corners.section`, `vertical_profile`, `head_profile`, `flange_supported`, `cladding_geometry._profiled_corners` |
| Corner timbers | At a chain corner set to Profile the battens meet in a **solid L of timber, square, not mitred**, as in Rockpanel's H.03 external corner: the face before the corner has a batten **twice the batten width** (100 at 50 mm battens) running on past the corner until its end is flush with the other face's batten face, and flange A lies on it; the face after the corner has one **one batten width** (50) tight behind it, and flange B lies over the wide one's end. Both are the batten depth. Where no batten reached the corner (one close by took its place), one is made from the nearest batten's section, and any batten the L covers gives way to it. Since they run past their own outline on purpose, they are trimmed in height only, to where the face is clad just inside the corner. Other corners keep mitred battens. At any external chain corner, a batten set out on a joint that lands close to the corner can straddle the wall's corner line: past it the cavity carries on in front of the other face's insulation, so that batten is trimmed in height only too and stays centred on its joint, with its gasket centred on it. | `cladding_geometry._corner_timbers` |
| EPDM gaskets | In panel mode every vertical timber the panels touch (battens, the corner L and the jamb cavity closers) carries a **2 mm EPDM gasket**, **15 mm wider than the timber each side** (Rockpanel: the gasket laps the framework by aR1 ≥ 15 mm). It is drawn in the back 2 mm of the board, overlapping it, so nothing moves and the cavity keeps its depth. A side that meets a mitred corner keeps the timber's end; at the timber L each gasket stops where the board does. Horizontal timbers (noggins, counter-battens) take none. Exported to IFC4X3 as `IfcCovering` (PredefinedType MEMBRANE, ObjectType *EPDM gasket*, material *EPDM foam gasket*), on DXF layer `GASKET`, and shown with the battens. | `cladding_geometry._gaskets` |
| Chains (corners) | A click that is coplanar with the active elevation merges into it. A click on a face that turns a corner from any elevation in the active chain becomes the next elevation in that chain: Elevation A becomes "Chain 1 · A → B → C". A face that meets nothing starts a new chain. Coursing is centred on the whole run, so panel joints and batten centres carry round the corner (the run reverses through re-entrant corners). The offset slider is **per elevation**: it shifts the face you are working on and leaves the rest of the chain alone, which is what you want to line boards up with something on that face — at the cost of the joints carrying round, once two faces of a chain are offset differently. Corner allowances and trims are not modelled: the run length is the sum of the face widths. | `fabric_extract.chain_link`, `app.linkIntoChain`, `cladding_geometry.build_elevation` |
| Openings drive the setting-out | On by default in panel mode. Panel edges land on the structural jambs of every window and door, and each span between jambs is split into equal bays no wider than the maximum panel, so an opening that does not suit the panel centres still sets the joints. Bay widths then vary and there is no closing cut; a bay under 100 mm is flagged. Untick *Set out from the structural openings* for a centred array that ignores them. Only holes at least 300 mm both ways count as openings, so a pipe penetration does not move the joints. | `cladding_primitives.openings`, `bays_between` |
| Cavity closer | A solid timber closer goes to both vertical sides of every opening whatever the cladding is, the full height of the opening, filling the cavity from the sheathing or insulation face out to the back of the boards. In panel mode it also backs the board edge at the jamb, so no batten is placed there and it counts as support when spans are checked. Width is set in the Openings section. | `cladding_geometry._openings_extras` |
| Corners trim the face | A wall face that runs past a corner is clad only up to it. Both faces are cut back to the corner line when they chain, so nothing projects through into the other face, and the chain run is measured on the clad part. | `cladding_primitives.clip_bounds`, `cladding_booleans.clip_elevation` |
| Unions that cannot kill the engine | A GEOS TopologyException ("found non-noded intersection") inside WASM is a C++ abort that takes the whole Pyodide runtime down, and try/except never sees it. It happened on a real model where two elements' edges nearly coincided (ends 0.02 mm apart): extracting that wall killed the engine, so the face never populated. Every union in the extractor's sections now looks first (`_near_misses`: vertices, or a vertex and an edge, between 1 micron and 0.5 mm apart without meeting) and only then unions on a 0.01 mm grid (snap-rounding, which always nodes); clean geometry is unioned exactly as before. The face's own outline is never snapped. In the app, `pyRun` stops a call to a dead engine waiting forever: once Python has stopped, a trivial call that answers "fatally failed" rejects the stuck one, so the restart-and-retry path runs instead of the wall sitting at "extracting". | `fabric_extract._union`, `_near_misses`, `app.pyRun` |
| Plank seams | A course is set out along the part of the face it actually crosses, found by intersecting the course band with the region, so a run broken by a gable, a splash zone or an opening is treated as separate runs. A run one board or shorter is a single piece with no seam; the staggered half-length start only applies where a run genuinely needs more than one board. | `cladding_booleans.strip_intervals`, `cladding_primitives.split_run` |
| Reveals | Both jambs and the **head** are lined, and every face board edge that runs along a jamb or head meets its lining with that opening's corner detail — **mitred** by default: board and lining are cut on the bisector through the outer arris (where the lining's opening face meets the cladding face) and the inner corner (its back face at the board's back), each pulled off it by half the mitre gap, so the gap is the mitre gap straight across and the arris is closed. (Before this fix the cut ran corner to corner the other way, 90° off, leaving the arris open and the board's back running into the lining.) Where a mitred edge meets a plain one, the vertex moves with the mitre if the plain edge runs that way, so an outline never folds back on itself. **Master** has two arrangements: the face board runs past and covers the lining's edge (the lining stops a panel joint gap short of the board's back), or the lining runs out flush to the face board's outside face (the face board stops a panel joint gap short of it). **Profile** puts a corner profile on the arris with both boards stopping D short of it; **Square** is a placeholder. The treatment covers the edges of holes left where a board spanning the opening is trimmed round it, not only board ends that land on a jamb; along an edge that carries on past the opening it steps at the opening's end. It is recorded as a shift on each outline vertex (`vshift`) and read by the IFC writer, the DXF and the viewer. The jamb linings run from the cladding face back to the wall face in their own frames; the head lining lies flat under the head in a **general frame** (n facing down, u inward, v = n × u along the wall) between the jamb linings. With *Reveal linings* off the boards stay square. Sills are not lined. | `cladding_edges.reveal_treatment`, `mitre_to_linings`, `cladding_geometry._openings_extras`, `_head_frame`, `cladding_primitives.ring_at` |
| Edge offsets | Every boundary edge of the clad area is classified and drawn in its own colour in the 2D view: **orange** chain corners and window or door jambs, **purple** tops (sloped gables and roof lines too, and the top of the cladding under a cill), **green** free ends of a run, **blue** bottoms (and the top of a splash zone over a roof or slab), **red** window or door heads. An offset pulls the whole trimmable buildup (battens, counter-battens, noggins and boards; not sheathing or insulation) back from its edges: top 10, bottom 10 and free ends 0 by default; corners and jambs take none. It is applied as a strip taken off the trimming region along each edge, so it follows a sloped top, and plank runs are set out on the same reduced area. Values are **per chain**: click an edge in 2D to type one, or use the Edges section of the panel, which shows the active chain. A pipe penetration is not an opening and has no edges of its own. | `cladding_edges.classify_edges`, `offset_region`, `dims2d.openEdgeEditor`, `app.onEdgeField` |
| Mitre gap | One value for every mitre in a chain, 10 mm by default: at a mitred chain corner between the two faces' boards, and between a face board and a jamb or head lining. It is measured straight across the joint, so each of the two boards is pulled back half of it along its own length (x / √(1 + k²) square to a cut at slope k). At a chain corner only the boards open up; the layers behind still meet on the bisector. | `cladding_edges.mitre_pullback`, `cladding_geometry.build_elevation` |
| Head ventilation | A chain setting. **At front** (the default): the head lining is tight to the lintel or window head, and the open mitre joint at its front edge is the vent. **At back**: the lining drops by an air space (10 mm by default) and stops the same distance short of the window frame (see *Reveal linings to the frame*), so air runs over it and out on the face of the frame; the face board comes down to the lowered lining, closing the front. Click a red head edge in 2D, or use the panel. | `cladding_geometry._openings_extras`, `cladding_edges.settings` |
| Reveal linings to the frame | The jamb and head linings run from the cladding face back to the window or door frame, **tight to its outer face**: past the wall face where the frame is set back, short of it where it stands forward (as in Rockpanel H.02). Where the frame is, per opening: **set** on the opening in its 2D box (mm behind the wall face, negative forward; always wins, *Clear* goes back); else the **frame in the model**, an `IfcWindow` or `IfcDoor` only (nothing else is ever taken for a frame, however far back), covering most of the opening, its outer face taken above its bottom tenth so a projecting sill is not the frame, and accepted only within the reveal: behind the cladding face and in front of the back of the picked wall (its depth measured from the model and sent with the face); else the chain's **frame setback**, 50 mm behind the wall face by default (Edges section). The *Window frames* check says how many came from each, and warns where a window or door was found but rejected, naming it and where it was. | `cladding_edges.opening_frames`, `fabric_extract._frame_of`, `app.wallDepth` |
| Splash zone applies to battens and cladding only [ASSUMED] | Yes. Sheathing and insulation follow the full outline. | `cladding_booleans.TRIMMABLE` |
| Ground splash zone [OPEN] | Same rule. The elevation base is always an abutment ("Elevation base"); untick it to start boards at the base. | `fabric_extract._merge_abutments` |
| Panel centres dependency [OPEN] | Width drives centres. Batten centres = (panel width + gap) / n, with n chosen so no span exceeds 600 mm. The centres field is locked in panel mode. Closing cuts are reported at both ends and the top. | `cladding_constants._parse` |
| Planks lapped or butt-jointed [OPEN] | Both. Lap = 0 is open-jointed: cover = face + gap. Lap > 0 is lapped: cover = face − lap, and courses overlap by the lap. Planks are modelled flat (boxes only). | `cladding_constants._parse` |
| IFC import | Two readers behind one button. web-ifc runs in the browser and keeps the file private; each element is built inside its own guard so one unbuildable element cannot abandon the file. If it fails or finds nothing, the server reader takes over using IfcOpenShell, which builds the swept solids, clippings and mapped items that defeat web-ifc. The panel names the reader used, lists what was skipped, and offers a re-import on the server. The length unit is judged by the model's size, never by how far it sits from the origin, and the scene is recentred so float32 keeps its millimetres on a georeferenced model; exports are put back on the host model. | `viewer.loadIFC`, `ifc_import.py` |
| Slider scope [OPEN] | Per chain, so joints align around corners. Default 0 centres the coursing on the run. | `app.onSlider` |
| Plank vertical setting-out [OPEN] | Starts at the top of the ground splash zone and works up; the closing cut lands at the top. Within a chain every face is set out from **one datum**: the lowest level any member starts cladding at, until the first custom vertical input in that chain — a typed row or course height, a split or a merge, in 2D or the 3D dialog — locks it to the base of the elevation where it was made. Selecting elevations never moves it, and later edits on other faces leave it where it is; it is still that face's base level, so moving the baserail on purpose moves it. Horizontal joints (plank courses and panel seams) run level round the corners even where the faces' bottoms differ; a face that starts higher gets its first course cut at its base. The slider only shifts along the wall. | `cladding_geometry._horizontal_planks`, `cladding_preview._build_all`, `app.lockDatum` |
| End joints [OPEN] | Must land on a batten, staggered course to course (odd courses start with a half-length board). Joints that cannot reach a batten are cut at the set-out length and counted as a warning. | `cladding_primitives.split_run` |
| Plank splices | A run longer than it needs is spliced into pieces set out to a **target length**: the stock length at *Fewer* (the default, as before), stepping down to the shortest piece at *More*, never longer than the stock. Each joint goes on the furthest batten within the target; **no splice may leave a piece shorter than the shortest piece** (300 mm by default) on either side of it, so where no batten within the target does, the piece runs on to the next batten that does, up to the stock length, and a run that then fits one length whole is left whole. Only a run shorter than the minimum is a shorter piece, and only where no batten works at all is a joint cut off a batten (and warned). | `cladding_primitives.split_run`, `cladding_constants._parse` |
| Optimise splices | Planks only, from the waste readout. The Splices slider is tried from Fewer to More in 10% steps (and where it is now), each a full rebuild of the setting-out packed onto stock lengths by the quick check, one trial per engine call so the button can say how far it has got. **The stock length and the shortest piece stay as set**, and joints still land on battens. Ranked by planks too long for the stock (left out of the packing, so a setting with any would look better than it is), then stock lengths, then waste, then pieces (less cutting and fixing), then nearness to the current setting. The readout shows the current setting and up to three that do better, each with **Apply**: each offer trades something (no offer is no better than an earlier one on lengths, waste and pieces at once), so the list runs from fewest lengths to fewest pieces. A rebuild clears them. | `cladding_preview.splice_trial`, `cladding_preview.rank_splices`, `app.optimiseSplices` |
| Plank joints stagger | Every plank keeps its joints clear of the joints in the plank beside it (the course below for horizontal planks, the plank to the left for vertical): **two battens clear** where a batten allows, else one, else, where the rest of the run fits one stock length, **no joint at all**, so neighbouring boards alternate (spliced, whole, spliced) rather than splicing in line. The minimum piece and the stock length still hold. The half-length start on alternate boards could not promise this alone: with a minimum piece over half the target it was pushed onto the neighbour's batten (on a real 2400 mm wall at 70% splices and a 900 minimum, 36 of 62 joints were on the same batten as the board beside them; now none). | `cladding_primitives.split_run`, `_clear_cut`, `cladding_geometry._joints` |
| Plank cutting list | Planks are cut from **stock lengths** (the stock length, a plank wide), so they pack in one dimension: each piece takes its length and a kerf, **end trim** comes off both ends of a length first, and a plank ripped narrower than the face (a closing course, or the course the base cuts) is still cut from a full-width length and says so. Two piece orders (longest first, wall order) × first, best and worst fit, fewest lengths then the longest offcut kept; Optimise boards adds seeded shuffles within the same work allowance. The plan has a panel plan's shape, each length a board, so the readout (*Lengths n of 3600*, *Divides the length*), the cutting plan DXF (each length standing up, a plank wide and its length tall, side by side along each elevation's row, pieces from the bottom named with their length, labels running up the page) and the cutting run all read it. Each elevation is packed on its own, as for panels. | `cladding_nesting.pack_lengths`, `cladding_nesting.plank_pieces` |
| Offcuts passed on | Off by default (*Use offcuts on later elevations*, Cutting section): every board is cut for one elevation. On, the elevations are packed in chain order and each may cut from the offcuts the ones before it leave: its pieces are packed with those offcuts already on the rack (new boards only when none takes a piece, the quick or full search's orders and rules, fewest new boards, then most cut from offcuts, then **the pieces cut from offcuts closest together on the wall**), the rest re-packed as usual, and it is kept **only if it buys fewer boards** than packing the elevation alone, so it never costs one. With planks, the full search then runs the keep-together pass over the offcuts and the new lengths at once, the offcuts' pieces counted as one more group, so what is cut from offcuts goes up in one place. Panels and planks alike; pieces only go forward, never back to an elevation already cut. A piece cut from an earlier board's offcut is drawn on that board (its *later* pieces, dashed in the run, named for their elevation in the DXF). Each elevation ends with a **rack**: the offcuts carried on (from every board so far when passing on, else its own), none under 150 mm, each marked with the elevation that cuts from it next. The plan reports `from_offcuts` and `saved`; the readout says how many boards the offcuts saved. | `cladding_nesting.chain_plan`, `_seeded`, `_together_with_offcuts`, `keep_together` |
| Plank cuts kept together | Fewest lengths first, then the pieces cut from each length **as close together on the wall as they can be**, so they go up side by side: two of the same length abutting, or one course on the next, and an offcut that can't make another of the same goes to the nearest piece it fits. After the packing, the full search (cutting plan, cutting run, Optimise boards) moves one piece to another length, or swaps two between lengths, wherever that shortens the chain linking each length's pieces edge to edge (a minimum spanning tree: abutting pieces add nothing). It looks at the 12 nearest pieces within 4 m, and a piece left stranded with nothing nearby looks at every length for the closest place there's room. It never adds a length or overfills one; a move that empties a length saves it. Deterministic and bounded. The plan reports the total as `spread` (m). The quick check skips it: the count is the same. | `cladding_nesting.keep_together`, `cladding_nesting._spread` |
| Plank corner trim | With planks, **Profile** at a right-angled external chain corner is a **square timber corner trim** (Fig. 8 of the timber cladding guides): in plan an L 50 × 50 with 30 mm legs, its back on the batten face like the planks', so it stands 30 − plank thickness proud of them, the 20 × 20 notch inside the L wrapping the corner of the battens. Each face's planks stop 20 mm and the chain's profile gap short of where the batten faces meet, square, and butt it; vertical planks reaching into its place are cut back or dropped, and past the wall's corner line they are trimmed in height only, so they run out to it. Behind it goes the same solid L of timber as the panel profile's (two batten widths on the face before the corner, one behind). With vertical battens the corner battens become the L; with horizontal battens (vertical planks) the L stands up the corner in the batten layer and the battens stop against it, so the trim and the last plank's edge are fixed all the way up, and being vertical it does not dam the cavity. IFC4X3 `IfcMember` *Corner trim*, solid timber, with a `CladForge_CornerTrim` set (size, leg, length); the DXF elevations show its 50 mm face. Plank reveals stay mitred. | `cladding_corners.trim_section`, `cladding_geometry._profiled_corners` |
| Coursing at openings [OPEN] | Straight through and cut. Coursing never resets at a reveal. | `cladding_booleans.apply_boolean_ops` |
| 2D elevation | A view, not a mode of the model: nothing is rebuilt on the way in or out. The perspective camera turns on a sphere about the target (a slerp, eased over 600 ms) so it swings round the model rather than through it, then an orthographic camera takes over with the frustum the perspective one saw at that distance, so the swap does not jump. Everything that picks or projects goes through the active camera. Flat, dimensions are HTML labels placed from projected points each frame, so they stay readable at any zoom; in 3D they stay sprites, and only courses and rows open the course dialog. Every dimension carries its kind and value (and a row its index, a cut its bay), or a *lock* naming what drives it. | `view2d.enter2D`, `exit2D`, `activeCamera`, `dims2d.applyDim`, `cladding_geometry._dim` |
| Panel rows | Panel courses are a list of **row heights**, bottom row first, carried on the elevation (`panel_rows`). With no list every row is the panel height, as before. Rows stack up with the joint gap between them from the chain's course datum (the lowest start in the chain until the first row or course edit locks it to that face's base, as for plank courses; selecting a face never moves it), so seams run level round the corners: a face that starts higher cuts the row at its base, and one that starts lower carries on down at the panel height. Once the datum is locked, rows cut at the base or below the datum are dimensioned read-only on that face; before that, a bottom row cut at a higher face's base can still be typed over, since that first edit is what locks the datum to the face; once the list runs out they carry on at the panel height, and the top row takes whatever is left. Each listed row is held to 150–3000 mm: rows down to 150 are deliberate tiers, and a row asked for below that, or a closing row under 100 mm, is flagged in the checks. Every row gets its own dimension up the right-hand side, labelled by row (*R1* when it is the only row), which can be typed over for this elevation or the whole chain. **The top row is a row, not a cut**: its dimension opens the row box like any other, with **Split row** (which types a new row below and leaves the remainder on top, so a one-row face can gain rows) and **Merge with row above** (not offered, as nothing is above it); only its height is not typed, because it is whatever is left, and the box says so. The word *Cut* is kept for widths cut at the ends of a run, the left and right closing cuts; a row cut at a face's base is labelled by its row and read-only, saying why. Noggins go behind every row joint when counter-battens are on. Panels are named by row, *Panel R2-3*, and the DXF dimensions every row and lists the heights in its schedule. | `cladding_primitives.panel_rows`, `cladding_geometry._panels`, `cladding_checks.check_rules`, `wizard.lockForRow` |
| Panel joints | Row heights and *Merge with row above* act on a whole row; a single horizontal joint can be dissolved bay by bay. In the 2D view each horizontal joint between two rows is drawn as one segment per bay (only where it crosses the clad area), highlighted on hover. **Click dissolves it**: the panels above and below in that bay become one panel of *lower + gap + upper*; clicking the dashed segment again puts the joint back. Several in a column give one tall panel for the bay; every merge is within one bay, so it is always a rectangle. A merge the stock board cannot take either way round is refused, saying the size it would have been against the board. Noggins (with counter-battens on) go only behind joints that remain. Merged panels are named for both cells, *Panel R1-7..R2-7*, and the 3D preview, IFC4X3, DXF elevation, waste readout and cutting plan all use the actual panels. Dissolved joints are stored per elevation beside `panel_rows` as `panel_joints`, each its **(row, bay)** pair (drawn indices from 0) plus the bay's span and the joint's level when clicked: when the grid changes underneath (row heights, panel width, offset, setting-out), the ones still in the same place are kept and the rest dropped, with one status message saying how many, so a joint is never applied to another panel. Vertical joints, splitting single panels, multi-select and undo are not in this change. | `cladding_geometry._hjoints`, `dims2d.toggleJoint`, `app.pruneJoints` |
| Saved state | **Save state (XML)** writes the whole session, **Load state** restores it: every chain (built or not, its levels, edge settings and datum), each elevation's place in its chain, corners and details, clips, offset, rows, dissolved joints, opening details and abutment choices, every panel and buildup setting, and the model's offset and project names. The **surfaces are stored as extracted** (frame, outline, openings, abutments), so a state loads without the IFC and without extracting again, and builds exactly the same cladding; the IFC elements the faces were picked from are listed for reference. Loaded onto an open model, the surfaces move onto its offset; with none open, the saved offset comes back so exports still land on the host model's coordinates. A loaded elevation cannot take more faces (its picks are not in the model): new faces start a new elevation, which links into the chain as usual. The XML types every value, so it reads back exactly; a file that is not a CladForge state, or is from a newer version, is refused with the reason. | `cladding_state.py`, `app.saveState`, `app.restoreState` |
| Board size | The stock board the panels are cut from, as supplied: **1250 × 2500 mm** by default, set in the Panel section and kept separate from the cut size, so changing the panel never loses the board. The panel's own width and row heights are labelled **cut width** and **cut heights**. **Edge trim** (0 by default, up to 50 mm) comes off every board edge before any piece is cut, to square up factory edges: pieces come from the board less twice the trim each way, and waste is still judged against the whole board. A cut panel larger than what is left, either way round, fails the Board size check and is left out of the packing, named on the cutting plan; a dissolved joint whose panel would not fit is refused. **Rotation allowed** (on by default) lets a piece be cut from the board turned 90°. | `cladding_constants._parse`, `cladding_checks` |
| Kerf | 3 mm per saw cut by default, editable (0–10). It is charged between pieces, never at the board edges: 4 × 622 + 3 × 3 = 2497 fits a 2500 board, 4 × 625 + 3 × 3 = 2509 does not. Each piece and the board are inflated by the kerf while packing, which gives exactly that. | `cladding_nesting._pack` |
| Packing method | 2D **guillotine** bin packing, because a panel saw cuts edge to edge (Jylänki, *A Thousand Ways to Pack the Bin*, 2010, guillotine section). Pieces are every panel of every built chain, **each elevation packed on its own**, so every board is cut for one elevation and the plan reads elevation by elevation: closing cuts, rows of different heights, merged panels, and panels notched round openings, which pack as their bounding rectangle (they are cut from one) while only their net area counts as used. Each strategy is a sort order (area, longest side, height, width, perimeter, shortest side, all descending, ties by name) × a choice of free space (Jylänki's six: best and worst area, short side and long side fit) × a split rule (his six: shorter or longer leftover axis, minimise or maximise area, shorter or longer axis). The **quick check** runs the original 16 after every rebuild in a few milliseconds. The **full search** (**Optimise boards**, and always for the cutting plan) runs all 216, then shuffled piece orders under the three best rules, then an **empty-a-board pass** that tries to fit the least-used board's pieces into the others' leftover space. Its effort is a fixed count of checks rather than a time, about 2 s in the browser, so the same job always gives the same layout on any machine; on mixed test jobs it saved a board in about a third of cases. Jylänki's free-space merge is left out: it can give layouts a panel saw cannot cut. Offcuts stay on their board for later pieces. It stops early at the lower bound, `ceil(net area / usable board area)`. At most 400 pieces are packed; beyond that the readout and the plan say it is capped. | `cladding_nesting.pack`, `cladding_nesting.chain_plan` |
| Waste readout | Top right of the view, for the whole job (every built chain): clad area (m², net of openings and splash zones), boards used against the lower bound (with *+n kept per elevation* where packing the whole job together would take fewer), and **waste = 1 − net panel area / (boards × board area)**, red above 5 %. It says whether the figure is the **quick check**, refreshed after each rebuild, or **optimised**, after **Optimise boards**; the next rebuild goes back to the quick check. Under it, **Divides the board** lists the cut widths and heights that use a board with nothing over, kerf and edge trim allowed for (1250 · 623 · 414 across and 2500 · 1248 · 831 · 622 · 497 along a 1250 × 2500 board with a 3 mm kerf). Panel mode only. While the readout shows, the 2D view keeps its strip clear, so no dimension lands under it. | `app.renderReadout`, `app.optimiseBoards` |
| Board use in 2D | Flat, every panel is tinted by how well the board it is cut from is used, from the last packing: **green** 90 % and over, **amber** 75–90 %, **red** under that. The red ones are where the design is costing boards: move a row height, a panel width or a joint towards the sizes that divide the board and watch them turn. In 3D the panels keep their own colour. | `dims2d.tintPanels` |
| Packing per elevation | Each elevation is packed on its own, in chain order, so every board is cut for one elevation: on site an offcut never travels round the building, and the cutting run and the plan read one elevation at a time. Within an elevation the boards are put in **wall order**, by their leftmost piece (left to right as seen from outside, then bottom up), and each board's pieces likewise; boards are numbered through the job in that order. Packing the whole job together could pass offcuts between elevations and sometimes save boards, so its count (quick check) is kept: the readout shows *+n kept per elevation* when it would be fewer, and the cutting plan says so. The full search's effort is shared between the elevations by piece count, so the job stays bounded. | `cladding_nesting.chain_plan`, `cladding_nesting.wall_order` |
| Slivers under a splash zone | Below each splash zone, across its width, any part of the face thinner than the shortest row (150 mm) is not clad. Between the zone and the roof or slab that sets it (its section taken out as a penetration) the face can leave a strip a few tens of mm high, or a thin wedge under a pitched line, and every board or plank crossing it would be cut to a sliver. Only height counts: a narrow face (a return in a chain) or a narrow pier between windows is not a sliver. They are found by opening the part of the clad area below the band (shrink by half a row, grow back, square-cornered) with it stretched a thousandfold sideways, so nothing is narrow across and heights are unchanged: a strip at least a row high, the wall under a balcony, narrow faces and piers and the cladding round a window all come back unchanged. On anything unexpected the step leaves the clad area as it was rather than fail the build. Setting-out and the export clip share the one clad area, so both leave them out. | `cladding_booleans.clip_region` |
| Floor grid | The 40 m floor grid toggles with **G** or *Grid* in the legend; it starts on. | `viewer.setLayerVisible` |
| Cutting run | A view of the cutting plan, not a new calculation: the same plan as the download (export geometry, full search). Split screen, 3D left and the plan right, the board strip drawn in board millimetres as the DXF draws it, plank lengths standing up. One pastel hue per board (on the schedule strip at half the saturation, so only the piece in focus is strong), stepped round the colour wheel by the golden angle so neighbouring boards never look alike, shared by the board's pieces in both views; offcuts stay grey and hatched. The piece in focus goes full colour with a glowing edge (two translucent bands just outside its outline, drawn over its neighbours: no post-processing), the rest of its board a little stronger, everything else pastel; board numbers are on every piece, so colour is never the only link. While the run is open the pieces it colours are drawn solid: the cladding is otherwise see-through, and see-through surfaces sorted by their centres could, from far off, draw the translucent layers behind over the boards and wash their colours out. A board number too small on screen to read (under 12 px tall) fades out, gone by 7 px, rather than shrinking to a dark speck. The camera frames every piece cut from the board in focus at once (their bounding sphere in the narrower field of view, never closer than 8 m), so it holds still while a board's pieces are stepped through and moves with the next board; anything standing in front of them (a roof, a wing, another face's cladding) turns the view away: from the square-on direction it tries turning up to 75° either way and looking from higher up, nearest first, casting sight lines from points on every focal piece out to the eye against the host model and the rest of the cladding as shown (hidden elements and switched-off layers do not count; cast outward, they also catch a solid the eye would sit inside), and takes the first with none blocked, coming closer in if none is clear, else the least blocked; the picked-face overlays and outlines are hidden while the run is open; it turns on the sphere round its target, as the 2D transition does, so it swings round the chain's corners. On a vertical plank the number runs up the plank. The run owns the keyboard while open, nothing is picked or edited, and a rebuild closes it. | `cutrun.js` |
| Cutting run: inspected boards | Once the run has moved past a board, its pieces on the wall are **hatched with broad black diagonals over their own colour**, so a glance shows nothing on the elevation was missed or counted twice. Inspected means before the board in focus in the run's order, so stepping back un-hatches them. Each piece carries a hatch skin built once when the run opens: a copy of its own geometry (so a mitred or sloped cut is hatched to its edge) drawn just in front of it with a see-through texture of black 45° lines 110 mm apart, square to the wall; playing only shows and hides them. | `cutrun.js` (`buildHatch`, `paintWall`) |
| Cutting run racks | Each elevation's run ends with its rack: its offcuts gathered at the end of its row on the strip (side by side at the boards' scale, in the hue of the board each came from, hatched, labelled with board and size, and the elevation that uses it next) and laid flat on the ground in front of its cladding in 3D (rows working outwards from the wall, 900 mm off the face), the camera looking down on them. With offcuts passed on, the next elevation starts with what it cuts from them, an offcut at a time: the rack is carried round in front of it, and the offcut glows there and on the old rack in the strip while its pieces light up on the wall and on the offcut drawn at the start of the row (numbered 12·o1, 12·o2: board 12's offcut). On the strip each offcut **flies** there first: at the first piece cut from it, it lifts off the rack at the end of the earlier row, glowing, and flies to the start of the row using it (1.1 s at 1×), the strip scrolling along with it, and lands as the offcut its pieces are drawn on; offcuts already taken are left on the rack as dashed outlines. Not with reduced motion. | `cutrun.js` (`runSteps`, `showRack`, `buildRack`, `renderStrip`, `flyOffcut`) |
| Cutting plan | **Download cutting plan (DXF)**, beside the DXF button: each elevation packed on its own by the full search, one row per elevation with its board count and waste, boards to scale left to right in wall order and numbered through the job, each piece in place with its panel name and cut size, rotated pieces marked, offcuts and edge trim hatched, waste per board and in total, and the strategy that won. Its header reads *"Cutting plan for setting-out. Not a quantity take-off for pricing."* | `cladding_nesting.plan_dxf` |
| Horizontal panel joints [ASSUMED] | Open joints at the gap. Noggins behind them only where counter-battens are on: a noggin between vertical battens sits on the drainage plane and dams it, so by default the seams are left to a proprietary horizontal profile and the checks say so. | `cladding_geometry._panels` |
| Batten orientation | Derived, never a free choice. Horizontal planks → vertical battens. Vertical planks → horizontal battens on vertical counter-battens. Panels → vertical battens, with seam noggins only when counter-battens are on. The only override is *Counter-battens: force on/off*, and the checks flag the buildups that then fail to drain. | `cladding_constants._parse`, `cladding_preview.check_rules` |
| Cutting run focus | Within a board the focus builds up, piece by piece: at a board's third piece its first three are all lit, full colour with a glowing edge as a group on the wall and on the schedule strip, the piece just added outlined thickest on the strip; the rest of the board is a little stronger than the other boards, and the next board starts again from its first piece. | `cutrun.paintWall`, `cutrun.paintStrip` |
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
| Board size | every cut panel, and every merged panel, fits the board less its edge trim, either way round if rotation is allowed | | a cut size larger than that |
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
 "frame": {"origin": [x,y,z], "u": [ux,uy,uz], "v": [vx,vy,vz], "n": [nx,ny,nz]},
 "color": "#..", "opacity": 1.0, "name": "...", "ifc_type": "batten", "elevation": "Elevation A"}
```

Coordinates are IFC millimetres, Z-up. The viewer swaps to Three.js Y-up.

The frame is general, as in FallWright: a local point is origin + u·U + v·V + d·N with
v = n × u. For a wall frame that is world Z exactly, so walls are unchanged, and a frame
with no v reads as world Z. A frame can also lie flat (a head lining faces down). The
extractor's `fit_plane`, `make_frame` and `_to_local` take a mode; "wall" is the
default and behaves as before. The IFC placement (axis n, reference u) gives the profile
y = n × u = v, so it needs no change.

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

**Saved state (XML).** `<CladForgeState format="cladforge-state" version="1">`, one element per value with its type (`t="num"`, `"str"`, `"bool"`, `"null"`, `"object"`, `"list"`, `"nums"`, `"points"`): chains hold `<elevation>`s, each with its `<surface>` (frame, polygons as `x,y x,y` point lists, abutments). A session file for CladForge, not an exchange format.

**DXF.** R12 (AC1009). One flattened elevation per region, moved to origin,
laid out left to right. Layers `WALL`, `OPENING`, `SPLASH_ZONE`, `SHEATHING`,
`INSULATION`, `COUNTER_BATTEN`, `BATTEN`, `CLADDING`, `DIMS`, `NOTES`.
Dimensions: batten centres, splash zone, closing cut, course height, overall
size. A setting-out schedule under each elevation and the disclaimer block in
the title area.

**Cutting plan (DXF).** R12, layers `BOARD`, `PIECE`, `OFFCUT` (hatched at 45°, since R12
has no hatch entity) and `NOTES`. Its scope is stated in the header: *Cutting plan for
setting-out. Not a quantity take-off for pricing.*

## File budget

| File | Lines | Budget |
|---|---|---|
| fabric_extract.py | 590 | 400 |
| cladding_constants.py | 104 | 80 |
| cladding_geometry.py | 725 | 400 |
| cladding_primitives.py | 446 | 300 |
| cladding_edges.py | 358 | 300 |
| cladding_corners.py | 126 | 300 |
| cladding_nesting.py | 346 | 300 |
| cladding_state.py | 150 | 300 |
| cladding_booleans.py | 222 | 200 |
| cladding_preview.py | 64 | 100 |
| ifc_generator.py | 418 | 400 |
| dxf_generator.py | 258 | 500 |
| app.py | 78 | 150 |
| templates/index.html | 307 | 500 |

`fabric_extract.py`, `cladding_constants.py`, `cladding_geometry.py`, `cladding_primitives.py`,
`cladding_edges.py`, `cladding_nesting.py`, `cladding_booleans.py` and `ifc_generator.py` are over
their budgets, `cladding_geometry.py` furthest (725 of 400). Splitting the region clean-up
(notches, seeded patches) out of the extractor, and the opening extras (closers, linings,
frames) and corner timbers out of the geometry, would bring the worst back inside; the edge
rules already live in their own module, `cladding_edges.py`.

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
exports and the cutting plan, checking the waste readout turns red above 5 %, that Optimise boards never
uses more boards than the quick check, and that panels are tinted by board use in 2D only. The cutting run is
opened on every chain in panels: one strip row per elevation, every board cut for one elevation, every piece found
on the wall, the same piece glowing on its board and on the wall in its board's colour with the camera on it, and
playback, keys and closing (colours restored, the view full width again). It also checks that picking alone generates nothing. `VENDOR_DIR` is only needed where the CDNs are
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
- One corner profile is modelled, the outer corner profile (Rockpanel Profile D type),
  at right-angled external corners of panels. Other profiles and corner beads are not;
  Square stands in for them. Its section is simplified: where the flanges leave the nose
  the model overlaps each panel's back corner by about 1 mm, and at the top corners of
  an opening with profiled jambs and head the jamb and head profiles stop short of each
  other rather than being mitred together.
- Openings get a cavity closer only at the jambs, and linings at the jambs and head;
  sill linings, cills and flashings are not modelled. The window frame is taken to sit at
  the wall face, since only the structural opening is known.
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
- No cavity barriers, no fixings, no trims or flashings.
- No georeferencing is added; coordinates stay in the host model's system.

## Licence

MIT. Copyright 2026 Jake White Architecture. See `NOTICE` for the
StairSmith and SunForm attribution.
