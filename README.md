# แจ้งเตือนประกาศงานราชการ (job.ocsc.go.th) เข้า LINE

สคริปต์นี้จะเช็คหน้า https://job.ocsc.go.th/portal ตามตารางเวลา แล้วส่งประกาศ
ที่ยังไม่เคยแจ้งเตือนเข้า LINE ของคุณผ่าน LINE Messaging API (broadcast)

## ขั้นตอนที่ 1: สร้าง LINE Official Account (ทำครั้งเดียว)

1. เข้า https://developers.line.biz/console/ แล้วล็อกอิน
2. สร้าง Provider ใหม่ (ชื่ออะไรก็ได้)
3. กด **Create a new channel** → เลือก **Messaging API** → กรอกข้อมูลแล้วสร้าง
4. ในแท็บ **Messaging API**:
   - เลื่อนหา **Channel access token** → กด Issue → คัดลอกเก็บไว้
   - สแกน QR Code ของบอทด้วยมือถือเพื่อ **แอดเป็นเพื่อน** (ขาดไม่ได้ ไม่งั้นส่งข้อความหาคุณไม่ได้)

## ขั้นตอนที่ 2: เตรียม GitHub repo

1. สร้าง repo ใหม่ (private ก็ได้) แล้วอัปโหลดไฟล์ทั้งหมดในโฟลเดอร์นี้ขึ้นไป
   (`notify.py`, `requirements.txt`, `.github/workflows/check.yml`)
2. สร้างไฟล์เปล่า ๆ ชื่อ `seen.json` ที่มีเนื้อหาแค่ `[]` แล้ว commit ขึ้นไปด้วย
   (ให้ script มีไฟล์ให้เขียนทับตั้งแต่รันครั้งแรก)
3. ไปที่ **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `LINE_CHANNEL_ACCESS_TOKEN`
   - Value: token ที่คัดลอกไว้จากขั้นตอนที่ 1
4. ไปที่แท็บ **Actions** ของ repo → เปิดใช้งาน workflow → ลองกด **Run workflow**
   ด้วยตัวเองครั้งแรกเพื่อทดสอบ

จากนั้นระบบจะรันอัตโนมัติทุก 30 นาทีตามที่ตั้งไว้ใน `check.yml`
(แก้บรรทัด `cron:` ถ้าอยากเปลี่ยนความถี่ เช่น `0 * * * *` = ทุกชั่วโมง)

## ทดสอบบนเครื่องตัวเองก่อน (แนะนำ)

```bash
pip install -r requirements.txt
playwright install chromium
export LINE_CHANNEL_ACCESS_TOKEN="ใส่ token ตรงนี้"
python notify.py
```

## ข้อควรรู้ / ข้อจำกัด

- หน้า job.ocsc.go.th เป็นเว็บที่โหลดข้อมูลด้วย JavaScript สคริปต์นี้ใช้วิธี
  กรองลิงก์จากคำสำคัญ (ประกาศ, รับสมัคร, สอบ, บรรจุ ฯลฯ) แทนการอิง CSS class
  เฉพาะ เพราะไม่ทราบโครงสร้าง DOM ที่แน่นอนล่วงหน้า **ถ้าลองรันแล้วได้ผลลัพธ์
  ไม่ตรง หรือไม่พบประกาศเลย** ให้เปิดหน้าเว็บจริงใน Chrome, กด F12 → Elements,
  คลิกขวาที่หัวข้อประกาศ → Inspect เพื่อดู class ที่ครอบ แล้วส่ง HTML ส่วนนั้น
  มาให้ผมช่วยปรับ selector ในฟังก์ชัน `fetch_announcements()` ให้แม่นยำขึ้นได้
- LINE broadcast จะส่งหา "ทุกคน" ที่แอดบอทนี้เป็นเพื่อน ถ้าใช้คนเดียวไม่มีปัญหา
  แต่ถ้ามีคนอื่นแอดด้วย เขาจะได้รับข้อความเดียวกัน
- Messaging API มีโควตาฟรีจำกัดจำนวนข้อความ push/broadcast ต่อเดือน
  (ปกติเพียงพอสำหรับใช้แจ้งเตือนส่วนตัว) ตรวจสอบโควตาปัจจุบันได้ในหน้า
  LINE Developers Console
