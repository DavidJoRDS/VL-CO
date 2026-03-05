import streamlit as st
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
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
from urllib.parse import urljoin, urlparse
from openpyxl.styles import Alignment, Font
import datetime

# 페이지 설정
st.set_page_config(page_title="VL&CO 상품크롤러", layout="wide")
st.title("🛒 VL&CO 상품크롤러")

# 세션 상태 초기화
if 'excel_data' not in st.session_state:
    st.session_state.excel_data = None
if 'zip_data' not in st.session_state:
    st.session_state.zip_data = None
if 'result_count' not in st.session_state:
    st.session_state.result_count = 0

target_url = st.text_input("크롤링할 사이트 주소를 입력하세요", value="https://www.thenorthfacekorea.co.kr/category/n/whitelabel/womens?page=3")

def clean_filename(filename):
    return re.sub(r'[\\/*?:"<>|]', "", filename)

def get_site_config(url):
    """사이트별 맞춤 설정 반환 (조건문 활용)"""
    domain = urlparse(url).netloc.lower()
    
    # 1. 몽클레르 전용 설정
    if "moncler" in domain:
        return {
            "item_selector": ".mcl-product-grid-item, [data-testid*='product'], .product-item",
            "wait_time": 10,  # 보안이 강하므로 로딩 시간을 길게 설정
            "price_keyword": "₩", # 원화 표시 기호
            "is_global": True
        }
    # 2. 아더에러 전용 설정
    elif "adererror" in domain:
        return {
            "item_selector": ".product-item, .item-box",
            "wait_time": 5,
            "price_keyword": "KRW",
            "is_global": False
        }
    # 3. 기본 설정 (노스페이스, 코닥 등)
    else:
        return {
            "item_selector": ".item-box, .product-item, li[class*='item'], div[class*='product']",
            "wait_time": 5,
            "price_keyword": "원",
            "is_global": False
        }

