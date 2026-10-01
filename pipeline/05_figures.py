"""Step 05 - Figures -> figures/ (site panels, commodity indices, site heatmap, climate, lead-lag).

Usage: python pipeline/05_figures.py [--demo] [options]   (see --help)
"""
import sys

from metals_monitor.cli import run_step

if __name__ == "__main__":
    sys.exit(run_step("figures", description=__doc__))
