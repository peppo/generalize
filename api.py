from qgis.core import QgsVectorLayer, QgsWkbTypes, QgsProject

from .topology_builder import build, remove_collinear_vertices, snap_to_self, to_qgs_features
from .visvalingam import simplify_arc, simplify_polygon


def generalize_polygon_layer(
    input_layer,
    percentage,
    output_layer=None,
    progress_callback=None,
    snap_tolerance: float = 0.0,
):
    """
    Generalize a polygon layer using the topological Visvalingam algorithm.

    Shared borders between adjacent polygons are simplified exactly once,
    so both neighbours always receive the same simplified edge — no slivers.

    :param input_layer:      QgsVectorLayer – the input polygon layer
    :param percentage:       int – reduction percentage (0–100)
    :param output_layer:     str or None – path to output shapefile (not yet implemented)
    :param progress_callback: callable(current, total) → bool
                              Called once per edge.  Return True to cancel.
    :param snap_tolerance:   float – when > 0, run remove_collinear_vertices()
                              before building topology.  Use this when the source
                              data has intermediate collinear vertices that prevent
                              shared-edge detection.  Default 0 (no preprocessing)
                              is correct for topologically perfect input.
    :return: (QgsVectorLayer, original_feature_count, new_feature_count)
    """
    if not isinstance(input_layer, QgsVectorLayer) \
            or input_layer.geometryType() != QgsWkbTypes.PolygonGeometry:
        raise ValueError("Input must be a polygon vector layer")

    if output_layer:
        raise NotImplementedError("File output not yet implemented. Use None for in-memory.")

    original_count = input_layer.featureCount()

    # --- 1. Pre-process: remove collinear (180-degree) vertices so shared
    #        borders have identical coordinate sequences in both polygons. ---
    layer = remove_collinear_vertices(input_layer) if snap_tolerance > 0 else input_layer

    # --- 2. Build topology ---
    topo = build(layer)
    edges = list(topo.edges.values())
    total_edges = len(edges)

    # --- 3. Simplify every edge exactly once ---
    for i, edge in enumerate(edges):
        if progress_callback and progress_callback(i, total_edges):
            return None, original_count, 0   # cancelled

        is_loop = (edge.start_node == edge.end_node)
        if is_loop:
            # Closed ring stored as a loop arc (first coord == last coord).
            # Use simplify_polygon so the polygon minimum of 4 points applies.
            edge.coords = simplify_polygon(edge.coords, percentage)
        else:
            # Open arc between two distinct junction nodes.
            edge.coords = simplify_arc(edge.coords, percentage)

    if progress_callback:
        progress_callback(total_edges, total_edges)

    # --- 4. Reconstruct QgsFeatures from the simplified topology ---
    new_layer = QgsVectorLayer(
        'Polygon?crs=' + input_layer.crs().authid(),
        f'{input_layer.name()}_generalized',
        'memory',
    )
    new_layer.setCrs(input_layer.crs())
    new_layer.dataProvider().addAttributes(input_layer.fields())
    new_layer.updateFields()

    new_count = 0
    for feat in to_qgs_features(topo):
        geom = feat.geometry()
        # Skip polygons that collapsed to nothing after simplification.
        if geom.isNull() or geom.isEmpty() or geom.area() <= 0:
            continue
        new_layer.dataProvider().addFeature(feat)
        new_count += 1

    QgsProject.instance().addMapLayer(new_layer)
    return new_layer, original_count, new_count
