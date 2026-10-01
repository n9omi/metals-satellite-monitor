"""Step 03 - Quality control, point-in-time seasonal z-scores, site capacity/utilization indices, EDA
tables and climate metrics -> outputs/.

Usage: python pipeline/03_features.py [--demo] [options]   (see --help)
"""
import sys

from metals_monitor.cli import run_step

if __name__ == "__main__":
    sys.exit(run_step("features", description=__doc__))
