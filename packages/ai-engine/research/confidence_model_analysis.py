#!/usr/bin/env python3
"""
Confidence Model Analysis — Multivariate Component Attribution & Recalibration

This script answers the key research questions:
1. Which confidence components independently predict outcomes?
2. How should the confidence score be recalibrated?
3. Does the recalibrated model improve on out-of-sample data?

Usage:
    python confidence_model_analysis.py [--output report.md]
"""

import sqlite3
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.isotonic import IsotonicRegression
from sklearn.model_selection import train_test_split, StratifiedKFold
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score, accuracy_score
from sklearn.preprocessing import StandardScaler
import warnings
warnings.filterwarnings('ignore')

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════════

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"
OUTPUT_PATH = Path(__file__).resolve().parent / "confidence_model_report.md"

FEATURE_COLS = [
    "institutional_score",
    "mss_score",
    "fvg_score",
    "volatility_score",
]

TARGET = "win"  # outcome == "win" → 1, else 0

TEST_SIZE = 0.30
RANDOM_STATE = 42
N_CV_FOLDS = 5

# Current EMA V5 confidence formula weights
CURRENT_WEIGHTS = {
    "institutional_score": 0.50,
    "regime_score": 0.10,
    "trend_score": -0.10,
    "pullback_score": 0.15,
    "candle_score": -0.10,
    "volume_score": -0.05,
}


# ══════════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ══════════════════════════════════════════════════════════════════════════════

def load_trades():
    """Load all closed trades with confidence and component scores."""
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row

    trades = []
    for table in ["positions_archive", "positions"]:
        rows = conn.execute(f"""
            SELECT confidence, outcome, pnl, risk_reward, regime, session,
                   symbol, side, institutional_score, mss_score, fvg_score,
                   volatility_score, mae_pct, mfe_pct, realized_r
            FROM {table}
            WHERE status = 'closed' AND outcome IS NOT NULL AND confidence IS NOT NULL
        """).fetchall()
        trades.extend([dict(r) for r in rows])

    conn.close()
    return pd.DataFrame(trades)


def prepare_features(df):
    """Prepare feature matrix and target vector."""
    # Normalize confidence to 0-1
    df["conf_norm"] = df["confidence"].apply(lambda x: x / 100 if x > 1 else x)

    # Binary target
    df["y"] = (df["outcome"] == "win").astype(int)

    # Filter to rows with non-zero component scores
    mask = df[FEATURE_COLS].sum(axis=1) > 0
    df_filtered = df[mask].copy()

    # Fill NaN with 0
    for col in FEATURE_COLS:
        df_filtered[col] = df_filtered[col].fillna(0)

    return df_filtered


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS 1: MULTIVARIATE LOGISTIC REGRESSION
# ══════════════════════════════════════════════════════════════════════════════

def analyze_components(df):
    """Estimate independent contribution of each component via logistic regression."""
    X = df[FEATURE_COLS].values
    y = df["y"].values

    # Standardize features for comparable coefficients
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # Split into train/test
    X_train, X_test, y_train, y_test = train_test_split(
        X_scaled, y, test_size=TEST_SIZE, random_state=RANDOM_STATE, stratify=y
    )

    # Fit logistic regression
    model = LogisticRegression(max_iter=1000, random_state=RANDOM_STATE)
    model.fit(X_train, y_train)

    # Predictions
    y_pred_proba_train = model.predict_proba(X_train)[:, 1]
    y_pred_proba_test = model.predict_proba(X_test)[:, 1]

    # Metrics
    results = {
        "train": {
            "brier": brier_score_loss(y_train, y_pred_proba_train),
            "log_loss": log_loss(y_train, y_pred_proba_train),
            "auc": roc_auc_score(y_train, y_pred_proba_train),
            "n": len(y_train),
        },
        "test": {
            "brier": brier_score_loss(y_test, y_pred_proba_test),
            "log_loss": log_loss(y_test, y_pred_proba_test),
            "auc": roc_auc_score(y_test, y_pred_proba_test),
            "n": len(y_test),
        },
    }

    # Component coefficients (standardized → comparable)
    coefficients = {}
    for i, col in enumerate(FEATURE_COLS):
        coefficients[col] = {
            "coef": model.coef_[0][i],
            "abs_coef": abs(model.coef_[0][i]),
            "direction": "positive" if model.coef_[0][i] > 0 else "negative",
            "odds_ratio": np.exp(model.coef_[0][i]),
        }

    # Sort by absolute coefficient (importance)
    sorted_coefs = sorted(coefficients.items(), key=lambda x: -x[1]["abs_coef"])

    return {
        "model": model,
        "scaler": scaler,
        "coefficients": coefficients,
        "sorted_coefs": sorted_coefs,
        "results": results,
        "X_test": X_test,
        "y_test": y_test,
        "y_pred_proba_test": y_pred_proba_test,
        "intercept": model.intercept_[0],
    }


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS 2: CROSS-VALIDATION STABILITY
# ══════════════════════════════════════════════════════════════════════════════

