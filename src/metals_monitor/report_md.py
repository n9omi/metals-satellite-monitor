"""Markdown report (renders on GitHub) and the shared report-input loader."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from . import config as C


def _read(path, **kw):
    if not path.exists() or path.stat().st_size < 3:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, **kw)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def load_report_inputs(ctx):
    O, F = ctx.paths.outputs, ctx.paths.figures
    meta_p = O / "run_meta.json"
    if not meta_p.exists():
        raise SystemExit("Missing outputs/run_meta.json - run step 04 (fundamentals) first.")
    r = dict(meta=json.loads(meta_p.read_text()))
    for name in ("site_snapshot", "commodity_snapshot", "climate_snapshot"):
        r[name] = _read(O / f"{name}.csv", parse_dates=["last_month"])
    r["anomalies"] = _read(O / "anomalies_recent.csv", parse_dates=["month"])
    r["lead_lag_best"] = _read(O / "lead_lag_best.csv")
    r["production"] = _read(O / "production_validation.csv")
    r["coverage"] = _read(O / "coverage.csv")
    for k in ("climate_snapshot", "site_snapshot", "commodity_snapshot"):
        df = r[k]
        for col in ("flag", "top_driver", "leaders"):
            if col in df:
                df[col] = df[col].fillna("")
    r["figures"] = {p.stem: p for p in sorted(F.glob("*.png"))}
    return r


def md_table(df, fmt="{:.2f}"):
    if df is None or df.empty:
        return "_none_\n"
    lines = ["| " + " | ".join(map(str, df.columns)) + " |", "|" + "---|" * len(df.columns)]
    for _, row in df.iterrows():
        cells = []
        for v in row.values:
            if isinstance(v, pd.Timestamp):
                cells.append("" if pd.isna(v) else v.strftime("%Y-%m"))
            elif isinstance(v, (float, np.floating)):
                cells.append("" if np.isnan(v) else fmt.format(v))
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def write_markdown(ctx):
    r = load_report_inputs(ctx)
    m = r["meta"]
    out = ctx.paths.reports / "report.md"
    L = ["# Metals & Minerals Satellite Monitor\n\n"]
    if m["mode"] == "demo":
        L.append("> **SYNTHETIC DEMO DATA.** These numbers are random test data, not observations.\n\n")
    L.append(f"Window {m['window_start']} to {m['window_end']} | generated {m['generated']} | "
             f"{m['n_sites']} sites, {m['n_regions']} climate regions\n\n")
    L.append("Indices are point-in-time z-scores against each site's own seasonal history "
             "(0 = normal for that calendar month; +/-2 = unusual).\n\n## Key takeaways\n\n")
    L += [f"- {t}\n" for t in m["takeaways"]]

    cs = r["commodity_snapshot"]
    if not cs.empty:
        L += ["\n## Commodity supply-activity indices\n\n",
              md_table(cs[["commodity", "last_month", "util_3m", "util_3m_chg", "capacity_idx", "n_sites", "leaders"]]),
              "\n![commodity indices](../figures/commodity_indices.png)\n"]
    rs = r["climate_snapshot"]
    if not rs.empty:
        L += ["\n## Climate and power-supply risk\n\n",
              md_table(rs[["label", "commodities", "risk_side", "last_month", "precip3_pct_normal",
                           "spi3_like", "t2m_anom_c", "flag"]]),
              "\n![climate](../figures/climate_spi3.png)\n"]
    ss = r["site_snapshot"]
    if not ss.empty:
        L += ["\n## Site snapshot\n\n",
              md_table(ss[["site", "commodities", "role", "last_month", "util_idx", "util_3m",
                           "capacity_idx", "top_driver", "top_driver_z"]]),
              "\n![site heatmap](../figures/site_heatmap.png)\n"]
    an = r["anomalies"]
    L += ["\n## Recent anomalies (|z| >= 2)\n\n", md_table(an.head(25) if not an.empty else an)]
    if not r["lead_lag_best"].empty:
        L += ["\n## Lead-lag vs. prices (strongest lag per pair)\n\n", md_table(r["lead_lag_best"]),
              "\np_approx uses an autocorrelation-adjusted n_eff but ignores multiple testing across "
              "13 lags and every pair. Treat |r| < 0.3 as noise.\n"]
    if not r["production"].empty:
        L += ["\n## Validation vs. reported production\n\n", md_table(r["production"])]
    if not r["coverage"].empty:
        L += ["\n## Data coverage (% of months missing after QC)\n\n", md_table(r["coverage"], "{:.0f}")]
    L.append("\n## Site panels\n\n")
    L += [f"- [{s}](../figures/site_{s}.png)\n" for s in ss["site"]] if not ss.empty else []
    L.append("\nMethod: see [docs/methodology.md](../docs/methodology.md).\n")
    out.write_text("".join(L), encoding="utf-8")
    return out


SIGNAL_SHORT = C.SIGNAL_SHORT
