"""
Integration tests for generalize_polygon_layer().

Run from the command line (no QGIS GUI required):
    cd d:\\dev\\workspace\\generalize
    "C:\\Program Files\\QGIS 3.40.15\\apps\\Python312\\python.exe" -m pytest tests/test_generalize.py -v
"""
import os
import sys
import unittest
import pytest

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
_INVERT        = os.path.join(_DATA_ROOT, 'invert',  'invert.geojson')
_INVERT2       = os.path.join(_DATA_ROOT, 'invert2', 'invert2.geojson')
_INVERT4       = os.path.join(_DATA_ROOT, 'invert4', 'invert4.geojson')
_ISLAND        = os.path.join(_DATA_ROOT, 'island_intersect', 'island_intersect.geojson')
_GEMEINDEN_BAYERN       = os.path.join(_DATA_ROOT, 'gemeinden_bayern',       'VerwaltungsEinheit.shp')
_GEMEINDEN_DEUTSCHLAND  = os.path.join(_DATA_ROOT, 'gemeinden_deutschland',  'Gemeinden_Deutschland.shp') + '|option:SHAPE_RESTORE_SHX=YES'
_GREAT_BRITAIN          = os.path.join(_DATA_ROOT, 'great_britain',           'westminster_const_region.shp')
_FRANCE                 = os.path.join(_DATA_ROOT, 'france',                  'communes-20220101.shp')
_UNTRASRIED             = os.path.join(_DATA_ROOT, 'untrasried',              'untrasried.geojson')
_TOO_FEW_POINTS         = os.path.join(_DATA_ROOT, 'too_few_points',         'too_few_points.geojson')
_SELF_INTERSECTION      = os.path.join(_DATA_ROOT, 'self_intersection',       'self_intersection.geojson')
_SLIVER2               = os.path.join(_DATA_ROOT, 'sliver2',              'sliver2.geojson')
_SLIVER3               = os.path.join(_DATA_ROOT, 'sliver3',              'sliver3.geojson')
_LOST2                 = os.path.join(_DATA_ROOT, 'lost',                 'lost2.geojson')
_LOST3                 = os.path.join(_DATA_ROOT, 'lost',                 'lost3.geojson')
_INVERT5AT98           = os.path.join(_DATA_ROOT, 'invert',               'invert5at98.geojson')


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
            layer, percentage=90, add_to_project=False,
            repair_inversions=True,
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
            layer, percentage=90, add_to_project=False,
            dissolve_small=True, repair_inversions=True,
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
            layer, percentage=90, add_to_project=False,
            repair_inversions=True,
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


class TestInvert4ValidGeometry(unittest.TestCase):
    """
    After generalizing invert4.geojson at 90%, every output feature must be a
    valid geometry (no self-intersections, including holes).
    """

    @classmethod
    def setUpClass(cls):
        from generalize.api import generalize_polygon_layer
        layer = _load_layer(_INVERT4)
        features, _, _ = generalize_polygon_layer(
            layer, percentage=90, add_to_project=False,
            repair_inversions=True,
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
            'Invalid geometries after 90% generalization of invert4.geojson:\n'
            + '\n'.join(f'  {msg}' for msg in invalid),
        )


class TestTooFewPointsValidGeometry(unittest.TestCase):
    """
    Regression test for Neureichenau (DE092720136136).

    At 50% generalisation this municipality contains a small island polygon
    whose ring is built from multiple topology arcs.  When each arc is
    simplified independently down to its two junction nodes the reconstructed
    ring ends up with fewer than 3 distinct points — a degenerate geometry
    that QGIS/GEOS rejects as 'Too few points in geometry component'.

    The fix: after topology reconstruction, drop any ring with < 3 distinct
    vertices instead of propagating the degenerate component.
    """

    PERCENTAGE = 50

    @classmethod
    def setUpClass(cls):
        from generalize.api import generalize_polygon_layer
        layer = _load_layer(_TOO_FEW_POINTS)
        features, _, _ = generalize_polygon_layer(
            layer, percentage=cls.PERCENTAGE, add_to_project=False,
            dissolve_small=True, repair_inversions=True,
        )
        cls.features = features

    def test_all_features_are_valid(self):
        """Every generalized feature must pass QGIS isGeosValid()."""
        invalid = []
        for f in self.features:
            geom = f.geometry()
            if not geom.isGeosValid():
                idx = f.fieldNameIndex('gml_id')
                fid = f.attribute(idx) if idx >= 0 else f.id()
                invalid.append(f'  {fid}: {geom.lastError()}')
        self.assertEqual(
            invalid, [],
            'Invalid geometries after 50% generalisation of too_few_points.geojson:\n'
            + '\n'.join(invalid),
        )


