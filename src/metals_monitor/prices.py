"""Monthly price loading: yfinance futures and/or a user CSV."""
from __future__ import annotations

import logging

import pandas as pd

LOG = logging.getLogger("metals_monitor")


def read_prices_csv(path):
    """Accepts long (date,commodity,price) or wide (date,<commodity>...) CSVs."""
    raw = pd.read_csv(path, parse_dates=["date"])
    if {"commodity", "price"} <= set(raw.columns):
        wide = raw.pivot_table(index="date", columns="commodity", values="price")
    else:
        wide = raw.set_index("date")
    wide.columns.name = None
    return wide.apply(pd.to_numeric, errors="coerce").resample("MS").last()


def load_prices(commodities, start, end, tickers, csv_path=None, use_yf=True):
    """Month-start-labelled, month-end-close prices for each commodity."""
    series = {}
    if csv_path:
        wide = read_prices_csv(csv_path)
        series.update({c: wide[c] for c in wide.columns})
    if use_yf:
        try:
            import yfinance as yf
        except ImportError:
            LOG.warning("yfinance not installed - skipping prices (pip install yfinance)")
            yf = None
        for com, tk in tickers.items():
            if yf is None or com not in commodities or com in series:
                continue
            try:
                d = yf.download(tk, start=str(pd.Timestamp(start).date()), end=str(pd.Timestamp(end).date()),
                                interval="1d", auto_adjust=True, progress=False)
                close = d["Close"]
                if isinstance(close, pd.DataFrame):
                    close = close.iloc[:, 0]
                close.index = pd.to_datetime(close.index)
                if close.index.tz is not None:
                    close.index = close.index.tz_localize(None)
                series[com] = close.resample("MS").last()
                LOG.info("prices: %s via %s (%d months)", com, tk, series[com].notna().sum())
            except Exception as ex:
                LOG.warning("prices: %s (%s) failed: %s", com, tk, ex)
    return pd.DataFrame(series)
