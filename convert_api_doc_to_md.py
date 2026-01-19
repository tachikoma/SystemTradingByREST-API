#!/usr/bin/env python3
"""
키움 REST API 문서 엑셀 파일을 AI 친화적인 마크다운으로 변환하는 스크립트

AI가 쉽게 검색하고 이해할 수 있도록:
1. 구조화된 헤딩 (H1, H2, H3)
2. 명확한 테이블 포맷
3. 코드 블록 (JSON 예제)
4. 목차(TOC) 자동 생성
5. 메타데이터 추가
"""

import pandas as pd
from pathlib import Path
from datetime import datetime
import re


def clean_dataframe(df):
    """빈 행/열 제거 및 데이터 정리"""
    # NaN을 빈 문자열로 변환
    df = df.fillna('')
    
    # 모든 컬럼이 비어있는 행 제거
    df = df[~(df.astype(str).apply(lambda x: x.str.strip()) == '').all(axis=1)]
    
    # 모든 행이 비어있는 컬럼 제거
    df = df.loc[:, ~(df.astype(str).apply(lambda x: x.str.strip()) == '').all()]
    
    return df


def _normalize_section_title(raw_title, prev_title=None, next_title=None, first_data_row=None):
    """원본 섹션 제목을 canonical 섹션명으로 매핑합니다."""
    if not raw_title:
        return raw_title
    s = str(raw_title).strip().lower()

    # 예제 문구가 포함된 경우 우선적으로 예제 매핑 처리
    if '예제' in s or 'example' in s:
        if 'request' in s or '요청' in s:
            return 'Request Example'
        if 'response' in s or '응답' in s:
            return 'Response Example'
        # 기본적으로 Request Example으로 처리
        return 'Request Example'

    # 'Body' 만 있는 경우 보통 응답 필드(Body: Response)로 쓰이는 경우가 많아 기본으로 Response 처리
    if 'body' in s and 'body(' not in s:
        return 'Response'

    # 직매핑 키워드
    mapping = {
        'api 정보': ['api 정보', 'api명', 'api 명', '서비스명', 'api 정보'],
        '기본 정보': ['기본 정보', '기본정보', '기본'],
        '개요': ['개요', '요약', '설명', '소개'],
        'request': ['request', '요청', 'header', '헤더', 'body(요청)', '요청 파라미터', 'request parameters', '요청 파라미터'],
        'response': ['response', '응답', '응답 필드', 'body(응답)', 'response fields'],
        'request example': ['request example', '요청 예제', '요청 예시', 'request 예제', '요청예제'],
        'response example': ['response example', '응답 예제', '응답 예시', 'response 예제', '응답예제']
    }

    for canon, keys in mapping.items():
        for k in keys:
            if k in s:
                # normalize spacing and casing for canonical names
                if canon == 'request':
                    return 'Request'
                if canon == 'response':
                    return 'Response'
                if canon == 'request example':
                    return 'Request Example'
                if canon == 'response example':
                    return 'Response Example'
                if canon == 'api 정보':
                    return 'API 정보'
                if canon == '기본 정보':
                    return '기본 정보'
                if canon == '개요':
                    return '개요'

    # 문맥 히어스틱: Header 만 있는 경우
    # 추가 안전장치: '예제' 키워드만 포함한 경우 request/response 판단
    if '예제' in s or 'example' in s:
        if 'request' in s or '요청' in s:
            return 'Request Example'
        if 'response' in s or '응답' in s:
            return 'Response Example'
        # 기본적으로 Request Example으로 처리
        return 'Request Example'

    if 'header' in s or s.strip() in ['헤더', 'header']:
        # 이전/다음 문맥으로 판단
        if prev_title and any(k in prev_title.lower() for k in ['api 정보', '기본']):
            return 'Request'
        if next_title and any(k in next_title.lower() for k in ['response', '응답']):
            return 'Request'
        # default
        return 'Request'

    # fallback: Title-case the original
    return raw_title.strip()


