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

st.set_page_config(page_title="VL&CO 최종 크롤러", layout="wide")
st.title("🛡️ VL&CO 완벽 가격 추적 크롤러")

if 'excel_data' not in st.session_state:
    st.session_state.excel_data = None
if 'zip_data' not in st.session_state:
    st.session_state.zip_data = None

target_url = st.text_input("크롤링할 사이트 주소를 입력하세요", value="")

def clean_filename(filename):
    return re.sub(r'[\\/*?:"<>|]', "", filename)

def debug_extract_prices(item_element, product_idx):
    """요소 내에서 가격을 강제로 찾아내는 디버깅 겸용 로직"""
    # 1. 시각적 텍스트와 숨겨진 텍스트 모두 가져오기
    inner_text = item_element.get_attribute('innerText') or ""
    text_content = item_element.get_attribute('textContent') or ""
    combined_text = inner_text + "\n" + text_content
    
    # 디버깅: 수집된 전체 텍스트 출력 (Streamlit 로그 확인용)
    print(f"--- [Item {product_idx}] Text Debug ---")
    print(combined_text[:200]) # 앞부분 200자만 로그 출력
    
    # 2. 가격 패턴 정의 (숫자 3자리 이상 + 콤마 또는 화폐 단위)
    # ₩, 원, KRW, JPY, $, ,(콤마) 등 모든 케이스 대응
    lines = [l.strip() for l in combined_text.split('\n') if l.strip()]
    price_candidates = []
    
    # 숫자 형태만 남겨서 비교하기 위한 정규식
    number_only_pattern = re.compile(r'[0-9,]{3,}')

    for line in lines:
        # 줄 안에 숫자가 있고, 가격 관련 기호가 있거나 콤마가 포함된 긴 숫자가 있다면 후보
        if number_only_pattern.search(line):
            # 한 줄에 여러 가격이 붙어있는 경우 분리 (예: 100,00080,000)
            matches = number_only_pattern.findall(line)
            if len(matches) >= 2:
                for m in matches:
                    if len(m.replace(',', '')) >= 3: # 최소 100단위 이상
                        price_candidates.append(m)
            else:
                # 기호가 포함된 줄 그대로 가져오기 (예: ₩15,000)
                if any(x in line for x in ['₩', '원', 'KRW', 'JPY', '$', ',']):
                    price_candidates.append(line)

    # 중복 제거 및 불필요한 텍스트 제거
    final_p = []
    seen = set()
    for p in price_candidates:
        clean_p = p.strip()
        if clean_p not in seen:
            final_p.append(clean_p)
            seen.add(clean_p)

    # 결과 분석
    if len(final_p) >= 2:
        # 나나미카/몽클레르 등은 보통 첫 번째가 정가, 두 번째가 세일가
        return final_p[0], final_p[1]
    elif len(final_p) == 1:
        return final_p[0], "-"
    return "가격미검출", "-"

if st.button("🚀 데이터 수집 시작"):
    with st.spinner('가격 데이터를 강제 추출 중입니다...'):
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
            time.sleep(6) # 초기 로딩 대기 시간 충분히 확보

            # 스크롤: 데이터가 끝까지 로드되도록 촘촘하게 이동
            for _ in range(3):
                driver.execute_script("window.scrollBy(0, 1500);")
                time.sleep(2)

            # 탐색 대상: 거의 모든 쇼핑몰 상품 래퍼 클래스 대응
            items = driver.find_elements(By.CSS_SELECTOR, "li, div[class*='item'], div[class*='product'], div[class*='Unit'], a[class*='product']")
            
            final_results = []
            seen_links = set()

            for idx, item in enumerate(items):
                try:
                    if item.size['width'] < 80: continue
                    
                    # 상세 링크 확보
                    try:
                        link_tag = item if item.tag_name == 'a' else item.find_element(By.TAG_NAME, 'a')
                        link = link_tag.get_attribute('href')
                    except: continue
                    
                    if not link or link in seen_links or "javascript" in link: continue

                    # [핵심] 가격 및 상품명 수집
                    reg_price, sale_price = debug_extract_prices(item, idx)
                    
                    lines = [l.strip() for l in item.text.split('\n') if l.strip()]
                    product_name = lines[0] if lines else "상품명 없음"
                    
                    # 이미지가 하나도 없으면 상품이 아닐 가능성이 높음
                    img_urls = []
                    imgs = item.find_elements(By.TAG_NAME, "img")
                    for img in imgs:
                        src = img.get_attribute('data-src') or img.get_attribute('src') or img.get_attribute('data-original')
                        if not src or any(x in src.lower() for x in ['swatch', 'color', 'icon']): continue
                        img_urls.append(urljoin(target_url, src))
                        if len(img_urls) >= 2: break
                    
                    if not img_urls: continue

                    final_results.append({
                        "제품명": product_name[:80], "정가": reg_price, "세일가": sale_price,
                        "링크": link, "이미지들": img_urls
                    })
                    seen_links.add(link)
                except: continue

            if final_results:
                wb = openpyxl.Workbook()
                ws = wb.active
                ws.append(["번호", "제품명", "이미지1", "이미지2", "정가", "세일가", "상세링크"])
                
                # 엑셀 서식 설정
                ws.column_dimensions['B'].width = 40
                ws.column_dimensions['C'].width = 25
                ws.column_dimensions['D'].width = 25
                ws.column_dimensions['E'].width = 20
                ws.column_dimensions['F'].width = 20

                for i, data in enumerate(final_results, start=1):
                    row_idx = i + 1
                    ws.row_dimensions[row_idx].height = 170
                    
                    ws.cell(row=row_idx, column=1, value=i).alignment = Alignment(horizontal='center', vertical='center')
                    ws.cell(row=row_idx, column=2, value=data["제품명"]).alignment = Alignment(wrap_text=True, vertical='center')
                    
                    # 정가/세일가 입력 및 강조
                    ws.cell(row=row_idx, column=5, value=data["정가"]).alignment = Alignment(horizontal='center', vertical='center')
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
                            img_thumb.thumbnail((200, 200))
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
                st.warning("상품 정보를 검출하지 못했습니다. 주소를 다시 확인해주세요.")
        finally:
            if 'driver' in locals(): driver.quit()

if st.session_state.excel_data:
    st.success(f"✅ {st.session_state.result_count}개 상품 분석 성공!")
    c1, c2 = st.columns(2)
    with c1: st.download_button("📥 엑셀 다운로드", st.session_state.excel_data, "result.xlsx")
    with c2: st.download_button("🖼️ 이미지(ZIP) 다운로드", st.session_state.zip_data, "images.zip")