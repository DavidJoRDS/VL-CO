import streamlit as st
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
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
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime

# ─────────────────────────────────────────
# 페이지 설정
# ─────────────────────────────────────────
st.set_page_config(page_title="VL&CO 상품크롤러", layout="wide")
st.title("🛒 VL&CO 상품크롤러")
st.caption("국내외 모든 쇼핑몰 범용 | 세일가 정밀 판별 | 빠른 병렬 수집")

# 세션 상태 초기화
for key in ['excel_data', 'zip_data', 'result_count']:
    if key not in st.session_state:
        st.session_state[key] = None

target_url = st.text_input("크롤링할 사이트 주소를 입력하세요", value="", placeholder="https://example.com/products")

# ─────────────────────────────────────────
# 유틸: 숫자 추출 패턴 (3자리 이상 숫자, 쉼표 포함)
# ─────────────────────────────────────────
NUM_PATTERN = re.compile(r'[\$¥€£₩]?\s*[\d,]+(?:\.\d+)?')

def extract_numbers(text: str) -> list[float]:
    """텍스트에서 가격으로 보이는 숫자 추출 (float 리스트 반환)"""
    results = []
    for m in NUM_PATTERN.findall(text):
        cleaned = re.sub(r'[^\d.]', '', m)
        try:
            val = float(cleaned)
            if val >= 100:   # 100 미만은 가격으로 보지 않음
                results.append(val)
        except ValueError:
            continue
    return results

def fmt_price(val: float, raw: str = "") -> str:
    """숫자 → 표시용 문자열 (원화 쉼표 포함)"""
    if val == int(val):
        return f"{int(val):,}"
    return f"{val:,.2f}"

# ─────────────────────────────────────────
# 핵심: 세일가 정밀 판별
# ─────────────────────────────────────────
def get_refined_prices(driver, item_element):
    """
    우선순위:
    1) strike / del / s 태그  → 해당 텍스트 = 정가
    2) CSS computed text-decoration: line-through  → 정가
    3) 클래스명 키워드 (origin, original, regular, old, before, crossed, retail) → 정가
    4) 가격이 2개 이상 → 더 큰 값 = 정가, 더 작은 값 = 세일가
    5) 가격이 1개 → 정가로 처리, 세일가 없음
    """
    try:
        num_pattern = re.compile(r'[\d,]+(?:\.\d+)?')
        
        # ── 1단계: 취소선 태그 탐색 ──────────────────────
        strikethrough_selectors = [
            "strike", "del", "s",
            "span[style*='line-through']",
            "span[style*='text-decoration: line-through']",
            "p[style*='line-through']",
        ]
        for sel in strikethrough_selectors:
            tags = item_element.find_elements(By.CSS_SELECTOR, sel)
            if tags:
                reg_text = tags[0].text.strip()
                reg_nums = extract_numbers(reg_text)
                if reg_nums:
                    reg_val = reg_nums[0]
                    # 나머지 텍스트에서 세일가 탐색
                    full_text = item_element.text
                    # 정가 텍스트 제거 후 가격 탐색
                    rest_text = full_text.replace(reg_text, "", 1)
                    sale_nums = extract_numbers(rest_text)
                    # 정가보다 작은 숫자 중 가장 큰 것
                    candidates = [v for v in sale_nums if 0 < v < reg_val]
                    if candidates:
                        sale_val = max(candidates)
                        return fmt_price(reg_val), fmt_price(sale_val)
                    else:
                        return fmt_price(reg_val), "-"
        
        # ── 2단계: JS computed style로 line-through 탐색 ──
        spans = item_element.find_elements(By.CSS_SELECTOR, "span, p, div")
        for span in spans[:20]:  # 너무 많으면 느리므로 최대 20개만
            try:
                td = driver.execute_script(
                    "return window.getComputedStyle(arguments[0]).textDecoration;", span
                )
                if td and "line-through" in td:
                    reg_text = span.text.strip()
                    reg_nums = extract_numbers(reg_text)
                    if reg_nums:
                        reg_val = reg_nums[0]
                        rest_text = item_element.text.replace(reg_text, "", 1)
                        sale_nums = extract_numbers(rest_text)
                        candidates = [v for v in sale_nums if 0 < v < reg_val]
                        if candidates:
                            return fmt_price(reg_val), fmt_price(max(candidates))
                        return fmt_price(reg_val), "-"
            except Exception:
                continue
        
        # ── 3단계: 클래스명 키워드로 정가 추정 ───────────
        origin_keywords = [
            "origin", "original", "regular", "old", "before",
            "crossed", "retail", "list", "msrp", "was", "before"
        ]
        sale_keywords = [
            "sale", "discount", "special", "now", "current",
            "final", "offer", "price-sale", "selling"
        ]
        
        reg_val_kw = None
        sale_val_kw = None
        
        all_price_tags = item_element.find_elements(By.CSS_SELECTOR, "span, p, div, em, strong")
        for tag in all_price_tags[:30]:
            try:
                cls = (tag.get_attribute("class") or "").lower()
                tag_text = tag.text.strip()
                nums = extract_numbers(tag_text)
                if not nums:
                    continue
                val = nums[0]
                if any(kw in cls for kw in origin_keywords):
                    reg_val_kw = val
                elif any(kw in cls for kw in sale_keywords):
                    sale_val_kw = val
            except Exception:
                continue
        
        if reg_val_kw and sale_val_kw:
            return fmt_price(reg_val_kw), fmt_price(sale_val_kw)
        if reg_val_kw:
            return fmt_price(reg_val_kw), "-"
        
        # ── 4단계: 전체 텍스트에서 가격 후보 추출 ────────
        full_text = item_element.text.strip()
        all_vals = extract_numbers(full_text)
        # 중복 제거하되 순서 유지
        seen = set()
        unique_vals = []
        for v in all_vals:
            if v not in seen:
                seen.add(v)
                unique_vals.append(v)
        
        if len(unique_vals) >= 2:
            # 더 큰 값 = 정가, 더 작은 값 = 세일가
            sorted_vals = sorted(unique_vals, reverse=True)
            return fmt_price(sorted_vals[0]), fmt_price(sorted_vals[1])
        elif len(unique_vals) == 1:
            return fmt_price(unique_vals[0]), "-"
    
    except Exception:
        pass
    
    return "정보없음", "-"


