"""Universe 장애 핫픽스 회귀 테스트 (2026-11-12 네이버 크롤링 0건 → 성공 오알림).

- 모든 파일/DB는 pytest tmp_path의 격리 경로만 사용한다 (운영 data/ 미접촉).
- 실제 네트워크/Kiwoom API/주문은 사용하지 않는다 (requests, Kiwoom 전부 mock).
"""
import os
import time
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import util.make_up_universe as mu
import util.db_helper as dbh
from util.make_up_universe import UniverseDataError, validate_source_df


# ---------------------------------------------------------------- fixtures
def make_kiwoom_df(n=4314):
    """정상 키움 캐시(4,314건)와 동일한 구조의 합성 데이터."""
    rng = np.random.default_rng(0)
    codes = [f"{i:05d}0" for i in range(n)]
    kosdaq = np.arange(n) % 2 == 1
    return pd.DataFrame({
        '종목코드': codes,
        '종목명': [f"종목{i}" for i in range(n)],
        '시장구분': np.where(kosdaq, '코스닥', '코스피'),
        '현재가': rng.integers(1000, 50000, n),
        '거래량': rng.integers(1000, 1_000_000, n),
        '거래대금': rng.integers(5000, 500_000, n),      # 백만원
        '시가총액': rng.integers(60_000, 900_000, n),    # 백만원
        '등락률': rng.normal(0, 2, n).round(2),
        '외국인비율': rng.uniform(0, 30, n).round(2),
        '상장주식수': rng.integers(1_000_000, 100_000_000, n),
    })


@pytest.fixture
def env(tmp_path, monkeypatch):
    """DB_DIR을 임시 경로로 격리하고 실주문/네트워크 차단."""
    monkeypatch.setattr(mu, 'DB_DIR', str(tmp_path))
    monkeypatch.setattr(dbh, 'DB_DIR', str(tmp_path))
    monkeypatch.setenv('SAVE_CANONICAL', '1')
    monkeypatch.setattr(mu, '_load_fdr_etf_symbols', lambda: set())  # FDR 네트워크 호출 차단
    monkeypatch.setattr(mu, '_FDR_ETF_SYMBOLS', None)

    def _no_network(*a, **k):
        raise AssertionError("실제 네트워크 호출 금지")
    return SimpleNamespace(dir=tmp_path, no_network=_no_network)


def write_sentinel(path):
    """기존 정상 파일을 흉내 내는 sentinel parquet. 덮어써졌는지 검사용."""
    df = make_kiwoom_df(600)
    df.to_parquet(path, index=True)
    return path.read_bytes()


class Resp:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


BLOCKED_HTML = "<html><body><p>접근이 제한되었습니다</p></body></html>"  # table/div 없음


# ---------------------------------------------------------------- validation
def test_validate_accepts_healthy_kiwoom_structure():
    assert validate_source_df(make_kiwoom_df(4314), 'kiwoom') is True


def test_validate_rejects_zero_rows_missing_columns_and_garbage():
    with pytest.raises(UniverseDataError, match="0건"):
        validate_source_df(pd.DataFrame(), 'naver')
    df = make_kiwoom_df(1000).drop(columns=['거래량'])
    with pytest.raises(UniverseDataError, match="거래량"):
        validate_source_df(df, 'naver')
    with pytest.raises(UniverseDataError, match="행 수 부족"):
        validate_source_df(make_kiwoom_df(50), 'small')
    bad = make_kiwoom_df(1000)
    bad['시가총액'] = 'N/A'
    with pytest.raises(UniverseDataError, match="시가총액"):
        validate_source_df(bad, 'garbage')


