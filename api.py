import time

import processing
from qgis.core import QgsVectorLayer, QgsWkbTypes, QgsProject, QgsMessageLog, Qgis

from .topology_builder import build, dissolve_small_rings, repair_ring_inversions, remove_collinear_vertices, snap_to_self, to_qgs_features
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
    dissolve_small: bool = False,
    repair_inversions: bool = False,
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

    # Progress weights based on measured timing on gemeinden_bayern (50%):
    #   unconstrained: topology 96 %, simplification  3 %, reconstruction 1 %
    W_TOPO = 96.0
    W_SIMP =  3.0

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

        edge_other_rings: dict[int, list] = {}

        # Snapshot original coords before simplification so repair_ring_inversions
        # can restore removed points if needed.
        original_edge_coords = {edge.id: edge.coords.copy() for edge in edges}

        # --- 3. Simplify every edge exactly once ---
        _log(f"Simplifying {total_edges} edges …")
        t2 = time.perf_counter()
        for i, edge in enumerate(edges):
            _set_progress(W_TOPO + W_SIMP * i / total_edges)

            other_rings = edge_other_rings.get(edge.id, [])
            is_loop = (edge.start_node == edge.end_node)
            if is_loop:
                edge.coords = simplify_polygon(edge.coords, percentage,                                               
                                               other_rings=other_rings)
            else:
                edge.coords = simplify_arc(edge.coords, percentage,
                                           other_rings=other_rings)

        _set_progress(W_TOPO + W_SIMP)

        simplified_vertices = sum(len(e.coords) for e in edges)
        _log(f"Simplification done in {time.perf_counter() - t2:.1f}s — "
             f"{total_vertices:,} → {simplified_vertices:,} vertices "
             f"({100 * (1 - simplified_vertices / total_vertices):.1f}% reduction)")

        # --- 4. Repair self-intersecting rings (optional) ---
        if repair_inversions:
            t_ir = time.perf_counter()
            n_repairs, n_invalid = repair_ring_inversions(topo, original_edge_coords)
            _log(f"Inversion repair: {n_invalid} ring(s) needed repair, "
                 f"{n_repairs} action(s) taken in {time.perf_counter() - t_ir:.1f}s")

        # --- 5. Drop small rings / parts (dissolve_small mode) ---
        if dissolve_small:
            t_ds = time.perf_counter()
            n_parts, n_holes = dissolve_small_rings(topo)
            if n_parts or n_holes:
                _log(f"Dissolve small: removed {n_parts} part(s) and "
                     f"{n_holes} hole(s) in {time.perf_counter() - t_ds:.1f}s")

        # --- 6. Collect QgsFeatures from the simplified topology ---
        t5 = time.perf_counter()
        _log("Reconstructing features …")
        features = []
        skipped = 0
        for feat in to_qgs_features(topo):
            geom = feat.geometry()
            if geom.isNull() or geom.isEmpty() or geom.area() <= 0:
                skipped += 1
                continue
            features.append(feat)
        _log(f"Reconstruction done in {time.perf_counter() - t5:.1f}s")

    except _Cancelled:
        _log("Cancelled by user.")
        return None, original_count, 0

    new_count = len(features)
    if skipped:
        _log(f"Warning: {skipped} feature(s) collapsed to empty geometry and were skipped.")

    # --- 7. Post-simplification validity check (log only) ---
    invalid_after = sum(1 for f in features if not f.geometry().isGeosValid())
    if invalid_after == 0:
        _log(f"Post-simplification check: all {new_count} output geometries are valid.")
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