# ─────────────────────────────────────────
# 스크롤: 페이지 끝까지 빠르게 내리기
# ─────────────────────────────────────────
def scroll_to_bottom(driver, status_writer, pause: float = 0.7, max_rounds: int = 30):
    """
    페이지 전체 높이를 감지하며 끝까지 스크롤.
    lazy-load 대응: 스크롤 후 새 콘텐츠가 없으면 종료.
    """
    status_writer("📜 페이지 끝까지 스크롤 중 (lazy-load 대응)...")
    last_height = driver.execute_script("return document.body.scrollHeight")
    
    for i in range(max_rounds):
        # 한 번에 화면 2개 높이씩 빠르게 내림
        driver.execute_script("window.scrollBy(0, window.innerHeight * 2);")
        time.sleep(pause)
        
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            # 높이 변화 없으면 한 번 더 확인 후 종료
            time.sleep(pause)
            new_height2 = driver.execute_script("return document.body.scrollHeight")
            if new_height2 == last_height:
                status_writer(f"  ✅ 스크롤 완료 ({i+1}회 반복, 총 높이 {new_height}px)")
                break
            last_height = new_height2
        else:
            last_height = new_height
    
    # 맨 위로 올렸다가 다시 끝으로 → 일부 사이트 끝부분 누락 방지
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(0.3)
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(pause)


# ─────────────────────────────────────────
# 이미지 다운로드 (병렬)
# ─────────────────────────────────────────
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8',
}

def download_single_image(args):
    """단일 이미지 다운로드 (ThreadPoolExecutor 용)"""
    img_url, save_path, thumb_path = args
    try:
        res = requests.get(img_url, timeout=7, headers=HEADERS)
        if res.status_code != 200:
            return False
        img_pil = PILImage.open(BytesIO(res.content)).convert("RGB")
        # 원본 저장
        img_pil.save(save_path, "JPEG", quality=85)
        # 썸네일 저장
        img_thumb = img_pil.copy()
        img_thumb.thumbnail((220, 220))
        img_thumb.save(thumb_path, "PNG")
        return True
    except Exception:
        return False


