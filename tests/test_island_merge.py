"""
Tests for island_merge.py.

Run:
    "C:\\Program Files\\QGIS 3.40.15\\apps\\Python312\\python.exe" -m pytest tests/test_island_merge.py -v

The "fast path" tests (nothing to merge) run in any QGIS headless environment.
The merge tests require the full QGIS processing provider and are skipped when
it is unavailable.
"""
import os
import sys
import unittest

_HERE      = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT  = os.path.dirname(_HERE)
_WORKSPACE = os.path.dirname(_PKG_ROOT)

for p in [_PKG_ROOT, _WORKSPACE]:
    if p not in sys.path:
        sys.path.insert(0, p)

import qgis_init  # noqa: E402  (must come after sys.path setup)

# The qgis: algorithm provider is not loaded by qgis_init; try to add it here.
_PROCESSING_AVAILABLE = False
try:
    from processing.core.Processing import Processing
    Processing.initialize()
    _PROCESSING_AVAILABLE = True
except Exception:
    pass

_needs_processing = unittest.skipUnless(
    _PROCESSING_AVAILABLE,
    'qgis: algorithms not available — run inside QGIS or extend qgis_init',
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_layer(wkts, crs='EPSG:3857'):
    """Return an in-memory polygon layer built from a list of WKT strings."""
    from qgis.core import QgsVectorLayer, QgsFeature, QgsGeometry
    layer = QgsVectorLayer(f'Polygon?crs={crs}', 'test', 'memory')
    feats = []
    for wkt in wkts:
        f = QgsFeature()
        f.setGeometry(QgsGeometry.fromWkt(wkt))
        feats.append(f)
    layer.dataProvider().addFeatures(feats)
    return layer


def _make_features(wkts):
    """Return a plain list of QgsFeatures (no layer)."""
    from qgis.core import QgsFeature, QgsGeometry
    feats = []
    for wkt in wkts:
        f = QgsFeature()
        f.setGeometry(QgsGeometry.fromWkt(wkt))
        feats.append(f)
    return feats


def _part_count(layer) -> int:
    """Total polygon parts across all features of a layer."""
    n = 0
    for feat in layer.getFeatures():
        g = feat.geometry()
        n += len(g.asMultiPolygon()) if g.isMultipart() else 1
    return n


# ---------------------------------------------------------------------------
# merge_small_islands_by_area
# ---------------------------------------------------------------------------

class TestMergeByArea(unittest.TestCase):

    def test_returns_input_when_nothing_qualifies(self):
        """Both polygons are large → fast path, original layer object returned."""
        from generalize.island_merge import merge_small_islands_by_area
        # Two large adjacent rectangles (area = 10 000 each).
        # avg_area=10000; at 50%: threshold=0.01×10000=100; both(10000)>100 → no match.
        layer = _make_layer([
            'POLYGON((0 0, 100 0, 100 100, 0 100, 0 0))',
            'POLYGON((100 0, 200 0, 200 100, 100 100, 100 0))',
        ])
        result = merge_small_islands_by_area(layer, percentage=50)
        self.assertIs(result, layer)

    def test_empty_layer_returns_input(self):
        """An empty layer is returned unchanged without error."""
        from generalize.island_merge import merge_small_islands_by_area
        layer = _make_layer([])
        result = merge_small_islands_by_area(layer, percentage=50)
        self.assertIs(result, layer)

    def test_input_layer_is_not_modified(self):
        """The caller's layer must have the same feature count after the call."""
        from generalize.island_merge import merge_small_islands_by_area
        layer = _make_layer([
            'POLYGON((0 0, 100 0, 100 100, 0 100, 0 0))',
            'POLYGON((100 0, 101 0, 101 1, 100 1, 100 0))',
        ])
        before = layer.featureCount()
        merge_small_islands_by_area(layer, percentage=50)
        self.assertEqual(layer.featureCount(), before)

    @_needs_processing
    def test_small_area_part_is_merged(self):
        """Tiny part (area=1) adjacent to a large polygon (area=10 000) is absorbed."""
        from generalize.island_merge import merge_small_islands_by_area
        layer = _make_layer([
            'POLYGON((0 0, 100 0, 100 100, 0 100, 0 0))',    # area = 10 000
            'POLYGON((100 0, 101 0, 101 1, 100 1, 100 0))',  # area = 1
        ])
        # avg_area ≈ 5000.5; at 50%: threshold = 0.01 × 5000.5 ≈ 50; tiny(1) < 50 → merged
        self.assertEqual(_part_count(layer), 2)
        result = merge_small_islands_by_area(layer, percentage=50)
        self.assertLess(_part_count(result), 2,
                        'Small part should have been merged into the large polygon')


# ---------------------------------------------------------------------------
# merge_small_islands_by_count
# ---------------------------------------------------------------------------

class TestMergeByCount(unittest.TestCase):

    def test_returns_input_when_nothing_qualifies(self):
        """Rectangles (5 stored vertices each) stay when threshold=4."""
        from generalize.island_merge import merge_small_islands_by_count
        from qgis.core import QgsFields
        # At 10%: factor=0.002; avg_count=5; threshold=max(4, 0.01)=4; both(5)>4 → no match.
        feats = _make_features([
            'POLYGON((0 0, 100 0, 100 100, 0 100, 0 0))',
            'POLYGON((100 0, 200 0, 200 100, 100 100, 100 0))',
        ])
        result = merge_small_islands_by_count(feats, 'EPSG:3857', QgsFields(), percentage=10)
        self.assertIs(result, feats)

    def test_empty_list_returns_input(self):
        """An empty feature list is returned unchanged without error."""
        from generalize.island_merge import merge_small_islands_by_count
        from qgis.core import QgsFields
        feats = []
        result = merge_small_islands_by_count(feats, 'EPSG:3857', QgsFields(), percentage=50)
        self.assertIs(result, feats)

    @_needs_processing
    def test_triangle_part_is_merged(self):
        """Triangle (4 stored vertices) adjacent to a large polygon is absorbed."""
        from generalize.island_merge import merge_small_islands_by_count
        from qgis.core import QgsFields
        feats = _make_features([
            'POLYGON((0 0, 100 0, 100 100, 0 100, 0 0))',  # 5 vertices
            'POLYGON((100 0, 101 0, 100 50, 100 0))',       # 4 vertices (triangle)
        ])
        # avg_count=(5+4)/2=4.5; at 50%: threshold=max(4, 0.01×4.5)=4; triangle(4)≤4 → merged
        result = merge_small_islands_by_count(feats, 'EPSG:3857', QgsFields(), percentage=50)
        self.assertLess(len(result), len(feats),
                        'Triangle should have been merged into the large polygon')


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    unittest.main(verbosity=2)
