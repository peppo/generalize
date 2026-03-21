import time

import processing
from qgis.core import QgsVectorLayer, QgsWkbTypes, QgsProject, QgsMessageLog, Qgis

from .topology_builder import build, dissolve_small_rings, remove_collinear_vertices, snap_to_self, to_qgs_features
from .visvalingam import simplify_arc, simplify_polygon

LOG_TAG = "Generalize"


def _log(msg):
    QgsMessageLog.logMessage(msg, LOG_TAG, Qgis.Info)


class _Cancelled(Exception):
    """Raised by _set_progress when the caller signals cancellation."""


def _check_validity(layer) -> int:
    """
    Run the QGIS 'Check Validity' algorithm (GEOS strict) on *layer*.

    Returns the number of invalid features found.
    """
    result = processing.run('qgis:checkvalidity', {
        'INPUT_LAYER': layer,
        'METHOD': 2,                          # GEOS strict
        'IGNORE_RING_SELF_INTERSECTION': False,
        'VALID_OUTPUT': 'memory:',
        'INVALID_OUTPUT': 'memory:',
        'ERROR_OUTPUT': 'memory:',
    })
    return result['INVALID_OUTPUT'].featureCount()


def generalize_polygon_layer(
    input_layer,
    percentage,
    output_layer=None,
    progress_callback=None,
    snap_tolerance: float = 0.0,
    add_to_project: bool = True,
    constrained: bool = False,
    dissolve_small: bool = False,
):
    """
    Generalize a polygon layer using the topological Visvalingam algorithm.

    Shared borders between adjacent polygons are simplified exactly once,
    so both neighbours always receive the same simplified edge — no slivers.

    :param input_layer:      QgsVectorLayer – the input polygon layer
    :param percentage:       int – reduction percentage (0–100)
    :param output_layer:     str or None – path to output shapefile (not yet implemented)
    :param progress_callback: callable(pct: float) → bool
                              Called with overall progress 0–100. Return True to cancel.
    :param snap_tolerance:   float – when > 0, run remove_collinear_vertices()
                              before building topology.  Use this when the source
                              data has intermediate collinear vertices that prevent
                              shared-edge detection.  Default 0 (no preprocessing)
                              is correct for topologically perfect input.
    :param constrained:      bool – when True, use the crossing-guarded cascade
                              algorithm that prevents self-intersections.  Slower
                              but guarantees valid output geometry at high
                              generalization rates.  Default False.
    :param dissolve_small:   bool – when True, drop polygon parts and holes
                              whose area < 2·d² (d = global average segment
                              length after simplification).  Parts and their
                              corresponding holes are removed atomically so no
                              gap or overlap is introduced.  At least one part
                              per feature is always preserved.  Default False.
    :return: (QgsVectorLayer, original_feature_count, new_feature_count)
    """
    if not isinstance(input_layer, QgsVectorLayer) \
            or input_layer.geometryType() != QgsWkbTypes.PolygonGeometry:
        raise ValueError("Input must be a polygon vector layer")

    if output_layer:
        raise NotImplementedError("File output not yet implemented. Use None for in-memory.")

    # --- 0. Geometry validity check ---
    _log("Checking geometry validity …")
    invalid_count = _check_validity(input_layer)
    if invalid_count > 0:
        raise ValueError(
            f"Input layer contains {invalid_count} invalid feature(s). "
            "Please repair the geometry (e.g. with 'Fix geometries') before generalizing."
        )

    original_count = input_layer.featureCount()
    _log(f"Starting generalization of '{input_layer.name()}' "
         f"({original_count} features, {percentage}% reduction)")
    t0 = time.perf_counter()

    # --- 1. Pre-process: remove collinear (180-degree) vertices so shared
    #         borders have identical coordinate sequences in both polygons. ---
    if snap_tolerance > 0:
        _log("Pre-processing: removing collinear vertices …")
        layer = remove_collinear_vertices(input_layer)
    else:
        layer = input_layer

    # Progress is split across phases by expected cost:
    #   0 –  80 %  topology build  (by ring)
    #  80 –  98 %  simplification  (by edge)
    #  98 – 100 %  reconstruction
    W_TOPO = 80.0
    W_SIMP = 18.0

    def _set_progress(pct):
        """Update progress; raise _Cancelled if the callback signals cancellation."""
        if progress_callback and progress_callback(pct):
            raise _Cancelled()

    try:
        # --- 2. Build topology ---
        t1 = time.perf_counter()

        def _topo_progress(current, total):
            _set_progress(W_TOPO * current / total)

        topo = build(layer, progress_callback=_topo_progress, phase_callback=_log)
        edges = list(topo.edges.values())
        total_edges = len(edges)
        total_vertices = sum(len(e.coords) for e in edges)
        _log(f"Topology built in {time.perf_counter() - t1:.1f}s — "
             f"{total_edges} edges, {total_vertices:,} vertices")
        _set_progress(W_TOPO)

        # --- 3. Build constraint map (constrained mode only) ---
        # For each edge, collect the original coords of:
        #   (a) all OTHER rings in the same polygon part (cross-ring: outer ↔ hole)
        #   (b) all OTHER arcs in the same ring          (cross-arc: sibling arcs)
        #   (c) all rings of OTHER parts of the same MultiPolygon feature
        #                                                (cross-part: part separation)
        # All sets are passed as static arrays to the crossing guard so that
        # simplified chords cannot cross any of these boundaries.
        edge_other_rings: dict[int, list] = {}
        if constrained:
            from collections import defaultdict as _defaultdict

            # Cache original edge coords before any simplification.
            edge_coord_cache: dict[int, object] = {
                eid: e.coords.copy() for eid, e in topo.edges.items()
            }

            # Cache full ring coord arrays (original, before any simplification).
            ring_coord_cache: dict[int, object] = {}
            for poly in topo.polygons.values():
                for ring in [poly.outer_ring] + poly.inner_rings:
                    rid = id(ring)
                    if rid not in ring_coord_cache:
                        ring_coord_cache[rid] = ring.iter_coords_numpy(topo.edges)

            # (c) Group parts by feature_id to build cross-part coord lists.
            feature_parts: dict = _defaultdict(list)
            for poly in topo.polygons.values():
                feature_parts[poly.feature_id].append(poly)

            # For each part, collect ring coord arrays from all OTHER parts of
            # the same feature.
            part_cross_part_coords: dict = {}   # id(poly) → list[np.ndarray]
            for _, parts in feature_parts.items():
                if len(parts) == 1:
                    part_cross_part_coords[id(parts[0])] = []
                    continue
                all_part_ring_coords = {
                    id(p): [
                        ring_coord_cache[id(r)]
                        for r in [p.outer_ring] + p.inner_rings
                    ]
                    for p in parts
                }
                for poly in parts:
                    part_cross_part_coords[id(poly)] = [
                        arr
                        for pid, coords_list in all_part_ring_coords.items()
                        if pid != id(poly)
                        for arr in coords_list
                    ]

            for poly in topo.polygons.values():
                all_rings = [poly.outer_ring] + poly.inner_rings
                cross_part_coords = part_cross_part_coords[id(poly)]

                for ring_idx, ring in enumerate(all_rings):
                    # (a) Cross-ring constraints.
                    cross_ring_coords = [
                        ring_coord_cache[id(r)]
                        for j, r in enumerate(all_rings) if j != ring_idx
                    ]
                    # (b) Sibling arc ids for this ring.
                    sibling_arc_ids = [eid for eid, _ in ring.half_edges]

                    for edge_id, _ in ring.half_edges:
                        if edge_id not in edge_other_rings:
                            edge_other_rings[edge_id] = (set(), [])
                        seen, lst = edge_other_rings[edge_id]

                        # Add cross-ring coords.
                        for arr in cross_ring_coords:
                            arr_key = id(arr)
                            if arr_key not in seen:
                                seen.add(arr_key)
                                lst.append(arr)

                        # Add sibling arc coords (cross-arc constraint).
                        for sib_id in sibling_arc_ids:
                            if sib_id == edge_id:
                                continue
                            arr = edge_coord_cache[sib_id]
                            arr_key = id(arr)
                            if arr_key not in seen:
                                seen.add(arr_key)
                                lst.append(arr)

                        # Add other-part coords (cross-part constraint).
                        for arr in cross_part_coords:
                            arr_key = id(arr)
                            if arr_key not in seen:
                                seen.add(arr_key)
                                lst.append(arr)

            # Unwrap the (seen, lst) tuples.
            edge_other_rings = {eid: lst for eid, (_, lst) in edge_other_rings.items()}

        # --- 4. Simplify every edge exactly once ---
        _log(f"Simplifying {total_edges} edges …")
        t2 = time.perf_counter()
        for i, edge in enumerate(edges):
            _set_progress(W_TOPO + W_SIMP * i / total_edges)

            other_rings = edge_other_rings.get(edge.id, [])
            is_loop = (edge.start_node == edge.end_node)
            if is_loop:
                edge.coords = simplify_polygon(edge.coords, percentage,
                                               constrained=constrained,
                                               other_rings=other_rings)
            else:
                edge.coords = simplify_arc(edge.coords, percentage,
                                           constrained=constrained,
                                           other_rings=other_rings)

        _set_progress(W_TOPO + W_SIMP)

        simplified_vertices = sum(len(e.coords) for e in edges)
        _log(f"Simplification done in {time.perf_counter() - t2:.1f}s — "
             f"{total_vertices:,} → {simplified_vertices:,} vertices "
             f"({100 * (1 - simplified_vertices / total_vertices):.1f}% reduction)")

        # --- 4.5. Drop small rings / parts (dissolve_small mode) ---
        if dissolve_small:
            t_ds = time.perf_counter()
            n_parts, n_holes = dissolve_small_rings(topo)
            if n_parts or n_holes:
                _log(f"Dissolve small: removed {n_parts} part(s) and "
                     f"{n_holes} hole(s) in {time.perf_counter() - t_ds:.1f}s")

        # --- 5. Collect QgsFeatures from the simplified topology ---
        _log("Reconstructing features …")
        features = []
        skipped = 0
        for feat in to_qgs_features(topo):
            geom = feat.geometry()
            if geom.isNull() or geom.isEmpty() or geom.area() <= 0:
                skipped += 1
                continue
            features.append(feat)

    except _Cancelled:
        _log("Cancelled by user.")
        return None, original_count, 0

    new_count = len(features)
    if skipped:
        _log(f"Warning: {skipped} feature(s) collapsed to empty geometry and were skipped.")

    # --- 5. Post-simplification validity check (and repair in constrained mode) ---
    # The per-arc constraint prevents most self-intersections, but cannot cover
    # all edge cases — e.g. two parts of a MultiPolygon that originally touch
    # at a single junction node can end up with slightly overlapping simplified
    # shapes (both sides add chords near the junction point).  In constrained
    # mode we apply makeValid() as a safety net for any remaining invalid
    # geometry.  makeValid() is cheap (no-op for already-valid geometry) and
    # produces minimal geometry changes (GEOS "structure" method).
    invalid_after = 0
    repaired = 0
    for feat in features:
        geom = feat.geometry()
        if geom.isGeosValid():
            continue
        invalid_after += 1
        if constrained:
            fixed = geom.makeValid()
            if not fixed.isNull() and fixed.area() > 0:
                feat.setGeometry(fixed)
                repaired += 1
    if invalid_after == 0:
        _log(f"Post-simplification check: all {new_count} output geometries are valid.")
    elif constrained:
        _log(f"Post-simplification check: {invalid_after} geometry(s) repaired with makeValid().")
    else:
        _log(f"Post-simplification check: {invalid_after} of {new_count} feature(s) have invalid geometry.")

    _log(f"Done in {time.perf_counter() - t0:.1f}s — {new_count} features ready.")

    if not add_to_project:
        return features, original_count, new_count

    new_layer = _make_layer(input_layer, features)
    QgsProject.instance().addMapLayer(new_layer)
    _log(f"Output layer '{new_layer.name()}' added to project.")
    return new_layer, original_count, new_count


def _make_layer(input_layer, features):
    """Create an in-memory QgsVectorLayer and populate it with ``features``."""
    new_layer = QgsVectorLayer(
        'Polygon?crs=' + input_layer.crs().authid(),
        f'{input_layer.name()}_generalized',
        'memory',
    )
    new_layer.setCrs(input_layer.crs())
    new_layer.dataProvider().addAttributes(input_layer.fields())
    new_layer.updateFields()
    new_layer.dataProvider().addFeatures(features)
    return new_layer
