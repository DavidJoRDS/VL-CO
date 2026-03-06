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
from openpyxl.styles import Alignment
import datetime

st.set_page_config(page_title="VL&CO 상품크롤러", layout="wide")
st.title("🛒 VL&CO 상품크롤러 (최적화 버전)")

if 'excel_data' not in st.session_state:
    st.session_state.excel_data = None
if 'zip_data' not in st.session_state:
    st.session_state.zip_data = None

target_url = st.text_input("크롤링할 사이트 주소를 입력하세요", value="")

def clean_filename(filename):
    return re.sub(r'[\\/*?:"<>|]', "", filename)

if st.button("🚀 데이터 수집 시작"):
    st.session_state.excel_data = None
    st.session_state.zip_data = None
    
    with st.spinner('사이트를 분석 중입니다...'):
        IMG_FOLDER = "collected_images"
        if os.path.exists(IMG_FOLDER):
            shutil.rmtree(IMG_FOLDER)
        os.makedirs(IMG_FOLDER)

        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

        try:
            driver = webdriver.Chrome(options=options)
            driver.set_page_load_timeout(30)
            driver.get(target_url)
            time.sleep(3)

            # [해결 2] 효율적인 페이지 로딩: 무한 스크롤 대신 페이지 전체 로드 유도
            # 2초 간격으로 큰 폭으로 3번만 스크롤하여 로딩 시간 단축
            for _ in range(3):
                driver.execute_script("window.scrollBy(0, 2500);")
                time.sleep(1.5)

            # [해결 1] 산산기어 등 상품 개수 누락 해결: 더 넓은 탐색 범위
            items = driver.find_elements(By.CSS_SELECTOR, "li, div[class*='item'], div[class*='product'], div[class*='Unit'], a[class*='product']")
            
            final_results = []
            seen_links = set()

            for item in items:
                try:
                    if item.size['width'] < 100: continue
                    
                    # 링크 확인
                    try:
                        link_tag = item if item.tag_name == 'a' else item.find_element(By.TAG_NAME, 'a')
                        link = link_tag.get_attribute('href')
                    except: continue
                    
                    if not link or link in seen_links or "javascript" in link: continue

                    # [해결 3] 몽클레르 컬러칩 오다운로드 해결: 이미지 크기 필터링 로직
                    img_urls = []
                    images = item.find_elements(By.TAG_NAME, 'img')
                    
                    # 큰 이미지를 먼저 찾기 위해 정렬하거나 속성 검사
                    for img in images:
                        # 컬러칩(swatch)이나 아이콘은 제외하는 키워드 필터링
                        src_candidate = img.get_attribute('data-src') or img.get_attribute('src') or img.get_attribute('data-original')
                        if not src_candidate: continue
                        
                        # 몽클레르 등 컬러칩 이미지는 보통 'swatch' 혹은 'color' 단어가 URL에 포함됨
                        if any(x in src_candidate.lower() for x in ['swatch', 'color_square', 'icon', 'logo']):
                            continue
                            
                        # 이미지 크기(가로세로)가 너무 작은 것은 버튼일 확률이 높음
                        if img.size['width'] > 0 and img.size['width'] < 50:
                            continue

                        src = urljoin(target_url, src_candidate)
                        if src not in img_urls:
                            img_urls.append(src)
                        if len(img_urls) >= 2: break
                    
                    if not img_urls: continue

                    full_text = item.text.strip()
                    lines = [l.strip() for l in full_text.split('\n') if l.strip()]
                    if not lines: continue
                    
                    p_name = lines[0]
                    prices = [l for l in lines if any(x in l for x in ['원', '₩', 'KRW', ',']) and any(c.isdigit() for c in l)]
                    p_price = prices[0] if prices else "정보없음"
                    s_price = prices[1] if len(prices) > 1 else "-"

                    final_results.append({
                        "제품명": p_name[:80],
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
                
                # 엑셀 설정
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
                            # 몽클레르 등 고해상도 이미지 차단 방지 헤더
                            res = requests.get(img_url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
                            img_pil = PILImage.open(BytesIO(res.content)).convert("RGB")
                            
                            # 엑셀용 썸네일
                            img_thumb = img_pil.copy()
                            img_thumb.thumbnail((180, 180))
                            t_path = os.path.join(IMG_FOLDER, f"t_{row_idx}_{j}.png")
                            img_thumb.save(t_path)
                            ws.add_image(XLImage(t_path), f"{'C' if j==0 else 'D'}{row_idx}")
                            
                            # 원본 저장
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
    st.success(f"✅ 총 {st.session_state.result_count}개의 상품 수집 완료!")
    c1, c2 = st.columns(2)
    with c1:
        st.download_button("📥 엑셀 다운로드", st.session_state.excel_data, "result.xlsx")
    with c2:
        st.download_button("🖼️ 이미지(ZIP) 다운로드", st.session_state.zip_data, "images.zip")