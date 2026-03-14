import processing
from qgis.core import QgsVectorLayer, QgsMessageLog, Qgis

LOG_TAG = "Generalize"


def _log(msg):
    QgsMessageLog.logMessage(msg, LOG_TAG, Qgis.Info)


def merge_small_islands_by_area(input_layer, percentage: int):
    """
    Pre-processing step: merge multipolygon parts with a tiny area into their
    largest-area neighbour.

    Works on an in-memory copy — the caller's layer is never modified.

    A part qualifies when:
        area < area_threshold
        area_threshold = percentage × 0.02 × avg_area  (max 2 % of average)

    The absorbing neighbour keeps its own attributes (largest area wins).

    Returns the processed QgsVectorLayer (in-memory, not added to project),
    or *input_layer* unchanged when no parts qualify.
    """
    # Explode multipolygons to single parts (in-memory copy, input untouched).
    single = processing.run('native:multiparttosingleparts', {
        'INPUT': input_layer,
        'OUTPUT': 'memory:',
    })['OUTPUT']

    features = list(single.getFeatures())
    if not features:
        return input_layer

    factor = percentage / 100.0 * 0.02

    all_areas = [f.geometry().area() for f in features]
    avg_area = sum(all_areas) / len(all_areas)
    area_threshold = factor * avg_area

    small_ids = [
        f.id() for f, area in zip(features, all_areas)
        if area < area_threshold
    ]

    if not small_ids:
        _log("Pre island merge: no parts below area threshold.")
        return input_layer

    _log(f"Pre island merge: {len(small_ids)} part(s) with area < {area_threshold:.1f}, "
         f"merging into largest neighbour …")

    single.selectByIds(small_ids)
    result = processing.run('qgis:eliminateselectedpolygons', {
        'INPUT': single,
        'MODE': 0,          # 0 = largest area absorbs the island
        'OUTPUT': 'memory:',
    })['OUTPUT']

    return result


def merge_small_islands_by_count(features, crs_authid, fields, percentage: int):
    """
    Post-processing step: merge parts that have very few vertices (after
    simplification) into their largest-area neighbour.

    A part qualifies when:
        vertex_count ≤ count_threshold
        count_threshold = max(4, percentage × 0.02 × avg_vertex_count)
        4 stored vertices = a triangle (3 distinct points + closing vertex),
        always merged regardless of reduction rate.

    Returns a new list of QgsFeatures, or the original list unchanged when no
    parts qualify.
    """
    if not features:
        return features

    # Build a temp layer so we can run eliminateselectedpolygons on it.
    temp = QgsVectorLayer(f'Polygon?crs={crs_authid}', '_temp', 'memory')
    temp.dataProvider().addAttributes(fields)
    temp.updateFields()
    temp.dataProvider().addFeatures(features)

    temp_features = list(temp.getFeatures())

    factor = percentage / 100.0 * 0.02
    all_counts = [f.geometry().constGet().vertexCount() for f in temp_features]
    avg_count = sum(all_counts) / len(all_counts)
    # Floor of 4: a triangle (3 distinct vertices + closing) is always a candidate.
    count_threshold = max(4, factor * avg_count)

    small_ids = [
        f.id() for f, count in zip(temp_features, all_counts)
        if count <= count_threshold
    ]

    if not small_ids:
        _log("Post island merge: no parts below vertex threshold.")
        return features

    _log(f"Post island merge: {len(small_ids)} part(s) with ≤ {count_threshold:.0f} vertices, "
         f"merging into largest neighbour …")

    temp.selectByIds(small_ids)
    result = processing.run('qgis:eliminateselectedpolygons', {
        'INPUT': temp,
        'MODE': 0,          # 0 = largest area absorbs the island
        'OUTPUT': 'memory:',
    })['OUTPUT']

    return list(result.getFeatures())
