"""1월 4일 매도 조건 분석 스크립트"""
import sqlite3
import pandas as pd
import numpy as np

# 보유 종목 정보 (로그에서 확인)
holdings = {
    '006400': {'name': '삼성SDI', 'purchase_price': 262000, 'quantity': 3},
    '247540': {'name': '에코프로비엠', 'purchase_price': 141700, 'quantity': 5},
    '347850': {'name': '디앤디파마텍', 'purchase_price': 88100, 'quantity': 9},
}

# 거래 비용 (모의투자)
SELL_FEE_RATE = 1.0035  # 수수료 0.35%
RSI_SELL_THRESHOLD = 80

def calculate_rsi(prices, period=2):
    """RSI 계산 (전략과 동일한 방식)"""
    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    
    avg_gains = []
    avg_losses = []
    
    # 첫 번째 평균
    avg_gains.append(np.mean(gains[:period]))
    avg_losses.append(np.mean(losses[:period]))
    
    # Smoothed RSI (Wilder's method)
    for i in range(period, len(gains)):
        avg_gains.append((avg_gains[-1] * (period - 1) + gains[i]) / period)
        avg_losses.append((avg_losses[-1] * (period - 1) + losses[i]) / period)
    
    rsi_values = []
    for avg_gain, avg_loss in zip(avg_gains, avg_losses):
        if avg_loss == 0:
            rsi_values.append(100)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(100 - (100 / (1 + rs)))
    
    return rsi_values

# DB 연결
db_path = '/Users/durkjaeyun/Documents/DjY/projects/investment/easy-system-trading/SystemTrading/260105_RSIStrategy.db'
conn = sqlite3.connect(db_path)

print("=" * 80)
print("📊 1월 4일 (2026-01-04) 매도 조건 분석")
print("=" * 80)

for code, info in holdings.items():
    print(f"\n{'='*80}")
    print(f"종목: {info['name']} ({code})")
    print(f"매입가: {info['purchase_price']:,}원 | 보유수량: {info['quantity']}주")
    print(f"{'='*80}")
    
    # 가격 데이터 조회 (최근 30일)
    query = f'SELECT "index", close FROM "{code}" ORDER BY "index" DESC LIMIT 30'
    df = pd.read_sql_query(query, conn)
    df = df.sort_values('index')  # 오래된 순으로 정렬
    
    if len(df) < 3:
        print("❌ 데이터 부족")
        continue
    
    # 1월 4일 (2026-01-02가 최신 - 1월 2일 장마감 데이터)
    latest_date = df.iloc[-1]['index']
    latest_close = df.iloc[-1]['close']
    
    print(f"\n📅 최신 데이터 날짜: {latest_date}")
    print(f"💵 종가: {latest_close:,}원")
    
    # RSI(2) 계산
    prices = df['close'].values
    if len(prices) >= 4:  # RSI(2) 계산에 최소 4개 필요
        rsi_values = calculate_rsi(prices, period=2)
        current_rsi = rsi_values[-1]
        print(f"📈 RSI(2): {current_rsi:.2f}")
    else:
        print("❌ RSI 계산 불가 (데이터 부족)")
        continue
    
    # 손익분기점 계산
    purchase_price = info['purchase_price']
    breakeven_price = int(np.ceil(purchase_price * SELL_FEE_RATE))
    
    print(f"\n💰 손익분기점: {breakeven_price:,}원 (매입가 × {SELL_FEE_RATE:.4f})")
    print(f"   └─ 수수료 포함 필요 상승액: {breakeven_price - purchase_price:,}원 ({((breakeven_price/purchase_price - 1) * 100):.2f}%)")
    
    # 수익률 계산
    profit_rate = ((latest_close - purchase_price) / purchase_price) * 100
    actual_profit_rate = ((latest_close - breakeven_price) / purchase_price) * 100
    
    print(f"\n📊 수익률:")
    print(f"   • 명목 수익률: {profit_rate:+.2f}%")
    print(f"   • 실제 수익률 (수수료 차감): {actual_profit_rate:+.2f}%")
    
    # 매도 조건 체크
    print(f"\n🔍 매도 조건 체크:")
    print(f"   ① RSI(2) > {RSI_SELL_THRESHOLD}: {current_rsi:.2f} > {RSI_SELL_THRESHOLD} = {current_rsi > RSI_SELL_THRESHOLD} {'✅' if current_rsi > RSI_SELL_THRESHOLD else '❌'}")
    print(f"   ② 현재가 > 손익분기점: {latest_close:,} > {breakeven_price:,} = {latest_close > breakeven_price} {'✅' if latest_close > breakeven_price else '❌'}")
    
    should_sell = current_rsi > RSI_SELL_THRESHOLD and latest_close > breakeven_price
    
    print(f"\n{'🎯 매도 신호 발생!' if should_sell else '⛔ 매도 조건 미충족'}")
    
    if not should_sell:
        reasons = []
        if current_rsi <= RSI_SELL_THRESHOLD:
            reasons.append(f"RSI({current_rsi:.2f})가 과열({RSI_SELL_THRESHOLD}) 미만")
        if latest_close <= breakeven_price:
            shortage = breakeven_price - latest_close
            reasons.append(f"현재가가 손익분기점보다 {shortage:,}원 낮음")
        print(f"   사유: {' & '.join(reasons)}")
    
    # 최근 5일 추이
    print(f"\n📈 최근 5일 추이:")
    recent_5 = df.tail(5)
    for _, row in recent_5.iterrows():
        date_str = row['index']
        close = row['close']
        change = ((close - purchase_price) / purchase_price) * 100
        print(f"   {date_str}: {close:,}원 ({change:+.2f}%)")

conn.close()

print(f"\n{'='*80}")
print("✅ 분석 완료")
print(f"{'='*80}")
