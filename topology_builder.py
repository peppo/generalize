"""
Build a TopoLayer from a QgsVectorLayer and reconstruct QgsFeatures from it.

Public API
----------
snap_to_self(layer, tolerance)  -> QgsVectorLayer   (topological pre-processing)
build(layer)                    -> TopoLayer
to_qgs_features(topo)           -> list[QgsFeature]

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
from PyQt5.QtCore import QByteArray
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
            holes = [h.iter_coords_numpy(topo.edges) for h in part.inner_rings]
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
    Core routine.  Iterates every ring in two passes:

    Pass A – For each ring, detect arc boundaries (positions where the set of
             sharing-partner rings changes), split the ring into arcs, and
             create or look up the corresponding TopoEdge.

    Pass B – Group rings by (feature_id, part_index) to form TopoPolygon
             objects, then set left/right polygon ownership on each edge.
    """
    snap_tol = topo.snap_tolerance
    ring_meta: dict[int, dict] = {}

    # --- Pass A: build TopoEdges and TopoRings ---
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

    # --- Pass B: assemble TopoPolygons and set edge ownership ---
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