# ---------------------------------------------------------------- crawler
def test_crawler_blocked_response_raises_and_preserves_cache(env, monkeypatch):
    naver = env.dir / 'all_stocks_naver.parquet'
    canon = env.dir / 'all_stocks_canonical.parquet'
    before_n, before_c = write_sentinel(naver), write_sentinel(canon)
    monkeypatch.setattr('requests.get', lambda *a, **k: Resp(BLOCKED_HTML))
    monkeypatch.setattr('requests.post', lambda *a, **k: Resp(BLOCKED_HTML))
    with pytest.raises(UniverseDataError):
        mu.execute_crawler('all_stocks_naver.parquet')
    assert naver.read_bytes() == before_n          # 0건으로 덮어쓰지 않음
    assert canon.read_bytes() == before_c
    assert not list(env.dir.glob('*.tmp.*'))        # 임시파일 잔존 없음


def test_crawler_http_error_raises(env, monkeypatch):
    monkeypatch.setattr('requests.get', lambda *a, **k: Resp("", status=503))
    with pytest.raises(Exception):
        mu.execute_crawler('all_stocks_naver.parquet')
    assert not (env.dir / 'all_stocks_naver.parquet').exists()


def test_crawler_missing_columns_not_saved(env, monkeypatch):
    """페이지는 파싱되지만 필수 컬럼(거래량 등)이 없는 경우 저장 금지."""
    rows = "".join(
        f'<tr><td class="no">{i}</td><td><a class="tltle" href="/x?code={i:06d}">N{i}</a></td>'
        f'<td class="number">{1000+i}</td></tr>' for i in range(300))
    html = ('<div class="box_type_l"><table><thead><tr><th>N</th><th>종목명</th><th>현재가</th><th>x</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>')
    monkeypatch.setattr('requests.get', lambda *a, **k: Resp(html))
    monkeypatch.setattr('requests.post', lambda *a, **k: Resp(html))
    with pytest.raises(UniverseDataError, match="필수 컬럼 누락"):
        mu.execute_crawler('all_stocks_naver.parquet')
    assert not (env.dir / 'all_stocks_naver.parquet').exists()


# ---------------------------------------------------------------- canonical
def test_canonical_not_overwritten_by_empty_or_incomplete(env):
    canon = env.dir / 'all_stocks_canonical.parquet'
    before = write_sentinel(canon)
    mu._normalize_units(pd.DataFrame(), save_canonical=True)
    mu._normalize_units(pd.DataFrame({'종목명': ['a']}), save_canonical=True)
    assert canon.read_bytes() == before


def test_canonical_saved_for_healthy_data(env):
    mu._normalize_units(make_kiwoom_df(4314), save_canonical=True)
    saved = pd.read_parquet(env.dir / 'all_stocks_canonical.parquet')
    assert len(saved) == 4314


# ---------------------------------------------------------------- fallback
def _fail_crawl(env, monkeypatch):
    monkeypatch.setattr('requests.get', lambda *a, **k: Resp(BLOCKED_HTML))
    monkeypatch.setattr('requests.post', lambda *a, **k: Resp(BLOCKED_HTML))


def test_fallback_to_valid_kiwoom_cache_when_naver_fails(env, monkeypatch):
    """운영 재현: 네이버 0건 + 12일 전 키움 캐시 4,314건 → 키움 캐시로 Universe 생성."""
    kiwoom = env.dir / 'all_stocks_kiwoom.parquet'
    make_kiwoom_df(4314).to_parquet(kiwoom, index=True)
    old = time.time() - 12 * 86400
    os.utime(kiwoom, (old, old))
    naver = env.dir / 'all_stocks_naver.parquet'
    pd.DataFrame().to_parquet(naver)  # 장애 당시의 0건 파일
    _fail_crawl(env, monkeypatch)

    universe = mu.get_universe(kiwoom_client=None, max_codes=50, universe_output_file='universe.parquet')
    assert len(universe) == 50
    assert len(pd.read_parquet(env.dir / 'universe.parquet')) == 50


