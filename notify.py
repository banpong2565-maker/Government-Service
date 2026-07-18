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

PORTAL_URL = "https://job.ocsc.go.th/portal"
SEEN_FILE = Path(__file__).parent / "seen.json"
LINE_BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast"

# คำที่มักปรากฏในลิงก์/ข้อความของประกาศรับสมัคร ใช้กรอง element ที่ไม่เกี่ยวข้องออก
KEYWORDS = ["รับสมัคร", "ประกาศ", "สอบ", "คัดเลือก", "สรรหา", "บรรจุ"]


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


def fetch_announcements() -> list[dict]:
    """
    เปิดหน้า portal ด้วย headless browser แล้วดึงลิงก์ประกาศทั้งหมด
    เนื่องจากหน้านี้เป็น SPA (React/Vue) ต้องรอให้ JS render เสร็จก่อนอ่าน DOM

    หมายเหตุ: ถ้าโครงสร้างหน้าเว็บมีการเปลี่ยนแปลง อาจต้องปรับ selector ด้านล่าง
    วิธีหา selector ที่แม่นขึ้น: เปิดหน้าเว็บจริงในเบราว์เซอร์ -> กด F12 (DevTools)
    -> คลิกขวาที่หัวข้อประกาศแต่ละอัน -> Inspect -> ดู class/tag ที่ครอบ แล้วมาแก้
    ในฟังก์ชันนี้
    """
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(PORTAL_URL, wait_until="networkidle", timeout=60000)

        # รอเผื่อกรณี render ช้า
        page.wait_for_timeout(3000)

        anchors = page.query_selector_all("a")
        for a in anchors:
            try:
                text = (a.inner_text() or "").strip()
                href = a.get_attribute("href") or ""
            except Exception:
                continue

            if not text or len(text) < 8:
                continue
            if not any(k in text for k in KEYWORDS):
                continue

            # ทำ href ให้เป็น absolute URL
            if href.startswith("/"):
                href = "https://job.ocsc.go.th" + href
            elif not href.startswith("http"):
                href = PORTAL_URL

            results.append({"title": text, "url": href})

        browser.close()

    # ตัดรายการซ้ำ (title เดียวกัน)
    unique = {}
    for item in results:
        unique[item["title"]] = item
    return list(unique.values())


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
    new_items = [a for a in announcements if a["title"] not in seen]

    if not new_items:
        print("ไม่มีประกาศใหม่")
        return

    print(f"พบประกาศใหม่ {len(new_items)} รายการ กำลังส่งแจ้งเตือน...")

    for item in new_items:
        message = f"📢 ประกาศใหม่ (งานราชการ)\n{item['title']}\n{item['url']}"
        send_line_broadcast(message, token)
        seen.add(item["title"])
        time.sleep(1)  # กันยิง API ถี่เกินไป

    save_seen(seen)
    print("อัปเดต seen.json เรียบร้อย")


if __name__ == "__main__":
    main()
