"""Pipeline steps. Each step reads the previous step's files and writes its own,
so any step can be re-run on its own (see run.sh and pipeline/)."""
from __future__ import annotations

import json
import logging

import pandas as pd

from . import analytics as A
from . import charts as CH

LOG = logging.getLogger("metals_monitor")


# ---------------------------------------------------------------------------
# 01 - ROI sanity check (Earth Engine)
# ---------------------------------------------------------------------------
def step_check_sites(ctx):
    from . import ee_signals as E
    if ctx.demo:
        LOG.info("check-sites needs Earth Engine; skipped in demo mode.")
        return
    E.ee_init(ctx.args.project)
    E.check_sites(ctx.sites, ctx.regions, ctx.paths.site_checks, ctx.end)


# ---------------------------------------------------------------------------
# 02 - Pull (Earth Engine, or synthetic in demo mode)
# ---------------------------------------------------------------------------
def step_pull(ctx):
    if ctx.demo:
        from .demo import make_demo_data
        make_demo_data(ctx.sites, ctx.regions, ctx.paths, ctx.start, ctx.end)
        return
    from . import ee_signals as E
    from .pull import run_pulls
    E.ee_init(ctx.args.project)
    failures = run_pulls(ctx.sites, ctx.regions, ctx.start, ctx.end, ctx.paths.cache,
                         ctx.args.workers, ctx.args.chunk_months, ctx.args.refresh)
    (ctx.paths.outputs / "pull_failures.json").write_text(json.dumps(failures, indent=2))


# ---------------------------------------------------------------------------
# 03 - Features: QC, z-scores, site indices, EDA, climate metrics
# ---------------------------------------------------------------------------
def step_features(ctx):
    P, idx = ctx.paths, A.month_index(ctx.start, ctx.end)
    panels, zframes, idx_frames = {}, {}, {}
    for sid, cfg in ctx.sites.items():
        df = A.load_site_panel(P.cache, sid, cfg, idx)
        if df is None:
            LOG.warning("%s: no cached data - run step 02 first", sid)
            continue
        df = A.qc_site(df)
        panels[sid] = df[A.signal_cols(df)]
        zframes[sid] = A.site_zscores(df)
        idx_frames[sid] = A.site_indices(df, zframes[sid], cfg["role"])
    if not panels:
        raise SystemExit("No cached data. Run step 02 (pull) first.")

    A.wide_to_long(panels).to_csv(P.outputs / "site_panel.csv", index=False)
    A.wide_to_long(zframes, "z").to_csv(P.outputs / "site_zscores.csv", index=False)
    (pd.concat(idx_frames, names=["site", "date"]).reset_index()
     .to_csv(P.outputs / "site_indices.csv", index=False))

    eda = A.eda_summary(panels)
    eda.to_csv(P.outputs / "eda_summary.csv", index=False)
    A.coverage_table(eda).to_csv(P.outputs / "coverage.csv")
    for sid, df in panels.items():
        ds = A.deseasonalized(df)
        if ds.shape[1] >= 2:
            ds.corr(min_periods=12).round(3).to_csv(P.outputs / "eda_corr" / f"corr_{sid}.csv")

    clim = {}
    for rid, rcfg in ctx.regions.items():
        p = P.cache / f"{rid}__climate.csv"
        if p.exists():
            df = pd.read_csv(p, parse_dates=["date"]).set_index("date").sort_index()
            full = pd.date_range(df.index.min(), idx[-1], freq="MS")
            clim[rid] = A.climate_metrics(df.reindex(full), rcfg)
    if clim:
        pd.concat(clim, names=["region", "date"]).to_csv(P.outputs / "climate_metrics.csv")
    LOG.info("features: %d sites, %d regions -> %s", len(panels), len(clim), P.outputs)


# ---------------------------------------------------------------------------
# Loaders shared by steps 04-06
# ---------------------------------------------------------------------------
def load_state(ctx):
    P, idx = ctx.paths, A.month_index(ctx.start, ctx.end)
    need = P.outputs / "site_indices.csv"
    if not need.exists():
        raise SystemExit("Missing outputs/site_indices.csv - run step 03 (features) first.")
    panel = pd.read_csv(P.outputs / "site_panel.csv", parse_dates=["date"])
    zs = pd.read_csv(P.outputs / "site_zscores.csv", parse_dates=["date"])
    si = pd.read_csv(need, parse_dates=["date"])
    sites = {k: v for k, v in ctx.sites.items() if k in set(si["site"])}
    st = dict(
        idx=idx, sites=sites,
        panels=A.long_to_wide(panel, idx),
        zframes=A.long_to_wide(zs, idx, value_name="z"),
        idx_frames={s: g.drop(columns="site").set_index("date").reindex(idx)
                    for s, g in si.groupby("site", sort=False)},
        clim={},
    )
    cp = P.outputs / "climate_metrics.csv"
    if cp.exists():
        cm = pd.read_csv(cp, parse_dates=["date"])
        cm["risk_flag"] = cm["risk_flag"].fillna("")
        st["clim"] = {r: g.drop(columns="region").set_index("date")
                      for r, g in cm.groupby("region", sort=False) if r in ctx.regions}
    return st


