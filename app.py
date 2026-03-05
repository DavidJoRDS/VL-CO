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
from urllib.parse import urljoin
from openpyxl.styles import Alignment, Font
import datetime

# 페이지 설정
st.set_page_config(page_title="VL&CO 상품크롤러", layout="wide")
st.title("🛒 VL&CO 상품크롤러 (성능 개선 버전)")

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
    
    with st.spinner('사이트 전체를 훑으며 상품과 이미지를 수집 중입니다. 잠시만 기다려 주세요...'):
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
            time.sleep(5)

            # [개선 1] 더 깊고 정밀한 스크롤 (Lazy Loading 대응)
            # 단순히 끝까지 내리는 게 아니라 조금씩 내려서 이미지가 로드될 시간을 줌
            last_height = driver.execute_script("return document.body.scrollHeight")
            for i in range(10): # 최대 10번 스크롤
                driver.execute_script("window.scrollBy(0, 1000);") # 1000픽셀씩 이동
                time.sleep(1.5)
                new_height = driver.execute_script("return document.body.scrollHeight")
                if i > 5 and new_height == last_height: break
                last_height = new_height

            # [개선 2] 상품 탐색 범위 확장
            items = driver.find_elements(By.CSS_SELECTOR, "li, div[class*='prod'], div[class*='item'], div[class*='Unit'], a[class*='product'], .mcl-product-grid-item")
            
            final_results = []
            seen_links = set()

            for item in items:
                try:
                    if item.size['width'] < 50 or item.size['height'] < 50: continue
                    
                    full_text = item.text.strip()
                    if not full_text: continue
                    
                    # 링크 추출
                    link = ""
                    try:
                        if item.tag_name == 'a':
                            link = item.get_attribute('href')
                        else:
                            link = item.find_element(By.TAG_NAME, 'a').get_attribute('href')
                    except: continue
                    
                    if not link or link in seen_links or "javascript" in link: continue

                    # [개선 3] 이미지 추출 로직 강화 (다양한 속성 체크)
                    img_urls = []
                    images = item.find_elements(By.TAG_NAME, 'img')
                    for img in images:
                        # 사이트마다 다른 이미지 저장 속성을 모두 뒤짐
                        src = (img.get_attribute('data-src') or 
                               img.get_attribute('data-original') or 
                               img.get_attribute('src') or 
                               img.get_attribute('srcset') or
                               img.get_attribute('data-lazy-src'))
                        
                        if src:
                            if ' ' in src: src = src.split(' ')[0] # srcset 대응
                            src = urljoin(target_url, src)
                            if 'http' in src and not any(x in src.lower() for x in ['logo', 'icon', 'btn', 'svg', 'gif']):
                                if src not in img_urls: img_urls.append(src)
                        if len(img_urls) >= 2: break
                    
                    if not img_urls: continue # 이미지가 없으면 상품이 아니라고 판단

                    lines = [l.strip() for l in full_text.split('\n') if l.strip()]
                    p_name = lines[0] if lines else "상품명 없음"
                    
                    # 가격 추출
                    prices = [l for l in lines if any(x in l for x in ['원', '₩', 'KRW', ',']) and any(c.isdigit() for c in l)]
                    p_price = prices[0] if prices else "정보없음"
                    s_price = prices[1] if len(prices) > 1 else "-"

                    final_results.append({
                        "제품명": p_name[:100],
                        "정가": p_price,
                        "세일가": s_price,
                        "링크": link,
                        "이미지들": img_urls
                    })
                    seen_links.add(link)
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
                            # [개선 4] 이미지 다운로드 헤더 추가 (차단 방지)
                            res = requests.get(img_url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
                            img_pil = PILImage.open(BytesIO(res.content)).convert("RGB")
                            
                            img_thumb = img_pil.copy()
                            img_thumb.thumbnail((180, 180))
                            t_path = os.path.join(IMG_FOLDER, f"t_{row_idx}_{j}.png")
                            img_thumb.save(t_path)
                            ws.add_image(XLImage(t_path), f"{'C' if j==0 else 'D'}{row_idx}")
                            
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
                st.warning("상품 정보를 찾을 수 없습니다.")

        except Exception as e:
            st.error(f"오류: {str(e)}")
        finally:
            if 'driver' in locals(): driver.quit()

if st.session_state.excel_data:
    st.success(f"✅ 총 {st.session_state.result_count}개의 상품 데이터를 수집했습니다!")
    c1, c2 = st.columns(2)
    with c1:
        st.download_button("📥 엑셀 다운로드", st.session_state.excel_data, "result.xlsx")
    with c2:
        st.download_button("🖼️ 이미지(ZIP) 다운로드", st.session_state.zip_data, "images.zip")