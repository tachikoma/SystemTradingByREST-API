# 주식 자동매매 "유료 서비스" 기획안

**일반 유저 대상 B2C SaaS 비즈니스 모델**로 재해석합니다.

---

## 🚨 법적 검토 (최우선)

### 1. 금융투자업 라이선스 필요
한국에서 **타인의 자산을 운용하거나 매매 대행**하면 불법입니다.

| 행위 | 필요 라이선스 | 비고 |
|------|--------------|------|
| 유저 계좌로 직접 매매 | ❌ **투자일임업** | 금융위 등록 필수 |
| 유료 매매 신호 제공 | ⚠️ **투자자문업** | 금융위 등록 필수 |
| 전략 소프트웨어 판매 | ✅ 합법 | 단, "수익 보장" 표현 금지 |
| 교육 콘텐츠 판매 | ✅ 합법 | - |

**결론**: 현재 코드로는 **"전략 소프트웨어 라이선스 판매"** 모델만 합법.

---

## 2. 합법적 비즈니스 모델 (3가지)

### 모델 A: 클라우드 전략 실행 플랫폼 (권장)
**개념**: 유저가 자기 키움 API 키를 등록하고, 우리 서버에서 전략을 실행.

**수익 구조**:
````python
# 요금제 예시
BASIC_PLAN = {
    "price": 29_000,  # 월 29,000원
    "features": ["RSI 전략", "1개 계좌", "텔레그램 알림"],
    "user_limit": 1000
}

PRO_PLAN = {
    "price": 79_000,  # 월 79,000원
    "features": ["모든 전략", "3개 계좌", "실시간 대시보드", "백테스트"],
    "user_limit": 500
}
````

**기술 스택**:
- **프론트엔드**: React/Vue + Vercel
- **백엔드**: FastAPI (유저 관리, 전략 실행 스케줄링)
- **워커**: 현재 `RSIStrategy` 코드를 Docker 컨테이너로 격리
- **DB**: PostgreSQL (유저 정보, 전략 설정) + Redis (실시간 데이터)
- **인프라**: AWS ECS Fargate (유저당 1 컨테이너)

**핵심 로직**:
````python
import docker

class StrategyExecutor:
    def start_user_strategy(self, user_id: str, api_key: str):
        """유저별 독립 컨테이너로 전략 실행"""
        client = docker.from_env()
        
        container = client.containers.run(
            image="stock-trading-worker:latest",
            detach=True,
            environment={
                "USER_ID": user_id,
                "KIWOOM_APPKEY": api_key,  # 암호화 필수
                "STRATEGY": "RSI",
                "TELEGRAM_CHAT_ID": self.get_user_telegram(user_id)
            },
            name=f"strategy-{user_id}",
            mem_limit="512m",
            cpu_quota=50000  # 0.5 CPU
        )
        
        return container.id
````

**장점**:
- ✅ 유저가 자기 계좌 소유 → 금융업 규제 회피
- ✅ 스케일 가능 (ECS Auto Scaling)
- ✅ 격리된 환경 (보안)

**단점**:
- 인프라 비용 (유저당 월 $5-10)
- 키움 API 키 보관 책임 (암호화 필수)

---

### 모델 B: 전략 라이선스 판매 (낮은 진입장벽)
**개념**: 현재 코드를 패키징해서 유저가 직접 로컬에서 실행.

**수익 구조**:
- **일회성 구매**: 300,000원 (평생 업데이트)
- **구독형**: 월 30,000원 (최신 전략 + 지원)

**제공 형태**:
1. **실행 파일**: PyInstaller로 빌드
   ````bash
   # 빌드 스크립트
   # 주의: .env 파일은 포함하지 않음 (보안), .env.example만 포함
   pyinstaller --onefile \
     --add-data ".env.example:." \
     --add-data "strategy:strategy" \
     --hidden-import websockets \
     main.py
   ````

   **첫 실행 시 .env 파일 자동 생성 로직** (`main.py` 시작 부분):
   ````python
   # filepath: main.py (첫 부분)
   from pathlib import Path
   import shutil
   import sys

   def ensure_env_file():
       """첫 실행 시 .env.example을 복사해서 .env 생성"""
       env_file = Path(".env")
       env_example = Path(".env.example")
       
       if not env_file.exists():
           if env_example.exists():
               shutil.copy(env_example, env_file)
               print("="*60)
               print("⚠️  첫 실행입니다! .env 파일을 생성했습니다.")
               print("📝 .env 파일을 열어서 키움 API 키를 입력하세요:")
               print(f"   - 위치: {env_file.absolute()}")
               print("   - KIWOOM_APPKEY=<여기에_앱키>")
               print("   - KIWOOM_SECRETKEY=<여기에_시크릿키>")
               print("="*60)
               input("설정 완료 후 Enter를 누르세요...")
           else:
               print("❌ .env.example 파일이 없습니다. 재설치하세요.")
               sys.exit(1)
   
   # 메인 실행 전 체크
   ensure_env_file()
   ````

