"""
analyze.py
Badge Access Log Analyzer: clean, validate, flag anomalies, chart, and export.

Pipeline:
    1. Load the raw log
    2. Clean and validate (duplicates, missing fields, bad timestamps, inconsistent values)
    3. Flag anomalies with explainable rules
    4. Build summary charts
    5. Export clean data, flags, and a summary workbook ready for Power BI / Excel

Usage:
    python analyze.py                              # uses data/raw_access_log.csv
    python analyze.py --input path/to/log.csv --out output
"""

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

# ---- Tunable rule thresholds (documented in README) -----------------------
AFTER_HOURS_START = 22        # 10 PM
AFTER_HOURS_END = 5           # 5 AM
DENIAL_WINDOW = "10min"       # window for counting repeated denials
DENIAL_THRESHOLD = 3          # denials in window that trigger a flag
TRAVEL_SECONDS = 120          # min. plausible time to move between buildings
DORMANT_DAYS = 30             # badge unseen this long, then active = flag

REQUIRED = ["event_id", "timestamp", "badge_id", "reader_id", "result"]
VALID_RESULTS = {"GRANTED", "DENIED"}

# Chart styling: one hue, recessive grid, ink-colored text
BLUE = "#2a78d6"
RED = "#d03b3b"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"


# ---------------------------------------------------------------------------
# 1. Clean and validate
# ---------------------------------------------------------------------------
def clean(raw: pd.DataFrame):
    """Return (clean_df, quality_report). Every fix is counted, nothing is silent."""
    report = []
    df = raw.copy()
    report.append(("Rows loaded", len(df)))

    # Normalize text fields: trim whitespace, standardize case
    for col in ["badge_id", "reader_id", "result", "department", "building", "reader_location"]:
        if col in df:
            df[col] = df[col].astype("string").str.strip()
    df["result"] = df["result"].str.upper()
    report.append(("Result values re-cased/trimmed", int((df["result"] != raw["result"]).sum())))

    # Exact duplicate events
    dupes = df.duplicated()
    report.append(("Exact duplicate rows removed", int(dupes.sum())))
    df = df[~dupes]

    # Missing required fields
    df = df.replace({"": pd.NA})
    missing = df[REQUIRED].isna().any(axis=1)
    report.append(("Rows missing a required field removed", int(missing.sum())))
    df = df[~missing]

    # Timestamps that will not parse
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="%Y-%m-%d %H:%M:%S", errors="coerce")
    bad_ts = df["timestamp"].isna()
    report.append(("Rows with malformed timestamps removed", int(bad_ts.sum())))
    df = df[~bad_ts]

    # Result values outside the allowed set
    bad_result = ~df["result"].isin(VALID_RESULTS)
    report.append(("Rows with invalid result values removed", int(bad_result.sum())))
    df = df[~bad_result]

    df = df.sort_values("timestamp").reset_index(drop=True)
    report.append(("Clean rows retained", len(df)))
    report.append(("Retention rate", f"{len(df) / len(raw):.1%}"))
    return df, pd.DataFrame(report, columns=["check", "value"])


# ---------------------------------------------------------------------------
# 2. Anomaly rules (each returns rows with a rule name and a plain-English reason)
# ---------------------------------------------------------------------------
def flag_after_hours(df):
    h = df["timestamp"].dt.hour
    hits = df[(df["result"] == "GRANTED") & ((h >= AFTER_HOURS_START) | (h < AFTER_HOURS_END))].copy()
    hits["rule"] = "After-hours access"
    hits["detail"] = "Granted entry at " + hits["timestamp"].dt.strftime("%H:%M")
    return hits


def flag_repeated_denials(df):
    denied = df[df["result"] == "DENIED"].copy()
    out = []
    for (badge, reader), grp in denied.groupby(["badge_id", "reader_id"]):
        counts = grp.rolling(DENIAL_WINDOW, on="timestamp")["event_id"].count()
        if (counts >= DENIAL_THRESHOLD).any():
            first = grp.iloc[0].copy()
            first["rule"] = "Repeated denials"
            first["detail"] = f"{len(grp)} denied swipes at {first['reader_location']}"
            out.append(first)
    return pd.DataFrame(out)


def flag_impossible_travel(df):
    granted = df[df["result"] == "GRANTED"].sort_values(["badge_id", "timestamp"]).copy()
    prev_bldg = granted.groupby("badge_id")["building"].shift()
    prev_time = granted.groupby("badge_id")["timestamp"].shift()
    gap = (granted["timestamp"] - prev_time).dt.total_seconds()
    mask = (prev_bldg.notna()) & (granted["building"] != prev_bldg) & (gap < TRAVEL_SECONDS)
    hits = granted[mask].copy()
    hits["rule"] = "Impossible travel"
    hits["detail"] = (prev_bldg[mask] + " to " + hits["building"] + " in "
                      + gap[mask].astype(int).astype(str) + "s")
    return hits


def flag_dormant(df):
    log_start = df["timestamp"].min()
    first_seen = df.groupby("badge_id")["timestamp"].min()
    late = first_seen[(first_seen - log_start).dt.days >= DORMANT_DAYS]
    out = []
    for badge, ts in late.items():
        row = df[(df["badge_id"] == badge) & (df["timestamp"] == ts)].iloc[0].copy()
        row["rule"] = "Dormant badge reactivated"
        row["detail"] = f"No activity for {(ts - log_start).days} days, then active"
        out.append(row)
    return pd.DataFrame(out)


