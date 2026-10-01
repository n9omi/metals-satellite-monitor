"""Step 04 - Commodity supply indices, prices, lead-lag, production validation, anomalies, snapshots
and takeaways -> outputs/.

Usage: python pipeline/04_fundamentals.py [--demo] [options]   (see --help)
"""
import sys

from metals_monitor.cli import run_step

if __name__ == "__main__":
    sys.exit(run_step("fundamentals", description=__doc__))
