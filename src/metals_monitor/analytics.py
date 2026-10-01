"""Analytics layer (pure pandas, no Earth Engine): QC, point-in-time z-scores,
capacity / utilization indices, climate risk, lead-lag, validation, snapshots
and auto-generated takeaways."""
from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd

from . import config as C

LOG = logging.getLogger("metals_monitor")


# ---------------------------------------------------------------------------
# Loading and reshaping
# ---------------------------------------------------------------------------
def month_index(start, end):
    """Monthly index from `start` up to (not including) the month `end`."""
    return pd.date_range(pd.Timestamp(start), pd.Timestamp(end) - pd.DateOffset(months=1), freq="MS")


def load_site_panel(cache_dir, sid, cfg, idx):
    parts = []
    for ind in cfg["indicators"]:
        p = cache_dir / f"{sid}__{ind}.csv"
        if not p.exists():
            continue
        df = pd.read_csv(p, parse_dates=["date"]).set_index("date")
        df = df.apply(pd.to_numeric, errors="coerce")
        parts.append(df.add_prefix(f"{ind}_"))
    if not parts:
        return None
    return pd.concat(parts, axis=1).reindex(idx)


def wide_to_long(frames: dict, value_name="value"):
    out = []
    for key, df in frames.items():
        m = df.reset_index(names="date").melt(id_vars="date", var_name="variable", value_name=value_name)
        m.insert(0, "site", key)
        out.append(m.dropna(subset=[value_name]))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(
        columns=["site", "date", "variable", value_name])


def long_to_wide(long: pd.DataFrame, idx=None, value_name="value", key="site"):
    frames = {}
    for k, g in long.groupby(key, sort=False):
        w = g.pivot_table(index="date", columns="variable", values=value_name, aggfunc="last")
        w.columns.name = None
        frames[k] = w.reindex(idx) if idx is not None else w.sort_index()
    return frames


# ---------------------------------------------------------------------------
# Quality control
# ---------------------------------------------------------------------------
def qc_site(df):
    """Blank months that fail coverage rules; derive S5P enhancements."""
    df = df.copy()

    def blank(cols, mask):
        cols = [c for c in cols if c in df.columns]
        if cols:
            df.loc[mask, cols] = np.nan

    def col(name, default=0.0):
        return df[name] if name in df.columns else pd.Series(default, index=df.index)

    if "s2_valid_frac" in df:
        bad = col("s2_valid_frac").fillna(0) < C.MIN_S2_VALID
        blank([c for c in df if c.startswith("s2_") and c not in ("s2_valid_frac", "s2_n_images")], bad)
    if "s1_n_images" in df:
        blank([c for c in df if c.startswith("s1_") and c != "s1_n_images"], col("s1_n_images").fillna(0) < 1)
    for gas in ("no2", "so2"):
        g, bg = f"s5p_{gas}", f"s5p_{gas}_bg"
        if g in df:
            blank([g, bg], col(f"s5p_{gas}_n").fillna(0) < 1)
            df[f"s5p_{gas}_enh"] = df[g] - df[bg] if bg in df else np.nan
    if "viirs_rad_sum" in df:
        cv = col("viirs_cf_cvg", np.nan)
        blank(["viirs_rad_sum", "viirs_rad_mean"], cv.isna() | (cv < 1))
    if "firms_hot_pixel_days" in df:
        blank(["firms_hot_pixel_days"], col("firms_n_days").fillna(0) < 20)
    if "dw_n_images" in df:
        blank(["dw_footprint_frac", "dw_trees_mean"], col("dw_n_images").fillna(0) < 1)
    return df


def signal_cols(df):
    """Analysable signal columns (drops QC counters, backgrounds and raw S5P)."""
    return [c for c in df.columns if not c.endswith(C.QC_SUFFIXES)
            and not c.endswith(("_no2", "_so2")) and df[c].notna().any()]


# ---------------------------------------------------------------------------
# Point-in-time standardisation and indices
# ---------------------------------------------------------------------------
def trailing_seasonal_z(s, min_hist=12):
    """Value minus the mean of the same calendar month in PRIOR years, scaled by
    the std of past anomalies. Uses only data available at each month."""
    s = pd.to_numeric(s, errors="coerce")
    clim = s.groupby(s.index.month).transform(lambda g: g.shift(1).expanding(min_periods=1).mean())
    anom = s - clim
    sd = anom.shift(1).expanding(min_periods=min_hist).std()
    return (anom / sd).replace([np.inf, -np.inf], np.nan)


