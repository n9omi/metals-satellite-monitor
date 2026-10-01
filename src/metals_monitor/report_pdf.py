"""Summary analysis PDF (reportlab).

Layout: summary page (headline tiles, takeaways, commodity table), commodity and
site views, climate and power risk, anomalies, price linkage and validation,
one appendix page per site, then methodology and data attribution."""
from __future__ import annotations

import os
import re
import shutil

import numpy as np
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from .report_md import load_report_inputs

INK, INK2, MUTED = colors.HexColor("#0b0b0b"), colors.HexColor("#52514e"), colors.HexColor("#898781")
GRID, PANEL = colors.HexColor("#e1e0d9"), colors.HexColor("#f4f3f0")
ACCENT = colors.HexColor("#2a78d6")
DEMO_RED = colors.HexColor("#d0453f")
# Light tints of the diverging scale for table cells (value is always printed too)
TINTS = [(-1.5, "#f2b3b2"), (-0.5, "#f8dcdb"), (0.5, None), (1.5, "#dbe9fb"), (99, "#b7d3f6")]

PAGE_W, PAGE_H = letter
MARGIN = 0.6 * inch
CONTENT_W = PAGE_W - 2 * MARGIN


# ---------------------------------------------------------------------------
# Fonts and styles
# ---------------------------------------------------------------------------
def _register_fonts():
    """DejaVu Sans ships with matplotlib, so it is always available and covers
    the glyphs the built-in PDF fonts lack."""
    import matplotlib
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    d = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
    try:
        pdfmetrics.registerFont(TTFont("DV", os.path.join(d, "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("DV-B", os.path.join(d, "DejaVuSans-Bold.ttf")))
        pdfmetrics.registerFont(TTFont("DV-I", os.path.join(d, "DejaVuSans-Oblique.ttf")))
        from reportlab.lib.fonts import addMapping
        addMapping("DV", 0, 0, "DV")
        addMapping("DV", 1, 0, "DV-B")
        addMapping("DV", 0, 1, "DV-I")
        addMapping("DV", 1, 1, "DV-B")
        return "DV", "DV-B"
    except Exception:
        return "Helvetica", "Helvetica-Bold"


def _styles(font, bold):
    s = {}
    s["title"] = ParagraphStyle("title", fontName=bold, fontSize=19, leading=23, textColor=INK)
    s["subtitle"] = ParagraphStyle("subtitle", fontName=font, fontSize=9.5, leading=13, textColor=INK2)
    s["h1"] = ParagraphStyle("h1", fontName=bold, fontSize=13, leading=17, textColor=INK,
                             spaceBefore=6, spaceAfter=6)
    s["h2"] = ParagraphStyle("h2", fontName=bold, fontSize=10.5, leading=14, textColor=INK,
                             spaceBefore=8, spaceAfter=4)
    s["body"] = ParagraphStyle("body", fontName=font, fontSize=8.8, leading=12.4, textColor=INK,
                               alignment=TA_LEFT)
    s["bullet"] = ParagraphStyle("bullet", parent=s["body"], leftIndent=11, bulletIndent=0,
                                 spaceAfter=3.5)
    s["small"] = ParagraphStyle("small", fontName=font, fontSize=7.4, leading=9.8, textColor=INK2)
    s["cell"] = ParagraphStyle("cell", fontName=font, fontSize=7.6, leading=9.4, textColor=INK)
    s["cellb"] = ParagraphStyle("cellb", parent=s["cell"], fontName=bold)
    s["cellr"] = ParagraphStyle("cellr", parent=s["cell"], alignment=2)
    s["head"] = ParagraphStyle("head", fontName=bold, fontSize=7.4, leading=9.2, textColor=INK2)
    s["headr"] = ParagraphStyle("headr", parent=s["head"], alignment=2)
    s["tile_v"] = ParagraphStyle("tile_v", fontName=bold, fontSize=17, leading=20, textColor=INK)
    s["tile_l"] = ParagraphStyle("tile_l", fontName=font, fontSize=7.6, leading=9.5, textColor=INK2)
    s["banner"] = ParagraphStyle("banner", fontName=bold, fontSize=8.6, leading=11.5,
                                 textColor=colors.white)
    return s


# ---------------------------------------------------------------------------
# Small builders
# ---------------------------------------------------------------------------
def _esc(x):
    return (str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _fmt(v, f="{:+.1f}"):
    if v is None or (isinstance(v, float) and np.isnan(v)) or (isinstance(v, (np.floating,)) and np.isnan(v)):
        return "-"
    if isinstance(v, pd.Timestamp):
        return "-" if pd.isna(v) else v.strftime("%b %Y")
    if isinstance(v, (int, np.integer)):
        return str(v)
    if isinstance(v, (float, np.floating)):
        out = f.format(v)
        if re.fullmatch(r"-0(\.0+)?%?", out):  # no negative zero in tables
            out = ("+" if "+" in f else "") + out[1:]
        return out
    return _esc(v)


def _tint(z):
    if z is None or pd.isna(z):
        return None
    for hi, hexc in TINTS:
        if z < hi:
            return colors.HexColor(hexc) if hexc else None
    return None


def _table(S, header, rows, widths, right_cols=(), tint_cols=None, zebra=False):
    """rows: list of lists of raw values. tint_cols: {col_index: column of z values}."""
    data = [[Paragraph(_esc(h), S["headr"] if i in right_cols else S["head"]) for i, h in enumerate(header)]]
    for r in rows:
        data.append([v if isinstance(v, Paragraph) else
                     Paragraph(v if isinstance(v, str) else _fmt(v), S["cellr"] if i in right_cols else S["cell"])
                     for i, v in enumerate(r)])
    t = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), PANEL),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, MUTED),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, GRID),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    for ci, zs in (tint_cols or {}).items():
        for ri, z in enumerate(zs, start=1):
            c = _tint(z)
            if c is not None:
                style.append(("BACKGROUND", (ci, ri), (ci, ri), c))
    t.setStyle(TableStyle(style))
    return t


