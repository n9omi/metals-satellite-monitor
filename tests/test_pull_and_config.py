import numpy as np
import pandas as pd
import pytest

from metals_monitor import config as C
from metals_monitor import pull


@pytest.fixture
def fake_pull(monkeypatch):
    calls = []

    def fake(stat_fn, a, b, chunk, label):
        calls.append((pd.Timestamp(a), pd.Timestamp(b)))
        idx = pd.date_range(a, pd.Timestamp(b) - pd.DateOffset(months=1), freq="MS")
        return pd.DataFrame({"date": idx, "x": np.arange(len(idx), dtype=float)})

    monkeypatch.setattr(pull, "pull_monthly", fake)
    return calls


def test_cache_is_incremental(tmp_path, fake_pull):
    df = pull.cached_pull("k", None, "2020-01-01", "2021-01-01", tmp_path)
    assert len(df) == 12 and fake_pull == [(pd.Timestamp("2020-01-01"), pd.Timestamp("2021-01-01"))]
    fake_pull.clear()
    df = pull.cached_pull("k", None, "2020-01-01", "2021-04-01", tmp_path)
    # re-pulls the last two cached months plus the new ones
    assert fake_pull == [(pd.Timestamp("2020-11-01"), pd.Timestamp("2021-04-01"))]
    assert len(df) == 15 and df["date"].is_unique
    fake_pull.clear()
    df = pull.cached_pull("k", None, "2019-06-01", "2021-04-01", tmp_path)
    assert fake_pull[0] == (pd.Timestamp("2019-06-01"), pd.Timestamp("2020-01-01"))
    assert len(df) == 22


def test_refresh_pulls_everything(tmp_path, fake_pull):
    pull.cached_pull("k", None, "2020-01-01", "2021-01-01", tmp_path)
    fake_pull.clear()
    pull.cached_pull("k", None, "2020-01-01", "2021-01-01", tmp_path, refresh=True)
    assert fake_pull == [(pd.Timestamp("2020-01-01"), pd.Timestamp("2021-01-01"))]


def test_error_classification():
    assert pull.is_heavy(Exception("Computation timed out."))
    assert pull.is_heavy(Exception("User memory limit exceeded."))
    assert not pull.is_heavy(Exception("Too many concurrent aggregations."))
    assert pull.n_months(pd.Timestamp("2020-01-01"), pd.Timestamp("2021-04-01")) == 15


def test_config_loads_and_validates(tmp_path):
    sites, regions, tickers = C.load_config(C.Path(__file__).resolve().parents[1] / "config" / "sites.yaml")
    assert len(sites) >= 10 and len(regions) >= 3 and "copper" in tickers
    for cfg in sites.values():
        assert set(cfg["indicators"]) <= C.VALID_INDICATORS
    bad = tmp_path / "bad.yaml"
    bad.write_text("sites:\n  x: {lat: 99, lon: 0, buffer_km: 5, role: mine, commodities: [cu], indicators: [s9]}\n")
    with pytest.raises(SystemExit):
        C.load_config(bad)


def test_select_filters_regions_by_commodity():
    sites, regions, _ = C.load_config(C.Path(__file__).resolve().parents[1] / "config" / "sites.yaml")
    s, r = C.select(sites, regions, commodities=["aluminum"])
    assert set(s) == {"kamsar"} and "yunnan" in r and "pilbara" not in r
