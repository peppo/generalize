"""
Build a TopoLayer from a QgsVectorLayer and reconstruct QgsFeatures from it.

Public API
----------
build(layer)           -> TopoLayer
to_qgs_features(topo)  -> list[QgsFeature]

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

No external libraries beyond NumPy and QGIS are used.
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np
from qgis.core import QgsFeature, QgsGeometry, QgsPointXY, QgsVectorLayer

from .topology import TopoEdge, TopoLayer, TopoNode, TopoPolygon, TopoRing


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build(layer: QgsVectorLayer, snap_tolerance: float = 1e-8) -> TopoLayer:
    """
    Build a TopoLayer from a QGIS polygon vector layer.

    All polygon rings are decomposed into topological arcs.  Shared arcs
    (boundaries between two adjacent polygons) are stored exactly once as a
    single TopoEdge object referenced by both polygons.

    Parameters
    ----------
    layer          : a valid QgsVectorLayer of polygon geometry type
    snap_tolerance : coordinates closer than this distance are treated as
                     identical.  For projected CRS (metres) 1e-8 m is
                     effectively exact; for geographic CRS (degrees) it is
                     also negligible.  Raise only if your source data has
                     small but non-zero gaps between adjacent polygons.
    """
    topo = TopoLayer(snap_tolerance=snap_tolerance)

    raw_rings = _extract_rings(layer)
    coord_to_rings = _build_coord_index(raw_rings, snap_tolerance)
    _build_topology(topo, raw_rings, coord_to_rings)

    return topo


def to_qgs_features(topo: TopoLayer) -> list[QgsFeature]:
    """
    Reconstruct a list of QgsFeatures from a TopoLayer.

    Feature ids, attributes, and geometry coordinates are preserved exactly.
    Multipolygon features are reconstructed as multipolygons.
    """
    by_feature: dict[int, list[TopoPolygon]] = defaultdict(list)
    for poly in topo.polygons.values():
        by_feature[poly.feature_id].append(poly)

    features: list[QgsFeature] = []
    for feature_id in sorted(by_feature.keys()):
        parts = sorted(by_feature[feature_id], key=lambda p: p.part_index)
        is_multi = any(p.is_multipart for p in parts)

        qgs_parts = []
        for part in parts:
            outer_pts = [QgsPointXY(x, y)
                         for x, y in part.outer_ring.iter_coords(topo.edges)]
            holes = [
                [QgsPointXY(x, y) for x, y in h.iter_coords(topo.edges)]
                for h in part.inner_rings
            ]
            qgs_parts.append([outer_pts] + holes)

        feat = QgsFeature()
        feat.setId(feature_id)
        if is_multi:
            feat.setGeometry(QgsGeometry.fromMultiPolygonXY(qgs_parts))
        else:
            feat.setGeometry(QgsGeometry.fromPolygonXY(qgs_parts[0]))
        feat.setAttributes(parts[0].attributes)
        features.append(feat)

    return features


# ---------------------------------------------------------------------------
# Phase 1 – extract rings
# ---------------------------------------------------------------------------

def _extract_rings(layer: QgsVectorLayer) -> list[dict]:
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
    raw_rings: list[dict] = []
    for feature in layer.getFeatures():
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
                    'ring_id':     len(raw_rings),
                    'feature_id':  fid,
                    'part_index':  part_idx,
                    'ring_index':  ring_idx,
                    'coords':      coords,
                    'attrs':       attrs,
                    'is_multipart': is_multi,
                })

    return raw_rings


# ---------------------------------------------------------------------------
# Phase 2 – coordinate → ring-set index
# ---------------------------------------------------------------------------

def _build_coord_index(
    raw_rings: list[dict],
    snap_tolerance: float,
) -> dict[tuple, set[int]]:
    """
    Map each (snapped) coordinate to the set of ring_ids that contain it.

    A coordinate present in more than one ring is a candidate for a shared
    arc boundary.
    """
    index: dict[tuple, set[int]] = defaultdict(set)
    for ring in raw_rings:
        for coord in ring['coords']:
            index[_snap(coord, snap_tolerance)].add(ring['ring_id'])
    return index


# ---------------------------------------------------------------------------
# Phases 3-5 – split rings into arcs, build edges & polygons
# ---------------------------------------------------------------------------

def _build_topology(
    topo: TopoLayer,
    raw_rings: list[dict],
    coord_to_rings: dict[tuple, set[int]],
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
    for ring in raw_rings:
        ring_id = ring['ring_id']
        coords = ring['coords']
        n = len(coords)

        # For each coordinate in this ring, compute which OTHER rings share it.
        sharing = [
            frozenset(coord_to_rings[_snap(c, snap_tol)] - {ring_id})
            for c in coords
        ]

        # An arc boundary is a position where the sharing set changes.
        # Because the ring is cyclic we compare with the previous position
        # (wrapping around).  The result is 0 elements (whole ring is one arc)
        # or ≥2 elements (at least one shared stretch and one unshared stretch).
        arc_starts = [
            i for i in range(n)
            if sharing[i] != sharing[(i - 1) % n]
        ]

        arcs = _split_into_arcs(coords, arc_starts)

        topo_ring = TopoRing()
        for arc_coords in arcs:
            edge_id, forward = _get_or_create_edge(topo, arc_coords)
            topo_ring.half_edges.append((edge_id, forward))

        ring_meta[ring_id] = {
            'topo_ring':   topo_ring,
            'feature_id':  ring['feature_id'],
            'part_index':  ring['part_index'],
            'ring_index':  ring['ring_index'],
            'attrs':       ring['attrs'],
            'is_multipart': ring['is_multipart'],
        }

    # --- Pass B: assemble TopoPolygons and set edge ownership ---
    poly_groups: dict[tuple, dict] = defaultdict(
        lambda: {'outer': None, 'holes': [], 'attrs': None, 'is_multipart': False}
    )
    for meta in ring_meta.values():
        key = (meta['feature_id'], meta['part_index'])
        poly_groups[key]['attrs'] = meta['attrs']
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

def _split_into_arcs(
    coords: list[tuple],
    arc_starts: list[int],
) -> list[list[tuple]]:
    """
    Split a ring (without its closing duplicate) into arcs.

    ``arc_starts`` is a sorted list of positions where a new arc begins.
    Each returned arc is a list that includes BOTH its start and end
    coordinate so that consecutive arcs share their junction point.

    If ``arc_starts`` is empty the whole ring is returned as a single
    loop arc whose last coordinate repeats the first.
    """
    n = len(coords)

    if not arc_starts:
        # Whole ring is one loop arc (no sharing, or uniformly shared).
        return [coords + [coords[0]]]

    arcs: list[list[tuple]] = []
    num = len(arc_starts)
    for j in range(num):
        start_idx = arc_starts[j]
        end_idx = arc_starts[(j + 1) % num]

        if end_idx > start_idx:
            arc = coords[start_idx: end_idx + 1]
        else:
            # end_idx < start_idx: arc wraps around the ring boundary.
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

    ``forward=True``  – arc_coords is aligned with edge.coords (start→end).
    ``forward=False`` – arc_coords is the reverse of edge.coords (end→start).
    """
    canon, is_forward = _canonicalize(arc_coords)
    key = tuple(canon)

    if key in topo._edge_lookup:
        return topo._edge_lookup[key], is_forward

    # New edge – always stored in canonical direction.
    start_node_id = _get_or_create_node(topo, canon[0])
    end_node_id = _get_or_create_node(topo, canon[-1])

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

    The canonical form is the orientation where the first coordinate is
    lexicographically ≤ the last coordinate, ensuring that the same arc
    traversed in opposite directions by two polygons produces the same key.

    For loop arcs (first == last) ``is_forward`` is always True.
    """
    first, last = arc_coords[0], arc_coords[-1]
    if first <= last:
        return arc_coords, True
    return list(reversed(arc_coords)), False


def _get_or_create_node(topo: TopoLayer, coord: tuple) -> int:
    """Return an existing node id for this coordinate, or create a new one."""
    snapped = _snap(coord, topo.snap_tolerance)
    if snapped in topo._node_lookup:
        return topo._node_lookup[snapped]

    node_id = topo._next_node_id
    topo._next_node_id += 1

    topo.nodes[node_id] = TopoNode(id=node_id, x=coord[0], y=coord[1])
    topo._node_lookup[snapped] = node_id
    return node_id


def _snap(coord: tuple, tolerance: float) -> tuple:
    """Snap a coordinate to a grid defined by ``tolerance``."""
    if tolerance == 0:
        return coord
    x, y = coord
    inv = 1.0 / tolerance
    return (round(x * inv) / inv, round(y * inv) / inv)