def _img(path, width=CONTENT_W, max_h=8.4 * inch):
    from reportlab.lib.utils import ImageReader
    iw, ih = ImageReader(str(path)).getSize()
    w, h = width, width * ih / iw
    if h > max_h:
        w, h = max_h * iw / ih, max_h
    im = Image(str(path), width=w, height=h)
    im.hAlign = "LEFT"
    return im


def _tiles(S, items):
    cells = [[Paragraph(_esc(v), S["tile_v"]) for v, _ in items],
             [Paragraph(_esc(lbl), S["tile_l"]) for _, lbl in items]]
    w = CONTENT_W / len(items)
    t = Table(cells, colWidths=[w - 4] * len(items))
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PANEL),
        ("LINEBEFORE", (1, 0), (-1, -1), 2, colors.white),
        ("TOPPADDING", (0, 0), (-1, 0), 7), ("BOTTOMPADDING", (0, 1), (-1, 1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def _banner(S, text, color):
    t = Table([[Paragraph(text, S["banner"])]], colWidths=[CONTENT_W])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), color),
                           ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 5),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    return t


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------
def build_pdf(ctx, out_path=None):
    font, bold = _register_fonts()
    S = _styles(font, bold)
    r = load_report_inputs(ctx)
    m, figs = r["meta"], r["figures"]
    demo = m["mode"] == "demo"
    sites = ctx.sites
    end_label = pd.Timestamp(m["window_end"] + "-01").strftime("%b %Y")
    out_path = out_path or ctx.paths.reports / "summary_report.pdf"

    def on_page(canv, doc):
        canv.saveState()
        canv.setFont(font, 7.4)
        canv.setFillColor(INK2)
        canv.drawString(MARGIN, PAGE_H - 0.42 * inch, "Metals & Minerals Satellite Monitor")
        canv.drawRightString(PAGE_W - MARGIN, PAGE_H - 0.42 * inch, f"Data through {end_label}")
        canv.setStrokeColor(GRID)
        canv.setLineWidth(0.5)
        canv.line(MARGIN, PAGE_H - 0.47 * inch, PAGE_W - MARGIN, PAGE_H - 0.47 * inch)
        canv.drawString(MARGIN, 0.38 * inch,
                        f"Generated {m['generated']}. Satellite activity proxies, not production data.")
        canv.drawRightString(PAGE_W - MARGIN, 0.38 * inch, f"Page {doc.page}")
        if demo:
            canv.setFont(bold, 46)
            canv.setFillColor(DEMO_RED)
            canv.setFillAlpha(0.08)
            canv.translate(PAGE_W / 2, PAGE_H / 2)
            canv.rotate(35)
            canv.drawCentredString(0, 0, "SYNTHETIC DEMO DATA")
        canv.restoreState()

    doc = BaseDocTemplate(str(out_path), pagesize=letter, leftMargin=MARGIN, rightMargin=MARGIN,
                          topMargin=0.7 * inch, bottomMargin=0.65 * inch,
                          title="Metals & Minerals Satellite Monitor - Summary Analysis",
                          author="metals-satellite-monitor", subject=f"Data through {end_label}")
    frame = Frame(MARGIN, 0.65 * inch, CONTENT_W, PAGE_H - 1.35 * inch, id="f",
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id="p", frames=[frame], onPage=on_page)])
    E = []

    # ---------------- Page 1: summary ----------------
    E += [Paragraph("Metals &amp; Minerals Supply Monitor", S["title"]),
          Paragraph(f"Summary analysis, data through {end_label} (window {m['window_start']} to "
                    f"{m['window_end']}). {m['n_sites']} sites and {m['n_regions']} climate regions.",
                    S["subtitle"]), Spacer(1, 8)]
    if demo:
        E += [_banner(S, "SYNTHETIC DEMO DATA: random test series generated to show the report layout. "
                         "Nothing in this document is an observation.", DEMO_RED), Spacer(1, 8)]
    an, rs, cs, ss = r["anomalies"], r["climate_snapshot"], r["commodity_snapshot"], r["site_snapshot"]
    n_flag = int((rs["flag"] != "").sum()) if not rs.empty else 0
    latest_moves = int((an["month"] == an["month"].max()).sum()) if not an.empty else 0
    E += [_tiles(S, [(str(m["n_sites"]), "sites monitored"),
                     (str(len(cs)), "commodity indices"),
                     (str(n_flag), "climate / power regions flagged"),
                     (str(latest_moves), "2-sigma signal moves, latest month")]),
          Spacer(1, 10), Paragraph("Key takeaways", S["h1"])]
    E += [Paragraph(_esc(t), S["bullet"], bulletText="•") for t in m["takeaways"]]
    if not cs.empty:
        E += [Spacer(1, 4), Paragraph("Commodity supply activity", S["h2"])]
        rows = [[Paragraph(_esc(x.commodity.replace("_", " ").title()), S["cellb"]), x.util_3m, x.util_3m_chg,
                 x.capacity_idx, int(x.n_sites), _esc(x.leaders or "-")] for x in cs.itertuples()]
        E.append(_table(S, ["Commodity", "Utilization (3m z)", "Change vs. 3m ago", "Capacity (z)", "Sites",
                            "Largest site moves (3m z)"], rows,
                        [1.05 * inch, 0.95 * inch, 0.95 * inch, 0.8 * inch, 0.45 * inch, CONTENT_W - 4.2 * inch],
                        right_cols=(1, 2, 3, 4), tint_cols={1: list(cs["util_3m"]), 3: list(cs["capacity_idx"])}))
        E.append(Paragraph("Cell shading: blue above, red below each series' own seasonal norm; "
                           "the value is always printed. Supply is roughly capacity x utilization.", S["small"]))

    # ---------------- Commodity + site views ----------------
    E.append(PageBreak())
    E.append(Paragraph("Commodity and site activity", S["h1"]))
    E.append(Paragraph("Utilization averages throughput proxies (SO<sub>2</sub>/NO<sub>2</sub> enhancement, "
                       "night lights, thermal hotspots, surface and SAR change). Capacity tracks footprint "
                       "growth (bare/built area, pond area for brine). Both are z-scores against the site's own "
                       "history up to that month.", S["body"]))
    if "commodity_indices" in figs:
        E += [Spacer(1, 6), _img(figs["commodity_indices"], max_h=4.6 * inch)]
    if "site_heatmap" in figs:
        E += [Spacer(1, 6), _img(figs["site_heatmap"], max_h=4.4 * inch)]

    # ---------------- Climate ----------------
    if not rs.empty:
        E.append(PageBreak())
        E.append(Paragraph("Climate and power-supply risk", S["h1"]))
        rows = [[Paragraph(_esc(x.label), S["cellb"]), _esc(x.commodities.replace("_", " ")), x.risk_side,
                 x.last_month, _fmt(x.precip3_pct_normal, "{:.0f}%"), x.spi3_like, x.t2m_anom_c,
                 Paragraph(_esc(x.flag or "-"), S["cellb"])]
                for x in rs.itertuples()]
        E.append(_table(S, ["Region", "Exposure", "Risk side", "Month", "3m rain % normal", "SPI-3-like",
                            "Temp anom. (C)", "Flag"], rows,
                        [1.6 * inch, 1.05 * inch, 0.55 * inch, 0.65 * inch, 0.8 * inch, 0.7 * inch, 0.75 * inch,
                         CONTENT_W - 6.1 * inch],
                        right_cols=(4, 5, 6), tint_cols={5: list(rs["spi3_like"])}))
        E.append(Spacer(1, 4))
        E += [Paragraph(f"<b>{_esc(regions_label)}</b>: {_esc(ctx.regions[rid]['mechanism'])}", S["small"])
              for rid, regions_label in zip(rs["region"], rs["label"])]
        if "climate_spi3" in figs:
            E += [Spacer(1, 8), _img(figs["climate_spi3"], max_h=5.6 * inch)]

    # ---------------- Sites + anomalies ----------------
    E.append(PageBreak())
    E.append(Paragraph("Site snapshot", S["h1"]))
    if not ss.empty:
        rows = [[Paragraph(_esc(sites[x.site]["label"]), S["cellb"]), _esc(x.commodities.replace("_", " ")),
                 x.last_month,
                 x.util_idx, x.util_3m, x.capacity_idx,
                 _esc(f"{x.top_driver} ({x.top_driver_z:+.1f})" if x.top_driver else "-")]
                for x in ss.itertuples()]
        E.append(_table(S, ["Site", "Commodities", "Month", "Util. (z)", "Util. 3m", "Capacity", "Top driver"],
                        rows, [2.05 * inch, 1.2 * inch, 0.65 * inch, 0.6 * inch, 0.6 * inch, 0.65 * inch,
                               CONTENT_W - 5.75 * inch],
                        right_cols=(3, 4, 5), tint_cols={3: list(ss["util_idx"]), 4: list(ss["util_3m"]),
                                                         5: list(ss["capacity_idx"])}))
    E.append(Paragraph(f"Recent anomalies (|z| of 2 or more, last {ctx.args.anomaly_months} months)", S["h2"]))
    if an.empty:
        E.append(Paragraph("None.", S["body"]))
    else:
        top = an.head(18)
        rows = [[_esc(sites[x.site]["label"].split(" (")[0] if x.site in sites else x.site), x.month,
                 _esc(_signal_name(x.signal)), x.z, x.direction] for x in top.itertuples()]
        E.append(_table(S, ["Site", "Month", "Signal", "z", "Direction"], rows,
                        [2.3 * inch, 0.8 * inch, 2.4 * inch, 0.6 * inch, CONTENT_W - 6.1 * inch],
                        right_cols=(3,), tint_cols={3: list(top["z"])}))
        if len(an) > len(top):
            E.append(Paragraph(f"{len(an) - len(top)} more in outputs/anomalies_recent.csv.", S["small"]))

    # ---------------- Prices and validation ----------------
    llb, pv, cov = r["lead_lag_best"], r["production"], r["coverage"]
    E.append(PageBreak())
    E.append(Paragraph("Price linkage and validation", S["h1"]))
    E.append(Paragraph("Each pair shows the lag with the largest |r| between the signal level and the "
                       "next months' log returns (k &gt; 0 means the satellite signal leads). p uses an "
                       "autocorrelation-adjusted sample size but not a multiple-testing correction across 13 "
                       "lags, so read these as hypotheses.", S["body"]))
    if not llb.empty:
        rows = [[_esc(x.pair.replace("_", " ")), f"{int(x.lag):+d}", _fmt(x.r, "{:+.2f}"), int(x.n),
                 _fmt(x.n_eff, "{:.0f}"), _fmt(x.p_approx, "{:.3f}")]
                for x in llb.itertuples()]
        E += [Spacer(1, 4), _table(S, ["Pair", "Best lag", "r", "n", "n_eff", "p (approx.)"], rows,
                                   [3.3 * inch, 0.7 * inch, 0.7 * inch, 0.5 * inch, 0.6 * inch,
                                    CONTENT_W - 5.8 * inch], right_cols=(1, 2, 3, 4, 5))]
    else:
        E.append(Paragraph("No price series available for the monitored commodities.", S["small"]))
    if "lead_lag" in figs:
        E += [Spacer(1, 6), _img(figs["lead_lag"], max_h=4.2 * inch)]
    if not pv.empty:
        names = {"utilization_idx": "utilization index", "capacity_idx": "capacity index",
                 "production": "production level", "prod_yoy_pct": "production YoY %"}
        rows = [[_esc(sites[x.site]["label"] if x.site in sites else x.site),
                 {"Q": "quarterly", "M": "monthly"}.get(x.freq, x.freq), names.get(x.index, x.index),
                 names.get(x.target, x.target), _fmt(x.r, "{:+.2f}"), int(x.n)] for x in pv.itertuples()]
        E += [KeepTogether([Paragraph("Validation against reported production", S["h2"]),
                            _table(S, ["Site", "Frequency", "Satellite index", "Reported output", "r", "n"],
                                   rows, [2.2 * inch, 0.8 * inch, 1.3 * inch, 1.4 * inch, 0.6 * inch,
                                          CONTENT_W - 6.3 * inch], right_cols=(4, 5)),
                            Paragraph("Validation is the real test of a satellite proxy: a useful site index "
                                      "should co-move with reported output before it is trusted for prices.",
                                      S["small"])])]
    if not cov.empty:
        cols = [c for c in SOURCE_NAMES if c in cov.columns]
        rows = [[_esc(sites[x["site"]]["label"].split(" (")[0] if x["site"] in sites else x["site"])]
                + [_fmt(x[c], "{:.0f}%") for c in cols] for _, x in cov.iterrows()]
        E += [KeepTogether([Paragraph("Data coverage (% of months missing after quality control)", S["h2"]),
                            _table(S, ["Site"] + [SOURCE_NAMES[c] for c in cols], rows,
                                   [2.2 * inch] + [(CONTENT_W - 2.2 * inch) / max(len(cols), 1)] * len(cols),
                                   right_cols=tuple(range(1, len(cols) + 1))),
                            Paragraph("- = signal not used at that site. Gaps come from cloud (optical), polar "
                                      "night or summer, sensor outages and the quality rules in "
                                      "docs/methodology.md.", S["small"])])]

    # ---------------- Appendix: one page per site ----------------
    for sid in ss["site"] if not ss.empty else []:
        key = f"site_{sid}"
        if key not in figs:
            continue
        cfg = sites[sid]
        E.append(PageBreak())
        E.append(Paragraph(f"Site appendix: {_esc(cfg['label'])}", S["h1"]))
        meta_line = (f"{_esc(', '.join(cfg['commodities']))} | {_esc(cfg['role'].replace('_', ' '))} | "
                     f"{_esc(cfg.get('country', ''))} | ROI {cfg['lat']:.3f}, {cfg['lon']:.3f}, radius "
                     f"{cfg['buffer_km']} km | signals: {_esc(', '.join(cfg['indicators']))}")
        E.append(Paragraph(meta_line, S["small"]))
        if cfg.get("note"):
            E.append(Paragraph(_esc(cfg["note"]), S["small"]))
        E += [Spacer(1, 6), _img(figs[key], max_h=8.2 * inch)]

    # ---------------- Methodology ----------------
    E.append(PageBreak())
    E.append(Paragraph("Methodology and caveats", S["h1"]))
    for title, text in METHOD_NOTES:
        E += [Paragraph(title, S["h2"]), Paragraph(text, S["body"])]
    E.append(Paragraph("Data sources and attribution", S["h2"]))
    E += [Paragraph(t, S["small"]) for t in ATTRIBUTION]

    doc.build(E)
    if not demo:
        arch = ctx.paths.reports / "archive"
        arch.mkdir(exist_ok=True)
        shutil.copyfile(out_path, arch / f"summary_report_{m['window_end']}.pdf")
    return out_path


