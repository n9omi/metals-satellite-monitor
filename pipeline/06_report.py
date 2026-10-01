"""Step 06 - reports/report.md and reports/summary_report.pdf (+ a dated copy in reports/archive/).

Usage: python pipeline/06_report.py [--demo] [options]   (see --help)
"""
import sys

from metals_monitor.cli import run_step

if __name__ == "__main__":
    sys.exit(run_step("report", description=__doc__))