class TestSelfIntersectionValidGeometry(unittest.TestCase):
    """
    Regression test for Jettingen-Scheppach (DE097740144144).

    At 50% generalisation this polygon produces a Self-intersection even with
    constrained=True.  The constrained cascade guards each topology arc against
    self-intersections *within that arc*, but a ring is composed of multiple
    independent arcs.  After simplification, two separate arcs of the same ring
    can cross each other — the per-arc constraint does not see this.

    The fix: after topology reconstruction, detect and repair cross-arc
    self-intersections (e.g. by using GEOS buffer(0) or by post-processing
    the ring).
    """

    PERCENTAGE = 50

    @classmethod
    def setUpClass(cls):
        from generalize.api import generalize_polygon_layer
        layer = _load_layer(_SELF_INTERSECTION)
        features, _, _ = generalize_polygon_layer(
            layer, percentage=cls.PERCENTAGE, add_to_project=False,
            repair_inversions=True,
        )
        cls.features = features

    def test_all_features_are_valid(self):
        """Every generalized feature must pass QGIS isGeosValid()."""
        invalid = []
        for f in self.features:
            geom = f.geometry()
            if not geom.isGeosValid():
                idx = f.fieldNameIndex('gml_id')
                fid = f.attribute(idx) if idx >= 0 else f.id()
                invalid.append(f'  {fid}: {geom.lastError()}')
        self.assertEqual(
            invalid, [],
            'Invalid geometries after 50% generalisation of self_intersection.geojson:\n'
            + '\n'.join(invalid),
        )


@pytest.mark.slow
class TestGemeindenBayernValidGeometry(unittest.TestCase):
    """
    After generalizing gemeinden_bayern at 50% with constrained=True, every
    output feature must pass the QGIS 'Check Validity' algorithm (GEOS strict).

    On failure the test prints a summary of every error message produced by
    the validity checker, including the exact location and a description, so
    the failing geometry can be reproduced and inspected in QGIS.
    """

    PERCENTAGE = 50

    @classmethod
    def setUpClass(cls):
        import processing
        from qgis.core import QgsVectorLayer
        from generalize.api import generalize_polygon_layer

        layer = _load_layer(_GEMEINDEN_BAYERN)
        features, _, _ = generalize_polygon_layer(
            layer, percentage=cls.PERCENTAGE, add_to_project=False,
        )
        cls.features = features

        # Build a temporary in-memory layer for the validity check algorithm.
        temp = QgsVectorLayer(
            f'Polygon?crs={layer.crs().authid()}', '_temp', 'memory'
        )
        temp.dataProvider().addAttributes(layer.fields())
        temp.updateFields()
        temp.dataProvider().addFeatures(features)

        result = processing.run('qgis:checkvalidity', {
            'INPUT_LAYER': temp,
            'METHOD': 2,                          # GEOS strict
            'IGNORE_RING_SELF_INTERSECTION': False,
            'VALID_OUTPUT':   'memory:',
            'INVALID_OUTPUT': 'memory:',
            'ERROR_OUTPUT':   'memory:',
        })
        cls.invalid_layer = result['INVALID_OUTPUT']
        cls.error_layer   = result['ERROR_OUTPUT']

    def test_all_features_are_valid(self):
        """Every generalized feature must pass QGIS/GEOS validity check."""
        invalid_count = self.invalid_layer.featureCount()
        if invalid_count == 0:
            return

        # Report invalid feature names (gml_id / name) from the invalid layer.
        invalid_names = []
        for f in self.invalid_layer.getFeatures():
            gml_id = f.attribute('gml_id') or f.id()
            name   = f.attribute('name') or ''
            invalid_names.append(f'  {gml_id} ({name})')

        # Report error messages + coordinates from the error point layer.
        error_msgs = []
        for f in self.error_layer.getFeatures():
            pt  = f.geometry().asPoint()
            msg = f.attribute('message') or ''
            error_msgs.append(f'  ({pt.x():.2f}, {pt.y():.2f}): {msg}')

        self.fail(
            f'{invalid_count} invalid feature(s) after {self.PERCENTAGE}% '
            f'generalization of gemeinden_bayern:\n'
            'Invalid features:\n' + '\n'.join(invalid_names) + '\n'
            'Errors:\n' + '\n'.join(error_msgs)
        )


