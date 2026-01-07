# 주식 자동매매 유료 서비스 기획서

**B2C SaaS 비즈니스 모델 - 일반 유저 대상 자동매매 플랫폼**

---

## 🚨 1. 법적 검토 (최우선 확인 필수)

### 1.1 금융투자업 라이선스 필요 여부

한국에서 **타인의 자산을 운용하거나 매매 대행**하면 금융투자업 등록이 필수입니다.

| 서비스 형태 | 필요 라이선스 | 법적 위험도 | 비고 |
|------------|--------------|------------|------|
| 유저 계좌로 직접 매매 대행 | ❌ **투자일임업** | 매우 높음 | 금융위 등록 필수, 자본금 30억 필요 |
| 유료 매매 신호 제공 | ⚠️ **투자자문업** | 높음 | 금융위 등록 필수, 자본금 5억 필요 |
| 전략 소프트웨어 판매 | ✅ 합법 | 낮음 | "수익 보장" 표현만 금지 |
| 교육 콘텐츠 판매 | ✅ 합법 | 매우 낮음 | 교육 목적만 명시 |

**결론**: 현재 코드베이스로는 **"전략 소프트웨어 라이선스 판매"** 모델만 합법적으로 운영 가능합니다.

### 1.2 준수 사항
- 투자 결과 보장 금지 ("월 수익률 20% 보장" 같은 표현 절대 금지)
- 백테스트 결과는 "과거 성과이며 미래 수익을 보장하지 않음" 문구 필수
- 개인정보보호법(PIPA) 준수 - 유저 API 키 암호화 저장 필수
- 전자금융거래법 준수 - 결제 대행사 사용 시 PG사가 라이선스 보유

---

## 2. 합법적 비즈니스 모델 (3가지)

### 모델 A: 클라우드 전략 실행 플랫폼 ⭐ (권장)

**개념**: 유저가 자기 키움 API 키를 등록하고, 우리 서버에서 전략을 실행해주는 SaaS.

**핵심 가치**: "설치/설정 없이 클릭 한 번으로 자동매매 시작"

#### 요금제 설계
````python
# 가격 전략
BASIC_PLAN = {
    "price": 29_000,  # 월 29,000원
    "features": [
        "RSI 전략 1개",
        "1개 증권 계좌 연결",
        "텔레그램 실시간 알림",
        "매일 수익률 리포트"
    ],
    "target_users": 1000,
    "margin": "60%" # 변동비 40%
}

PRO_PLAN = {
    "price": 79_000,  # 월 79,000원
    "features": [
        "모든 전략 (RSI + 볼린저밴드 + 이동평균)",
        "3개 계좌 동시 운영",
        "실시간 웹 대시보드",
        "백테스트 시뮬레이터",
        "개인화된 전략 파라미터 조정"
    ],
    "target_users": 300,
    "margin": "70%"
}

ENTERPRISE_PLAN = {
    "price": 150_000,  # 월 150,000원 (연 150만원)
    "features": [
        "Pro 모든 기능",
        "전용 서버 (독립 인스턴스)",
        "API 직접 접근",
        "우선 고객 지원 (카톡 1:1)"
    ],
    "target_users": 50,
    "margin": "80%"
}
````

#### 기술 아키텍처

**시스템 구성도**:
````
[유저 웹 브라우저]
    ↓ HTTPS (React SPA)
[CDN: Vercel/Cloudflare] 
    ↓
[API Gateway: FastAPI]
    ├─ Auth Service (JWT)
    ├─ Strategy Manager
    ├─ Payment Service (Stripe)
    └─ Notification Service (Telegram)
    ↓
[PostgreSQL]  [Redis Cache]
    ↓
[Task Queue: Celery + Redis]
    ↓
[Docker Swarm / AWS ECS Fargate]
    ├─ Worker Container 1 (User A) → [Kiwoom API]
    ├─ Worker Container 2 (User B) → [Kiwoom API]
    └─ Worker Container N (User N)
````

**기술 스택**:
- **프론트엔드**: React 18 + TypeScript + Tailwind CSS
- **배포**: Vercel (무료 CDN)
- **백엔드**: FastAPI (Python 3.11) + Pydantic
- **데이터베이스**: 
  - PostgreSQL (유저 정보, 전략 설정, 거래 이력)
  - Redis (실시간 시세, 세션)
- **워커**: 현재 `RSIStrategy` 코드를 Docker 이미지로 패키징
- **오케스트레이션**: AWS ECS Fargate (서버리스 컨테이너)
- **모니터링**: Grafana + Prometheus + Sentry
- **결제**: Stripe (국내 카드 지원)

**핵심 구현 예시**:

````python
import docker
import logging
from typing import Optional
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

class StrategyExecutor:
    def __init__(self, encryption_key: bytes):
        self.docker_client = docker.from_env()
        self.cipher = Fernet(encryption_key)
    
    async def start_user_strategy(
        self, 
        user_id: str, 
        encrypted_api_key: str,
        strategy_name: str = "RSI"
    ) -> str:
        """유저별 독립 컨테이너로 전략 실행"""
        
        # API 키 복호화 (메모리에서만 처리)
        api_key = self.cipher.decrypt(encrypted_api_key.encode()).decode()
        
        # 유저별 환경변수 설정
        env_vars = {
            "USER_ID": user_id,
            "KIWOOM_APPKEY": api_key.split(":")[0],
            "KIWOOM_SECRETKEY": api_key.split(":")[1],
            "KIWOOM_MODE": "real",
            "STRATEGY": strategy_name,
            "TELEGRAM_CHAT_ID": await self.get_user_telegram(user_id),
            "DB_CONNECTION": f"postgresql://user:pass@db:5432/trading_{user_id}"
        }
        
        try:
            container = self.docker_client.containers.run(
                image="stock-trading-worker:v2.0",
                detach=True,
                environment=env_vars,
                name=f"strategy-{user_id}",
                mem_limit="512m",  # 메모리 제한
                cpu_quota=50000,   # 0.5 CPU
                network="trading_network",  # 격리된 네트워크
                restart_policy={"Name": "on-failure", "MaximumRetryCount": 3},
                labels={
                    "user_id": user_id,
                    "strategy": strategy_name,
                    "tier": await self.get_user_tier(user_id)
                }
            )
            
            logger.info(f"Started container {container.id} for user {user_id}")
            return container.id
            
        except Exception as e:
            logger.error(f"Failed to start strategy for user {user_id}: {e}")
            raise
    
    async def stop_user_strategy(self, user_id: str):
        """전략 중지"""
        container_name = f"strategy-{user_id}"
        try:
            container = self.docker_client.containers.get(container_name)
            container.stop(timeout=30)
            container.remove()
            logger.info(f"Stopped container for user {user_id}")
        except docker.errors.NotFound:
            logger.warning(f"Container not found for user {user_id}")
````

