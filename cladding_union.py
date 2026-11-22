"""One solid per element for the IFC.

A standing seam tray goes out as one element with its seam folds, its turned-up ends and
the drip its end is turned into; a flashing with the seam it turns up into and the end
pieces cut at a corner; a ridge cap with its stop ends (ifc_generator.tray_owners). Each
of those pieces is its own prism, and written as they are the element would carry a
stack of separate, overlapping solids. Here they are unioned into one closed solid first
(union_pieces), so the element is one sheet of metal, and written as one brep whose faces
are the union's coplanar triangles merged back into polygons (faces).

The boolean is Manifold's: the manifold3d package where it is installed (the tests, a
server), and in the browser the manifold-3d WebAssembly module, which the page loads
before an IFC export and offers as globalThis._cfUnion (static/app.js). Where neither is
there, or a union fails, None comes back and the pieces are written as they were.
"""
import math
import sys

from cladding_constants import frame_to_world
from cladding_primitives import ring_at


def piece_mesh(mesh):
    """A prism (a profile extruded along its frame's n, its ends sheared by ring_at) as a
    closed triangle mesh: (vertices [(x, y, z)], triangles [(i, j, k)], outward and anti-
    clockwise from outside). None for one this cannot mesh: a ring with holes, a custom
    profile's swept section, a profile too small to close."""
    if mesh.get("holes") or mesh.get("section_ring"):
        return None
    frame = mesh["frame"]
    near = float(mesh["depth"])
    far = near + float(mesh["thickness"])
    lo2 = ring_at(mesh, 0, near)
    hi2 = ring_at(mesh, 0, far)
    n = len(lo2)
    if n < 3 or len(hi2) != n or far - near <= 1e-6:
        return None
    caps = _ear_clip(lo2)
    if caps is None:
        return None
    verts = [tuple(frame_to_world(frame, u, v, near)) for u, v in lo2]
    verts += [tuple(frame_to_world(frame, u, v, far)) for u, v in hi2]
    tris = []
    for i in range(n):                                  # the sides: a quad per edge of the ring
        j = (i + 1) % n
        tris += [(i, j, n + j), (i, n + j, n + i)]
    for a, b, c in caps:                                # the two ends, the far one as the ring runs
        tris += [(a, c, b), (n + a, n + b, n + c)]
    if _volume(verts, tris) < 0.0:                      # a clockwise ring, or a mirrored frame
        tris = [(a, c, b) for a, b, c in tris]
    return verts, tris


def _ear_clip(ring):
    """Triangles [(i, j, k)] covering a simple polygon *ring* [(u, v)], each wound as the
    ring is. None if it cannot be cut into ears (a ring that crosses itself)."""
    pts = [(float(u), float(v)) for u, v in ring]
    n = len(pts)
    area = sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n)) / 2.0
    if abs(area) < 1e-9:
        return None
    turn = 1.0 if area > 0 else -1.0
    idx = list(range(n))
    out = []
    cross = lambda o, a, b: (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])   # noqa: E731

    def inside(p, a, b, c):
        """In the triangle or on its edge (a corner of the ring lying on the ear's long side
        blocks it as surely as one inside), but not at one of its own corners."""
        if p == a or p == b or p == c:
            return False
        d1, d2, d3 = cross(a, b, p) * turn, cross(b, c, p) * turn, cross(c, a, p) * turn
        return d1 >= -1e-9 and d2 >= -1e-9 and d3 >= -1e-9

    guard = 0
    while len(idx) > 3 and guard < 4 * n * n:
        guard += 1
        m = len(idx)
        cut = False
        for k in range(m):
            ia, ib, ic = idx[k - 1], idx[k], idx[(k + 1) % m]
            a, b, c = pts[ia], pts[ib], pts[ic]
            if cross(a, b, c) * turn <= 1e-9:
                continue                                # reflex or flat: not an ear
            if any(inside(pts[j], a, b, c) for j in idx if j not in (ia, ib, ic)):
                continue
            out.append((ia, ib, ic))
            idx.pop(k)
            cut = True
            break
        if not cut:                                     # only flat corners left (a doubled vertex): cut one anyway
            k = min(range(m), key=lambda k: abs(cross(pts[idx[k - 1]], pts[idx[k]], pts[idx[(k + 1) % m]])))
            if cross(pts[idx[k - 1]], pts[idx[k]], pts[idx[(k + 1) % m]]) * turn < -1e-6:
                return None
            out.append((idx[k - 1], idx[k], idx[(k + 1) % m]))
            idx.pop(k)
    if len(idx) == 3:
        out.append(tuple(idx))
    covered = sum(cross(pts[a], pts[b], pts[c]) for a, b, c in out) / 2.0
    if abs(covered - area) > 1e-6 * max(1.0, abs(area)) or any(cross(pts[a], pts[b], pts[c]) * turn < -1e-9 for a, b, c in out):
        return None                                     # the ears do not tile the ring: leave the piece unmeshed
    return out


def _volume(verts, tris):
    s = 0.0
    for a, b, c in tris:
        (ax, ay, az), (bx, by, bz), (cx, cy, cz) = verts[a], verts[b], verts[c]
        s += ax * (by * cz - bz * cy) - ay * (bx * cz - bz * cx) + az * (bx * cy - by * cx)
    return s / 6.0


