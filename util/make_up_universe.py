import requests
from bs4 import BeautifulSoup
import numpy as np
import pandas as pd
from datetime import datetime, time as datetime_time
from zoneinfo import ZoneInfo
import logging
import os

BASE_URL = 'https://finance.naver.com/sise/sise_market_sum.nhn?sosok='
CODES = [0, 1]  # KOSPI:0, KOSDAQ:1
START_PAGE = 1
fields = []
now = datetime.now(ZoneInfo("Asia/Seoul"))
formattedDate = now.strftime("%Y%m%d")

logger = logging.getLogger(__name__)


def is_market_hours():
    """
    장시간인지 확인하는 함수
    평일 09:00 ~ 15:30 사이를 장시간으로 판단
    """
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    
    # 주말 체크
    if now.weekday() >= 5:  # 5=토요일, 6=일요일
        return False
    
    # 장시작: 09:00, 장마감: 15:30
    market_open = datetime_time(9, 0)
    market_close = datetime_time(15, 30)
    
    current_time = now.time()
    
    return market_open <= current_time <= market_close


def execute_crawler():
    # KOSPI, KOSDAQ 종목을 하나로 합치는데 사용할 변수
    df_total = []

    # CODES에 담긴 KOSPI, KOSDAQ 종목 모두를 크롤링하기 위해 for문을 사용
    for code in CODES:

        # 전체 페이지 개수를 가져오기 위한 코드
        res = requests.get(BASE_URL + str(CODES[0]))
        page_soup = BeautifulSoup(res.text, 'lxml')

        # '맨뒤'에 해당하는 태그를 기준으로 전체 페이지 개수 추출하기
        total_page_num = page_soup.select_one('td.pgRR > a')
        total_page_num = int(total_page_num.get('href').split('=')[-1])

        # 조회할 수 있는 항목정보들 추출
        ipt_html = page_soup.select_one('div.subcnt_sise_item_top')

        # 전역변수 fields에 항목들을 담아 다른 함수에서도 접근가능하도록 만듬
        global fields
        fields = [item.get('value') for item in ipt_html.select('input')]

        # page마다 존재하는 모든 종목들의 항목정보를 크롤링해서 result에 저장(여기서 crawler 함수가 한 페이씩 크롤링해오는 역할을 담당)
        result = [crawler(code, str(page)) for page in range(1, total_page_num + 1)]

        # 전체 페이지를 저장한 result를 하나의 데이터프레임으로 만듬
        df = pd.concat(result, axis=0, ignore_index=True)

        # 변수 df는 KOSPI, KOSDAQ별로 크롤링한 종목 정보이고 이를 하나로 합치기 위해 df_total에 추가
        df_total.append(df)

    # df_total를 하나의 데이터프레임으로 만듬
    df_total = pd.concat(df_total)

    # 합친 데이터프레임의 index 번호를 새로 매김
    df_total.reset_index(inplace=True, drop=True)

    # 전체 크롤링 결과를 엑셀 출력
    df_total.to_excel('NaverFinance.xlsx')

    # 크롤링 결과를 반환
    return df_total


def crawler(code, page):

    global fields

    # Naver finance에 전달할 값들 세팅(요청을 보낼 때는 menu, fieldIds, returnUrl을 지정해서 보내야 함)
    data = {'menu': 'market_sum',
            'fieldIds': fields,
            'returnUrl': BASE_URL + str(code) + "&page=" + str(page)}

    # 네이버로 요청을 전달(post방식)
    res = requests.post('https://finance.naver.com/sise/field_submit.nhn', data=data)

    page_soup = BeautifulSoup(res.text, 'lxml')

    # 크롤링할 table의 html 가져오는 코드(크롤링 대상 요소의 클래스는 브라우저에서 확인)
    table_html = page_soup.select_one('div.box_type_l')

    # column명을 가공
    header_data = [item.get_text().strip() for item in table_html.select('thead th')][1:-1]

    # 종목명 + 수치 추출 (a.title = 종목명, td.number = 기타 수치)
    inner_data = [item.get_text().strip() for item in table_html.find_all(lambda x:
                                                                          (x.name == 'a' and
                                                                           'tltle' in x.get('class', [])) or
                                                                          (x.name == 'td' and
                                                                           'number' in x.get('class', []))
                                                                          )]

    # page마다 있는 종목의 순번 가져오기
    no_data = [item.get_text().strip() for item in table_html.select('td.no')]
    number_data = np.array(inner_data)

    # 가로 x 세로 크기에 맞게 행렬화
    number_data.resize(len(no_data), len(header_data))

    # 한 페이지에서 얻은 정보를 모아 DataFrame로 만들어 반환
    df = pd.DataFrame(data=number_data, columns=header_data)
    return df


