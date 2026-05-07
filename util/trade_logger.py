"""매매 이력을 CSV 파일로 기록하는 유틸리티 모듈.

기록 항목:
    timestamp       - 주문 접수 시각 (KST, ISO 8601)
    mode            - mock / real
    action          - BUY / SELL
    code            - 종목 코드
    name            - 종목명
    price           - 주문 가격 (원)
    quantity        - 주문 수량 (주)
    amount          - 주문 총금액 (price × quantity)
    fee             - 수수료+세금 추정액 (원)
    net_amount      - 실비용(매수) 또는 실수령(매도) 추정액 (원)
    purchase_price  - 매수 단가 (매도 시에만, 없으면 0)
    profit          - 예상 순이익 (매도 시에만, 없으면 0)
    profit_rate     - 예상 수익률 % (매도 시에만, 없으면 0.0)
    sell_reason     - 매도 사유 (매도 시에만: RSI_SIGNAL / TIME_STOP_LOSS / UNIVERSE_LIQUIDATION 등)
    order_no        - 주문 번호

파일 위치: {LOG_DIR}/trade_history.csv  (환경변수 LOG_DIR, 기본값 ./logs)
스레드 안전: FileLock 방식 대신 threading.Lock 사용 (외부 의존성 없음)
"""

import csv
import os
import threading
from pathlib import Path
from typing import Optional

from util.logging_config import get_logger
from util.time_helper import get_korea_time

logger = get_logger(__name__)

_lock = threading.Lock()

_CSV_FIELDNAMES = [
    "timestamp",
    "mode",
    "action",
    "code",
    "name",
    "price",
    "quantity",
    "amount",
    "fee",
    "net_amount",
    "purchase_price",
    "profit",
    "profit_rate",
    "sell_reason",
    "order_no",
]


def _get_csv_path() -> Path:
    log_dir = os.getenv("LOG_DIR", "./logs")
    path = Path(log_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path / "trade_history.csv"


def _ensure_header(csv_path: Path) -> None:
    """파일이 없거나 비어 있을 때 헤더를 기록한다."""
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=_CSV_FIELDNAMES)
            writer.writeheader()


def log_trade(
    *,
    mode: str,
    action: str,
    code: str,
    name: str,
    price: int,
    quantity: int,
    fee: int = 0,
    net_amount: int = 0,
    purchase_price: int = 0,
    profit: int = 0,
    profit_rate: float = 0.0,
    sell_reason: str = "",
    order_no: str = "",
) -> None:
    """매매 한 건을 CSV에 추가로 기록한다.

    Args:
        mode: 'mock' 또는 'real'
        action: 'BUY' 또는 'SELL'
        code: 종목 코드
        name: 종목명
        price: 주문 가격 (원)
        quantity: 주문 수량 (주)
        fee: 수수료+세금 추정액 (원)
        net_amount: 실비용(매수) 또는 실수령(매도) 추정액 (원)
        purchase_price: 매수 단가 (매도 시)
        profit: 예상 순이익 (매도 시)
        profit_rate: 예상 수익률 % (매도 시)
        sell_reason: 매도 사유 (매도 시)
        order_no: 주문 번호
    """
    row = {
        "timestamp": get_korea_time().isoformat(timespec="seconds"),
        "mode": mode,
        "action": action,
        "code": code,
        "name": name,
        "price": price,
        "quantity": quantity,
        "amount": price * quantity,
        "fee": fee,
        "net_amount": net_amount,
        "purchase_price": purchase_price,
        "profit": profit,
        "profit_rate": round(profit_rate, 4),
        "sell_reason": sell_reason,
        "order_no": order_no,
    }

    csv_path = _get_csv_path()
    try:
        with _lock:
            _ensure_header(csv_path)
            with open(csv_path, "a", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=_CSV_FIELDNAMES)
                writer.writerow(row)
        logger.debug("매매 이력 기록 완료: action=%s code=%s order_no=%s", action, code, order_no)
    except Exception as e:
        # 기록 실패는 매매 흐름에 영향을 주지 않도록 경고만 출력
        logger.warning("매매 이력 CSV 기록 실패 (무시): %s", e)
