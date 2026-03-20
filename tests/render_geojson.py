"""
Render one or more GeoJSON files to a PNG for visual inspection.

Usage (standalone):
    python tests/render_geojson.py test_data/no_overlap.geojson
    python tests/render_geojson.py test_data/no_overlap.geojson test_data/no_overlap_generalized.geojson

Or call render() directly from a test to save a diagnostic image on failure.
"""
import json
import os
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import Polygon as MplPolygon
from matplotlib.collections import PatchCollection
import numpy as np


# Colours per feature id (cycles if more features than colours)
_COLOURS = [
    '#a6cee3', '#1f78b4', '#b2df8a', '#33a02c',
    '#fb9a99', '#e31a1c', '#fdbf6f', '#ff7f00',
]


def _rings_from_geometry(geom):
    """Return a list of (exterior_ring, [hole_rings]) for each polygon part."""
    parts = []
    gtype = geom['type']
    if gtype == 'Polygon':
        coords = geom['coordinates']
        parts.append((coords[0], coords[1:]))
    elif gtype == 'MultiPolygon':
        for poly in geom['coordinates']:
            parts.append((poly[0], poly[1:]))
    return parts


def render(geojson_paths, output_path, title=None):
    """
    Render GeoJSON files to a PNG.

    :param geojson_paths: list of file paths (one layer per file, drawn in order)
    :param output_path:   path to write the PNG
    :param title:         optional figure title
    """
    fig, ax = plt.subplots(figsize=(10, 10))
    ax.set_aspect('equal')

    all_x, all_y = [], []

    for layer_idx, path in enumerate(geojson_paths):
        with open(path, encoding='utf-8') as f:
            data = json.load(f)

        features = data.get('features', [])
        alpha = 0.5 if layer_idx == 0 else 0.4
        lw = 0.8 if layer_idx == 0 else 1.2
        ls = '-' if layer_idx == 0 else '--'

        for feat in features:
            fid = feat.get('properties', {}).get('id', None)
            colour = _COLOURS[int(fid) % len(_COLOURS)] if fid is not None else '#cccccc'
            label_added = False

            for exterior, holes in _rings_from_geometry(feat['geometry']):
                xy = np.array(exterior)
                all_x.extend(xy[:, 0])
                all_y.extend(xy[:, 1])

                patch = MplPolygon(xy, closed=True)
                pc = PatchCollection(
                    [patch],
                    facecolor=colour,
                    edgecolor='black',
                    alpha=alpha,
                    linewidth=lw,
                    linestyle=ls,
                )
                ax.add_collection(pc)

                # Annotate with feature id at centroid of first part only
                if not label_added and fid is not None:
                    cx, cy = xy[:-1, 0].mean(), xy[:-1, 1].mean()
                    ax.text(cx, cy, str(fid), ha='center', va='center',
                            fontsize=9, fontweight='bold', color='#222222')
                    label_added = True

                for hole in holes:
                    hxy = np.array(hole)
                    hpatch = MplPolygon(hxy, closed=True)
                    hpc = PatchCollection(
                        [hpatch], facecolor='white', edgecolor='black',
                        alpha=1.0, linewidth=lw,
                    )
                    ax.add_collection(hpc)

    if all_x:
        pad_x = (max(all_x) - min(all_x)) * 0.05 or 1
        pad_y = (max(all_y) - min(all_y)) * 0.05 or 1
        ax.set_xlim(min(all_x) - pad_x, max(all_x) + pad_x)
        ax.set_ylim(min(all_y) - pad_y, max(all_y) + pad_y)

    if title:
        ax.set_title(title, fontsize=11)

    # Legend: file names
    handles = []
    for i, path in enumerate(geojson_paths):
        ls = '-' if i == 0 else '--'
        handles.append(mpatches.Patch(
            facecolor='grey', alpha=0.5 if i == 0 else 0.4,
            edgecolor='black', linewidth=0.8, linestyle=ls,
            label=os.path.basename(path),
        ))
    ax.legend(handles=handles, loc='upper right', fontsize=8)

    ax.ticklabel_format(style='plain', useOffset=False)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close(fig)
    print(f'Saved: {output_path}')


_TEST_OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'test_output')


def output_path(name: str) -> str:
    """Return an absolute path inside test_output/ for the given filename."""
    return os.path.join(os.path.abspath(_TEST_OUTPUT), name)


if __name__ == '__main__':
    paths = sys.argv[1:]
    if not paths:
        print('Usage: python tests/render_geojson.py file1.geojson [file2.geojson ...]')
        sys.exit(1)
    name = os.path.splitext(os.path.basename(paths[-1]))[0] + '_rendered.png'
    render(paths, output_path(name), title=' vs '.join(os.path.basename(p) for p in paths))
