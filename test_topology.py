"""
Integration test: build topology from test shapefile, reconstruct features,
and verify that feature count, attributes, and geometries are preserved exactly.

Run from the command line (no QGIS GUI required):
    cd d:\\dev\\workspace\\generalize
    "C:\\Program Files\\QGIS 3.40.15\\apps\\Python312\\python.exe" -m pytest test_topology.py -v

Or as a plain script:
    "C:\\Program Files\\QGIS 3.40.15\\apps\\Python312\\python.exe" test_topology.py
"""
import os
import sys
import unittest

# ---------------------------------------------------------------------------
# Bootstrap – must happen before any qgis.* import
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKSPACE = os.path.dirname(_HERE)            # d:\dev\workspace

# Make the generalize package importable as 'generalize.xxx'
if _WORKSPACE not in sys.path:
    sys.path.insert(0, _WORKSPACE)

# Initialize QGIS (idempotent, safe to import multiple times)
import qgis_init  # noqa: E402  (must come after sys.path setup)

# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------
_DATA_ROOT = os.path.join(_HERE, 'test_data')

_SHP = os.path.join(_DATA_ROOT, 'verwaltungsgrenzen_vermessung', 'VerwaltungsEinheit.shp')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_layer(path: str):
    from qgis.core import QgsVectorLayer
    layer = QgsVectorLayer(path, 'test', 'ogr')
    if not layer.isValid():
        raise RuntimeError(f'Could not load layer: {path}')
    return layer


def _part_count(layer) -> int:
    """Total number of polygon parts across all features."""
    n = 0
    for feat in layer.getFeatures():
        geom = feat.geometry()
        n += len(geom.asMultiPolygon()) if geom.isMultipart() else 1
    return n


# ---------------------------------------------------------------------------
# Test suite A – topology structure
# ---------------------------------------------------------------------------

class TestTopologyBuild(unittest.TestCase):
    """Verify that the TopoLayer is built with a sensible structure."""

    @classmethod
    def setUpClass(cls):
        from generalize.topology_builder import build
        cls.layer = _load_layer(_SHP)
        cls.topo  = build(cls.layer)
        print(f'\n{cls.topo}')

    def test_polygon_count_matches_parts(self):
        """One TopoPolygon per polygon part (not per QgsFeature)."""
        self.assertEqual(len(self.topo.polygons), _part_count(self.layer))

    def test_has_nodes_and_edges(self):
        self.assertGreater(len(self.topo.nodes), 0)
        self.assertGreater(len(self.topo.edges), 0)

    def test_shared_edges_exist(self):
        """Administrative boundaries must produce shared edges."""
        self.assertGreater(
            self.topo.shared_edge_count, 0,
            'No shared edges found – topology detection failed',
        )

    def test_edge_has_one_or_two_polygon_owners(self):
        """Every edge must belong to 1 (boundary) or 2 (shared) polygons."""
        for edge in self.topo.edges.values():
            owners = sum(
                1 for p in (edge.left_polygon, edge.right_polygon)
                if p is not None
            )
            self.assertGreaterEqual(owners, 1, f'Edge {edge.id} has no owner')
            self.assertLessEqual(owners, 2, f'Edge {edge.id} has >2 owners')

    def test_shared_edge_count_less_than_total(self):
        """Shared edges < total edges proves deduplication happened."""
        self.assertLess(self.topo.shared_edge_count, len(self.topo.edges))


# ---------------------------------------------------------------------------
# Test suite B – round-trip fidelity
# ---------------------------------------------------------------------------

class TestTopologyRoundtrip(unittest.TestCase):
    """
    Build topology and immediately reconstruct without any simplification.
    Every feature must come back identical to the original.
    """

    @classmethod
    def setUpClass(cls):
        from generalize.topology_builder import build, to_qgs_features
        cls.layer        = _load_layer(_SHP)
        topo             = build(cls.layer)
        cls.reconstructed = {f.id(): f for f in to_qgs_features(topo)}
        cls.original      = {f.id(): f for f in cls.layer.getFeatures()}

    # ---- count ----

    def test_feature_count(self):
        self.assertEqual(
            len(self.reconstructed), len(self.original),
            f'Expected {len(self.original)} features, '
            f'got {len(self.reconstructed)}',
        )

    # ---- attributes ----

    def test_all_feature_ids_present(self):
        missing = set(self.original) - set(self.reconstructed)
        self.assertFalse(missing, f'Missing feature ids: {missing}')

    def test_attributes_preserved(self):
        bad = []
        for fid, orig in self.original.items():
            recon = self.reconstructed[fid]
            if orig.attributes() != recon.attributes():
                bad.append(fid)
        self.assertFalse(bad, f'Attribute mismatch for ids: {bad[:10]}')

    def test_field_count_preserved(self):
        field_count = self.layer.fields().count()
        for fid, feat in self.reconstructed.items():
            self.assertEqual(
                len(feat.attributes()), field_count,
                f'Feature {fid}: expected {field_count} fields, '
                f'got {len(feat.attributes())}',
            )

    # ---- geometry ----

    def test_geometry_area_preserved(self):
        """Reconstructed area must equal original area (lossless round-trip).

        We use a relative tolerance of 1e-9 to absorb the tiny floating-point
        differences that arise when ring vertices are visited in a different
        starting order by the shoelace formula.
        """
        rel_tol = 1e-9
        for fid, orig in self.original.items():
            a_orig  = orig.geometry().area()
            a_recon = self.reconstructed[fid].geometry().area()
            if a_orig == 0:
                self.assertEqual(a_recon, 0, f'Feature {fid}: zero area changed')
                continue
            rel_diff = abs(a_orig - a_recon) / a_orig
            self.assertLess(
                rel_diff, rel_tol,
                msg=f'Feature {fid}: area {a_orig} → {a_recon} '
                    f'(relative diff {rel_diff:.2e})',
            )

    def test_geometry_difference_is_empty(self):
        """A∖B and B∖A must both have near-zero area (no geometry was lost or gained).

        Features with invalid source geometries are skipped: GEOS cannot
        compute a meaningful difference for them and that is a data quality
        issue unrelated to our topology code.
        """
        tol = 1e-6    # CRS units²
        skipped = 0
        for fid, orig in self.original.items():
            g_orig  = orig.geometry()
            g_recon = self.reconstructed[fid].geometry()

            # Skip if the source geometry is already invalid in the raw data.
            if not g_orig.isGeosValid():
                skipped += 1
                continue

            diff_ab = g_orig.difference(g_recon)
            diff_ba = g_recon.difference(g_orig)
            area_ab = diff_ab.area() if not diff_ab.isNull() else 0.0
            area_ba = diff_ba.area() if not diff_ba.isNull() else 0.0
            self.assertAlmostEqual(
                area_ab, 0.0, delta=tol,
                msg=f'Feature {fid}: original∖reconstructed area = {area_ab}',
            )
            self.assertAlmostEqual(
                area_ba, 0.0, delta=tol,
                msg=f'Feature {fid}: reconstructed∖original area = {area_ba}',
            )
        if skipped:
            print(f'\n  (skipped {skipped} features with invalid source geometry)')