def _is_section_header(row):
    """행(row: list[str])이 섹션 헤더인지 간단히 판별합니다."""
    # 섹션 헤더는 보통 한 셀만 값이 있거나 첫 셀이 비어있지 않은 경우가 많음
    non_empty = [str(c).strip() for c in row if str(c).strip()]
    if len(non_empty) == 1:
        token = non_empty[0]
        # JSON-like 라인이나 단순 값('{', '}', '"key": ...')은 헤더가 아니다
        if re.match(r'^[\{\}\[\]\s"\']', token):
            return False
        # 헤더 토큰에 한글/영문자가 포함되어 있으면 섹션 헤더로 판단
        if re.search(r'[A-Za-z가-힣]', token):
            return True
        return False
    # 또는 첫 셀에 'Header','Request','Response','예제' 같은 키워드가 있으면 True
    first = str(row[0]).strip().lower() if len(row) > 0 else ''
    header_keywords = ['api', 'header', 'request', 'response', '요청', '응답', '예제', '개요', '기본']
    if any(k in first for k in header_keywords):
        return True
    return False


def _is_main_section_header(row, current_canon=None, current_raw=None):
    """메인(최상위) 섹션 헤더인지 판단합니다.
    current_canon이 'Request' 또는 'Response'일 경우, 'Header'/'Body' 같은
    서브 구분 토큰은 메인 헤더로 보지 않습니다.
    """
    if not _is_section_header(row):
        return False
    if current_canon and current_canon in ('Request', 'Response'):
        token = str(row[0]).strip().lower()
        # If the current section originally came from a bare 'header' title,
        # and we now see a bare 'body', treat 'body' as a main section header
        # (common pattern: Header -> Body mapping to Request/Response).
        if current_raw and current_raw in ('header', '헤더') and token in ('body', '바디'):
            return True
        if token in ('header', '헤더', 'body', '바디'):
            return False
    return True


def _contains_json_start(row):
    for c in row:
        if isinstance(c, str) and ('{' in c or '[' in c):
            return True
    return False


def _parse_json_block(df, start_idx):
    """start_idx에서 시작하는 연속된 JSON-like 블록을 추출합니다."""
    lines = []
    open_braces = 0
    open_brackets = 0
    i = start_idx
    seen_any = False
    while i < len(df):
        row = df.iloc[i].tolist()
        # join all cell texts in the row
        text = '\n'.join([str(c) for c in row if str(c).strip()])
        if not text.strip() and not seen_any:
            i += 1
            continue
        if not text.strip() and seen_any:
            break
        lines.append(text)
        seen_any = True
        open_braces += text.count('{') - text.count('}')
        open_brackets += text.count('[') - text.count(']')
        i += 1
        if open_braces <= 0 and open_brackets <= 0 and seen_any:
            break
    return ('\n'.join(lines)).strip(), i - 1


