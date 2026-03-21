"""
pytest configuration for the generalize test suite.

Slow tests (full-dataset runs taking minutes) are skipped by default.
Run them explicitly with:

    pytest --slow

or target a single slow test class:

    pytest tests/test_generalize.py::TestGemeindenBayernValidGeometry --slow
"""
import pytest


def pytest_addoption(parser):
    parser.addoption(
        '--slow', action='store_true', default=False,
        help='Run slow tests (full-dataset runs, may take many minutes).',
    )


def pytest_configure(config):
    config.addinivalue_line(
        'markers',
        'slow: mark test as slow (skipped by default; enable with --slow)',
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption('--slow'):
        return  # run everything
    skip_slow = pytest.mark.skip(reason='slow test — run with --slow to enable')
    for item in items:
        if 'slow' in item.keywords:
            item.add_marker(skip_slow)