2. **Docker 이미지**:
   ````dockerfile
   # filepath: Dockerfile
   FROM python:3.11-slim
   WORKDIR /app
   COPY . .
   RUN pip install poetry && poetry install --no-dev
   CMD ["poetry", "run", "python", "main.py", "--yes"]
   ````

3. **라이선스 검증**:
   ````python
   # filepath: util/license_check.py
   import requests
   from cryptography.fernet import Fernet
   
   def validate_license(license_key: str) -> bool:
       """서버에 라이선스 검증 요청"""
       resp = requests.post(
           "https://api.myservice.com/validate",
           json={"key": license_key, "hwid": get_hardware_id()}
       )
       return resp.json()["valid"]
   ````

**장점**:
- ✅ 개발 비용 최소 (현재 코드 90% 재사용)
- ✅ 법적 리스크 낮음
- ✅ 유저가 자기 환경 관리

**단점**:
- 불법 복제 위험
- 유저 지원 부담 (환경 설정 문의)

---

### 모델 C: 교육 + 전략 패키지
**개념**: 온라인 강의 + 전략 코드 판매.

**수익 구조**:
- **강의**: 200,000원 (20시간, Udemy/인프런)
  - 키움 API 사용법
  - 백테스트 방법론
  - 전략 개발 실습
- **전략 코드**: 강의 수강생 대상 50% 할인

**장점**:
- ✅ 법적으로 가장 안전
- ✅ 지속 수익 (신규 전략 강의)

**단점**:
- 콘텐츠 제작 시간
- 경쟁 많음 (유튜브 무료 콘텐츠와 경쟁)

---

## 3. 추천 모델: A + B 하이브리드

### Phase 1: 라이선스 판매 (0-6개월)
- 현재 코드를 실행 파일로 패키징
- 랜딩 페이지 + 결제 시스템 (Stripe)
- 목표: 월 50명 가입 (월 매출 150만원)

### Phase 2: 클라우드 전환 (6-12개월)
- AWS 인프라 구축
- 웹 대시보드 개발
- 목표: 월 200명 (월 매출 580만원)

### Phase 3: 전략 마켓플레이스 (12개월+)
- 유저가 자작 전략 등록/판매 (수수료 30%)
- 목표: 월 1,000명 (월 매출 2,900만원)

---

## 4. 기술 아키텍처 (모델 A 기준)

### 4.1 시스템 구성도
````
[유저 브라우저]
    ↓ HTTPS
[FastAPI 백엔드] ← PostgreSQL (유저/전략 설정)
    ↓ gRPC
[Strategy Scheduler] ← Redis (실시간 데이터)
    ↓ Docker API
[ECS Fargate]
    └─ Container 1 (User A) → [Kiwoom API]
    └─ Container 2 (User B) → [Kiwoom API]
    └─ Container N (User N)
````

### 4.2 핵심 테이블 스키마
````sql
CREATE TABLE users (
    id UUID PRIMARY KEY,
    email VARCHAR(255) UNIQUE,
    plan_type VARCHAR(20),  -- 'basic' | 'pro'
    created_at TIMESTAMP
);

CREATE TABLE api_keys (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id),
    kiwoom_appkey_encrypted TEXT,  -- AES-256 암호화
    created_at TIMESTAMP
);

CREATE TABLE strategy_runs (
    id UUID PRIMARY KEY,
    user_id UUID,
    container_id VARCHAR(64),
    status VARCHAR(20),  -- 'running' | 'stopped' | 'error'
    started_at TIMESTAMP,
    stopped_at TIMESTAMP
);
````

### 4.3 보안 체크리스트
- [ ] API 키 암호화 (AES-256 + AWS KMS)
- [ ] Rate Limiting (유저당 초당 10 요청)
- [ ] 컨테이너 네트워크 격리 (VPC)
- [ ] 로그 마스킹 (API 키 노출 방지)
- [ ] 2FA 인증 (Google Authenticator)
- [ ] GDPR 준수 (유저 데이터 삭제 기능)

---

## 5. 비용 분석 (유저 100명 기준)

### 인프라 비용
| 항목 | 단가 | 수량 | 월 비용 |
|------|------|------|---------|
| ECS Fargate (0.5 CPU) | $0.04/시간 | 100 컨테이너 × 24시간 | $2,880 |
| RDS PostgreSQL (db.t3.small) | $0.034/시간 | 1개 | $25 |
| Redis ElastiCache | $0.023/시간 | 1개 | $17 |
| S3 백업 | $0.023/GB | 100GB | $2 |
| CloudWatch 로그 | $0.50/GB | 50GB | $25 |
| **합계** | | | **$2,949** |

### 수익 분석
- **수입**: 100명 × 29,000원 = 2,900,000원
- **비용**: $2,949 ≈ 3,900,000원
- **손익**: -1,000,000원 (적자)

**결론**: **최소 150명**부터 수익 발생 (월 435만원).