def get_universe():
    """
    유니버스를 생성하는 함수
    장시간이 아니고 NaverFinance.xlsx 파일이 있으면 기존 파일 사용
    장시간이거나 파일이 없으면 크롤링 실행
    """
    excel_file = 'NaverFinance.xlsx'
    
    # 장시간이 아니고 기존 파일이 있으면 파일 로드
    if not is_market_hours() and os.path.exists(excel_file):
        logger.info(f"장시간이 아닙니다. 기존 {excel_file} 파일을 사용합니다.")
        print(f"장시간이 아닙니다. 기존 {excel_file} 파일을 사용합니다.")
        df = pd.read_excel(excel_file, index_col=0)
    else:
        # 장시간이거나 파일이 없으면 크롤링 실행
        if is_market_hours():
            logger.info("장시간입니다. 크롤링을 실행합니다.")
            print("장시간입니다. 크롤링을 실행합니다.")
        else:
            logger.info(f"{excel_file} 파일이 없습니다. 크롤링을 실행합니다.")
            print(f"{excel_file} 파일이 없습니다. 크롤링을 실행합니다.")
        
        # 크롤링 결과를 얻어옴
        df = execute_crawler()

    mapping = {',': '', 'N/A': '0', '%': ''}
    df.replace(mapping, regex=True, inplace=True)

    # 사용할 column들 설정 (RSI 전략에 최적화)
    cols = ['거래량', '거래대금', '시가총액', '등락률', '외국인비율']

    # column들을 숫자타입으로 변환(Naver Finance를 크롤링해온 데이터는 str 형태)
    for col in cols:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    
    # NaN이 생긴 행 제거
    df = df.dropna(subset=cols)
    
    # 음수 등락률 절대값 처리 필요 (등락률은 이미 숫자)
    if len(df) == 0:
        logger.warning("필터링 후 데이터가 없습니다.")
        return []

    # ===== RSI(2) 전략에 최적화된 Universe 구성 =====
    # 1. 기본 필터링: 유동성 + 적절한 시가총액 범위
    # 거래대금/시가총액 단위: 백만원
    df = df[
        (df['거래대금'] > 3000) &              # 30억 이상 (유동성 확보)
        (df['시가총액'] > 50000) &             # 500억 이상 (최소 규모)
        (df['시가총액'] < 5000000) &           # 5조 미만 (대형 우량주 제외)
        (df['거래량'] > 0) &                   # 거래량 있는 종목
        (~df.종목명.str.contains("지주", na=False)) &    # 지주회사 제외
        (~df.종목명.str.contains("홀딩스", na=False)) &  # 홀딩스 제외
        (~df.종목명.str.contains("스팩", na=False)) &    # 스팩 제외
        (~df.종목명.str.contains("리츠", na=False)) &    # 리츠 제외
        (~df.종목명.str.contains("우", na=False))        # 우선주 제외
    ]

    # 2. 변동성 지표 계산
    # - 등락률 절대값: 당일 변동성
    # - 외국인비율: 유동성 대리변수
    df['변동성_지표'] = abs(df['등락률'])
    
    # 3. 거래 활발도 계산 (거래대금 대비 시가총액 비율)
    df['거래회전율'] = df['거래대금'] / df['시가총액'] * 100
    
    # 4. 변동성 + 거래활발도 기준 종합 점수
    # 변동성 상위 50% + 거래회전율 상위 50% 종목 선호
    df['변동성_순위'] = df['변동성_지표'].rank(method='max', ascending=False)
    df['거래회전율_순위'] = df['거래회전율'].rank(method='max', ascending=False)
    df['종합_순위'] = (df['변동성_순위'] + df['거래회전율_순위']) / 2

    # 5. 종합 순위로 정렬
    df = df.sort_values(by=['종합_순위'])

    # 필터링한 데이터프레임의 index 번호를 새로 매김
    df.reset_index(inplace=True, drop=True)

    # 상위 100개만 추출
    df = df.loc[:99]

    # 유니버스 생성 결과를 엑셀 출력
    df.to_excel('universe.xlsx')
    return df['종목명'].tolist()


if __name__ == "__main__":
    print('Start!')
    universe = get_universe()
    print(universe)
    print('End')