def _detect_header_row(rows):
    """간단한 헤더 판별: 첫 행이 열명(한글/영문)으로 보이는지 판단"""
    if not rows:
        return False
    first = rows[0]
    # header heuristics: contain non-numeric strings and not too long
    non_numeric = sum(1 for c in first if c and not re.match(r"^[0-9\-.,]+$", c))
    return non_numeric >= max(1, len(first) // 2)


def _classify_section_rows(section_rows):
    """section_rows는 {'type': 'row'|'json', 'cells': [...]|'text':...} 리스트입니다.
    반환값은 블록 리스트: {'type':'kv'|'table'|'json'|'rows', ...}
    """
    blocks = []
    # collect contiguous row blocks
    temp_rows = []
    for item in section_rows:
        if item.get('type') == 'json':
            # flush temp_rows
            if temp_rows:
                # decide kv or table
                # if most rows have exactly two non-empty cells -> kv
                non_empty_counts = [sum(1 for c in r if str(c).strip()) for r in temp_rows]
                two_col = sum(1 for n in non_empty_counts if n == 2)
                if two_col >= len(non_empty_counts) * 0.6:
                    items = []
                    for r in temp_rows:
                        cells = [str(c).strip() for c in r if str(c).strip()]
                        if len(cells) >= 2:
                            items.append([cells[0], cells[1]])
                    blocks.append({'type': 'kv', 'items': items})
                else:
                    # table
                    rows_copy = [[str(c).strip() for c in r] for r in temp_rows]
                    has_header = _detect_header_row(rows_copy)
                    blocks.append({'type': 'table', 'rows': rows_copy, 'has_header': has_header})
                temp_rows = []
            # add json block
            blocks.append({'type': 'json', 'text': item.get('text', '')})
        else:
            temp_rows.append(item.get('cells', []))

    # flush remaining temp_rows
    if temp_rows:
        non_empty_counts = [sum(1 for c in r if str(c).strip()) for r in temp_rows]
        two_col = sum(1 for n in non_empty_counts if n == 2)
        if two_col >= len(non_empty_counts) * 0.6:
            items = []
            for r in temp_rows:
                cells = [str(c).strip() for c in r if str(c).strip()]
                if len(cells) >= 2:
                    items.append([cells[0], cells[1]])
            blocks.append({'type': 'kv', 'items': items})
        else:
            rows_copy = [[str(c).strip() for c in r] for r in temp_rows]
            has_header = _detect_header_row(rows_copy)
            blocks.append({'type': 'table', 'rows': rows_copy, 'has_header': has_header})

    return blocks



def parse_api_list_sheet(df):
    """API 리스트 시트 파싱"""
    # 첫 행이 헤더
    df.columns = df.iloc[0]
    df = df.iloc[1:].reset_index(drop=True)
    df = clean_dataframe(df)
    
    return df


def parse_api_detail_sheet(df, sheet_name):
    """개별 API 상세 시트 파싱 (개선판)

    반환값 예시:
    {
      'sheet_name': sheet_name,
      'sections': [
         {'title': 'API 정보', 'content': [ {'type':'kv','items':[...]}, {'type':'json','text':...} ]},
         ...
      ]
    }
    """
    df = clean_dataframe(df)

    result = {'sheet_name': sheet_name, 'sections': []}

    rows = [list(df.iloc[i]) for i in range(len(df))]
    # 제거: 문서 상단의 '키움 REST API' 같은 잡요소 제거
    def _is_noise_row(r):
        for c in r:
            if c and isinstance(c, str) and '키움 rest api' in c.lower():
                return True
        return False
    rows = [r for r in rows if not _is_noise_row(r)]
    i = 0
    current_section = None
    prev_section_raw = None
    while i < len(rows):
        row = rows[i]
        if _is_section_header(row):
            raw_title = str(row[0]).strip() if row and str(row[0]).strip() else 'Unknown'
            # peek next non-empty row for context
            next_raw = None
            j = i + 1
            while j < len(rows) and all(not str(c).strip() for c in rows[j]):
                j += 1
            if j < len(rows):
                next_raw = str(rows[j][0]).strip()

            canon = _normalize_section_title(raw_title, prev_title=(prev_section_raw or ''), next_title=(next_raw or ''))
            # start collecting section content from next row
            section_items = []
            # record the original raw token used to start this section
            orig_raw = raw_title.strip().lower()
            i += 1
            while i < len(rows) and not _is_main_section_header(rows[i], current_canon=canon, current_raw=orig_raw):
                r = rows[i]
                # skip completely empty rows
                if all(not str(c).strip() for c in r):
                    i += 1
                    continue
                if _contains_json_start(r):
                    text, end_idx = _parse_json_block(df, i)
                    section_items.append({'type': 'json', 'text': text})
                    i = end_idx + 1
                    continue
                section_items.append({'type': 'row', 'cells': r})
                i += 1

            # 특별 처리: 'API 정보' 섹션은 항목|내용 2열 테이블로 강제 변환
            if canon == 'API 정보':
                # decide kv vs 2-column table based on row shapes
                temp_rows = [it.get('cells', []) for it in section_items if it.get('type') != 'json']
                non_empty_counts = [sum(1 for c in r if str(c).strip()) for r in temp_rows]
                two_col = sum(1 for n in non_empty_counts if n == 2)
                if temp_rows and two_col >= max(1, len(non_empty_counts) * 0.6):
                    items = []
                    for r in temp_rows:
                        cells = [str(c).strip() for c in r if str(c).strip()]
                        if len(cells) >= 2:
                            items.append([cells[0], cells[1]])
                    blocks = [{'type': 'kv', 'items': items}]
                else:
                    table_rows = [['항목', '내용']]
                    for it in section_items:
                        if it.get('type') == 'json':
                            table_rows.append(['예제(JSON)', it.get('text', '')])
                            continue
                        cells = [str(c).strip() for c in it.get('cells', []) if str(c).strip()]
                        if not cells:
                            continue
                        key = cells[0]
                        val = ' '.join(cells[1:]) if len(cells) > 1 else ''
                        table_rows.append([key, val])
                    blocks = [{'type': 'table', 'rows': table_rows, 'has_header': True}]
            else:
                # classify collected rows into blocks
                blocks = _classify_section_rows(section_items)
            # json-only 블록이면서 제목이 불명확하면 이전 섹션 문맥으로 예제(Request/Response) 결정
            if blocks and blocks[0].get('type') == 'json' and canon not in ('Request Example', 'Response Example'):
                if result['sections']:
                    prev_title = result['sections'][-1].get('title', '')
                    if prev_title == 'Request':
                        canon = 'Request Example'
                    elif prev_title == 'Response':
                        canon = 'Response Example'

            result['sections'].append({'title': canon, 'content': blocks})
            prev_section_raw = raw_title
            continue
        else:
            # No explicit section header: treat leading rows as '개요' until a header appears
            temp_items = []
            while i < len(rows) and not _is_section_header(rows[i]):
                r = rows[i]
                if all(not str(c).strip() for c in r):
                    i += 1
                    continue
                if _contains_json_start(r):
                    text, end_idx = _parse_json_block(df, i)
                    temp_items.append({'type': 'json', 'text': text})
                    i = end_idx + 1
                    continue
                temp_items.append({'type': 'row', 'cells': r})
                i += 1
            if temp_items:
                # Determine if these leading rows are actually API 정보 (common keys)
                header_tokens = [str(it.get('cells', [])[0]).strip().lower() for it in temp_items if it.get('type') == 'row' and it.get('cells')]
                api_info_keywords = ['이름', 'id', 'api id', 'api명', 'api 명', '메뉴 위치', '메뉴']
                is_api_info = any(any(k in tok for k in api_info_keywords) for tok in header_tokens)
                if is_api_info:
                    # normalize into API 정보 2-column table or kvs
                    temp_blocks = []
                    temp_rows_only = [it.get('cells', []) for it in temp_items if it.get('type') != 'json']
                    non_empty_counts = [sum(1 for c in r if str(c).strip()) for r in temp_rows_only]
                    two_col = sum(1 for n in non_empty_counts if n == 2)
                    if temp_rows_only and two_col >= max(1, len(non_empty_counts) * 0.6):
                        items = []
                        for r in temp_rows_only:
                            cells = [str(c).strip() for c in r if str(c).strip()]
                            if len(cells) >= 2:
                                items.append([cells[0], cells[1]])
                        temp_blocks = [{'type': 'kv', 'items': items}]
                    else:
                        table_rows = [['항목', '내용']]
                        for it in temp_items:
                            if it.get('type') == 'json':
                                table_rows.append(['예제(JSON)', it.get('text', '')])
                                continue
                            cells = [str(c).strip() for c in it.get('cells', []) if str(c).strip()]
                            if not cells:
                                continue
                            key = cells[0]
                            val = ' '.join(cells[1:]) if len(cells) > 1 else ''
                            table_rows.append([key, val])
                        temp_blocks = [{'type': 'table', 'rows': table_rows, 'has_header': True}]
                    result['sections'].append({'title': 'API 정보', 'content': temp_blocks})
                else:
                    blocks = _classify_section_rows(temp_items)
                    result['sections'].append({'title': '개요', 'content': blocks})
            continue

    return result


def format_table_markdown(data, has_header=True):
    """데이터를 마크다운 테이블로 포맷"""
    if not data:
        return ""

    # sanitize cells: escape pipe characters
    def _sanitize(cell):
        s = '' if cell is None else str(cell)
        # preserve multi-line descriptions by replacing newlines with HTML line breaks
        return s.replace('|', '\\|').replace('\r\n', '\n').replace('\n', '<br>')

    rows = [[_sanitize(c) for c in r] for r in data]

    if has_header and len(rows) > 0:
        header = rows[0]
        lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * len(header)) + " |"]
        for row in rows[1:]:
            while len(row) < len(header):
                row.append("")
            lines.append("| " + " | ".join(row[:len(header)]) + " |")
        return "\n".join(lines)

    # no header
    max_cols = max(len(r) for r in rows)
    lines = []
    for row in rows:
        while len(row) < max_cols:
            row.append("")
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def create_api_markdown(api_data):
    """개별 API를 마크다운으로 변환"""
    lines = []
    
    # 시트명에서 API ID 추출
    sheet_name = api_data['sheet_name']
    lines.append(f"## {sheet_name}\n")
    
    # Merge consecutive 'API 정보' sections and normalize API 정보 into a 2-column table
    merged_sections = []
    for section in api_data.get('sections', []):
        if merged_sections and section.get('title') == 'API 정보' and merged_sections[-1].get('title') == 'API 정보':
            # merge content lists
            merged_sections[-1]['content'].extend(section.get('content', []))
        else:
            merged_sections.append(section)

    # Normalize API 정보 content to a single 2-col table if present
    for sec in merged_sections:
        if sec.get('title') == 'API 정보':
            content = sec.get('content', [])
            table_rows = [['항목', '내용']]
            for block in content:
                btype = block.get('type')
                if btype == 'kv':
                    for k, v in block.get('items', []):
                        table_rows.append([k, v])
                elif btype == 'table':
                    for r in block.get('rows', [])[1:] if block.get('has_header') else block.get('rows', []):
                        # first non-empty cell is key, rest joined as value
                        cells = [c for c in r if str(c).strip()]
                        if not cells:
                            continue
                        key = cells[0]
                        val = ' '.join(cells[1:]) if len(cells) > 1 else ''
                        table_rows.append([key, val])
                elif btype == 'json':
                    table_rows.append(['예제(JSON)', block.get('text', '')])
            sec['content'] = [{'type': 'table', 'rows': table_rows, 'has_header': True}]
    
    for section in merged_sections:
        title = section.get('title')
        lines.append(f"### {title}\n")
        # new-style content blocks
        if 'content' in section:
            for block in section['content']:
                btype = block.get('type')
                if btype == 'kv':
                    for k, v in block.get('items', []):
                        lines.append(f"**{k}**: {v}\n")
                elif btype == 'table':
                    md = format_table_markdown(block.get('rows', []), has_header=block.get('has_header', True))
                    if md:
                        lines.append(md + "\n")
                elif btype == 'json':
                    lines.append('```json')
                    lines.append(block.get('text', ''))
                    lines.append('```\n')
                else:
                    # fallback: render rows
                    md = format_table_markdown(block.get('rows', []), has_header=False)
                    if md:
                        lines.append(md + "\n")
        else:
            # backward compatibility: old 'data' structure
            data = section.get('data', [])
            if data and len(data) > 0 and all(isinstance(r, list) for r in data):
                if len(data[0]) == 2:
                    for row in data:
                        if len(row) >= 2:
                            lines.append(f"**{row[0]}**: {row[1]}\n")
                else:
                    table = format_table_markdown(data, has_header=True)
                    if table:
                        lines.append(table + "\n")

        lines.append("")

    return "\n".join(lines)


