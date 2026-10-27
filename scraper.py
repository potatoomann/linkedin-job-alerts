import json, random, re, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
from bs4 import BeautifulSoup

KEYWORDS = ('SOC OR "cyber security" OR cybersecurity OR "security analyst" '
            'OR VAPT OR "penetration tester" OR pentest OR "application security"')
LOCATIONS = ["Kochi, Kerala, India", "Bengaluru, Karnataka, India", "United Arab Emirates",
             "Qatar", "Saudi Arabia", "Oman", "Bahrain", "Kuwait"]
PAGES, MAX_HOURS, SEEN_DAYS = 2, 24, 30
SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/126.0 Safari/537.36", "Accept-Language": "en-US,en;q=0.9"}
DATA = Path(__file__).parent / "data"

SECURITY_WORDS = re.compile(r"\b(soc|security|cyber|cybersecurity|vapt|penetration|pentest(er)?|"
                            r"appsec|threat|incident|siem|infosec|blue team|red team)\b", re.I)
TOO_SENIOR = re.compile(r"\b(senior|sr\.?|lead|manager|principal|architect|head|director|staff|"
                        r"l2|l3|level\s*(2|3|ii|iii)|tier\s*(2|3))\b", re.I)
JUNIOR_WORDS = re.compile(r"\b(intern(ship)?|fresher|trainee|junior|jr\.?|graduate|entry|l1|associate)\b", re.I)
YEARS = re.compile(r"(?:minimum(?: of)?|min\.?|at least)?\s*(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*(\d{1,2})\s*)?"
                   r"\+?\s*(?:years?|yrs?)\b[^.]{0,40}?\bexp", re.I)

def clean(el):
    return " ".join(el.get_text(" ").split()) if el else ""

def parse_search(html):
    jobs = []
    for card in BeautifulSoup(html, "html.parser").select("div.base-card[data-entity-urn]"):
        jid = card["data-entity-urn"].rsplit(":", 1)[-1]
        t = card.select_one("time")
        jobs.append({"id": jid, "title": clean(card.select_one(".base-search-card__title")),
                     "company": clean(card.select_one(".base-search-card__subtitle")),
                     "location": clean(card.select_one(".job-search-card__location")),
                     "posted": clean(t), "posted_date": t.get("datetime", "") if t else "",
                     "url": f"https://www.linkedin.com/jobs/view/{jid}/"})
    return jobs

def parse_detail(html):
    soup = BeautifulSoup(html, "html.parser")
    seniority = ""
    for item in soup.select(".description__job-criteria-item"):
        if "seniority" in clean(item.select_one("h3")).lower():
            seniority = clean(item.select_one(".description__job-criteria-text"))
    return {"applicants": clean(soup.select_one(".num-applicants__caption")),
            "posted": clean(soup.select_one(".posted-time-ago__text")),
            "seniority": seniority,
            "description": clean(soup.select_one(".show-more-less-html__markup"))}

def title_ok(title):
    return bool(SECURITY_WORDS.search(title)) and not TOO_SENIOR.search(title)

def min_years(text):
    found = [int(m.group(1)) for m in YEARS.finditer(text) if int(m.group(1)) <= 15]
    return min(found) if found else None

def experience_ok(desc, seniority, title):
    y = min_years(desc)
    if y is not None:
        return y <= 1
    if seniority.lower() in ("internship", "entry level"):
        return True
    return bool(JUNIOR_WORDS.search(title))

def first_25(applicants):
    if "first 25" in applicants.lower():
        return True
    m = re.search(r"(\d+)\s+applicants", applicants)
    return bool(m) and int(m.group(1)) < 25

def hours_ago(posted):
    m = re.search(r"(\d+)\s+(minute|hour|day|week|month)", posted)
    if not m:
        return None
    n, u = int(m.group(1)), m.group(2)
    return {"minute": 0, "hour": n, "day": n * 24, "week": n * 168, "month": n * 720}[u]

def get(session, url, params=None):
    for attempt in range(4):
        time.sleep(random.uniform(2, 4))
        r = session.get(url, params=params, headers=HEADERS, timeout=30)
        if r.status_code == 200:
            return r.text
        if r.status_code in (429, 999) or r.status_code >= 500:
            time.sleep(15 * (attempt + 1)); continue
        return ""
    print(f"gave up on {url} {params}", file=sys.stderr)
    return ""

def load(name, default):
    p = DATA / name
    return json.loads(p.read_text()) if p.exists() else default

def main():
    now = datetime.now(timezone.utc)
    seen = {k: v for k, v in load("checked.json", {}).items()
            if datetime.fromisoformat(v) > now - timedelta(days=SEEN_DAYS)}
    session, cards = requests.Session(), {}
    for loc in LOCATIONS:
        for page in range(PAGES):
            batch = parse_search(get(session, SEARCH_URL, {"keywords": KEYWORDS, "location": loc,
                    "f_TPR": "r86400", "f_E": "1,2", "sortBy": "DD", "start": page * 25}))
            for j in batch:
                cards.setdefault(j["id"], j)
            if len(batch) < 25:
                break
    new_jobs, rejected = [], []
    for job in cards.values():
        if job["id"] in seen:
            continue
        why = ""
        if not title_ok(job["title"]):
            why = "title not security/junior"
        else:
            d = parse_detail(get(session, DETAIL_URL.format(job["id"])))
            seen[job["id"]] = now.isoformat()
            posted = d["posted"] or job["posted"]
            h = hours_ago(posted)
            if h is None or h > MAX_HOURS:
                why = f"posted: {posted or 'unknown'}"
            elif not first_25(d["applicants"]):
                why = f"applicants: {d['applicants'] or 'unknown'}"
            elif not experience_ok(d["description"], d["seniority"], job["title"]):
                why = f"experience: {min_years(d['description'])} yrs, {d['seniority'] or 'no level'}"
        if why:
            rejected.append({"title": job["title"], "company": job["company"],
                             "location": job["location"], "url": job["url"], "reason": why})
            continue
        job.update(posted=posted, hours_ago=h, applicants=d["applicants"],
                   seniority=d["seniority"], description=d["description"][:3000])
        new_jobs.append(job)
    new_jobs.sort(key=lambda j: j["hours_ago"])
    DATA.mkdir(exist_ok=True)
    (DATA / "jobs.json").write_text(json.dumps({"generated_at": now.isoformat(),
        "count": len(new_jobs), "jobs": new_jobs}, indent=2, ensure_ascii=False))
    (DATA / "checked.json").write_text(json.dumps(seen, indent=1, sort_keys=True))
    (DATA / "rejected.json").write_text(json.dumps({"generated_at": now.isoformat(),
        "cards": len(cards), "rejected": rejected}, indent=2, ensure_ascii=False))
    print(f"searched {len(cards)} cards, {len(new_jobs)} new, {len(rejected)} rejected")

if __name__ == "__main__":
    main()