def site_zscores(df):
    return pd.DataFrame({c: trailing_seasonal_z(df[c]) for c in signal_cols(df)}, index=df.index)


def site_indices(df, z, role):
    """Utilization = mean signed z of throughput proxies; capacity = mean z of
    3-month-smoothed footprint proxies. Each clipped to +/-5 before averaging."""
    util = {c: sgn for c, sgn in C.UTILIZATION_SIGNALS.items() if c in z}
    cap_map = {**C.CAPACITY_SIGNALS, **C.ROLE_CAPACITY_EXTRA.get(role, {})}
    cap = {}
    for c, sgn in cap_map.items():
        if c in df and df[c].notna().sum() >= 12:
            cap[c] = trailing_seasonal_z(df[c].rolling(3, min_periods=2).mean()) * sgn
    U = pd.DataFrame({c: z[c].clip(-5, 5) * sgn for c, sgn in util.items()}, index=df.index)
    K = pd.DataFrame({c: v.clip(-5, 5) for c, v in cap.items()}, index=df.index)
    out = pd.DataFrame(index=df.index)
    out["utilization_idx"] = U.mean(axis=1) if not U.empty else np.nan
    out["utilization_idx_3m"] = out["utilization_idx"].rolling(3, min_periods=2).mean()
    out["n_util_signals"] = U.notna().sum(axis=1) if not U.empty else 0
    out["capacity_idx"] = K.mean(axis=1) if not K.empty else np.nan
    out["n_capacity_signals"] = K.notna().sum(axis=1) if not K.empty else 0
    return out


def commodity_indices(idx_frames, sites, column):
    """Weighted mean of a site index across the sites tied to each commodity."""
    out = {}
    for com in sorted({c for cfg in sites.values() for c in cfg["commodities"]}):
        members = [s for s, cfg in sites.items() if com in cfg["commodities"] and s in idx_frames]
        if not members:
            continue
        U = pd.DataFrame({s: idx_frames[s][column] for s in members})
        W = pd.Series({s: float(sites[s].get("weight", 1.0)) for s in members})
        wsum = U.notna().mul(W, axis=1).sum(axis=1)
        out[com] = (U.fillna(0).mul(W, axis=1).sum(axis=1) / wsum).where(wsum > 0)
    return pd.DataFrame(out)


# ---------------------------------------------------------------------------
# Exploratory statistics
# ---------------------------------------------------------------------------
def eda_summary(panels):
    rows = []
    for sid, df in panels.items():
        for c in signal_cols(df):
            s = df[c]
            v = s.dropna()
            row = dict(site=sid, variable=c, n=len(v), missing_pct=100 * s.isna().mean())
            if len(v):
                yrs = (v.index - v.index[0]).days / 365.25
                monthly = v.groupby(v.index.month).mean()
                row.update(
                    first=v.index[0].strftime("%Y-%m"), last=v.index[-1].strftime("%Y-%m"),
                    mean=v.mean(), std=v.std(), min=v.min(), max=v.max(), last_value=v.iloc[-1],
                    trend_per_yr=np.polyfit(yrs, v.values, 1)[0] if len(v) >= 12 else np.nan,
                    seasonality_ratio=(monthly.std() / v.std()) if len(v) >= 24 and v.std() > 0 else np.nan)
            rows.append(row)
    return pd.DataFrame(rows)


def deseasonalized(df):
    out = {}
    for c in signal_cols(df):
        s = df[c]
        out[c] = s - s.groupby(s.index.month).transform("mean")
    return pd.DataFrame(out, index=df.index)


def coverage_table(eda):
    if eda.empty:
        return pd.DataFrame()
    return (eda.assign(source=eda["variable"].str.split("_").str[0])
            .groupby(["site", "source"])["missing_pct"].mean().unstack().round(0))