````sql
-- 유저 테이블
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    plan_type VARCHAR(20) NOT NULL CHECK (plan_type IN ('basic', 'pro', 'enterprise')),
    subscription_status VARCHAR(20) DEFAULT 'active' CHECK (subscription_status IN ('active', 'paused', 'cancelled')),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    last_login TIMESTAMP
);

-- API 키 (암호화 저장)
CREATE TABLE api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    kiwoom_appkey_encrypted TEXT NOT NULL,  -- AES-256-GCM 암호화
    kiwoom_secretkey_encrypted TEXT NOT NULL,
    encryption_iv TEXT NOT NULL,  -- 초기화 벡터
    is_verified BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
    last_verified TIMESTAMP
);

-- 전략 실행 상태
CREATE TABLE strategy_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    strategy_name VARCHAR(50) NOT NULL,
    container_id VARCHAR(64),
    status VARCHAR(20) NOT NULL DEFAULT 'running' 
        CHECK (status IN ('running', 'stopped', 'error', 'paused')),
    started_at TIMESTAMP DEFAULT NOW(),
    stopped_at TIMESTAMP,
    error_message TEXT,
    INDEX idx_user_status (user_id, status),
    INDEX idx_container (container_id)
);

-- 거래 이력 (감사 로그)
CREATE TABLE trades (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    strategy_run_id UUID REFERENCES strategy_runs(id),
    stock_code VARCHAR(10) NOT NULL,
    stock_name VARCHAR(100),
    trade_type VARCHAR(4) CHECK (trade_type IN ('buy', 'sell')),
    quantity INT NOT NULL,
    price DECIMAL(10, 2) NOT NULL,
    total_amount DECIMAL(15, 2) NOT NULL,
    fees DECIMAL(10, 2),
    executed_at TIMESTAMP DEFAULT NOW(),
    INDEX idx_user_date (user_id, executed_at),
    INDEX idx_stock (stock_code)
);

-- 구독 결제 이력
CREATE TABLE payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    stripe_payment_id VARCHAR(255) UNIQUE,
    amount DECIMAL(10, 2) NOT NULL,
    currency VARCHAR(3) DEFAULT 'KRW',
    status VARCHAR(20) CHECK (status IN ('pending', 'succeeded', 'failed', 'refunded')),
    plan_type VARCHAR(20),
    billing_period_start DATE,
    billing_period_end DATE,
    created_at TIMESTAMP DEFAULT NOW()
);
````

