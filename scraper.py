import json, os, random, re, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
from bs4 import BeautifulSoup

# LinkedIn cuts very long queries, so titles are split into groups (one search per group)
KEYWORD_GROUPS = [
    'SOC OR "security operations" OR "security analyst" OR "cyber security" OR cybersecurity OR "information security"',
    '"incident response" OR "threat intelligence" OR SIEM OR "threat hunter" OR "detection engineer" OR MDR OR XDR '
    'OR EDR OR DFIR OR "digital forensics" OR "malware analyst" OR "blue team"',
    'VAPT OR "penetration tester" OR pentest OR "ethical hacker" OR "vulnerability analyst" OR "vulnerability management" '
    'OR "red team" OR "offensive security" OR "security researcher" OR "exploit developer"',
    '"application security" OR AppSec OR "product security" OR DevSecOps OR "API security" OR "mobile security" '
    'OR "secure code" OR "web application security"',
    '"network security" OR firewall OR "endpoint security" OR "security engineer" OR "security administrator" '
    'OR "OT security" OR "ICS security" OR "cloud security" OR CSPM OR CNAPP',
    '"identity and access" OR IAM OR PAM OR "access management" OR GRC OR "IT risk" OR "IT audit" OR "cyber audit" '
    'OR "ISO 27001" OR "third party risk" OR TPRM OR "data privacy" OR "data protection"',
    '"cybersecurity intern" OR "cyber security intern" OR "security intern" OR "information security intern" '
    'OR "cyber security trainee" OR "security associate" OR "security consultant" OR "security support"',
]
# (location, pages of 25). Countries where an Indian fresher can realistically get hired / sponsored.
LOCATIONS = [("India", 4), ("Kochi, Kerala, India", 1), ("Thiruvananthapuram, Kerala, India", 1),
             ("Bengaluru, Karnataka, India", 2), ("United Arab Emirates", 2), ("Saudi Arabia", 2),
             ("Qatar", 1), ("Oman", 1), ("Bahrain", 1), ("Kuwait", 1), ("Singapore", 1), ("Malaysia", 1),
             ("Sri Lanka", 1), ("Maldives", 1), ("Vietnam", 1), ("Thailand", 1), ("Indonesia", 1),
             ("Philippines", 1), ("Hong Kong SAR", 1), ("Japan", 1),
             ("Germany", 1), ("Netherlands", 1), ("Ireland", 1), ("United Kingdom", 1), ("Poland", 1),
             ("Canada", 1), ("Australia", 1), ("New Zealand", 1)]
FAST_LOCATIONS = [("India", 2), ("United Arab Emirates", 1)]   # "India" already covers Kochi and Bengaluru
# Remote-only searches (LinkedIn f_WT=2). Hybrid jobs already come through the city/country searches above.
REMOTE_SEARCHES = [("India", 2), ("Worldwide", 2)]
REMOTE_OPEN = re.compile(r"\b(anywhere|worldwide|global(ly)?|remote[- ]first|work from anywhere|india|apac|asia)\b", re.I)
REMOTE_BLOCKED = re.compile(r"\b(us|u\.s\.|united states|uk|eu|europe|canada|australia)[- ]only\b|"
                            r"must (be|reside|live) (located |based )?in (the )?(us|u\.s\.|united states|uk|europe|eu|canada)|"
                            r"us citizens?|green card", re.I)
def work_mode(search_remote, desc):
    if search_remote or re.search(r"\b(fully remote|100% remote|remote (role|position|job|work)|work from home|wfh)\b", desc, re.I):
        return "Remote"
    if re.search(r"\bhybrid\b", desc, re.I):
        return "Hybrid"
    return "On-site / not stated"
# In these countries a fresher from India needs the employer to sponsor a visa, so keep only posts that say so
SPONSOR_COUNTRIES = {"Japan", "Germany", "Netherlands", "Ireland", "United Kingdom", "Poland",
                     "Canada", "Australia", "New Zealand", "Hong Kong SAR"}
SPONSOR_YES = re.compile(r"visa sponsor|sponsorship (is )?(available|provided|offered)|will sponsor|relocation "
                         r"(support|assistance|package|provided)|relocate|work permit (support|provided)", re.I)
SPONSOR_NO = re.compile(r"no (visa )?sponsorship|unable to sponsor|not (able to )?sponsor|cannot sponsor|"
                        r"must (have|hold) (the )?(right|authori[sz]ation) to work|citizens? only|"
                        r"permanent residents? only|security clearance", re.I)