# ---------------------------------------------------------------------------
# Climate / power-supply risk
# ---------------------------------------------------------------------------
def climate_metrics(df, rcfg, baseline=C.CLIMATE_BASELINE):
    df = df.apply(pd.to_numeric, errors="coerce").copy()
    if "n_pentads" in df:
        df.loc[df["n_pentads"].fillna(0) < 6, "precip_mm"] = np.nan
    if "n_era5" in df:
        df.loc[df["n_era5"].fillna(0) < 1, "t2m_c"] = np.nan
    y0, y1 = baseline
    base = (df.index.year >= y0) & (df.index.year <= y1)
    m = df.index.month

    def by_month(series, fn):
        return series[base].groupby(m[base]).agg(fn).reindex(m).values

    p = df["precip_mm"]
    p3 = p.rolling(3, min_periods=3).sum()
    df["precip_pct_normal"] = 100 * p / by_month(p, "mean")
    df["precip_z"] = (p - by_month(p, "mean")) / by_month(p, "std")
    df["spi3_like"] = (p3 - by_month(p3, "mean")) / by_month(p3, "std")
    df["precip3_pct_normal"] = 100 * p3 / by_month(p3, "mean")
    df["t2m_anom_c"] = df["t2m_c"] - by_month(df["t2m_c"], "mean")
    df = df.replace([np.inf, -np.inf], np.nan)

    def flag(row):
        f = []
        x = row["spi3_like"]
        if pd.notna(x):
            if rcfg["risk_side"] == "dry" and x <= -1:
                f.append("SEVERE DRY" if x <= -1.5 else "DRY")
            if rcfg["risk_side"] == "wet" and x >= 1:
                f.append("SEVERE WET" if x >= 1.5 else "WET")
        if pd.notna(row["t2m_anom_c"]) and row["t2m_anom_c"] >= 1.5:
            f.append("HEAT")
        return ", ".join(f)

    df["risk_flag"] = df.apply(flag, axis=1)
    return df


# ---------------------------------------------------------------------------
# Prices, lead-lag and production validation
# ---------------------------------------------------------------------------
def lead_lag(signal, price, max_lag=6, min_n=18):
    """corr(signal_t, logret_{t+k}); k > 0 means the satellite signal LEADS price.
    p_approx uses an autocorrelation-adjusted effective sample size."""
    ret = np.log(price.where(price > 0)).diff()
    rows = []
    for transform, sig in (("level", signal), ("change", signal.diff())):
        for k in range(-max_lag, max_lag + 1):
            pair = pd.concat([sig, ret.shift(-k)], axis=1).dropna()
            n = len(pair)
            if n < min_n:
                continue
            r = pair.iloc[:, 0].corr(pair.iloc[:, 1])
            if not np.isfinite(r):
                continue
            a1, a2 = pair.iloc[:, 0].autocorr(1), pair.iloc[:, 1].autocorr(1)
            rho = (a1 * a2) if np.isfinite(a1) and np.isfinite(a2) else 0.0
            n_eff = max(3.0, n * (1 - rho) / (1 + rho)) if rho > -1 else float(n)
            t = r * math.sqrt(max(n_eff - 2, 1) / max(1e-12, 1 - r * r))
            rows.append(dict(signal_transform=transform, lag=k, r=r, n=n, n_eff=round(n_eff, 1),
                             p_approx=math.erfc(abs(t) / math.sqrt(2))))
    return pd.DataFrame(rows)


def lead_lag_best(ll):
    if ll is None or ll.empty:
        return pd.DataFrame()
    return (ll[ll["signal_transform"] == "level"].assign(a=lambda d: d["r"].abs())
            .sort_values("a", ascending=False).groupby("pair").head(1).drop(columns="a")
            .reset_index(drop=True))


def production_validation(idx_frames, prod):
    """Correlate site indices with reported output (site,date,value)."""
    rows = []
    for sid, g in prod.groupby("site"):
        if sid not in idx_frames:
            LOG.warning("production csv: unknown site %s", sid)
            continue
        p = g.set_index("date")["value"].astype(float).sort_index()
        gaps = p.index.to_series().diff().dt.days.median()
        freq, yoy = ("Q", 4) if (gaps or 31) > 45 else ("M", 12)
        P = p.groupby(p.index.to_period(freq)).sum()
        I = idx_frames[sid][["utilization_idx", "capacity_idx"]]
        I = I.groupby(I.index.to_period(freq)).mean()
        j = pd.concat([P.rename("production"), I], axis=1).dropna(subset=["production"])
        j["prod_yoy_pct"] = 100 * j["production"].pct_change(yoy, fill_method=None)
        for col in ("utilization_idx", "capacity_idx"):
            for target in ("production", "prod_yoy_pct"):
                k = j[[col, target]].dropna()
                if len(k) >= 6:
                    rows.append(dict(site=sid, freq=freq, index=col, target=target,
                                     r=k[col].corr(k[target]), n=len(k)))
    return pd.DataFrame(rows)


