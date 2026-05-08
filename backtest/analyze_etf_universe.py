"""
기존 백테스트 거래 CSV를 기반으로 ETF/비ETF 성과를 비교 분석한다.

사용 예시:
    poetry run python -m backtest.analyze_etf_universe
    poetry run python -m backtest.analyze_etf_universe --pattern "trades_202605*.csv"
    poetry run python -m backtest.analyze_etf_universe --mode auto --whitelist-codes "229200,381180"
"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

ETF_NAME_PREFIXES = (
    "KODEX",
    "TIGER",
    "ARIRANG",
    "KOSEF",
    "KBSTAR",
    "HANARO",
    "SOL",
    "ACE",
    "TIMEFOLIO",
    "PLUS",
    "RISE",
    "TREX",
    "FOCUS",
    "KIWOOM",
)


def is_etf_name(name: str) -> bool:
    if not isinstance(name, str):
        return False
    s = name.strip().upper()
    return s.startswith(ETF_NAME_PREFIXES) or (" ETF" in s) or s.endswith("ETF")


def parse_csv_set(value: str) -> set[str]:
    if not value:
        return set()
    return {x.strip() for x in value.split(",") if x and x.strip()}


@dataclass
class Policy:
    mode: str
    whitelist_codes: set[str]
    whitelist_names: set[str]


def apply_policy(df: pd.DataFrame, policy: Policy) -> pd.DataFrame:
    mode = policy.mode
    out = df.copy()
    out["is_etf"] = out["name"].map(is_etf_name)

    if mode == "all":
        return out
    if mode == "exclude":
        return out.loc[~out["is_etf"]].copy()
    if mode == "only":
        return out.loc[out["is_etf"]].copy()

    # auto: ETF는 whitelist만 유지
    keep = (~out["is_etf"]).to_numpy()
    if policy.whitelist_codes:
        keep = keep | out["code"].astype(str).isin(policy.whitelist_codes).to_numpy()
    if policy.whitelist_names:
        keep = keep | out["name"].astype(str).isin(policy.whitelist_names).to_numpy()
    return out.loc[keep].copy()


def load_master_list(master_db: Path) -> pd.DataFrame:
    conn = sqlite3.connect(master_db)
    try:
        master = pd.read_sql_query("SELECT code, name FROM master_list", conn)
    finally:
        conn.close()
    master["code"] = master["code"].astype(str).str.zfill(6)
    return master


def load_trades(files: Iterable[Path]) -> pd.DataFrame:
    frames = []
    for file in files:
        df = pd.read_csv(file)
        df["source_file"] = file.name
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out["code"] = out["code"].astype(str).str.zfill(6)
    return out


def summarize_by_group(df: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        df.groupby("is_etf", as_index=False)
        .agg(
            sell_trades=("profit_rate", "count"),
            avg_profit_rate=("profit_rate", "mean"),
            median_profit_rate=("profit_rate", "median"),
            win_rate_pct=("profit_rate", lambda s: (s > 0).mean() * 100),
            stop_loss_rate_5_pct=("profit_rate", lambda s: (s <= -5.0).mean() * 100),
            total_profit=("profit", "sum"),
            unique_codes=("code", "nunique"),
        )
        .sort_values("is_etf")
    )
    return grouped


def summarize_etf_symbols(df: pd.DataFrame) -> pd.DataFrame:
    etf = df.loc[df["is_etf"]].copy()
    if etf.empty:
        return pd.DataFrame()
    out = (
        etf.groupby(["code", "name"], as_index=False)
        .agg(
            sell_trades=("profit_rate", "count"),
            avg_profit_rate=("profit_rate", "mean"),
            win_rate_pct=("profit_rate", lambda s: (s > 0).mean() * 100),
            stop_loss_rate_5_pct=("profit_rate", lambda s: (s <= -5.0).mean() * 100),
            total_profit=("profit", "sum"),
        )
        .sort_values(["sell_trades", "total_profit"], ascending=[False, True])
    )
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="ETF 유니버스 판단용 백테스트 거래 분석")
    p.add_argument("--output-dir", default="backtest/output", help="거래 CSV 디렉토리")
    p.add_argument("--pattern", default="trades_202605*.csv", help="분석할 거래 CSV 패턴")
    p.add_argument("--master-db", default="data/master_list.db", help="종목명 매핑 DB 경로")
    p.add_argument("--mode", choices=["all", "exclude", "only", "auto"], default="all")
    p.add_argument("--whitelist-codes", default="", help="auto 모드 유지 ETF 코드 CSV")
    p.add_argument("--whitelist-names", default="", help="auto 모드 유지 ETF 이름 CSV")
    p.add_argument("--save-prefix", default="etf_universe_analysis", help="출력 파일 prefix")
    return p


def main() -> int:
    args = build_parser().parse_args()

    output_dir = Path(args.output_dir)
    files = sorted(output_dir.glob(args.pattern))
    if not files:
        print(f"거래 파일이 없습니다: pattern={args.pattern}")
        return 1

    master = load_master_list(Path(args.master_db))
    trades = load_trades(files)
    if trades.empty:
        print("거래 데이터가 비어 있습니다.")
        return 1

    merged = trades.merge(master, on="code", how="left")
    sells = merged.loc[(merged["type"] == "sell") & merged["profit_rate"].notna()].copy()
    if sells.empty:
        print("매도 거래가 없어 분석할 수 없습니다.")
        return 1

    policy = Policy(
        mode=args.mode,
        whitelist_codes=parse_csv_set(args.whitelist_codes),
        whitelist_names=parse_csv_set(args.whitelist_names),
    )
    applied = apply_policy(sells, policy)

    by_group = summarize_by_group(applied)
    by_symbol = summarize_etf_symbols(applied)

    # 결과 저장
    prefix = f"{args.save_prefix}_{args.mode}"
    group_csv = output_dir / f"{prefix}_group.csv"
    symbol_csv = output_dir / f"{prefix}_etf_symbols.csv"
    by_group.to_csv(group_csv, index=False)
    by_symbol.to_csv(symbol_csv, index=False)

    print(f"파일 개수: {len(files)}")
    print(f"분석 모드: {args.mode}")
    print("\n[ETF vs 비ETF]")
    print(by_group.to_string(index=False))
    print("\n저장 완료:")
    print(group_csv)
    print(symbol_csv)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
