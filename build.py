"""
Build script: creates generalize-<version>.zip for QGIS plugin distribution.

Usage:
    python build.py

The zip is written to the parent directory (next to the generalize/ folder)
so it can be installed via QGIS > Plugins > Install from ZIP.
"""

import re
import subprocess
import sys
import zipfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PLUGIN_DIR = Path(__file__).parent          # …/generalize/
PLUGIN_NAME = PLUGIN_DIR.name               # "generalize"

# Files/directories to skip (relative to PLUGIN_DIR).
EXCLUDE_NAMES = {
    'build.py',
    'qgis_init.py',
    'test_generalize.py',
    'visvalingam_topo.py',  # legacy, not imported
    'README.md',
    '.gitignore',
}

EXCLUDE_DIRS = {
    'tests',
    'test_data',
    'test_output',
    '__pycache__',
    '.git',
    '.pytest_cache',
}

EXCLUDE_SUFFIXES = {'.pyc', '.pyo', '.ts'}   # .ts sources excluded; only .qm binaries ship

# Path to lrelease.  Override via environment variable LRELEASE if needed.
import os as _os
LRELEASE = _os.environ.get(
    'LRELEASE',
    r'd:\dev\qt\6.11.0\mingw_64\bin\lrelease.exe',
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_version() -> str:
    metadata = PLUGIN_DIR / 'metadata.txt'
    for line in metadata.read_text(encoding='utf-8').splitlines():
        m = re.match(r'^\s*version\s*=\s*(.+)', line)
        if m:
            return m.group(1).strip()
    raise RuntimeError("version not found in metadata.txt")


def iter_plugin_files():
    """Yield (Path, archive_name) pairs for everything that belongs in the zip."""
    for path in sorted(PLUGIN_DIR.rglob('*')):
        if not path.is_file():
            continue
        # Check every parent directory against EXCLUDE_DIRS
        rel = path.relative_to(PLUGIN_DIR)
        parts = rel.parts
        if any(p in EXCLUDE_DIRS for p in parts):
            continue
        if path.name in EXCLUDE_NAMES:
            continue
        if path.suffix in EXCLUDE_SUFFIXES:
            continue
        # Archive path: generalize/<relative path inside plugin>
        archive_name = f"{PLUGIN_NAME}/{rel.as_posix()}"
        yield path, archive_name


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def compile_translations():
    """Compile every i18n/*.ts file to a .qm binary next to it."""
    i18n_dir = PLUGIN_DIR / 'i18n'
    ts_files = sorted(i18n_dir.glob('*.ts'))
    if not ts_files:
        return
    lrelease = Path(LRELEASE)
    if not lrelease.exists():
        print(f"WARNING: lrelease not found at {lrelease} — skipping translation compile.")
        print("         Set the LRELEASE environment variable to the correct path.")
        return
    for ts in ts_files:
        qm = ts.with_suffix('.qm')
        result = subprocess.run(
            [str(lrelease), str(ts), '-qm', str(qm)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"  Compiled {ts.name} -> {qm.name}")
        else:
            print(f"  ERROR compiling {ts.name}:\n{result.stderr}")
            sys.exit(1)


def main():
    compile_translations()

    version = read_version()
    out_path = PLUGIN_DIR.parent / f"{PLUGIN_NAME}-{version}.zip"

    print(f"Building {out_path.name} …")
    with zipfile.ZipFile(out_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for path, archive_name in iter_plugin_files():
            zf.write(path, archive_name)
            print(f"  + {archive_name}")

    size_kb = out_path.stat().st_size // 1024
    print(f"\nDone: {out_path}  ({size_kb} KB)")


if __name__ == '__main__':
    main()
