"""Pull engine: monthly feature collections, chunking, retries, incremental cache."""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from . import config as C
from . import ee_signals as E

LOG = logging.getLogger("metals_monitor")

_TRANSIENT = ("too many concurrent", "rate limit", "quota", "429", "503", "internal error",
              "deadline", "unavailable", "connection", "timed out waiting")
_HEAVY = ("computation timed out", "memory limit", "too many pixels", "too many input", "maxpixels")


def is_heavy(ex) -> bool:
    """Errors that a smaller request can fix (split the chunk)."""
    return any(k in str(ex).lower() for k in _HEAVY)


def _getinfo(obj, label, retries=5):
    delay = 5
    for attempt in range(retries):
        try:
            return obj.getInfo()
        except Exception as ex:  # ee.EEException, HTTP and socket errors
            msg = str(ex).lower()
            if is_heavy(ex) or attempt == retries - 1 or not any(k in msg for k in _TRANSIENT):
                raise
            LOG.warning("%s: transient error (%s) - retry in %ss", label, str(ex)[:80], delay)
            time.sleep(delay)
            delay = min(delay * 2, 120)


def n_months(a, b) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month)


def _pull_chunk(stat_fn, start_ts, n, label):
    ee = E.ee
    cs = start_ts.strftime("%Y-%m-%d")

    def per_month(i):
        s = ee.Date(cs).advance(ee.Number(i), "month")
        d = ee.Dictionary(stat_fn(s, s.advance(1, "month")))
        return ee.Feature(None, d.set("date", s.format("YYYY-MM-dd")))

    fc = ee.FeatureCollection(ee.List.sequence(0, n - 1).map(per_month))
    try:
        info = _getinfo(fc, f"{label} {cs}+{n}m")
        return [f["properties"] for f in info["features"]]
    except Exception as ex:
        if n > 1 and is_heavy(ex):
            h = n // 2
            LOG.info("%s: splitting %s (%d months) after: %s", label, cs, n, str(ex)[:100])
            return (_pull_chunk(stat_fn, start_ts, h, label)
                    + _pull_chunk(stat_fn, start_ts + pd.DateOffset(months=h), n - h, label))
        raise


def pull_monthly(stat_fn, start, end, chunk_months, label):
    rows, cur, end_ts = [], pd.Timestamp(start), pd.Timestamp(end)
    while cur < end_ts:
        nxt = min(cur + pd.DateOffset(months=chunk_months), end_ts)
        rows += _pull_chunk(stat_fn, cur, n_months(cur, nxt), label)
        cur = nxt
    df = pd.DataFrame(rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
    return df


def cached_pull(key, stat_fn, start, end, cache_dir, chunk_months=12, refresh=False, overlap=2):
    """Incremental cache. Re-pulls only missing months plus the last `overlap`
    cached months (late-arriving data, e.g. VIIRS and CHIRPS updates)."""
    path = cache_dir / f"{key}.csv"
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    old = None
    ranges = [(start_ts, end_ts)]
    if path.exists() and not refresh:
        old = pd.read_csv(path, parse_dates=["date"])
        if not old.empty:
            ranges = []
            if start_ts < old["date"].min():
                ranges.append((start_ts, old["date"].min()))
            tail = max(start_ts, old["date"].max() - pd.DateOffset(months=overlap - 1))
            if tail < end_ts:
                ranges.append((tail, end_ts))
    frames = [old] if old is not None else []
    for a, b in ranges:
        if a < b:
            LOG.info("pull %-34s %s -> %s", key, a.date(), b.date())
            frames.append(pull_monthly(stat_fn, a, b, chunk_months, key))
    frames = [f for f in frames if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame()
    df = (pd.concat(frames, ignore_index=True)
          .drop_duplicates("date", keep="last").sort_values("date"))
    df.to_csv(path, index=False)
    return df


def run_pulls(sites, regions, start, end, cache_dir, workers=4, chunk_months=12, refresh=False):
    """Pull every (site, indicator) and (region, climate) series into the cache."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    meta_path = cache_dir / "_meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}

    for sid, cfg in sites.items():  # Sentinel-1 orbit direction per site (cached)
        if "s1" in cfg["indicators"] and sid not in meta:
            meta[sid] = {"s1_pass": E.s1_dominant_pass(E.site_geom(cfg), "2020-01-01", end)}
            LOG.info("%s: Sentinel-1 orbit direction = %s", sid, meta[sid]["s1_pass"])
    meta_path.write_text(json.dumps(meta, indent=2))

    jobs = []
    for sid, cfg in sites.items():
        for ind in cfg["indicators"]:
            s = max(pd.Timestamp(start), pd.Timestamp(C.IND_MIN_START.get(ind, start)))
            jobs.append(("site", sid, ind, cfg, s))
    for rid, rcfg in regions.items():
        s = min(pd.Timestamp(start), pd.Timestamp(C.CLIMATE_HISTORY_START))
        jobs.append(("region", rid, "climate", rcfg, s))

    def work(job):
        kind, oid, ind, cfg, s = job
        key = f"{oid}__{ind}"
        fn = E.make_stat_fn(kind, ind, cfg, meta.get(oid, {}))
        cached_pull(key, fn, s, end, cache_dir, chunk_months, refresh)
        return key

    failures = []
    LOG.info("Running %d Earth Engine jobs with %d workers", len(jobs), workers)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(work, j): j for j in jobs}
        for f in as_completed(futs):
            j = futs[f]
            try:
                LOG.info("done  %s", f.result())
            except Exception as ex:
                failures.append((j[1], j[2], str(ex)[:200]))
                LOG.error("FAILED %s/%s: %s", j[1], j[2], str(ex)[:200])
    if failures:
        LOG.warning("%d job(s) failed; re-run to retry (the cache keeps completed work). "
                    "Try --chunk-months 6 if failures mention timeouts or memory.", len(failures))
    return failures