def cross_validate_model(df):
    """Check if component importance is stable across folds."""
    X = df[FEATURE_COLS].values
    y = df["y"].values

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    skf = StratifiedKFold(n_splits=N_CV_FOLDS, shuffle=True, random_state=RANDOM_STATE)

    fold_coefs = {col: [] for col in FEATURE_COLS}
    fold_auc = []
    fold_brier = []

    for train_idx, test_idx in skf.split(X_scaled, y):
        X_train, X_test = X_scaled[train_idx], X_scaled[test_idx]
        y_train, y_test = y[train_idx], y[test_idx]

        model = LogisticRegression(max_iter=1000, random_state=RANDOM_STATE)
        model.fit(X_train, y_train)

        y_pred = model.predict_proba(X_test)[:, 1]
        fold_auc.append(roc_auc_score(y_test, y_pred))
        fold_brier.append(brier_score_loss(y_test, y_pred))

        for i, col in enumerate(FEATURE_COLS):
            fold_coefs[col].append(model.coef_[0][i])

    stability = {}
    for col in FEATURE_COLS:
        coefs = fold_coefs[col]
        stability[col] = {
            "mean_coef": np.mean(coefs),
            "std_coef": np.std(coefs),
            "cv": np.std(coefs) / abs(np.mean(coefs)) if np.mean(coefs) != 0 else float('inf'),
            "consistent_sign": len(set(1 if c > 0 else -1 for c in coefs)) == 1,
        }

    return {
        "stability": stability,
        "mean_auc": np.mean(fold_auc),
        "std_auc": np.std(fold_auc),
        "mean_brier": np.mean(fold_brier),
        "std_brier": np.std(fold_brier),
    }


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS 3: PROBABILITY RECALIBRATION
# ══════════════════════════════════════════════════════════════════════════════

def recalibrate_confidence(df):
    """Map raw confidence scores to observed probabilities using isotonic regression."""
    conf = df["conf_norm"].values
    y = df["y"].values

    # Split for calibration validation
    conf_train, conf_test, y_train, y_test = train_test_split(
        conf, y, test_size=TEST_SIZE, random_state=RANDOM_STATE, stratify=y
    )

    # Fit isotonic regression (non-parametric, monotonic mapping)
    iso = IsotonicRegression(out_of_bounds='clip')
    iso.fit(conf_train, y_train)

    # Calibrated predictions
    y_cal_train = iso.predict(conf_train)
    y_cal_test = iso.predict(conf_test)

    # Raw predictions (current confidence)
    results = {
        "raw": {
            "train": {
                "brier": brier_score_loss(y_train, conf_train),
                "log_loss": log_loss(y_train, np.clip(conf_train, 1e-15, 1-1e-15)),
            },
            "test": {
                "brier": brier_score_loss(y_test, conf_test),
                "log_loss": log_loss(y_test, np.clip(conf_test, 1e-15, 1-1e-15)),
            },
        },
        "calibrated": {
            "train": {
                "brier": brier_score_loss(y_train, y_cal_train),
                "log_loss": log_loss(y_train, np.clip(y_cal_train, 1e-15, 1-1e-15)),
            },
            "test": {
                "brier": brier_score_loss(y_test, y_cal_test),
                "log_loss": log_loss(y_test, np.clip(y_cal_test, 1e-15, 1-1e-15)),
            },
        },
        "iso_model": iso,
    }

    # Build recalibration mapping table
    mapping = []
    for raw_val in np.arange(0.40, 1.01, 0.05):
        cal_val = iso.predict([raw_val])[0]
        mapping.append({
            "raw_confidence": raw_val,
            "calibrated_probability": cal_val,
            "adjustment": cal_val - raw_val,
        })

    results["mapping"] = mapping
    return results


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS 4: EXPECTANCY-MAXIMIZING THRESHOLD
# ══════════════════════════════════════════════════════════════════════════════

