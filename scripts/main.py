#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, time, pathlib, requests
from bs4 import BeautifulSoup
from collections import OrderedDict

BASE = "https://obs.itu.edu.tr"
OUTFILE = os.path.join("..", "data", "itu_all_plans.txt")

# Scope
DEFAULT_PLAN_TYPES = [
    "lisans", "cap", "yandal", "uolp", "ddp",
    "yuksek-lisans", "yuksek-lisans-ikinci-ogretim", "doktora",
]
DEFAULT_LEVELS = [2]  # Lisans only; add more if needed

# Networking
DELAY, TIMEOUT, RETRIES = 0.4, 30, 3

PLAN_LABELS = {
    "lisans": "Lisans", "cap": "ÇAP", "yandal": "Yandal",
    "uolp": "UOLP/DDP", "ddp": "UOLP/DDP",
    "yuksek-lisans": "Yüksek Lisans",
    "yuksek-lisans-ikinci-ogretim": "Yüksek Lisans (İÖ)",
    "doktora": "Doktora",
}

# Patterns
PROGRAM_CODE_RE = re.compile(r"^[A-ZÇĞİÖŞÜ0-9_]+$")
DETAIL_HREF_RE  = re.compile(r"/public/DersPlan/DersPlanDetay/(\d+)")
SEM_HEADER_RE   = re.compile(r"(?:^\s*\d+\.\s*Yarıyıl|^\s*\d+\s*Semester|Yarıyıl|Semester)", re.I)
COURSE_CODE_RE  = re.compile(r"[A-ZÇĞİÖŞÜ]{2,}\s*\d{3,}[A-Z]?$", re.U)

# ------------------ HTTP helpers ------------------

def make_session():
    s = requests.Session()
    s.headers.update({"User-Agent": "ITU-CoursePlan-Scraper/1.2"})
    return s

def get(session, url, **kwargs):
    for attempt in range(RETRIES):
        try:
            r = session.get(url, timeout=TIMEOUT, **kwargs)
            r.raise_for_status()
            return r
        except Exception as e:
            if attempt < RETRIES - 1:
                time.sleep(0.8 * (attempt + 1))
            else:
                raise

# ------------------ faculties (from /public/DersPlan) ------------------

def fetch_faculties(session):
    """
    Build {faculty_id -> faculty_name} from the Fakülte <select id="akademikBirimId">.
    """
    url = f"{BASE}/public/DersPlan"
    r = get(session, url)
    soup = BeautifulSoup(r.text, "html.parser")

    facmap = {}
    for opt in soup.select("#akademikBirimId option"):
        val = (opt.get("value") or "").strip()
        name = opt.get_text(strip=True)
        if val and val.isdigit():
            facmap[val] = name
    if not facmap:
        print("[warn] Could not parse faculties from /public/DersPlan (dropdown).")
    else:
        print(f"[info] faculties loaded: {len(facmap)} items (from dropdown)")
    return facmap

# ------------------ programs (from ProgramKodlariList) ------------------

def nearest_prev_h5(el):
    h5 = el.find_previous("h5")
    if h5:
        txt = h5.get_text(strip=True)
        if txt and not txt.lower().startswith("program kodları"):
            return txt
    return None

def fetch_program_codes(session, level=2, facmap=None):
    """
    Return list of dicts: {faculty, faculty_id, name, programKodu}
    Tries, in order:
      1) row attribute data-akademikbirimid / data-fakulteid
      2) nearest previous <h5> header on the page
      3) fallback: "None"
    """
    url = f"{BASE}/public/GenelTanimlamalar/ProgramKodlariList?programSeviyeTipiId={level}"
    r = get(session, url)
    soup = BeautifulSoup(r.text, "html.parser")

    programs = []
    unknown_count = 0

    # Walk all tables; rows that look like [CODE, NAME, ...]
    for table in soup.select("table"):
        for tr in table.select("tbody tr"):
            tds = tr.select("td")
            if len(tds) < 2:
                continue
            code = tds[0].get_text(strip=True)
            name = tds[1].get_text(strip=True)
            if not PROGRAM_CODE_RE.match(code):
                continue

            # Try to read faculty id from attributes
            fac_id = tr.get("data-akademikbirimid") or tr.get("data-fakulteid") or ""
            faculty = None
            if fac_id and facmap and fac_id in facmap:
                faculty = facmap[fac_id]

            # If attribute not available, fall back to nearest <h5> grouping
            if not faculty:
                faculty = nearest_prev_h5(tr)

            # Final fallback
            if not faculty:
                faculty = "None"
                unknown_count += 1

            programs.append({
                "faculty": faculty,
                "faculty_id": fac_id or None,
                "name": name,
                "programKodu": code,
            })

    print(f"[info] program list (level={level}) -> {len(programs)} programs; unknown faculties: {unknown_count}")
    if programs:
        s = programs[0]
        print(f"       sample: {s['programKodu']} | {s['name']} | faculty={s['faculty']} (id={s['faculty_id']})")
    return programs

# ------------------ list plans for a program ------------------

def list_plan_ids(session, planTipiKodu, programKodu):
    url = f"{BASE}/public/DersPlan/DersPlanlariList"
    r = get(session, url, params={"planTipiKodu": planTipiKodu, "programKodu": programKodu})
    soup = BeautifulSoup(r.text, "html.parser")

    out = []
    for row in soup.select("table tbody tr"):
        a = row.select_one('a[href*="/public/DersPlan/DersPlanDetay/"]')
        if not a:
            continue
        m = DETAIL_HREF_RE.search(a.get("href", ""))
        if not m:
            continue
        effective_text = " ".join(row.get_text(" ", strip=True).split())
        out.append((m.group(1), effective_text))
    return out

