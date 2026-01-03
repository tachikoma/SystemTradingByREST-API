## ETF NAV(0G)

### Header

| authorization | 접근토큰 | String | Y | 1000 | 토큰 지정시 토큰타입("Bearer") 붙혀서 호출 
 예) Bearer Egicyx... |
| --- | --- | --- | --- | --- | --- |
| cont-yn | 연속조회여부 | String | N | 1 | 응답 Header의 연속조회여부값이 Y일 경우 다음데이터 요청시 응답 Header의 cont-yn값 세팅 |
| next-key | 연속조회키 | String | N | 50 | 응답 Header의 연속조회여부값이 Y일 경우 다음데이터 요청시 응답 Header의 next-key값 세팅 |


### Body

| grp_no | 그룹번호 | String | Y | 4 |
| --- | --- | --- | --- | --- |
| refresh | 기존등록유지여부 | String | Y | 1 |
| data | 실시간 등록 리스트 | LIST |  |  |
| - item | 실시간 등록 요소 | String | N | 100 |
| - type | 실시간 항목 | String | Y | 2 |


### Header

| cont-yn | 연속조회여부 | String | N | 1 | 다음 데이터가 있을시 Y값 전달 |
| --- | --- | --- | --- | --- | --- |
| next-key | 연속조회키 | String | N | 50 | 다음 데이터가 있을시 다음 키값 전달 |


### Body

| return_msg | 결과메시지 | String | N | 통신결과에대한메시지 |
| --- | --- | --- | --- | --- |
| trnm | 서비스명 | String | N | 등록,해지요청시 요청값 반환 , 실시간수신시 REAL 반환 |
| data | 실시간 등록리스트 | LIST | N |  |
| - type | 실시간항목 | String | N | TR 명(0A,0B....) |
| - name | 실시간 항목명 | String | N |  |
| - item | 실시간 등록 요소 | String | N | 종목코드 |
| - values | 실시간 값 리스트 | LIST | N |  |
| - - 36 | NAV | String | N |  |
| - - 37 | NAV전일대비 | String | N |  |
| - - 38 | NAV등락율 | String | N |  |
| - - 39 | 추적오차율 | String | N |  |
| - - 20 | 체결시간 | String | N |  |
| - - 10 | 현재가 | String | N |  |
| - - 11 | 전일대비 | String | N |  |
| - - 12 | 등락율 | String | N |  |
| - - 13 | 누적거래량 | String | N |  |
| - - 25 | 전일대비기호 | String | N |  |
| - - 667 | ELW기어링비율 | String | N |  |
| - - 668 | ELW손익분기율 | String | N |  |
| - - 669 | ELW자본지지점 | String | N |  |
| - - 265 | NAV/지수괴리율 | String | N |  |
| - - 266 | NAV/ETF괴리율 | String | N |  |

