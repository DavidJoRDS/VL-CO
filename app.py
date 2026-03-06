import streamlit as st
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import requests
import os
import zipfile
import shutil
import re
from io import BytesIO
from PIL import Image as PILImage
import openpyxl
from openpyxl.drawing.image import Image as XLImage
from urllib.parse import urljoin
from openpyxl.styles import Alignment, Font
import datetime

st.set_page_config(page_title="VL&CO 상품크롤러", layout="wide")
st.title("🚀 VL&CO 최종 통합 크롤러")

if 'excel_data' not in st.session_state:
    st.session_state.excel_data = None
if 'zip_data' not in st.session_state:
    st.session_state.zip_data = None

target_url = st.text_input("크롤링할 사이트 주소", value="")

def extract_prices(item_element):
    """요소 내의 모든 텍스트를 분석하여 정가와 할인가를 정확히 분리"""
    # innerText를 사용하여 줄바꿈이 포함된 텍스트 전체 획득
    full_text = item_element.text.strip()
    lines = [l.strip() for l in full_text.split('\n') if l.strip()]
    
    # 숫자와 가격 관련 기호가 포함된 모든 문구 추출
    price_candidates = []
    for line in lines:
        if any(c.isdigit() for c in line) and any(x in line for x in ['원', '₩', 'KRW', ',']):
            # 한 줄에 여러 가격이 있을 경우 분리 (예: "100,000 80,000")
            matches = re.findall(r'[0-9,]{3,}', line)
            if len(matches) > 1:
                price_candidates.extend([m + "원" if '원' in line else m for m in matches])
            else:
                price_candidates.append(line)

    # 중복 제거 및 정리
    final_p = []
    for p in price_candidates:
        if p not in final_p: final_p.append(p)

    if len(final_p) >= 2:
        return final_p[0], final_p[1] # 첫 번째가 보통 정가, 두 번째가 할인가
    return (final_p[0], "-") if final_p else ("정보없음", "-")

if st.button("🚀 데이터 수집 시작"):
    with st.spinner('레이아웃과 가격 데이터를 정밀하게 분석 중입니다...'):
        IMG_FOLDER = "collected_images"
        if os.path.exists(IMG_FOLDER): shutil.rmtree(IMG_FOLDER)
        os.makedirs(IMG_FOLDER)

        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        # [수정] 행 높이 계산을 위해 이미지 로딩 비활성화 옵션 제거
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

        try:
            driver = webdriver.Chrome(options=options)
            driver.get(target_url)
            
            # 페이지 로딩 대기
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            
            # 효율적 스크롤 (개수 확보를 위해 3번 수행)
            for _ in range(3):
                driver.execute_script("window.scrollBy(0, 2000);")
                time.sleep(1)

            # 탐색 범위 확장
            items = driver.find_elements(By.CSS_SELECTOR, "li, div[class*='item'], div[class*='product'], a[class*='product']")
            
            final_results = []
            seen_links = set()

            for item in items:
                try:
                    if item.size['width'] < 100: continue
                    link_tag = item if item.tag_name == 'a' else item.find_element(By.TAG_NAME, 'a')
                    link = link_tag.get_attribute('href')
                    if not link or link in seen_links or "javascript" in link: continue

                    # 이미지 추출 로직 (컬러칩 제외)
                    img_urls = []
                    imgs = item.find_elements(By.TAG_NAME, "img")
                    for img in imgs:
                        src = img.get_attribute('data-src') or img.get_attribute('src') or img.get_attribute('data-original')
                        if not src or any(x in src.lower() for x in ['swatch', 'color', 'icon']): continue
                        img_urls.append(urljoin(target_url, src))
                        if len(img_urls) >= 2: break
                    
                    if not img_urls: continue

                    # 가격 추출 (보완된 로직 적용)
                    reg_price, sale_price = extract_prices(item)
                    product_name = item.text.split('\n')[0]

                    final_results.append({
                        "제품명": product_name[:80],
                        "정가": reg_price, "세일가": sale_price,
                        "링크": link, "이미지들": img_urls
                    })
                    seen_links.add(link)
                except: continue

            if final_results:
                wb = openpyxl.Workbook()
                ws = wb.active
                ws.append(["번호", "제품명", "이미지1", "이미지2", "정가", "세일가", "상세링크"])
                
                # [수정] 엑셀 행/열 너비 최적화 (첨부파일 스타일 복구)
                ws.column_dimensions['B'].width = 40
                ws.column_dimensions['C'].width = 30
                ws.column_dimensions['D'].width = 30
                ws.column_dimensions['E'].width = 20
                ws.column_dimensions['F'].width = 20

                for i, data in enumerate(final_results, start=1):
                    row_idx = i + 1
                    # [핵심] 이미지 크기에 맞는 넉넉한 행 높이 설정
                    ws.row_dimensions[row_idx].height = 180 
                    
                    ws.cell(row=row_idx, column=1, value=i).alignment = Alignment(horizontal='center', vertical='center')
                    ws.cell(row=row_idx, column=2, value=data["제품명"]).alignment = Alignment(wrap_text=True, vertical='center')
                    ws.cell(row=row_idx, column=5, value=data["정가"]).alignment = Alignment(horizontal='center', vertical='center')
                    
                    # 세일가 강조 표시 복구
                    s_cell = ws.cell(row=row_idx, column=6, value=data["세일가"])
                    s_cell.alignment = Alignment(horizontal='center', vertical='center')
                    if data["세일가"] != "-":
                        s_cell.font = Font(color="FF0000", bold=True)
                    
                    ws.cell(row=row_idx, column=7, value="상세보기").hyperlink = data["링크"]

                    for j, img_url in enumerate(data["이미지들"]):
                        try:
                            res = requests.get(img_url, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
                            img_pil = PILImage.open(BytesIO(res.content)).convert("RGB")
                            
                            # 썸네일 생성 및 삽입
                            img_thumb = img_pil.copy()
                            img_thumb.thumbnail((220, 220)) # 썸네일 크기 소폭 확대
                            t_path = os.path.join(IMG_FOLDER, f"t_{row_idx}_{j}.png")
                            img_thumb.save(t_path)
                            ws.add_image(XLImage(t_path), f"{'C' if j==0 else 'D'}{row_idx}")
                            
                            # 고화질 원본 저장 (ZIP용)
                            img_pil.save(os.path.join(IMG_FOLDER, f"{i}_{j+1}.jpg"), "JPEG", quality=85)
                        except: continue

                excel_io = BytesIO()
                wb.save(excel_io)
                st.session_state.excel_data = excel_io.getvalue()
                
                zip_io = BytesIO()
                with zipfile.ZipFile(zip_io, "w") as zf:
                    for root, _, files in os.walk(IMG_FOLDER):
                        for f in files:
                            if not f.startswith("t_"): zf.write(os.path.join(root, f), f)
                st.session_state.zip_data = zip_io.getvalue()
                st.session_state.result_count = len(final_results)
                shutil.rmtree(IMG_FOLDER)
            else:
                st.warning("상품 정보를 찾을 수 없습니다.")
        finally:
            if 'driver' in locals(): driver.quit()

if st.session_state.excel_data:
    st.success(f"✅ {st.session_state.result_count}개 상품 수집 완료!")
    c1, c2 = st.columns(2)
    with c1: st.download_button("📥 엑셀 다운로드", st.session_state.excel_data, "result.xlsx")
    with c2: st.download_button("🖼️ 이미지(ZIP) 다운로드", st.session_state.zip_data, "images.zip")