def test_try_load_cache_skips_invalid_and_stale(env):
    # 0건 키움 캐시 → 건너뜀, 정상 네이버 캐시 사용
    pd.DataFrame().to_parquet(env.dir / 'all_stocks_kiwoom.parquet')
    make_kiwoom_df(800).to_parquet(env.dir / 'all_stocks_naver.parquet', index=True)
    df = mu._try_load_cache()
    assert df is not None and len(df) == 800
    # 너무 오래된 캐시는 제외
    old = time.time() - (mu.CACHE_MAX_AGE_DAYS + 5) * 86400
    os.utime(env.dir / 'all_stocks_naver.parquet', (old, old))
    assert mu._try_load_cache() is None


def test_no_valid_cache_raises_and_existing_universe_preserved(env, monkeypatch):
    uni = env.dir / 'universe.parquet'
    before = write_sentinel(uni)
    pd.DataFrame().to_parquet(env.dir / 'all_stocks_kiwoom.parquet')
    pd.DataFrame().to_parquet(env.dir / 'all_stocks_naver.parquet')
    _fail_crawl(env, monkeypatch)
    with pytest.raises(Exception):
        mu.get_universe(kiwoom_client=None, max_codes=50, universe_output_file='universe.parquet')
    assert uni.read_bytes() == before


def test_filter_empty_or_missing_columns_raises_not_empty_list(env):
    uni = env.dir / 'universe.parquet'
    before = write_sentinel(uni)
    with pytest.raises(UniverseDataError):
        mu._filter_and_create_universe(pd.DataFrame(), universe_output_file='universe.parquet')
    with pytest.raises(UniverseDataError):
        mu._filter_and_create_universe(make_kiwoom_df(100).drop(columns=['거래량']), universe_output_file='universe.parquet')
    assert uni.read_bytes() == before


def test_kiwoom_fetch_invalid_result_not_cached(env, monkeypatch):
    """키움 API가 대부분 실패해 비정상 결과면 캐시(및 canonical)를 쓰지 않는다."""
    kiwoom_cache = env.dir / 'all_stocks_kiwoom.parquet'
    before = write_sentinel(kiwoom_cache)
    monkeypatch.setattr(mu.time, 'sleep', lambda *_: None, raising=False)
    client = SimpleNamespace(
        mock=True,
        get_code_list_by_market=lambda m: [{'code': f'{i:06d}', 'name': f'N{i}'} for i in range(20)],
        get_stock_info=lambda code: None,  # 전부 실패
    )
    with pytest.raises(UniverseDataError):
        mu.fetch_all_stocks_from_kiwoom(client, use_cache=False, save_cache=True)
    assert kiwoom_cache.read_bytes() == before


# ---------------------------------------------------------------- strategy
from strategy.RSIStrategy import RSIStrategy  # noqa: E402
import strategy.RSIStrategy as rs  # noqa: E402


class ForbiddenKiwoom:
    """모든 주문 시도를 기록하고 실제 전송은 하지 않는 가짜 클라이언트."""
    mock = True

    def __init__(self, balance=None):
        self.balance = balance or {}
        self.order = {}
        self.universe_realtime_transaction_info = {}
        self.orders_sent = []

    def send_order(self, *a, **k):
        self.orders_sent.append((a, k))
        return {'success': True, 'order_no': 'TEST'}

    def get_code_list_by_market(self, market):
        return CODE_LISTS[market]


CODE_LISTS = {
    "0": [{'code': f'{i:06d}', 'name': f'종목{i}'} for i in range(0, 300)],
    "10": [],
}


