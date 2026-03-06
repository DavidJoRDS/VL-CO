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
st.title("⚡ VL&CO 초고속 상품크롤러")

# 세션 상태 초기화
if 'excel_data' not in st.session_state:
    st.session_state.excel_data = None
if 'zip_data' not in st.session_state:
    st.session_state.zip_data = None

target_url = st.text_input("크롤링할 사이트 주소", value="")

def extract_prices(text_lines):
    price_pattern = re.compile(r'[0-9,]{3,}')
    found_prices = []
    for line in text_lines:
        if any(x in line for x in ['원', '₩', 'KRW', 'JPY', 'USD']) or price_pattern.search(line):
            matches = price_pattern.findall(line)
            for m in matches:
                if line.strip() not in found_prices:
                    found_prices.append(line.strip())
    unique_prices = list(dict.fromkeys(found_prices)) # 순서 유지하며 중복 제거
    if len(unique_prices) >= 2:
        return unique_prices[0], unique_prices[1]
    return (unique_prices[0], "-") if unique_prices else ("정보없음", "-")

if st.button("🚀 초고속 수집 시작"):
    st.session_state.excel_data = None
    st.session_state.zip_data = None
    
    with st.spinner('최적화 알고리즘으로 데이터를 빠르게 추출 중입니다...'):
        IMG_FOLDER = "collected_images"
        if os.path.exists(IMG_FOLDER): shutil.rmtree(IMG_FOLDER)
        os.makedirs(IMG_FOLDER)

        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        # [최적화 1] 이미지 로딩 안 함 (속도 향상의 핵심)
        # 이미지는 URL만 따고 나중에 별도로 requests로 받으면 훨씬 빠릅니다.
        options.add_argument("--blink-settings=imagesEnabled=false") 
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

        try:
            driver = webdriver.Chrome(options=options)
            # [최적화 2] 페이지 로드 전략 설정 (DOM 구성만 되면 바로 시작)
            driver.set_page_load_timeout(15) 
            driver.get(target_url)

            # [최적화 3] 암시적 대기(Explicit Wait) 사용
            # 무조건 5초 쉬는 대신, 상품 요소가 나타나면 즉시 다음 단계 진행
            wait = WebDriverWait(driver, 10)
            try:
                wait.until(EC.presence_of_element_located((By.TAG_NAME, "img")))
            except: pass # 못 찾아도 진행

            # [최적화 4] 최소한의 스크롤
            # 2500px씩 딱 2번만 빠르게 스크롤 (산산기어 등 대부분의 사이트 커버)
            driver.execute_script("window.scrollBy(0, 2500);")
            time.sleep(0.8)
            driver.execute_script("window.scrollBy(0, 2500);")
            time.sleep(0.8)

            items = driver.find_elements(By.CSS_SELECTOR, "li, div[class*='item'], div[class*='product'], a[class*='product']")
            
            final_results = []
            seen_links = set()

            for item in items:
                try:
                    if item.size['width'] < 80: continue
                    link_tag = item if item.tag_name == 'a' else item.find_element(By.TAG_NAME, 'a')
                    link = link_tag.get_attribute('href')
                    if not link or link in seen_links or "javascript" in link: continue

                    # [최적화 5] JS로 이미지 속성 직접 추출 (속도 향상)
                    img_urls = []
                    imgs = item.find_elements(By.TAG_NAME, "img")
                    for img in imgs:
                        # 몽클레르 등 컬러칩 필터링 로직 유지
                        src = img.get_attribute('data-src') or img.get_attribute('src') or img.get_attribute('data-original')
                        if not src or any(x in src.lower() for x in ['swatch', 'color', 'icon']): continue
                        img_urls.append(urljoin(target_url, src))
                        if len(img_urls) >= 2: break
                    
                    if not img_urls: continue

                    full_text = item.text.strip()
                    lines = [l.strip() for l in full_text.split('\n') if l.strip()]
                    reg_price, sale_price = extract_prices(lines)

                    final_results.append({
                        "제품명": lines[0][:80] if lines else "상품명 없음",
                        "정가": reg_price, "세일가": sale_price,
                        "링크": link, "이미지들": img_urls
                    })
                    seen_links.add(link)
                except: continue

            if final_results:
                wb = openpyxl.Workbook()
                ws = wb.active
                ws.append(["번호", "제품명", "이미지1", "이미지2", "정가", "세일가", "상세링크"])
                
                # 엑셀 작업 시 세션 상태에 저장하여 버튼 소멸 방지
                for i, data in enumerate(final_results, start=1):
                    row_idx = i + 1
                    ws.row_dimensions[row_idx].height = 150
                    ws.cell(row=row_idx, column=1, value=i).alignment = Alignment(horizontal='center', vertical='center')
                    ws.cell(row=row_idx, column=2, value=data["제품명"]).alignment = Alignment(wrap_text=True, vertical='center')
                    ws.cell(row=row_idx, column=5, value=data["정가"]).alignment = Alignment(horizontal='center', vertical='center')
                    s_cell = ws.cell(row=row_idx, column=6, value=data["세일가"])
                    if data["세일가"] != "-": s_cell.font = Font(color="FF0000", bold=True)
                    ws.cell(row=row_idx, column=7, value="상세보기").hyperlink = data["링크"]

                    for j, img_url in enumerate(data["이미지들"]):
                        try:
                            # 멀티프로세싱 대신 타임아웃을 짧게 잡아 속도 유지
                            res = requests.get(img_url, timeout=3, headers={'User-Agent': 'Mozilla/5.0'})
                            img_pil = PILImage.open(BytesIO(res.content)).convert("RGB")
                            img_thumb = img_pil.copy()
                            img_thumb.thumbnail((180, 180))
                            t_path = os.path.join(IMG_FOLDER, f"t_{row_idx}_{j}.png")
                            img_thumb.save(t_path)
                            ws.add_image(XLImage(t_path), f"{'C' if j==0 else 'D'}{row_idx}")
                            img_pil.save(os.path.join(IMG_FOLDER, f"{i}_{j+1}.jpg"), "JPEG", quality=80)
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
    st.success(f"⚡ {st.session_state.result_count}개 상품 수집 완료!")
    c1, c2 = st.columns(2)
    with c1: st.download_button("📥 엑셀 다운로드", st.session_state.excel_data, "result.xlsx")
    with c2: st.download_button("🖼️ 이미지(ZIP) 다운로드", st.session_state.zip_data, "images.zip")