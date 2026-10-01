# Methodology

This document specifies every computation in the pipeline, so the numbers in the report can be
checked by hand. Constants live in `src/metals_monitor/config.py`.

## 1. Regions of interest

Each site is a circle of radius `buffer_km` around `(lat, lon)` (`config/sites.yaml`). Sentinel-5P
uses a larger circle (`max(buffer_km, 15)` km), because a TROPOMI pixel is about 3.5 × 5.5 km.
Its background is an annulus from `max(3 × buffer_km, 40)` km to that radius + 50 km. Climate
regions are rectangles (`bbox`).

Coordinates are approximate. `bash run.sh --check-sites` renders a 12-month cloud-free
Sentinel-2 composite of each site with the ROI outlined, plus `rois.geojson` for QGIS.

## 2. Monthly signals (Earth Engine)

Every signal is reduced to one value per calendar month: a mean over the ROI unless noted.
Reductions use `bestEffort` with `tileScale=4`. Sums use a fixed scale, so the pixel size never
changes the total.

| Prefix | Collection | Per-month computation | Columns |
|---|---|---|---|
| `s2_` | `COPERNICUS/S2_SR_HARMONIZED` linked to `GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED` | Mask pixels with `cs_cdf < 0.60`; median composite of reflectance/10000. Indices: NDVI = (B8−B4)/(B8+B4); MNDWI = (B3−B11)/(B3+B11); BSI = ((B11+B4)−(B8+B2))/((B11+B4)+(B8+B2)). | `ndvi`, `mndwi`, `bsi`, `vis` (mean of B2-B4), `water_frac` (share of clear pixels with MNDWI > 0), `bare_frac` (BSI > 0.05 and NDVI < 0.20), `change_frac_yoy` (share of pixels with \|BSI − BSI one year earlier\| > 0.08), `valid_frac` (cloud-free share of ROI), `n_images` |
| `s1_` | `COPERNICUS/S1_GRD`, IW, VV+VH, one orbit direction | Pick the site's dominant pass once (cached); median composite in dB. | `vv_db`, `vh_db`, `water_frac` (VV < −18 dB), `change_frac_yoy` (\|VV − VV one year earlier\| > 3 dB), `n_images` |
| `s5p_` | `COPERNICUS/S5P/OFFL/L3_NO2` (tropospheric column), `.../L3_SO2` | Monthly mean of all orbits, in µmol/m². For SO<sub>2</sub>, only values below −0.001 mol/m² are masked, per catalog guidance (negative noise is kept). | `no2`, `no2_bg`, `so2`, `so2_bg`, `*_n` (orbit count) → derived `*_enh = roi − bg` |
| `viirs_` | `NOAA/VIIRS/DNB/MONTHLY_V1/VCMSLCFG` | `avg_rad` clipped at 0. | `rad_sum` (sum over ROI), `rad_mean`, `cf_cvg` (mean cloud-free observations) |
| `firms_` | `FIRMS` (MODIS) | Per day: pixel flagged if confidence ≥ 30; summed over the month. | `hot_pixel_days` (sum over ROI), `n_days` |
| `dw_` | `GOOGLE/DYNAMICWORLD/V1` | Monthly mean class probabilities. | `footprint_frac` (share of pixels with P(bare)+P(built) > 0.5), `trees_mean` |
| climate | `UCSB-CHG/CHIRPS/PENTAD`, `ECMWF/ERA5_LAND/MONTHLY_AGGR` | Monthly rainfall sum; mean 2 m temperature. | `precip_mm`, `t2m_c`, `n_pentads`, `n_era5` |

Composites are made robust to empty months by merging a fully masked image into each
collection. An empty month therefore returns nulls rather than an error.

## 3. Quality control (step 03)

| Rule | Effect |
|---|---|
| `s2_valid_frac < 0.30` | Blank all S2 signals that month |
| `s1_n_images = 0`, `dw_n_images = 0`, `s5p_*_n = 0` | Blank that source's signals |
| `viirs_cf_cvg` missing or < 1 | Blank night lights (a zero without coverage is not darkness) |
| `firms_n_days < 20` | Blank hotspots (partial month) |
| `n_pentads < 6` | Blank rainfall (partial month) |

## 4. Point-in-time seasonal z-score

For a monthly series *x*, month *t* and calendar month *m(t)*:

- climatology *c<sub>t</sub>* = mean of *x* in the same calendar month in **earlier** years only
- anomaly *a<sub>t</sub>* = *x<sub>t</sub>* − *c<sub>t</sub>*
- *z<sub>t</sub>* = *a<sub>t</sub>* / sd(*a<sub>1</sub> … a<sub>t−1</sub>*), requiring at least 12 past anomalies

