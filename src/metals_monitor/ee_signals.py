"""Earth Engine layer: initialisation, geometries and monthly signal reducers.

Every `*_stat_fn` returns a function `fn(start: ee.Date, end: ee.Date) -> ee.Dictionary`
that is mapped server-side over a list of months by `pull.py`.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request

import pandas as pd

from . import config as C

try:  # Earth Engine is only needed for pulls; analytics run without it.
    import ee
except ImportError:  # pragma: no cover
    ee = None

LOG = logging.getLogger("metals_monitor")


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------
def ee_init(project: str | None = None):
    """Initialise Earth Engine.

    Uses a service account when GEE_SERVICE_ACCOUNT_KEY is set (path to a JSON
    key, or the JSON itself - handy in GitHub Actions); otherwise the user's
    stored credentials, prompting for authentication on first use.
    """
    if ee is None:
        raise SystemExit("earthengine-api is not installed: pip install earthengine-api")
    project = project or os.environ.get("EE_PROJECT")
    if not project:
        raise SystemExit("No Earth Engine project. Set EE_PROJECT in .env or pass --project.")
    key = os.environ.get("GEE_SERVICE_ACCOUNT_KEY")
    if key:
        info = json.loads(key) if key.strip().startswith("{") else json.loads(open(key).read())
        creds = ee.ServiceAccountCredentials(info["client_email"], key_data=json.dumps(info))
        ee.Initialize(creds, project=project)
        LOG.info("Earth Engine initialised with service account %s", info["client_email"])
        return
    try:
        ee.Initialize(project=project)
    except Exception:  # first run on this machine
        ee.Authenticate()
        ee.Initialize(project=project)
    LOG.info("Earth Engine initialised (project=%s)", project)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def site_point(cfg):
    return ee.Geometry.Point([cfg["lon"], cfg["lat"]])


def site_geom(cfg, radius_km=None):
    return site_point(cfg).buffer((radius_km or cfg["buffer_km"]) * 1000, maxError=50)


def ring_geom(cfg):
    """Background annulus for S5P enhancement, well outside the plume core."""
    inner = max(cfg["buffer_km"] * 3, 40) * 1000
    outer = inner + 50_000
    pt = site_point(cfg)
    return pt.buffer(outer, maxError=200).difference(pt.buffer(inner, maxError=200), maxError=200)


def region_geom(rcfg):
    return ee.Geometry.Rectangle(rcfg["bbox"])


def _empty(bands):
    """Fully masked image with the given bands. Merging it into a collection
    guarantees composites have bands even when a month has no scenes."""
    return (ee.Image.constant([0] * len(bands)).rename(bands).toFloat()
            .updateMask(ee.Image.constant(0)))


def _reduce(img, geom, scale, reducer=None, best_effort=True):
    return img.reduceRegion(
        reducer=reducer or ee.Reducer.mean(), geometry=geom, scale=scale,
        maxPixels=1e10, bestEffort=best_effort, tileScale=4)


# ---------------------------------------------------------------------------
# Sentinel-2 (surface reflectance + Cloud Score+)
# ---------------------------------------------------------------------------
S2_BANDS = ["B2", "B3", "B4", "B8", "B11", "B12"]


def _s2_clear(geom, s, e):
    def prep(img):
        return (img.select(S2_BANDS).divide(10000)
                .updateMask(img.select("cs_cdf").gte(C.CS_CLEAR)))
    return (ee.ImageCollection(C.S2_ID).filterBounds(geom).filterDate(s, e)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 90))
            .linkCollection(ee.ImageCollection(C.CSP_ID), ["cs_cdf"])
            .map(prep))


def _s2_composite(geom, s, e):
    col = _s2_clear(geom, s, e)
    return col.merge(ee.ImageCollection([_empty(S2_BANDS)])).median(), col.size()


def _s2_indices(c):
    ndvi = c.normalizedDifference(["B8", "B4"]).rename("ndvi")
    mndwi = c.normalizedDifference(["B3", "B11"]).rename("mndwi")
    bsi = c.expression("((S + R) - (N + B)) / ((S + R) + (N + B))", {
        "S": c.select("B11"), "R": c.select("B4"), "N": c.select("B8"), "B": c.select("B2"),
    }).rename("bsi")
    vis = c.select(["B2", "B3", "B4"]).reduce(ee.Reducer.mean()).rename("vis")
    return ee.Image.cat(ndvi, mndwi, bsi, vis)


def s2_stat_fn(geom, scale):
    def fn(s, e):
        comp, n = _s2_composite(geom, s, e)
        prev, _ = _s2_composite(geom, s.advance(-1, "year"), e.advance(-1, "year"))
        idx, pidx = _s2_indices(comp), _s2_indices(prev)
        water = idx.select("mndwi").gt(C.MNDWI_WATER).rename("water_frac")
        bare = (idx.select("bsi").gt(C.BSI_BARE).And(idx.select("ndvi").lt(C.NDVI_BARE))
                .rename("bare_frac"))
        chg = (idx.select("bsi").subtract(pidx.select("bsi")).abs().gt(C.BSI_CHANGE)
               .rename("change_frac_yoy"))
        valid = (ee.Image.constant(1).updateMask(comp.select("B4").mask())
                 .unmask(0).rename("valid_frac"))
        img = ee.Image.cat(idx, water, bare, chg, valid).toFloat()
        return _reduce(img, geom, scale).set("n_images", n)
    return fn


# ---------------------------------------------------------------------------
# Sentinel-1 SAR
# ---------------------------------------------------------------------------
def _s1(geom, s, e, orbit_pass):
    return (ee.ImageCollection(C.S1_ID).filterBounds(geom).filterDate(s, e)
            .filter(ee.Filter.eq("instrumentMode", "IW"))
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
            .filter(ee.Filter.eq("orbitProperties_pass", orbit_pass))
            .select(["VV", "VH"]))


def s1_dominant_pass(geom, start, end):
    base = (ee.ImageCollection(C.S1_ID).filterBounds(geom).filterDate(start, end)
            .filter(ee.Filter.eq("instrumentMode", "IW"))
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV")))
    counts = ee.Dictionary({
        p: base.filter(ee.Filter.eq("orbitProperties_pass", p)).size()
        for p in ("ASCENDING", "DESCENDING")
    }).getInfo()
    return max(counts, key=counts.get)


def s1_stat_fn(geom, scale, orbit_pass):
    def fn(s, e):
        col = _s1(geom, s, e, orbit_pass)
        empty = ee.ImageCollection([_empty(["VV", "VH"])])
        comp = col.merge(empty).median()
        prev = _s1(geom, s.advance(-1, "year"), e.advance(-1, "year"), orbit_pass).merge(empty).median()
        water = comp.select("VV").lt(C.SAR_WATER_DB).rename("water_frac")
        chg = (comp.select("VV").subtract(prev.select("VV")).abs().gt(C.SAR_CHANGE_DB)
               .rename("change_frac_yoy"))
        img = ee.Image.cat(comp.rename(["vv_db", "vh_db"]), water, chg).toFloat()
        return _reduce(img, geom, scale).set("n_images", col.size())
    return fn


# ---------------------------------------------------------------------------
# Sentinel-5P NO2 / SO2 (ROI minus background ring)
# ---------------------------------------------------------------------------
def s5p_stat_fn(geom, ring):
    def fn(s, e):
        out = ee.Dictionary({})
        for gas, (cid, band) in C.S5P_IDS.items():
            col = ee.ImageCollection(cid).filterDate(s, e).filterBounds(geom).select(band)
            if gas == "so2":  # catalog guidance: drop only outliers below -0.001 mol/m2
                col = col.map(lambda i: i.updateMask(i.gt(-0.001)))
            m = col.merge(ee.ImageCollection([_empty([band])])).mean().multiply(1e6)  # umol/m2
            a = _reduce(m.rename(gas), geom, C.S5P_SCALE)
            b = _reduce(m.rename(gas + "_bg"), ring, C.S5P_SCALE)
            out = out.combine(a).combine(b).set(gas + "_n", col.size())
        return out
    return fn


# ---------------------------------------------------------------------------
# VIIRS night lights, FIRMS hotspots, Dynamic World, climate
# ---------------------------------------------------------------------------
def viirs_stat_fn(geom):
    def fn(s, e):
        col = (ee.ImageCollection(C.VIIRS_ID).filterDate(s, e)
               .map(lambda i: i.select(["avg_rad", "cf_cvg"]).toFloat()))
        img = col.merge(ee.ImageCollection([_empty(["avg_rad", "cf_cvg"])])).mean()
        tot = _reduce(img.select("avg_rad").max(0).rename("rad_sum"), geom, C.VIIRS_SCALE,
                      ee.Reducer.sum(), best_effort=False)
        avg = _reduce(img.rename(["rad_mean", "cf_cvg"]), geom, C.VIIRS_SCALE)
        return tot.combine(avg).set("n_images", col.size())
    return fn


def firms_stat_fn(geom):
    def fn(s, e):
        col = ee.ImageCollection(C.FIRMS_ID).filterDate(s, e)
        hot = col.map(lambda i: i.select("confidence").gte(C.FIRMS_MIN_CONF).unmask(0)
                      .rename("hot_pixel_days").toFloat())
        zero = ee.ImageCollection([ee.Image.constant(0).rename("hot_pixel_days").toFloat()])
        tot = hot.merge(zero).sum()
        return (_reduce(tot, geom, C.FIRMS_SCALE, ee.Reducer.sum(), best_effort=False)
                .set("n_days", col.size()))
    return fn


def dw_stat_fn(geom, scale):
    bands = ["bare", "built", "trees"]

    def fn(s, e):
        col = ee.ImageCollection(C.DW_ID).filterBounds(geom).filterDate(s, e).select(bands)
        m = col.merge(ee.ImageCollection([_empty(bands)])).mean()
        fp = m.select("bare").add(m.select("built")).gt(0.5).rename("footprint_frac")
        img = ee.Image.cat(fp, m.select("trees").rename("trees_mean")).toFloat()
        return _reduce(img, geom, scale).set("n_images", col.size())
    return fn


def climate_stat_fn(geom):
    def fn(s, e):
        p = ee.ImageCollection(C.CHIRPS_ID).filterDate(s, e).select("precipitation")
        psum = p.merge(ee.ImageCollection([_empty(["precipitation"])])).sum()
        t = ee.ImageCollection(C.ERA5L_ID).filterDate(s, e).select("temperature_2m")
        tm = t.merge(ee.ImageCollection([_empty(["temperature_2m"])])).mean().subtract(273.15)
        a = _reduce(psum.rename("precip_mm"), geom, C.CHIRPS_SCALE)
        b = _reduce(tm.rename("t2m_c"), geom, C.ERA5_SCALE)
        return a.combine(b).set("n_pentads", p.size()).set("n_era5", t.size())
    return fn


def scale_for(cfg):
    return cfg.get("scale", 30 if cfg["buffer_km"] <= 12 else 60)


def make_stat_fn(kind, key, cfg, meta):
    if kind == "region":
        return climate_stat_fn(region_geom(cfg))
    geom, scale = site_geom(cfg), scale_for(cfg)
    if key == "s2":
        return s2_stat_fn(geom, scale)
    if key == "s1":
        return s1_stat_fn(geom, scale, meta["s1_pass"])
    if key == "s5p":
        r = cfg.get("s5p_radius_km", max(cfg["buffer_km"], 15))
        return s5p_stat_fn(site_geom(cfg, r), ring_geom(cfg))
    if key == "viirs":
        return viirs_stat_fn(geom)
    if key == "firms":
        return firms_stat_fn(geom)
    if key == "dw":
        return dw_stat_fn(geom, scale)
    raise ValueError(f"unknown indicator {key}")


# ---------------------------------------------------------------------------
# ROI sanity check: thumbnails + GeoJSON
# ---------------------------------------------------------------------------
def check_sites(sites, regions, out_dir, end):
    """Save a 12-month clear Sentinel-2 composite per site with the ROI outlined
    in red, plus rois.geojson (sites + regions) for QGIS."""
    out_dir.mkdir(parents=True, exist_ok=True)
    feats = []
    e = ee.Date(pd.Timestamp(end).strftime("%Y-%m-%d"))
    s = e.advance(-12, "month")
    for sid, cfg in sites.items():
        geom = site_geom(cfg)
        view = site_point(cfg).buffer(cfg["buffer_km"] * 1000 * 1.6).bounds()
        comp, _ = _s2_composite(view, s, e)
        rgb = comp.visualize(bands=["B4", "B3", "B2"], min=0, max=0.3)
        outline = (ee.Image().byte().paint(ee.FeatureCollection([ee.Feature(geom)]), 1, 2)
                   .visualize(palette=["ff0000"]))
        url = rgb.blend(outline).getThumbURL({"region": view, "dimensions": 768, "format": "png"})
        path = out_dir / f"{sid}.png"
        try:
            urllib.request.urlretrieve(url, path)
            LOG.info("%-20s -> %s", sid, path)
        except Exception as ex:
            LOG.warning("%s: thumbnail download failed (%s); URL: %s", sid, ex, url)
        props = {k: cfg[k] for k in ("label", "role", "buffer_km", "country") if k in cfg}
        feats.append({"type": "Feature", "geometry": geom.getInfo(),
                      "properties": {"id": sid, "kind": "site", **props,
                                     "commodities": ",".join(cfg["commodities"])}})
    for rid, rcfg in regions.items():
        w, so, ea, n = rcfg["bbox"]
        feats.append({"type": "Feature",
                      "geometry": {"type": "Polygon",
                                   "coordinates": [[[w, so], [ea, so], [ea, n], [w, n], [w, so]]]},
                      "properties": {"id": rid, "kind": "region", "label": rcfg["label"],
                                     "commodities": ",".join(rcfg["commodities"])}})
    (out_dir / "rois.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    LOG.info("Wrote %s. Open the PNGs and adjust lat/lon/buffer_km where the red circle misses.",
             out_dir / "rois.geojson")
