from pathlib import Path
import pandas as pd

DEFAULT_VERSION=1
DEFAULT_YEAR=2021
OUTPUT_DIR = Path(f"../pipeline/wastewater/v{DEFAULT_VERSION}.{DEFAULT_YEAR}/score_output")
QA_DIR = Path(f"../pipeline/wastewater/v{DEFAULT_VERSION}.{DEFAULT_YEAR}/score_output/qa")
QA_DIR.mkdir(parents=True, exist_ok=True)

rows = []

for state_dir in sorted(OUTPUT_DIR.iterdir()):
    if not state_dir.is_dir():
        continue

    csv_file = state_dir / "final_bg_scores.csv"
    if not csv_file.exists():
        continue

    df = pd.read_csv(csv_file)

    score_cols = [c for c in df.columns if c != "block_group_geoid"]

    if len(score_cols) != 1:
        raise ValueError(
            f"Expected exactly one indicator column in {csv_file}"
    )

    score_col = score_cols[0]

    rows.append({
        "state": state_dir.name,
        "block_groups": len(df),
        "min": df[score_col].min(),
        "max": df[score_col].max(),
        "mean": df[score_col].mean(),
        "median": df[score_col].median(),
        "zeros": (df[score_col] == 0).sum(),
        "nans": df[score_col].isna().sum(),
    })

summary = pd.DataFrame(rows).sort_values("state")

summary.to_csv(
    QA_DIR / "wastewater_national_summary.csv",
    index=False
)

all_scores = []

for state in summary["state"]:
    csv_file = OUTPUT_DIR / state / "final_bg_scores.csv"
    df = pd.read_csv(csv_file)

    score_cols = [
        column
        for column in df.columns
        if column != "block_group_geoid"
    ]

    if len(score_cols) != 1:
        raise ValueError(
            f"Expected exactly one indicator column in {csv_file}, "
            f"but found: {score_cols}"
        )

    score_col = score_cols[0]
    all_scores.append(df[score_col])

all_scores = pd.concat(all_scores, ignore_index=True)

report = f"""
Wastewater National QA Summary
==============================

States processed: {len(summary)}

Total block groups:
{len(all_scores):,}

National minimum:
{all_scores.min()}

National maximum:
{all_scores.max()}

National mean:
{all_scores.mean()}

National median:
{all_scores.median()}

Zero scores:
{(all_scores == 0).sum():,}

Missing values:
{all_scores.isna().sum():,}

Top 10 states by maximum score
------------------------------
{summary.nlargest(10, "max")[["state","max"]].to_string(index=False)}

Top 10 states by mean score
---------------------------
{summary.nlargest(10, "mean")[["state","mean"]].to_string(index=False)}
"""

(QA_DIR / "wastewater_national_summary.txt").write_text(report)

print(report)
print()
print(f"CSV written to {QA_DIR/'wastewater_national_summary.csv'}")
print(f"Report written to {QA_DIR/'wastewater_national_summary.txt'}")