def run_rules(df):
    frames = [flag_after_hours(df), flag_repeated_denials(df),
              flag_impossible_travel(df), flag_dormant(df)]
    flags = pd.concat([f for f in frames if not f.empty], ignore_index=True)
    cols = ["rule", "timestamp", "badge_id", "department", "reader_location", "building", "result", "detail"]
    return flags[cols].sort_values(["rule", "timestamp"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# 3. Charts
# ---------------------------------------------------------------------------
def _style(ax, title, xlabel="", ylabel=""):
    ax.set_title(title, loc="left", fontsize=12, fontweight="bold", color=INK, pad=12)
    ax.set_xlabel(xlabel, color=INK_2)
    ax.set_ylabel(ylabel, color=INK_2)
    ax.tick_params(colors=INK_2, length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ["top", "right", "left"]:
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)


def make_charts(df, flags, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans", "figure.dpi": 120})

    # Swipes by hour of day
    fig, ax = plt.subplots(figsize=(9, 4))
    by_hour = df.groupby(df["timestamp"].dt.hour).size().reindex(range(24), fill_value=0)
    colors = [RED if (h >= AFTER_HOURS_START or h < AFTER_HOURS_END) else BLUE for h in by_hour.index]
    ax.bar(by_hour.index, by_hour.values, color=colors, width=0.8)
    _style(ax, "Badge swipes by hour of day (red = after-hours window)", "Hour", "Swipes")
    ax.set_xticks(range(0, 24, 2))
    fig.tight_layout()
    fig.savefig(out_dir / "swipes_by_hour.png")
    plt.close(fig)

    # Denial rate by reader
    fig, ax = plt.subplots(figsize=(9, 4.5))
    rate = (df.assign(denied=df["result"] == "DENIED")
              .groupby("reader_location")["denied"].mean().sort_values() * 100)
    ax.barh(rate.index, rate.values, color=BLUE, height=0.6)
    for y, v in enumerate(rate.values):
        ax.text(v + 0.1, y, f"{v:.1f}%", va="center", fontsize=9, color=INK)
    _style(ax, "Denial rate by reader", "% of swipes denied")
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    fig.tight_layout()
    fig.savefig(out_dir / "denial_rate_by_reader.png")
    plt.close(fig)

    # Daily volume trend
    fig, ax = plt.subplots(figsize=(9, 4))
    daily = df.set_index("timestamp").resample("D").size()
    ax.plot(daily.index, daily.values, color=BLUE, linewidth=2)
    _style(ax, "Daily swipe volume", "", "Swipes")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out_dir / "daily_volume.png")
    plt.close(fig)

    # Flags by rule
    fig, ax = plt.subplots(figsize=(9, 3.5))
    counts = flags["rule"].value_counts().sort_values()
    ax.barh(counts.index, counts.values, color=BLUE, height=0.55)
    for y, v in enumerate(counts.values):
        ax.text(v + 0.5, y, str(v), va="center", fontsize=9, color=INK)
    _style(ax, "Flagged events by rule", "Events flagged")
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    fig.tight_layout()
    fig.savefig(out_dir / "flags_by_rule.png")
    plt.close(fig)


# ---------------------------------------------------------------------------
# 4. Export
# ---------------------------------------------------------------------------
def export(df, flags, quality, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_dir / "clean_access_log.csv", index=False)
    flags.to_csv(out_dir / "flagged_events.csv", index=False)

    top_badges = (flags.groupby(["badge_id", "department"])
                       .agg(flags=("rule", "size"), rules=("rule", lambda r: ", ".join(sorted(set(r)))))
                       .sort_values("flags", ascending=False).head(15).reset_index())
    by_reader = (df.groupby(["building", "reader_location"])
                   .agg(swipes=("event_id", "size"),
                        denied=("result", lambda r: (r == "DENIED").sum()))
                   .reset_index())
    by_reader["denial_rate"] = (by_reader["denied"] / by_reader["swipes"]).round(4)

    with pd.ExcelWriter(out_dir / "access_summary.xlsx") as xl:
        quality.to_excel(xl, sheet_name="Data Quality", index=False)
        flags["rule"].value_counts().rename_axis("rule").reset_index(name="count") \
            .to_excel(xl, sheet_name="Flags by Rule", index=False)
        top_badges.to_excel(xl, sheet_name="Top Flagged Badges", index=False)
        by_reader.to_excel(xl, sheet_name="Reader Summary", index=False)
        flags.to_excel(xl, sheet_name="All Flags", index=False)
    return top_badges


def main():
    p = argparse.ArgumentParser(description="Analyze a badge access-control log.")
    p.add_argument("--input", default="data/raw_access_log.csv")
    p.add_argument("--out", default="output")
    args = p.parse_args()
    out = Path(args.out)

    raw = pd.read_csv(args.input, dtype=str, keep_default_na=False)
    df, quality = clean(raw)
    flags = run_rules(df)
    make_charts(df, flags, out / "charts")
    top = export(df, flags, quality, out)

    print("\n== Data quality ==")
    print(quality.to_string(index=False))
    print("\n== Flagged events by rule ==")
    print(flags["rule"].value_counts().to_string())
    print("\n== Top flagged badges ==")
    print(top.head(5).to_string(index=False))
    print(f"\nOutputs written to {out.resolve()}")


if __name__ == "__main__":
    main()