def find_optimal_threshold(df, recal_iso=None):
    """Find the threshold that maximizes expectancy on calibrated scores."""
    if recal_iso is not None:
        df = df.copy()
        df["calibrated_conf"] = recal_iso.predict(df["conf_norm"].values)
        score_col = "calibrated_conf"
    else:
        score_col = "conf_norm"

    thresholds = np.arange(0.30, 0.96, 0.025)
    results = []

    for thresh in thresholds:
        above = df[df[score_col] >= thresh]
        if len(above) < 5:
            continue

        n = len(above)
        wins = (above["outcome"] == "win").sum()
        wr = wins / n
        pnls = above["pnl"].values
        avg_pnl = pnls.mean()
        gw = pnls[pnls > 0].sum()
        gl = abs(pnls[pnls < 0].sum())
        pf = gw / gl if gl > 0 else (float('inf') if gw > 0 else 0)
        avg_win = pnls[pnls > 0].mean() if (pnls > 0).any() else 0
        avg_loss = abs(pnls[pnls < 0].mean()) if (pnls < 0).any() else 0
        exp = wr * avg_win - (1 - wr) * avg_loss

        results.append({
            "threshold": thresh,
            "trades": n,
            "win_rate": wr,
            "profit_factor": pf,
            "expectancy": exp,
            "avg_pnl": avg_pnl,
        })

    return pd.DataFrame(results)


# ══════════════════════════════════════════════════════════════════════════════
# REPORT GENERATION
# ══════════════════════════════════════════════════════════════════════════════

