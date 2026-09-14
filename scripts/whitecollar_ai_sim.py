"""AI-driven decline simulation for Japanese white-collar employment.

This is an illustrative system-dynamics model, not a forecast. No public
dataset directly measures "AI-caused white-collar job loss," so this script
combines:

  - Publicly reported occupational headcounts (Labour Force Survey, order of
    magnitude, in 10,000s of workers / 万人) as of the mid-2020s, for four
    broad white-collar categories: management, professional/technical,
    clerical, and sales.
  - A logistic (S-curve) AI capability-adoption curve per scenario.
  - Per-occupation "task automation exposure" shares loosely informed by
    widely cited estimates (Nomura Research Institute / Frey-Osborne style
    automation-probability studies, Goldman Sachs generative-AI task-exposure
    estimates). These are illustrative, not measured.
  - A Japan-specific friction mechanism: because layoffs of regular
    (seishain) employees are legally and culturally hard, AI-driven headcount
    reduction is modeled as happening mostly through *not refilling
    retirements* (attrition), with only a scenario-dependent fraction of any
    "excess" automation pressure translating into forced early exits
    (early-retirement programs, non-renewal of fixed-term contracts, etc).

Run: python3 scripts/whitecollar_ai_sim.py
Writes: data/whitecollar_sim/{scenario}.csv, data/whitecollar_sim/summary.json
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "whitecollar_sim"

START_YEAR = 2025
YEARS = 21  # 2025..2045 inclusive

# Segment starting headcount (万人 = 10,000 persons), rough 2024 order of
# magnitude from Japan's Labour Force Survey occupational breakdown.
SEGMENTS = {
    "management": {"label_ja": "管理的職業", "w0": 120, "tau": 0.15, "rho": 0.025},
    "professional": {"label_ja": "専門的・技術的職業", "w0": 1290, "tau": 0.25, "rho": 0.030},
    "clerical": {"label_ja": "事務従事者", "w0": 1230, "tau": 0.45, "rho": 0.035},
    "sales": {"label_ja": "販売従事者", "w0": 850, "tau": 0.20, "rho": 0.040},
}

SCENARIOS = {
    "conservative": {
        "label_ja": "慎重シナリオ",
        "t_mid": 15.0, "k": 0.30,          # AI adoption S-curve
        "r0": 0.75, "r_end": 0.55,          # redeployment fraction (start/end)
        "rigidity": 0.05,                   # share of excess pressure -> forced exits
    },
    "base": {
        "label_ja": "標準シナリオ",
        "t_mid": 10.0, "k": 0.40,
        "r0": 0.65, "r_end": 0.40,
        "rigidity": 0.15,
    },
    "aggressive": {
        "label_ja": "急進シナリオ",
        "t_mid": 6.0, "k": 0.50,
        "r0": 0.55, "r_end": 0.25,
        "rigidity": 0.35,
    },
}


def adoption_curve(t: int, t_mid: float, k: float) -> float:
    """Logistic AI-capability-realized share for year offset t."""
    return 1.0 / (1.0 + math.exp(-k * (t - t_mid)))


def redeployment(t: int, r0: float, r_end: float, n_years: int) -> float:
    """Linear interpolation of the fraction of automated capacity that is
    redeployed into new/expanded work rather than reducing headcount."""
    frac = t / (n_years - 1)
    return r0 + (r_end - r0) * frac


def run_scenario(name: str, params: dict) -> dict:
    segments = {k: dict(v) for k, v in SEGMENTS.items()}
    for seg in segments.values():
        seg["trajectory"] = [seg["w0"]]
        seg["decline_rate"] = []  # per-year net decline rate applied

    for t in range(YEARS - 1):
        a_t = adoption_curve(t, params["t_mid"], params["k"])
        r_t = redeployment(t, params["r0"], params["r_end"], YEARS)
        for seg in segments.values():
            w = seg["trajectory"][-1]
            gross = seg["tau"] * a_t
            net_pressure = gross * (1.0 - r_t)
            not_refilled = min(net_pressure, seg["rho"])
            excess = max(net_pressure - seg["rho"], 0.0)
            forced = excess * params["rigidity"]
            decline_rate = not_refilled + forced
            seg["decline_rate"].append(decline_rate)
            seg["trajectory"].append(w * (1.0 - decline_rate))

    total = [sum(seg["trajectory"][t] for seg in segments.values()) for t in range(YEARS)]
    total0 = total[0]

    # --- summary metrics -------------------------------------------------
    def year_of_drop(threshold: float) -> float | None:
        """First calendar year (fractional, linear-interpolated) at which
        total headcount has fallen by `threshold` fraction from year 0."""
        target = total0 * (1.0 - threshold)
        for t in range(1, YEARS):
            if total[t] <= target:
                prev, cur = total[t - 1], total[t]
                frac = (prev - target) / (prev - cur) if prev != cur else 0.0
                return START_YEAR + (t - 1) + frac
        return None

    yoy = [(total[t] / total[t - 1] - 1.0) for t in range(1, YEARS)]
    peak_idx = min(range(len(yoy)), key=lambda i: yoy[i])

    def cagr(n: int) -> float:
        if n >= YEARS:
            return float("nan")
        return (total[n] / total0) ** (1.0 / n) - 1.0

    summary = {
        "scenario": name,
        "label_ja": params["label_ja"],
        "total_2025_manyoku": round(total0, 1),
        "total_2030_manyoku": round(total[5], 1),
        "total_2035_manyoku": round(total[10], 1),
        "total_2040_manyoku": round(total[15], 1),
        "total_2045_manyoku": round(total[20], 1),
        "cagr_2025_2030_pct": round(cagr(5) * 100, 2),
        "cagr_2025_2035_pct": round(cagr(10) * 100, 2),
        "cagr_2025_2045_pct": round(cagr(20) * 100, 2),
        "year_reach_minus10pct": year_of_drop(0.10),
        "year_reach_minus20pct": year_of_drop(0.20),
        "year_reach_minus30pct": year_of_drop(0.30),
        "peak_annual_decline_pct": round(-yoy[peak_idx] * 100, 2),
        "peak_annual_decline_year": START_YEAR + peak_idx + 1,
    }

    # --- write per-year CSV ------------------------------------------------
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUT_DIR / f"{name}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        header = ["year"] + list(segments.keys()) + ["total", "total_index_2025_100", "yoy_pct"]
        writer.writerow(header)
        for t in range(YEARS):
            row = [START_YEAR + t]
            row += [round(segments[k]["trajectory"][t], 2) for k in segments]
            row.append(round(total[t], 2))
            row.append(round(total[t] / total0 * 100, 2))
            row.append(round(yoy[t - 1] * 100, 3) if t > 0 else "")
            writer.writerow(row)

    return summary


def main() -> None:
    summaries = [run_scenario(name, params) for name, params in SCENARIOS.items()]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with (OUT_DIR / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(
            {
                "assumptions": {
                    "segments": SEGMENTS,
                    "scenarios": SCENARIOS,
                    "start_year": START_YEAR,
                    "years_simulated": YEARS,
                },
                "results": summaries,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"Wrote {len(summaries)} scenario CSVs and summary.json to {OUT_DIR}")
    for s in summaries:
        print(
            f"[{s['scenario']}] 2025={s['total_2025_manyoku']}万人 -> "
            f"2035={s['total_2035_manyoku']}万人 -> 2045={s['total_2045_manyoku']}万人 | "
            f"-10% by {s['year_reach_minus10pct']:.1f} | "
            f"-20% by {s['year_reach_minus20pct'] and round(s['year_reach_minus20pct'],1)} | "
            f"peak decline {s['peak_annual_decline_pct']}%/yr in {s['peak_annual_decline_year']}"
        )


if __name__ == "__main__":
    main()
