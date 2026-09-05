"""Compatibility shim for toolchains with setuptools < 61 (which ignore [project] in pyproject.toml).

pyproject.toml is the canonical metadata source; keep the two in sync when bumping versions.
"""
import re
from pathlib import Path

from setuptools import setup

version = re.search(r'__version__ = "([^"]+)"', (Path(__file__).parent / "openrad" / "__init__.py").read_text()).group(1)

setup(
    name="openradiology",
    version=version,
    packages=["openrad", "openrad.locales"],
    package_data={"openrad": ["py.typed", "schema/*.json", "locales/*.json"]},
    python_requires=">=3.9",
    install_requires=["pydicom>=2.4.0", "numpy>=1.22.0", "Pillow>=9.0.0", "tomli>=2.0; python_version < '3.11'"],
    entry_points={"console_scripts": ["openrad=openrad.cli:main"]},
)