def generate_report(df, comp_results, cv_results, cal_results, thresh_results):
    """Generate markdown report."""
    lines = []
    lines.append("# Confidence Model Analysis Report")
    lines.append(f"\n**Generated:** {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Trades analyzed:** {len(df)}")
    lines.append(f"**Train/Test split:** {1-TEST_SIZE:.0%}/{TEST_SIZE:.0%}")
    lines.append("")

    # ── Executive Summary ──
    lines.append("## Executive Summary")
    lines.append("")
    raw_brier = cal_results["raw"]["test"]["brier"]
    cal_brier = cal_results["calibrated"]["test"]["brier"]
    brier_improvement = (raw_brier - cal_brier) / raw_brier * 100

    lines.append(f"| Metric | Raw Confidence | Recalibrated |")
    lines.append(f"|--------|---------------|--------------|")
    lines.append(f"| Brier Score (test) | {raw_brier:.4f} | {cal_brier:.4f} |")
    lines.append(f"| Log Loss (test) | {cal_results['raw']['test']['log_loss']:.4f} | {cal_results['calibrated']['test']['log_loss']:.4f} |")
    lines.append(f"| **Improvement** | — | **{brier_improvement:.1f}% better** |")
    lines.append("")

    # ── Component Analysis ──
    lines.append("## 1. Component Contribution (Logistic Regression)")
    lines.append("")
    lines.append("Standardized coefficients show independent contribution of each component.")
    lines.append("Positive = higher score → more likely to win. Negative = higher score → less likely to win.")
    lines.append("")
    lines.append("| Component | Coefficient | Direction | Odds Ratio | Importance Rank |")
    lines.append("|-----------|-------------|-----------|------------|-----------------|")

    for rank, (col, coef) in enumerate(comp_results["sorted_coefs"], 1):
        direction = "✅ Positive" if coef["direction"] == "positive" else "❌ Negative"
        lines.append(
            f"| {col.replace('_', ' ').title()} | {coef['coef']:+.4f} | {direction} | {coef['odds_ratio']:.3f} | #{rank} |"
        )

    lines.append("")
    lines.append(f"**Intercept:** {comp_results['intercept']:.4f}")
    lines.append("")

    # Model performance
    lines.append("### Model Performance")
    lines.append("")
    lines.append("| Dataset | AUC | Brier | Log Loss | N |")
    lines.append("|---------|-----|-------|----------|---|")
    for split in ["train", "test"]:
        r = comp_results["results"][split]
        lines.append(f"| {split.title()} | {r['auc']:.4f} | {r['brier']:.4f} | {r['log_loss']:.4f} | {r['n']} |")
    lines.append("")

    # ── Cross-Validation Stability ──
    lines.append("## 2. Component Stability (5-Fold Cross-Validation)")
    lines.append("")
    lines.append("Are component coefficients stable across different data splits?")
    lines.append("")
    lines.append("| Component | Mean Coef | Std | CV | Consistent Sign? |")
    lines.append("|-----------|-----------|-----|----|--------------------|")

    for col in FEATURE_COLS:
        s = cv_results["stability"][col]
        sign = "✅ Yes" if s["consistent_sign"] else "❌ No"
        cv_str = f"{s['cv']:.2f}" if s['cv'] != float('inf') else "∞"
        lines.append(
            f"| {col.replace('_', ' ').title()} | {s['mean_coef']:+.4f} | {s['std_coef']:.4f} | {cv_str} | {sign} |"
        )

    lines.append("")
    lines.append(f"**Cross-validated AUC:** {cv_results['mean_auc']:.4f} ± {cv_results['std_auc']:.4f}")
    lines.append(f"**Cross-validated Brier:** {cv_results['mean_brier']:.4f} ± {cv_results['std_brier']:.4f}")
    lines.append("")

    # Interpretation
    lines.append("### Interpretation")
    lines.append("")
    for col in FEATURE_COLS:
        coef = comp_results["coefficients"][col]
        stab = cv_results["stability"][col]
        name = col.replace("_", " ").title()

        if not stab["consistent_sign"]:
            lines.append(f"- **{name}:** ⚠️ Sign is inconsistent across folds — coefficient may be unreliable. Do not change weight without more data.")
        elif coef["direction"] == "positive" and coef["abs_coef"] > 0.1:
            lines.append(f"- **{name}:** ✅ Consistently positive predictor. Higher values → better outcomes.")
        elif coef["direction"] == "negative" and coef["abs_coef"] > 0.1:
            lines.append(f"- **{name}:** ❌ Consistently negative predictor. Higher values → worse outcomes. Consider inverting or reducing weight.")
        else:
            lines.append(f"- **{name}:** ⚪ Weak predictor (|coef| < 0.1). May not add meaningful value.")

    lines.append("")

    # ── Probability Recalibration ──
    lines.append("## 3. Probability Recalibration (Isotonic Regression)")
    lines.append("")
    lines.append("Non-parametric mapping from raw confidence to observed win probability.")
    lines.append("")
    lines.append("| Raw Confidence | Calibrated Probability | Adjustment |")
    lines.append("|----------------|------------------------|------------|")

    for row in cal_results["mapping"]:
        lines.append(
            f"| {row['raw_confidence']*100:.0f}% | {row['calibrated_probability']*100:.1f}% | {row['adjustment']*100:+.1f}% |"
        )

    lines.append("")

    # ── Optimal Threshold ──
    lines.append("## 4. Expectancy-Maximizing Threshold")
    lines.append("")
    lines.append("Using recalibrated confidence scores:")
    lines.append("")
    lines.append("| Threshold | Trades | Win Rate | Profit Factor | Expectancy | Avg PnL |")
    lines.append("|-----------|--------|----------|---------------|------------|---------|")

    for _, r in thresh_results.iterrows():
        pf_str = f"{r['profit_factor']:.2f}" if r['profit_factor'] != float('inf') else "∞"
        lines.append(
            f"| ≥{r['threshold']*100:.1f}% | {r['trades']:.0f} | {r['win_rate']*100:.1f}% | {pf_str} | ${r['expectancy']:.2f} | ${r['avg_pnl']:.2f} |"
        )

    # Find optimal
    if not thresh_results.empty:
        best = thresh_results.loc[thresh_results["expectancy"].idxmax()]
        lines.append("")
        lines.append(f"**Optimal threshold:** ≥{best['threshold']*100:.1f}% — "
                     f"{best['trades']:.0f} trades, WR {best['win_rate']*100:.1f}%, "
                     f"PF {best['profit_factor']:.2f}, Exp ${best['expectancy']:.2f}")

    lines.append("")

    # ── Recommendations ──
    lines.append("## 5. Recommendations")
    lines.append("")
    lines.append("### Evidence-Based Actions (ordered by confidence)")
    lines.append("")

    # Determine recommendations based on evidence
    rec_idx = 1
    for col in FEATURE_COLS:
        coef = comp_results["coefficients"][col]
        stab = cv_results["stability"][col]
        name = col.replace("_", " ").title()

        if stab["consistent_sign"] and coef["abs_coef"] > 0.1:
            if coef["direction"] == "negative":
                lines.append(f"{rec_idx}. **Reduce {name} weight** — Consistently predicts worse outcomes (coef={coef['coef']:+.4f}, stable across CV folds).")
                rec_idx += 1
            elif coef["direction"] == "positive":
                lines.append(f"{rec_idx}. **Increase {name} weight** — Consistently predicts better outcomes (coef={coef['coef']:+.4f}, stable across CV folds).")
                rec_idx += 1

    # Recalibration recommendation
    if brier_improvement > 10:
        lines.append(f"{rec_idx}. **Apply isotonic recalibration** — Reduces Brier score by {brier_improvement:.1f}% on test data. Preserves ranking while fixing probability scale.")
        rec_idx += 1

    lines.append("")
    lines.append("### What NOT to do")
    lines.append("")
    lines.append("- Do not change weights based on univariate analysis alone.")
    lines.append("- Do not lower the confidence threshold without validating on hold-out data.")
    lines.append("- Do not interpret raw confidence as probability — use the recalibration mapping.")
    lines.append("")
    lines.append("### Next Steps")
    lines.append("")
    lines.append("1. Validate these findings on the next 100+ trades (out-of-sample).")
    lines.append("2. If component findings hold, adjust weights in `confidence_engine.py`.")
    lines.append("3. Implement isotonic recalibration as a post-processing step.")
    lines.append("4. A/B test: current model vs recalibrated model with position sizing.")
    lines.append("")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    output_path = Path(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[1] == "--output" else OUTPUT_PATH

    print("Loading trades...")
    df = load_trades()
    print(f"  Loaded {len(df)} trades")

    print("Preparing features...")
    df = prepare_features(df)
    print(f"  {len(df)} trades with component scores")

    print("Running multivariate component analysis...")
    comp_results = analyze_components(df)
    print(f"  Train AUC: {comp_results['results']['train']['auc']:.4f}")
    print(f"  Test AUC:  {comp_results['results']['test']['auc']:.4f}")

    print("Running cross-validation stability analysis...")
    cv_results = cross_validate_model(df)
    print(f"  CV AUC: {cv_results['mean_auc']:.4f} ± {cv_results['std_auc']:.4f}")

    print("Running probability recalibration...")
    cal_results = recalibrate_confidence(df)
    raw_brier = cal_results["raw"]["test"]["brier"]
    cal_brier = cal_results["calibrated"]["test"]["brier"]
    print(f"  Raw Brier:       {raw_brier:.4f}")
    print(f"  Calibrated Brier: {cal_brier:.4f}")
    print(f"  Improvement:     {(raw_brier - cal_brier) / raw_brier * 100:.1f}%")

    print("Finding optimal threshold...")
    thresh_results = find_optimal_threshold(df, cal_results["iso_model"])
    if not thresh_results.empty:
        best = thresh_results.loc[thresh_results["expectancy"].idxmax()]
        print(f"  Optimal: ≥{best['threshold']*100:.1f}% (Exp ${best['expectancy']:.2f}, PF {best['profit_factor']:.2f})")

    print("Generating report...")
    report = generate_report(df, comp_results, cv_results, cal_results, thresh_results)
    output_path.write_text(report)
    print(f"  Report saved to: {output_path}")

    print("\n✅ Analysis complete.")


if __name__ == "__main__":
    main()
