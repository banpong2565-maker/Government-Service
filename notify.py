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

    หมายเหตุ: หน้านี้อาจมีการแบ่งหน้า (pagination) หรือปุ่ม "โหลดเพิ่ม" ซึ่งสคริปต์นี้
    ยังไม่รองรับ จะดึงได้เฉพาะรายการที่แสดงอยู่ในการโหลดครั้งแรกเท่านั้น
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
        page = context.new_page()
        page.goto(JOBS_LIST_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)

        # เผื่อรายการโหลดแบบ lazy/infinite-scroll: เลื่อนหน้าลงสองสามครั้ง
        for _ in range(4):
            page.mouse.wheel(0, 2000)
            page.wait_for_timeout(1500)

        # รอให้แน่ใจว่ามีลิงก์ /portal/jobs/ ปรากฏจริง ก่อนอ่าน DOM ต่อ
        try:
            page.wait_for_selector('a[href*="/portal/jobs/"]', timeout=15000)
        except Exception:
            pass

        anchor_count = page.eval_on_selector_all(
            'a[href*="/portal/jobs/"]', "els => els.length"
        )
        print(f"[DEBUG] เจอลิงก์ /portal/jobs/ ทั้งหมด {anchor_count} ลิงก์ (รวมซ้ำ)")
        print(f"[DEBUG] URL ปัจจุบันหลังโหลด: {page.url}")
        body_snippet = page.evaluate(
            "() => (document.body.innerText || '').slice(0, 300)"
        )
        print(f"[DEBUG] เนื้อหาจริงบนหน้า (300 ตัวอักษรแรก): {body_snippet!r}")

        raw_jobs = page.evaluate(
            """
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

                    // เดินขึ้นไปหา container ที่มีคำว่า "อัตรา" (ใกล้สุดที่เจอ)
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
        )

        browser.close()

    period_pattern = re.compile(r"เปิดรับสมัคร\s*([^\n]+)")
    quota_pattern = re.compile(r"(\d[\d,]*)\s*อัตรา")

    results = []
    for job in raw_jobs:
        texts = [t for t in job["texts"] if t]
        if not texts:
            continue
        # ชื่อตำแหน่งมักยาวกว่าคำว่าประเภทบุคลากร (เช่น "ข้าราชการพลเรือน")
        title = max(texts, key=len)

        block = job.get("blockText", "") or ""
        period_match = period_pattern.search(block)
        quota_match = quota_pattern.search(block)

        results.append({
            "kind": "job",
            "title": title,
            "period": period_match.group(1).strip() if period_match else "",
            "quota": (quota_match.group(1) + " อัตรา") if quota_match else "",
            "url": f"https://job.ocsc.go.th/portal/jobs/{job['id']}",
        })

    print(f"[DEBUG] แปลงเป็นรายการตำแหน่งงานได้ {len(results)} รายการ")
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

    for item in new_items:
        if item["kind"] == "job":
            extra_lines = []
            if item.get("period"):
                extra_lines.append(f"รับสมัคร: {item['period']}")
            if item.get("quota"):
                extra_lines.append(f"จำนวน: {item['quota']}")
            extra = ("\n" + "\n".join(extra_lines)) if extra_lines else ""
            message = f"📢 เปิดรับสมัครตำแหน่งใหม่\n{item['title']}{extra}\n{item['url']}"
        else:
            message = f"📢 ประกาศใหม่ (งานราชการ)\n{item['title']}\n{item['url']}"

        send_line_broadcast(message, token)
        seen.add(item["url"])
        time.sleep(1)  # กันยิง API ถี่เกินไป

    save_seen(seen)
    print("อัปเดต seen.json เรียบร้อย")


if __name__ == "__main__":
    main()
