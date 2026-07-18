def fetch_jobs() -> list[dict]:
    """
    ดึงประกาศรับสมัครตำแหน่งงานแต่ละอันจากหน้า job-office?type=1
    (URL รูปแบบ /portal/jobs/<id>) พร้อมชื่อหน่วยงาน ช่วงวันรับสมัคร และจำนวนอัตรา
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

        raw_jobs = []
        # ลองโหลดซ้ำสูงสุด 3 ครั้ง ถ้ายังไม่เจอลิงก์เลย (กันเคส CI โหลดช้า/พลาด)
        for attempt in range(1, 4):
            page.goto(JOBS_LIST_URL, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(5000)

            for _ in range(4):
                page.mouse.wheel(0, 2000)
                page.wait_for_timeout(1500)

            try:
                page.wait_for_selector(
                    'a[href*="/portal/jobs/"]', timeout=20000
                )
            except Exception:
                pass

            anchor_count = page.eval_on_selector_all(
                'a[href*="/portal/jobs/"]', "els => els.length"
            )
            print(f"[DEBUG] (รอบที่ {attempt}) เจอลิงก์ /portal/jobs/ ทั้งหมด {anchor_count} ลิงก์")

            if anchor_count > 0:
                break
            print(f"[DEBUG] ยังไม่เจอลิงก์ — โหลดหน้าใหม่อีกครั้ง (attempt {attempt}/3)")
            page.wait_for_timeout(3000)

        print(f"[DEBUG] URL ปัจจุบันหลังโหลด: {page.url}")

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
