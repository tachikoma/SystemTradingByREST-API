# VPS 전환 실행 런북: RSI → VALUE 모드

> Phase 2 실행 시 VPS에서 수행할 명령어 시퀀스 (복붙 가능)
> 대상: VPS (develop 브랜치, scripts/start_watchdog.sh 로 운영)

---

## 사전 조건

- [ ] 로컬에서 `feature/strategy-research → develop` 머지 완료 및 원격 푸시 완료
- [ ] 텔레그램 봇 정상 동작 확인
- [ ] `.env` 백업본 확인 가능
- [ ] **장중이 아닌 시간**(09:00~15:20 외)에 실행 권장 (리밸런싱 충돌 방지)
- [ ] 혹은 주말에 실행 권장

---

## Step 1: VPS SSH 접속

```bash
ssh user@vps-ip
```

> 실제 IP와 사용자명으로 변경 필요

---

## Step 2: 프로젝트 디렉토리 이동 및 develop 브랜치 최신 코드 가져오기

```bash
cd /path/to/SystemTrading  # 실제 VPS 경로로 변경

# 현재 브랜치 확인
git branch

# develop 브랜치로 전환 (이미 develop이면 생략)
git checkout develop

# 최신 코드 가져오기 (Phase 2에서 로컬에서 머지 후 푸시한 상태)
git pull tachikoma develop

# 머지 로그 확인
git log --oneline -3
```

---

## Step 3: .env 파일 백업 및 수정

```bash
# .env 백업
cp .env .env.backup.$(date +%Y%m%d_%H%M%S)
echo "✅ .env 백업 완료: .env.backup.$(date +%Y%m%d_%H%M%S)"

# .env 수정 (nano 또는 vi 사용)
nano .env
```

**`.env`에 추가/수정할 내용** (아래 블록을 복사하여 기존 .env 마지막에 추가):

```env
# ===================================================================
# Phase 2: RSI → VALUE 전환 (2026-06-22)
# ===================================================================
STRATEGY_MODE=value
VALUE_KEEP_HOLDINGS=1
TIME_STOP_LOSS_DAYS=90
```

**변경 전후 비교 확인**:

```bash
# 변경된 값 확인
grep -E "^(STRATEGY_MODE|VALUE_KEEP_HOLDINGS|TIME_STOP_LOSS_DAYS)" .env

# 예상 출력:
# STRATEGY_MODE=value
# VALUE_KEEP_HOLDINGS=1
# TIME_STOP_LOSS_DAYS=90
```

---

## Step 4: watchdog 중단 및 재시작

```bash
# 1. 현재 실행 중인 watchdog 중단
sh scripts/stop_watchdog.sh

# 2. watchdog이 완전히 종료될 때까지 잠시 대기 (메인 프로세스 정리 시간)
sleep 3

# 3. watchdog 재시작 (내부적으로 main.py를 실행함)
sh scripts/start_watchdog.sh

# 4. 프로세스 상태 확인
pgrep -af "watchdog.py|main.py" | grep -v grep
```

> **참고**: `start_watchdog.sh`는 내부적으로 `stop_watchdog.sh`를 먼저 호출하므로,
> `sh scripts/start_watchdog.sh` 만으로도 중단→재시작이 한 번에 가능합니다.
> 위처럼 분리한 것은 .env 변경 후 확실히 반영되도록 의도적인 시차를 둔 것입니다.

---

## Step 5: 로그 실시간 모니터링

watchdog는 `main.py`를 `nohup`으로 실행하며, 로그는 `logs/kiwoom_nohup.log`와
`logs/` 디렉토리 내 RotatingFileHandler 로그 파일에 기록됩니다.

```bash
# 1. 로그 디렉토리 확인
ls -la logs/

# 2. 최신 nohup 로그 확인 (watchdog의 표준출력)
tail -100 logs/kiwoom_nohup.log

# 3. RotatingFileHandler 로그 확인 (전략 상세 로그)
# 파일명 패턴 확인
ls -lt logs/*.log | head -5

# 가장 최근 로그 확인
tail -100 logs/kiwoom_nohup.log

# 4. 실시간 로그 팔로우
tail -f logs/kiwoom_nohup.log
```

**확인할 로그 패턴**:

| 패턴 | 의미 | OK/FAIL |
|------|------|---------|
| `전략 모드: Value` | VALUE 모드 정상 기동 | ✅ OK |
| `Starting System Trading in real mode...` | 실전 모드 정상 시작 | ✅ OK |
| `Error: API keys for real mode are not set.` | API 키 문제 | ❌ FAIL |
| `Unknown STRATEGY_MODE` | STRATEGY_MODE 오타 | ❌ FAIL |
| `Value 리밸런싱 대상` | 리밸런싱 정상 | ✅ OK (장중) |
| `PBR 데이터가 있는 종목이 없습니다` | PBR 데이터 없음 | ⚠️ 재시도 |
| `KOSPI200 < MA200: 약세장으로 전량 청산` | 마켓 필터 발동 | ⚠️ 주의 |

---

## Step 6: 텔레그램 알림 확인

VPS 재시작 후 텔레그램으로 수신되어야 하는 메시지:

1. `🚀 Starting System Trading in real mode...` (기동)
2. `전략 모드: Value` (VALUE 모드 확인)
3. `✅ Universe 재구성 완료 (종목 수: N)` (유니버스 로드)

---

## Step 7: (다음 거래일) VALUE 리밸런싱 최종 확인

다음 거래일 장중(09:00~15:20)에 추가 확인:

```bash
tail -n 500 logs/kiwoom_nohup.log | grep -E "Value|PBR|리밸런싱|매수|매도"
```

**확인 사항**:
- [ ] `Value 리밸런싱 대상 N개: [종목코드, ...]` → PBR 기반 선정 완료
- [ ] PBR 오름차순 정렬 로그
- [ ] 신규 VALUE 매수 주문 접수 (`Value 매수 주문 접수`)
- [ ] 기존 RSI 종목 유지 (매도되지 않음)
- [ ] 예수금 정상 차감

---

## 롤백 절차 (문제 발생 시)

```bash
# 1. watchdog 중단
sh scripts/stop_watchdog.sh
sleep 2

# 2. .env 복원
cp .env.backup.YYYYMMDD_HHMMSS .env

# 3. git 되돌리기 (머지 커밋 취소)
# git log --oneline -5  # 머지 커밋 확인
# git reset --hard HEAD~1  # 머지 커밋 취소

# 4. watchdog 재시작
sh scripts/start_watchdog.sh

# 5. 롤백 확인
sleep 3
tail -50 logs/kiwoom_nohup.log
```
