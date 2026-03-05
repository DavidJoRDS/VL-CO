import streamlit as st
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
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
from urllib.parse import urljoin, urlparse
from openpyxl.styles import Alignment, Font
import datetime

# 페이지 설정
st.set_page_config(page_title="VL&CO 상품크롤러", layout="wide")
st.title("🛒 VL&CO 상품크롤러")

if 'excel_data' not in st.session_state:
    st.session_state.excel_data = None
if 'zip_data' not in st.session_state:
    st.session_state.zip_data = None
if 'result_count' not in st.session_state:
    st.session_state.result_count = 0

target_url = st.text_input("크롤링할 사이트 주소를 입력하세요", value="")

def clean_filename(filename):
    return re.sub(r'[\\/*?:"<>|]', "", filename)

if st.button("🚀 데이터 수집 시작"):
    st.session_state.excel_data = None
    st.session_state.zip_data = None
    
    with st.spinner('해당 사이트의 구조를 분석하여 상품을 찾는 중입니다...'):
        IMG_FOLDER = "collected_images"
        if os.path.exists(IMG_FOLDER):
            shutil.rmtree(IMG_FOLDER)
        os.makedirs(IMG_FOLDER)

        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

        try:
            driver = webdriver.Chrome(options=options)
            driver.get(target_url)
            time.sleep(7) # 로딩 대기 시간 증가

            # 스크롤 로직 (더 꼼꼼하게 스크롤)
            for _ in range(3):
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)

            # [핵심 변경] 범용 탐색 로직: '이미지'와 '텍스트'가 같이 들어있는 모든 묶음을 탐색
            # li, div 중 상품 카드일 확률이 높은 요소들을 광범위하게 수집
            items = driver.find_elements(By.CSS_SELECTOR, "li, div[class*='prod'], div[class*='item'], div[class*='Unit'], a[class*='product']")
            
            final_results = []
            seen_links = set()

            for item in items:
                try:
                    # 너무 작거나 텍스트가 없는 요소는 패스
                    if item.size['width'] < 100 or item.size['height'] < 100: continue
                    
                    full_text = item.text.strip()
                    if not full_text or not any(c.isdigit() for c in full_text): continue
                    
                    # 링크 찾기
                    link = ""
                    try:
                        if item.tag_name == 'a':
                            link = item.get_attribute('href')
                        else:
                            link = item.find_element(By.TAG_NAME, 'a').get_attribute('href')
                    except: continue
                    
                    if not link or link in seen_links or "javascript" in link: continue

                    # 이미지 찾기
                    img_urls = []
                    images = item.find_elements(By.TAG_NAME, 'img')
                    for img in images:
                        src = img.get_attribute('data-src') or img.get_attribute('data-original') or img.get_attribute('src')
                        if src:
                            src = urljoin(target_url, src)
                            if 'http' in src and not any(x in src.lower() for x in ['logo', 'icon', 'btn', 'svg']):
                                img_urls.append(src)
                        if len(img_urls) >= 2: break
                    
                    if not img_urls: continue

                    # 이름 및 가격 추출 (줄바꿈 기준 첫 줄은 이름, '원'이나 ',' 포함된 줄은 가격)
                    lines = [l.strip() for l in full_text.split('\n') if l.strip()]
                    p_name = lines[0] if lines else "상품명 없음"
                    p_price = "정보없음"
                    s_price = "-"
                    
                    prices = [l for l in lines if any(x in l for x in ['원', '₩', 'KRW', ',']) and any(c.isdigit() for c in l)]
                    if prices:
                        p_price = prices[0]
                        if len(prices) > 1: s_price = prices[1]

                    final_results.append({
                        "제품명": p_name[:50], # 너무 길면 생략
                        "정가": p_price,
                        "세일가": s_price,
                        "링크": link,
                        "이미지들": img_urls
                    })
                    seen_links.add(link)
                    
                    if len(final_results) >= 80: break # 최대 수집량 제한 (메모리 보호)
                except: continue

            if final_results:
                wb = openpyxl.Workbook()
                ws = wb.active
                ws.append(["번호", "제품명", "이미지1", "이미지2", "정가", "세일가", "상세링크"])
                
                # 엑셀 스타일
                ws.column_dimensions['B'].width = 40
                ws.column_dimensions['C'].width = 25
                ws.column_dimensions['D'].width = 25

                for i, data in enumerate(final_results, start=1):
                    row_idx = i + 1
                    ws.row_dimensions[row_idx].height = 150
                    ws.cell(row=row_idx, column=1, value=i).alignment = Alignment(horizontal='center', vertical='center')
                    ws.cell(row=row_idx, column=2, value=data["제품명"]).alignment = Alignment(wrap_text=True, vertical='center')
                    ws.cell(row=row_idx, column=5, value=data["정가"]).alignment = Alignment(horizontal='center', vertical='center')
                    ws.cell(row=row_idx, column=6, value=data["세일가"]).alignment = Alignment(horizontal='center', vertical='center')
                    
                    l_cell = ws.cell(row=row_idx, column=7, value="상세보기")
                    l_cell.hyperlink = data["링크"]

                    safe_name = clean_filename(data["제품명"])
                    for j, img_url in enumerate(data["이미지들"]):
                        try:
                            res = requests.get(img_url, timeout=5)
                            img_pil = PILImage.open(BytesIO(res.content)).convert("RGB")
                            
                            # 썸네일 생성 및 엑셀 삽입
                            img_thumb = img_pil.copy()
                            img_thumb.thumbnail((180, 180))
                            t_path = os.path.join(IMG_FOLDER, f"t_{row_idx}_{j}.png")
                            img_thumb.save(t_path)
                            ws.add_image(XLImage(t_path), f"{'C' if j==0 else 'D'}{row_idx}")
                            
                            # ZIP 저장용 원본급 이미지
                            img_pil.save(os.path.join(IMG_FOLDER, f"{i}_{safe_name}_{j+1}.jpg"), "JPEG", quality=85)
                        except: continue

                excel_io = BytesIO()
                wb.save(excel_io)
                st.session_state.excel_data = excel_io.getvalue()

                zip_io = BytesIO()
                with zipfile.ZipFile(zip_io, "w") as zf:
                    for root, _, files in os.walk(IMG_FOLDER):
                        for file in files:
                            if not file.startswith("t_"):
                                zf.write(os.path.join(root, file), file)
                st.session_state.zip_data = zip_io.getvalue()
                st.session_state.result_count = len(final_results)
                shutil.rmtree(IMG_FOLDER)
            else:
                st.warning("이 사이트의 상품 구조를 파악하지 못했습니다. 주소를 다시 확인해 주세요.")

        except Exception as e:
            st.error(f"오류: {str(e)}")
        finally:
            if 'driver' in locals(): driver.quit()

# 결과 표시
if st.session_state.excel_data:
    st.success(f"✅ {st.session_state.result_count}개의 상품 데이터를 찾았습니다!")
    c1, c2 = st.columns(2)
    with c1:
        st.download_button("📥 엑셀 다운로드", st.session_state.excel_data, "result.xlsx")
    with c2:
        st.download_button("🖼️ 이미지(ZIP) 다운로드", st.session_state.zip_data, "images.zip")