No value after *t* enters *z<sub>t</sub>*, so the indices can be backtested without look-ahead.
(`tests/test_analytics.py::test_trailing_z_has_no_look_ahead` checks this.) The cost is a warm-up:
indices start about two years after a series begins.

## 5. Site indices

- **Utilization** = mean over available signals of clip(sign × z, −5, 5). The signals are
  `s5p_so2_enh`, `s5p_no2_enh`, `viirs_rad_sum`, `firms_hot_pixel_days`, `s2_change_frac_yoy` and
  `s1_change_frac_yoy`, all with sign +1. A 3-month mean is reported alongside.
- **Capacity** = mean of clip(z of the 3-month-smoothed series, −5, 5) for `dw_footprint_frac` and
  `s2_bare_frac`, plus `s2_water_frac` for brine sites.
- `n_util_signals` and `n_capacity_signals` record how many inputs each month had.

## 6. Commodity indices

Commodity index at month *t* = Σ *w<sub>s</sub>* · idx<sub>s,t</sub> / Σ *w<sub>s</sub>*, taken over the sites
listed under that commodity with data at *t*. A site with several commodities (e.g. Norilsk:
nickel, palladium, copper) enters each one. Weights default to 1. Set `weight` to capacity or
production shares to make the index production-weighted.

## 7. Climate and power-supply risk

The baseline is 2001-2020 by calendar month.

- `precip_pct_normal` = rainfall / baseline mean × 100
- `spi3_like` = (3-month rainfall total − baseline mean) / baseline sd. This is a normal
  approximation to SPI-3, not the gamma-fitted index.
- `t2m_anom_c` = temperature − baseline mean

Flags: if `risk_side: dry`, then DRY when SPI-3 ≤ −1 and SEVERE DRY when ≤ −1.5. If
`risk_side: wet`, then WET when ≥ 1 and SEVERE WET when ≥ 1.5. HEAT is added when the
temperature anomaly is at least 1.5 °C.

## 8. Price linkage

Prices are month-end closes, labelled by month. Returns are monthly log differences. For a
signal *s* (the 3-month commodity utilization index, or regional SPI-3) and lag *k* ∈ [−6, 6]:

*r<sub>k</sub>* = corr(*s<sub>t</sub>*, ret<sub>t+k</sub>), so *k* > 0 means the signal leads the price.

Both the level and the first difference of *s* are tested. Significance uses an effective sample
size *n<sub>eff</sub>* = *n*(1 − ρ<sub>1</sub>ρ<sub>2</sub>)/(1 + ρ<sub>1</sub>ρ<sub>2</sub>), where ρ are lag-1 autocorrelations.
The p-value is a normal approximation to *t* = *r*·√((*n<sub>eff</sub>*−2)/(1−*r*²)). No correction is
made for testing 13 lags × 2 transforms × every pair, so the best lag is a hypothesis, not a result.

## 9. Validation against reported output

`--production-csv` takes columns `site,date,value` (quarterly or monthly, detected from the date
spacing). Each site's indices are averaged to that frequency and correlated with the production
level and its year-on-year change. A proxy should pass this test before it is used for prices.

## 10. Anomaly feed and takeaways

`anomalies_recent.csv` lists every signal with |z| ≥ 2 in the last `--anomaly-months` (default 6).
The PDF takeaways are rule-based sentences built from the snapshots. They cover the largest
commodity moves, flagged regions, the latest 2-sigma moves, the fastest footprint growth, and the
strongest price link (or a statement that none clears |r| ≥ 0.3 at p < 0.05).

## 11. Known failure modes

- **Cloud** in the tropics (Grasberg, Cobre Panamá, Morowali, Weda Bay) blanks Sentinel-2 for months. SAR is the fallback.
- **Polar night and summer** at Norilsk remove Sentinel-5P in winter and VIIRS in summer.
- **Wind** moves NO<sub>2</sub>/SO<sub>2</sub> plumes. Monthly means and a distant background ring reduce, but do not remove, this effect.
- **Fires** near tropical sites inflate FIRMS counts. Treat hotspot spikes alongside the tree-cover signal.
- **Arid terrain** is always "bare", so `bare_frac` and Dynamic World footprint carry little information at Atacama sites. Change fractions do better there.
- **Sentinel-1B** failed in December 2021, and Sentinel-1C (launched December 2024) restored the second satellite. Revisit density changes across that period, so `s1_n_images` should be read alongside the SAR signals.
