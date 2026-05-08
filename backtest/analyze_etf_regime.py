"""
기간(연도/분기)별 ETF 성과를 비교하고 auto whitelist 후보를 평가한다.

사용 예시:
    poetry run python -m backtest.analyze_etf_regime
    poetry run python -m backtest.analyze_etf_regime --freq quarterly
    poetry run python -m backtest.analyze_etf_regime --freq yearly --pattern "trades_2026*.csv"
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from backtest.analyze_etf_universe import (
    Policy,
    apply_policy,
    load_master_list,
    load_trades,
    summarize_etf_symbols,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="기간별 ETF 성과 분석")
    p.add_argument("--output-dir", default="backtest/output", help="거래 CSV 디렉토리")
    p.add_argument("--pattern", default="trades_202605*.csv", help="분석할 거래 CSV 패턴")
    p.add_argument("--master-db", default="data/master_list.db", help="종목명 매핑 DB 경로")
    p.add_argument("--freq", choices=["quarterly", "yearly"], default="quarterly", help="집계 주기")
    p.add_argument(
        "--base-whitelist-codes",
        default="229200,381180",
        help="기준 whitelist ETF 코드 CSV (시나리오 비교용)",
    )
    p.add_argument("--candidate-min-sell-trades", type=int, default=10, help="자동 후보 최소 매도 거래 수")
    p.add_argument(
        "--candidate-min-avg-profit-rate",
        type=float,
        default=0.0,
        help="자동 후보 최소 평균 수익률(%)",
    )
    p.add_argument(
        "--candidate-max-stop-loss-rate-5",
        type=float,
        default=15.0,
        help="자동 후보 최대 손절 비중(%, profit_rate<=-5)",
    )
    p.add_argument("--save-prefix", default="etf_regime_analysis", help="출력 파일 prefix")
    return p


def period_series(df: pd.DataFrame, freq: str) -> pd.Series:
    dt = pd.to_datetime(df["date"].astype(str), format="%Y%m%d", errors="coerce")
    if freq == "yearly":
        return dt.dt.to_period("Y").astype(str)
    return dt.dt.to_period("Q").astype(str)


def summarize_period(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    out = df.copy()
    out["period"] = period_series(out, freq)
    out = out[out["period"].notna()].copy()

    grouped = (
        out.groupby(["period", "is_etf"], as_index=False)
        .agg(
            sell_trades=("profit_rate", "count"),
            avg_profit_rate=("profit_rate", "mean"),
            win_rate_pct=("profit_rate", lambda s: (s > 0).mean() * 100),
            stop_loss_rate_5_pct=("profit_rate", lambda s: (s <= -5.0).mean() * 100),
            total_profit=("profit", "sum"),
        )
        .sort_values(["period", "is_etf"])
    )
    return grouped


def evaluate_whitelist_scenarios(
    sells: pd.DataFrame, freq: str, scenarios: dict[str, list[str]]
) -> pd.DataFrame:
    rows = []
    for name, codes in scenarios.items():
        applied = apply_policy(sells, Policy(mode="auto", whitelist_codes=set(codes), whitelist_names=set()))
        applied["period"] = period_series(applied, freq)
        applied = applied[applied["period"].notna()].copy()

        for period, chunk in applied.groupby("period"):
            rows.append(
                {
                    "scenario": name,
                    "codes": ",".join(codes),
                    "period": period,
                    "sell_trades": int(len(chunk)),
                    "avg_profit_rate": float(chunk["profit_rate"].mean()),
                    "stop_loss_rate_5_pct": float((chunk["profit_rate"] <= -5.0).mean() * 100),
                    "total_profit": float(chunk["profit"].sum()),
                }
            )

    return pd.DataFrame(rows).sort_values(["period", "total_profit"], ascending=[True, False])


def main() -> int:
    args = build_parser().parse_args()

    output_dir = Path(args.output_dir)
    files = sorted(output_dir.glob(args.pattern))
    if not files:
        print(f"거래 파일이 없습니다: pattern={args.pattern}")
        return 1

    master = load_master_list(Path(args.master_db))
    trades = load_trades(files)
    merged = trades.merge(master, on="code", how="left")
    sells = merged[(merged["type"] == "sell") & merged["profit_rate"].notna()].copy()
    if sells.empty:
        print("매도 데이터가 없습니다.")
        return 1

    base = apply_policy(sells, Policy(mode="all", whitelist_codes=set(), whitelist_names=set()))
    period_group = summarize_period(base, args.freq)

    base_codes = [c.strip() for c in str(args.base_whitelist_codes).split(",") if c.strip()]

    etf_symbols = summarize_etf_symbols(base)
    candidate = etf_symbols[
        (etf_symbols["sell_trades"] >= args.candidate_min_sell_trades)
        & (etf_symbols["avg_profit_rate"] > args.candidate_min_avg_profit_rate)
        & (etf_symbols["stop_loss_rate_5_pct"] <= args.candidate_max_stop_loss_rate_5)
    ].copy()
    candidate_codes = candidate["code"].astype(str).tolist()

    scenarios = {
        "base_whitelist": base_codes,
        "candidate_auto": candidate_codes,
    }
    period_scenario = evaluate_whitelist_scenarios(sells, args.freq, scenarios)

    prefix = f"{args.save_prefix}_{args.freq}"
    group_csv = output_dir / f"{prefix}_group.csv"
    candidate_csv = output_dir / f"{prefix}_candidate_symbols.csv"
    scenario_csv = output_dir / f"{prefix}_scenario_compare.csv"
    report_md = output_dir / f"{prefix}_report.md"

    period_group.to_csv(group_csv, index=False)
    candidate.to_csv(candidate_csv, index=False)
    period_scenario.to_csv(scenario_csv, index=False)

    with report_md.open("w", encoding="utf-8") as f:
        f.write(f"# ETF 기간별 분석 ({args.freq})\n\n")
        f.write(f"- 대상 파일 수: {len(files)}\n")
        f.write(f"- 패턴: {args.pattern}\n")
        f.write("- 손절 비중 기준: 실현손익률 <= -5%\n\n")
        f.write("## 실행 파라미터\n\n")
        f.write(f"- base_whitelist_codes: {','.join(base_codes) if base_codes else '(none)'}\n")
        f.write(f"- candidate_min_sell_trades: {args.candidate_min_sell_trades}\n")
        f.write(f"- candidate_min_avg_profit_rate: {args.candidate_min_avg_profit_rate}\n")
        f.write(f"- candidate_max_stop_loss_rate_5: {args.candidate_max_stop_loss_rate_5}\n\n")

        f.write("## 기간별 ETF vs 비ETF\n\n")
        f.write(period_group.to_markdown(index=False))
        f.write("\n\n## ETF 후보(자동 추출)\n\n")
        if candidate.empty:
            f.write("- 후보 없음\n")
        else:
            f.write(candidate.to_markdown(index=False))

        f.write("\n\n## 시나리오 비교\n\n")
        f.write(period_scenario.to_markdown(index=False))

    print("Saved:")
    print(group_csv)
    print(candidate_csv)
    print(scenario_csv)
    print(report_md)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
