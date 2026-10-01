import numpy as np
import pandas as pd

from metals_monitor import analytics as A


def _series(n=96, seed=1):
    idx = pd.date_range("2015-01-01", periods=n, freq="MS")
    return pd.Series(np.random.default_rng(seed).normal(size=n), index=idx)


def test_trailing_z_has_no_look_ahead():
    x = _series()
    z1 = A.trailing_seasonal_z(x)
    x2 = x.copy()
    x2.iloc[-1] = 50.0
    z2 = A.trailing_seasonal_z(x2)
    pd.testing.assert_series_equal(z1.iloc[:-1], z2.iloc[:-1])
    assert z2.iloc[-1] > 5


def test_trailing_z_needs_history():
    z = A.trailing_seasonal_z(_series(n=12))
    assert z.isna().all()


def test_qc_blanks_cloudy_s2_and_derives_s5p_enhancement():
    idx = pd.date_range("2020-01-01", periods=3, freq="MS")
    df = pd.DataFrame({"s2_ndvi": [0.1, 0.2, 0.3], "s2_valid_frac": [0.9, 0.1, 0.5],
                       "s5p_no2": [50.0, 60.0, 70.0], "s5p_no2_bg": [20.0, 20.0, 20.0],
                       "s5p_no2_n": [10, 0, 5],
                       "viirs_rad_sum": [100.0, 200.0, 300.0], "viirs_cf_cvg": [3.0, np.nan, 0.0]},
                      index=idx)
    q = A.qc_site(df)
    assert np.isnan(q.loc[idx[1], "s2_ndvi"]) and q.loc[idx[0], "s2_ndvi"] == 0.1
    assert q.loc[idx[0], "s5p_no2_enh"] == 30.0 and np.isnan(q.loc[idx[1], "s5p_no2_enh"])
    assert q["viirs_rad_sum"].notna().tolist() == [True, False, False]
    assert "s2_valid_frac" not in A.signal_cols(q) and "s5p_no2_enh" in A.signal_cols(q)


def test_site_indices_sign_and_clip():
    idx = pd.date_range("2018-01-01", periods=60, freq="MS")
    rng = np.random.default_rng(3)
    df = pd.DataFrame({"viirs_rad_sum": rng.normal(100, 5, 60)}, index=idx)
    df.iloc[-1, 0] = 1e6  # extreme spike
    z = A.site_zscores(df)
    out = A.site_indices(df, z, "mine")
    assert out["utilization_idx"].iloc[-1] == 5  # clipped
    assert out["n_util_signals"].iloc[-1] == 1


def test_commodity_index_weights():
    idx = pd.date_range("2020-01-01", periods=2, freq="MS")
    frames = {"a": pd.DataFrame({"utilization_idx": [1.0, 1.0]}, index=idx),
              "b": pd.DataFrame({"utilization_idx": [-1.0, np.nan]}, index=idx)}
    sites = {"a": {"commodities": ["copper"], "weight": 3.0}, "b": {"commodities": ["copper"], "weight": 1.0}}
    ci = A.commodity_indices(frames, sites, "utilization_idx")
    assert ci["copper"].tolist() == [0.5, 1.0]


def test_climate_flags_follow_risk_side():
    idx = pd.date_range("2001-01-01", "2024-12-01", freq="MS")
    rng = np.random.default_rng(5)
    p = pd.Series(100 + rng.normal(0, 10, len(idx)), index=idx)
    p.iloc[-3:] = 5.0  # drought
    df = pd.DataFrame({"precip_mm": p, "t2m_c": 20.0, "n_pentads": 6, "n_era5": 1})
    dry = A.climate_metrics(df, {"risk_side": "dry"})
    wet = A.climate_metrics(df, {"risk_side": "wet"})
    assert "DRY" in dry["risk_flag"].iloc[-1]
    assert wet["risk_flag"].iloc[-1] == ""


def test_lead_lag_detects_planted_lead():
    idx = pd.date_range("2015-01-01", periods=120, freq="MS")
    rng = np.random.default_rng(11)
    sig = pd.Series(rng.normal(size=120), index=idx)
    ret = 0.05 * sig.shift(2).fillna(0) + rng.normal(0, 0.01, 120)  # signal leads by 2
    price = pd.Series(100 * np.exp(ret.cumsum()), index=idx)
    ll = A.lead_lag(sig, price)
    best = ll[ll["signal_transform"] == "level"].sort_values("r", key=abs, ascending=False).iloc[0]
    assert best["lag"] == 2 and best["r"] > 0.8 and best["p_approx"] < 0.001


def test_long_wide_roundtrip():
    idx = pd.date_range("2020-01-01", periods=4, freq="MS")
    frames = {"s": pd.DataFrame({"x": [1.0, np.nan, 3.0, 4.0], "y": [0.1, 0.2, 0.3, 0.4]}, index=idx)}
    back = A.long_to_wide(A.wide_to_long(frames), idx)
    pd.testing.assert_frame_equal(back["s"][["x", "y"]], frames["s"], check_freq=False, check_names=False)