def make_strategy(env, monkeypatch, kiwoom=None, existing=260):
    st = RSIStrategy.__new__(RSIStrategy)
    st.kiwoom = kiwoom or ForbiddenKiwoom()
    st.strategy_name = 'RSIStrategy_test'
    st.mock_trade_blacklist = set()
    st.universe = {}
    st.last_universe_update = rs.get_korea_time() - pd.Timedelta(days=30)
    st.UNIVERSE_UPDATE_DAYS = 30
    st.universe_updated_today = False
    st.universe_update_attempted_today = False
    st.last_universe_rebuild_error = None
    st.universe_liquidation_mode = 'live'
    st.UNIVERSE_MIN_RATIO = 0.5
    st.REALTIME_MAX_CODES = 100
    st.POLLING_MAX_CODES = 200
    st._stop_event = SimpleNamespace(wait=lambda *_: None)
    st._delayed_messages = []
    st._delayed_messages_lock = __import__('threading').Lock()
    # 기존 Universe(DB) 준비
    old = pd.DataFrame({'code': [f'{i:06d}' for i in range(1000, 1000 + existing)],
                        'code_name': [f'OLD{i}' for i in range(existing)],
                        'created_at': ['20260814'] * existing})
    dbh.insert_df_to_db(st.strategy_name, 'universe', old)
    for _, r in old.iterrows():
        st.universe[r['code']] = {'code_name': r['code_name']}
    # 외부 부작용 차단
    st.messages = []
    monkeypatch.setattr(rs, 'send_message', lambda text, parse_mode=None: st.messages.append(text))
    monkeypatch.setattr(rs, 'get_korea_time', lambda: pd.Timestamp('2026-11-12 12:00', tz='Asia/Seoul').to_pydatetime())
    monkeypatch.setattr(st, 'check_and_get_price_data', lambda: None)
    monkeypatch.setattr(st, 'set_universe_real_time', lambda: None)
    return st


def db_codes(st):
    return {r[1] for r in dbh.execute_sql(st.strategy_name, "select * from universe").fetchall()}


def test_rebuild_failure_keeps_universe_db_and_no_success_notice(env, monkeypatch):
    kw = ForbiddenKiwoom(balance={'999999': {'종목명': 'HELD', '보유수량': 10, '매입가': 1000}})
    st = make_strategy(env, monkeypatch, kw)
    before_db, before_uni = db_codes(st), set(st.universe)
    before_update = st.last_universe_update
    monkeypatch.setattr(rs, 'get_universe', lambda **k: (_ for _ in ()).throw(UniverseDataError("네이버 크롤링 결과 0건")))

    st._run_universe_rebuild(rs.get_korea_time())

    assert st.universe_updated_today is False
    assert st.universe_update_attempted_today is True
    assert st.last_universe_update == before_update or st.last_universe_update.date() == pd.Timestamp('2026-08-14').date()
    assert db_codes(st) == before_db and set(st.universe) == before_uni
    assert kw.orders_sent == []                         # 시장가 청산 포함 주문 0건
    assert any('❌ Universe 재구성 실패' in m for m in st.messages)
    assert not any('✅ Universe 재구성 완료' in m for m in st.messages)


@pytest.mark.parametrize("bad_list", [[], ['종목1', '종목2']])
def test_empty_or_tiny_universe_never_replaces_existing(env, monkeypatch, bad_list):
    st = make_strategy(env, monkeypatch)
    before_db = db_codes(st)
    monkeypatch.setattr(rs, 'get_universe', lambda **k: bad_list)
    assert st.check_and_get_universe(force_update=True) is False
    assert db_codes(st) == before_db
    assert st.last_universe_rebuild_error


def test_shrunk_universe_below_ratio_rejected(env, monkeypatch):
    st = make_strategy(env, monkeypatch)  # 기존 260
    before_db = db_codes(st)
    monkeypatch.setattr(rs, 'get_universe', lambda **k: [f'종목{i}' for i in range(40)])  # 40 < 130
    assert st.check_and_get_universe(force_update=True) is False
    assert db_codes(st) == before_db


def test_rebuild_success_updates_state_and_notifies(env, monkeypatch):
    st = make_strategy(env, monkeypatch)
    monkeypatch.setattr(rs, 'get_universe', lambda **k: [f'종목{i}' for i in range(0, 250)])
    now = rs.get_korea_time()
    st._run_universe_rebuild(now)
    assert st.universe_updated_today is True
    assert st.last_universe_update == now
    assert len(db_codes(st)) == 250
    assert any('✅ Universe 재구성 완료' in m for m in st.messages)
    assert not any('❌' in m for m in st.messages)


