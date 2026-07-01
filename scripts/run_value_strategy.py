#!/usr/bin/env python3
"""Value Strategy (PBR ranking-based) — Standalone One-Shot Execution

Fetches PBR data via pykrx (previous trading day), ranks all stocks by PBR
ascending, applies market cap filter, and places buy orders on the top N
stocks through the Kiwoom REST API.

Designed for GitHub Actions cron scheduling — runs once, then exits.

Usage:
    poetry run python scripts/run_value_strategy.py                        # dry-run (default)
    poetry run python scripts/run_value_strategy.py --no-dry-run           # live orders (mock)
    KIWOOM_MODE=mock poetry run python scripts/run_value_strategy.py --no-dry-run
    KIWOOM_MODE=real KIWOOM_REAL_APPKEY=... KIWOOM_REAL_SECRETKEY=... poetry run python scripts/run_value_strategy.py

Environment variables:
    KIWOOM_MODE                 mock (default) or real
    VALUE_HOLDINGS              10 (default)
    VALUE_MAX_BUDGET            0 = unlimited (default)
    VALUE_MIN_MARKET_CAP        minimum market cap in won (0 = auto bottom 10%)
    VALUE_MARKET_FILTER         true (default) or false
    VALUE_KEEP_HOLDINGS         false (default) or true
    TELEGRAM_BOT_TOKEN          Telegram bot token
    TELEGRAM_CHAT_ID            Telegram chat ID

IMPORTANT: PBR data from pykrx is from the PREVIOUS trading day.
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("value_strategy")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env_bool(key: str, default: str = "false") -> bool:
    return os.getenv(key, default).strip().lower() in ("1", "true", "yes")


def _env_int(key: str, default: str) -> int:
    try:
        return int(os.getenv(key, default))
    except (ValueError, TypeError):
        return int(default)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_telegram(message: str) -> bool:
    try:
        from util.notifier import send_telegram_message
        return send_telegram_message(message)
    except Exception:
        logger.info("[Telegram fallback] %s", message)
        return False


# ---------------------------------------------------------------------------
# Naver stock screener data fetcher (primary)
# ---------------------------------------------------------------------------

NAVER_BASE = "https://finance.naver.com/sise/sise_market_sum.nhn?sosok="
NAVER_POST = "https://finance.naver.com/sise/field_submit.nhn"
NAVER_FIELDS = ["per", "pbr", "eps", "dividend", "market_sum"]


def fetch_naver_stock_data() -> dict[str, dict[str, float | int]]:
    """Fetch PBR/PER/EPS/BPS/DIV/market_cap from Naver stock screener.

    Handles KOSPI + KOSDAQ, pagination, column-name-based field mapping.
    Returns the same dict format as ``fetch_fundamental()`` but with an
    additional ``market_cap`` key per stock.
    """
    import numpy as np
    import requests
    from bs4 import BeautifulSoup

    logger.info("Fetching Naver stock data (PBR/PER/EPS/BPS/market_cap) …")

    all_data: dict[str, dict[str, float | int]] = {}
    session = requests.Session()

    # We request just enough fields to cover our needs. Naver includes extra
    # base columns (현재가, 전일비, 등락률 etc.) that do not map 1:1 to
    # NAVER_FIELDS — we match by column header text below.
    fields = NAVER_FIELDS

    for sosok in (0, 1):
        market_name = "KOSPI" if sosok == 0 else "KOSDAQ"

        # GET first page → parse total pages + available field IDs
        try:
            r0 = session.get(NAVER_BASE + str(sosok), timeout=15)
            r0.encoding = "euc-kr"
            soup0 = BeautifulSoup(r0.text, "lxml")
        except Exception as e:
            logger.warning("  %s first page failed (%s) — skipped", market_name, e)
            continue

        # Total page count from the "맨뒤" (last-page) link
        total_pages = 1
        pgRR = soup0.select_one("td.pgRR > a")
        if pgRR:
            href = pgRR.get("href", "")
            if "page=" in href:
                total_pages = int(href.split("page=")[-1].split("&")[0])

        # Dynamically discover field IDs (including PER/PBR etc.)
        ipt_div = soup0.select_one("div.subcnt_sise_item_top")
        if ipt_div:
            available = [i.get("value") for i in ipt_div.select("input")]
            fields = [f for f in NAVER_FIELDS if f in available] or NAVER_FIELDS

        logger.info("  %s: %d pages, %d fields", market_name, total_pages, len(fields))

        for page in range(1, total_pages + 1):
            post_data = {
                "menu": "market_sum",
                "fieldIds": fields,
                "returnUrl": f"{NAVER_BASE}{sosok}&page={page}",
            }
            try:
                r = session.post(NAVER_POST, data=post_data, timeout=15)
                r.encoding = "euc-kr"
            except Exception as e:
                logger.warning("  Page %d/%s failed (%s)", page, market_name, e)
                continue

            soup = BeautifulSoup(r.text, "lxml")
            table = soup.select_one("div.box_type_l")
            if not table:
                continue

            # Column headers: skip first (순위) and last (기타)
            ths = table.select("thead th")
            header_data = [th.get_text().strip() for th in ths][1:-1]
            if not header_data:
                continue

            # Stock codes from <a class="tltle"> href
            codes: list[str] = []
            for a in table.select("a.tltle"):
                href = a.get("href", "")
                if "code=" in href:
                    codes.append(href.split("code=")[1].split("&")[0].zfill(6))
                else:
                    codes.append("")

            # Flat list: [stock_name, td_val1, td_val2, …, stock_name, …]
            inner_data = [
                item.get_text().strip()
                for item in table.find_all(
                    lambda x: (x.name == "a" and "tltle" in x.get("class", []))
                    or (x.name == "td" and "number" in x.get("class", []))
                )
            ]

            no_count = len(table.select("td.no"))
            if no_count == 0:
                continue
            if len(inner_data) != no_count * len(header_data):
                continue  # malformed page

            arr = np.array(inner_data).reshape(no_count, len(header_data))

            # Map Korean column names to our internal field keys
            # header_data sample (fieldIds=per,pbr,eps,dividend,market_sum):
            # ['종목명','현재가','전일비','등락률','액면가','시가총액','주당순이익','보통주배당금','PER','PBR']
            COL_MAP = {
                "시가총액": "market_cap_raw",
                "주당순이익": "EPS",
                "보통주배당금": "div_per_share",
                "PER": "PER",
                "PBR": "PBR",
                "현재가": "cur_price",
            }
            col_idx: dict[str, int] = {}
            for j, col in enumerate(header_data):
                mapped = COL_MAP.get(col)
                if mapped:
                    col_idx[mapped] = j

            def _num(row_i: int, key: str, default: float = 0.0) -> float:
                idx = col_idx.get(key)
                if idx is None:
                    return default
                try:
                    raw = arr[row_i][idx]
                    # Remove non-numeric chars except . , -
                    import re
                    cleaned = re.sub(r"[^0-9.,\-]", "", raw).strip()
                    cleaned = cleaned.replace(",", "")
                    if not cleaned or cleaned in ("-",):
                        return default
                    return float(cleaned)
                except (ValueError, IndexError):
                    return default

            for i in range(no_count):
                code = codes[i] if i < len(codes) else ""
                if not code:
                    continue

                pbr = _num(i, "PBR")
                if pbr <= 0:
                    continue

                raw_mcap = _num(i, "market_cap_raw")  # 억(10⁸) 단위
                market_cap = int(raw_mcap * 100_000_000)

                eps = _num(i, "EPS")
                div_ps = _num(i, "div_per_share")
                cur_prc = _num(i, "cur_price")
                div_pct = (div_ps / cur_prc * 100) if cur_prc > 0 and div_ps > 0 else 0.0

                all_data[code] = {
                    "PBR": pbr,
                    "PER": _num(i, "PER", float("inf")),
                    "EPS": eps,
                    "BPS": 0.0,  # not available from Naver screener
                    "DIV": div_pct,
                    "market_cap": market_cap,
                }

    logger.info("Naver stock data: %d stocks", len(all_data))
    return all_data


# ---------------------------------------------------------------------------
# pykrx data fetchers (fallback / supplementary)
# ---------------------------------------------------------------------------

def fetch_fundamental(today: str | None = None) -> dict[str, dict[str, float]]:
    """PER/PBR/EPS/BPS/DIV for ALL stocks via pykrx."""
    if today is None:
        today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    try:
        from pykrx import stock as krx_stock
        df = krx_stock.get_market_fundamental_by_ticker(today, market="ALL")
    except Exception as e:
        logger.error("pykrx fundamental fetch failed (%s): %s", today, e)
        return {}

    result: dict[str, dict[str, float]] = {}
    for code, row in df.iterrows():
        code = str(code).zfill(6)
        try:
            result[code] = {
                "PER": float(row["PER"]) if row["PER"] > 0 else float("inf"),
                "PBR": float(row["PBR"]) if row["PBR"] > 0 else float("inf"),
                "EPS": float(row["EPS"]),
                "BPS": float(row["BPS"]),
                "DIV": float(row["DIV"]),
            }
        except (ValueError, TypeError, KeyError):
            continue
    logger.info("pykrx fundamental: %d stocks (%s)", len(result), today)
    return result


def fetch_market_caps(today: str | None = None) -> dict[str, int]:
    """Market cap in won for ALL stocks via pykrx."""
    if today is None:
        today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    try:
        from pykrx import stock as krx_stock
        df = krx_stock.get_market_cap_by_ticker(today)
    except Exception as e:
        logger.warning("pykrx market cap fetch failed (%s): %s — cap filter skipped", today, e)
        return {}

    result: dict[str, int] = {}
    for code, row in df.iterrows():
        code = str(code).zfill(6)
        try:
            result[code] = int(float(row["시가총액"]) * 100_000_000)
        except (ValueError, TypeError, KeyError):
            continue
    logger.info("pykrx market cap: %d stocks", len(result))
    return result


def resolve_name(code: str) -> str:
    try:
        from pykrx import stock as krx_stock
        return krx_stock.get_market_ticker_name(code)
    except Exception:
        return ""


def check_market_filter(date_str: str | None = None) -> bool:
    """KOSPI200 (069500) > MA200 using pykrx OHLCV."""
    if not _env_bool("VALUE_MARKET_FILTER", "true"):
        logger.info("Market filter disabled via VALUE_MARKET_FILTER=false")
        return True

    try:
        from pykrx import stock as krx_stock
        today = datetime.now(ZoneInfo("Asia/Seoul"))
        from_date = (today - timedelta(days=300)).strftime("%Y%m%d")
        to_date = today.strftime("%Y%m%d")
        df = krx_stock.get_market_ohlcv_by_date(from_date, to_date, "069500")

        if df is None or len(df) < 200:
            logger.warning("069500 data insufficient (%d rows) — filter bypassed", len(df) if df is not None else 0)
            return True

        closes = df["종가"].values.astype(float)
        cur = closes[-1]
        ma200 = closes[-200:].mean()
        ok = cur > ma200
        logger.info("Market filter: KOSPI200=%.0f MA200=%.0f → %s", cur, ma200, "BULL" if ok else "BEAR")
        return ok
    except Exception as e:
        logger.warning("Market filter error (%s) — bypassed", e)
        return True


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def run() -> None:
    parser = argparse.ArgumentParser(description="Value Strategy — PBR ranking rebalancing")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", default=True,
                        help="Print ranking only (default: true)")
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false",
                        help="Execute real buy orders")
    args = parser.parse_args()

    dry_run = args.dry_run
    mode = os.getenv("KIWOOM_MODE", "mock").strip().lower()
    is_mock = mode == "mock"

    value_holdings = _env_int("VALUE_HOLDINGS", "10")
    value_max_budget = _env_int("VALUE_MAX_BUDGET", "0")
    value_min_market_cap = _env_int("VALUE_MIN_MARKET_CAP", "0")
    value_market_filter_only_kospi = _env_bool("VALUE_MARKET_FILTER_ONLY_KOSPI", "false")
    value_keep_holdings = _env_bool("VALUE_KEEP_HOLDINGS", "false")

    logger.info("=" * 70)
    logger.info("  Value Strategy — Standalone One-Shot Execution")
    logger.info("  %s", datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S %Z"))
    logger.info("  Mode: %s  |  Dry-run: %s", "REAL" if not is_mock else "MOCK", dry_run)
    logger.info("  PBR/시총 data: Naver stock screener (실시간)")
    logger.info("  Market filter: pykrx KOSPI200 MA200")
    logger.info("  VALUE_HOLDINGS=%d  VALUE_MAX_BUDGET=%d  MIN_MCAP=%d",
                value_holdings, value_max_budget, value_min_market_cap)
    logger.info("=" * 70)

    # ---- 1. Market filter ----
    logger.info("[1/7] Market filter (KOSPI200 MA200) …")
    if not check_market_filter():
        msg = "⚠️ Value Strategy: KOSPI200 < MA200 (bear) — rebalancing skipped"
        logger.warning(msg)
        send_telegram(msg)
        return

    # ---- 2. PBR / market cap from Naver (primary) ----
    logger.info("[2/7] Fetching stock data from Naver …")
    stock_data = fetch_naver_stock_data()
    if not stock_data:
        logger.warning("Naver fetch failed — falling back to pykrx")
        logger.info("[2b/7] Fetching fundamental data (pykrx fallback) …")
        fundamental = fetch_fundamental()
        if not fundamental:
            send_telegram("❌ Value Strategy: no fundamental data — aborting")
            return
        caps = fetch_market_caps()
        logger.info("  pykrx fallback: %d stocks with PBR", len(fundamental))
    else:
        fundamental = {}
        caps: dict[str, int] = {}
        for code, d in stock_data.items():
            fundamental[code] = {k: d[k] for k in ("PBR", "PER", "EPS", "BPS", "DIV")}
            caps[code] = d.get("market_cap", 0) or 0
        logger.info("  Naver: %d stocks with PBR + market cap", len(stock_data))

    # ---- 3. Filter & rank ----
    logger.info("[3/6] Filtering and ranking by PBR …")

    candidates: list[tuple[str, float, int]] = []
    for code, fund in fundamental.items():
        pbr = fund.get("PBR", float("inf"))
        if pbr <= 0 or pbr == float("inf"):
            continue
        cap = caps.get(code, 0)
        if caps and code not in caps:
            continue  # skip stocks not in cap data (delisted / suspended)
        if value_min_market_cap > 0 and cap < value_min_market_cap:
            continue
        candidates.append((code, pbr, cap))

    # Auto bottom-10 % cap removal
    if caps and value_min_market_cap == 0:
        positive_caps = sorted({c[2] for c in candidates if c[2] > 0})
        if positive_caps:
            threshold = positive_caps[max(0, len(positive_caps) // 10)]
            before = len(candidates)
            candidates = [c for c in candidates if c[2] >= threshold]
            logger.info("  Bottom 10%% cap removed: %d → %d (threshold=%s won)", before, len(candidates), f"{threshold:,}")

    if value_market_filter_only_kospi:
        logger.info("  KOSPI-only filter requested (filtering handled by Naver data)")

    candidates.sort(key=lambda x: x[1])  # ascending PBR

    if not candidates:
        logger.warning("  No candidates after filtering")
        send_telegram("⚠️ Value Strategy: no candidates — aborting")
        return

    targets = candidates[:value_holdings]

    # Print ranking table
    logger.info("")
    logger.info("  %-4s %-8s %-20s %-8s %-8s %s", "Rank", "Code", "Name", "PBR", "PER", "MarketCap")
    logger.info("  " + "-" * 70)
    lines: list[str] = []
    for i, (code, pbr, cap) in enumerate(targets, 1):
        name = resolve_name(code) or code
        per = fundamental[code].get("PER", float("inf"))
        per_s = f"{per:.2f}" if per != float("inf") else "N/A"
        cap_s = f"{cap / 1e8:.1f}억" if cap else "N/A"
        logger.info("  %-4d %-8s %-20s %-8.2f %-8s %s", i, code, name, pbr, per_s, cap_s)
        lines.append(f"{i}. {name}({code}) PBR={pbr:.2f}")
    logger.info("  " + "-" * 70)

    # Dry-run: notify and exit
    if dry_run:
        logger.info("  DRY-RUN — no orders placed")
        send_telegram(
            f"📋 <b>Value Strategy (Dry-Run)</b>\n"
            + "\n".join(lines)
        )
        return

    # ---- 4. Safety gate for REAL mode ----
    logger.info("[4/6] Safety check …")
    if not is_mock:
        logger.warning("=" * 70)
        logger.warning("  ⚠️  WARNING: REAL TRADING MODE — about to place REAL buy orders!")
        logger.warning("  Press Ctrl+C within 10 seconds to abort.")
        logger.warning("=" * 70)
        for s in range(10, 0, -1):
            logger.warning("  Proceeding in %d …", s)
            time.sleep(1)
        logger.warning("  Proceeding with real orders …")

    # ---- 5. Kiwoom auth ----
    logger.info("[5/6] Initializing Kiwoom API …")
    if is_mock:
        appkey = os.environ.get("KIWOOM_MOCK_APPKEY") or os.environ.get("KIWOOM_APPKEY")
        secretkey = os.environ.get("KIWOOM_MOCK_SECRETKEY") or os.environ.get("KIWOOM_SECRETKEY")
    else:
        appkey = os.environ.get("KIWOOM_REAL_APPKEY")
        secretkey = os.environ.get("KIWOOM_REAL_SECRETKEY")

    if not appkey or not secretkey:
        logger.error("API keys missing for %s mode", mode)
        send_telegram(f"❌ Value Strategy: API keys missing for {mode}")
        return

    try:
        from api.Kiwoom import Kiwoom
        kiwoom = Kiwoom(appkey=appkey, secretkey=secretkey, mock=is_mock)
    except Exception as e:
        logger.exception("Kiwoom init failed")
        send_telegram(f"❌ Value Strategy: Kiwoom init error — {e}")
        return

    # ---- 6. Balance & deposit ----
    logger.info("[6/6] Checking balance and placing orders …")
    try:
        kiwoom.get_balance()
        holdings = set(kiwoom.balance.keys())
        logger.info("  Current holdings: %d stocks", len(holdings))
    except Exception as e:
        logger.exception("Balance check failed")
        send_telegram(f"❌ Value Strategy: balance error — {e}")
        return

    try:
        deposit = kiwoom.get_deposit()
        logger.info("  Available deposit: %s won", f"{deposit:,}")
    except Exception as e:
        logger.exception("Deposit check failed")
        send_telegram(f"❌ Value Strategy: deposit error — {e}")
        return

    # Duplicate prevention: check today's executed buy orders
    today_bought: set[str] = set()
    try:
        orders = kiwoom.get_order()
        for o in orders:
            if o.get("주문구분") in ("매수", "2") and o.get("체결량", 0) > 0:
                today_bought.add(o["종목코드"])
        if today_bought:
            logger.info("  Today's executed buys: %d stocks — skipping", len(today_bought))
    except Exception:
        logger.warning("  Could not check today's orders — proceeding anyway")

    target_codes = {code for code, _, _ in targets}

    # Sell holdings not in target (only if !value_keep_holdings)
    if not value_keep_holdings:
        for code in holdings:
            if code not in target_codes:
                logger.info("  Would sell %s (out of ranking) — sell not implemented in one-shot", code)

    # Buy
    investable = min(deposit, value_max_budget) if value_max_budget > 0 else deposit
    pending = 0
    placed: list[tuple[str, str, int, int, float]] = []

    for code, pbr, _ in targets:
        if code in holdings:
            continue
        if code in today_bought:
            continue

        remaining = value_holdings - (len(holdings) + pending)
        if remaining <= 0:
            break

        name = resolve_name(code) or code

        # Get latest close price
        try:
            df = kiwoom.get_price_data(code, max_loops=1)
            if df is None or df.empty:
                logger.warning("  Skip %s: no price data", name)
                continue
            price = int(df["close"].iloc[-1])
        except Exception as e:
            logger.warning("  Skip %s: price fetch error (%s)", name, e)
            continue

        budget = investable / remaining
        max_pos = int(deposit * 0.3)
        qty = min(int(budget / price), int(max_pos / price))
        if qty < 1:
            logger.info("  Skip %s: budget insufficient (%s won, price=%s)", name, f"{int(budget):,}", f"{price:,}")
            continue

        cost = math.floor(qty * price * 1.0015)
        if deposit < cost:
            logger.warning("  Skip %s: deposit short (need %s, have %s)", name, f"{cost:,}", f"{deposit:,}")
            continue

        try:
            result = kiwoom.send_order("buy_value", "1001", 0, code, qty, price, "00")
        except Exception as e:
            logger.warning("  Skip %s: order error (%s)", name, e)
            continue

        if result.get("success"):
            deposit -= cost
            placed.append((code, name, qty, price, pbr))
            pending += 1
            logger.info("  ✅ Buy %s(%s): %d shares @ %s won (PBR=%.2f)", name, code, qty, f"{price:,}", pbr)
        else:
            logger.warning("  ❌ Buy %s failed: %s", name, result.get("error_message", "?"))

    # Summary
    logger.info("")
    logger.info("=" * 70)
    logger.info("  Execution done  |  Orders placed: %d  |  Remaining deposit: %s won",
                len(placed), f"{deposit:,}")
    logger.info("=" * 70)

    if placed:
        order_lines = [
            f"• {n}({c}) {q}주 @ {p:,}원 (PBR={pb:.2f})"
            for c, n, q, p, pb in placed
        ]
        send_telegram(
            f"📈 <b>Value Strategy — Buy Orders</b>\n"
            f"Mode: {'Mock' if is_mock else 'REAL'} | {len(placed)}건\n"
            + "\n".join(order_lines) + "\n"
            f"Remaining: {deposit:,}원"
        )
    else:
        send_telegram(
            f"ℹ️ Value Strategy — No orders\n"
            f"Mode: {'Mock' if is_mock else 'REAL'}"
        )


def main() -> None:
    try:
        run()
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        sys.exit(1)
    except Exception:
        logger.exception("Unhandled error")
        send_telegram("❌ Value Strategy: unhandled error — check logs")
        sys.exit(1)


if __name__ == "__main__":
    main()