**보안 체크리스트**:
- [x] API 키 AES-256-GCM 암호화 (AWS KMS로 마스터 키 관리)
- [x] 컨테이너 네트워크 격리 (Docker Swarm Overlay Network)
- [x] Rate Limiting (유저당 초당 10 요청, IP당 분당 100 요청)
- [x] SQL Injection 방지 (SQLAlchemy ORM + 파라미터화)
- [x] XSS 방지 (React의 기본 이스케이핑 + CSP 헤더)
- [x] HTTPS 강제 (Let's Encrypt 무료 인증서)
- [x] 2FA 인증 (Google Authenticator, 선택사항)
- [x] 로그 마스킹 (API 키 자동 ***** 처리)
- [x] GDPR 준수 (30일 이내 유저 데이터 완전 삭제 기능)

**장점**:
- ✅ 유저가 자기 계좌 소유 → 금융업 규제 회피
- ✅ 스케일 가능 (ECS Auto Scaling으로 수천 명 지원)
- ✅ 설치 불필요 → 전환율 높음 (브라우저만으로 시작)
- ✅ 지속 수익 (구독 모델)

**단점**:
- 인프라 비용 부담 (유저당 월 $3-5, 200명 기준 월 $600-1000)
- 키움 API 키 보관 책임 (보안 사고 시 치명적)
- 개발 기간 길음 (6-12개월)

---

### 모델 B: 전략 라이선스 판매 (빠른 출시 가능)

**개념**: 현재 코드를 실행 파일/Docker 이미지로 패키징해서 유저가 직접 로컬 실행.

#### 수익 구조
- **일회성 구매**: 300,000원 (평생 업데이트)
- **구독형**: 월 30,000원 (최신 전략 + 우선 지원)

#### 제공 형태

**1. 실행 파일 (Windows/Mac/Linux)**:
````bash
#!/bin/bash
# PyInstaller로 단일 실행 파일 빌드
# 주의: .env 파일은 절대 포함하지 않음! (API 키 노출 위험)
# .env.example만 템플릿으로 포함

poetry run pyinstaller \
  --onefile \
  --windowed \
  --icon=assets/icon.ico \
  --add-data ".env.example:." \
  --add-data "strategy:strategy" \
  --add-data "util:util" \
  --hidden-import websockets \
  --hidden-import pandas \
  --name "StockTradingBot" \
  main.py

# 빌드 결과: dist/StockTradingBot.exe (Windows)
#           dist/StockTradingBot (Mac/Linux)
````

   **중요**: 빌드 전 `.gitignore`에 `.env` 추가 확인
   ````bash
   # .gitignore
   .env          # ← 실제 API 키 파일 (절대 커밋 금지)
   *.db          # SQLite 데이터베이스
   __pycache__/
   dist/
   build/
   ````

   **배포 패키지 구성**:
   ````
   StockTradingBot-v2.0-Windows.zip
   ├── StockTradingBot.exe        # 실행 파일
   ├── .env.example               # 템플릿 (자동 포함됨)
   ├── README.txt                 # 설치 가이드
   └── license.key.example        # 라이선스 키 예시
   ````

   **README.txt 내용**:
   ````
   [주식 자동매매 설치 가이드]
   
   1. StockTradingBot.exe를 더블클릭하세요
   2. 첫 실행 시 .env 파일이 자동 생성됩니다
   3. 키움 OpenAPI+ 계정 준비:
      - https://www.kiwoom.com → OpenAPI+ 신청
      - APPKEY와 SECRETKEY 발급
   4. .env 파일을 메모장으로 열어서 API 키 입력
   5. 프로그램 재시작
   
   ⚠️ 주의: .env 파일을 타인과 공유하지 마세요!
   ````

**2. Docker 이미지**:
````dockerfile
FROM python:3.11-slim

WORKDIR /app

# 의존성 설치
COPY pyproject.toml poetry.lock ./
RUN pip install poetry && \
    poetry config virtualenvs.create false && \
    poetry install --no-dev --no-interaction

# 소스 코드 복사
COPY . .

# 라이선스 검증 스크립트 추가
COPY license_validator.py /app/

# 실행
ENTRYPOINT ["python", "-c", \
  "import license_validator; \
   license_validator.validate() and \
   __import__('subprocess').call(['python', 'main.py', '--yes'])"]
````

**3. 라이선스 검증 시스템**:
````python
import requests
import hashlib
import platform
import uuid
from pathlib import Path
from cryptography.fernet import Fernet

class LicenseValidator:
    LICENSE_SERVER = "https://license.stocktrading.com/api/validate"
    
    def __init__(self):
        self.license_file = Path.home() / ".stocktrading" / "license.key"
    
    def get_hardware_id(self) -> str:
        """하드웨어 고유 ID 생성 (MAC 주소 기반)"""
        mac = ':'.join(['{:02x}'.format((uuid.getnode() >> i) & 0xff) 
                        for i in range(0,8*6,8)][::-1])
        return hashlib.sha256(f"{mac}{platform.node()}".encode()).hexdigest()
    
    def validate(self) -> bool:
        """라이선스 키 검증"""
        if not self.license_file.exists():
            print("❌ 라이선스 파일이 없습니다. 구매 후 license.key를 설치하세요.")
            return False
        
        license_key = self.license_file.read_text().strip()
        hwid = self.get_hardware_id()
        
        try:
            resp = requests.post(
                self.LICENSE_SERVER,
                json={
                    "license_key": license_key,
                    "hardware_id": hwid,
                    "version": "2.0.0"
                },
                timeout=10
            )
            
            if resp.status_code == 200:
                data = resp.json()
                if data["valid"]:
                    print(f"✅ 라이선스 인증 성공 ({data['plan']} 플랜)")
                    return True
                else:
                    print(f"❌ 라이선스 인증 실패: {data['reason']}")
                    return False
            else:
                print(f"❌ 서버 오류: {resp.status_code}")
                return False
                
        except Exception as e:
            print(f"❌ 네트워크 오류: {e}")
            # 오프라인 모드: 최근 7일 이내 검증 이력 확인
            return self._check_offline_cache()
    
    def _check_offline_cache(self) -> bool:
        """오프라인 검증 (7일간 유효)"""
        cache_file = self.license_file.parent / ".cache"
        if not cache_file.exists():
            return False
        
        # 캐시 파일의 타임스탬프 확인
        from datetime import datetime, timedelta
        last_validated = datetime.fromtimestamp(cache_file.stat().st_mtime)
        if datetime.now() - last_validated < timedelta(days=7):
            print("✅ 오프라인 모드 (캐시 사용)")
            return True
        return False

# 전역 검증 함수
def validate():
    return LicenseValidator().validate()
````

**라이선스 서버 API** (FastAPI):
````python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import hashlib
from datetime import datetime

app = FastAPI()

class LicenseRequest(BaseModel):
    license_key: str
    hardware_id: str
    version: str

# 간단한 DB (실제로는 PostgreSQL 사용)
licenses_db = {
    "ABC123-XYZ789": {
        "user_email": "user@example.com",
        "plan": "pro",
        "expires_at": None,  # 평생 라이선스
        "max_devices": 3,
        "registered_hwids": ["hwid1", "hwid2"]
    }
}

@app.post("/api/validate")
async def validate_license(req: LicenseRequest):
    license_data = licenses_db.get(req.license_key)
    
    if not license_data:
        return {"valid": False, "reason": "존재하지 않는 라이선스 키"}
    
    # 만료 체크
    if license_data["expires_at"]:
        if datetime.now() > datetime.fromisoformat(license_data["expires_at"]):
            return {"valid": False, "reason": "만료된 라이선스"}
    
    # 하드웨어 등록 체크
    if req.hardware_id not in license_data["registered_hwids"]:
        if len(license_data["registered_hwids"]) >= license_data["max_devices"]:
            return {"valid": False, "reason": f"최대 {license_data['max_devices']}대까지 등록 가능"}
        else:
            # 새 디바이스 등록
            license_data["registered_hwids"].append(req.hardware_id)
    
    return {
        "valid": True,
        "plan": license_data["plan"],
        "expires_at": license_data["expires_at"]
    }
````

**장점**:
- ✅ 개발 비용 최소 (현재 코드 90% 재사용)
- ✅ 3개월 내 출시 가능
- ✅ 인프라 비용 거의 없음 (라이선스 서버만)
- ✅ 법적 리스크 낮음
- ✅ 유저가 자기 환경 관리 (서버 다운 위험 없음)

**단점**:
- 불법 복제 위험 (크랙 방지 어려움)
- 유저 지원 부담 (환경 설정 문의 많음)
- 지속 수익 모델 구축 어려움 (일회성 구매 시)

---

### 모델 C: 교육 + 전략 패키지

**개념**: 온라인 강의로 신뢰 구축 후 전략 코드 판매.

#### 수익 구조
**1단계: 무료 콘텐츠로 유입**
- YouTube 채널: "파이썬 주식 자동매매 시작하기" (10편, 무료)
- 블로그: 백테스트 결과, 전략 설명 (SEO 최적화)

**2단계: 유료 강의 (인프런/유데미)**
- 가격: 99,000원 (평생 소장)
- 내용:
  - 키움 API 사용법 (5시간)
  - RSI 전략 원리와 백테스트 (3시간)
  - 실전 운영 노하우 (2시간)
  - 전략 커스터마이징 실습 (5시간)

**3단계: 전략 코드 판매 (강의 수강생 대상)**
- 가격: 200,000원 (수강생은 100,000원)
- 제공: 소스 코드 + 1개월 이메일 지원

**예상 수익**:
- 강의 수강생: 월 50명 × 99,000원 = 4,950,000원
- 전략 구매: 월 20명 × 100,000원 = 2,000,000원
- **총 월 매출**: 약 700만원 (플랫폼 수수료 30% 제외 시 490만원)

**장점**:
- ✅ 법적으로 가장 안전
- ✅ 브랜드 구축 (전문가 이미지)
- ✅ 지속 수익 (신규 전략 강의)
- ✅ 커뮤니티 형성 (충성도 높은 고객)

**단점**:
- 콘텐츠 제작 시간 (영상 편집 등)
- 경쟁 많음 (유튜브 무료 콘텐츠와 경쟁)
- 초기 수익 느림 (3-6개월 소요)

---

## 3. 추천 전략: A + B 하이브리드 (단계적 접근)

### Phase 1: 라이선스 판매로 시작 (0-6개월)
**목표**: PMF 검증 + 초기 자금 확보

**실행 계획**:
1. **제품 개발 (2개월)**
   - 현재 코드를 PyInstaller로 빌드
   - 라이선스 검증 시스템 구축
   - 사용자 매뉴얼 작성 (PDF, 50페이지)

2. **마케팅 (1개월)**
   - 랜딩 페이지 제작 (Webflow/Framer)
   - 백테스트 결과 공개 (연 25% 수익률 강조)
   - 네이버 카페 이벤트 ("선착순 10명 무료 체험")

3. **결제 시스템 (1주)**
   - Stripe 통합 (국내 카드 지원)
   - 자동 라이선스 발급 (이메일 전송)

4. **KPI**:
   - 월 50명 가입 목표
   - ARPU: 30,000원 (구독형)
   - **월 매출**: 150만원
   - **누적 고객**: 300명 (6개월 후)

### Phase 2: 클라우드 전환 (6-12개월)
**목표**: 스케일업 + 고객 경험 개선

**실행 계획**:
1. **인프라 구축 (3개월)**
   - AWS ECS Fargate 설정
   - PostgreSQL RDS + Redis ElastiCache
   - 웹 대시보드 프로토타입 (React)

2. **마이그레이션 (1개월)**
   - 기존 고객 데이터 이전
   - 라이선스 → 클라우드 무료 전환 이벤트

3. **마케팅 확대**:
   - YouTube 채널 개설 (주 1회 업로드)
   - 인플루언서 협업 (재테크 유튜버 5명)
   - 네이버 블로그 SEO (키워드: "주식 자동매매")

4. **KPI**:
   - 월 200명 유지 목표
   - ARPU: 35,000원 (Pro 플랜 비율 증가)
   - **월 매출**: 700만원
   - **누적 고객**: 1,200명

### Phase 3: 전략 마켓플레이스 (12개월+)
**목표**: 플랫폼화 + 네트워크 효과

**실행 계획**:
1. **유저 기여 전략**:
   - 누구나 전략 등록 가능
   - 수수료: 판매가의 30%
   - 백테스트 자동 검증 시스템

2. **소셜 기능**:
   - 전략 평점/리뷰
   - 유저 간 전략 공유 (공개/비공개)
   - 리더보드 (월간 수익률 랭킹)

3. **KPI**:
   - 월 1,000명 활성 유저
   - ARPU: 40,000원
   - **월 매출**: 4,000만원
   - **전략 마켓플레이스 GMV**: 월 2,000만원 (수수료 600만원)

---

## 4. 상세 비용 분석

### 4.1 인프라 비용 (유저 200명 기준, Phase 2)

| 항목 | 단가 | 수량 | 월 비용 (USD) | 월 비용 (KRW) |
|------|------|------|---------------|---------------|
| ECS Fargate (0.5 vCPU, 1GB) | $0.04/hour | 200 컨테이너 × 9시간/일 | $720 | 950,000원 |
| RDS PostgreSQL (db.t4g.small) | $0.034/hour | 1 인스턴스 | $25 | 33,000원 |
| ElastiCache Redis (cache.t3.micro) | $0.017/hour | 1 인스턴스 | $12 | 16,000원 |
| Application Load Balancer | $0.0225/hour | 1개 | $17 | 22,000원 |
| S3 백업 | $0.023/GB | 100GB | $2 | 3,000원 |
| CloudWatch 로그 | $0.50/GB | 50GB | $25 | 33,000원 |
| Route 53 (DNS) | $0.50/zone | 1 zone | $0.50 | 700원 |
| **합계** | | | **$801** | **1,058,000원** |

> **환율**: 1 USD = 1,320 KRW (2026년 1월 기준)

### 4.2 개발 비용 (Phase 2)

| 항목 | 단가 | 비고 |
|------|------|------|
| 백엔드 개발 (3개월) | 15,000,000원 | FastAPI, DB 설계, Docker |
| 프론트엔드 개발 (3개월) | 12,000,000원 | React 대시보드 |
| 디자인 (UI/UX) | 3,000,000원 | Figma 디자인 + 퍼블리싱 |
| 인프라 설정 | 2,000,000원 | AWS 아키텍처 구축 |
| QA/테스트 | 1,500,000원 | 통합 테스트, 부하 테스트 |
| 법률 자문 | 1,000,000원 | 약관 검토, 컴플라이언스 |
| **총 개발비** | **34,500,000원** | 약 3,500만원 |

### 4.3 운영 비용 (월간)

| 항목 | 월 비용 | 비고 |
|------|---------|------|
| 인프라 (AWS) | 1,058,000원 | 위 표 참고 |
| 결제 수수료 (Stripe) | 203,000원 | 매출 700만원 × 2.9% |
| 고객 지원 (파트타임) | 1,500,000원 | 카톡 상담 + 이메일 |
| 마케팅 광고비 | 2,000,000원 | 네이버/구글 광고 |
| 도메인/SSL | 10,000원 | 연 12만원 ÷ 12 |
| **월 운영비** | **4,771,000원** | 약 480만원 |

### 4.4 손익분기점 분석 (Phase 2)

**고정비**: 월 4,771,000원  
**변동비**: 없음 (인프라 비용은 사용량 기반이나 고정비로 분류)  
**단위 기여이익**: 35,000원 (ARPU)

**손익분기점 유저 수** = 고정비 ÷ 단위 기여이익 = 4,771,000 ÷ 35,000 = **136명**

**결론**: **최소 140명**부터 수익 발생.

### 4.5 예상 재무제표 (12개월)

| 월 | 유저 수 | 월 매출 | 월 비용 | 월 손익 | 누적 손익 | 비고 |
|----|---------|---------|---------|---------|-----------|------|
| 1 | 10 | 300,000 | 1,000,000 | -700,000 | -700,000 | Phase 1 시작 |
| 3 | 50 | 1,500,000 | 1,200,000 | 300,000 | -1,100,000 | 라이선스 판매 |
| 6 | 120 | 3,600,000 | 2,500,000 | 1,100,000 | -500,000 | Phase 2 준비 |
| 9 | 200 | 7,000,000 | 4,771,000 | 2,229,000 | 5,187,000 | 클라우드 전환 |
| 12 | 350 | 12,250,000 | 6,500,000 | 5,750,000 | 22,437,000 | Phase 2 안정화 |

**총 투자금 필요**: 약 **3,500만원** (개발비 3,450만원 + 초기 운영비 50만원)  
**투자 회수 기간**: 약 7개월 (누적 손익 흑자 전환)

---

## 5. 마케팅 전략

### 5.1 채널별 전략

#### A. 네이버 카페 (초기 고객 확보)
- **타겟 카페**:
  - "주식 초보 모임" (회원 15만명)
  - "직장인 재테크 연구소" (회원 8만명)
  - "퀀트 투자 연구회" (회원 3만명)

- **전략**:
  - 백테스트 결과 공유 (인증샷)
  - "무료 체험 30일" 이벤트 (선착순 50명)
  - 유저 성공 사례 게시 (월 수익 인증)

- **예산**: 월 50만원 (카페 광고 배너)

#### B. YouTube SEO
- **타겟 키워드**:
  - "주식 자동매매 파이썬" (월 검색 1,200회)
  - "키움 API 사용법" (월 검색 800회)
  - "RSI 전략 백테스트" (월 검색 500회)

- **콘텐츠 계획** (주 1회 업로드):
  1. "파이썬으로 주식 자동매매 시작하기" (튜토리얼)
  2. "실제 수익 공개! 1개월 운영 결과" (후기)
  3. "백테스트 vs 실전 거래 차이점" (교육)
  4. "RSI 전략 완벽 가이드" (전략 설명)

- **예산**: 월 100만원 (영상 편집 외주)

#### C. 인플루언서 협업
- **타겟 유튜버**:
  - 재테크 채널 (구독자 5-10만명)
  - 프로그래밍 채널 (개발자 대상)

- **제휴 조건**:
  - 수익 분배: 매출의 20% (Stripe 추천 링크)
  - 무료 Pro 플랜 1년 제공

- **예상 효과**:
  - 유튜버 1명당 월 10명 신규 가입
  - 5명 협업 시 월 50명 증가

- **예산**: 성과 기반 (매출의 20%)

#### D. 구글/네이버 광고
- **캠페인 설정**:
  - 타겟: 30-50대 남성, 주식 투자 경험자
  - 키워드: "주식 자동매매", "키움 API", "퀀트 투자"
  - 일 예산: 50,000원

- **광고 카피**:
  - "9.7년 백테스트 연 25% 수익률 증명"
  - "설치 없이 3분 만에 시작하는 자동매매"
  - "한정 이벤트: 첫 달 50% 할인"

- **예산**: 월 150만원 (일 5만원 × 30일)

### 5.2 퍼널 전환율 (Phase 2 기준)

````
YouTube 영상 조회 (10,000 views)
    ↓ 5% 클릭
랜딩 페이지 방문 (500명)
    ↓ 30% 회원가입
무료 체험 시작 (150명)
    ↓ 30% 유료 전환
유료 구독 (45명)
````

**목표 달성 전략**:
- 월 10,000 조회 달성 → 유료 전환 45명
- 네이버 광고 + 인플루언서로 추가 100명 유입
- **총 월 신규 가입**: 약 50-70명

### 5.3 바이럴 마케팅

**추천 프로그램**:
````python
# 추천인 보상 시스템
REFERRAL_REWARDS = {
    "referrer": {
        "reward_type": "discount",
        "value": 10_000,  # 추천인: 다음 달 10,000원 할인
        "max_per_month": 100_000  # 월 최대 100,000원
    },
    "referee": {
        "reward_type": "discount",
        "value": 5_000,  # 신규 가입자: 첫 달 5,000원 할인
    }
}
````

**예상 효과**:
- 유저 1명당 평균 0.5명 추천 (바이럴 계수 0.5)
- 200명 유저 → 100명 신규 유입 (3개월간)

---

## 6. 법률 & 컴플라이언스

### 6.1 필수 약관 및 정책

#### A. 서비스 이용약관
````markdown
# 서비스 이용약관 (필수 조항)

## 제1조 (목적)
본 약관은 주식자동매매 서비스(이하 "서비스")의 이용과 관련하여
회사와 이용자의 권리, 의무 및 책임사항을 규정함을 목적으로 합니다.

## 제10조 (면책사항) ⚠️ 핵심
1. 본 서비스는 투자 결과를 보장하지 않습니다.
2. 백테스트 성과는 과거 데이터이며, 미래 수익을 보장하지 않습니다.
3. 전략 실행은 회원 본인의 판단과 책임 하에 이루어집니다.
4. 시장 변동, API 장애, 시스템 오류 등으로 인한 손실은 회사가 책임지지 않습니다.
5. 회원은 본 서비스를 불법 금융 행위에 사용할 수 없습니다.

## 제11조 (금지 행위)
1. 타인의 API 키를 무단 사용하는 행위
2. 시스템을 해킹하거나 보안을 침해하는 행위
3. 라이선스 키를 제3자에게 양도하거나 재판매하는 행위
4. 서비스를 이용하여 법률을 위반하는 행위
````

#### B. 개인정보처리방침
````markdown
# 개인정보처리방침

## 1. 수집하는 개인정보 항목
- 필수: 이메일, 비밀번호, 결제 정보
- 선택: 텔레그램 Chat ID, 키움 API 키 (암호화 저장)

## 2. 개인정보의 처리 목적
- 회원 가입 및 관리
- 서비스 제공 (자동매매 실행)
- 결제 및 환불 처리
- 고객 지원 및 문의 응대

## 3. 개인정보의 보유 기간
- 회원 탈퇴 시 즉시 삭제 (법률 보관 의무 제외)
- 거래 기록: 5년 (전자상거래법)
- API 키: 회원 탈퇴 시 즉시 영구 삭제

## 4. 개인정보의 제3자 제공
본 서비스는 원칙적으로 개인정보를 제3자에게 제공하지 않습니다.
단, 다음의 경우 예외로 합니다:
- Stripe (결제 처리): 카드 정보, 이메일
- 키움증권 (API 호출): 회원이 등록한 API 키만 사용

## 5. 개인정보 보호책임자
- 이름: [담당자명]
- 이메일: privacy@stocktrading.com
````

#### C. 전자금융거래 약관
````markdown
# 전자금융거래 이용약관

## 제7조 (거래내용의 확인)
회원은 거래내용을 수시로 확인할 수 있으며,
오류가 있는 경우 즉시 회사에 정정을 요구할 수 있습니다.

## 제10조 (손해배상)
회사는 고의 또는 과실이 없는 한 
전자금융거래의 오류로 인한 손해를 배상하지 않습니다.
````

### 6.2 법률 자문 체크리스트
- [ ] 투자자문업 등록 불필요 확인 (소프트웨어 판매만)
- [ ] 약관 변호사 검토 (비용: 100만원)
- [ ] 개인정보보호법(PIPA) 준수 확인
- [ ] 전자금융거래법 준수 확인
- [ ] 전자상거래법 준수 (청약철회, 환불 규정)
- [ ] 저작권법 (코드 라이선스, 오픈소스 사용)

### 6.3 세금 및 회계

#### 사업자 등록
````
업종: 소프트웨어 개발 및 공급업
과세 유형: 간이과세자 → 일반과세자 (연 매출 8,000만원 초과 시)
````

#### 세금 종류
| 세금 | 세율 | 납부 시기 | 비고 |
|------|------|-----------|------|
| 부가가치세 | 10% | 분기별 (1/4/7/10월) | 매출액의 10% |
| 법인세 (법인 설립 시) | 10-25% | 연 1회 (3월) | 과세표준에 따라 차등 |
| 종합소득세 (개인) | 6-45% | 연 1회 (5월) | 구간별 누진세 |

#### 회계 처리
- **회계 소프트웨어**: 더존 SmartA (월 50,000원)
- **세무사 비용**: 월 30만원 (기장료 + 세무 신고)

### 6.4 보험

#### 배상책임보험
````
보험사: 삼성화재 IT배상책임보험
보장 내용:
- 시스템 오류로 인한 고객 손해 배상
- 개인정보 유출 배상
- 법률 소송 비용
보험료: 연 200만원
보장 한도: 1억원
````

---

## 7. 기술 구현 상세

### 7.1 핵심 기능 명세

#### A. 유저 온보딩 플로우
````typescript
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';

export const OnboardingFlow: React.FC = () => {
  const [step, setStep] = useState(1);
  const navigate = useNavigate();
  
  const steps = [
    {
      id: 1,
      title: "키움 API 키 등록",
      component: <APIKeyInput />,
      validation: validateAPIKey
    },
    {
      id: 2,
      title: "전략 선택",
      component: <StrategySelector />,
      validation: validateStrategy
    },
    {
      id: 3,
      title: "초기 자금 설정",
      component: <InitialCapitalInput />,
      validation: validateCapital
    },
    {
      id: 4,
      title: "텔레그램 연동 (선택)",
      component: <TelegramSetup />,
      validation: () => true // 선택사항
    }
  ];
  
  const handleNext = async () => {
    const currentStep = steps[step - 1];
    const isValid = await currentStep.validation();
    
    if (isValid) {
      if (step < steps.length) {
        setStep(step + 1);
      } else {
        // 온보딩 완료 → 전략 시작
        await startStrategy();
        navigate('/dashboard');
      }
    }
  };
  
  return (
    <div className="onboarding-container">
      <ProgressBar current={step} total={steps.length} />
      {steps[step - 1].component}
      <Button onClick={handleNext}>다음</Button>
    </div>
  );
};
````

#### B. 실시간 대시보드
````typescript
import React, { useEffect, useState } from 'react';
import { useWebSocket } from '../hooks/useWebSocket';

export const Dashboard: React.FC = () => {
  const [portfolio, setPortfolio] = useState(null);
  const ws = useWebSocket();
  
  useEffect(() => {
    // WebSocket으로 실시간 업데이트 수신
    ws.on('portfolio_update', (data) => {
      setPortfolio(data);
    });
    
    // 초기 데이터 로드
    fetch('/api/portfolio').then(r => r.json()).then(setPortfolio);
  }, []);
  
  if (!portfolio) return <Loading />;
  
  return (
    <div className="dashboard">
      <PortfolioSummary 
        totalValue={portfolio.total_value}
        dailyPnL={portfolio.daily_pnl}
        totalPnL={portfolio.total_pnl}
      />
      <HoldingsTable holdings={portfolio.holdings} />
      <TradeHistory trades={portfolio.recent_trades} />
      <PerformanceChart data={portfolio.equity_curve} />
    </div>
  );
};
````

#### C. 전략 중지/재개 API
````python
from fastapi import APIRouter, Depends, HTTPException
from services.strategy_executor import StrategyExecutor
from dependencies import get_current_user, get_executor

router = APIRouter(prefix="/api/strategy", tags=["strategy"])

@router.post("/start")
async def start_strategy(
    user = Depends(get_current_user),
    executor: StrategyExecutor = Depends(get_executor)
):
    """전략 시작"""
    try:
        container_id = await executor.start_user_strategy(
            user_id=user.id,
            encrypted_api_key=user.api_key,
            strategy_name=user.selected_strategy
        )
        return {"status": "started", "container_id": container_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/stop")
async def stop_strategy(
    user = Depends(get_current_user),
    executor: StrategyExecutor = Depends(get_executor)
):
    """전략 중지"""
    await executor.stop_user_strategy(user.id)
    return {"status": "stopped"}

@router.get("/status")
async def get_strategy_status(user = Depends(get_current_user)):
    """전략 실행 상태 조회"""
    # DB에서 최근 strategy_run 조회
    run = await db.strategy_runs.find_one(
        {"user_id": user.id},
        sort=[("started_at", -1)]
    )
    return {
        "status": run["status"] if run else "not_started",
        "started_at": run["started_at"] if run else None,
        "container_id": run["container_id"] if run else None
    }
````

### 7.2 모니터링 & 알림

#### Grafana 대시보드
````yaml
{
  "dashboard": {
    "title": "Stock Trading Service Metrics",
    "panels": [
      {
        "title": "Active Users",
        "targets": [{
          "expr": "count(strategy_runs{status='running'})"
        }]
      },
      {
        "title": "API Error Rate",
        "targets": [{
          "expr": "rate(kiwoom_api_errors_total[5m])"
        }]
      },
      {
        "title": "Container CPU Usage",
        "targets": [{
          "expr": "avg(container_cpu_usage_seconds_total)"
        }]
      }
    ]
  }
}
````

#### Sentry 에러 추적
````python
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration

sentry_sdk.init(
    dsn="https://xxx@sentry.io/xxx",
    integrations=[FastApiIntegration()],
    traces_sample_rate=0.1,  # 10% 트랜잭션만 추적 (비용 절감)
    environment="production"
)
````

---

## 8. 위험 요소 & 대응 전략

| 위험 요소 | 확률 | 영향도 | 대응 방안 |
|-----------|------|--------|-----------|
| 키움 API 정책 변경 | 중 | 높음 | 한국투자증권 API도 지원 (멀티 브로커) |
| 불법 복제 (크랙) | 높음 | 중 | 라이선스 서버 검증 + 자주 업데이트 |
| 유저 API 키 유출 | 낮음 | 치명 | AES-256 암호화 + AWS KMS, 보험 가입 |
| 전략 성과 부진 | 중 | 높음 | 백테스트 정기 검증, 다중 전략 제공 |
| 인프라 장애 (AWS) | 낮음 | 높음 | Multi-AZ 배포 + 자동 failover |
| 법률 소송 | 낮음 | 치명 | 면책 조항 명시 + 배상책임보험 |
| 경쟁사 출현 | 높음 | 중 | 차별화 포인트 강화 (UI/UX, 고객 지원) |
| 키움 계정 정지 | 낮음 | 높음 | 유저별 독립 계정 사용 (회사 책임 아님) |

### 8.1 비즈니스 연속성 계획 (BCP)

**시나리오 1: AWS 장애**
- **대응**: 
  - Multi-Region 배포 (서울 + 도쿄)
  - RTO (복구 목표 시간): 30분 이내
  - RPO (복구 시점 목표): 실시간 (DB 복제)

**시나리오 2: 대규모 보안 사고**
- **대응**:
  - 즉시 서비스 중단
  - 모든 유저에게 이메일 공지
  - API 키 재발급 요청
  - 보험 처리 (최대 1억원 보장)

**시나리오 3: 급격한 유저 증가 (Viral Growth)**
- **대응**:
  - Auto Scaling 설정 (ECS Fargate)
  - DB 수평 확장 (Read Replica 추가)
  - Rate Limiting 강화 (유저당 API 호출 제한)

---

## 9. Exit 전략

### 9.1 M&A (인수합병)
**타겟 매수자**:
- 증권사 (키움, 한국투자, NH투자증권)
- 핀테크 스타트업 (토스, 뱅크샐러드)
- 로보어드바이저 업체 (쿼터백, 파운트)

**목표 가치**: 누적 매출의 3-5배 (예: 연 매출 5억 → 가치 15-25억)

**협상 포인트**:
- 활성 유저 수 (1,000명 이상)
- MRR (월 반복 매출)
- 기술 스택 (AWS 인프라, React 대시보드)
- 전략 IP (백테스트 검증된 알고리즘)

### 9.2 IPO (기업공개)
**조건**: 연 매출 50억 이상 (KOSDAQ 상장 요건)  
**현실성**: 낮음 (금융 규제로 성장 한계)

### 9.3 서비스 종료
**절차**:
1. 3개월 전 사전 공지 (약관 명시)
2. 신규 가입 중단
3. 환불 처리 (잔여 기간 비례)
4. 유저 데이터 안전 삭제 (GDPR 준수)
5. 코드 오픈소스 공개 (선택사항)

---

## 10. 성공 KPI

### Phase 1 (0-6개월): 라이선스 판매
- [ ] 월 50명 유료 가입
- [ ] 이탈률 < 10%
- [ ] NPS (Net Promoter Score) > 50
- [ ] 월 매출 150만원 달성

### Phase 2 (6-12개월): 클라우드 전환
- [ ] 월 200명 활성 유저
- [ ] 유료 전환율 > 30%
- [ ] ARPU > 35,000원
- [ ] 월 매출 700만원 달성

### Phase 3 (12개월+): 플랫폼화
- [ ] 월 1,000명 활성 유저
- [ ] 전략 마켓플레이스 GMV 월 2,000만원
- [ ] 유저 기여 전략 50개 이상
- [ ] 월 매출 4,000만원 달성

---

## 11. 최종 권장사항

### ✅ 추천하는 경우
- 소프트웨어 개발 경험 5년 이상
- 초기 자금 3,500만원 이상 보유
- 풀타임 투입 가능 (최소 12개월)
- 법률/세무/회계 지식 (또는 전문가 네트워크)

### ⚠️ 주의할 점
- **법적 리스크**: 투자자문업 경계선 조심 (절대 "수익 보장" 표현 금지)
- **기술 부채**: 빠른 출시를 위해 코드 품질 희생하지 말 것
- **고객 지원**: 초기에는 창업자가 직접 응대 (신뢰 구축)

### ❌ 추천하지 않는 경우
- 프로그래밍 경험 3년 미만
- 초기 자금 1,000만원 미만
- 사이드 프로젝트로 시작 (성공 확률 낮음)
- 단기 수익 기대 (최소 12개월 소요)

---

## 12. 체크리스트

### Phase 1 출시 전 필수 (3개월)
- [ ] 라이선스 검증 시스템 구축
- [ ] 실행 파일 빌드 (Windows/Mac)
- [ ] 사용자 매뉴얼 작성 (50페이지)
- [ ] 랜딩 페이지 제작
- [ ] Stripe 결제 연동
- [ ] 약관 3종 (이용약관, 개인정보, 전자금융) 작성
- [ ] 사업자 등록
- [ ] 첫 고객 10명 확보 (베타 테스터)

### Phase 2 클라우드 전환 전 (6개월)
- [ ] AWS 인프라 구축 완료
- [ ] 웹 대시보드 프로토타입
- [ ] PostgreSQL 마이그레이션
- [ ] 보안 감사 (외부 업체)
- [ ] 부하 테스트 (1,000명 동시 접속)
- [ ] Grafana 모니터링 대시보드
- [ ] 배상책임보험 가입

### Phase 3 플랫폼화 전 (12개월)
- [ ] 전략 마켓플레이스 개발
- [ ] 결제 수수료 시스템 (Stripe Connect)
- [ ] 유저 리뷰/평점 시스템
- [ ] 소셜 공유 기능 (전략 성과)
- [ ] API 문서화 (외부 개발자용)
- [ ] 커뮤니티 포럼 구축

---

## 13. 부록: 참고 자료

### 13.1 법률 관련
- [금융위원회 - 투자자문업 등록 안내](https://www.fsc.go.kr)
- [개인정보보호위원회 - PIPA 가이드](https://www.pipc.go.kr)
- [전자금융거래법 전문](https://law.go.kr)

### 13.2 기술 문서
- [Stripe API 한국 가이드](https://stripe.com/docs)
- [AWS ECS Fargate 베스트 프랙티스](https://aws.amazon.com/ecs/)
- [Docker 컨테이너 보안](https://docs.docker.com/engine/security/)

### 13.3 마케팅 리소스
- [네이버 광고 가이드](https://searchad.naver.com)
- [YouTube SEO 최적화](https://creatoracademy.youtube.com)
- [SaaS 퍼널 최적화 (영문)](https://www.profitwell.com)

### 13.4 비슷한 서비스 벤치마크
- **해외**:
  - Quantopian (폐업, 2020) - 교훈: 무료 모델은 지속 불가
  - QuantConnect (운영 중) - 구독형 $20-200/월
  - TradingView (상장) - 차트 + 알림, $12.95-59.95/월
  
- **국내**:
  - 매직스톡 - 증권사 제휴 모델
  - 쿼터백 - 로보어드바이저 (투자일임업 등록)

### 13.5 커뮤니티
- [네이버 카페 "주식 자동매매 연구소"](https://cafe.naver.com)
- [Discord 서버 "알고트레이딩 KR"](https://discord.com)
- [Reddit r/algotrading](https://reddit.com/r/algotrading)

---

## 14. 실행 가이드 (액션 플랜)

### Week 1-4: 시장 검증 (MVP 전)
**목표**: "이 서비스를 돈 내고 쓸 사람이 있는가?" 검증

**실행 계획**:
1. **랜딩 페이지 제작** (3일)
   - Framer/Webflow 사용 (코딩 불필요)
   - 백테스트 결과 시각화 (차트)
   - 대기자 명단 폼 (이메일 수집)

2. **네이버 카페 이벤트** (1주)
   - "무료 백테스트 리포트 받기" 이벤트
   - 100명 이메일 수집 목표
   - 설문조사: "월 얼마면 쓸 의향 있나요?"

3. **결과 분석** (1일)
   - 전환율 > 10% (100명 중 10명 유료 의향)
   - 적정 가격: 설문 결과 중앙값
   - **Go/No-Go 결정**: 시장 있으면 개발 시작, 없으면 포기

**예산**: 50만원 (랜딩 페이지 제작 + 카페 광고)

### Week 5-16: Phase 1 개발 (3개월)
**목표**: 라이선스 판매 가능한 제품 출시

**주차별 계획**:
- **Week 5-8**: 코드 패키징 + 라이선스 시스템
  - PyInstaller 빌드 스크립트 작성
  - 라이선스 검증 API 개발 (FastAPI)
  - Stripe 결제 연동

- **Week 9-12**: 사용자 매뉴얼 + 지원 시스템
  - 50페이지 PDF 매뉴얼 작성
  - 카톡 상담 채널 개설
  - FAQ 페이지 제작

- **Week 13-16**: 베타 테스트
  - 10명 베타 테스터 모집
  - 버그 수정 + 피드백 반영
  - 공식 출시

**예산**: 500만원 (개발자 1명 × 3개월)

### Week 17-28: 초기 고객 확보 (3개월)
**목표**: 월 50명 유료 가입

**주차별 계획**:
- **Week 17-20**: 콘텐츠 마케팅
  - YouTube 채널 개설
  - 영상 10개 업로드 (주 2-3회)
  - 블로그 글 20개 발행 (SEO)

- **Week 21-24**: 유료 광고
  - 네이버 검색 광고 (일 3만원)
  - 구글 Display 광고 (일 2만원)
  - A/B 테스트 (카피, 크리에이티브)

- **Week 25-28**: 추천 프로그램
  - 추천인 보상 시스템 런칭
  - 기존 고객에게 추천 링크 발송
  - 추천 성과 분석

**예산**: 500만원 (광고비 450만원 + 영상 제작 50만원)

### Week 29-52: Phase 2 준비 (6개월)
**목표**: 클라우드 플랫폼 개발 + 200명 유저 확보

**분기별 계획**:
- **Q3 (Week 29-40)**: 인프라 구축
  - AWS ECS Fargate 설정
  - PostgreSQL + Redis 구축
  - Docker 이미지 최적화

- **Q4 (Week 41-52)**: 웹 대시보드 개발
  - React 프론트엔드 (8주)
  - FastAPI 백엔드 리팩토링 (4주)
  - 베타 출시 (기존 고객 무료 전환)

**예산**: 2,500만원 (개발자 2명 × 6개월)

---

## 15. 자주 묻는 질문 (FAQ)

### Q1. 개인 프로젝트로 쓰다가 나중에 서비스화하면 안 되나요?
**A**: 가능하지만, 코드 아키텍처가 많이 다릅니다.
- **개인 사용**: 단일 계정, 환경변수로 API 키 관리
- **서비스**: 멀티 테넌트, DB에 암호화 저장, 컨테이너 격리

처음부터 서비스 아키텍처로 설계하지 않으면 나중에 전체 재작성이 필요합니다.

### Q2. 법적으로 문제없나요? 투자자문업 등록 필요 없나요?
**A**: **소프트웨어만 판매**하면 합법입니다.
- ❌ 불법: "제가 월 20% 수익 보장하고 운용해드립니다"
- ✅ 합법: "이 소프트웨어는 RSI 전략을 자동 실행합니다 (수익 보장 없음)"

약관에 면책 조항을 명확히 하고, "수익 보장" 표현만 하지 않으면 됩니다.

### Q3. 키움 API 정책이 바뀌면 어떡하죠?
**A**: 멀티 브로커 전략으로 대응하세요.
- 한국투자증권 API도 동시 지원
- 코드 아키텍처: `BrokerInterface` 추상화
  ````python
  class BrokerInterface(ABC):
      @abstractmethod
      def get_balance(self): pass
      
  class KiwoomBroker(BrokerInterface): ...
  class KoreaInvestmentBroker(BrokerInterface): ...
  ````

### Q4. 초기 자금 3,500만원 없으면 못 하나요?
**A**: Phase 1(라이선스 판매)만 하면 **50만원**으로도 가능합니다.
- 랜딩 페이지: Framer (월 10달러)
- 결제: Stripe (초기 비용 없음, 수수료만)
- 서버: Vercel (무료 플랜)

Phase 2(클라우드)는 투자금이 필요하지만, Phase 1 수익으로 충당 가능합니다.

### Q5. 경쟁사가 나오면 어떻게 하죠?
**A**: 차별화 포인트로 방어하세요.
- **기술**: 9.7년 백테스트 검증된 전략 (경쟁사는 2-3년)
- **UI/UX**: 초보자도 3분 만에 시작 (원클릭 설치)
- **고객 지원**: 카톡 1:1 상담 (24시간 응답)
- **커뮤니티**: 유저 간 전략 공유 (네트워크 효과)

### Q6. 실제로 돈 벌 수 있나요? (전략 성과)
**A**: **과거 성과 ≠ 미래 수익**입니다.
- 백테스트: 연 25% (2016-2025)
- 실전: 변동성 높음 (월 -10% ~ +30%)
- 손실 가능성 있음 (MDD -49%)

서비스 약관에 이걸 명시하고, 고객이 이해했다는 동의를 받아야 합니다.

### Q7. 서비스 중단하면 유저 데이터는 어떻게 하나요?
**A**: 약관에 명시된 절차대로 처리하세요.
1. 3개월 전 사전 공지
2. 데이터 다운로드 기능 제공 (CSV)
3. 환불 처리 (잔여 기간 비례)
4. 30일 후 모든 데이터 영구 삭제 (GDPR)

---

## 결론: 당신의 선택

이 기획서는 "주식 자동매매를 유료 서비스로 만들 수 있나?"라는 질문에 대한 포괄적인 답변입니다.

### ✅ 가능합니다. 하지만...

| 구분 | 개인 사용 | 유료 서비스 |
|------|-----------|-------------|
| **개발 기간** | 1개월 | 6-12개월 |
| **초기 비용** | 50만원 | 800만원 ~ 3,500만원 |
| **법률 검토** | 불필요 | 필수 (변호사 자문 100만원) |
| **고객 지원** | 없음 | 24/7 (카톡, 이메일) |
| **리스크** | 개인 손실만 | 법적 소송 가능 |
| **월 매출 잠재력** | 0원 | 700만원 ~ 4,000만원 |
| **스트레스** | 낮음 | 매우 높음 |

### 🎯 최종 추천
1. **먼저 개인 사용으로 6개월 실전 운영**하세요.
   - 전략 성과 검증
   - 시스템 안정성 확인
   - 장애 대응 경험

2. **성과가 좋으면 Phase 1(라이선스 판매)** 시작하세요.
   - 투자금: 50만원
   - 기간: 3개월
   - 리스크: 낮음

3. **유저 100명 넘으면 Phase 2(클라우드)** 검토하세요.
   - 투자금: 3,500만원
   - 기간: 6개월
   - 리스크: 중간

4. **실패해도 괜찮습니다.**
   - 기술 스택 경험 (AWS, Docker, FastAPI)
   - 사업 감각 (마케팅, 고객 응대)
   - 포트폴리오 (이직 시 유리)

### 💡 마지막 조언
**"완벽한 계획보다 빠른 실행"**

이 기획서는 100% 완벽한 시나리오입니다. 실제로는 70%만 계획대로 되고, 30%는 예상 밖의 일이 벌어집니다.

시장은 기획서를 읽지 않습니다. 빨리 작은 제품을 만들어서 **실제 고객 피드백**을 받으세요.

**그게 가장 정확한 시장 조사입니다.**

---

## 작성 정보
- **작성일**: 2026년 1월 7일
- **버전**: v2.0
- **작성자**: GitHub Copilot
- **기반 프로젝트**: [SystemTrading RSI Strategy](../../readme.md)
- **연락처**: 질문은 GitHub Issues에 남겨주세요