# ─────────────────────────────────────────
# 메인 크롤링 실행
# ─────────────────────────────────────────
if st.button("🚀 데이터 수집 시작", type="primary"):

    if not target_url.strip():
        st.error("URL을 입력해주세요.")
        st.stop()

    IMG_FOLDER = "collected_images"
    if os.path.exists(IMG_FOLDER):
        shutil.rmtree(IMG_FOLDER)
    os.makedirs(IMG_FOLDER)

    # ── Chrome 옵션 ───────────────────────────────────
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    # ── 진행상황 UI ───────────────────────────────────
    with st.status("🔄 수집 작업을 시작합니다...", expanded=True) as status_ui:

        def log(msg: str):
            """진행상황 실시간 출력"""
            st.write(msg)

        try:
            # ── STEP 1: 드라이버 실행 & 페이지 로드 ─────
            log("🌐 [1단계] 브라우저 실행 및 페이지 접속 중...")
            driver = webdriver.Chrome(options=options)
            driver.set_page_load_timeout(30)
            driver.get(target_url)

            log("⏳ [1단계] 초기 페이지 렌더링 대기 중 (3초)...")
            time.sleep(3)
            log(f"  ✅ 페이지 접속 완료: {driver.title or target_url}")

            # ── STEP 2: 스크롤 ────────────────────────
            log("📜 [2단계] 전체 상품 로드를 위해 페이지 스크롤 시작...")
            scroll_to_bottom(driver, log, pause=0.6, max_rounds=40)

            # ── STEP 3: 상품 요소 탐색 ────────────────
            log("🔍 [3단계] 페이지에서 상품 요소 탐색 중...")

            # 범용 셀렉터 - 우선순위대로 여러 패턴 시도
            SELECTORS = [
                # 상품 카드 계열
                "[class*='product-card']",
                "[class*='ProductCard']",
                "[class*='product_card']",
                "[class*='item-card']",
                "[class*='ItemCard']",
                # 그리드/리스트 아이템
                "[class*='product-item']",
                "[class*='productItem']",
                "[class*='product_item']",
                "[class*='goods-item']",
                "[class*='goodsItem']",
                "[class*='item-wrap']",
                # 일반 article/li
                "article[class*='product']",
                "article[class*='item']",
                "li[class*='product']",
                "li[class*='item']",
                "li[class*='goods']",
                # 링크 기반
                "a[class*='product']",
                "a[class*='item-link']",
                # 폴백
                "li",
            ]

            items = []
            used_selector = ""
            for sel in SELECTORS:
                candidates = driver.find_elements(By.CSS_SELECTOR, sel)
                # 링크와 이미지가 모두 있는 요소만 유효한 상품으로 판단
                valid = []
                for c in candidates:
                    try:
                        if c.size['width'] < 80 or c.size['height'] < 80:
                            continue
                        has_link = bool(c.find_elements(By.TAG_NAME, 'a'))
                        has_img  = bool(c.find_elements(By.TAG_NAME, 'img'))
                        if has_link and has_img:
                            valid.append(c)
                    except Exception:
                        continue
                if len(valid) >= 3:
                    items = valid
                    used_selector = sel
                    break

            log(f"  ✅ 상품 후보 {len(items)}개 발견 (셀렉터: {used_selector})")

            if not items:
                log("⚠️ 상품 요소를 찾지 못했습니다. 수집을 종료합니다.")
                st.warning("상품 정보를 찾을 수 없습니다. URL 또는 페이지 구조를 확인해주세요.")
                driver.quit()
                st.stop()

            # ── STEP 4: 각 상품 정보 추출 ────────────
            log(f"📦 [4단계] {len(items)}개 상품에서 가격/이미지 정보 추출 중...")

            final_results = []
            seen_links = set()
            skip_count = 0

            for idx, item in enumerate(items, start=1):
                try:
                    # 링크 추출
                    try:
                        link_tag = item if item.tag_name == 'a' else item.find_element(By.TAG_NAME, 'a')
                        link = link_tag.get_attribute('href') or ""
                    except Exception:
                        skip_count += 1
                        continue

                    if not link or link in seen_links or "javascript" in link.lower():
                        skip_count += 1
                        continue

                    # 너무 짧은 텍스트는 상품 아님
                    item_text = item.text.strip()
                    if len(item_text) < 5:
                        skip_count += 1
                        continue

                    # 상품명: 첫 줄 (너무 짧으면 두 번째 줄도 시도)
                    lines = [l.strip() for l in item_text.split('\n') if l.strip()]
                    p_name = lines[0] if lines else "이름없음"
                    if len(p_name) < 3 and len(lines) > 1:
                        p_name = lines[1]

                    # 가격 추출
                    reg_p, sale_p = get_refined_prices(driver, item)

                    # 이미지 URL 수집 (최대 2개)
                    img_urls = []
                    imgs = item.find_elements(By.TAG_NAME, "img")
                    for img in imgs:
                        src = (
                            img.get_attribute('data-src')
                            or img.get_attribute('data-lazy-src')
                            or img.get_attribute('data-original')
                            or img.get_attribute('data-srcset', )
                            or img.get_attribute('src')
                            or ""
                        )
                        # srcset에서 첫 번째 URL만 추출
                        if src and ' ' in src:
                            src = src.split()[0]
                        if not src:
                            continue
                        # 노이즈 이미지 필터링
                        src_lower = src.lower()
                        if any(x in src_lower for x in [
                            'swatch', 'color', 'icon', 'logo', 'banner',
                            'pixel', 'spacer', 'blank', '1x1', 'placeholder'
                        ]):
                            continue
                        # 상대경로 → 절대경로
                        abs_src = urljoin(target_url, src)
                        img_urls.append(abs_src)
                        if len(img_urls) >= 2:
                            break

                    if not img_urls:
                        skip_count += 1
                        continue

                    final_results.append({
                        "제품명": p_name[:80],
                        "정가": reg_p,
                        "세일가": sale_p,
                        "링크": link,
                        "이미지들": img_urls,
                    })
                    seen_links.add(link)

                    if idx % 20 == 0:
                        log(f"  📦 {idx}/{len(items)} 처리 중... (수집됨: {len(final_results)}개)")

                except Exception:
                    skip_count += 1
                    continue

            log(f"  ✅ 상품 정보 추출 완료 → 유효 {len(final_results)}개 / 스킵 {skip_count}개")

            if not final_results:
                log("⚠️ 유효한 상품 정보가 없습니다.")
                st.warning("상품 정보를 추출하지 못했습니다.")
                driver.quit()
                st.stop()

            driver.quit()
            log("🌐 브라우저 종료")

            # ── STEP 5: 이미지 병렬 다운로드 ─────────
            log(f"🖼️ [5단계] 이미지 병렬 다운로드 시작 ({len(final_results)}개 상품)...")

            # 다운로드 작업 목록 구성
            download_tasks = []
            for i, data in enumerate(final_results, start=1):
                for j, img_url in enumerate(data["이미지들"]):
                    save_path  = os.path.join(IMG_FOLDER, f"{i}_{j+1}.jpg")
                    thumb_path = os.path.join(IMG_FOLDER, f"t_{i+1}_{j}.png")
                    download_tasks.append((img_url, save_path, thumb_path, i, j))

            # 병렬 다운로드 (최대 12 스레드)
            img_results = {}  # (i, j) → thumb_path or None
            completed_dl = 0

            with ThreadPoolExecutor(max_workers=12) as executor:
                future_map = {
                    executor.submit(
                        download_single_image,
                        (url, sp, tp)
                    ): (idx, jdx, tp)
                    for url, sp, tp, idx, jdx in download_tasks
                }
                for future in as_completed(future_map):
                    idx, jdx, tp = future_map[future]
                    success = future.result()
                    img_results[(idx, jdx)] = tp if success else None
                    completed_dl += 1
                    if completed_dl % 20 == 0:
                        log(f"  🖼️ 이미지 다운로드 {completed_dl}/{len(download_tasks)} 완료...")

            log(f"  ✅ 이미지 다운로드 완료 ({completed_dl}개 처리)")

            # ── STEP 6: 엑셀 생성 ────────────────────
            log("📊 [6단계] 엑셀 파일 생성 중...")

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "상품목록"

            # 헤더
            headers = ["번호", "제품명", "이미지1", "이미지2", "정가", "세일가", "상세링크"]
            ws.append(headers)

            # 헤더 스타일
            for col_idx, _ in enumerate(headers, start=1):
                cell = ws.cell(row=1, column=col_idx)
                cell.font = Font(bold=True, color="FFFFFF")
                cell.alignment = Alignment(horizontal='center', vertical='center')
                from openpyxl.styles import PatternFill
                cell.fill = PatternFill(start_color="2F4F8F", end_color="2F4F8F", fill_type="solid")

            # 열 너비
            ws.column_dimensions['A'].width = 6
            ws.column_dimensions['B'].width = 40
            ws.column_dimensions['C'].width = 28
            ws.column_dimensions['D'].width = 28
            ws.column_dimensions['E'].width = 18
            ws.column_dimensions['F'].width = 22
            ws.column_dimensions['G'].width = 14

            for i, data in enumerate(final_results, start=1):
                row_idx = i + 1
                ws.row_dimensions[row_idx].height = 165

                ws.cell(row=row_idx, column=1, value=i).alignment = Alignment(horizontal='center', vertical='center')
                ws.cell(row=row_idx, column=2, value=data["제품명"]).alignment = Alignment(wrap_text=True, vertical='center')
                ws.cell(row=row_idx, column=5, value=data["정가"]).alignment = Alignment(horizontal='center', vertical='center')

                # 세일가 강조
                s_cell = ws.cell(row=row_idx, column=6)
                s_cell.alignment = Alignment(horizontal='center', vertical='center')
                if data["세일가"] != "-":
                    s_cell.value = f"▼ {data['세일가']}"
                    s_cell.font = Font(color="CC0000", bold=True, size=11)
                else:
                    s_cell.value = "-"

                # 링크 하이퍼링크
                link_cell = ws.cell(row=row_idx, column=7, value="🔗 상세보기")
                link_cell.hyperlink = data["링크"]
                link_cell.font = Font(color="0563C1", underline="single")
                link_cell.alignment = Alignment(horizontal='center', vertical='center')

                # 이미지 삽입 (다운로드 성공한 경우만)
                for j in range(2):
                    thumb_path = img_results.get((i, j))
                    if thumb_path and os.path.exists(thumb_path):
                        col_letter = 'C' if j == 0 else 'D'
                        try:
                            ws.add_image(XLImage(thumb_path), f"{col_letter}{row_idx}")
                        except Exception:
                            pass

            excel_io = BytesIO()
            wb.save(excel_io)
            st.session_state.excel_data = excel_io.getvalue()
            log("  ✅ 엑셀 파일 생성 완료")

            # ── STEP 7: ZIP 생성 ──────────────────────
            log("🗜️ [7단계] 이미지 ZIP 압축 중...")

            zip_io = BytesIO()
            with zipfile.ZipFile(zip_io, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for root, _, files in os.walk(IMG_FOLDER):
                    for f in files:
                        if not f.startswith("t_"):
                            zf.write(os.path.join(root, f), f)
            st.session_state.zip_data = zip_io.getvalue()
            st.session_state.result_count = len(final_results)

            shutil.rmtree(IMG_FOLDER)
            log("  ✅ ZIP 압축 완료")

            status_ui.update(
                label=f"✅ 수집 완료! 총 {len(final_results)}개 상품",
                state="complete",
                expanded=False
            )

        except Exception as e:
            st.error(f"❌ 오류 발생: {e}")
            status_ui.update(label="❌ 오류 발생", state="error")
        finally:
            if 'driver' in locals():
                try:
                    driver.quit()
                except Exception:
                    pass
            if os.path.exists(IMG_FOLDER):
                shutil.rmtree(IMG_FOLDER)


# ─────────────────────────────────────────
# 결과 다운로드 버튼
# ─────────────────────────────────────────
if st.session_state.excel_data:
    st.divider()
    st.success(f"✅ {st.session_state.result_count}개 상품 수집 완료!")

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    c1, c2 = st.columns(2)
    with c1:
        st.download_button(
            label="📥 엑셀 다운로드",
            data=st.session_state.excel_data,
            file_name=f"products_{ts}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
    with c2:
        st.download_button(
            label="🖼️ 이미지(ZIP) 다운로드",
            data=st.session_state.zip_data,
            file_name=f"images_{ts}.zip",
            mime="application/zip",
            use_container_width=True,
        )