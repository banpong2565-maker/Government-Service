"""
ตรวจสอบประกาศรับสมัครงานราชการใหม่จาก https://job.ocsc.go.th/portal
แล้วส่งแจ้งเตือนเข้า LINE (ผ่าน LINE Messaging API - broadcast)

วิธีใช้:
    1. ติดตั้ง dependencies: pip install playwright requests
       แล้วรัน: playwright install chromium
    2. ตั้งค่า environment variable LINE_CHANNEL_ACCESS_TOKEN
    3. รัน: python notify.py

ไฟล์ seen.json ใช้เก็บรายการประกาศที่เคยแจ้งเตือนไปแล้ว เพื่อไม่ให้แจ้งซ้ำ
"""

import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

import requests
from playwright.sync_api import sync_playwright

import re

PORTAL_URL = "https://job.ocsc.go.th/portal"
JOBS_LIST_URL = "https://job.ocsc.go.th/portal/job-office?type=1"
SEEN_FILE = Path(__file__).parent / "seen.json"
LINE_BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast"

# ข่าวประกาศทั่วไปจากสำนักงาน ก.พ. จะมี URL รูปแบบ /portal/news/<ตัวเลข>
NEWS_URL_PATTERN = re.compile(r"/portal/news/\d+")
# ประกาศรับสมัครตำแหน่งงานแต่ละอัน จะมี URL รูปแบบ /portal/jobs/<ตัวเลข>
JOBS_URL_PATTERN = re.compile(r"/portal/jobs/(\d+)")


