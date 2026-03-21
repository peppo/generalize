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
_INVERT        = os.path.join(_DATA_ROOT, 'invert',  'invert.geojson')
_INVERT2       = os.path.join(_DATA_ROOT, 'invert2', 'invert2.geojson')
_ISLAND        = os.path.join(_DATA_ROOT, 'island_intersect', 'island_intersect.geojson')


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


class TestNoOverlapTopology(unittest.TestCase):
    """
    Verify that the topology builder correctly detects shared edges in
    no_overlap.geojson so the sliver root-cause is clear.
    """

    @classmethod
    def setUpClass(cls):
        from generalize.topology_builder import build
        layer = _load_layer(_NO_OVERLAP)
        cls.topo = build(layer)

    def test_shared_edges_detected(self):
        """Polygons 1-2 and 2-3 share boundaries — at least 2 shared edges expected."""
        self.assertGreater(
            self.topo.shared_edge_count, 0,
            f'No shared edges found in no_overlap.geojson — topology detection broken.\n'
            f'  total edges : {len(self.topo.edges)}\n'
            f'  shared edges: {self.topo.shared_edge_count}\n'
            f'  {self.topo}'
        )

    def test_topology_stats(self):
        """Print topology stats for diagnosis (never fails)."""
        print(
            f'\n  no_overlap topology: {self.topo}\n'
            f'  shared={self.topo.shared_edge_count}  '
            f'boundary={self.topo.boundary_edge_count}  '
            f'total={len(self.topo.edges)}'
        )


class TestInvertValidGeometry(unittest.TestCase):
    """
    After generalizing invert.geojson at 90%, every output feature must be a
    valid geometry (no self-intersections, no bowtie rings, etc.).

    Aggressive simplification of a complex concave polygon can cause ring
    inversion — simplified edges cross each other, producing a self-intersecting
    (invalid) geometry that breaks downstream operations.
    """

    @classmethod
    def setUpClass(cls):
        from generalize.api import generalize_polygon_layer
        layer = _load_layer(_INVERT)
        features, _, _ = generalize_polygon_layer(
            layer, percentage=90, add_to_project=False, constrained=True
        )
        cls.features = features

    def test_all_features_are_valid(self):
        """Every generalized feature must pass QGIS isGeosValid()."""
        invalid = []
        for f in self.features:
            geom = f.geometry()
            if not geom.isGeosValid():
                fid = f.attributes()[0]
                invalid.append(
                    f'feature id={fid}: {geom.lastError()}'
                )
        self.assertEqual(
            invalid, [],
            'Invalid geometries after 90% generalization of invert.geojson:\n'
            + '\n'.join(f'  {msg}' for msg in invalid),
        )


class TestIslandIntersectValidGeometry(unittest.TestCase):
    """
    After generalizing island_intersect.geojson at 90%, every output feature
    must be a valid geometry.

    The dataset contains MultiPolygons whose outer ring wraps around hole rings
    (islands).  At aggressive simplification rates the simplified outer ring can
    cross a hole boundary — producing a Self-intersection that is distinct from
    the within-ring inversion tested in TestInvert*: here two *different* rings
    of the same polygon cross each other.  The constrained cascade prevents
    within-ring crossings but not cross-ring crossings, so this test exposes
    the remaining gap.
    """

    @classmethod
    def setUpClass(cls):
        from generalize.api import generalize_polygon_layer
        layer = _load_layer(_ISLAND)
        features, _, _ = generalize_polygon_layer(
            layer, percentage=90, add_to_project=False, constrained=True
        )
        cls.features = features

    def test_all_features_are_valid(self):
        """Every generalized feature must pass QGIS isGeosValid()."""
        invalid = []
        for f in self.features:
            geom = f.geometry()
            if not geom.isGeosValid():
                fid = f.attributes()[0] if f.attributes() else f.id()
                invalid.append(
                    f'feature id={fid}: {geom.lastError()}'
                )
        self.assertEqual(
            invalid, [],
            'Invalid geometries after 90% generalization of island_intersect.geojson:\n'
            + '\n'.join(f'  {msg}' for msg in invalid),
        )


class TestInvert2ValidGeometry(unittest.TestCase):
    """
    After generalizing invert2.geojson at 90%, every output feature must be a
    valid geometry (no self-intersections).
    """

    @classmethod
    def setUpClass(cls):
        from generalize.api import generalize_polygon_layer
        layer = _load_layer(_INVERT2)
        features, _, _ = generalize_polygon_layer(
            layer, percentage=90, add_to_project=False, constrained=True
        )
        cls.features = features

    def test_all_features_are_valid(self):
        """Every generalized feature must pass QGIS isGeosValid()."""
        invalid = []
        for f in self.features:
            geom = f.geometry()
            if not geom.isGeosValid():
                fid = f.attributes()[0]
                invalid.append(
                    f'feature id={fid}: {geom.lastError()}'
                )
        self.assertEqual(
            invalid, [],
            'Invalid geometries after 90% generalization of invert2.geojson:\n'
            + '\n'.join(f'  {msg}' for msg in invalid),
        )


if __name__ == '__main__':
    unittest.main(verbosity=2)