@pytest.mark.slow
class TestGemeindenBayernNoSliver(unittest.TestCase):
    """
    After generalizing gemeinden_bayern at 90% — with both the fast
    (unconstrained) and the constrained algorithm — adjacent municipalities
    must still share a common boundary.

    The topology invariant guarantees this: every shared edge is simplified
    exactly once and referenced by both neighbours, so the simplified
    boundary is always identical on both sides.  A sliver (gap or overlap)
    would appear as a GeometryCollection intersection between two features
    that should share a pure line.

    For each adjacent pair (distance ≤ 1 m) the intersection must be either:
      • PointGeometry   — features touch at a corner only (valid)
      • LineGeometry    — features share a common edge (ideal)
    A GeometryCollection (mixed line + isolated points) or a PolygonGeometry
    (overlap) indicates a broken shared boundary.
    """

    PERCENTAGE = 90

    @classmethod
    def setUpClass(cls):
        from generalize.api import generalize_polygon_layer
        layer = _load_layer(_GEMEINDEN_BAYERN)

        cls.features, _, _ = generalize_polygon_layer(
            layer, percentage=cls.PERCENTAGE, add_to_project=False,
        )

    @staticmethod
    def _find_slivers(features, valid_only=False):
        """
        Return a list of descriptions for adjacent feature pairs whose
        intersection has non-zero polygon area (a real overlap / sliver).

        A GeometryCollection whose area is 0 (e.g. a mix of line segments and
        corner points at 3-way junctions) is not flagged -- only actual
        polygon-area overlaps matter.

        Parameters
        ----------
        features   : list of QgsFeature
        valid_only : when True, skip pairs that include an invalid (self-
                     intersecting) feature.  Use this for the unconstrained
                     mode where self-intersecting features are expected and
                     would otherwise produce spurious GEOS polygon results.
        """
        from qgis.core import QgsSpatialIndex
        index = QgsSpatialIndex()
        by_id = {}
        for f in features:
            index.addFeature(f)
            by_id[f.id()] = f

        if valid_only:
            valid = {f.id(): f.geometry().isGeosValid() for f in features}

        slivers = []
        checked = set()
        for f in features:
            fid = f.id()
            if valid_only and not valid.get(fid, True):
                continue
            for cid in index.intersects(f.geometry().boundingBox()):
                if cid <= fid or (fid, cid) in checked:
                    continue
                checked.add((fid, cid))
                if valid_only and not valid.get(cid, True):
                    continue
                g1 = f.geometry()
                g2 = by_id[cid].geometry()
                if g1.distance(g2) > 1.0:   # not adjacent
                    continue
                shared = g1.intersection(g2)
                if shared.isEmpty():
                    continue
                area = shared.area()
                if area > 0:
                    slivers.append(
                        f'features {fid} and {cid}: '
                        f'intersection area={area:.6f}'
                    )
        return slivers

    def test_no_sliver(self):
        slivers = self._find_slivers(self.features)
        self.assertEqual(
            slivers, [],
            f'Slivers after {self.PERCENTAGE}% generalization '
            f'of gemeinden_bayern ({len(slivers)} pair(s)):\n'
            + '\n'.join(f'  {s}' for s in slivers[:10]),
        )


class TestSliver2NoSliver(unittest.TestCase):
    """
    Regression test for the Haidmühle/Grainet Forest area.
    """

    PERCENTAGE = 90

    @classmethod
    def setUpClass(cls):
        from generalize.api import generalize_polygon_layer
        layer = _load_layer(_SLIVER2)
        cls.features, _, _ = generalize_polygon_layer(
            layer, percentage=cls.PERCENTAGE, add_to_project=False,
        )

    def test_no_sliver(self):
        slivers = TestGemeindenBayernNoSliver._find_slivers(self.features)
        self.assertEqual(
            slivers, [],
            f'Slivers after {self.PERCENTAGE}% generalisation '
            f'of sliver2 ({len(slivers)} pair(s)):\n'
            + '\n'.join(f'  {s}' for s in slivers[:10]),
        )

    def test_dissolve_small_preserves_all_features(self):
        """dissolve_small=True must not eliminate entire features (create geographic holes)."""
        from generalize.api import generalize_polygon_layer
        layer = _load_layer(_SLIVER2)
        features, _, _ = generalize_polygon_layer(
            layer, percentage=self.PERCENTAGE, add_to_project=False,
            dissolve_small=True,
        )
        input_count = layer.featureCount()
        self.assertEqual(
            len(features), input_count,
            f'dissolve_small dropped entire features: '
            f'got {len(features)}, expected {input_count}',
        )


