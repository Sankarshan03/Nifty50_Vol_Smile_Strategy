"""CLI: python run_research.py --data-dir data/raw            (real data: options.csv + underlying.csv)
       python run_research.py --synthetic 40 --out reports_synth   (pipeline validation only)"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nifty_0dte_smile.config import Config  # noqa: E402
from nifty_0dte_smile.report import run_all  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", help="folder with options.(csv|parquet) and underlying.(csv|parquet)")
    ap.add_argument("--synthetic", type=int, default=0, help="generate N synthetic days instead of loading data")
    ap.add_argument("--mispricing", type=float, default=0.0, help="synthetic only: planted ATM mispricing (IV units)")
    ap.add_argument("--freq", type=int, default=5, help="synthetic only: snapshot minutes")
    ap.add_argument("--out", default="reports")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--time-convention", default="trading248", choices=["trading248", "trading252", "calendar"])
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args()
    cfg = Config(time_convention=a.time_convention)
    if a.synthetic:
        from nifty_0dte_smile.data.synthetic import generate
        o, u = generate(n_days=a.synthetic, freq_min=a.freq, mispricing_vol=a.mispricing, convention=a.time_convention)
        label = f"SYNTHETIC ({a.synthetic} days, {a.freq}-min, planted mispricing={a.mispricing})"
    elif a.data_dir:
        from nifty_0dte_smile.data.loader import load_dataset
        o, u = load_dataset(a.data_dir)
        label = f"real data from {a.data_dir}"
    else:
        ap.error("give --data-dir or --synthetic N")
    run_all(o, u, a.out, cfg, n_jobs=a.jobs, data_label=label, cache=not a.no_cache)
    print(f"done -> {a.out}/RESEARCH_REPORT.md")


if __name__ == "__main__":
    main()