def load_seen() -> set:
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def save_seen(seen: set) -> None:
    # เก็บแค่ 500 รายการล่าสุดกันไฟล์บวมไม่รู้จบ
    trimmed = list(seen)[-500:]
    SEEN_FILE.write_text(
        json.dumps(trimmed, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def fetch_news() -> list[dict]:
    """ดึงข่าวประกาศทั่วไปจากหน้าแรกของ portal (URL รูปแบบ /portal/news/<id>)"""
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(8000)

        anchors = page.query_selector_all("a")
        for a in anchors:
            try:
                text = (a.inner_text() or "").strip()
                href = a.get_attribute("href") or ""
            except Exception:
                continue

            if not text or len(text) < 8:
                continue
            if not NEWS_URL_PATTERN.search(href):
                continue

            if href.startswith("/"):
                href = "https://job.ocsc.go.th" + href
            elif not href.startswith("http"):
                href = PORTAL_URL

            results.append({"kind": "news", "title": text, "url": href})

        browser.close()

    unique = {}
    for item in results:
        unique[item["url"]] = item
    return list(unique.values())


def fetch_jobs() -> list[dict]:
    """
    ดึงประกาศรับสมัครตำแหน่งงานแต่ละอันจากหน้า job-office?type=1
    (URL รูปแบบ /portal/jobs/<id>) พร้อมชื่อหน่วยงาน ช่วงวันรับสมัคร และจำนวนอัตรา

    หมายเหตุสำคัญ: หน้ารายการหลัก (job-office?type=N) แสดงแค่ "การ์ดหน่วยงาน"
    (เช่น กรมการแพทย์ 22 อัตรา) เท่านั้น — ลิงก์ตำแหน่งงานย่อย /portal/jobs/<id>
    จะปรากฏก็ต่อเมื่อเข้าไปที่หน้าของหน่วยงานนั้นๆ (/portal/search?department=<ชื่อ>)
    แล้วเท่านั้น จึงต้องไล่เก็บลิงก์หน่วยงานทั้งหมดก่อน แล้วค่อยเข้าไปทีละหน่วยงาน
    """
    # ประเภทบุคลากร 3 แท็บบนเว็บ: 1=ข้าราชการพลเรือน, 2=พนักงานราชการ, 3=บุคลากรประเภทอื่น
    job_office_types = [1, 2, 3]
    all_raw_jobs: dict[str, dict] = {}

    extract_jobs_script = """
        () => {
            const jobs = new Map();
            const anchors = Array.from(
                document.querySelectorAll('a[href*="/portal/jobs/"]')
            );
            for (const a of anchors) {
                const href = a.getAttribute('href') || '';
                const match = href.match(/\\/portal\\/jobs\\/(\\d+)/);
                if (!match) continue;
                const id = match[1];
                if (!jobs.has(id)) {
                    jobs.set(id, { texts: [], blockText: '' });
                }
                const entry = jobs.get(id);
                const text = (a.innerText || '').trim();
                if (text) entry.texts.push(text);

                let el = a;
                for (let i = 0; i < 8 && el; i++) {
                    el = el.parentElement;
                    if (el && el.innerText && el.innerText.includes('อัตรา')) {
                        if (el.innerText.length > entry.blockText.length
                            || entry.blockText === '') {
                            if (entry.blockText === '' ||
                                el.innerText.length < entry.blockText.length + 400) {
                                entry.blockText = el.innerText;
                            }
                        }
                        break;
                    }
                }
            }
            return Array.from(jobs.entries()).map(([id, v]) => ({
                id, texts: v.texts, blockText: v.blockText
            }));
        }
        """

    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="th-TH",
            timezone_id="Asia/Bangkok",
            extra_http_headers={"Accept-Language": "th-TH,th;q=0.9,en;q=0.8"},
        )
        # ซ่อนร่องรอยว่าเป็น headless browser เพิ่มเติม (บางเว็บเช็คจุดนี้)
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = context.new_page()

        # --- ขั้นที่ 1: เก็บลิงก์การ์ดหน่วยงานจากทั้ง 3 แท็บ ---
        department_urls: set[str] = set()

        for job_type in job_office_types:
            list_url = f"https://job.ocsc.go.th/portal/job-office?type={job_type}"
            try:
                page.goto(list_url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                print(f"[DEBUG] page.goto timeout/error (type={job_type}): {e}")
                continue

            page.wait_for_timeout(4000)

            try:
                page.wait_for_selector(
                    'a[href*="/portal/search?department="]', timeout=15000
                )
            except Exception:
                pass

            hrefs = page.eval_on_selector_all(
                'a[href*="/portal/search?department="]',
                "els => els.map(e => e.getAttribute('href'))",
            )
            print(f"[DEBUG] type={job_type}: เจอการ์ดหน่วยงาน {len(hrefs)} ใบ")

            for href in hrefs:
                if not href:
                    continue
                if href.startswith("/"):
                    href = "https://job.ocsc.go.th" + href
                department_urls.add(href)

        print(f"[DEBUG] รวมหน่วยงานทั้งหมด (ไม่ซ้ำ) {len(department_urls)} หน่วยงาน")

        # --- ขั้นที่ 2: เข้าไปทีละหน่วยงาน เก็บลิงก์ /portal/jobs/<id> ---
        for i, dept_url in enumerate(sorted(department_urls), start=1):
            try:
                page.goto(dept_url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                print(f"[DEBUG] เข้าไม่ถึงหน่วยงาน ({dept_url}): {e}")
                continue

            page.wait_for_timeout(2500)

            try:
                page.wait_for_selector('a[href*="/portal/jobs/"]', timeout=10000)
            except Exception:
                pass

            raw_jobs = page.evaluate(extract_jobs_script)
            print(f"[DEBUG] ({i}/{len(department_urls)}) {dept_url} -> {len(raw_jobs)} ตำแหน่ง")

            # ดึงชื่อหน่วยงานออกจาก query param ?department=<ชื่อ> ของ URL
            qs = parse_qs(urlparse(dept_url).query)
            department_name = unquote(qs.get("department", [""])[0]) or "หน่วยงานไม่ระบุชื่อ"

            for job in raw_jobs:
                job["department"] = department_name
                job["department_url"] = dept_url
                all_raw_jobs[job["id"]] = job

        # --- DEBUG: เก็บ screenshot + HTML ของหน้าสุดท้ายไว้ตรวจสอบ ---
        try:
            debug_dir = Path(__file__).parent / "debug_output"
            debug_dir.mkdir(exist_ok=True)
            page.screenshot(path=str(debug_dir / "jobs_page.png"), full_page=True)
            (debug_dir / "jobs_page.html").write_text(
                page.content(), encoding="utf-8"
            )
            print(f"[DEBUG] บันทึก screenshot และ HTML ไว้ที่ {debug_dir} แล้ว")
        except Exception as e:
            print(f"[DEBUG] บันทึกไฟล์ debug ไม่สำเร็จ: {e}")
        # --- END DEBUG ---

        browser.close()

    period_pattern = re.compile(r"เปิดรับสมัคร\s*([^\n]+)")
    quota_pattern = re.compile(r"(\d[\d,]*)\s*อัตรา")

    # บรรทัดที่ไม่ใช่ชื่อตำแหน่งจริง ต้องกรองทิ้งตอนหา title
    personnel_types = {"ข้าราชการพลเรือน", "พนักงานราชการ", "บุคลากรประเภทอื่น"}
    read_count_pattern = re.compile(r"^อ่าน[\d,\s]*ครั้ง$")
    quota_line_pattern = re.compile(r"^\d[\d,]*\s*อัตรา$")
    date_range_pattern = re.compile(r"\d{1,2}\s*\S*\.?\s*\d{4}\s*-\s*\d{1,2}")

    def extract_title(texts: list[str], department_name: str) -> str:
        """แยกเฉพาะบรรทัดที่เป็นชื่อตำแหน่งจริง ตัดประเภทบุคลากร/ชื่อหน่วยงาน/
        วันที่/จำนวนอัตรา/ยอดอ่าน ที่อาจติดมาด้วยถ้าการ์ดห่อด้วย <a> เดียวทั้งก้อน"""
        candidates = []
        for t in texts:
            for line in t.splitlines():
                line = line.strip()
                if not line:
                    continue
                if line in personnel_types:
                    continue
                if department_name and line == department_name:
                    continue
                if line.startswith("เปิดรับสมัคร"):
                    continue
                if read_count_pattern.match(line.replace(" ", "")):
                    continue
                if quota_line_pattern.match(line.replace(" ", "")):
                    continue
                if date_range_pattern.search(line):
                    continue
                candidates.append(line)
        if candidates:
            return max(candidates, key=len)
        # ถ้ากรองแล้วไม่เหลือเลย ใช้ข้อความยาวสุดจากเดิมเป็น fallback กันพัง
        return max(texts, key=len) if texts else ""

    results = []
    for job_id, job in all_raw_jobs.items():
        texts = [t for t in job["texts"] if t]
        if not texts:
            continue

        department_name = job.get("department", "")
        title = extract_title(texts, department_name)

        # หาวันที่/อัตรา จากข้อความของลิงก์ตัวเองก่อน (แม่นยำกว่า)
        # ถ้าไม่เจอค่อย fallback ไปที่ blockText (container ข้างนอก)
        own_text = "\n".join(texts)
        block = job.get("blockText", "") or ""

        period_match = period_pattern.search(own_text) or period_pattern.search(block)
        quota_match = quota_pattern.search(own_text) or quota_pattern.search(block)

        results.append({
            "kind": "job",
            "title": title,
            "department": department_name,
            "department_url": job.get("department_url", ""),
            "period": period_match.group(1).strip() if period_match else "",
            "quota": (quota_match.group(1) + " อัตรา") if quota_match else "",
            "url": f"https://job.ocsc.go.th/portal/jobs/{job_id}",
        })

    print(f"[DEBUG] แปลงเป็นรายการตำแหน่งงานได้ {len(results)} รายการ (จาก {len(department_urls)} หน่วยงาน)")
    return results


def fetch_announcements() -> list[dict]:
    """รวมทั้งข่าวประกาศทั่วไปและประกาศรับสมัครตำแหน่งงานเข้าด้วยกัน"""
    news = fetch_news()
    print(f"[DEBUG] เจอข่าวประกาศทั่วไป {len(news)} รายการ")
    jobs = fetch_jobs()
    return news + jobs


def send_line_broadcast(message: str, token: str) -> None:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    body = {"messages": [{"type": "text", "text": message[:5000]}]}
    resp = requests.post(LINE_BROADCAST_URL, headers=headers, json=body, timeout=15)
    if resp.status_code != 200:
        print(f"[LINE ERROR] {resp.status_code}: {resp.text}", file=sys.stderr)
    else:
        print("[LINE] ส่งแจ้งเตือนสำเร็จ")


def main():
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    if not token:
        print("กรุณาตั้งค่า environment variable LINE_CHANNEL_ACCESS_TOKEN ก่อน", file=sys.stderr)
        sys.exit(1)

    print("กำลังดึงรายการประกาศจาก job.ocsc.go.th ...")
    try:
        announcements = fetch_announcements()
    except Exception as e:
        print(f"ดึงข้อมูลไม่สำเร็จ: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"พบประกาศทั้งหมด {len(announcements)} รายการบนหน้าเว็บ")

    seen = load_seen()
    new_items = [a for a in announcements if a["url"] not in seen]

    if not new_items:
        print("ไม่มีประกาศใหม่")
        return

    print(f"พบประกาศใหม่ {len(new_items)} รายการ กำลังส่งแจ้งเตือน...")

    job_items = [item for item in new_items if item["kind"] == "job"]
    news_items = [item for item in new_items if item["kind"] != "job"]

    # --- จัดกลุ่มตำแหน่งงานใหม่ตามหน่วยงาน แล้วรวมเป็น 1 ข้อความต่อหน่วยงาน ---
    jobs_by_department: dict[str, list[dict]] = {}
    for item in job_items:
        dept = item.get("department") or "หน่วยงานไม่ระบุชื่อ"
        jobs_by_department.setdefault(dept, []).append(item)

    for dept, jobs in jobs_by_department.items():
        # หาช่วงวันรับสมัครที่พบบ่อยที่สุดในกลุ่ม มาแสดงครั้งเดียวด้านบน
        # (ปกติตำแหน่งในหน่วยงานเดียวกันมักเปิดรับพร้อมกันในช่วงเวลาเดียวกัน)
        periods = [item["period"] for item in jobs if item.get("period")]
        common_period = max(set(periods), key=periods.count) if periods else ""

        lines = [f"📢 {dept} เปิดรับสมัคร {len(jobs)} ตำแหน่ง"]
        if common_period:
            lines.append(f"รับสมัคร: {common_period}")

        for idx, item in enumerate(jobs, start=1):
            lines.append("")  # เว้นบรรทัดคั่นระหว่างตำแหน่ง
            lines.append(f"{idx}. {item['title']}")
            if item.get("quota"):
                lines.append(f"   จำนวน: {item['quota']}")
            # ถ้าตำแหน่งนี้วันรับสมัครไม่ตรงกับวันที่แสดงด้านบน ให้โชว์แยกกันไว้ กันข้อมูลหาย
            if item.get("period") and item["period"] != common_period:
                lines.append(f"   รับสมัคร: {item['period']}")

        # ลิงก์เดียวท้ายข้อความ ไปยังหน้าหน่วยงานที่รวมทุกตำแหน่งไว้
        dept_url = jobs[0].get("department_url") or ""
        if dept_url:
            lines.append("")
            lines.append(f"ดูรายละเอียด: {dept_url}")

        message = "\n".join(lines)
        send_line_broadcast(message, token)
        for item in jobs:
            seen.add(item["url"])
        time.sleep(1)  # กันยิง API ถี่เกินไป

    # --- ข่าวประกาศทั่วไป ส่งทีละข้อความเหมือนเดิม ---
    for item in news_items:
        message = f"📢 ประกาศใหม่ (งานราชการ)\n{item['title']}\n{item['url']}"
        send_line_broadcast(message, token)
        seen.add(item["url"])
        time.sleep(1)

    save_seen(seen)
    print("อัปเดต seen.json เรียบร้อย")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        print("[FATAL ERROR] สคริปต์ล่มระหว่างทำงาน:", flush=True)
        traceback.print_exc()
        sys.exit(1)
