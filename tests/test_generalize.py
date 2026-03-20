"""
Integration tests for generalize_polygon_layer().

Run from the command line (no QGIS GUI required):
    cd d:\\dev\\workspace\\generalize
    "C:\\Program Files\\QGIS 3.40.15\\apps\\Python312\\python.exe" -m pytest tests/test_generalize.py -v
"""
import os
import sys
import unittest

# ---------------------------------------------------------------------------
# Bootstrap – must happen before any qgis.* import
# ---------------------------------------------------------------------------
_HERE      = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT  = os.path.dirname(_HERE)
_WORKSPACE = os.path.dirname(_PKG_ROOT)

if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)
if _WORKSPACE not in sys.path:
    sys.path.insert(0, _WORKSPACE)

import qgis_init  # noqa: E402

from processing.core.Processing import Processing
Processing.initialize()

# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------
_DATA_ROOT     = os.path.join(_PKG_ROOT, 'test_data')
_NO_OVERLAP_DIR = os.path.join(_DATA_ROOT, 'no_overlap')
_NO_OVERLAP    = os.path.join(_NO_OVERLAP_DIR, 'no_overlap.geojson')
_NO_OVERLAP_EXPECTED = os.path.join(_NO_OVERLAP_DIR, 'no_overlap_generalized_expected.geojson')


def _load_layer(path: str):
    from qgis.core import QgsVectorLayer
    layer = QgsVectorLayer(path, os.path.basename(path), 'ogr')
    if not layer.isValid():
        raise RuntimeError(f'Could not load layer: {path}')
    return layer


def _feature_by_id(features, fid: int):
    """Return the feature whose first attribute ('id') equals fid."""
    for f in features:
        if f.attributes()[0] == fid:
            return f
    raise KeyError(f'No feature with id={fid}')


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestNoSliver(unittest.TestCase):
    """
    After generalizing no_overlap.geojson at 50%, polygons 1 and 2 share a
    boundary in the input and must still share it in the output — no gap/sliver.
    """

    @classmethod
    def setUpClass(cls):
        from generalize.api import generalize_polygon_layer
        layer = _load_layer(_NO_OVERLAP)
        features, _, _ = generalize_polygon_layer(
            layer, percentage=50, add_to_project=False
        )
        cls.features = features

    def test_no_sliver_between_polygon_1_and_2(self):
        """Polygons 1 and 2 must share a common edge, not just isolated points.

        When the shared boundary is simplified independently for each polygon,
        the two copies may drop different intermediate vertices, leaving a
        triangular gap. The polygons still touch at the surviving shared
        vertices (distance == 0) but their intersection is a GeometryCollection
        mixing a LineString with isolated Points — not a pure line.
        Topology-aware simplification must produce a purely linear intersection.
        """
        from qgis.core import QgsWkbTypes
        f1 = _feature_by_id(self.features, 1)
        f2 = _feature_by_id(self.features, 2)
        shared = f1.geometry().intersection(f2.geometry())
        geom_type = QgsWkbTypes.geometryType(shared.wkbType())
        self.assertEqual(
            geom_type, QgsWkbTypes.LineGeometry,
            f'Sliver detected: shared boundary between polygon 1 and 2 is not a '
            f'pure line (got wkbType {shared.wkbType()}, e.g. GeometryCollection '
            f'with isolated points indicates diverging simplified boundaries)',
        )


class TestNoOverlapExpected(unittest.TestCase):
    """
    After generalizing no_overlap.geojson at 50%, every feature must match
    the geometry in no_overlap_generalized_expected.geojson exactly.
    On failure a diagnostic PNG is written to test_output/.
    """

    @classmethod
    def setUpClass(cls):
        from generalize.api import generalize_polygon_layer
        layer = _load_layer(_NO_OVERLAP)
        features, _, _ = generalize_polygon_layer(
            layer, percentage=50, add_to_project=False
        )
        cls.features = {f.attributes()[0]: f for f in features}

        expected_layer = _load_layer(_NO_OVERLAP_EXPECTED)
        cls.expected = {f['id']: f for f in expected_layer.getFeatures()}

    def _render_on_failure(self, fid):
        from tests.render_geojson import render, output_path
        render(
            [_NO_OVERLAP, _NO_OVERLAP_EXPECTED],
            output_path(f'no_overlap_failure_id{fid}.png'),
            title=f'no_overlap 50% — mismatch on feature {fid}',
        )

    def _check_feature(self, fid):
        actual = self.features[fid].geometry()
        expected = self.expected[fid].geometry()
        if not actual.equals(expected):
            self._render_on_failure(fid)
            self.fail(
                f'Feature {fid} geometry does not match expected.\n'
                f'  actual vertices  : {actual.constGet().vertexCount()}\n'
                f'  expected vertices: {expected.constGet().vertexCount()}'
            )

    def test_feature_1_matches_expected(self):
        self._check_feature(1)

    def test_feature_2_matches_expected(self):
        self._check_feature(2)

    def test_feature_3_matches_expected(self):
        self._check_feature(3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