def union_pieces(pieces):
    """The union of closed triangle meshes [(vertices, triangles)] as one: (vertices,
    triangles), or None where there is no Manifold to do it or it fails. Worked about the
    first vertex, so single precision is fine whatever the model's coordinates."""
    pieces = [p for p in pieces if p]
    if not pieces:
        return None
    ox, oy, oz = pieces[0][0][0]
    local = [([(x - ox, y - oy, z - oz) for x, y, z in v], t) for v, t in pieces]
    try:
        got = _native(local) if not _in_browser() else _browser(local)
    except Exception:   # noqa: BLE001 — no union: the pieces go out as they are
        return None
    if not got:
        return None
    verts, tris = got
    if not tris:
        return None
    return [(x + ox, y + oy, z + oz) for x, y, z in verts], tris


def _in_browser():
    return sys.platform == "emscripten"


def _native(pieces):
    import numpy as np
    from manifold3d import Manifold, Mesh, OpType, Error
    made = []
    for verts, tris in pieces:
        mesh = Mesh(vert_properties=np.asarray(verts, dtype=np.float32), tri_verts=np.asarray(tris, dtype=np.uint32))
        mesh.merge()
        solid = Manifold(mesh)
        if solid.status() != Error.NoError:
            return None
        if not solid.is_empty():
            made.append(solid)
    if not made:
        return None
    out = Manifold.batch_boolean(made, OpType.Add).to_mesh()
    v = np.asarray(out.vert_properties)[:, :3]
    return [tuple(float(c) for c in q) for q in v], [tuple(int(i) for i in t) for t in np.asarray(out.tri_verts)]


def _browser(pieces):
    import js                                           # noqa: F401 — Pyodide's bridge to the page
    from pyodide.ffi import to_js
    fn = getattr(js.globalThis, "_cfUnion", None)
    if fn is None:
        return None
    payload = [[[c for q in v for c in q], [i for t in tr for i in t]] for v, tr in pieces]
    res = fn(to_js(payload))
    if res is None:
        return None
    flat_v = list(res.v.to_py())
    flat_t = list(res.t.to_py())
    k = int(res.np)
    verts = [(float(flat_v[i]), float(flat_v[i + 1]), float(flat_v[i + 2])) for i in range(0, len(flat_v), k)]
    tris = [(int(flat_t[i]), int(flat_t[i + 1]), int(flat_t[i + 2])) for i in range(0, len(flat_t), 3)]
    return verts, tris


def faces(verts, tris, tol=1e-3):
    """A closed triangle mesh's faces as polygons: [(outer loop, [inner loops])] of vertex
    indices, each the edge-connected triangles that lie in one plane facing one way, their
    outline found from the edges only one of them has. Where an outline will not close
    into simple loops (two corners touching), its triangles are kept as they are."""
    norms, offs = [], []
    for a, b, c in tris:
        p, q, r = verts[a], verts[b], verts[c]
        u = (q[0] - p[0], q[1] - p[1], q[2] - p[2])
        w = (r[0] - p[0], r[1] - p[1], r[2] - p[2])
        nx, ny, nz = u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0]
        size = math.sqrt(nx * nx + ny * ny + nz * nz)
        if size < 1e-12:
            norms.append(None)
            offs.append(0.0)
            continue
        nx, ny, nz = nx / size, ny / size, nz / size
        norms.append((nx, ny, nz))
        offs.append(nx * p[0] + ny * p[1] + nz * p[2])
    parent = list(range(len(tris)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    by_edge = {}
    for i, (a, b, c) in enumerate(tris):
        for e in ((a, b), (b, c), (c, a)):
            by_edge[e] = i
    for (a, b), i in by_edge.items():
        j = by_edge.get((b, a))
        if j is None or j <= i or norms[i] is None or norms[j] is None:
            continue
        ni, nj = norms[i], norms[j]
        if ni[0] * nj[0] + ni[1] * nj[1] + ni[2] * nj[2] > 1.0 - 1e-6 and abs(offs[i] - offs[j]) < tol:
            parent[find(i)] = find(j)
    groups = {}
    for i in range(len(tris)):
        groups.setdefault(find(i), []).append(i)
    out = []
    for members in groups.values():
        if len(members) == 1:
            out.append((list(tris[members[0]]), []))
            continue
        edges = set()
        for i in members:
            a, b, c = tris[i]
            edges.update(((a, b), (b, c), (c, a)))
        rim = [e for e in edges if (e[1], e[0]) not in edges]
        nxt = {}
        clean = True
        for a, b in rim:
            if a in nxt:
                clean = False
                break
            nxt[a] = b
        loops = []
        if clean:
            seen = set()
            for start in nxt:
                if start in seen:
                    continue
                loop, at = [], start
                while at not in seen:
                    seen.add(at)
                    loop.append(at)
                    at = nxt.get(at)
                    if at is None:
                        clean = False
                        break
                if not clean or at != start:
                    clean = False
                    break
                loops.append(loop)
        if not clean or not loops:
            out += [(list(tris[i]), []) for i in members]
            continue
        n = norms[members[0]] or (0.0, 0.0, 1.0)

        def area(loop):
            s = [0.0, 0.0, 0.0]
            for k in range(len(loop)):
                p, q = verts[loop[k]], verts[loop[(k + 1) % len(loop)]]
                s[0] += p[1] * q[2] - p[2] * q[1]
                s[1] += p[2] * q[0] - p[0] * q[2]
                s[2] += p[0] * q[1] - p[1] * q[0]
            return (s[0] * n[0] + s[1] * n[1] + s[2] * n[2]) / 2.0

        loops.sort(key=area, reverse=True)
        outer, inner = loops[0], loops[1:]
        if area(outer) <= 0.0 or any(area(h) >= 0.0 for h in inner):
            out += [(list(tris[i]), []) for i in members]      # more than one outline facing out: keep its triangles
            continue
        out.append((outer, inner))
    return out
