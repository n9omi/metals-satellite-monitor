import marimo

__generated_with = "0.9.0"
app = marimo.App(width="medium")


@app.cell
def _():
    from pathlib import Path

    import marimo as mo
    import matplotlib.pyplot as plt
    import pandas as pd
    return Path, mo, pd, plt


@app.cell
def _(Path, mo):
    mo.md(
        """
        # Metals & minerals satellite monitor: explorer

        Reads the pipeline outputs. Run `bash run.sh` (live) or `bash run.sh --demo`
        (synthetic) first, then `marimo edit notebooks/explore.py`.
        """
    )
    root = Path(__file__).resolve().parents[1]
    has_live = (root / "outputs" / "site_indices.csv").exists()
    use_demo = mo.ui.switch(label="Use synthetic demo outputs (demo_out/)", value=not has_live)
    use_demo
    return has_live, root, use_demo


@app.cell
def _(pd, root, use_demo):
    base = (root / "demo_out" / "outputs") if use_demo.value else (root / "outputs")
    panel = pd.read_csv(base / "site_panel.csv", parse_dates=["date"])
    indices = pd.read_csv(base / "site_indices.csv", parse_dates=["date"])
    snapshot = pd.read_csv(base / "site_snapshot.csv")
    commodity = pd.read_csv(base / "commodity_indices.csv", parse_dates=["date"])
    return base, commodity, indices, panel, snapshot


@app.cell
def _(mo, snapshot):
    mo.vstack([mo.md("## Latest site snapshot"), mo.ui.table(snapshot, selection=None)])
    return


@app.cell
def _(mo, panel):
    site = mo.ui.dropdown(sorted(panel["site"].unique()), value=sorted(panel["site"].unique())[0], label="Site")
    site
    return (site,)


@app.cell
def _(mo, panel, site):
    available = sorted(panel.loc[panel["site"] == site.value, "variable"].unique())
    signals = mo.ui.multiselect(available, value=available[:3], label="Signals")
    signals
    return available, signals


@app.cell
def _(indices, panel, plt, signals, site):
    _sel = panel[(panel["site"] == site.value) & (panel["variable"].isin(signals.value))]
    _n = max(len(signals.value), 1) + 1
    _fig, _axes = plt.subplots(_n, 1, figsize=(9, 1.8 * _n), sharex=True)
    for _ax, _var in zip(_axes, signals.value):
        _s = _sel[_sel["variable"] == _var].set_index("date")["value"].sort_index()
        _ax.plot(_s.index, _s.values, lw=0.7, alpha=0.4, color="#2a78d6")
        _ax.plot(_s.index, _s.rolling(3, min_periods=2).mean().values, lw=1.6, color="#2a78d6")
        _ax.set_title(_var, loc="left", fontsize=9)
        _ax.grid(alpha=0.3)
    _i = indices[indices["site"] == site.value].set_index("date")
    _axes[-1].axhline(0, color="#c3c2b7", lw=0.8)
    _axes[-1].plot(_i.index, _i["utilization_idx_3m"], color="#2a78d6", lw=1.6, label="utilization (3m)")
    _axes[-1].plot(_i.index, _i["capacity_idx"], color="#eb6834", lw=1.6, label="capacity")
    _axes[-1].legend(frameon=False, fontsize=8)
    _axes[-1].set_title("indices (point-in-time z)", loc="left", fontsize=9)
    _fig.tight_layout()
    _fig
    return


@app.cell
def _(commodity, mo):
    _wide = commodity.pivot(index="date", columns="commodity", values="utilization_idx_3m").round(2)
    mo.vstack([mo.md("## Commodity utilization index (3m z), latest 12 months"),
               mo.ui.table(_wide.tail(12).reset_index(), selection=None)])
    return


if __name__ == "__main__":
    app.run()