def create_toc(api_list_df):
    """목차 생성"""
    lines = ["# 목차\n"]
    
    # 대분류별로 그룹화
    if 'No.' in api_list_df.columns and 'API 명' in api_list_df.columns:
        categories = {}
        
        for _, row in api_list_df.iterrows():
            category = str(row.get('대분류', '기타')).strip()
            api_name = str(row.get('API 명', '')).strip()
            api_id = str(row.get('API ID', '')).strip()
            
            if not category or category == 'nan':
                category = '기타'
            
            if category not in categories:
                categories[category] = []
            
            if api_name and api_id:
                categories[category].append((api_name, api_id))
        
        # 목차 생성
        for category, apis in sorted(categories.items()):
            lines.append(f"## {category}\n")
            for api_name, api_id in apis:
                # 실제 생성되는 파일명 규칙과 동일하게 특수문자 치환
                safe_name = re.sub(r'[<>:\"/\\|?*]', '_', f"{api_name}({api_id})")
                # 꺽쇠로 감싸서 공백이나 한글이 포함된 파일명도 링크로 정상 작동하게 함
                lines.append(f"- [{api_name}](<{safe_name}.md>) (`{api_id}`)")
            lines.append("")
    
    return "\n".join(lines)


def convert_excel_to_markdown(excel_file, output_dir):
    """메인 변환 함수"""
    print(f"📖 엑셀 파일 읽는 중: {excel_file}")
    xls = pd.ExcelFile(excel_file)
    
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    # API 리스트 시트 처리
    print("\n📋 API 리스트 처리 중...")
    api_list_df = pd.read_excel(xls, sheet_name='API 리스트')
    api_list_df = parse_api_list_sheet(api_list_df)
    
    # README 생성 (목차 + API 리스트)
    print("📝 README.md 생성 중...")
    readme_lines = []
    readme_lines.append("# 키움 REST API 문서\n")
    readme_lines.append(f"> 생성일: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    readme_lines.append(f"> 총 API 개수: {len(xls.sheet_names) - 2} (API 리스트 시트 제외)\n")
    readme_lines.append("---\n")
    
    # 목차
    readme_lines.append(create_toc(api_list_df))
    readme_lines.append("\n---\n")
    
    # 전체 API 리스트 테이블
    readme_lines.append("## 전체 API 리스트\n")
    readme_lines.append(api_list_df.to_markdown(index=False))
    
    # README 저장
    readme_file = output_path / "README.md"
    with open(readme_file, 'w', encoding='utf-8') as f:
        f.write("\n".join(readme_lines))
    print(f"✅ {readme_file} 생성 완료")
    
    # 개별 API 문서 생성
    print(f"\n📄 개별 API 문서 생성 중 (총 {len(xls.sheet_names) - 2}개)...")
    api_count = 0
    
    for sheet_name in xls.sheet_names[1:]:  # API 리스트 시트 제외
        if sheet_name == '오류코드':
            # 오류코드는 별도 처리
            print("  ⚠️  오류코드 시트 처리 중...")
            df = pd.read_excel(xls, sheet_name=sheet_name)
            df = clean_dataframe(df)
            
            error_lines = ["# 오류 코드\n"]
            error_lines.append(df.to_markdown(index=False))
            
            error_file = output_path / "ERROR_CODES.md"
            with open(error_file, 'w', encoding='utf-8') as f:
                f.write("\n".join(error_lines))
            print(f"  ✅ {error_file.name} 생성 완료")
            continue
        
        try:
            df = pd.read_excel(xls, sheet_name=sheet_name)
            api_data = parse_api_detail_sheet(df, sheet_name)
            markdown = create_api_markdown(api_data)
            
            # 파일명 생성 (특수문자 제거)
            safe_filename = re.sub(r'[<>:"/\\|?*]', '_', sheet_name)
            api_file = output_path / f"{safe_filename}.md"
            
            with open(api_file, 'w', encoding='utf-8') as f:
                f.write(markdown)
            
            api_count += 1
            if api_count % 20 == 0:
                print(f"  진행 중... {api_count}/{len(xls.sheet_names) - 2}")
        
        except Exception as e:
            print(f"  ⚠️  {sheet_name} 처리 실패: {e}")
    
    print(f"\n✨ 완료! 총 {api_count}개 API 문서 생성")
    print(f"📁 출력 디렉토리: {output_path.absolute()}")
    print(f"\n💡 사용 방법:")
    print(f"   1. README.md에서 전체 API 목록 확인")
    print(f"   2. 개별 API 문서는 '카테고리(API_ID).md' 형식")
    print(f"   3. 오류 코드는 ERROR_CODES.md 참조")


if __name__ == "__main__":
    # 현재 디렉토리에서 실행
    excel_file = "키움 REST API 문서.xlsx"
    output_dir = "docs/kiwoom_api"
    
    if not Path(excel_file).exists():
        print(f"❌ 오류: {excel_file} 파일을 찾을 수 없습니다.")
        exit(1)
    
    convert_excel_to_markdown(excel_file, output_dir)