# 데이터 수집 시작 버튼
if st.button("🚀 데이터 수집 시작"):
    st.session_state.excel_data = None
    st.session_state.zip_data = None
    
    config = get_site_config(target_url)
    
    with st.spinner(f'알고리즘이 해당 사이트 구조를 분석하여 수집 중입니다... (예상 대기: {config["wait_time"]}초 이상)'):
        IMG_FOLDER = "collected_images"
        if os.path.exists(IMG_FOLDER):
            shutil.rmtree(IMG_FOLDER)
        os.makedirs(IMG_FOLDER)

        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        # 봇 감지 회피를 위한 User-Agent 설정
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

        try:
            driver = webdriver.Chrome(options=options)
            driver.get(target_url)
            
            # 사이트별 최적화된 대기 시간 적용
            time.sleep(config["wait_time"])

            # 몽클레르 등 글로벌 사이트의 쿠키/국가 팝업 제거 시도
            try:
                close_btn = driver.find_elements(By.CSS_SELECTOR, "button[class*='close'], .mcl-modal__close")
                if close_btn: close_btn[0].click()
            except: pass

            # 스크롤 로직
            last_height = driver.execute_script("return document.body.scrollHeight")
            for _ in range(5): # 명품 사이트는 무한 스크롤이 많으므로 횟수 제한
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)
                new_height = driver.execute_script("return document.body.scrollHeight")
                if new_height == last_height: break
                last_height = new_height

            # 사이트 맞춤 선택자로 상품 탐색
            items = driver.find_elements(By.CSS_SELECTOR, config["item_selector"])
            final_results = []
            seen_links = set()

            for item in items:
                try:
                    link_tag = item.find_element(By.TAG_NAME, 'a')
                    link = link_tag.get_attribute('href')
                    if not link or link in seen_links: continue

                    full_text = driver.execute_script("return arguments[0].innerText;", item)
                    # 가격 키워드 조건 체크 (원, ₩, KRW 등)
                    if not full_text or config["price_keyword"] not in full_text: continue
                    
                    lines = [l.strip() for l in full_text.split('\n') if l.strip()]
                    product_name = lines[0]
                    
                    # 가격 추출 로직 보완
                    price_list = [l for l in lines if config["price_keyword"] in l or (',' in l and any(c.isdigit() for c in l))]
                    reg_price = price_list[0] if len(price_list) >= 1 else "정보없음"
                    sale_price = price_list[1] if len(price_list) >= 2 else "-"

                    img_urls = []
                    img_tags = item.find_elements(By.TAG_NAME, 'img')
                    for img in img_tags:
                        # 다양한 이미지 속성 대응
                        src = img.get_attribute('data-original') or img.get_attribute('data-src') or img.get_attribute('src') or img.get_attribute('srcset')
                        if src:
                            if ' ' in src: src = src.split(' ')[0] # srcset 대응
                            src = urljoin(target_url, src)
                            if 'http' in src and not any(x in src.lower() for x in ['icon', 'logo', 'btn', 'svg']):
                                if src not in img_urls: img_urls.append(src)
                        if len(img_urls) >= 2: break

                    if img_urls:
                        final_results.append({"제품명": product_name, "정가": reg_price, "세일가": sale_price, "링크": link, "이미지들": img_urls})
                        seen_links.add(link)
                except: continue

            if final_results:
                wb = openpyxl.Workbook()
                ws = wb.active
                ws.append(["번호", "제품명", "이미지1", "이미지2", "정가", "세일가", "상세링크"])
                
                # 엑셀 스타일 설정
                ws.column_dimensions['B'].width = 45
                ws.column_dimensions['C'].width = 30
                ws.column_dimensions['D'].width = 30
                ws.column_dimensions['E'].width = 15
                ws.column_dimensions['F'].width = 15

                for i, data in enumerate(final_results, start=1):
                    row_idx = i + 1
                    ws.row_dimensions[row_idx].height = 150
                    ws.cell(row=row_idx, column=1, value=i).alignment = Alignment(horizontal='center', vertical='center')
                    ws.cell(row=row_idx, column=2, value=data["제품명"]).alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
                    ws.cell(row=row_idx, column=5, value=data["정가"]).alignment = Alignment(horizontal='center', vertical='center')
                    
                    s_cell = ws.cell(row=row_idx, column=6, value=data["세일가"])
                    s_cell.alignment = Alignment(horizontal='center', vertical='center')
                    if data["세일가"] != "-": s_cell.font = Font(color="FF0000", bold=True)
                    
                    l_cell = ws.cell(row=row_idx, column=7, value="상세링크")
                    l_cell.hyperlink = data["링크"]

                    safe_name = clean_filename(data["제품명"])
                    for j, img_url in enumerate(data["이미지들"]):
                        try:
                            res = requests.get(img_url, timeout=5)
                            img_pil = PILImage.open(BytesIO(res.content)).convert("RGB")
                            
                            img_thumb = img_pil.copy()
                            img_thumb.thumbnail((200, 200))
                            thumb_path = os.path.join(IMG_FOLDER, f"thumb_{row_idx}_{j}.png")
                            img_thumb.save(thumb_path)
                            ws.add_image(XLImage(thumb_path), f"{'C' if j==0 else 'D'}{row_idx}")
                            
                            final_img_name = f"{i}_{safe_name}_{j+1}.jpg"
                            img_pil.save(os.path.join(IMG_FOLDER, final_img_name), "JPEG", quality=90)
                        except: continue

                excel_io = BytesIO()
                wb.save(excel_io)
                st.session_state.excel_data = excel_io.getvalue()

                zip_io = BytesIO()
                with zipfile.ZipFile(zip_io, "w") as zf:
                    for root, dirs, files in os.walk(IMG_FOLDER):
                        for file in files:
                            if not file.startswith("thumb_"):
                                zf.write(os.path.join(root, file), file)
                st.session_state.zip_data = zip_io.getvalue()
                st.session_state.result_count = len(final_results)

                shutil.rmtree(IMG_FOLDER)
            else:
                st.warning(f"'{config['item_selector']}' 조건으로 상품을 찾지 못했습니다. 사이트 보안이 강화되었거나 구조가 다를 수 있습니다.")

        except Exception as e:
            st.error(f"❌ 오류 발생: {str(e)}")
        finally:
            if 'driver' in locals(): driver.quit()

# 수집 결과 상시 노출
if st.session_state.excel_data and st.session_state.zip_data:
    st.success(f"✅ 총 {st.session_state.result_count}개의 상품이 준비되었습니다!")
    col1, col2 = st.columns(2)
    timestamp = datetime.datetime.now().strftime('%H%M%S')
    
    with col1:
        st.download_button(
            label="📥 엑셀 파일 다운로드",
            data=st.session_state.excel_data,
            file_name=f"result_{timestamp}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    with col2:
        st.download_button(
            label="🖼️ 이미지 모음(.zip) 다운로드",
            data=st.session_state.zip_data,
            file_name=f"images_{timestamp}.zip",
            mime="application/zip"
        )