class TestSliver3NoSliver(unittest.TestCase):
    """
    Regression test for the Bischofsgrüner Forst area.

    At 90% reduction the small exclave/island of Bischofsgrüner Forst must not
    be dissolved away, leaving a geographic hole in the output dataset.
    dissolve_small=True must preserve all input features.
    """

    PERCENTAGE = 90

    @classmethod
    def setUpClass(cls):
        from generalize.api import generalize_polygon_layer
        layer = _load_layer(_SLIVER3)
        cls.layer = layer
        cls.features, _, _ = generalize_polygon_layer(
            layer, percentage=cls.PERCENTAGE, add_to_project=False,
        )

    def test_no_geographic_hole(self):
        """
        The Bischofsgrüner Forst exclave fills a hole in the surrounding feature.
        If the exclave collapses, that hole is left unfilled and the union of all
        output features has an interior ring.  The union must be hole-free.
        """
        from qgis.core import QgsGeometry
        output_union = QgsGeometry.unaryUnion(
            [f.geometry() for f in self.features]
        )
        if output_union.isMultipart():
            rings_per_part = [len(rings) for rings in output_union.asMultiPolygon()]
        else:
            rings_per_part = [len(output_union.asPolygon())]
        holes = sum(r - 1 for r in rings_per_part)
        self.assertEqual(
            holes, 0,
            f'Output union has {holes} hole(s) — '
            f'Bischofsgrüner Forst exclave may have collapsed',
        )


class TestSliver3aNoSliver(unittest.TestCase):
    """
    Regression test for the Bischofsgrüner Forst area with dissolve_small=True.

    When dissolve_small is active, the small exclave that would otherwise
    collapse must instead be merged into an adjacent ring — no geographic hole.
    """

    PERCENTAGE = 90

    @classmethod
    def setUpClass(cls):
        from generalize.api import generalize_polygon_layer
        layer = _load_layer(_SLIVER3)
        cls.layer = layer
        cls.features, _, _ = generalize_polygon_layer(
            layer, percentage=cls.PERCENTAGE, add_to_project=False,
            dissolve_small=True,
        )

    def test_no_geographic_hole(self):
        """
        With dissolve_small=True the Bischofsgrüner Forst exclave must be
        merged into a neighbouring ring rather than dropped, so the union of
        all output features must be hole-free.
        """
        from qgis.core import QgsGeometry
        output_union = QgsGeometry.unaryUnion(
            [f.geometry() for f in self.features]
        )
        if output_union.isMultipart():
            rings_per_part = [len(rings) for rings in output_union.asMultiPolygon()]
        else:
            rings_per_part = [len(output_union.asPolygon())]
        holes = sum(r - 1 for r in rings_per_part)
        self.assertEqual(
            holes, 0,
            f'Output union has {holes} hole(s) even with dissolve_small=True — '
            f'Bischofsgrüner Forst exclave was not merged into a neighbour',
        )


class TestUntrasriedValidGeometry(unittest.TestCase):
    """
    Regression test for Untrasried (DEBKGVGB000006H6) from gemeinden_deutschland.

    At 50% generalisation this municipality produces a Self-intersection even
    with constrained=True.  The test data includes Untrasried and its 8
    neighbours so the shared junction nodes are present and the cross-arc
    constraint is exercised.
    """

    PERCENTAGE = 80

    @classmethod
    def setUpClass(cls):
        from generalize.api import generalize_polygon_layer
        layer = _load_layer(_UNTRASRIED)
        features, _, _ = generalize_polygon_layer(
            layer, percentage=cls.PERCENTAGE, add_to_project=False,
            dissolve_small=True, repair_inversions=True,
        )
        cls.features = features

    def test_all_features_are_valid(self):
        """Every generalized feature must pass QGIS isGeosValid()."""
        invalid = []
        for f in self.features:
            geom = f.geometry()
            if not geom.isGeosValid():
                idx = f.fieldNameIndex('Geografisc')
                fid = f.attribute(idx) if idx >= 0 else f.id()
                invalid.append(f'  {fid}: {geom.lastError()}')
        self.assertEqual(
            invalid, [],
            'Invalid geometries after 50% generalisation of untrasried.geojson:\n'
            + '\n'.join(invalid),
        )


