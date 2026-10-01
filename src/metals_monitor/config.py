"""Constants, paths and site/region configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Time windows
# ---------------------------------------------------------------------------
DEFAULT_START = "2019-01-01"
CLIMATE_HISTORY_START = "2001-01-01"
CLIMATE_BASELINE = (2001, 2020)

# ---------------------------------------------------------------------------
# Earth Engine collections (checked against the EE data catalog, Oct 2026)
# ---------------------------------------------------------------------------
S2_ID = "COPERNICUS/S2_SR_HARMONIZED"
CSP_ID = "GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED"
S1_ID = "COPERNICUS/S1_GRD"
S5P_IDS = {
    "no2": ("COPERNICUS/S5P/OFFL/L3_NO2", "tropospheric_NO2_column_number_density"),
    "so2": ("COPERNICUS/S5P/OFFL/L3_SO2", "SO2_column_number_density"),
}
VIIRS_ID = "NOAA/VIIRS/DNB/MONTHLY_V1/VCMSLCFG"
FIRMS_ID = "FIRMS"
DW_ID = "GOOGLE/DYNAMICWORLD/V1"
CHIRPS_ID = "UCSB-CHG/CHIRPS/PENTAD"
ERA5L_ID = "ECMWF/ERA5_LAND/MONTHLY_AGGR"

# Earliest useful month per indicator (avoids wasted compute)
IND_MIN_START = {
    "s2": "2017-04-01", "s1": "2014-10-01", "s5p": "2018-07-01",
    "viirs": "2014-01-01", "firms": "2000-11-01", "dw": "2015-07-01",
}
VALID_INDICATORS = set(IND_MIN_START)
VALID_ROLES = {"mine", "mine_smelter", "smelter", "industrial_park", "port", "brine"}

# ---------------------------------------------------------------------------
# Thresholds (heuristics - tune per site if needed; see docs/methodology.md)
# ---------------------------------------------------------------------------
CS_CLEAR = 0.60          # Cloud Score+ cs_cdf clear-pixel threshold
MNDWI_WATER = 0.0        # MNDWI > 0 -> open water / brine
BSI_BARE = 0.05          # bare-soil index threshold
NDVI_BARE = 0.20
BSI_CHANGE = 0.08        # |dBSI| vs. last year that counts as "changed"
SAR_WATER_DB = -18.0     # VV below this -> smooth water
SAR_CHANGE_DB = 3.0      # |dVV| vs. last year that counts as "changed"
FIRMS_MIN_CONF = 30
MIN_S2_VALID = 0.30      # min cloud-free fraction of ROI to keep an S2 month
S5P_SCALE, VIIRS_SCALE, FIRMS_SCALE = 1113.2, 463.83, 1000
CHIRPS_SCALE, ERA5_SCALE = 5566, 11132

# ---------------------------------------------------------------------------
# Signals entering the fundamental indices (+1 = higher means more activity)
# ---------------------------------------------------------------------------
UTILIZATION_SIGNALS = {
    "s5p_so2_enh": 1, "s5p_no2_enh": 1, "viirs_rad_sum": 1,
    "firms_hot_pixel_days": 1, "s2_change_frac_yoy": 1, "s1_change_frac_yoy": 1,
}
CAPACITY_SIGNALS = {"dw_footprint_frac": 1, "s2_bare_frac": 1}
ROLE_CAPACITY_EXTRA = {"brine": {"s2_water_frac": 1}}

KEY_SIGNALS = {  # plotted per site, in this order
    "s2_change_frac_yoy": "Surface change vs. year-ago (fraction of ROI)",
    "s2_bare_frac": "Bare / disturbed ground (fraction)",
    "s2_water_frac": "Open water / brine ponds (fraction)",
    "s2_ndvi": "NDVI (mean)",
    "s1_vv_db": "SAR VV backscatter (dB)",
    "s1_change_frac_yoy": "SAR change vs. year-ago (fraction)",
    "s1_water_frac": "SAR water (fraction)",
    "s5p_so2_enh": "SO2 enhancement vs. background (umol/m2)",
    "s5p_no2_enh": "NO2 enhancement vs. background (umol/m2)",
    "viirs_rad_sum": "Night lights (sum of radiance)",
    "firms_hot_pixel_days": "Thermal-anomaly pixel-days",
    "dw_footprint_frac": "Bare + built footprint (fraction)",
}
SIGNAL_SHORT = {
    "s2_change_frac_yoy": "surface change", "s2_bare_frac": "bare ground",
    "s2_water_frac": "pond / water area", "s2_ndvi": "NDVI", "s2_bsi": "bare-soil index",
    "s2_mndwi": "MNDWI", "s2_vis": "visible brightness",
    "s1_vv_db": "SAR VV", "s1_vh_db": "SAR VH", "s1_change_frac_yoy": "SAR change",
    "s1_water_frac": "SAR water", "s5p_so2_enh": "SO2", "s5p_no2_enh": "NO2",
    "viirs_rad_sum": "night lights", "viirs_rad_mean": "night lights (mean)",
    "firms_hot_pixel_days": "thermal hotspots", "dw_footprint_frac": "footprint",
    "dw_trees_mean": "tree cover",
}
QC_SUFFIXES = ("_n", "_n_images", "_n_days", "_valid_frac", "_cf_cvg", "_bg", "_n_pentads", "_n_era5")


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
def repo_root() -> Path:
    """Repository root: $MM_ROOT if set, else the current working directory."""
    return Path(os.environ.get("MM_ROOT", Path.cwd())).resolve()


@dataclass
class Paths:
    root: Path
    demo: bool = False
    base: Path = field(init=False)

    def __post_init__(self):
        self.base = self.root / "demo_out" if self.demo else self.root

    @property
    def cache(self) -> Path:
        return self.base / "data" / "cache"

    @property
    def outputs(self) -> Path:
        return self.base / "outputs"

    @property
    def figures(self) -> Path:
        return self.base / "figures"

    @property
    def reports(self) -> Path:
        return self.base / "reports"

    @property
    def site_checks(self) -> Path:
        return self.figures / "site_checks"

    def ensure(self):
        for p in (self.cache, self.outputs, self.figures, self.reports, self.outputs / "eda_corr"):
            p.mkdir(parents=True, exist_ok=True)
        return self


# ---------------------------------------------------------------------------
# Site / region configuration
# ---------------------------------------------------------------------------
def load_config(path: str | Path | None = None):
    """Return (sites, regions, price_tickers) from config/sites.yaml."""
    path = Path(path) if path else repo_root() / "config" / "sites.yaml"
    if not path.exists():
        raise SystemExit(f"Config not found: {path}. Run from the repo root or pass --config.")
    cfg = yaml.safe_load(path.read_text()) or {}
    sites, regions = cfg.get("sites", {}) or {}, cfg.get("regions", {}) or {}
    tickers = cfg.get("price_tickers", {}) or {}
    errors = []
    for k, v in sites.items():
        missing = {"lat", "lon", "buffer_km", "role", "commodities", "indicators"} - set(v)
        if missing:
            errors.append(f"site {k}: missing {sorted(missing)}")
            continue
        if not (-90 <= v["lat"] <= 90 and -180 <= v["lon"] <= 180):
            errors.append(f"site {k}: lat/lon out of range")
        bad = set(v["indicators"]) - VALID_INDICATORS
        if bad:
            errors.append(f"site {k}: unknown indicators {sorted(bad)}")
        if v["role"] not in VALID_ROLES:
            errors.append(f"site {k}: role must be one of {sorted(VALID_ROLES)}")
        v.setdefault("label", k)
        v.setdefault("weight", 1.0)
        v.setdefault("note", "")
        v.setdefault("country", "")
    for k, v in regions.items():
        missing = {"bbox", "commodities", "risk_side"} - set(v)
        if missing:
            errors.append(f"region {k}: missing {sorted(missing)}")
            continue
        if v["risk_side"] not in ("dry", "wet"):
            errors.append(f"region {k}: risk_side must be dry or wet")
        v.setdefault("label", k)
        v.setdefault("mechanism", "")
    if errors:
        raise SystemExit("Config errors:\n  " + "\n  ".join(errors))
    return sites, regions, tickers


def select(sites, regions, site_ids=None, commodities=None, include_climate=True):
    """Filter sites/regions by ids and/or commodities."""
    if site_ids:
        unknown = set(site_ids) - set(sites)
        if unknown:
            raise SystemExit(f"Unknown site(s): {', '.join(sorted(unknown))}")
        sites = {k: v for k, v in sites.items() if k in site_ids}
    if commodities:
        cs = set(commodities)
        sites = {k: v for k, v in sites.items() if cs & set(v["commodities"])}
        regions = {k: v for k, v in regions.items() if cs & set(v["commodities"])}
    elif site_ids:
        used = {c for v in sites.values() for c in v["commodities"]}
        regions = {k: v for k, v in regions.items() if used & set(v["commodities"])}
    return sites, (regions if include_climate else {})
