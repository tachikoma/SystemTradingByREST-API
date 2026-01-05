"""
API Delay 최적값 분석
실제 데이터 기반으로 최적 delay 계산
"""

# 실제 측정 데이터
data_points = [
    {"delay": 0.2, "retry_rate": 0.166, "total_time": 62},  # 어제
    {"delay": 0.3, "retry_rate": 0.111, "total_time": 65},  # 오늘
]

TOTAL_STOCKS = 4233
RETRY_WAIT = 0.5  # retry 시 추가 대기 (고정)

# API 응답 시간 역산
# 실제 시간 = 성공 호출 시간 + retry 호출 시간 + retry 대기 시간
# 실제 시간 = (TOTAL - retry) × (delay + response) + retry × (delay + response + RETRY_WAIT)
# 실제 시간 = TOTAL × (delay + response) + retry × RETRY_WAIT

def estimate_response_time():
    """API 평균 응답 시간 추정"""
    d1 = data_points[0]
    d2 = data_points[1]
    
    retry1 = int(TOTAL_STOCKS * d1["retry_rate"])
    retry2 = int(TOTAL_STOCKS * d2["retry_rate"])
    
    time1_sec = d1["total_time"] * 60
    time2_sec = d2["total_time"] * 60
    
    # time = TOTAL × (delay + resp) + retry × RETRY_WAIT
    # resp를 구하기 위해 두 식을 연립
    
    # time1 - TOTAL × delay1 - retry1 × RETRY_WAIT = TOTAL × resp
    # time2 - TOTAL × delay2 - retry2 × RETRY_WAIT = TOTAL × resp
    
    resp1 = (time1_sec - retry1 * RETRY_WAIT) / TOTAL_STOCKS - d1["delay"]
    resp2 = (time2_sec - retry2 * RETRY_WAIT) / TOTAL_STOCKS - d2["delay"]
    
    avg_resp = (resp1 + resp2) / 2
    
    print("=" * 80)
    print("📊 API 응답 시간 역산")
    print("=" * 80)
    print(f"Delay 0.2초 기준 추정 응답 시간: {resp1:.3f}초")
    print(f"Delay 0.3초 기준 추정 응답 시간: {resp2:.3f}초")
    print(f"평균 API 응답 시간: {avg_resp:.3f}초")
    print()
    
    return avg_resp

def predict_retry_rate(delay):
    """Delay에 따른 retry 비율 예측 (선형 보간)"""
    d1, d2 = data_points[0], data_points[1]
    
    # 선형 보간
    slope = (d2["retry_rate"] - d1["retry_rate"]) / (d2["delay"] - d1["delay"])
    # retry_rate = slope × (delay - d1_delay) + d1_retry_rate
    
    retry_rate = slope * (delay - d1["delay"]) + d1["retry_rate"]
    
    # 0% 이하는 불가능
    retry_rate = max(0, retry_rate)
    
    return retry_rate

def calculate_total_time(delay, response_time):
    """총 소요 시간 계산"""
    retry_rate = predict_retry_rate(delay)
    retry_count = int(TOTAL_STOCKS * retry_rate)
    success_count = TOTAL_STOCKS - retry_count
    
    # 성공 호출 시간
    success_time = success_count * (delay + response_time)
    
    # Retry 호출 시간 (delay + 대기 + response)
    retry_time = retry_count * (delay + RETRY_WAIT + response_time)
    
    total_seconds = success_time + retry_time
    total_minutes = total_seconds / 60
    
    return {
        "delay": delay,
        "retry_rate": retry_rate * 100,
        "retry_count": retry_count,
        "success_count": success_count,
        "total_time_min": total_minutes,
        "success_time": success_time / 60,
        "retry_time": retry_time / 60,
    }

def find_theoretical_optimal():
    """이론적 최적값 계산 (retry = 0인 지점)"""
    d1, d2 = data_points[0], data_points[1]
    slope = (d2["retry_rate"] - d1["retry_rate"]) / (d2["delay"] - d1["delay"])
    
    # retry_rate = slope × (delay - d1_delay) + d1_retry_rate = 0
    # delay = (d1_retry_rate / -slope) + d1_delay
    
    optimal_delay = (d1["retry_rate"] / -slope) + d1["delay"]
    
    return optimal_delay

# 분석 실행
print("=" * 80)
print("🔍 키움 API Delay 최적값 분석")
print("=" * 80)
print(f"\n기준 데이터:")
print(f"  • 총 종목: {TOTAL_STOCKS:,}개")
print(f"  • Retry 대기: {RETRY_WAIT}초 (고정)")
print()

# API 응답 시간 추정
response_time = estimate_response_time()

# 이론적 최적값
theoretical_optimal = find_theoretical_optimal()
print("=" * 80)
print("🎯 이론적 최적값 (Retry = 0%)")
print("=" * 80)
print(f"Retry가 0%가 되는 Delay: {theoretical_optimal:.3f}초")
print(f"(하지만 실제로는 비선형이므로 참고용)")
print()

# 다양한 delay 시나리오 분석
print("=" * 80)
print("📈 Delay별 예상 성능")
print("=" * 80)
print(f"{'Delay':>7} | {'Retry%':>7} | {'Retry수':>8} | {'소요시간':>10} | {'변화':>8}")
print("-" * 80)

delays_to_test = [0.3, 0.35, 0.4, 0.45, 0.5, 0.6, 0.7, 1.0]
results = []

for delay in delays_to_test:
    result = calculate_total_time(delay, response_time)
    results.append(result)
    
    # 0.3초 대비 변화
    baseline = 65  # 현재 0.3초 소요시간
    change = ((result["total_time_min"] - baseline) / baseline) * 100
    change_str = f"{change:+.1f}%"
    
    print(f"{delay:>6.2f}초 | {result['retry_rate']:>6.1f}% | {result['retry_count']:>7}개 | "
          f"{result['total_time_min']:>8.1f}분 | {change_str:>8}")

# 최적값 찾기
print()
print("=" * 80)
print("✅ 최적값 추천")
print("=" * 80)

optimal_result = min(results, key=lambda x: x["total_time_min"])
print(f"\n최단 시간: {optimal_result['delay']:.2f}초 - {optimal_result['total_time_min']:.1f}분")

# Retry < 5% 중 최단 시간
low_retry_results = [r for r in results if r["retry_rate"] < 5.0]
if low_retry_results:
    stable_result = min(low_retry_results, key=lambda x: x["total_time_min"])
    print(f"안정성 우선 (Retry < 5%): {stable_result['delay']:.2f}초 - {stable_result['total_time_min']:.1f}분")

# 실용적 추천
print("\n💡 실용적 추천:")
print(f"  • 속도 우선: 0.3-0.35초 (현재와 비슷하거나 약간 빠름)")
print(f"  • 균형: 0.35-0.4초 (retry 감소, 시간 소폭 증가)")
print(f"  • 안정성 우선: 0.45-0.5초 (retry 최소화, 시간 약간 증가)")
print()
print("⚠️  주의: 실제 키움 API rate limit은 비공개이므로")
print("   실제 테스트로 검증이 필요합니다.")