MAX_HOURS, SEEN_DAYS = 24, 30
# LIVE=1: never stops — quick check every ~3 min (priority places, last 1h) + full sweep every hour.
# Otherwise one full sweep (all places, last 6h) and exit — used by the hourly GitHub Action.
LIVE = os.environ.get("LIVE") == "1"
LIVE_EVERY, FULL_EVERY = 180, 3600   # seconds
FAST, WINDOW = False, "r21600"
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")   # phone push via the free ntfy app; set as a GitHub secret
MAX_ALERTS = 10
SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/126.0 Safari/537.36", "Accept-Language": "en-US,en;q=0.9"}
DATA = Path(os.environ.get("DATA_DIR") or Path(__file__).parent / "data")

SECURITY_WORDS = re.compile(
    r"\b(soc|security|cyber|cybersecurity|infosec|vapt|penetration|pentest(er|ing)?|ethical hack(er|ing)|appsec|"
    r"devsecops|threat|incident|siem|soar|edr|xdr|mdr|dfir|forensics?|malware|blue team|red team|"
    r"vulnerability|firewall|iam|pam|identity and access|access management|grc|it risk|it audit(or)?|"
    r"cyber audit|iso ?27001|third[- ]party risk|tprm|data privacy|data protection|cspm|cnapp|ot/ics|ics)\b", re.I)
TOO_SENIOR = re.compile(r"\b(senior|sr\.?|lead|manager|principal|architect|head|director|staff|"
                        r"chief|vp|vice president|expert|sme|"
                        r"l2|l3|level\s*(2|3|ii|iii)|tier\s*(2|3))\b", re.I)
JUNIOR_WORDS = re.compile(r"\b(intern(ship)?|fresher|trainee|junior|jr\.?|graduate|entry|l1|associate)\b", re.I)
YEARS = re.compile(r"(?:minimum(?: of)?|min\.?|at least)?\s*(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*(\d{1,2})\s*)?"
                   r"\+?\s*(?:years?|yrs?)\b[^.]{0,40}?\bexp", re.I)

# Fake / pay-to-intern filters
SCAM_WORDS = re.compile(
    r"\b(registration|training|course|enrol?ment|certification|processing|onboarding|kit)\s+(fee|fees|charges?|cost)\b|"
    r"security deposit|refundable deposit|pay (a |the )?(fee|amount|deposit)|fee of\s*(rs|inr|₹)|"
    r"\bunpaid\b|no stipend|without stipend|(rs\.?|inr|₹)\s?[\d,]+\s*(/-)?\s*(fee|deposit|registration)|"
    r"whatsapp only", re.I)
