import streamlit as st
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
import time
import requests
import os
from io import BytesIO
from PIL import Image as PILImage
import openpyxl
from openpyxl.drawing.image import Image as XLImage
from urllib.parse import urljoin
from openpyxl.styles import Alignment, Font

# 페이지 설정
st.set_page_config(page_title="범용 쇼핑몰 크롤러", layout="wide")
st.title("🛒 범용 쇼핑몰 상품 크롤러")
st.write("도메인 주소를 입력하면 상품명, 가격, 이미지를 추출하여 엑셀로 만들어줍니다.")

# 사이트 주소 입력창
target_url = st.text_input("크롤링할 사이트 주소를 입력하세요", value="https://www.thenorthfacekorea.co.kr/category/n/whitelabel/womens?page=3")

if st.button("데이터 수집 시작"):
    with st.spinner('데이터를 수집 중입니다... 잠시만 기다려 주세요.'):
        # 이미지 저장 폴더 생성
        if not os.path.exists("img"):
            os.makedirs("img")

        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        # 사용자 에이전트 설정 (차단 방지)
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

        try:
            # 경로를 직접 지정하지 않고 시스템 설치본을 사용하도록 변경
            driver = webdriver.Chrome(options=options)
        except Exception as e:
            st.error(f"드라이버 초기화 실패: {str(e)}")
            st.stop()

            # 스크롤 로직
            last_height = driver.execute_script("return document.body.scrollHeight")
            while True:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1.5)
                new_height = driver.execute_script("return document.body.scrollHeight")
                if new_height == last_height:
                    driver.execute_script("window.scrollBy(0, -800);")
                    time.sleep(1)
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(3)
                    break
                last_height = new_height

            # 상품 탐색
            candidates = driver.find_elements(By.CSS_SELECTOR, "li, div[class*='item'], div[class*='product']")
            final_results = []
            seen_links = set()

            for item in candidates:
                try:
                    link_tag = item.find_element(By.TAG_NAME, 'a')
                    link = link_tag.get_attribute('href')
                    if not link or link in seen_links or 'javascript' in link: continue
                    if len(link.split('/')) < 4: continue

                    img_tags = item.find_elements(By.TAG_NAME, 'img')
                    if not img_tags: continue

                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", item)
                    full_text = driver.execute_script("return arguments[0].innerText;", item)
                    if not full_text or not any(x in full_text for x in ['원', 'KRW', '₩', ',']): continue
                    
                    lines = [l.strip() for l in full_text.split('\n') if l.strip()]
                    if len(lines) < 1 or len(lines[0]) < 2: continue
                    
                    product_name = lines[0]
                    price_info = [l for l in lines if any(x in l for x in ['원', 'KRW', '₩', ','])]
                    reg_price = price_info[0] if len(price_info) >= 1 else "가격없음"
                    sale_price = price_info[1] if len(price_info) >= 2 else "-"

                    img_urls = []
                    for img in img_tags:
                        src = img.get_attribute('data-original') or img.get_attribute('data-src') or img.get_attribute('src')
                        if src:
                            src = urljoin(target_url, src)
                            if 'http' in src and not any(x in src.lower() for x in ['icon', 'logo', 'btn']):
                                if src not in img_urls: img_urls.append(src)
                        if len(img_urls) >= 2: break

                    if len(img_urls) > 0:
                        final_results.append({"제품명": product_name, "정가": reg_price, "세일가": sale_price, "링크": link, "이미지들": img_urls})
                        seen_links.add(link)
                except: continue

            # 엑셀 생성
            if final_results:
                wb = openpyxl.Workbook()
                ws = wb.active
                ws.append(["번호", "제품명", "이미지1", "이미지2", "정가", "세일가", "상세링크"])
                
                ws.column_dimensions['B'].width = 45
                ws.column_dimensions['C'].width = 30
                ws.column_dimensions['D'].width = 30

                for i, data in enumerate(final_results, start=1):
                    row_idx = i + 1
                    ws.row_dimensions[row_idx].height = 150
                    ws.cell(row=row_idx, column=1, value=i).alignment = Alignment(horizontal='center', vertical='center')
                    ws.cell(row=row_idx, column=2, value=data["제품명"]).alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
                    ws.cell(row=row_idx, column=5, value=data["정가"]).alignment = Alignment(horizontal='center', vertical='center')
                    ws.cell(row=row_idx, column=6, value=data["세일가"]).alignment = Alignment(horizontal='center', vertical='center')
                    if data["세일가"] != "-": ws.cell(row=row_idx, column=6).font = Font(color="FF0000", bold=True)
                    
                    link_cell = ws.cell(row=row_idx, column=7, value="링크")
                    link_cell.hyperlink = data["링크"]

                    for j, img_url in enumerate(data["이미지들"]):
                        try:
                            res = requests.get(img_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
                            img_pil = PILImage.open(BytesIO(res.content)).convert("RGB")
                            img_pil.thumbnail((200, 200))
                            path = f"temp_{row_idx}_{j}.png"
                            img_pil.save(path)
                            ws.add_image(XLImage(path), f"{'C' if j==0 else 'D'}{row_idx}")
                        except: continue

                excel_data = BytesIO()
                wb.save(excel_data)
                excel_data.seek(0)

                st.success(f"총 {len(final_results)}개의 상품을 찾았습니다!")
                st.download_button(label="📥 엑셀 파일 다운로드", data=excel_data, file_name=f"crawling_result.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            else:
                st.error("상품을 찾지 못했습니다. 주소를 확인해 주세요.")

        finally:
            driver.quit()