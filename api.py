from qgis.core import QgsVectorLayer, QgsFeature, QgsGeometry, QgsWkbTypes, QgsPointXY, QgsProject
from .visvalingam import simplify_polygon
import numpy as np


def generalize_polygon_layer(input_layer, percentage, output_layer=None, progress_callback=None):
    """
    Generalize a polygon layer using the Visvalingam algorithm.

    :param input_layer: QgsVectorLayer - The input polygon layer
    :param percentage: int - Reduction percentage (0-100)
    :param output_layer: str or None - Path to output shapefile, or None for in-memory layer
    :param progress_callback: callable - Function to call with (current, total) for progress
    :return: tuple - (QgsVectorLayer, original_count, new_count)
    """
    if not isinstance(input_layer, QgsVectorLayer) or input_layer.geometryType() != QgsWkbTypes.PolygonGeometry:
        raise ValueError("Input must be a polygon vector layer")

    if output_layer:
        raise NotImplementedError("File output not yet implemented. Use None for in-memory.")

    # In-memory layer
    new_layer = QgsVectorLayer('Polygon?crs=' + input_layer.crs().authid(), f'{input_layer.name()}_generalized', 'memory')
    new_layer.setCrs(input_layer.crs())
    new_layer.dataProvider().addAttributes(input_layer.fields())
    new_layer.updateFields()

    features = list(input_layer.getFeatures())
    total_features = len(features)
    original_count = total_features
    new_count = 0

    for i, feature in enumerate(features):
        if progress_callback and progress_callback(i, total_features):
            break  # Cancelled

        geom = feature.geometry()
        if geom.isMultipart():
            parts = []
            for part in geom.asMultiPolygon():
                simplified = _simplify_geometry(part, percentage)
                if simplified:
                    parts.append(simplified)
            if parts:
                new_geom = QgsGeometry.fromMultiPolygonXY(parts)
            else:
                continue
        else:
            simplified = _simplify_geometry(geom.asPolygon(), percentage)
            if simplified:
                new_geom = QgsGeometry.fromPolygonXY(simplified)
            else:
                continue

        new_feature = QgsFeature()
        new_feature.setGeometry(new_geom)
        new_feature.setAttributes(feature.attributes())
        new_layer.dataProvider().addFeature(new_feature)
        new_count += 1

    if progress_callback:
        progress_callback(total_features, total_features)

    # Add to project
    QgsProject.instance().addMapLayer(new_layer)

    return new_layer, original_count, new_count


def _simplify_geometry(polygon, percentage):
    if not polygon:
        return None
    outer_ring = polygon[0]
    if len(outer_ring) < 4:
        return polygon

    coords = np.array([(p.x(), p.y()) for p in outer_ring])
    simplified_coords = simplify_polygon(coords, percentage)
    if len(simplified_coords) < 4:
        return None
    simplified_ring = [QgsPointXY(x, y) for x, y in simplified_coords]
    return [simplified_ring] + polygon[1:]