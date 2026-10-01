"""Synthetic demo data so the full pipeline (and the PDF) runs without Earth Engine.

The series are random, with a few built-in stories to show what the signals look
like (a mine shutdown, an industrial park ramp-up, a hydro-region drought).
Nothing here is an observation. Every demo output is labelled SYNTHETIC.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from . import config as C

LOG = logging.getLogger("metals_monitor")


def _months(start, end):
    return pd.date_range(start, pd.Timestamp(end) - pd.DateOffset(months=1), freq="MS")


def _seas(idx, amp):
    return amp * np.sin(2 * np.pi * (np.asarray(idx.month) - 1) / 12)


def make_demo_data(sites, regions, paths, start, end, seed=7):
    rng = np.random.default_rng(seed)
    paths.cache.mkdir(parents=True, exist_ok=True)
    for sid, cfg in sites.items():
        shut_date = pd.Timestamp("2023-12-01") if sid.startswith("cobre_panama") else None
        ramp = sid in ("morowali_imip", "weda_bay_iwip", "kamoa_kakula")
        for ind in cfg["indicators"]:
            idx = _months(max(pd.Timestamp(start), pd.Timestamp(C.IND_MIN_START[ind])), end)
            n = len(idx)
            trend = np.linspace(0, 1, n)
            act = np.ones(n)
            if shut_date is not None:
                act = np.where(idx >= shut_date, 0.2, 1.0)
            if ramp:
                act = act * (0.6 + 0.8 * trend)
            act = act * (1 + rng.normal(0, 0.05, n))
            if ind == "s2":
                d = dict(ndvi=0.1 + _seas(idx, .05) + rng.normal(0, .01, n),
                         bsi=0.1 + rng.normal(0, .01, n), mndwi=-0.2 + rng.normal(0, .02, n),
                         vis=0.2 + rng.normal(0, .01, n),
                         water_frac=0.05 + 0.03 * trend + rng.normal(0, .005, n),
                         bare_frac=0.6 + (0.1 if ramp else 0.03) * trend + rng.normal(0, .01, n),
                         change_frac_yoy=0.1 * act + rng.normal(0, .01, n),
                         valid_frac=rng.uniform(0.1, 1, n), n_images=rng.integers(0, 12, n))
            elif ind == "s1":
                d = dict(vv_db=-8 + rng.normal(0, .3, n), vh_db=-15 + rng.normal(0, .3, n),
                         water_frac=0.02 + rng.normal(0, .003, n),
                         change_frac_yoy=0.08 * act + rng.normal(0, .01, n), n_images=rng.integers(0, 6, n))
            elif ind == "s5p":
                d = dict(no2=60 * act + _seas(idx, 10) + rng.normal(0, 5, n), no2_bg=20 + rng.normal(0, 3, n),
                         no2_n=rng.integers(0, 30, n), so2=200 * act + rng.normal(0, 40, n),
                         so2_bg=10 + rng.normal(0, 10, n), so2_n=rng.integers(0, 30, n))
            elif ind == "viirs":
                d = dict(rad_sum=5000 * act + rng.normal(0, 200, n), rad_mean=5 * act + rng.normal(0, .3, n),
                         cf_cvg=rng.integers(0, 10, n).astype(float), n_images=1)
            elif ind == "firms":
                d = dict(hot_pixel_days=rng.poisson(10 * np.clip(act, 0, None)), n_days=rng.integers(18, 32, n))
            elif ind == "dw":
                d = dict(footprint_frac=0.3 + (0.2 if ramp else 0.05) * trend + rng.normal(0, .01, n),
                         trees_mean=0.5 - (0.2 if ramp else 0.03) * trend + rng.normal(0, .01, n),
                         n_images=rng.integers(0, 8, n))
            df = pd.DataFrame(d, index=idx).reset_index(names="date")
            for c in df.columns[1:]:  # mimic Earth Engine nulls
                df.loc[rng.random(n) < 0.05, c] = np.nan
            df.to_csv(paths.cache / f"{sid}__{ind}.csv", index=False)
    for rid in regions:
        idx = _months(C.CLIMATE_HISTORY_START, end)
        n = len(idx)
        p = np.clip(100 + _seas(idx, 60) + rng.normal(0, 30, n), 0, None)
        if rid == "yunnan":
            p[-5:] *= 0.35  # a built-in drought to exercise the risk flag
        df = pd.DataFrame(dict(precip_mm=p, t2m_c=18 + _seas(idx, 6) + rng.normal(0, .5, n),
                               n_pentads=6, n_era5=1), index=idx).reset_index(names="date")
        df.loc[df.index[-1], "n_pentads"] = 3  # partial latest month, as in real CHIRPS
        df.to_csv(paths.cache / f"{rid}__climate.csv", index=False)

    # Prices (random walks) and quarterly production for one site
    days = pd.date_range(start, pd.Timestamp(end) - pd.Timedelta(days=1), freq="D")
    rows = []
    for com, s0 in (("copper", 4.0), ("aluminum", 2400), ("nickel", 16000), ("iron_ore", 105),
                    ("palladium", 1000)):
        rows.append(pd.DataFrame(dict(date=days, commodity=com,
                                      price=s0 * np.exp(np.cumsum(rng.normal(0, .01, len(days)))))))
    prices_csv = paths.base / "data" / "demo_prices.csv"
    pd.concat(rows).to_csv(prices_csv, index=False)
    q = pd.date_range(pd.Timestamp(start) + pd.offsets.QuarterEnd(0), pd.Timestamp(end), freq="QE")
    prod_csv = paths.base / "data" / "demo_production.csv"
    pd.DataFrame(dict(site="escondida", date=q, value=250 + rng.normal(0, 15, len(q)))).to_csv(prod_csv, index=False)
    LOG.info("Synthetic demo data written to %s", paths.base)
    return prices_csv, prod_csv
