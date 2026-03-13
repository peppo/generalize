"""
Topological representation of a polygon layer.

The key invariant: every coordinate sequence shared between two polygons
is stored as exactly ONE TopoEdge. Simplifying that edge automatically
affects both adjacent polygons, which prevents slivers (gaps/overlaps).

Conceptually this is a planar graph:
  - Nodes  : points where three or more boundaries meet (junctions)
  - Edges  : arcs of coordinates running between two nodes
  - Polygons: defined as ordered sequences of directed edge references

Building process (to be implemented in topology_builder.py):
  1. Extract all rings from all QGIS features.
  2. Detect "junction" points — coordinates that appear in three or more rings,
     or that appear more than once in the same ring (self-touching).
  3. Split every ring at its junction points to obtain arcs.
  4. Deduplicate arcs: two rings sharing the same arc will produce the same
     canonical coordinate sequence (one forward, one reversed). Keep one
     TopoEdge and let both polygons reference it with opposite direction flags.
  5. Record left/right polygon ownership on each TopoEdge.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

@dataclass
class TopoNode:
    """
    A junction point in the planar graph.

    Nodes occur where three or more polygon boundaries meet, or at the
    endpoints of boundary arcs that touch the outer extent of the layer.
    Every TopoEdge begins and ends at a TopoNode.
    """
    id: int
    x: float
    y: float


# ---------------------------------------------------------------------------
# Edges (arcs)
# ---------------------------------------------------------------------------

@dataclass
class TopoEdge:
    """
    A topological arc — an ordered sequence of coordinates between two nodes.

    Exactly ONE TopoEdge exists for every boundary segment regardless of
    how many polygons share it.  At most two polygons can share an edge
    (left and right side).

    ``coords`` contains ALL points along the arc, including the two endpoint
    nodes, so coords[0] == nodes[start_node] and coords[-1] == nodes[end_node].

    ``left_polygon`` and ``right_polygon`` are the ids of the TopoPolygon that
    lies to the left / right when the edge is traversed in the forward
    direction (start_node → end_node).  None means "exterior / no polygon".
    """
    id: int
    start_node: int                 # id of the starting TopoNode
    end_node: int                   # id of the ending TopoNode
    coords: np.ndarray              # shape (n, 2), dtype float64; n >= 2
    left_polygon: Optional[int]     # polygon id to the left  (or None)
    right_polygon: Optional[int]    # polygon id to the right (or None)


# ---------------------------------------------------------------------------
# Rings
# ---------------------------------------------------------------------------

@dataclass
class TopoRing:
    """
    A closed ring expressed as an ordered list of directed edge references.

    Each entry is ``(edge_id, forward)`` where:
    - forward=True  → traverse the edge start_node → end_node
    - forward=False → traverse the edge end_node   → start_node  (reversed)

    The ring is implicitly closed: the end-node of the last half-edge equals
    the start-node of the first half-edge.  No coordinates are stored here —
    the actual points are always fetched from the referenced TopoEdge.
    """
    half_edges: list[tuple[int, bool]] = field(default_factory=list)

    def iter_coords(self, edges: dict[int, TopoEdge]) -> list[tuple[float, float]]:
        """
        Reconstruct the ring's coordinate sequence from the referenced edges.

        The closing duplicate point (first == last) is included so that the
        result can be passed directly to QgsGeometry.fromPolygonXY.
        """
        pts: list[tuple[float, float]] = []
        for edge_id, forward in self.half_edges:
            edge = edges[edge_id]
            seg = edge.coords if forward else edge.coords[::-1]
            # Append all points except the last one to avoid duplicating
            # the shared node between consecutive half-edges.
            pts.extend((float(x), float(y)) for x, y in seg[:-1])
        # Close the ring.
        if pts:
            pts.append(pts[0])
        return pts

    def iter_coords_numpy(self, edges: dict[int, TopoEdge]) -> np.ndarray:
        """
        Reconstruct the ring as a closed numpy array of shape (n, 2).

        The last row equals the first row (closing duplicate included).
        No Python tuples are created; all work stays in numpy.
        Used by the fast WKB geometry reconstruction path.
        """
        parts = []
        for edge_id, forward in self.half_edges:
            seg = edges[edge_id].coords          # (m, 2) numpy array
            if not forward:
                seg = seg[::-1]                  # reversed view, no copy
            parts.append(seg[:-1])               # all but the junction point
        if not parts:
            return np.empty((0, 2), dtype=np.float64)
        coords = np.vstack(parts)                # (k, 2)
        return np.vstack([coords, coords[:1]])   # close the ring


# ---------------------------------------------------------------------------
# Polygons
# ---------------------------------------------------------------------------

@dataclass
class TopoPolygon:
    """
    A single polygon part in topological form.

    Multipolygon QGIS features are split into one TopoPolygon per part so
    that every TopoPolygon has exactly one outer ring.

    Attributes
    ----------
    id           : unique id within the TopoLayer
    feature_id   : id of the originating QgsFeature
    part_index   : 0 for simple polygons; part number for multipolygons
    outer_ring   : the exterior ring
    inner_rings  : zero or more interior rings (holes)
    attributes   : the QgsFeature attribute list (preserved, not modified)
    is_multipart : True when the originating QgsFeature was a multipolygon;
                   needed to reconstruct the correct geometry type on export.
    """
    id: int
    feature_id: int
    part_index: int
    outer_ring: TopoRing
    inner_rings: list[TopoRing] = field(default_factory=list)
    attributes: list = field(default_factory=list)
    is_multipart: bool = False


# ---------------------------------------------------------------------------
# Layer (top-level container)
# ---------------------------------------------------------------------------

@dataclass
class TopoLayer:
    """
    Complete topological representation of a QGIS polygon layer.

    Invariant
    ---------
    Every coordinate arc shared between two adjacent polygons exists as
    exactly one TopoEdge.  Simplifying (or otherwise editing) that edge
    automatically affects both neighbours with no risk of slivers.

    Usage outline
    -------------
    1. Build with ``topology_builder.build(qgs_vector_layer)``  (to be written).
    2. Simplify edges with the Visvalingam algorithm operating on edge.coords.
    3. Reconstruct QgsFeatures with ``to_qgs_features()``.

    Internal lookup helpers
    -----------------------
    ``_node_lookup``  maps a snapped ``(x, y)`` tuple to a node id so that
    coordinates that are identical (within the snap tolerance) map to the
    same TopoNode.

    ``_edge_lookup``  maps a canonical edge key to an edge id so that the
    same arc encountered from the opposite polygon direction resolves to the
    same TopoEdge (but with ``forward=False``).
    The canonical key is the tuple of all coordinate pairs oriented so that
    the lexicographically-smaller endpoint comes first.
    """
    nodes: dict[int, TopoNode] = field(default_factory=dict)
    edges: dict[int, TopoEdge] = field(default_factory=dict)
    polygons: dict[int, TopoPolygon] = field(default_factory=dict)

    # -- private counters & indices (excluded from repr) --------------------
    _next_node_id: int = field(default=0, repr=False)
    _next_edge_id: int = field(default=0, repr=False)
    _next_polygon_id: int = field(default=0, repr=False)

    # (snapped_x, snapped_y) → node_id
    _node_lookup: dict[tuple[float, float], int] = field(
        default_factory=dict, repr=False
    )
    # canonical_arc_key → edge_id
    _edge_lookup: dict[tuple, int] = field(
        default_factory=dict, repr=False
    )

    # snap tolerance used during construction (units of the CRS)
    snap_tolerance: float = 0

    # -----------------------------------------------------------------------
    # Convenience / stats
    # -----------------------------------------------------------------------

    @property
    def shared_edge_count(self) -> int:
        """Number of edges that are shared between two polygons."""
        return sum(
            1 for e in self.edges.values()
            if e.left_polygon is not None and e.right_polygon is not None
        )

    @property
    def boundary_edge_count(self) -> int:
        """Number of edges that belong to only one polygon (outer boundary)."""
        return sum(
            1 for e in self.edges.values()
            if (e.left_polygon is None) != (e.right_polygon is None)
        )

    def __repr__(self) -> str:
        return (
            f"TopoLayer("
            f"nodes={len(self.nodes)}, "
            f"edges={len(self.edges)} "
            f"[shared={self.shared_edge_count}, boundary={self.boundary_edge_count}], "
            f"polygons={len(self.polygons)})"
        )