# ------------------ parse a plan's semesters + courses ------------------

def _normalize_code(code):
    return re.sub(r"\s+", "", code)

def _semester_sort_key(title):
    m = re.search(r"(\d+)", title or "")
    return (int(m.group(1)) if m else 999, title or "")

def parse_plan_detail(session, plan_id):
    """
    Return OrderedDict: { semester_title -> [course_code, ...] }
    Strategy:
      1) find heading that looks like semester; use its next table
      2) fallback: scan all tables; if first column looks like course codes, accept in page order
    """
    url = f"{BASE}/public/DersPlan/DersPlanDetay/{plan_id}"
    r = get(session, url)
    soup = BeautifulSoup(r.text, "html.parser")

    candidates = []
    for hdr in soup.find_all(["h4", "h5", "h6", "strong"]):
        title = hdr.get_text(" ", strip=True)
        if title and SEM_HEADER_RE.search(title):
            tbl = hdr.find_next("table")
            if tbl:
                candidates.append((title, tbl))

    semesters = OrderedDict()
    if candidates:
        candidates.sort(key=lambda x: _semester_sort_key(x[0]))
        for title, tbl in candidates:
            codes = []
            for tr in tbl.select("tbody tr"):
                td0 = tr.find("td")
                if not td0:
                    continue
                code_raw = td0.get_text(strip=True)
                if code_raw and COURSE_CODE_RE.search(code_raw):
                    codes.append(_normalize_code(code_raw))
            semesters[title] = codes

    if not semesters:
        # fallback: detect course-y tables
        for idx, tbl in enumerate(soup.select("table")):
            first_col = [td.get_text(strip=True) for td in tbl.select("tbody tr td:first-child")]
            looks_like_courses = sum(1 for t in first_col if COURSE_CODE_RE.search(t)) >= 3
            if not looks_like_courses:
                continue
            codes = [_normalize_code(t) for t in first_col if COURSE_CODE_RE.search(t)]
            semesters[f"Semester {idx+1}"] = codes

    return semesters

# ------------------ text output ------------------

def clean_plan_header(program_name, effective_text):
    text = effective_text.strip()
    if text.lower().startswith("detay"):
        text = text[5:].strip()
    return text if (program_name in text) else f"{program_name} {text}"

def append_block(f, faculty, plan_type_label, program_name, plan_header, sem_to_codes):
    f.write("FACULTY\n")
    f.write(f"{faculty}\n")
    f.write("TYPE\n")
    f.write(f"{plan_type_label}\n")
    f.write("MAJOR\n")
    f.write(f"{program_name}\n")
    f.write("PLAN\n")
    f.write(f"{plan_header}\n")
    wrote_any = False
    for _, codes in sem_to_codes.items():
        if codes:
            f.write(";".join(codes) + "\n")
            wrote_any = True
    return wrote_any

# ------------------ main driver ------------------

def main():
    pathlib.Path(os.path.dirname(OUTFILE)).mkdir(parents=True, exist_ok=True)
    open(OUTFILE, "w", encoding="utf-8").close()  # truncate

    session = make_session()

    # 1) faculties
    facmap = fetch_faculties(session)

    # 2) all programs per level
    total_blocks = 0
    for level in DEFAULT_LEVELS:
        programs = fetch_program_codes(session, level=level, facmap=facmap)
        print(f"[info] level={level}: {len(programs)} programs")

        for planTipiKodu in DEFAULT_PLAN_TYPES:
            plan_type_label = PLAN_LABELS.get(planTipiKodu, planTipiKodu)
            print(f"\n[info] == plan type: {planTipiKodu} ({plan_type_label}) ==")

            for p in programs:
                programKodu = p["programKodu"]
                program_name = p["name"]
                faculty = p["faculty"] or "None"

                try:
                    plan_rows = list_plan_ids(session, planTipiKodu, programKodu)
                except Exception as e:
                    print(f"[warn] list_plan_ids failed for {programKodu}/{planTipiKodu}: {e}")
                    plan_rows = []

                if not plan_rows:
                    continue

                for plan_id, effective_text in plan_rows:
                    plan_header = clean_plan_header(program_name, effective_text)
                    print(f"[scrape] {faculty} | {programKodu} | {program_name}")
                    print(f"         PLAN {plan_id}: {plan_header}")

                    try:
                        sem_to_codes = parse_plan_detail(session, plan_id)
                    except Exception as e:
                        print(f"[warn] parse_plan_detail failed for {plan_id}: {e}")
                        sem_to_codes = OrderedDict()

                    total_courses = sum(len(v) for v in sem_to_codes.values())
                    print(f"         -> semesters: {len(sem_to_codes)} | courses: {total_courses}")

                    with open(OUTFILE, "a", encoding="utf-8") as f:
                        wrote_any = append_block(
                            f, faculty, plan_type_label, program_name, plan_header, sem_to_codes
                        )

                    if not wrote_any:
                        print("         [note] No course rows written for this plan (check page structure).")

                    total_blocks += 1
                    time.sleep(DELAY)

    print(f"\n[done] wrote: {OUTFILE}")
    print(f"[stats] total plan blocks written: {total_blocks}")

if __name__ == "__main__":
    main()
