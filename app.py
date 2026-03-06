import streamlit as st
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
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
st.title("🛒 VL&CO 상품크롤러 (세일가 완벽 보완)")

if 'excel_data' not in st.session_state:
    st.session_state.excel_data = None
if 'zip_data' not in st.session_state:
    st.session_state.zip_data = None

target_url = st.text_input("크롤링할 사이트 주소를 입력하세요", value="")

def clean_filename(filename):
    return re.sub(r'[\\/*?:"<>|]', "", filename)

def get_refined_prices(item_element):
    """이전 코드의 '순차 추출' 장점을 살려 가격을 분리"""
    # innerText를 가져와서 줄바꿈 단위로 분리
    full_text = item_element.text.strip()
    lines = [l.strip() for l in full_text.split('\n') if l.strip()]
    
    price_candidates = []
    for line in lines:
        # 1. 화폐 단위나 콤마가 포함된 줄을 찾음
        if any(x in line for x in ['원', 'KRW', '₩', 'JPY', 'USD', ',']) and any(c.isdigit() for c in line):
            # 2. 한 줄에 가격이 여러 개(정가+세일가) 있는 경우를 대비해 숫자 패턴으로 재분할
            # 콤마 포함 숫자를 찾되, 너무 짧은 숫자는 제외
            found = re.findall(r'[0-9,]{3,}', line)
            if len(found) >= 2:
                # 한 줄에 두 개 이상의 가격이 있다면 각각 추가
                for f in found:
                    price_candidates.append(f.strip())
            else:
                # 한 줄에 하나라면 줄 전체 추가 (단위 포함 목적)
                price_candidates.append(line)

    # 중복 제거 (순서 유지)
    unique_prices = []
    for p in price_candidates:
        if p not in unique_prices: unique_prices.append(p)

    # 최종 배정: 첫 번째 발견 가격은 정가, 두 번째는 세일가
    reg_p = unique_prices[0] if len(unique_prices) >= 1 else "정보없음"
    sale_p = unique_prices[1] if len(unique_prices) >= 2 else "-"
    
    return reg_p, sale_p

if st.button("🚀 데이터 수집 시작"):
    with st.spinner('가격 정보를 정밀 분석하며 수집 중입니다...'):
        IMG_FOLDER = "collected_images"
        if os.path.exists(IMG_FOLDER): shutil.rmtree(IMG_FOLDER)
        os.makedirs(IMG_FOLDER)

        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

        try:
            driver = webdriver.Chrome(options=options)
            driver.get(target_url)
            time.sleep(5)

            # 스크롤 최적화
            for _ in range(3):
                driver.execute_script("window.scrollBy(0, 2000);")
                time.sleep(1.5)

            # 상품 탐색 (범용 선택자 유지)
            items = driver.find_elements(By.CSS_SELECTOR, "li, div[class*='item'], div[class*='product'], a[class*='product']")
            
            final_results = []
            seen_links = set()

            for item in items:
                try:
                    if item.size['width'] < 100: continue
                    link_tag = item if item.tag_name == 'a' else item.find_element(By.TAG_NAME, 'a')
                    link = link_tag.get_attribute('href')
                    if not link or link in seen_links or "javascript" in link: continue

                    # 가격 추출 (보완된 로직 적용)
                    reg_price, sale_price = get_refined_prices(item)
                    
                    # 상품명 추출 (배너 제외)
                    lines = [l.strip() for l in item.text.split('\n') if l.strip()]
                    p_name = lines[0] if lines else "상품명 없음"
                    if len(p_name) < 2: p_name = lines[1] if len(lines) > 1 else p_name

                    # 이미지 추출
                    img_urls = []
                    imgs = item.find_elements(By.TAG_NAME, "img")
                    for img in imgs:
                        src = img.get_attribute('data-src') or img.get_attribute('src') or img.get_attribute('data-original')
                        if not src or any(x in src.lower() for x in ['swatch', 'color', 'icon']): continue
                        img_urls.append(urljoin(target_url, src))
                        if len(img_urls) >= 2: break
                    
                    if not img_urls: continue

                    final_results.append({
                        "제품명": p_name[:80], "정가": reg_price, "세일가": sale_price,
                        "링크": link, "이미지들": img_urls
                    })
                    seen_links.add(link)
                except: continue

            if final_results:
                wb = openpyxl.Workbook()
                ws = wb.active
                ws.append(["번호", "제품명", "이미지1", "이미지2", "정가", "세일가", "상세링크"])
                
                # 엑셀 스타일 및 너비 설정
                ws.column_dimensions['B'].width = 40
                ws.column_dimensions['C'].width = 30
                ws.column_dimensions['D'].width = 30
                ws.column_dimensions['E'].width = 20
                ws.column_dimensions['F'].width = 20

                for i, data in enumerate(final_results, start=1):
                    row_idx = i + 1
                    ws.row_dimensions[row_idx].height = 180 # 행 높이 보정
                    
                    ws.cell(row=row_idx, column=1, value=i).alignment = Alignment(horizontal='center', vertical='center')
                    ws.cell(row=row_idx, column=2, value=data["제품명"]).alignment = Alignment(wrap_text=True, vertical='center')
                    ws.cell(row=row_idx, column=5, value=data["정가"]).alignment = Alignment(horizontal='center', vertical='center')
                    
                    # 세일가 강조 및 빨간색 처리
                    s_cell = ws.cell(row=row_idx, column=6, value=data["세일가"])
                    s_cell.alignment = Alignment(horizontal='center', vertical='center')
                    if data["세일가"] != "-":
                        s_cell.font = Font(color="FF0000", bold=True)
                    
                    ws.cell(row=row_idx, column=7, value="상세보기").hyperlink = data["링크"]

                    for j, img_url in enumerate(data["이미지들"]):
                        try:
                            res = requests.get(img_url, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
                            img_pil = PILImage.open(BytesIO(res.content)).convert("RGB")
                            img_thumb = img_pil.copy()
                            img_thumb.thumbnail((220, 220))
                            t_path = os.path.join(IMG_FOLDER, f"t_{row_idx}_{j}.png")
                            img_thumb.save(t_path)
                            ws.add_image(XLImage(t_path), f"{'C' if j==0 else 'D'}{row_idx}")
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