TRAINING_CO = re.compile(r"\b(academy|institute|training|edtech|edutech|learning|school|classes|bootcamp)\b", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

def scam_reason(job, desc):
    m = SCAM_WORDS.search(desc)
    if m:
        return f"possible scam/paid: '{m.group(0)}'"
    if JUNIOR_WORDS.search(job["title"]) and TRAINING_CO.search(job["company"]):
        return "internship from a training institute"
    return ""

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

NOT_CYBER = re.compile(r"\b(guard|bouncer|cctv|patrol|loss prevention|security supervisor|security officer|"
                       r"gunman|watchman|social security)\b", re.I)

def title_ok(title):
    return (bool(SECURITY_WORDS.search(title)) and not TOO_SENIOR.search(title)
            and not NOT_CYBER.search(title))

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
    return bool(m) and int(m.group(1)) < 50

def hours_ago(posted):
    m = re.search(r"(\d+)\s+(minute|hour|day|week|month)", posted)
    if not m:
        return None
    n, u = int(m.group(1)), m.group(2)
    return {"minute": 0, "hour": n, "day": n * 24, "week": n * 168, "month": n * 720}[u]

BLOCKS = [0]

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
    BLOCKS[0] += 1
    return ""

def alert(jobs):
    """Instant phone notification for each new job (ntfy.sh, free)."""
    if not NTFY_TOPIC or not jobs:
        return
    for j in jobs[:MAX_ALERTS]:
        msg = (f"{j['company']} · {j['location']} · {j.get('work_mode', '')}\n"
               f"{j['posted']} · {j['applicants'] or 'applicants not shown'}"
               + (f"\nEmail: {', '.join(j['emails'])}" if j.get("emails") else ""))
        try:
            requests.post("https://ntfy.sh/", timeout=15, json={
                "topic": NTFY_TOPIC, "title": f"New job: {j['title']}"[:250], "message": msg,
                "click": j["url"], "priority": 4, "tags": ["rotating_light"],
                "actions": [{"action": "view", "label": "Open job", "url": j["url"]}]})
        except requests.RequestException as e:
            print(f"alert failed: {e}", file=sys.stderr)
    if len(jobs) > MAX_ALERTS:
        requests.post("https://ntfy.sh/", timeout=15, json={"topic": NTFY_TOPIC,
            "title": f"+{len(jobs) - MAX_ALERTS} more new jobs", "message": "See your Job Alert email."})

def load(name, default):
    p = DATA / name
    return json.loads(p.read_text()) if p.exists() else default

def main():
    now = datetime.now(timezone.utc)
    seen = {k: v for k, v in load("checked.json", {}).items()
            if datetime.fromisoformat(v) > now - timedelta(days=SEEN_DAYS)}
    session, cards = requests.Session(), {}
    BLOCKS[0] = 0
    locs, remotes = (FAST_LOCATIONS, [("India", 1)]) if FAST else (LOCATIONS, REMOTE_SEARCHES)
    searches = [(loc, pages, False) for loc, pages in locs] + [(loc, pages, True) for loc, pages in remotes]
    for kw in KEYWORD_GROUPS:
        for loc, pages, remote in searches:
            for page in range(pages):
                params = {"keywords": kw, "location": loc, "f_TPR": WINDOW, "f_E": "1,2", "sortBy": "DD", "start": page * 25}
                if remote:
                    params["f_WT"] = "2"
                batch = parse_search(get(session, SEARCH_URL, params))
                for j in batch:
                    c = cards.setdefault(j["id"], {**j, "search_loc": loc, "remote_search": remote})
                    c["remote_search"] = c["remote_search"] or remote
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
            else:
                why = scam_reason(job, d["description"])
            if not why and job["search_loc"] == "Worldwide" and (
                    REMOTE_BLOCKED.search(d["description"]) or not REMOTE_OPEN.search(d["description"])):
                why = "remote but not open to India"
            if not why and job["search_loc"] in SPONSOR_COUNTRIES:
                if SPONSOR_NO.search(d["description"]) or not SPONSOR_YES.search(d["description"]):
                    why = "no visa sponsorship mentioned"
        if why:
            rejected.append({"title": job["title"], "company": job["company"],
                             "location": job["location"], "url": job["url"], "reason": why})
            continue
        job.update(posted=posted, hours_ago=h, applicants=d["applicants"],
                   seniority=d["seniority"], description=d["description"][:3000],
                   emails=sorted({e.rstrip(".") for e in EMAIL.findall(d["description"])}),
                   work_mode=work_mode(job.pop("remote_search"), d["description"]))
        job.pop("search_loc", None)
        new_jobs.append(job)
    new_jobs.sort(key=lambda j: j["hours_ago"])
    alert(new_jobs)
    # keep jobs found in the last 3 hours (several runs) so the hourly apply/digest task sees all of them
    keep = [j for j in load("jobs.json", {}).get("jobs", [])
            if datetime.fromisoformat(j.get("found_at", "2000-01-01T00:00:00+00:00")) > now - timedelta(hours=3)]
    for j in new_jobs:
        j["found_at"] = now.isoformat()
    new_jobs = new_jobs + [j for j in keep if j["id"] not in {n["id"] for n in new_jobs}]
    DATA.mkdir(exist_ok=True)
    (DATA / "jobs.json").write_text(json.dumps({"generated_at": now.isoformat(),
        "count": len(new_jobs), "jobs": new_jobs}, indent=2, ensure_ascii=False))
    (DATA / "checked.json").write_text(json.dumps(seen, indent=1, sort_keys=True))
    (DATA / "rejected.json").write_text(json.dumps({"generated_at": now.isoformat(),
        "cards": len(cards), "rejected": rejected}, indent=2, ensure_ascii=False))
    print(f"{now:%H:%M} {'quick' if FAST else 'full'}: {len(cards)} cards, {len(new_jobs)} listed, "
          f"{len(rejected)} rejected, {BLOCKS[0]} blocked", flush=True)
    return BLOCKS[0] >= 3

def live():
    """Run forever: quick check every ~3 min, full sweep every hour. Sleeps 15 min if LinkedIn blocks us."""
    global FAST, WINDOW
    last_full = float("-inf")
    while True:
        full = time.time() - last_full >= FULL_EVERY
        FAST, WINDOW = (False, "r21600") if full else (True, "r3600")
        try:
            blocked = main()
            if full:
                last_full = time.time()
        except Exception as e:          # never die; log and keep going
            print(f"{datetime.now(timezone.utc):%H:%M} error: {e}", file=sys.stderr); blocked = False
        time.sleep(900 if blocked else LIVE_EVERY + random.uniform(-30, 30))

if __name__ == "__main__":
    live() if LIVE else main()
