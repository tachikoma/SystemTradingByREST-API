import types
import pytest
import util.make_up_universe as mu

# Minimal HTML that mimics Naver table structure expected by crawler
SAMPLE_HTML = '''
<div class="box_type_l">
  <table>
    <thead>
      <tr>
        <th></th>
        <th>종목명</th>
        <th>현재가</th>
        <th></th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td class="no">1</td>
        <td><a class="tltle" href="/item/main.nhn?code=0001">AAA</a></td>
        <td class="number">1000</td>
      </tr>
      <tr>
        <td class="no">2</td>
        <td><a class="tltle" href="/item/main.nhn?code=0002">BBB</a></td>
        <td class="number">2000</td>
      </tr>
    </tbody>
  </table>
</div>
'''

class DummyResp:
    def __init__(self, text):
        self.text = text


def test_crawler_parses_table(monkeypatch):
    # Patch requests.post used by crawler to return SAMPLE_HTML
    monkeypatch.setattr('requests.post', lambda *a, **k: DummyResp(SAMPLE_HTML))

    # Call crawler with explicit fields and verify resulting DataFrame
    df = mu.crawler(0, '1', fields=['open', 'high'])

    # Expect 종목코드 column inserted and two rows
    assert '종목코드' in df.columns
    assert len(df) == 2
    assert df.loc[0, '종목코드'] == '0001'
    assert df.loc[1, '종목코드'] == '0002'
    # 확인: header (종목명, 현재가) 포함
    assert '종목명' in df.columns
    assert '현재가' in df.columns