---

## 6. 마케팅 전략

### 6.1 채널
- **네이버 카페** (주식/재테크 카페)
  - "자동매매 무료 체험" 이벤트
  - 백테스트 결과 공유 (25% 수익률)

- **유튜브 SEO**
  - "키움 자동매매 파이썬" 키워드 공략
  - 매주 1개 영상 (전략 소개, 백테스트 리뷰)

- **인플루언서 협업**
  - 재테크 유튜버에게 제휴 제안 (매출의 20%)

### 6.2 퍼널
````
유튜브 영상 (1,000 조회)
    ↓ 10% 클릭
랜딩 페이지 (100 방문)
    ↓ 30% 회원가입
무료 체험 (30명)
    ↓ 20% 전환
유료 가입 (6명)
````

**목표**: 월 1,500 조회 → 90명 가입 (ARPU 29,000원)

---

## 7. 법률 리스크 대응

### 7.1 면책 조항 (약관 필수)
````markdown
# 서비스 이용약관 (예시)

제10조 (면책사항)
1. 본 서비스는 투자 결과를 보장하지 않습니다.
2. 전략 실행은 회원 본인의 판단과 책임 하에 이루어집니다.
3. 시장 변동, API 장애 등으로 인한 손실은 회사가 책임지지 않습니다.
4. 회원은 본 서비스를 불법 금융 행위에 사용할 수 없습니다.
````

### 7.2 필수 문서
- [ ] 개인정보처리방침 (PIPA 준수)
- [ ] 서비스 이용약관
- [ ] 금융거래정보 수집 동의서
- [ ] 전자금융거래 약관

### 7.3 컴플라이언스 체크
- [ ] 금융위원회 신고 (매출 1억 초과 시)
- [ ] 전자금융업자 등록 (결제 대행사 사용 시 불필요)
- [ ] 세무 신고 (부가가치세, 법인세)

---

## 8. 위험 요소 & Exit 전략

### 위험 요소
1. **키움 API 정책 변경** (확률: 중, 영향: 높음)
   - 대응: 한국투자증권 API도 지원 (멀티 브로커)

2. **불법 복제** (확률: 높음, 영향: 중)
   - 대응: 라이선스 서버 검증 + 정기 업데이트

3. **법률 소송** (확률: 낮음, 영향: 치명)
   - 대응: 법률 자문 + 배상책임보험

### Exit 전략
- **M&A**: 증권사/핀테크 기업에 매각 (목표 가치 10억원)
- **서비스 종료**: 유저 데이터 안전 삭제 + 환불 (1개월 사전 공지)

---

## 9. 타임라인 (12개월 로드맵)

### Q1: MVP 출시 (3개월)
- [ ] 실행 파일 패키징 + 랜딩 페이지
- [ ] 결제 시스템 (Stripe) 통합
- [ ] 베타 유저 50명 모집 (무료)

### Q2: 클라우드 전환 (3개월)
- [ ] AWS 인프라 구축
- [ ] 웹 대시보드 프로토타입
- [ ] 유료 전환 (30명 목표)

### Q3: 마케팅 확대 (3개월)
- [ ] 유튜브 채널 개설 (주 1회 업로드)
- [ ] 네이버 카페 이벤트 (100명 목표)
- [ ] 인플루언서 협업 (5명)

### Q4: 수익화 (3개월)
- [ ] 유저 200명 달성
- [ ] 손익분기점 돌파
- [ ] 전략 마켓플레이스 베타 출시

---

## 10. 예상 재무제표 (12개월)

| 월 | 유저 수 | 월 매출 | 월 비용 | 월 손익 | 누적 손익 |
|----|---------|---------|---------|---------|-----------|
| 1 | 10 | 290,000 | 500,000 | -210,000 | -210,000 |
| 3 | 30 | 870,000 | 1,500,000 | -630,000 | -1,470,000 |
| 6 | 100 | 2,900,000 | 3,900,000 | -1,000,000 | -4,470,000 |
| 9 | 200 | 5,800,000 | 6,800,000 | -1,000,000 | -7,470,000 |
| 12 | 350 | 10,150,000 | 10,000,000 | **+150,000** | **-7,320,000** |

**총 투자금 필요**: 약 800만원 (초기 개발 + 12개월 운영)

---

## 결론

**유료 서비스는 가능하지만, 개인 프로젝트보다 100배 복잡합니다.**

| 구분 | 개인 사용 | 유료 서비스 |
|------|-----------|-------------|
| 개발 기간 | 1개월 | 6-12개월 |
| 초기 비용 | 50만원 | 800만원 |
| 법률 검토 | 불필요 | 필수 (변호사 자문) |
| 고객 지원 | 없음 | 24/7 필요 |
| 리스크 | 개인 손실 | 법적 소송 가능 |

**추천**: 먼저 **개인 사용으로 6개월 실전 운영** 후, 성과가 안정적이면 서비스화 검토하세요.