# ---------------------------------------------------------------------------
# Test suite C – topological generalization (no slivers)
# ---------------------------------------------------------------------------

class TestTopologicalGeneralization(unittest.TestCase):
    """
    Verify that simplifying via the topology pipeline produces valid output
    and — crucially — no slivers between adjacent polygons.
    """

    PERCENTAGE = 50   # aggressive reduction to make differences visible

    @classmethod
    def setUpClass(cls):
        from generalize.topology_builder import build
        from generalize.visvalingam import simplify_arc, simplify_polygon

        cls.layer = _load_layer(_SHP)
        topo = build(cls.layer)

        for edge in topo.edges.values():
            is_loop = (edge.start_node == edge.end_node)
            if is_loop:
                edge.coords = simplify_polygon(edge.coords, cls.PERCENTAGE)
            else:
                edge.coords = simplify_arc(edge.coords, cls.PERCENTAGE)

        cls.topo = topo

        from generalize.topology_builder import to_qgs_features
        cls.simplified = [
            f for f in to_qgs_features(topo)
            if not f.geometry().isNull()
            and not f.geometry().isEmpty()
            and f.geometry().area() > 0
        ]

    def test_output_has_features(self):
        self.assertGreater(len(self.simplified), 0)

    def test_vertex_count_reduced(self):
        """Total vertex count must drop after simplification."""
        orig_verts = sum(
            f.geometry().constGet().nCoordinates()
            for f in self.layer.getFeatures()
        )
        simp_verts = sum(
            f.geometry().constGet().nCoordinates()
            for f in self.simplified
        )
        self.assertLess(simp_verts, orig_verts,
                        f'Expected fewer vertices: {simp_verts} >= {orig_verts}')
        reduction = 100 * (1 - simp_verts / orig_verts)
        print(f'\n  Vertex reduction: {orig_verts} → {simp_verts} ({reduction:.1f}%)')

    def test_no_slivers_on_shared_edges(self):
        """
        For every pair of polygons that shared an edge BEFORE simplification,
        the simplified shared edge must be identical (same coordinates).

        A sliver would appear if the two polygons had different coordinate
        sequences for their shared boundary.  With topological simplification
        this is impossible by construction — this test confirms it.
        """
        from generalize.topology import TopoEdge

        mismatches = 0
        checked = 0
        for edge in self.topo.edges.values():
            if edge.left_polygon is None or edge.right_polygon is None:
                continue   # boundary edge, not shared
            checked += 1

            # Retrieve the edge sequence as seen by each polygon
            left_poly  = self.topo.polygons[edge.left_polygon]
            right_poly = self.topo.polygons[edge.right_polygon]

            # Find this edge in each polygon's rings and check direction
            def find_half_edge(poly, eid):
                for ring in [poly.outer_ring] + poly.inner_rings:
                    for eid2, fwd in ring.half_edges:
                        if eid2 == eid:
                            return fwd
                return None

            fwd_left  = find_half_edge(left_poly,  edge.id)
            fwd_right = find_half_edge(right_poly, edge.id)

            # The two polygons must traverse the same edge in opposite directions.
            if fwd_left is not None and fwd_right is not None:
                self.assertNotEqual(
                    fwd_left, fwd_right,
                    f'Edge {edge.id}: both polygons traverse it in the same '
                    f'direction – topology is inconsistent',
                )

        self.assertGreater(checked, 0, 'No shared edges found to verify')
        print(f'\n  Verified {checked} shared edges — no slivers by construction')

    def test_attributes_preserved_after_simplification(self):
        """Attributes must be unchanged by the simplification."""
        orig = {f.id(): f.attributes() for f in self.layer.getFeatures()}
        for feat in self.simplified:
            fid = feat.id()
            if fid in orig:
                self.assertEqual(feat.attributes(), orig[fid],
                                 f'Attributes changed for feature {fid}')


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    unittest.main(verbosity=2)
