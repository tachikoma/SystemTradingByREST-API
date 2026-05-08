**Docker 배포 및 실행 가이드**

- **개요:** 이 문서는 `SystemTrading` 레포를 Docker로 빌드, 배포, 실행하는 방법을 설명합니다.
- **전제:** macOS(또는 리눅스)에서 Docker가 설치되어 있어야 합니다. Apple Silicon(M1/M2) 환경에서는 추가 옵션이 필요할 수 있습니다.

**사전 준비**:
- `.env` 파일을 프로젝트 루트에 복사/설정하세요 (`cp .env.example .env`) 및 API 키/모드 설정.
- Docker Desktop 설치: https://docs.docker.com/get-docker/

**이미지 빌드 (로컬)**
```bash
# 프로젝트 루트에서
docker build -t systemtrading:latest .
```

Apple Silicon에서 AMD 이미지를 사용해야 할 경우:
```bash
docker build --platform=linux/amd64 -t systemtrading:latest .
```

**컨테이너 실행 (단일)**
```bash
# .env를 사용하고 logs 폴더를 로컬에 마운트
docker run --rm --env-file .env -v "$(pwd)/logs:/app/logs" systemtrading:latest
```

**docker compose (개발)**
```bash
# 개발 모드: 특정 서비스만 데몬으로 실행하려면 `-d <service>` 사용
docker compose up --build -d systemtrading
```

**docker compose (프로덕션)**
```bash
docker compose -f docker-compose.yml up --build -d systemtrading-prod
```

**로그와 데이터**
- 로깅: 컨테이너 내부의 `logs/kiwoom.log` 등이 로컬 `./logs`로 마운트됩니다. 로그 디렉토리를 유지하세요.
- DB/캐시: 프로젝트가 내부에 SQLite 파일을 생성할 경우 해당 파일을 호스트 볼륨으로 마운트하거나 외부 볼륨으로 관리하세요.

**환경변수 (.env)**
- 반드시 `.env`를 통해 민감 정보(API 키 등)를 주입하세요. `.env`는 절대 깃에 커밋하지 마세요.

**ETF 유니버스 정책 (.env)**
- 유니버스 생성 시 ETF 포함 방식을 환경변수로 제어할 수 있습니다.

```env
# ETF 정책: all | exclude | only | auto
UNIVERSE_ETF_MODE=auto

# auto 모드에서 유지할 ETF 코드(콤마 구분)
UNIVERSE_ETF_WHITELIST_CODES=229200,381180

# auto 모드에서 유지할 ETF 이름(선택)
UNIVERSE_ETF_WHITELIST_NAMES=
```

- 권장: `UNIVERSE_ETF_MODE=auto`
- 권장 화이트리스트(2026-05 백테스트 기준): `229200,381180`

**권장 추가 설정 / 다음 단계**
- CI: GitHub Actions에서 `docker build` 후 multi-arch 이미지로 푸시 (`docker/build-push-action`).
- 멀티 아키텍처: `docker buildx`로 `linux/amd64,linux/arm64` 이미지를 생성하세요.
- 모니터링: 프로덕션에선 로그를 중앙화(ELK/Fluentd)하고 Secrets는 Vault/K8s Secret으로 관리하세요.

**DB_DIR / 데이터 위치**
- 기본: 애플리케이션은 캐시와 DB 파일을 `DB_DIR` 환경변수(기본 `./data`)에 저장합니다.
- 개발용(로컬): `docker-compose.yml`의 `systemtrading` 서비스는 호스트의 `./data`를 `/app/data`로 바인드하여 컨테이너가 생성한 파일이 호스트에 남도록 구성되어 있습니다.
- 프로덕션: `systemtrading-prod`는 named volume(`db_data`)을 사용하도록 권장합니다. 예:

```yaml
services:
	systemtrading-prod:
		volumes:
			- db_data:/app/data
volumes:
	db_data:
```

- 요약:
	- 로컬 개발: `./data` 바인드 → 파일을 직접 확인/백업 가능
	- 프로덕션: named volume → macOS에서 성능 우수, Docker가 관리
	- `DB_DIR`을 변경하려면 `docker-compose.yml`에서 `DB_DIR` 환경변수를 설정하세요 (`DB_DIR=/app/data`).

필요하면 이 문서를 기반으로 GitHub Actions 워크플로와 Kubernetes 배포 매니페스트도 생성해드릴게요.
