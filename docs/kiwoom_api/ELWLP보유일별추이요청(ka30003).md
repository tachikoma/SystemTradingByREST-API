## ELWLP보유일별추이요청(ka30003)

### Header

| authorization | 접근토큰 | String | Y | 1000 | 토큰 지정시 토큰타입("Bearer") 붙혀서 호출 
 예) Bearer Egicyx... |
| --- | --- | --- | --- | --- | --- |
| cont-yn | 연속조회여부 | String | N | 1 | 응답 Header의 연속조회여부값이 Y일 경우 다음데이터 요청시 응답 Header의 cont-yn값 세팅 |
| next-key | 연속조회키 | String | N | 50 | 응답 Header의 연속조회여부값이 Y일 경우 다음데이터 요청시 응답 Header의 next-key값 세팅 |


### Body

| base_dt | 기준일자 | String | Y | 8 | YYYYMMDD |
| --- | --- | --- | --- | --- | --- |


### Header

| cont-yn | 연속조회여부 | String | N | 1 | 다음 데이터가 있을시 Y값 전달 |
| --- | --- | --- | --- | --- | --- |
| next-key | 연속조회키 | String | N | 50 | 다음 데이터가 있을시 다음 키값 전달 |


### Body

| - dt | 일자 | String | N | 20 |
| --- | --- | --- | --- | --- |
| - cur_prc | 현재가 | String | N | 20 |
| - pre_tp | 대비구분 | String | N | 20 |
| - pred_pre | 전일대비 | String | N | 20 |
| - flu_rt | 등락율 | String | N | 20 |
| - trde_qty | 거래량 | String | N | 20 |
| - trde_prica | 거래대금 | String | N | 20 |
| - chg_qty | 변동수량 | String | N | 20 |
| - lprmnd_qty | LP보유수량 | String | N | 20 |
| - wght | 비중 | String | N | 20 |