@pytest.mark.slow
class TestGemeindenDeutschlandValidGeometry(unittest.TestCase):
    """
    After generalizing gemeinden_deutschland at 50% with constrained=True,
    every output feature must pass the QGIS 'Check Validity' algorithm (GEOS
    strict).  This is the full 10 981-municipality Germany dataset and is the
    largest regression test.
    """

    PERCENTAGE = 50

    @classmethod
    def setUpClass(cls):
        import processing
        from qgis.core import QgsVectorLayer
        from generalize.api import generalize_polygon_layer

        layer = _load_layer(_GEMEINDEN_DEUTSCHLAND)
        features, _, _ = generalize_polygon_layer(
            layer, percentage=cls.PERCENTAGE, add_to_project=False,
        )
        cls.features = features

        temp = QgsVectorLayer(
            f'Polygon?crs={layer.crs().authid()}', '_temp', 'memory'
        )
        temp.dataProvider().addAttributes(layer.fields())
        temp.updateFields()
        temp.dataProvider().addFeatures(features)

        result = processing.run('qgis:checkvalidity', {
            'INPUT_LAYER': temp,
            'METHOD': 2,
            'IGNORE_RING_SELF_INTERSECTION': False,
            'VALID_OUTPUT':   'memory:',
            'INVALID_OUTPUT': 'memory:',
            'ERROR_OUTPUT':   'memory:',
        })
        cls.invalid_layer = result['INVALID_OUTPUT']
        cls.error_layer   = result['ERROR_OUTPUT']

    def test_all_features_are_valid(self):
        """Every generalized feature must pass QGIS/GEOS validity check."""
        invalid_count = self.invalid_layer.featureCount()
        if invalid_count == 0:
            return

        invalid_names = []
        for f in self.invalid_layer.getFeatures():
            name = f.attribute('Geografisc') or ''
            oid  = f.attribute('Objektiden') or f.id()
            invalid_names.append(f'  {oid} ({name})')

        error_msgs = []
        for f in self.error_layer.getFeatures():
            pt  = f.geometry().asPoint()
            msg = f.attribute('message') or ''
            error_msgs.append(f'  ({pt.x():.2f}, {pt.y():.2f}): {msg}')

        self.fail(
            f'{invalid_count} invalid feature(s) after {self.PERCENTAGE}% '
            f'generalization of gemeinden_deutschland:\n'
            'Invalid features:\n' + '\n'.join(invalid_names) + '\n'
            'Errors:\n' + '\n'.join(error_msgs)
        )


def _validity_test_body(test_case, invalid_layer, error_layer, percentage, dataset_name,
                         name_field, id_field):
    """Shared failure reporting for full-dataset validity tests."""
    invalid_count = invalid_layer.featureCount()
    if invalid_count == 0:
        return

    invalid_names = []
    for f in invalid_layer.getFeatures():
        name = f.attribute(name_field) or ''
        oid  = f.attribute(id_field) if id_field else f.id()
        invalid_names.append(f'  {oid} ({name})')

    error_msgs = []
    for f in error_layer.getFeatures():
        pt  = f.geometry().asPoint()
        msg = f.attribute('message') or ''
        error_msgs.append(f'  ({pt.x():.4f}, {pt.y():.4f}): {msg}')

    test_case.fail(
        f'{invalid_count} invalid feature(s) after {percentage}% '
        f'generalization of {dataset_name}:\n'
        'Invalid features:\n' + '\n'.join(invalid_names) + '\n'
        'Errors:\n' + '\n'.join(error_msgs)
    )


def _run_validity_check(layer, percentage):
    """Generalise *layer* and run QGIS checkvalidity; return (invalid, error) layers."""
    import processing
    from qgis.core import QgsVectorLayer
    from generalize.api import generalize_polygon_layer

    features, _, _ = generalize_polygon_layer(
        layer, percentage=percentage, add_to_project=False,
    )
    temp = QgsVectorLayer(f'Polygon?crs={layer.crs().authid()}', '_temp', 'memory')
    temp.dataProvider().addAttributes(layer.fields())
    temp.updateFields()
    temp.dataProvider().addFeatures(features)
    result = processing.run('qgis:checkvalidity', {
        'INPUT_LAYER': temp, 'METHOD': 2,
        'IGNORE_RING_SELF_INTERSECTION': False,
        'VALID_OUTPUT': 'memory:', 'INVALID_OUTPUT': 'memory:', 'ERROR_OUTPUT': 'memory:',
    })
    return result['INVALID_OUTPUT'], result['ERROR_OUTPUT']


@pytest.mark.slow
class TestGreatBritainValidGeometry(unittest.TestCase):
    """
    After generalizing westminster_const_region (632 Westminster
    constituencies, EPSG:27700) at 50% with constrained=True, every output
    feature must pass the QGIS 'Check Validity' algorithm (GEOS strict).
    """

    PERCENTAGE = 50

    @classmethod
    def setUpClass(cls):
        layer = _load_layer(_GREAT_BRITAIN)
        cls.invalid_layer, cls.error_layer = _run_validity_check(
            layer, cls.PERCENTAGE,
        )

    def test_all_features_are_valid(self):
        _validity_test_body(
            self, self.invalid_layer, self.error_layer,
            self.PERCENTAGE, 'great_britain',
            name_field='NAME', id_field=None,
        )