def test_success_liquidation_dry_run_sends_no_orders(env, monkeypatch):
    kw = ForbiddenKiwoom(balance={'999999': {'종목명': 'HELD', '보유수량': 10, '매입가': 1000}})
    st = make_strategy(env, monkeypatch, kw)
    st.universe_liquidation_mode = 'dry_run'
    monkeypatch.setattr(rs, 'get_universe', lambda **k: [f'종목{i}' for i in range(0, 250)])
    assert st.update_universe_with_holdings() is True
    assert kw.orders_sent == []
    assert '999999' in st.universe  # 청산 생략 → Universe에 유지
    assert any('DRY-RUN' in m for m in st.messages)


def test_failed_rebuild_never_liquidates_even_in_live_mode(env, monkeypatch):
    kw = ForbiddenKiwoom(balance={'999999': {'종목명': 'HELD', '보유수량': 10, '매입가': 1000}})
    st = make_strategy(env, monkeypatch, kw)  # live 모드, 보유종목이 기존 Universe에 없음
    monkeypatch.setattr(rs, 'get_universe', lambda **k: (_ for _ in ()).throw(UniverseDataError("x")))
    assert st.update_universe_with_holdings() is False
    assert kw.orders_sent == []


def test_exception_in_update_is_not_treated_as_success(env, monkeypatch):
    st = make_strategy(env, monkeypatch)
    monkeypatch.setattr(st, 'update_universe_with_holdings', lambda: None)  # notify_on_exception 폴백 등
    st._run_universe_rebuild(rs.get_korea_time())
    assert st.universe_updated_today is False
    assert any('❌ Universe 재구성 실패' in m for m in st.messages)


# ---------------------------------------------------------------- Codex 리뷰 반영
def test_validate_rejects_unparseable_change_rate_and_foreign_ratio():
    for col in ('등락률', '외국인비율'):
        bad = make_kiwoom_df(1000)
        bad[col] = '-'
        with pytest.raises(UniverseDataError, match=col):
            validate_source_df(bad, 'x')


def test_crawler_per_market_schema_checked_before_concat(env, monkeypatch):
    """한 시장만 '거래량' 컬럼이 없어도 합산 전에 검출되어 저장되지 않아야 한다."""
    def page(with_volume, n=150):
        head = '<th>N</th><th>종목명</th><th>거래대금</th><th>시가총액</th><th>등락률</th><th>외국인비율</th>'
        head += '<th>거래량</th>' if with_volume else ''
        head += '<th>x</th>'
        cells = '<td class="number">1</td>' * (5 + (1 if with_volume else 0))
        rows = "".join(f'<tr><td class="no">{i}</td><td><a class="tltle" href="/x?code={i:06d}">N{i}</a></td>{cells}</tr>' for i in range(n))
        return f'<div class="box_type_l"><table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>'
    calls = {'n': 0}

    def post(*a, **k):
        calls['n'] += 1
        return Resp(page(with_volume=(calls['n'] == 1)))
    monkeypatch.setattr('requests.get', lambda *a, **k: Resp(page(True)))
    monkeypatch.setattr('requests.post', post)
    with pytest.raises(UniverseDataError, match="market=1"):
        mu.execute_crawler('all_stocks_naver.parquet')
    assert not (env.dir / 'all_stocks_naver.parquet').exists()


def test_filter_failure_after_valid_crawl_falls_back_to_cache(env, monkeypatch):
    make_kiwoom_df(4314).to_parquet(env.dir / 'all_stocks_kiwoom.parquet', index=True)
    calls = {'n': 0}
    real = mu._filter_and_create_universe

    def flaky(df, *a, **k):
        calls['n'] += 1
        if calls['n'] == 1:
            raise UniverseDataError("필터 실패")
        return real(df, *a, **k)
    monkeypatch.setattr(mu, '_filter_and_create_universe', flaky)
    monkeypatch.setattr(mu, 'execute_crawler', lambda *a, **k: make_kiwoom_df(800))
    out = mu.get_universe(kiwoom_client=None, max_codes=30, universe_output_file='universe.parquet')
    assert len(out) == 30 and calls['n'] == 2


