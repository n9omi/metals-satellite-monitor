"""Step 02 - Pull monthly satellite signals from Earth Engine into data/cache/ (incremental). In --demo
mode, writes synthetic data instead.

Usage: python pipeline/02_pull.py [--demo] [options]   (see --help)
"""
import sys

from metals_monitor.cli import run_step

if __name__ == "__main__":
    sys.exit(run_step("pull", description=__doc__))
