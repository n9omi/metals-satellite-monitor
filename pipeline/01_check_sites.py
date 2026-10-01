"""Step 01 - ROI sanity check: a cloud-free Sentinel-2 thumbnail of every site with its region of
interest outlined, plus figures/site_checks/rois.geojson for QGIS.

Usage: python pipeline/01_check_sites.py [--demo] [options]   (see --help)
"""
import sys

from metals_monitor.cli import run_step

if __name__ == "__main__":
    sys.exit(run_step("check-sites", description=__doc__))