def recent_anomalies(zframes, end_month, months=6, thresh=2.0):
    cutoff = end_month - pd.DateOffset(months=months - 1)
    rows = []
    for sid, z in zframes.items():
        rec = z[z.index >= cutoff]
        for c in rec.columns:
            for d, v in rec[c].dropna().items():
                if abs(v) >= thresh:
                    rows.append(dict(site=sid, month=d, signal=c, z=v,
                                     direction="up" if v > 0 else "down"))
    df = pd.DataFrame(rows, columns=["site", "month", "signal", "z", "direction"])
    if df.empty:
        return df
    df["abs_z"] = df["z"].abs()
    return (df.sort_values(["month", "abs_z"], ascending=[False, False])
            .drop(columns="abs_z").reset_index(drop=True))


# ---------------------------------------------------------------------------
# Snapshots (latest readings) and auto-generated takeaways
# ---------------------------------------------------------------------------
def _last(series):
    s = series.dropna()
    return (s.index[-1], s.iloc[-1]) if len(s) else (pd.NaT, np.nan)


def site_snapshot(idx_frames, zframes, sites):
    rows = []
    for sid, f in idx_frames.items():
        last, util = _last(f["utilization_idx"])
        u3 = f["utilization_idx_3m"].dropna()
        chg = (u3.iloc[-1] - u3.iloc[-4]) if len(u3) >= 4 else np.nan
        cap_month, cap = _last(f["capacity_idx"])
        driver, driver_z = "", np.nan
        z = zframes[sid]
        if pd.notna(last):
            comps = [c for c in C.UTILIZATION_SIGNALS if c in z and pd.notna(z.at[last, c])]
            if comps:
                top = max(comps, key=lambda c: abs(z.at[last, c]))
                driver, driver_z = C.SIGNAL_SHORT.get(top, top), z.at[last, top]
        cfg = sites[sid]
        rows.append(dict(site=sid, label=cfg["label"], commodities=", ".join(cfg["commodities"]),
                         role=cfg["role"], last_month=last, util_idx=util,
                         util_3m=u3.iloc[-1] if len(u3) else np.nan, util_3m_chg=chg,
                         capacity_idx=cap, top_driver=driver, top_driver_z=driver_z,
                         n_util_signals=int(f["n_util_signals"].get(last, 0)) if pd.notna(last) else 0))
    return pd.DataFrame(rows)


def commodity_snapshot(util, cap, site_snap, sites):
    rows = []
    u3 = util.rolling(3, min_periods=2).mean()
    for c in util.columns:
        s = u3[c].dropna()
        members = site_snap[site_snap["site"].map(lambda x: c in sites[x]["commodities"])]
        movers = members.dropna(subset=["util_3m"]).assign(a=lambda d: d["util_3m"].abs())
        movers = movers.sort_values("a", ascending=False).head(2)
        rows.append(dict(
            commodity=c, last_month=s.index[-1] if len(s) else pd.NaT,
            util_3m=s.iloc[-1] if len(s) else np.nan,
            util_3m_chg=(s.iloc[-1] - s.iloc[-4]) if len(s) >= 4 else np.nan,
            capacity_idx=cap[c].dropna().iloc[-1] if c in cap and cap[c].notna().any() else np.nan,
            n_sites=len(members),
            leaders="; ".join(f"{sites[r.site]['label'].split(' (')[0]} {_sz(r.util_3m)}"
                              for r in movers.itertuples())))
    return pd.DataFrame(rows).sort_values("util_3m", key=lambda s: -s.abs(), na_position="last") \
        .reset_index(drop=True) if rows else pd.DataFrame()