@pytest.mark.slow
class TestFranceValidGeometry(unittest.TestCase):
    """
    After generalizing communes-20220101 (34 955 French communes,
    EPSG:4326) at 50% with constrained=True, every output feature must
    pass the QGIS 'Check Validity' algorithm (GEOS strict).
    """

    PERCENTAGE = 50

    @classmethod
    def setUpClass(cls):
        layer = _load_layer(_FRANCE)
        cls.invalid_layer, cls.error_layer = _run_validity_check(
            layer, cls.PERCENTAGE,
        )

    def test_all_features_are_valid(self):
        _validity_test_body(
            self, self.invalid_layer, self.error_layer,
            self.PERCENTAGE, 'france',
            name_field='nom', id_field='insee',
        )


class TestDissolveSmallUnconstrained(unittest.TestCase):
    """
    Regression test: dissolve_small=True must not crash when constrained=False.

    dissolve_small_rings() used a set[TopoRing] internally.  TopoRing is a
    @dataclass which auto-generates __eq__, which sets __hash__ = None —
    making the class unhashable.  Adding a TopoRing to a set therefore raises
    "unhashable type: 'TopoRing'".  This test was introduced to pin that fix.
    """

    PERCENTAGE = 90

    def test_does_not_raise(self):
        from generalize.api import generalize_polygon_layer
        layer = _load_layer(_TOO_FEW_POINTS)
        # Must not raise — before the fix this raised "unhashable type: 'TopoRing'"
        features, _, _ = generalize_polygon_layer(
            layer, percentage=self.PERCENTAGE, add_to_project=False,
            dissolve_small=True,
        )
        self.assertIsNotNone(features)


class TestDissolveSmall(unittest.TestCase):
    """
    Regression test for the dissolve_small option.

    Neureichenau (too_few_points.geojson) is a MultiPolygon with 17 parts at
    50% generalisation.  Several of those parts are tiny island polygons whose
    area falls below the 2·d² threshold.  With dissolve_small=True they must
    be dropped from the topology before reconstruction so that the output has
    fewer parts, all geometries remain valid, and at least one part per feature
    is preserved.
    """

    PERCENTAGE = 50

    @classmethod
    def setUpClass(cls):
        from generalize.api import generalize_polygon_layer
        layer = _load_layer(_TOO_FEW_POINTS)

        features_plain, _, _ = generalize_polygon_layer(
            layer, percentage=cls.PERCENTAGE, add_to_project=False,
            dissolve_small=False,
        )
        cls.features_plain = features_plain

        features_dissolved, _, _ = generalize_polygon_layer(
            layer, percentage=cls.PERCENTAGE, add_to_project=False,
            dissolve_small=True,
        )
        cls.features_dissolved = features_dissolved

    def _part_count(self, features):
        """Return total number of polygon parts across all features."""
        from qgis.core import QgsWkbTypes
        total = 0
        for f in features:
            geom = f.geometry()
            if QgsWkbTypes.isMultiType(geom.wkbType()):
                total += geom.constGet().numGeometries()
            else:
                total += 1
        return total

    def test_dissolve_small_reduces_part_count(self):
        """dissolve_small=True must produce fewer polygon parts than False."""
        plain_parts    = self._part_count(self.features_plain)
        dissolved_parts = self._part_count(self.features_dissolved)
        self.assertLess(
            dissolved_parts, plain_parts,
            f'Expected fewer parts with dissolve_small=True '
            f'(got {dissolved_parts} vs {plain_parts} without dissolve_small)',
        )

    def test_dissolve_small_all_features_valid(self):
        """Every feature produced with dissolve_small=True must be valid."""
        invalid = []
        for f in self.features_dissolved:
            geom = f.geometry()
            if not geom.isGeosValid():
                idx = f.fieldNameIndex('gml_id')
                fid = f.attribute(idx) if idx >= 0 else f.id()
                invalid.append(f'  {fid}: {geom.lastError()}')
        self.assertEqual(
            invalid, [],
            'Invalid geometries after dissolve_small=True generalisation:\n'
            + '\n'.join(invalid),
        )

    def test_dissolve_small_at_least_one_part_per_feature(self):
        """Every feature must have at least one remaining polygon part."""
        self.assertGreater(
            len(self.features_dissolved), 0,
            'All features were dissolved — at least one must remain.',
        )