# ---------------------------------------------------------------------------
# 04 - Fundamentals: commodity indices, prices, lead-lag, validation, snapshots
# ---------------------------------------------------------------------------
def step_fundamentals(ctx):
    from .prices import load_prices, read_prices_csv
    P, a = ctx.paths, ctx.args
    st = load_state(ctx)
    sites, idx = st["sites"], st["idx"]

    util = A.commodity_indices(st["idx_frames"], sites, "utilization_idx")
    cap = A.commodity_indices(st["idx_frames"], sites, "capacity_idx")
    long_u = util.reset_index(names="date").melt("date", var_name="commodity", value_name="utilization_idx")
    long_u["utilization_idx_3m"] = (long_u.groupby("commodity")["utilization_idx"]
                                    .transform(lambda s: s.rolling(3, min_periods=2).mean()))
    if not cap.empty:
        long_c = cap.reset_index(names="date").melt("date", var_name="commodity", value_name="capacity_idx")
        long_u = long_u.merge(long_c, on=["date", "commodity"], how="left")
    long_u.to_csv(P.outputs / "commodity_indices.csv", index=False)

    commodities = sorted({c for cfg in sites.values() for c in cfg["commodities"]}
                         | {c for r in ctx.regions.values() for c in r["commodities"]})
    if ctx.demo:
        prices = read_prices_csv(P.base / "data" / "demo_prices.csv")
        prices = prices[[c for c in prices.columns if c in commodities]]
    else:
        prices = load_prices(commodities, idx[0], idx[-1] + pd.DateOffset(months=1), ctx.tickers,
                             a.prices_csv, not a.no_prices)
    prices = prices[(prices.index >= idx[0]) & (prices.index <= idx[-1])] if not prices.empty else prices
    if not prices.empty:
        (prices.reset_index(names="date").melt("date", var_name="commodity", value_name="price")
         .dropna().to_csv(P.outputs / "prices_monthly.csv", index=False))

    frames = []
    for com in prices.columns:
        if com in util:
            sm = util[com].rolling(3, min_periods=2).mean()
            if sm.notna().sum() >= 18:
                d = A.lead_lag(sm.reindex(prices.index), prices[com])
                d.insert(0, "pair", f"{com} utilization -> {com} price")
                frames.append(d)
        for rid, df in st["clim"].items():
            if com in ctx.regions[rid]["commodities"]:
                d = A.lead_lag(df["spi3_like"].reindex(prices.index), prices[com])
                d.insert(0, "pair", f"{rid} SPI-3 -> {com} price")
                frames.append(d)
    ll = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    llb = A.lead_lag_best(ll)
    ll.to_csv(P.outputs / "lead_lag.csv", index=False)
    llb.to_csv(P.outputs / "lead_lag_best.csv", index=False)

    prod_path = (P.base / "data" / "demo_production.csv") if ctx.demo else a.production_csv
    pv = pd.DataFrame()
    if prod_path:
        pv = A.production_validation(st["idx_frames"], pd.read_csv(prod_path, parse_dates=["date"]))
    pv.to_csv(P.outputs / "production_validation.csv", index=False)

    anomalies = A.recent_anomalies(st["zframes"], idx[-1], months=a.anomaly_months)
    anomalies.to_csv(P.outputs / "anomalies_recent.csv", index=False)

    ss = A.site_snapshot(st["idx_frames"], st["zframes"], sites)
    cs = A.commodity_snapshot(util, cap, ss, sites)
    rs = A.climate_snapshot(st["clim"], ctx.regions)
    ss.to_csv(P.outputs / "site_snapshot.csv", index=False)
    cs.to_csv(P.outputs / "commodity_snapshot.csv", index=False)
    rs.to_csv(P.outputs / "climate_snapshot.csv", index=False)

    meta = dict(
        mode="demo" if ctx.demo else "live",
        window_start=f"{idx[0]:%Y-%m}", window_end=f"{idx[-1]:%Y-%m}",
        generated=pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
        n_sites=len(sites), n_regions=len(st["clim"]),
        prices=list(prices.columns),
        takeaways=A.takeaways(cs, ss, rs, anomalies, llb, ctx.regions, sites),
    )
    (P.outputs / "run_meta.json").write_text(json.dumps(meta, indent=2))
    LOG.info("fundamentals: %d commodities, %d price series, %d anomalies",
             util.shape[1], prices.shape[1], len(anomalies))


# ---------------------------------------------------------------------------
# 05 - Figures
# ---------------------------------------------------------------------------
def step_figures(ctx):
    P = ctx.paths
    st = load_state(ctx)
    sites = st["sites"]
    for sid, df in st["panels"].items():
        CH.plot_site(sid, sites[sid], df, st["idx_frames"][sid], P.figures / f"site_{sid}.png")
    ci = pd.read_csv(P.outputs / "commodity_indices.csv", parse_dates=["date"])
    util = ci.pivot(index="date", columns="commodity", values="utilization_idx")
    cap = (ci.pivot(index="date", columns="commodity", values="capacity_idx")
           if "capacity_idx" in ci else pd.DataFrame())
    if not util.empty:
        CH.plot_commodities(util, cap, P.figures / "commodity_indices.png")
    CH.plot_site_heatmap(st["idx_frames"], sites, P.figures / "site_heatmap.png")
    if st["clim"]:
        CH.plot_climate(st["clim"], ctx.regions, P.figures / "climate_spi3.png")
    llp = P.outputs / "lead_lag.csv"
    if llp.exists() and llp.stat().st_size > 5:
        ll = pd.read_csv(llp)
        if not ll.empty:
            CH.plot_lead_lag(ll, P.figures / "lead_lag.png")
    LOG.info("figures -> %s", P.figures)


# ---------------------------------------------------------------------------
# 06 - Reports (Markdown + PDF)
# ---------------------------------------------------------------------------
def step_report(ctx):
    from .report_md import write_markdown
    from .report_pdf import build_pdf
    md = write_markdown(ctx)
    pdf = build_pdf(ctx)
    LOG.info("report -> %s and %s", md, pdf)