def climate_snapshot(clim, regions):
    rows = []
    for rid, df in clim.items():
        d = df.dropna(subset=["spi3_like"])
        if d.empty:
            continue
        last = d.index[-1]
        rows.append(dict(region=rid, label=regions[rid]["label"],
                         commodities=", ".join(regions[rid]["commodities"]),
                         risk_side=regions[rid]["risk_side"], last_month=last,
                         precip_pct_normal=df.at[last, "precip_pct_normal"],
                         precip3_pct_normal=df.at[last, "precip3_pct_normal"],
                         spi3_like=df.at[last, "spi3_like"], t2m_anom_c=df.at[last, "t2m_anom_c"],
                         flag=df.at[last, "risk_flag"] or ""))
    return pd.DataFrame(rows)


def _describe_z(z):
    if pd.isna(z):
        return "no reading"
    a = abs(z)
    if a < 0.5:
        return "near its seasonal norm"
    word = "above" if z > 0 else "below"
    return f"{'well ' if a >= 1.5 else ''}{word} its seasonal norm"


def _sz(v):
    """Signed one-decimal format without a negative zero."""
    v = 0.0 if abs(v) < 0.05 else v
    return f"{v:+.1f}"


def _short(sites, sid):
    return sites[sid]["label"].split(" (")[0] if sites and sid in sites else sid.replace("_", " ")


def takeaways(comm_snap, site_snap, clim_snap, anomalies, ll_best, regions, sites=None, max_commodities=4):
    """Rule-based plain-English bullets for the report front page."""
    out = []
    if comm_snap is not None and not comm_snap.empty:
        for r in comm_snap.dropna(subset=["util_3m"]).head(max_commodities).itertuples():
            trend = ""
            if pd.notna(r.util_3m_chg) and abs(r.util_3m_chg) >= 0.25:
                trend = f", {'up' if r.util_3m_chg > 0 else 'down'} {abs(r.util_3m_chg):.1f} vs. three months earlier"
            lead = f" Largest site moves: {r.leaders}." if r.leaders else ""
            out.append(f"{r.commodity.replace('_', ' ').title()}: supply-activity index "
                       f"{_sz(r.util_3m)} (3-month average z) across {r.n_sites} site(s), "
                       f"{_describe_z(r.util_3m)}{trend}.{lead}")
    if clim_snap is not None and not clim_snap.empty:
        flagged = clim_snap[clim_snap["flag"] != ""]
        for r in flagged.itertuples():
            out.append(f"{r.label}: 3-month rainfall at {r.precip3_pct_normal:.0f}% of normal "
                       f"(SPI-3-like {_sz(r.spi3_like)}, {r.flag}) to {r.last_month:%b %Y}. "
                       f"Exposure: {r.commodities.replace('_', ' ')}. {regions[r.region]['mechanism']}")
        if flagged.empty:
            out.append("No climate or power-supply risk region is flagged in the latest month.")
    if anomalies is not None and not anomalies.empty:
        latest = anomalies["month"].max()
        top = anomalies[anomalies["month"] == latest].head(3)
        moves = "; ".join(f"{_short(sites, r.site)} {C.SIGNAL_SHORT.get(r.signal, r.signal)} {_sz(r.z)}"
                          for r in top.itertuples())
        n = (anomalies["month"] == latest).sum()
        out.append(f"{n} signal reading(s) moved 2+ standard deviations in {latest:%b %Y}. "
                   f"Largest: {moves}.")
    if site_snap is not None and not site_snap.empty:
        cap = site_snap.dropna(subset=["capacity_idx"]).sort_values("capacity_idx", ascending=False)
        if not cap.empty and cap.iloc[0]["capacity_idx"] >= 1.5:
            r = cap.iloc[0]
            out.append(f"Fastest footprint growth relative to its own history: {r['label']} "
                       f"(capacity index {_sz(r['capacity_idx'])}).")
    if ll_best is not None and not ll_best.empty:
        strong = ll_best[(ll_best["r"].abs() >= 0.3) & (ll_best["p_approx"] < 0.05)]
        if strong.empty:
            out.append("No satellite-to-price relationship clears |r| >= 0.3 at p < 0.05; "
                       "treat the signals as supply monitoring, not price timing.")
        else:
            r = strong.iloc[0]
            who = "signal leads price" if r["lag"] > 0 else "price leads signal" if r["lag"] < 0 else "same month"
            out.append(f"Strongest price link: {r['pair']} (r = {r['r']:+.2f} at lag {int(r['lag'])}, {who}; "
                       f"n = {int(r['n'])}). Best of 13 lags, so treat it as a hypothesis to test.")
    return out