@pytest.mark.slow
class TestDissolveSmallNoCollapse(unittest.TestCase):
    """
    dissolve_small=True at 96% reduction on gemeinden_bayern must not collapse
    any polygon to an empty geometry.

    At aggressive reduction rates the pre-simplification dissolve can remove
    all parts of a MultiPolygon feature, leaving it with no geometry at all.
    Every output feature must have at least one part with positive area.
    """

    PERCENTAGE = 96
    # These tiny single-part municipalities consist entirely of parts below the
    # dissolve threshold at 96% and are therefore intentionally removed.
    EXPECTED_LOST = {'Geiersberg', 'Pröll'}

    @classmethod
    def setUpClass(cls):
        from generalize.api import generalize_polygon_layer
        layer = _load_layer(_GEMEINDEN_BAYERN)
        cls.input_count = layer.featureCount()
        cls.name_idx = layer.fields().indexOf('name')
        cls.input_names = {f.attribute(cls.name_idx) for f in layer.getFeatures()}
        cls.features, _, _ = generalize_polygon_layer(
            layer, percentage=cls.PERCENTAGE, add_to_project=False,
            dissolve_small=True,
        )

    def test_no_collapsed_polygons(self):
        """Every output feature must have a non-empty geometry with area > 0."""
        collapsed = []
        for f in self.features:
            geom = f.geometry()
            if geom is None or geom.isEmpty() or geom.area() <= 0:
                idx = f.fieldNameIndex('gml_id')
                fid = f.attribute(idx) if idx >= 0 else f.id()
                name = f.attribute(f.fieldNameIndex('name')) or ''
                collapsed.append(f'  {fid} ({name})')
        self.assertEqual(
            collapsed, [],
            f'{len(collapsed)} feature(s) collapsed to empty geometry after '
            f'{self.PERCENTAGE}% generalization with dissolve_small=True:\n'
            + '\n'.join(collapsed),
        )

    def test_only_expected_features_lost(self):
        """Exactly EXPECTED_LOST features may be absent from the output; no others."""
        output_names = {f.attribute(self.name_idx) for f in self.features}
        actually_lost = self.input_names - output_names
        unexpected_lost = actually_lost - self.EXPECTED_LOST
        unexpectedly_kept = self.EXPECTED_LOST - actually_lost
        msgs = []
        if unexpected_lost:
            msgs.append('Unexpectedly lost: ' + ', '.join(sorted(unexpected_lost)))
        if unexpectedly_kept:
            msgs.append('Expected to be lost but still present: ' + ', '.join(sorted(unexpectedly_kept)))
        self.assertFalse(msgs, '\n'.join(msgs))


class TestLost2NoHoleAfterUnion(unittest.TestCase):
    """
    Three adjacent municipalities (Burgwindheim, Winkelhofer Forst, Ebrach)
    share borders.  After simplification with dissolve_small and repair_inversions
    their union must be a single polygon with no holes — any sliver gap between
    neighbours would appear as an interior ring in the union.
    """

    PERCENTAGE = 95

    @classmethod
    def setUpClass(cls):
        from generalize.api import generalize_polygon_layer
        from qgis.core import QgsGeometry
        layer = _load_layer(_LOST2)
        cls.features, _, _ = generalize_polygon_layer(
            layer, percentage=cls.PERCENTAGE, add_to_project=False,
            dissolve_small=True, repair_inversions=True,
        )
        geoms = [f.geometry() for f in cls.features]
        cls.union = QgsGeometry.unaryUnion(geoms)

    def test_union_has_no_holes(self):
        """Union of all output polygons must contain no interior rings."""
        from qgis.core import QgsWkbTypes
        union = self.union
        self.assertFalse(union.isNull(), 'Union geometry is null')
        self.assertFalse(union.isEmpty(), 'Union geometry is empty')

        holes = []
        geom = union.constGet()
        if QgsWkbTypes.isMultiType(union.wkbType()):
            parts = [geom.geometryN(i) for i in range(geom.numGeometries())]
        else:
            parts = [geom]

        for part in parts:
            for ring_idx in range(1, part.numInteriorRings() + 1):
                ring = part.interiorRing(ring_idx - 1)
                holes.append(f'  ring with {ring.numPoints()} points')

        self.assertEqual(
            holes, [],
            f'Union has {len(holes)} hole(s) after {self.PERCENTAGE}% '
            f'generalization — shared borders are not topologically consistent:\n'
            + '\n'.join(holes),
        )


