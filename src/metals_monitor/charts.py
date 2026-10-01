"""Static figures (matplotlib). Colour roles follow a validated categorical
palette (slot 1 blue, slot 2 orange) and a blue <-> red diverging scale with a
neutral grey midpoint: blue = above normal / wetter, red = below normal / drier."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C

INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, AXIS, SURFACE = "#e1e0d9", "#c3c2b7", "#ffffff"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
POS, NEG = "#2a78d6", "#d0453f"
DIVERGING = ["#a8302f", "#e34948", "#f2b3b2", "#f0efec", "#9ec5f4", "#3987e5", "#184f95"]


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 8.5,
        "axes.edgecolor": AXIS, "axes.linewidth": 0.6, "axes.labelcolor": INK2,
        "axes.titlesize": 9, "axes.titlecolor": INK, "axes.titlelocation": "left",
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "grid.linestyle": "-",
        "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelcolor": INK2, "ytick.labelcolor": INK2,
        "legend.frameon": False, "legend.fontsize": 8, "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE, "savefig.dpi": 200,
    })
    return plt


def _diverging_cmap():
    from matplotlib.colors import LinearSegmentedColormap
    return LinearSegmentedColormap.from_list("mm_div", DIVERGING)


def plot_site(sid, cfg, df, idx, path):
    plt = _plt()
    cols = [c for c in C.KEY_SIGNALS if c in df and df[c].notna().sum() >= 3]
    n = len(cols) + 1
    fig, axes = plt.subplots(n, 1, figsize=(7.2, 1.25 * n + 0.7), sharex=True)
    axes = np.atleast_1d(axes)
    for ax, c in zip(axes, cols):
        s = df[c]
        ax.plot(s.index, s.values, lw=0.7, alpha=0.35, color=SERIES[0])
        ax.plot(s.index, s.rolling(3, min_periods=2).mean().values, lw=1.5, color=SERIES[0])
        ax.set_title(C.KEY_SIGNALS[c], fontsize=8, color=INK2)
    ax = axes[-1]
    ax.axhline(0, color=AXIS, lw=0.8)
    ax.plot(idx.index, idx["utilization_idx_3m"].values, color=SERIES[0], lw=1.6, label="Utilization (3m)")
    ax.plot(idx.index, idx["capacity_idx"].values, color=SERIES[1], lw=1.6, label="Capacity")
    ax.set_title("Fundamental indices (point-in-time z)", fontsize=8, color=INK2)
    ax.legend(loc="upper left", ncol=2)
    fig.suptitle(f"{cfg['label']}  -  {', '.join(cfg['commodities'])}  -  {cfg['role'].replace('_', ' ')}",
                 fontsize=10, color=INK, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _year_locator(step):
    import matplotlib.dates as mdates
    return mdates.YearLocator(step)


def plot_commodities(util, cap, path, months=None):
    plt = _plt()
    u3 = util.rolling(3, min_periods=2).mean()
    if months:
        u3, cap = u3.iloc[-months:], cap.iloc[-months:]
    cols = list(util.columns)
    ncol = 4 if len(cols) > 4 else max(len(cols), 1)
    nrow = int(np.ceil(len(cols) / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(7.4, 1.55 * nrow + 0.65), sharex=True, sharey=True)
    axes = np.atleast_1d(axes).ravel()
    for ax, c in zip(axes, cols):
        ax.axhline(0, color=AXIS, lw=0.8)
        ax.plot(u3.index, u3[c].clip(-5, 5).values, color=SERIES[0], lw=1.5, label="Utilization (3m)")
        if c in cap:
            ax.plot(cap.index, cap[c].clip(-5, 5).values, color=SERIES[1], lw=1.5, label="Capacity")
        last = u3[c].dropna()
        if len(last):
            ax.annotate(f"{last.iloc[-1]:+.1f}", (last.index[-1], np.clip(last.iloc[-1], -5, 5)),
                        xytext=(3, 0), textcoords="offset points", fontsize=7.5, color=INK2, va="center")
        ax.set_title(c.replace("_", " ").title(), fontsize=8.5)
        ax.set_ylim(-5.2, 5.2)
        ax.tick_params(axis="x", labelsize=7, rotation=0)
        ax.xaxis.set_major_locator(_year_locator(2))
    for ax in axes[len(cols):]:
        ax.set_visible(False)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper right", ncol=2, bbox_to_anchor=(0.99, 1.0))
    fig.suptitle("Commodity supply-activity indices (z)", fontsize=10, x=0.01, ha="left", color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(path)
    plt.close(fig)


def plot_climate(clim, regions, path, months=48):
    plt = _plt()
    n = len(clim)
    fig, axes = plt.subplots(n, 1, figsize=(7.2, 1.25 * n + 0.6), sharex=True)
    axes = np.atleast_1d(axes)
    for ax, (rid, df) in zip(axes, clim.items()):
        d = df["spi3_like"].iloc[-months:]
        ax.bar(d.index, d.values, width=24, color=[POS if v >= 0 else NEG for v in d.fillna(0)])
        for y in (-1, 1):
            ax.axhline(y, color=MUTED, lw=0.5)
        ax.axhline(0, color=AXIS, lw=0.8)
        risk = "drought" if regions[rid]["risk_side"] == "dry" else "extreme rain"
        ax.set_title(f"{regions[rid]['label']}  (supply risk: {risk})", fontsize=8)
        lim = max(3.2, float(np.nanmax(np.abs(d.values))) + 0.3) if d.notna().any() else 3.2
        ax.set_ylim(-lim, lim)
    fig.suptitle("3-month rainfall anomaly, SPI-3-like (blue wetter, red drier; lines at +/-1)",
                 fontsize=9.5, x=0.01, ha="left", color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(path)
    plt.close(fig)


def plot_site_heatmap(idx_frames, sites, path, months=36):
    plt = _plt()
    order = sorted(idx_frames, key=lambda s: (sites[s]["commodities"][0], s))
    M = pd.DataFrame({s: idx_frames[s]["utilization_idx_3m"] for s in order}).iloc[-months:]
    fig, ax = plt.subplots(figsize=(7.4, 0.2 * len(order) + 1.15))
    x = np.arange(M.shape[0] + 1)
    y = np.arange(M.shape[1] + 1)
    mesh = ax.pcolormesh(x, y, M.T.values.astype(float), cmap=_diverging_cmap(), vmin=-3, vmax=3,
                         edgecolors=SURFACE, linewidth=0.8)
    ax.set_yticks(y[:-1] + 0.5)
    ax.set_yticklabels([f"{sites[s]['label'].split(' (')[0]}  [{sites[s]['commodities'][0].replace('_', ' ')}]"
                        for s in order], fontsize=7)
    ticks = [i for i, d in enumerate(M.index) if d.month in (1, 7)]
    ax.set_xticks([t + 0.5 for t in ticks])
    ax.set_xticklabels([M.index[t].strftime("%b %y") for t in ticks], fontsize=7)
    ax.invert_yaxis()
    ax.grid(False)
    for sp in ax.spines.values():
        sp.set_visible(False)
    cb = fig.colorbar(mesh, ax=ax, fraction=0.03, pad=0.01)
    cb.set_label("utilization index, 3m (z)", color=INK2, fontsize=7.5)
    cb.outline.set_visible(False)
    ax.set_title("Site utilization index, last %d months (grey = no data / normal)" % months, fontsize=9)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def plot_lead_lag(ll, path, top=6):
    """Bars of r by lag for the `top` pairs with the largest best-lag |r|."""
    plt = _plt()
    lv = ll[ll["signal_transform"] == "level"]
    if lv.empty:
        return False
    best = lv.assign(a=lv["r"].abs()).groupby("pair")["a"].max().sort_values(ascending=False)
    pairs = list(best.index[:top])
    ncol = 3 if len(pairs) > 2 else len(pairs)
    nrow = int(np.ceil(len(pairs) / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(7.4, 1.5 * nrow + 0.8), sharex=True, sharey=True)
    axes = np.atleast_1d(axes).ravel()
    for ax, p in zip(axes, pairs):
        d = lv[lv["pair"] == p]
        ax.bar(d["lag"], d["r"], color=SERIES[0], width=0.7)
        ax.axhline(0, color=AXIS, lw=0.8)
        for y in (-0.3, 0.3):
            ax.axhline(y, color=MUTED, lw=0.5)
        ax.set_title(p.replace("_", " ").replace(" -> ", "\n-> "), fontsize=7.2)
        ax.set_xticks(range(-6, 7, 2))
        ax.set_ylim(-0.7, 0.7)
    for ax in axes[len(pairs):]:
        ax.set_visible(False)
    fig.supxlabel("lag k in months (k > 0: satellite signal leads price)", fontsize=8, color=INK2)
    fig.suptitle(f"Correlation of signal level with monthly log return, top {len(pairs)} pairs (lines at +/-0.3)",
                 fontsize=9.5, x=0.01, ha="left", color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(path)
    plt.close(fig)
    return True
