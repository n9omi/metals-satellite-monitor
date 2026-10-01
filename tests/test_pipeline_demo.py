import json

import pandas as pd
from pypdf import PdfReader


def test_demo_pipeline_writes_all_outputs(demo_ctx):
    O, F, R = demo_ctx.paths.outputs, demo_ctx.paths.figures, demo_ctx.paths.reports
    for name in ("site_panel", "site_zscores", "site_indices", "eda_summary", "coverage", "climate_metrics",
                 "commodity_indices", "lead_lag", "lead_lag_best", "anomalies_recent", "site_snapshot",
                 "commodity_snapshot", "climate_snapshot", "production_validation", "prices_monthly"):
        assert (O / f"{name}.csv").exists(), name
    meta = json.loads((O / "run_meta.json").read_text())
    assert meta["mode"] == "demo" and meta["takeaways"]
    assert (F / "commodity_indices.png").exists() and (F / "site_heatmap.png").exists()
    assert len(list(F.glob("site_*.png"))) == meta["n_sites"] + 1  # + heatmap
    assert (R / "report.md").exists()


def test_demo_story_is_visible(demo_ctx):
    """The built-in Cobre Panama shutdown must show up as a negative utilization index."""
    si = pd.read_csv(demo_ctx.paths.outputs / "site_indices.csv", parse_dates=["date"])
    cp = si[(si["site"] == "cobre_panama") & (si["date"] >= "2024-02-01") & (si["date"] <= "2024-12-01")]
    assert cp["utilization_idx_3m"].mean() < -1.5
    rs = pd.read_csv(demo_ctx.paths.outputs / "climate_snapshot.csv")
    assert "DRY" in rs.set_index("region").loc["yunnan", "flag"]


def test_pdf_is_built_and_labelled(demo_ctx):
    pdf = demo_ctx.paths.reports / "summary_report.pdf"
    reader = PdfReader(str(pdf))
    assert len(reader.pages) >= 8
    first = reader.pages[0].extract_text()
    assert "SYNTHETIC DEMO DATA" in first and "Key takeaways" in first
    last = reader.pages[-1].extract_text()
    assert "Methodology" in last and "attribution" in last.lower()