class TestLost3NoHoleAfterUnion(unittest.TestCase):
    """
    After simplification with dissolve_small=True at 98%, the union of all
    output polygons must contain no interior rings (no sliver gaps).
    """

    PERCENTAGE = 98

    @classmethod
    def setUpClass(cls):
        from generalize.api import generalize_polygon_layer
        from qgis.core import QgsGeometry
        layer = _load_layer(_LOST3)
        cls.features_dissolve, _, _ = generalize_polygon_layer(
            layer, percentage=cls.PERCENTAGE, add_to_project=False,
            dissolve_small=True,
        )
        cls.features_no_dissolve, _, _ = generalize_polygon_layer(
            layer, percentage=cls.PERCENTAGE, add_to_project=False,
            dissolve_small=False,
        )
        cls.union_dissolve = QgsGeometry.unaryUnion([f.geometry() for f in cls.features_dissolve])
        cls.union_no_dissolve = QgsGeometry.unaryUnion([f.geometry() for f in cls.features_no_dissolve])

    @staticmethod
    def _count_holes(union):
        from qgis.core import QgsWkbTypes
        holes = []
        geom = union.constGet()
        if QgsWkbTypes.isMultiType(union.wkbType()):
            parts = [geom.geometryN(i) for i in range(geom.numGeometries())]
        else:
            parts = [geom]
        for part in parts:
            for ring_idx in range(1, part.numInteriorRings() + 1):
                ring = part.interiorRing(ring_idx - 1)
                holes.append(f'  ring with {ring.numPoints()} points')
        return holes

    def test_union_no_dissolve_has_no_holes(self):
        """Without dissolve_small the topology alone must produce no holes."""
        union = self.union_no_dissolve
        self.assertFalse(union.isNull(), 'Union geometry is null')
        self.assertFalse(union.isEmpty(), 'Union geometry is empty')
        holes = self._count_holes(union)
        self.assertEqual(
            holes, [],
            f'Union (no dissolve) has {len(holes)} hole(s) after {self.PERCENTAGE}% '
            f'generalization:\n' + '\n'.join(holes),
        )

    def test_union_has_no_holes(self):
        """Union of all output polygons (dissolve_small=True) must contain no interior rings."""
        union = self.union_dissolve
        self.assertFalse(union.isNull(), 'Union geometry is null')
        self.assertFalse(union.isEmpty(), 'Union geometry is empty')
        holes = self._count_holes(union)
        self.assertEqual(
            holes, [],
            f'Union has {len(holes)} hole(s) after {self.PERCENTAGE}% '
            f'generalization with dissolve_small — shared borders are not topologically consistent:\n'
            + '\n'.join(holes),
        )


class TestInvert5At98NoHoleAfterUnion(unittest.TestCase):
    """
    After 98% generalization with repair_inversions=True and dissolve_small=True,
    the union of all output polygons must contain no interior rings.
    """

    PERCENTAGE = 98

    @classmethod
    def setUpClass(cls):
        from generalize.api import generalize_polygon_layer
        from qgis.core import QgsGeometry
        layer = _load_layer(_INVERT5AT98)
        cls.features, _, _ = generalize_polygon_layer(
            layer, percentage=cls.PERCENTAGE, add_to_project=False,
            dissolve_small=True,
            repair_inversions=True,
        )
        cls.union = QgsGeometry.unaryUnion([f.geometry() for f in cls.features])

    def test_union_has_no_holes(self):
        """Union of all output polygons must contain no interior rings."""
        from qgis.core import QgsWkbTypes
        union = self.union
        self.assertFalse(union.isNull(), 'Union geometry is null')
        self.assertFalse(union.isEmpty(), 'Union geometry is empty')

        holes = []
        geom = union.constGet()
        if QgsWkbTypes.isMultiType(union.wkbType()):
            parts = [geom.geometryN(i) for i in range(geom.numGeometries())]
        else:
            parts = [geom]

        for part in parts:
            for ring_idx in range(1, part.numInteriorRings() + 1):
                ring = part.interiorRing(ring_idx - 1)
                holes.append(f'  ring with {ring.numPoints()} points')

        self.assertEqual(
            holes, [],
            f'Union has {len(holes)} hole(s) after {self.PERCENTAGE}% '
            f'generalization with repair_inversions — shared borders are not topologically consistent:\n'
            + '\n'.join(holes),
        )


class TestDialogSmoke(unittest.TestCase):
    """
    Smoke test: GeneralizeDialog can be constructed on the current Qt version.

    Catches Qt enum AttributeErrors (e.g. Qt.Horizontal removed in PyQt6)
    without requiring a real QGIS GUI session.  Runs with QT_QPA_PLATFORM=offscreen
    via qgis_init, so no display is needed.
    """

    def test_dialog_instantiates(self):
        from unittest.mock import MagicMock
        from generalize.generalize_dialog import GeneralizeDialog
        iface = MagicMock()
        dlg = GeneralizeDialog(iface)
        self.assertEqual(dlg.slider.minimum(), 0)
        self.assertEqual(dlg.slider.maximum(), 99)
        self.assertEqual(dlg.slider.value(), 90)
        self.assertAlmostEqual(dlg.pct_spinbox.value(), 90.0)
        self.assertAlmostEqual(dlg.pct_spinbox.maximum(), 99.9)
        # Slider position 99 must map to 99.9%, not 99.0%
        dlg.slider.setValue(99)
        self.assertAlmostEqual(dlg.pct_spinbox.value(), 99.9)
        self.assertTrue(dlg.repair_checkbox.isChecked())
        self.assertFalse(dlg.dissolve_small_checkbox.isChecked())


if __name__ == '__main__':
    unittest.main(verbosity=2)