SOURCE_NAMES = {"s2": "Sentinel-2", "s1": "Sentinel-1", "s5p": "Sentinel-5P", "viirs": "VIIRS",
                "firms": "FIRMS", "dw": "Dynamic World"}


def _signal_name(sig):
    from .config import SIGNAL_SHORT
    return SIGNAL_SHORT.get(sig, sig)


METHOD_NOTES = [
    ("What is measured",
     "Each site is a circle (region of interest) around a mine, smelter, industrial park, port or brine "
     "field. Every month the pipeline reduces satellite data over it in Google Earth Engine: Sentinel-2 "
     "surface reflectance with Cloud Score+ masking (bare-soil index, NDVI, MNDWI water/pond fraction and "
     "the share of pixels that changed against the same month a year earlier); Sentinel-1 SAR in one "
     "orbit direction (backscatter, SAR water, year-on-year change); Sentinel-5P tropospheric NO<sub>2</sub> "
     "and SO<sub>2</sub> as the site mean minus a background ring 40-90 km out; VIIRS monthly night lights "
     "screened by cloud-free coverage; MODIS FIRMS thermal-anomaly pixel-days; and the Dynamic World "
     "bare-plus-built footprint. Supply regions get CHIRPS rainfall and ERA5-Land temperature."),
    ("How signals become indices",
     "Each signal is converted to a point-in-time seasonal z-score: the value minus the mean of the same "
     "calendar month in prior years, divided by the standard deviation of past anomalies. Only data "
     "available at that month is used, so the series can be backtested without look-ahead. The utilization "
     "index averages the signed z-scores of throughput proxies; the capacity index averages z-scores of "
     "3-month-smoothed footprint proxies. Commodity indices are weighted means across sites (weights in "
     "config/sites.yaml, default equal). Rainfall risk uses an SPI-3-like standardised 3-month total "
     "against a 2001-2020 baseline by calendar month; flags fire on the side of the distribution that "
     "hurts supply for that region (drought for hydro-powered smelting, extreme rain for haulage and ports)."),
    ("How to read it",
     "These are activity proxies, not production measurements. A z-score near +/-2 says a signal is "
     "unusual for that site and month; it does not say why. Confirm moves against company reports, customs "
     "and trade data, or news before acting. Cloud cover (tropics), polar night (no Sentinel-5P), polar "
     "summer (no night lights) and sensor gaps blank months, which the coverage table shows. Underground "
     "mines expose only their surface footprint. Price correlations on fewer than ~90 monthly points are "
     "fragile, and the best of 13 lags will look significant by chance more often than its p-value suggests."),
    ("Site locations",
     "Coordinates are approximate centroids. Each live run should be preceded by "
     "<font name='Courier'>bash run.sh --check-sites</font>, "
     "which saves a cloud-free image of every site with its outline for visual confirmation."),
]

ATTRIBUTION = [
    "Contains modified Copernicus Sentinel data (Sentinel-1, Sentinel-2, Sentinel-5P), processed in Google "
    "Earth Engine.",
    "Cloud Score+ S2_HARMONIZED V1 (Google, CC-BY-4.0). Dynamic World V1 (Google and World Resources "
    "Institute, CC-BY-4.0).",
    "VIIRS Day/Night Band monthly composites, Earth Observation Group, Payne Institute for Public Policy, "
    "Colorado School of Mines (public domain).",
    "FIRMS MODIS active fire / thermal anomalies, NASA LANCE / EOSDIS. Near-real-time data are not "
    "science quality; see the LANCE disclaimer at earthdata.nasa.gov.",
    "CHIRPS rainfall, UC Santa Barbara Climate Hazards Center. ERA5-Land monthly aggregates, ECMWF / "
    "Copernicus Climate Change Service.",
    "Prices: CME Group futures via Yahoo Finance (yfinance), for research use.",
]
