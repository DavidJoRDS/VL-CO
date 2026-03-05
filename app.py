import streamlit as st
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
import time
import requests
import os
from io import BytesIO
from PIL import Image as PILImage
import openpyxl
from openpyxl.drawing.image import Image as XLImage
from urllib.parse import urljoin
from openpyxl.styles import Alignment, Font
import datetime

# 페이지 설정
st.set_page_config(page_title="범용 쇼핑몰 크롤러", layout="wide")
st.title("🛒 범용 쇼핑몰 상품 크롤러")

target_url = st.text_input("크롤링할 사이트 주소를 입력하세요", value="https://www.thenorthfacekorea.co.kr/category/n/whitelabel/womens?page=3")

if st.button("🚀 데이터 수집 시작"):
    with st.spinner('서버 인스턴스를 초기화하고 상품 정보를 수집 중입니다...'):
        if not os.path.exists("img"):
            os.makedirs("img")

        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

        try:
            # [수정] 경로를 강제하지 않고 시스템 설치본을 자동으로 찾도록 설정
            driver = webdriver.Chrome(options=options)
            driver.get(target_url)
            time.sleep(5)

            # 스크롤 로직 (기존 유지)
            last_height = driver.execute_script("return document.body.scrollHeight")
            while True:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)
                new_height = driver.execute_script("return document.body.scrollHeight")
                if new_height == last_height:
                    break
                last_height = new_height

            # 상품 탐색 (기존 crawler.py 로직 기반)
            items = driver.find_elements(By.CSS_SELECTOR, ".item-box, .product-item, li[class*='item'], div[class*='product']")
            final_results = []
            seen_links = set()

            for item in items:
                try:
                    link_tag = item.find_element(By.TAG_NAME, 'a')
                    link = link_tag.get_attribute('href')
                    if not link or link in seen_links: continue

                    full_text = driver.execute_script("return arguments[0].innerText;", item)
                    if not full_text or '원' not in full_text: continue
                    
                    lines = [l.strip() for l in full_text.split('\n') if l.strip()]
                    product_name = lines[0]
                    price_list = [l for l in lines if '원' in l or (',' in l and any(c.isdigit() for c in l))]
                    reg_price = price_list[0] if len(price_list) >= 1 else "정보없음"
                    sale_price = price_list[1] if len(price_list) >= 2 else "-"

                    img_urls = []
                    img_tags = item.find_elements(By.TAG_NAME, 'img')
                    for img in img_tags:
                        src = img.get_attribute('data-original') or img.get_attribute('data-src') or img.get_attribute('src')
                        if src:
                            src = urljoin(target_url, src)
                            if 'http' in src and not any(x in src.lower() for x in ['icon', 'logo', 'btn']):
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
                
                # 엑셀 스타일 설정 (기존 요청사항 유지)
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
                    
                    l_cell = ws.cell(row=row_idx, column=7, value="링크")
                    l_cell.hyperlink = data["링크"]

                    for j, img_url in enumerate(data["이미지들"]):
                        try:
                            res = requests.get(img_url, timeout=5)
                            img_pil = PILImage.open(BytesIO(res.content)).convert("RGB")
                            img_pil.thumbnail((200, 200))
                            path = f"temp_{row_idx}_{j}.png"
                            img_pil.save(path)
                            ws.add_image(XLImage(path), f"{'C' if j==0 else 'D'}{row_idx}")
                        except: continue

                excel_data = BytesIO()
                wb.save(excel_data)
                excel_data.seek(0)

                st.success(f"✅ {len(final_results)}개의 상품 수집 완료!")
                st.download_button("📥 엑셀 파일 다운로드", excel_data, f"result_{datetime.datetime.now().strftime('%H%M%S')}.xlsx")
            else:
                st.warning("상품을 찾지 못했습니다. 사이트 구조가 다르거나 로딩이 덜 되었을 수 있습니다.")

        except Exception as e:
            st.error(f"❌ 오류 발생: {str(e)}")
        finally:
            if 'driver' in locals(): driver.quit()