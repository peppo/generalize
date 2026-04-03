"""
Build a TopoLayer from a QgsVectorLayer and reconstruct QgsFeatures from it.

Public API
----------
snap_to_self(layer, tolerance)   -> QgsVectorLayer   (topological pre-processing)
build(layer)                     -> TopoLayer
dissolve_small_rings(topo)       -> (n_parts, n_holes)
repair_ring_inversions(topo, original_edge_coords) -> int
to_qgs_features(topo)            -> list[QgsFeature]

Algorithm overview
------------------
1. Extract every polygon ring from every feature (outer rings and holes).
2. Build a coordinate → {ring_ids} index so we know which rings share a point.
3. For each ring, walk its coordinates and detect positions where the set of
   "sharing partners" changes.  These positions are arc boundaries (junctions).
4. Split every ring into arcs at those boundaries.
5. Canonicalise each arc (normalise direction) and look it up in a dict.
   - First time seen  → create a new TopoEdge.
   - Already present  → the arc is shared; record forward/reverse flag.
6. Assign left/right polygon ownership on every TopoEdge.
7. Assemble TopoPolygon objects from the arc references.

Performance notes
-----------------
* ``snap_tolerance=0`` (the default) uses raw floating-point tuples as dict
  keys throughout — no rounding, no function-call overhead per coordinate.
  For shapefiles coordinates on a shared border are bit-for-bit identical,
  so exact matching is sufficient.
* The sharing-set computation avoids allocating new ``frozenset`` / ``set``
  objects for the common cases (coord not shared, or shared with exactly one
  other ring).  Only 3-way junctions trigger a set allocation.
* Geometry reconstruction uses WKB binary encoding (``iter_coords_numpy`` +
  ``struct.pack``) rather than creating one ``QgsPointXY`` per output vertex.
"""
from __future__ import annotations

import struct
from collections import defaultdict

import numpy as np
from qgis.PyQt.QtCore import QByteArray
from qgis.core import QgsFeature, QgsGeometry, QgsVectorLayer

from .topology import TopoEdge, TopoLayer, TopoNode, TopoPolygon, TopoRing


# ---------------------------------------------------------------------------
# Pre-processing: snap shared borders to make coordinates bit-for-bit identical
# ---------------------------------------------------------------------------

def remove_collinear_vertices(
    layer: QgsVectorLayer, tolerance: float = 1e-8
) -> QgsVectorLayer:
    """
    Remove vertices whose perpendicular distance to the line connecting their
    two neighbours is less than ``tolerance`` (map units).

    Such vertices are geometrically redundant (180° interior angle) but prevent
    shared-border detection when adjacent polygons were digitised with different
    numbers of intermediate points on the same straight boundary segment.
    Removing them is the minimal, non-destructive way to normalise the data:
    no coordinates are moved and no new vertices are introduced.

    Parameters
    ----------
    layer     : input polygon layer (read-only; a new in-memory layer is returned)
    tolerance : perpendicular-distance threshold (map units).  Vertices within
                this distance of the straight line through their neighbours are
                removed.  Default 1e-8 handles exact collinearity with typical
                floating-point noise in metre-based CRS data.

    Returns
    -------
    A new in-memory QgsVectorLayer with collinear vertices removed.
    """
    from qgis.core import QgsFeature, QgsGeometry, QgsPointXY  # noqa: PLC0415

    out = QgsVectorLayer(
        f'Polygon?crs={layer.crs().authid()}',
        layer.name() + '_clean',
        'memory',
    )
    out.setCrs(layer.crs())
    out.dataProvider().addAttributes(layer.fields())
    out.updateFields()

    for feat in layer.getFeatures():
        geom = feat.geometry()
        is_multi = geom.isMultipart()
        parts = geom.asMultiPolygon() if is_multi else [geom.asPolygon()]

        new_parts = []
        for polygon in parts:
            new_rings = []
            for ring_pts in polygon:
                # ring_pts includes the closing duplicate; strip it.
                xy = np.array(
                    [(p.x(), p.y()) for p in ring_pts[:-1]], dtype=np.float64
                )
                n = len(xy)
                if n < 3:
                    new_rings.append(ring_pts)
                    continue

                keep = _collinear_mask(xy, tolerance)
                if keep.sum() < 3:
                    keep[:] = True  # ring too small to simplify

                kept = xy[keep]
                pts = [QgsPointXY(x, y) for x, y in kept]
                pts.append(pts[0])   # re-add closing duplicate
                new_rings.append(pts)
            new_parts.append(new_rings)

        if is_multi:
            new_geom = QgsGeometry.fromMultiPolygonXY(new_parts)
        else:
            new_geom = QgsGeometry.fromPolygonXY(new_parts[0])

        new_feat = QgsFeature(feat)   # copy: preserves id and attributes
        new_feat.setGeometry(new_geom)
        out.dataProvider().addFeature(new_feat)

    return out


def _collinear_mask(xy: np.ndarray, tolerance: float) -> np.ndarray:
    """
    Boolean mask (n,): True = keep, False = collinear with neighbours.

    Uses a single vectorised pass: each vertex is tested against its original
    neighbours, not the post-removal ones.  A second call handles the rare case
    where removal exposes new collinear vertices.
    """
    prev_ = np.roll(xy,  1, axis=0)   # xy[i-1]
    next_ = np.roll(xy, -1, axis=0)   # xy[i+1]

    AC = next_ - prev_                              # (n, 2)
    AC_len = np.hypot(AC[:, 0], AC[:, 1])           # (n,)
    AB = xy - prev_                                 # (n, 2)
    cross = AB[:, 0] * AC[:, 1] - AB[:, 1] * AC[:, 0]  # (n,)

    # Perpendicular distance from vertex to line prev→next.
    safe_len = np.where(AC_len > 0, AC_len, 1.0)
    perp_dist = np.abs(cross) / safe_len            # (n,)

    return perp_dist >= tolerance


def snap_to_self(layer: QgsVectorLayer, tolerance: float = 1.0) -> QgsVectorLayer:
    """
    Snap every polygon's boundary to its neighbours so that shared borders
    have bit-for-bit identical coordinates.

    This is a necessary pre-processing step when the source data was digitised
    independently for each feature and adjacent polygon boundaries have small
    but non-zero geometric gaps (e.g. cadastral data surveyed at different
    times).  After snapping, ``build()`` with ``snap_tolerance=0`` will
    correctly detect all shared edges.

    Internally this calls QGIS's *Snap geometries to layer* algorithm
    (``native:snapgeometries``) with the layer snapped to itself.  The
    *prefer aligning nodes* behaviour is used so that existing vertices are
    moved to coincide with nearby vertices in neighbouring polygons rather
    than inserting extra vertices.

    Parameters
    ----------
    layer     : input polygon layer (read-only; a new in-memory layer is returned)
    tolerance : maximum distance in map units within which vertices are snapped
                together.  Use a value slightly larger than the largest gap in
                the data.  A good starting point for cadastral data is 1.0 m.

    Returns
    -------
    A new in-memory QgsVectorLayer with snapped geometries and the same
    attributes as the input.
    """
    import processing  # available inside QGIS / qgis.core environment

    result = processing.run(
        'native:snapgeometries',
        {
            'INPUT':            layer,
            'REFERENCE_LAYER':  layer,
            'TOLERANCE':        tolerance,
            'BEHAVIOR':         1,       # prefer aligning nodes
            'OUTPUT':           'memory:',
        },
    )
    snapped: QgsVectorLayer = result['OUTPUT']
    snapped.setName(layer.name() + '_snapped')
    snapped.setCrs(layer.crs())
    return snapped


