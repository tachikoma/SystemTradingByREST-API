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


def parse_api_list_sheet(df):
    """API 리스트 시트 파싱"""
    # 첫 행이 헤더
    df.columns = df.iloc[0]
    df = df.iloc[1:].reset_index(drop=True)
    df = clean_dataframe(df)
    
    return df


def parse_api_detail_sheet(df, sheet_name):
    """개별 API 상세 시트 파싱"""
    df = clean_dataframe(df)
    
    # 첫 컬럼을 키로 사용하는 key-value 구조 찾기
    result = {
        'sheet_name': sheet_name,
        'sections': []
    }
    
    current_section = None
    section_data = []
    
    for idx, row in df.iterrows():
        first_col = str(row.iloc[0]).strip()
        
        # 섹션 구분자 감지 (API 정보, 요청 파라미터, 응답 등)
        if first_col and not first_col.startswith('Unnamed'):
            # 이전 섹션 저장
            if current_section and section_data:
                result['sections'].append({
                    'title': current_section,
                    'data': section_data.copy()
                })
            
            # 새 섹션 시작
            current_section = first_col
            section_data = []
        
        # 데이터 행 추가
        if not first_col or first_col != current_section:
            row_data = [str(cell).strip() for cell in row if str(cell).strip()]
            if row_data:
                section_data.append(row_data)
    
    # 마지막 섹션 저장
    if current_section and section_data:
        result['sections'].append({
            'title': current_section,
            'data': section_data.copy()
        })
    
    return result


def format_table_markdown(data, has_header=True):
    """데이터를 마크다운 테이블로 포맷"""
    if not data:
        return ""
    
    lines = []
    
    if has_header and len(data) > 0:
        # 헤더
        header = data[0]
        lines.append("| " + " | ".join(header) + " |")
        lines.append("| " + " | ".join(["---"] * len(header)) + " |")
        
        # 데이터 행
        for row in data[1:]:
            # 컬럼 수 맞추기
            while len(row) < len(header):
                row.append("")
            lines.append("| " + " | ".join(row[:len(header)]) + " |")
    else:
        # 헤더 없이 모든 행 출력
        if data:
            max_cols = max(len(row) for row in data)
            for row in data:
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
    
    for section in api_data['sections']:
        title = section['title']
        data = section['data']
        
        # 섹션 제목
        lines.append(f"### {title}\n")
        
        # 데이터가 key-value 형태인지 테이블인지 판단
        if data and len(data[0]) == 2:
            # Key-Value 형태 (예: API 정보)
            for row in data:
                if len(row) >= 2:
                    lines.append(f"**{row[0]}**: {row[1]}\n")
        else:
            # 테이블 형태 (예: 요청 파라미터, 응답 필드)
            table = format_table_markdown(data, has_header=True)
            if table:
                lines.append(table + "\n")
        
        lines.append("")  # 섹션 간 공백
    
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
                # 마크다운 링크용 앵커 생성 (한글 지원)
                anchor = f"{api_name}({api_id})"
                lines.append(f"- [{api_name}]({anchor}.md) (`{api_id}`)")
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