@pytest.mark.parametrize("mode", ['dry-run', 'DRY_RUN', 'typo', ''])
def test_unknown_liquidation_mode_never_sends_orders(env, monkeypatch, mode):
    kw = ForbiddenKiwoom(balance={'999999': {'종목명': 'HELD', '보유수량': 10, '매입가': 1000}})
    st = make_strategy(env, monkeypatch, kw)
    st.universe_liquidation_mode = mode.strip().lower().replace('-', '_')
    st._liquidate_excluded_holdings({'999999'}, {'999999': {'code_name': 'HELD'}})
    assert kw.orders_sent == []


def test_ratio_baseline_is_db_not_accumulated_memory(env, monkeypatch):
    """메모리 Universe가 누적돼 커져도 DB 기준(260)으로 비교하므로 정상 재구성이 막히지 않는다."""
    st = make_strategy(env, monkeypatch)  # DB 260
    for i in range(2000, 2800):
        st.universe[f'{i:06d}'] = {'code_name': 'ACC'}  # 메모리 누적 모사
    monkeypatch.setattr(rs, 'get_universe', lambda **k: [f'종목{i}' for i in range(0, 200)])
    assert st.check_and_get_universe(force_update=True) is True
    assert len(db_codes(st)) == 200


def test_filter_fallback_skips_already_failed_cache(env, monkeypatch):
    """키움 캐시가 필터에서 실패하면 같은 캐시를 재선택하지 않고 네이버 캐시로 폴백한다."""
    make_kiwoom_df(4314).to_parquet(env.dir / 'all_stocks_kiwoom.parquet', index=True)
    make_kiwoom_df(900).to_parquet(env.dir / 'all_stocks_naver.parquet', index=True)
    os.utime(env.dir / 'all_stocks_naver.parquet', (time.time() - 3 * 86400,) * 2)
    _fail_crawl(env, monkeypatch)
    real = mu._filter_and_create_universe
    seen = []

    def filt(df, *a, **k):
        seen.append(len(df))
        if len(df) == 4314:
            raise UniverseDataError("키움 캐시 필터 실패")
        return real(df, *a, **k)
    monkeypatch.setattr(mu, '_filter_and_create_universe', filt)
    out = mu.get_universe(kiwoom_client=None, max_codes=30, universe_output_file='universe.parquet')
    assert len(out) == 30
    assert seen == [4314, 900]  # 키움 → 네이버 (키움 재시도 없음)


def _two_caches(env):
    make_kiwoom_df(4314).to_parquet(env.dir / 'all_stocks_kiwoom.parquet', index=True)
    make_kiwoom_df(900).to_parquet(env.dir / 'all_stocks_naver.parquet', index=True)
    t = time.time() - 3 * 86400
    os.utime(env.dir / 'all_stocks_naver.parquet', (t, t))


def _kiwoom_fails_filter(monkeypatch, seen):
    real = mu._filter_and_create_universe

    def filt(df, *a, **k):
        seen.append(len(df))
        if len(df) == 4314:
            raise UniverseDataError("키움 캐시 필터 실패")
        return real(df, *a, **k)
    monkeypatch.setattr(mu, '_filter_and_create_universe', filt)


def test_closed_market_auto_mode_skips_failed_kiwoom_cache(env, monkeypatch):
    """장 종료 후 자동 모드: 키움 fetch 결과가 필터에서 실패해도 같은 캐시를 재선택하지 않고 네이버 캐시로 폴백."""
    _two_caches(env)
    seen = []
    _kiwoom_fails_filter(monkeypatch, seen)
    monkeypatch.setattr(mu, 'check_transaction_closed', lambda: True)
    monkeypatch.setattr(mu, 'execute_crawler', lambda *a, **k: (_ for _ in ()).throw(AssertionError("크롤링으로 가면 안 됨")))
    # 키움 캐시를 읽어 반환하는 fetch (attrs에 출처 기록됨)
    monkeypatch.setattr(mu, 'fetch_all_stocks_from_kiwoom', lambda c, **k: _read_kiwoom(env))
    out = mu.get_universe(kiwoom_client=SimpleNamespace(), max_codes=30, universe_output_file='universe.parquet')
    assert len(out) == 30
    assert seen == [4314, 900]