# ---------------------------------------------------------------------------
# WKB helpers (used by to_qgs_features)
# ---------------------------------------------------------------------------

_WKB_POLY_HDR  = struct.pack('<BI', 1, 3)   # little-endian, type = Polygon
_WKB_MPOLY_HDR = struct.pack('<BI', 1, 6)   # little-endian, type = MultiPolygon


def _ring_wkb(ring_np: np.ndarray) -> bytes:
    """WKB encoding of one ring (closed, shape (n, 2))."""
    n = len(ring_np)
    return struct.pack('<I', n) + ring_np.astype('<f8', copy=False).tobytes()


def _polygon_wkb(rings_np: list[np.ndarray]) -> bytes:
    """WKB encoding of one polygon (outer + optional holes)."""
    return (
        _WKB_POLY_HDR
        + struct.pack('<I', len(rings_np))
        + b''.join(_ring_wkb(r) for r in rings_np)
    )


def _multipolygon_wkb(parts_rings: list[list[np.ndarray]]) -> bytes:
    """WKB encoding of a multipolygon (list of per-part ring lists)."""
    return (
        _WKB_MPOLY_HDR
        + struct.pack('<I', len(parts_rings))
        + b''.join(_polygon_wkb(rings) for rings in parts_rings)
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build(layer: QgsVectorLayer, snap_tolerance: float = 0, progress_callback=None, phase_callback=None) -> TopoLayer:
    """
    Build a TopoLayer from a QGIS polygon vector layer.

    All polygon rings are decomposed into topological arcs.  Shared arcs
    (boundaries between two adjacent polygons) are stored exactly once as a
    single TopoEdge object referenced by both polygons.

    Parameters
    ----------
    layer          : a valid QgsVectorLayer of polygon geometry type
    snap_tolerance : coordinates closer than this distance are treated as
                     identical.  The default (0) uses exact floating-point
                     comparison, which is correct for properly-built shapefiles
                     where shared borders have bit-for-bit identical coordinates.
                     Increase only when the source data has sub-millimetre gaps
                     between nominally-adjacent polygons.
    """
    topo = TopoLayer(snap_tolerance=snap_tolerance)

    # Internal phase weights (must sum to 100):
    #   20 % – extract rings from layer features
    #   15 % – build coordinate index
    #   65 % – build topology (arc splitting, edge creation)
    W_EXTRACT, W_INDEX, W_BUILD = 20, 15, 65

    def _sub_cb(offset, weight):
        if progress_callback is None:
            return None
        def cb(current, total):
            if total > 0:
                progress_callback(offset + weight * current // total, 100)
        return cb

    if phase_callback:
        phase_callback("Reading features from layer …")
    raw_rings = _extract_rings(layer, progress_callback=_sub_cb(0, W_EXTRACT))

    if phase_callback:
        phase_callback(f"Building coordinate index ({len(raw_rings)} rings) …")
    coord_to_rings = _build_coord_index(
        raw_rings, snap_tolerance,
        progress_callback=_sub_cb(W_EXTRACT, W_INDEX),
    )

    if phase_callback:
        phase_callback("Building topology (splitting arcs, creating edges) …")
    _build_topology(
        topo, raw_rings, coord_to_rings,
        progress_callback=_sub_cb(W_EXTRACT + W_INDEX, W_BUILD),
    )

    return topo


def _find_best_neighbor(topo: TopoLayer, pid_small: int,
                        exclude: set[int]) -> int | None:
    """Return the pid of the polygon sharing the most edge length with pid_small's outer ring."""
    small_eids = {eid for eid, _ in topo.polygons[pid_small].outer_ring.half_edges}
    best_pid, best_length = None, 0.0
    for pid, poly in topo.polygons.items():
        if pid == pid_small or pid in exclude:
            continue
        length = 0.0
        for eid, _ in poly.outer_ring.half_edges:
            if eid in small_eids:
                edge = topo.edges[eid]
                if len(edge.coords) >= 2:
                    d = np.diff(edge.coords, axis=0)
                    length += float(np.hypot(d[:, 0], d[:, 1]).sum())
        if length > best_length:
            best_length, best_pid = length, pid
    return best_pid


def _find_node_based_neighbor(topo: TopoLayer, pid_small: int,
                              exclude: set[int]) -> int | None:
    """
    Find the polygon with the most edge-length contact at the self-loop junction
    node of pid_small's outer ring.

    Used as a fallback when _find_best_neighbor finds no shared-edge neighbour.
    A self-loop edge's start_node == end_node; that node is often a shared
    junction where other polygons' edges start or end.  We accumulate edge
    length per polygon across all edges that meet at that node.
    """
    small_ring = topo.polygons[pid_small].outer_ring
    loop_nodes: set[int] = set()
    small_eids: set[int] = set()
    for eid, _ in small_ring.half_edges:
        small_eids.add(eid)
        e = topo.edges[eid]
        if e.start_node == e.end_node:
            loop_nodes.add(e.start_node)

    if not loop_nodes:
        return None

    pid_lengths: dict[int, float] = defaultdict(float)
    for eid, edge in topo.edges.items():
        if eid in small_eids:
            continue
        if edge.start_node not in loop_nodes and edge.end_node not in loop_nodes:
            continue
        seg_len = 0.0
        if len(edge.coords) >= 2:
            d = np.diff(edge.coords, axis=0)
            seg_len = float(np.hypot(d[:, 0], d[:, 1]).sum())
        for pid in (edge.left_polygon, edge.right_polygon):
            if (pid is not None and pid != pid_small
                    and pid not in exclude and pid in topo.polygons):
                pid_lengths[pid] += seg_len

    return max(pid_lengths, key=pid_lengths.__getitem__) if pid_lengths else None


def _merge_ring_into_neighbor(topo: TopoLayer, pid_small: int,
                               pid_neighbor: int) -> bool:
    """
    Merge the outer ring of ``pid_small`` into the outer ring of ``pid_neighbor``
    by removing their shared edges and inserting the small polygon's non-shared
    edges at the correct position in the neighbor's ring.

    After a successful merge the neighbor's outer ring encloses the union of
    both polygons' areas, so no geographic gap is introduced when ``pid_small``
    is subsequently deleted from the topology.

    Returns True on success, False when no shared edges exist.
    """
    small    = topo.polygons[pid_small]
    neighbor = topo.polygons[pid_neighbor]

    small_eids    = {eid for eid, _ in small.outer_ring.half_edges}
    neighbor_eids = {eid for eid, _ in neighbor.outer_ring.half_edges}
    shared_eids   = small_eids & neighbor_eids

    if not shared_eids:
        return False

    # Find A's non-shared edges starting from the one right after the last
    # shared edge in A's ring.  This sub-sequence forms the path that replaces
    # the shared segment in B's ring.
    #
    # Why: B traverses the shared segment in reverse (opposite direction to A).
    # If B enters the shared segment at node n_entry and exits at n_exit, then
    # A's non-shared path from n_entry to n_exit (in A's forward direction)
    # starts immediately after the last shared edge in A's ring.
    a_he = small.outer_ring.half_edges
    n = len(a_he)
    last_shared_in_a = max(
        (i for i, (eid, _) in enumerate(a_he) if eid in shared_eids), default=None
    )
    if last_shared_in_a is None:
        return False

    a_non_shared = [
        a_he[(last_shared_in_a + 1 + i) % n]
        for i in range(n)
        if a_he[(last_shared_in_a + 1 + i) % n][0] not in shared_eids
    ]

    # Build the new neighbor ring: walk B's half-edges, skip shared edges,
    # and insert A's non-shared sub-sequence at the first shared edge encountered.
    new_he: list[tuple[int, bool]] = []
    inserted = False
    for eid, fwd in neighbor.outer_ring.half_edges:
        if eid in shared_eids:
            if not inserted:
                new_he.extend(a_non_shared)
                inserted = True
            # skip the shared edge itself
        else:
            new_he.append((eid, fwd))

    if not inserted or not new_he:
        return False

    neighbor.outer_ring.half_edges = new_he

    # Update edge polygon references: the non-shared edges of pid_small now
    # belong to pid_neighbor's boundary.  Without this, those edges keep
    # left_polygon or right_polygon == pid_small, which becomes a dangling
    # reference once pid_small is deleted and breaks downstream topology lookups.
    for eid, _ in a_non_shared:
        edge = topo.edges[eid]
        if edge.left_polygon == pid_small:
            edge.left_polygon = pid_neighbor
        if edge.right_polygon == pid_small:
            edge.right_polygon = pid_neighbor

    return True


def dissolve_small_rings(topo: TopoLayer, threshold: float | None = None) -> tuple[int, int]:
    """
    Remove topology rings whose area is below *threshold*.

    When *threshold* is ``None`` (default) it is derived automatically from
    the current edge geometry as ``2 · d²``, where ``d`` is the global
    average edge-segment length.  This is self-scaling: after aggressive
    generalisation the average segment is longer, so the threshold is larger —
    tiny artefacts are dropped proportionally to the generalisation level.

    Pass an explicit *threshold* to use a predetermined area cutoff, e.g. an
    estimate based on the *expected* post-simplification segment length when
    calling before simplification.

    Two types of rings are dropped, always atomically so no gap or overlap is
    created in the output:

    * **Small holes** — an inner ring of a TopoPolygon whose area < threshold.
      If a sibling TopoPolygon (an island whose outer ring shares the same
      edge IDs) exists, it is dropped at the same time.

    * **Small polygon parts** — the outer ring of a TopoPolygon whose area <
      threshold.  Only dropped when the feature has at least one other polygon
      part remaining.  If a parent TopoPolygon has this ring as a hole, that
      hole is removed at the same time.

    Returns ``(n_parts_dropped, n_holes_dropped)``.
    """
    # --- Compute threshold from edge geometry if not supplied -----------------
    if threshold is None:
        total_length = 0.0
        total_segments = 0
        for edge in topo.edges.values():
            if len(edge.coords) >= 2:
                diffs = np.diff(edge.coords, axis=0)
                total_length += float(np.hypot(diffs[:, 0], diffs[:, 1]).sum())
                total_segments += len(edge.coords) - 1

        if total_segments == 0:
            return 0, 0

        d = total_length / total_segments
        threshold = 2.0 * d * d

    # --- Helper: shoelace area of a closed ring (numpy) ----------------------
    def _area(ring: TopoRing) -> float:
        coords = ring.iter_coords_numpy(topo.edges)
        if len(coords) < 4:
            return 0.0
        x, y = coords[:-1, 0], coords[:-1, 1]
        xn, yn = coords[1:, 0], coords[1:, 1]
        return abs(float(np.sum(x * yn - xn * y))) / 2.0

    # --- Build fast lookups --------------------------------------------------
    # frozenset(edge_ids) → polygon dict-key, for outer rings
    outer_key_to_pid: dict[frozenset, int] = {
        frozenset(eid for eid, _ in poly.outer_ring.half_edges): pid
        for pid, poly in topo.polygons.items()
    }
    # frozenset(edge_ids) → (parent polygon dict-key, TopoRing), for holes
    hole_key_to_parent: dict[frozenset, tuple[int, TopoRing]] = {}
    for pid, poly in topo.polygons.items():
        for hole in poly.inner_rings:
            key = frozenset(eid for eid, _ in hole.half_edges)
            hole_key_to_parent[key] = (pid, hole)

    # Group polygon dict-keys by feature_id
    feature_pids: dict[int, list[int]] = defaultdict(list)
    for pid, poly in topo.polygons.items():
        feature_pids[poly.feature_id].append(pid)

    pids_to_remove: set[int] = set()
    holes_to_drop: dict[int, set[int]] = defaultdict(set)  # pid → set of id(ring)

    # --- Phase 1: small holes → drop hole + twin island ----------------------
    for pid, poly in topo.polygons.items():
        for hole in poly.inner_rings:
            if _area(hole) < threshold:
                holes_to_drop[pid].add(id(hole))
                twin_pid = outer_key_to_pid.get(
                    frozenset(eid for eid, _ in hole.half_edges)
                )
                if twin_pid is not None:
                    pids_to_remove.add(twin_pid)

    # --- Phase 2: small outer rings not already removed ----------------------
    # Sort smallest-first so the smallest parts are dropped first; the
    # "at least one part per feature" guard then naturally keeps the largest.
    candidates = sorted(
        (_area(poly.outer_ring), pid)
        for pid, poly in topo.polygons.items()
        if pid not in pids_to_remove
    )
    for area, pid in candidates:
        if area >= threshold:
            break
        if pid in pids_to_remove:
            continue
        poly = topo.polygons[pid]
        remaining = [p for p in feature_pids[poly.feature_id]
                     if p not in pids_to_remove]
        if len(remaining) <= 1:
            continue  # never drop the last part of a feature

        # Merge into the topologically adjacent neighbor (most shared edge
        # length) before removing, so no geographic gap is created.
        best_nb = _find_best_neighbor(topo, pid, pids_to_remove)
        if best_nb is not None:
            _merge_ring_into_neighbor(topo, pid, best_nb)
            pids_to_remove.add(pid)
        else:
            # No shared-edge neighbour — try node-based neighbour (shared
            # junction node of the self-loop arc).
            best_nb = _find_node_based_neighbor(topo, pid, pids_to_remove)
            if best_nb is not None:
                # Re-assign the small polygon to the neighbour's feature so it
                # is included in that feature's output geometry as an extra part.
                nb_poly = topo.polygons[best_nb]
                target_fid = nb_poly.feature_id
                target_parts = [p for p in topo.polygons.values()
                                if p.feature_id == target_fid]
                new_part_idx = max(p.part_index for p in target_parts) + 1
                for p in target_parts:
                    p.is_multipart = True
                poly.feature_id   = target_fid
                poly.part_index   = new_part_idx
                poly.is_multipart = True
                # Do NOT add to pids_to_remove — polygon is kept under new fid.
            else:
                # Truly isolated — remove without merging.
                pids_to_remove.add(pid)

        # Also remove the corresponding hole from the parent (if any)
        outer_key = frozenset(eid for eid, _ in poly.outer_ring.half_edges)
        if outer_key in hole_key_to_parent:
            parent_pid, parent_hole = hole_key_to_parent[outer_key]
            holes_to_drop[parent_pid].add(id(parent_hole))

    # --- Apply removals ------------------------------------------------------
    n_holes = 0
    for pid, drop_ids in holes_to_drop.items():
        poly = topo.polygons[pid]
        before = len(poly.inner_rings)
        poly.inner_rings = [h for h in poly.inner_rings if id(h) not in drop_ids]
        n_holes += before - len(poly.inner_rings)

    for pid in pids_to_remove:
        del topo.polygons[pid]

    return len(pids_to_remove), n_holes


# ---------------------------------------------------------------------------
# Post-simplification: repair self-intersecting (inverted) rings
# ---------------------------------------------------------------------------

def _signed_area(coords: np.ndarray) -> float:
    """Signed shoelace area of a closed ring (coords[-1] == coords[0])."""
    x, y = coords[:-1, 0], coords[:-1, 1]
    xn, yn = coords[1:, 0], coords[1:, 1]
    return float(np.sum(x * yn - xn * y)) / 2.0



def _find_crossings(coords):
    """
    Find pairs of non-adjacent segments in a closed ring that properly
    intersect (t and u strictly between 0 and 1).

    coords: (n+1, 2) numpy array, closed (coords[0] == coords[-1]).
    Returns a list of (i, j) segment-index pairs.

    Implementation: row-by-row with a bounding-box pre-filter.  For each
    segment i we first keep only the j candidates whose axis-aligned bounding
    box overlaps segment i's box, then run the full intersection formula on
    that typically-small subset.  This keeps memory at O(n) per step (vs the
    naive O(n²) block allocation) and skips the expensive cross-product math
    for the vast majority of non-overlapping pairs.
    """
    n = len(coords) - 1  # number of segments
    if n < 4:
        return []

    eps = 1e-9

    # Segment start points, direction vectors, and axis-aligned bounding boxes
    p = coords[:-1]                    # (n, 2)
    r = coords[1:] - p                 # (n, 2)
    end = p + r                        # (n, 2)  == coords[1:]

    x_lo = np.minimum(p[:, 0], end[:, 0])   # (n,)
    x_hi = np.maximum(p[:, 0], end[:, 0])
    y_lo = np.minimum(p[:, 1], end[:, 1])
    y_hi = np.maximum(p[:, 1], end[:, 1])

    # Pre-built index array so inner-loop slices are zero-copy views
    all_j = np.arange(n)

    crossings = []

    for i in range(n):
        # Non-adjacent j range; exclude wrap-around pair (0, n-1)
        j_end = (n - 1) if i == 0 else n
        j_start = i + 2
        if j_start >= j_end:
            continue

        # Contiguous array slices are zero-copy views — no per-row allocation
        xl_j = x_lo[j_start:j_end]
        xh_j = x_hi[j_start:j_end]
        yl_j = y_lo[j_start:j_end]
        yh_j = y_hi[j_start:j_end]

        # Bounding-box pre-filter
        bbox_ok = (
            (x_lo[i] <= xh_j) & (xl_j <= x_hi[i]) &
            (y_lo[i] <= yh_j) & (yl_j <= y_hi[i])
        )
        if not bbox_ok.any():
            continue

        js = all_j[j_start:j_end][bbox_ok]   # view then fancy-index: compact

        # Full intersection formula on surviving candidates
        ri = r[i]                      # (2,)
        dj = p[js] - p[i]             # (k, 2)
        rj = r[js]                     # (k, 2)

        cross = ri[0] * rj[:, 1] - ri[1] * rj[:, 0]   # (k,)
        non_par = np.abs(cross) > 1e-12
        safe = np.where(non_par, cross, 1.0)

        t = (dj[:, 0] * rj[:, 1] - dj[:, 1] * rj[:, 0]) / safe
        u = (dj[:, 0] * ri[1]    - dj[:, 1] * ri[0])    / safe

        hit = non_par & (t > eps) & (t < 1.0 - eps) & (u > eps) & (u < 1.0 - eps)
        for j in js[hit]:
            crossings.append((i, int(j)))

    return crossings


def _loop_areas(
    coords: np.ndarray,
    crossing: tuple[int, int],
) -> tuple[float, float]:
    """
    Approximate areas of the two loops formed by crossing pair (i, j).

    Loop A: the path c[i] → c[i+1] → … → c[j+1], closed back to c[i].
    Loop B: the complementary path c[j+1] → … → c[n-1] → c[0] → … → c[i],
            closed back to c[j+1].

    Returns (|area_A|, |area_B|).
    """
    i, j = crossing
    n = len(coords) - 1  # number of unique ring vertices

    loop_a_pts = coords[i:j + 2]
    loop_a = np.vstack([loop_a_pts, loop_a_pts[:1]])
    area_a = abs(_signed_area(loop_a))

    loop_b_pts = np.vstack([coords[j + 1:n], coords[:i + 1]])
    loop_b = np.vstack([loop_b_pts, loop_b_pts[:1]])
    area_b = abs(_signed_area(loop_b))

    return area_a, area_b


def _build_seg_to_he(ring, edges):
    """
    Return a list where entry i is the index into ring.half_edges of the
    half-edge that produced segment i in the reconstructed ring.
    """
    seg_to_he = []
    for he_idx, (edge_id, _) in enumerate(ring.half_edges):
        n_segs = max(0, len(edges[edge_id].coords) - 1)
        seg_to_he.extend([he_idx] * n_segs)
    return seg_to_he


def _build_ring_to_edge_pos(  # exported for diagnostics
    ring: TopoRing,
    edges: dict,
) -> list[tuple[int, int]]:
    """
    Return a list where entry k is ``(he_idx, edge_forward_idx)`` for ring
    position k (0-indexed, not including the closing duplicate).

    ``edge_forward_idx`` is the index into ``edge.coords`` in the **forward**
    direction of the edge.  For a reversed half-edge (``forward=False``), the
    lk-th traversal position maps to ``edge.coords[m-1-lk]``.

    A half-edge is a directed reference ``(edge_id, forward)`` to a shared
    arc.  The ring contributes ``m-1`` positions per half-edge (``seg[:-1]``),
    where the excluded last point is the junction node shared with the next
    half-edge.
    """
    mapping: list[tuple[int, int]] = []
    for he_idx, (edge_id, forward) in enumerate(ring.half_edges):
        m = len(edges[edge_id].coords)
        for lk in range(m - 1):
            efwd_idx = lk if forward else (m - 1 - lk)
            mapping.append((he_idx, efwd_idx))
    return mapping


def _find_intersected_segment(ring, crossings, seg_to_he, edges):
    """
    Find the ring segment that is crossed most often ("the intersected"),
    and return (he_idx, edge_id, lo_idx, hi_idx) where lo_idx < hi_idx
    are indices into edge.coords forming the two endpoints of that segment
    in the forward direction of the edge.
    Returns None if the segment cannot be determined.
    """
    seg_freq: dict[int, int] = {}
    for i, j in crossings:
        seg_freq[i] = seg_freq.get(i, 0) + 1
        seg_freq[j] = seg_freq.get(j, 0) + 1
    if not seg_freq:
        return None

    intersected_ring_seg = max(seg_freq, key=seg_freq.get)
    if intersected_ring_seg >= len(seg_to_he):
        return None

    he_idx = seg_to_he[intersected_ring_seg]
    edge_id, forward = ring.half_edges[he_idx]
    edge = edges[edge_id]
    n = len(edge.coords)

    # First ring segment index produced by this half-edge
    he_ring_start = next(i for i, he in enumerate(seg_to_he) if he == he_idx)
    local_seg = intersected_ring_seg - he_ring_start

    if forward:
        lo_idx, hi_idx = local_seg, local_seg + 1
    else:
        # Reversed traversal: local segment k → coords[n-1-k] → coords[n-2-k]
        # In forward edge order that is [n-2-k, n-1-k].
        lo_idx = n - 2 - local_seg
        hi_idx = n - 1 - local_seg

    if lo_idx < 0 or hi_idx >= n:
        return None

    return he_idx, edge_id, lo_idx, hi_idx


def _best_restore_for_segment(edge, original_coords, lo_idx, hi_idx):
    """
    Find the best original interior point to insert between
    edge.coords[lo_idx] and edge.coords[hi_idx].

    'Best' = largest absolute perpendicular distance from the chord formed
    by those two current edge points.  Returns None when no candidates remain
    between those positions in the original arc.
    """
    # Arc-length parameterisation of original_coords
    diffs = np.diff(original_coords, axis=0)
    seg_lens = np.hypot(diffs[:, 0], diffs[:, 1])
    arc_s = np.concatenate([[0.0], np.cumsum(seg_lens)])
    total = float(arc_s[-1])
    if total < 1e-12:
        return None

    def _t(p):
        d = np.hypot(original_coords[:, 0] - float(p[0]),
                     original_coords[:, 1] - float(p[1]))
        return float(arc_s[int(np.argmin(d))]) / total

    t_lo = _t(edge.coords[lo_idx])
    t_hi = _t(edge.coords[hi_idx])
    if t_lo > t_hi:
        t_lo, t_hi = t_hi, t_lo

    current = {(round(float(p[0]), 9), round(float(p[1]), 9)) for p in edge.coords}

    # Original points with arc-length t strictly between t_lo and t_hi,
    # not yet present in edge.coords.
    remaining = []
    for i, p in enumerate(original_coords):
        if i == 0 or i == len(original_coords) - 1:
            continue  # skip loop/arc endpoints
        t = float(arc_s[i]) / total
        if t_lo < t < t_hi:
            key = (round(float(p[0]), 9), round(float(p[1]), 9))
            if key not in current:
                remaining.append(p)

    if not remaining:
        return None

    candidates = np.array(remaining, dtype=np.float64)
    s = edge.coords[lo_idx].astype(np.float64)
    e_pt = edge.coords[hi_idx].astype(np.float64)
    chord = e_pt - s
    chord_len = float(np.linalg.norm(chord))
    if chord_len < 1e-12:
        return candidates[0]

    perp = np.array([-chord[1], chord[0]]) / chord_len
    dists = np.abs((candidates - s) @ perp)
    return candidates[int(np.argmax(dists))]


def _insert_point(edge, point, original_coords):
    """
    Insert *point* into edge.coords at the position that preserves the
    original arc order, determined by arc-length along original_coords.
    """
    diffs = np.diff(original_coords, axis=0)
    seg_lens = np.hypot(diffs[:, 0], diffs[:, 1])
    arc_s = np.concatenate([[0.0], np.cumsum(seg_lens)])
    total = float(arc_s[-1])

    def _t(p):
        d = np.hypot(original_coords[:, 0] - float(p[0]),
                     original_coords[:, 1] - float(p[1]))
        return float(arc_s[int(np.argmin(d))]) / total if total > 1e-12 else 0.0

    t_new = _t(point)
    # Exclude edge.coords[-1]: for loop edges it duplicates coords[0] and gets
    # t=0, which would inflate insert_pos and place the point one position too late.
    # For open arcs the endpoint has t=1 > any interior t_new, so excluding it
    # never changes the count.
    t_curr = [_t(cp) for cp in edge.coords[:-1]]
    insert_pos = min(sum(1 for t in t_curr if t < t_new), len(edge.coords) - 1)
    edge.coords = np.insert(edge.coords, insert_pos, point, axis=0)


def _drop_small_loop(
    ring: TopoRing,
    edges: dict,
    crossing: tuple[int, int],
    ring_coords: np.ndarray,
) -> None:
    """
    Remove the interior points of the smaller loop formed by *crossing*.

    **Same-edge case**: all small-loop ring positions belong to one half-edge —
    those edge-internal coords are deleted directly from ``edge.coords``.

    **Cross-edge case**: the small loop spans multiple half-edges — interior
    points are removed from the boundary edges, and entirely-consumed
    intermediate half-edges are removed from ``ring.half_edges``.

    Junction nodes (``edge.coords[0]`` and ``edge.coords[-1]``) are never
    removed.  If a neighbour polygon shares a modified edge it automatically
    receives the updated coords, absorbing the dropped area.
    """
    i, j = crossing
    n = len(ring_coords) - 1  # number of unique ring vertices

    area_a, area_b = _loop_areas(ring_coords, crossing)
    if area_a <= area_b:
        # Loop A (positions i+1 … j) is the small one
        small_positions = list(range(i + 1, j + 1))
    else:
        # Loop B (positions j+1 … n-1, 0 … i-1) is the small one
        small_positions = list(range(j + 1, n)) + list(range(0, i))

    if not small_positions:
        return

    ring_to_edge = _build_ring_to_edge_pos(ring, edges)
    small_set = set(small_positions)

    # he_idx → set of edge_forward_indices to remove
    he_removals: dict[int, set] = {}
    for pos in small_positions:
        he_idx, efwd_idx = ring_to_edge[pos]
        he_removals.setdefault(he_idx, set()).add(efwd_idx)

    # Delete non-junction interior points from each affected edge
    any_deleted = False
    for he_idx, efwd_indices in he_removals.items():
        edge_id, _fwd = ring.half_edges[he_idx]
        edge = edges[edge_id]
        m = len(edge.coords)
        to_delete = sorted(idx for idx in efwd_indices if 0 < idx < m - 1)
        if to_delete:
            any_deleted = True
            edge.coords = np.delete(edge.coords, to_delete, axis=0)

    # Fallback: if no interior points were removable in the small loop
    # (e.g. all mapped to junction nodes when the crossing involves the
    # closing segment), delete ring_pos[i] — the boundary vertex of the
    # crossing segment on the small-loop side.  This eliminates the spike
    # that approaches the junction node and causes the crossing.
    if not any_deleted:
        for cand_pos in [i, j]:
            he_idx, efwd_idx = ring_to_edge[cand_pos]
            edge_id, _ = ring.half_edges[he_idx]
            edge = edges[edge_id]
            m = len(edge.coords)
            if 0 < efwd_idx < m - 1:
                edge.coords = np.delete(edge.coords, efwd_idx, axis=0)
                break

    # Cross-edge case: remove half-edges whose every ring position is in the
    # small loop (they are entirely consumed).
    he_ring_positions: dict[int, set] = {}
    for k, (he_idx, _) in enumerate(ring_to_edge):
        he_ring_positions.setdefault(he_idx, set()).add(k)

    he_to_remove = {
        he_idx
        for he_idx, positions in he_ring_positions.items()
        if positions.issubset(small_set)
    }
    if he_to_remove:
        ring.half_edges = [
            he for idx, he in enumerate(ring.half_edges)
            if idx not in he_to_remove
        ]


def repair_ring_inversions(
    topo: TopoLayer,
    original_edge_coords: dict[int, np.ndarray],
    max_attempts: int = 5,
    progress_callback=None,
) -> int:
    """
    Detect self-intersecting outer rings caused by over-simplification and
    repair them using one of two strategies:

    **Case 1 — Concave inversion**: multiple crossing pairs, or one crossing
    where both loops are large (area ratio ≥ 5 %).  Fix: restore the original
    point furthest from the most-crossed segment.

    **Case 2 — Figure-8**: exactly one crossing pair and the smaller loop is
    < 5 % of the total ring area.  Fix: drop the small loop by removing its
    interior points from the relevant edge(s).

    A **half-edge** is a directed reference ``(edge_id, forward)`` to a shared
    arc.  Modifying ``edge.coords`` automatically updates ALL polygons that
    share the same edge.  After each pass every outer ring is re-evaluated so
    that fixes to shared edges are propagated to neighbours.

    Returns ``(total_repairs, initial_invalid_count)`` where
    *total_repairs* is the number of actions taken (point restorations +
    loop drops) and *initial_invalid_count* is the number of outer rings
    that were self-intersecting before any repair.
    """
    _FIGURE8_RATIO = 0.05
    total_repairs = 0
    initial_invalid_count = 0

    # After the first pass, only rings that share an edge with a repaired ring
    # need re-checking.  ``dirty_edges`` holds the set of edge_ids modified in
    # the previous pass; None means "check everything" (first pass).
    dirty_edges: set[int] | None = None

    n_polys = len(topo.polygons)
    for attempt in range(max_attempts):
        invalid_rings = []
        for i, (pid, poly) in enumerate(topo.polygons.items()):
            if attempt == 0 and progress_callback is not None and n_polys > 0:
                progress_callback(100.0 * i / n_polys)
            for ring in [poly.outer_ring] + poly.inner_rings:
                # Skip rings that share no dirty edges (optimization: only on
                # passes after the first, when we know which edges changed).
                if dirty_edges is not None:
                    if not any(eid in dirty_edges for eid, _ in ring.half_edges):
                        continue
                coords = ring.iter_coords_numpy(topo.edges)
                if len(coords) >= 4 and _find_crossings(coords):
                    invalid_rings.append((pid, ring))

        if attempt == 0:
            initial_invalid_count = len(invalid_rings)

        if not invalid_rings:
            break

        # Collect edges dirtied in this repair pass so the next pass can skip
        # rings that cannot have been newly invalidated.
        dirty_edges = set()

        for pid, ring in invalid_rings:
            # Re-fetch coords: a shared-edge fix earlier in this pass may
            # have already resolved this ring.
            coords = ring.iter_coords_numpy(topo.edges)
            crossings = _find_crossings(coords)
            if not crossings:
                continue

            # --- Case 2: figure-8 — find the first crossing with a tiny loop ---
            case2_done = False
            for crossing in crossings:
                area_a, area_b = _loop_areas(coords, crossing)
                total_area = area_a + area_b
                if total_area > 0 and min(area_a, area_b) / total_area < _FIGURE8_RATIO:
                    _drop_small_loop(ring, topo.edges, crossing, coords)
                    dirty_edges.update(eid for eid, _ in ring.half_edges)
                    total_repairs += 1
                    case2_done = True
                    break  # re-evaluate the ring in the next pass

            if case2_done:
                continue

            # --- Case 1: concave inversion (all crossings have large loops) ---
            seg_to_he = _build_seg_to_he(ring, topo.edges)
            result = _find_intersected_segment(ring, crossings, seg_to_he, topo.edges)
            if result is None:
                continue

            _, edge_id, lo_idx, hi_idx = result
            edge = topo.edges[edge_id]
            point = _best_restore_for_segment(
                edge, original_edge_coords[edge_id], lo_idx, hi_idx
            )
            if point is None:
                continue

            _insert_point(edge, point, original_edge_coords[edge_id])
            dirty_edges.add(edge_id)
            total_repairs += 1

    return total_repairs, initial_invalid_count


def to_qgs_features(topo: TopoLayer) -> list[QgsFeature]:
    """
    Reconstruct a list of QgsFeatures from a TopoLayer.

    Feature ids, attributes, and geometry coordinates are preserved exactly.
    Multipolygon features are reconstructed as multipolygons.

    Uses binary WKB encoding to build geometries, which avoids creating one
    QgsPointXY object per output vertex.
    """
    by_feature: dict[int, list[TopoPolygon]] = defaultdict(list)
    for poly in topo.polygons.values():
        by_feature[poly.feature_id].append(poly)

    features: list[QgsFeature] = []
    for feature_id in sorted(by_feature.keys()):
        parts = sorted(by_feature[feature_id], key=lambda p: p.part_index)
        is_multi = any(p.is_multipart for p in parts)

        # Build per-part lists of numpy ring arrays (outer + holes, closed).
        parts_rings: list[list[np.ndarray]] = []
        for part in parts:
            outer = part.outer_ring.iter_coords_numpy(topo.edges)
            holes = list(h.iter_coords_numpy(topo.edges) for h in part.inner_rings)
            parts_rings.append([outer] + holes)

        # Encode as WKB and hand off to QGIS — avoids QgsPointXY per vertex.
        wkb = _multipolygon_wkb(parts_rings) if is_multi else _polygon_wkb(parts_rings[0])
        geom = QgsGeometry()
        geom.fromWkb(QByteArray(wkb))
        feat = QgsFeature()
        feat.setId(feature_id)
        feat.setGeometry(geom)
        feat.setAttributes(parts[0].attributes)
        features.append(feat)

    return features


# ---------------------------------------------------------------------------
# Phase 1 – extract rings
# ---------------------------------------------------------------------------

def _extract_rings(layer: QgsVectorLayer, progress_callback=None) -> list[dict]:
    """
    Return a flat list of ring descriptors, one per polygon ring in the layer.

    Each dict has:
      ring_id    – index in the returned list (used as key everywhere)
      feature_id – QgsFeature.id()
      part_index – 0 for simple polygons; part number for multipolygons
      ring_index – 0 = outer ring, ≥1 = hole
      coords     – list of (x, y) tuples WITHOUT the closing duplicate
      attrs      – QgsFeature.attributes()
      is_multipart – True when the originating feature was a multipolygon
    """
    total = layer.featureCount()
    raw_rings: list[dict] = []
    for feat_idx, feature in enumerate(layer.getFeatures()):
        geom = feature.geometry()
        fid = feature.id()
        attrs = feature.attributes()
        is_multi = geom.isMultipart()

        parts = geom.asMultiPolygon() if is_multi else [geom.asPolygon()]

        for part_idx, polygon in enumerate(parts):
            for ring_idx, ring_pts in enumerate(polygon):
                # Strip closing duplicate (last point == first for closed rings)
                coords = [(p.x(), p.y()) for p in ring_pts[:-1]]
                if len(coords) < 3:
                    continue
                raw_rings.append({
                    'ring_id':      len(raw_rings),
                    'feature_id':   fid,
                    'part_index':   part_idx,
                    'ring_index':   ring_idx,
                    'coords':       coords,
                    'attrs':        attrs,
                    'is_multipart': is_multi,
                })

        if progress_callback:
            progress_callback(feat_idx + 1, total)

    return raw_rings


# ---------------------------------------------------------------------------
# Phase 2 – coordinate → ring-set index
# ---------------------------------------------------------------------------

def _build_coord_index(
    raw_rings: list[dict],
    snap_tolerance: float,
    progress_callback=None,
) -> dict[tuple, set[int]]:
    """
    Map each (snapped) coordinate to the set of ring_ids that contain it.

    A coordinate present in more than one ring is a candidate for a shared
    arc boundary.
    """
    index: dict[tuple, set[int]] = defaultdict(set)
    total = len(raw_rings)

    if snap_tolerance == 0:
        # Fast path: raw tuple keys, no rounding, no function call overhead.
        for ring in raw_rings:
            rid = ring['ring_id']
            for coord in ring['coords']:
                index[coord].add(rid)
            if progress_callback:
                progress_callback(rid + 1, total)
    else:
        for ring in raw_rings:
            rid = ring['ring_id']
            for coord in ring['coords']:
                index[_snap(coord, snap_tolerance)].add(rid)
            if progress_callback:
                progress_callback(rid + 1, total)

    return index


# ---------------------------------------------------------------------------
# Phases 3-5 – split rings into arcs, build edges & polygons
# ---------------------------------------------------------------------------

# Sentinel: coordinate is not shared with any other ring.
# Must be a value that can never be a ring_id (ring_ids are non-negative integers).
_UNSHARED = -1


def _build_topology(
    topo: TopoLayer,
    raw_rings: list[dict],
    coord_to_rings: dict[tuple, set[int]],
    progress_callback=None,
) -> None:
    """
    Core routine.  Iterates every ring in three passes:

    Pass A – For each ring, detect arc boundaries (positions where the set of
             sharing-partner rings changes) and record arc_starts.

    Pass A.5 – Propagate junction coordinates: any coord that is an arc_start
               in some ring must also be an arc_start in every other ring that
               contains it.  Without this step, a small ring whose entire
               boundary is shared with a single larger ring produces only one
               arc_start (at the n-way junction), yielding a self-loop that
               cannot match the larger ring's open arcs.

    Pass B – Split each ring into arcs using the augmented arc_starts,
             create or look up TopoEdge objects, assemble TopoRings.

    Pass C – Group rings by (feature_id, part_index) to form TopoPolygon
             objects, then set left/right polygon ownership on each edge.
    """
    snap_tol = topo.snap_tolerance
    ring_meta: dict[int, dict] = {}

    # --- Pass A: compute sharing values and arc_starts for every ring ---
    # (no TopoEdge creation yet — we need all arc_starts before propagating)
    all_arc_starts: dict[int, list[int]] = {}   # ring_id → sorted arc_start list

    total_rings = len(raw_rings)
    for ring_idx, ring in enumerate(raw_rings):
        ring_id = ring['ring_id']
        coords  = ring['coords']
        n       = len(coords)

        # For each coordinate in this ring, compute a "sharing value":
        #   _UNSHARED (0)   – coordinate belongs only to this ring
        #   int > 0         – coordinate is shared with exactly one other ring
        #                     (the value is that ring's ring_id)
        #   frozenset       – coordinate is shared with 2+ other rings (rare)
        #
        # Using plain ints for the common cases avoids per-coordinate set
        # allocation and is much cheaper to compare for arc-boundary detection.
        sharing: list = []
        if snap_tol == 0:
            for c in coords:
                rings_here = coord_to_rings[c]
                k = len(rings_here)
                if k == 1:
                    sharing.append(_UNSHARED)
                elif k == 2:
                    # Find the one ring that is not ring_id.
                    for r in rings_here:
                        if r != ring_id:
                            sharing.append(r)
                            break
                else:
                    sharing.append(frozenset(rings_here - {ring_id}))
        else:
            for c in coords:
                rings_here = coord_to_rings[_snap(c, snap_tol)]
                k = len(rings_here)
                if k == 1:
                    sharing.append(_UNSHARED)
                elif k == 2:
                    for r in rings_here:
                        if r != ring_id:
                            sharing.append(r)
                            break
                else:
                    sharing.append(frozenset(rings_here - {ring_id}))

        # An arc boundary is a position where the sharing value changes,
        # subject to two adjustments:
        #
        # 1. Narrowing suppression (n-way junctions): when the current sharing
        #    partners are a strict subset of the previous partners the ring is
        #    leaving an n-way junction — the arc with the remaining partner(s)
        #    began at that junction, not one step later.  Suppressing these
        #    transitions ensures matching canonical keys.
        #
        # 2. SHARED→UNSHARED shift: when a shared section ends and the next
        #    coordinate is unshared, the arc_start is normally placed at the
        #    first unshared coord, which becomes the arc endpoint (included by
        #    _split_into_arcs).  The adjacent ring traversing the same boundary
        #    in the opposite direction sees UNSHARED→SHARED and places its
        #    arc_start at the first *shared* coord — one position earlier.
        #    The two arcs therefore have different lengths and keys don't match.
        #    Fix: move the arc_start for SHARED→UNSHARED transitions back one
        #    position to the *last shared coord*.  The shared arc then ends at
        #    the last shared coord, which is the same coord where the adjacent
        #    ring starts its matching arc.
        raw_arc_starts = [
            i for i in range(n)
            if sharing[i] != sharing[(i - 1) % n]
            and not _sharing_narrows(sharing[i], sharing[(i - 1) % n])
        ]
        arc_starts = []
        for i in raw_arc_starts:
            if sharing[i] == _UNSHARED and sharing[(i - 1) % n] != _UNSHARED:
                arc_starts.append((i - 1) % n)
            else:
                arc_starts.append(i)
        arc_starts = sorted(set(arc_starts))

        all_arc_starts[ring_id] = arc_starts

    # --- Pass A.5: propagate junction coordinates across rings ---
    #
    # Problem: a ring R_small whose entire boundary is shared with a single
    # larger ring R_large has a constant sharing value throughout (e.g.
    # sharing=R_large_id at every coord).  Its only arc_start comes from the
    # one n-way junction where a third ring also touches the boundary.
    # R_large, however, has additional arc_starts at the points where it
    # *enters* and *exits* the R_small-shared section — points that are just
    # ordinary shared coords from R_small's perspective (no sharing change).
    # Result: R_small produces one self-loop arc; R_large produces shorter
    # open arcs.  Their canonical keys never match → shared arc undetected.
    #
    # Fix: collect all arc_start coordinates across every ring ("junction
    # coords"), then add any junction coord that appears in a ring's own coord
    # list as an extra arc_start for that ring.  This ensures both rings agree
    # on where arc boundaries lie.
    junction_coords: set[tuple] = set()
    for ring in raw_rings:
        ring_id = ring['ring_id']
        coords  = ring['coords']
        for i in all_arc_starts[ring_id]:
            junction_coords.add(coords[i])

    for ring in raw_rings:
        ring_id = ring['ring_id']
        coords  = ring['coords']
        existing = set(all_arc_starts[ring_id])
        extra = [
            i for i, c in enumerate(coords)
            if c in junction_coords and i not in existing
        ]
        if extra:
            all_arc_starts[ring_id] = sorted(existing | set(extra))

    # --- Pass B: build TopoEdges and TopoRings using the final arc_starts ---
    for ring_idx, ring in enumerate(raw_rings):
        ring_id = ring['ring_id']
        coords  = ring['coords']
        arc_starts = all_arc_starts[ring_id]

        arcs = _split_into_arcs(coords, arc_starts)

        topo_ring = TopoRing()
        for arc_coords in arcs:
            edge_id, forward = _get_or_create_edge(topo, arc_coords)
            topo_ring.half_edges.append((edge_id, forward))

        ring_meta[ring_id] = {
            'topo_ring':    topo_ring,
            'feature_id':   ring['feature_id'],
            'part_index':   ring['part_index'],
            'ring_index':   ring['ring_index'],
            'attrs':        ring['attrs'],
            'is_multipart': ring['is_multipart'],
        }

        if progress_callback:
            progress_callback(ring_idx + 1, total_rings)

    # --- Pass C: assemble TopoPolygons and set edge ownership ---
    poly_groups: dict[tuple, dict] = defaultdict(
        lambda: {'outer': None, 'holes': [], 'attrs': None, 'is_multipart': False}
    )
    for meta in ring_meta.values():
        key = (meta['feature_id'], meta['part_index'])
        poly_groups[key]['attrs']        = meta['attrs']
        poly_groups[key]['is_multipart'] = meta['is_multipart']
        if meta['ring_index'] == 0:
            poly_groups[key]['outer'] = meta['topo_ring']
        else:
            poly_groups[key]['holes'].append(meta['topo_ring'])

    for (feature_id, part_index), group in poly_groups.items():
        poly_id = topo._next_polygon_id
        topo._next_polygon_id += 1

        poly = TopoPolygon(
            id=poly_id,
            feature_id=feature_id,
            part_index=part_index,
            outer_ring=group['outer'],
            inner_rings=group['holes'],
            attributes=group['attrs'],
            is_multipart=group['is_multipart'],
        )
        topo.polygons[poly_id] = poly

        # Assign edge ownership.
        # Convention: the polygon that traverses an edge in the canonical
        # (forward) direction owns the RIGHT side; the polygon that traverses
        # it reversed owns the LEFT side.
        for ring in [group['outer']] + group['holes']:
            for edge_id, forward in ring.half_edges:
                edge = topo.edges[edge_id]
                if forward:
                    if edge.right_polygon is None:
                        edge.right_polygon = poly_id
                else:
                    if edge.left_polygon is None:
                        edge.left_polygon = poly_id


# ---------------------------------------------------------------------------
# Arc helpers
# ---------------------------------------------------------------------------

def _sharing_narrows(curr, prev) -> bool:
    """Return True if the current sharing partners are a strict subset of the
    previous sharing partners.

    This happens when a ring leaves an n-way junction and re-enters a region
    shared with fewer neighbours.  Example: a ring transitions from
    frozenset({A, B}) (3-way junction) to just A (2-way shared with A).
    The arc with partner A began at the n-way junction, not one step later,
    so the transition coord should NOT open a new arc.

    The check handles all n ≥ 2 because frozenset strict-subset comparison is
    used regardless of how many rings meet at the junction.

    ``_UNSHARED`` (0) is treated as "no partners" and never counts as a
    subset, so entering unshared territory always starts a new arc.
    """
    if curr == _UNSHARED or prev == _UNSHARED:
        return False
    curr_set = curr if isinstance(curr, frozenset) else frozenset({curr})
    prev_set = prev if isinstance(prev, frozenset) else frozenset({prev})
    return curr_set < prev_set   # strict subset: curr lost some partners


def _split_into_arcs(
    coords: list[tuple],
    arc_starts: list[int],
) -> list[list[tuple]]:
    """
    Split a ring (without its closing duplicate) into arcs.

    ``arc_starts`` is a sorted list of positions where a new arc begins.
    Each returned arc includes BOTH its start and end coordinate so that
    consecutive arcs share their junction point.

    If ``arc_starts`` is empty the whole ring is returned as a single
    loop arc whose last coordinate repeats the first.
    """
    n = len(coords)

    if not arc_starts:
        return [coords + [coords[0]]]

    arcs: list[list[tuple]] = []
    num = len(arc_starts)
    for j in range(num):
        start_idx = arc_starts[j]
        end_idx   = arc_starts[(j + 1) % num]

        if end_idx > start_idx:
            arc = coords[start_idx: end_idx + 1]
        else:
            arc = coords[start_idx:] + coords[: end_idx + 1]

        if len(arc) >= 2:
            arcs.append(arc)

    return arcs


def _get_or_create_edge(
    topo: TopoLayer,
    arc_coords: list[tuple],
) -> tuple[int, bool]:
    """
    Return ``(edge_id, forward)`` for the given arc.

    Creates a new TopoEdge if this arc has not been seen before.
    The edge is always stored in canonical direction (first coord ≤ last
    coord lexicographically).

    ``forward=True``  – arc_coords is aligned with edge.coords.
    ``forward=False`` – arc_coords is the reverse of edge.coords.
    """
    canon, is_forward = _canonicalize(arc_coords)
    key = tuple(canon)

    if key in topo._edge_lookup:
        return topo._edge_lookup[key], is_forward

    start_node_id = _get_or_create_node(topo, canon[0])
    end_node_id   = _get_or_create_node(topo, canon[-1])

    edge_id = topo._next_edge_id
    topo._next_edge_id += 1

    edge = TopoEdge(
        id=edge_id,
        start_node=start_node_id,
        end_node=end_node_id,
        coords=np.array(canon, dtype=np.float64),
        left_polygon=None,
        right_polygon=None,
    )
    topo.edges[edge_id] = edge
    topo._edge_lookup[key] = edge_id

    return edge_id, is_forward


def _canonicalize(arc_coords: list[tuple]) -> tuple[list[tuple], bool]:
    """
    Return ``(canonical_coords, is_forward)``.

    For **open arcs** (first != last): the canonical form puts the
    lexicographically-smaller endpoint first.

    For **loop arcs** (first == last, entire ring = one arc): the canonical
    form is the lexicographically-smaller of the minimum-rotation forward form
    and the minimum-rotation reversed form.  This is necessary because an outer
    ring (CCW) and an inner ring (CW hole) traverse the same loop arc in
    opposite directions; without this normalisation both would produce different
    tuple keys and the shared edge would not be detected.

    ``is_forward=True``  means arc_coords traverses the canonical arc in the
                         same cyclic direction (ignoring start vertex).
    ``is_forward=False`` means arc_coords is the cyclic reversal of the
                         canonical arc.
    """
    first, last = arc_coords[0], arc_coords[-1]

    if first != last:
        # Open arc: smaller endpoint goes first.
        if first <= last:
            return arc_coords, True
        return list(reversed(arc_coords)), False

    # Loop arc: choose canonical form as the lex-min of all rotations of both
    # the forward and reversed traversals.
    body = arc_coords[:-1]          # strip closing duplicate; n unique vertices
    n = len(body)
    if n == 0:
        return arc_coords, True

    # Minimum-rotation of forward traversal
    fwd_start = min(range(n), key=lambda i: body[i:] + body[:i])
    fwd = body[fwd_start:] + body[:fwd_start]

    # Minimum-rotation of reversed traversal
    rev_body = list(reversed(body))
    rev_start = min(range(n), key=lambda i: rev_body[i:] + rev_body[:i])
    rev = rev_body[rev_start:] + rev_body[:rev_start]

    if fwd <= rev:
        return fwd + [fwd[0]], True
    return rev + [rev[0]], False


def _get_or_create_node(topo: TopoLayer, coord: tuple) -> int:
    """Return an existing node id for this coordinate, or create a new one."""
    key = coord if topo.snap_tolerance == 0 else _snap(coord, topo.snap_tolerance)
    if key in topo._node_lookup:
        return topo._node_lookup[key]

    node_id = topo._next_node_id
    topo._next_node_id += 1

    topo.nodes[node_id] = TopoNode(id=node_id, x=coord[0], y=coord[1])
    topo._node_lookup[key] = node_id
    return node_id


def _snap(coord: tuple, tolerance: float) -> tuple:
    """Snap a coordinate to a grid defined by ``tolerance``."""
    x, y = coord
    inv = 1.0 / tolerance
    return (round(x * inv) / inv, round(y * inv) / inv)