def _read_kiwoom(env):
    df = pd.read_parquet(env.dir / 'all_stocks_kiwoom.parquet')
    df.attrs['cache_file'] = 'all_stocks_kiwoom.parquet'
    return df


def test_filter_from_cache_fallback_returns_none_when_all_fail(env, monkeypatch):
    _two_caches(env)
    monkeypatch.setattr(mu, '_filter_and_create_universe',
                        lambda *a, **k: (_ for _ in ()).throw(UniverseDataError("항상 실패")))
    out, err = mu._filter_from_cache_fallback(max_codes=30, universe_output_file=None)
    assert out is None and isinstance(err, UniverseDataError)


# ---------------------------------------------------------------- check_and_cache_if_needed (PR 리뷰 반영)
def _cache_check_setup(env, monkeypatch):
    monkeypatch.setenv('DB_DIR', str(env.dir))
    monkeypatch.delenv('DISABLE_INITIAL_CACHE', raising=False)
    st = make_strategy(env, monkeypatch)
    st.last_full_cache_time = None
    calls = []
    monkeypatch.setattr(mu, 'cache_daily_data', lambda kw: calls.append(1))  # 실제 API 수집 차단
    return st, calls


def test_fresh_empty_canonical_is_ignored_and_valid_kiwoom_cache_used(env, monkeypatch):
    """운영 재현: 방금 생성된 0건 canonical + 12일 전 정상 키움 캐시(4,314건) → 재수집 없이 키움 캐시 사용."""
    st, calls = _cache_check_setup(env, monkeypatch)
    pd.DataFrame().to_parquet(env.dir / 'all_stocks_canonical.parquet')  # mtime=지금
    kiwoom = env.dir / 'all_stocks_kiwoom.parquet'
    make_kiwoom_df(4314).to_parquet(kiwoom, index=True)
    t = time.time() - 12 * 86400
    os.utime(kiwoom, (t, t))
    st.check_and_cache_if_needed()
    assert calls == []                                   # 전체 재수집(API) 불필요
    assert st.last_full_cache_time is not None
    assert abs(st.last_full_cache_time.timestamp() - t) < 5  # 키움 캐시의 mtime 기준


def test_canonical_missing_columns_is_ignored(env, monkeypatch):
    st, calls = _cache_check_setup(env, monkeypatch)
    make_kiwoom_df(1000).drop(columns=['거래량']).to_parquet(env.dir / 'all_stocks_canonical.parquet', index=True)
    make_kiwoom_df(1000).to_parquet(env.dir / 'all_stocks_kiwoom.parquet', index=True)
    st.check_and_cache_if_needed()
    assert calls == []


def test_valid_fresh_canonical_still_used(env, monkeypatch):
    st, calls = _cache_check_setup(env, monkeypatch)
    make_kiwoom_df(4314).to_parquet(env.dir / 'all_stocks_canonical.parquet', index=True)
    st.check_and_cache_if_needed()
    assert calls == [] and st.last_full_cache_time is not None


def test_all_caches_invalid_triggers_recache_not_trust(env, monkeypatch):
    st, calls = _cache_check_setup(env, monkeypatch)
    pd.DataFrame().to_parquet(env.dir / 'all_stocks_canonical.parquet')
    pd.DataFrame().to_parquet(env.dir / 'all_stocks_kiwoom.parquet')
    st.check_and_cache_if_needed()
    assert calls == [1]                                  # 유효 캐시가 없으므로 재수집 경로
    assert st.kiwoom.orders_sent == []
