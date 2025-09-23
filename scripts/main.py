#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import pathlib
import requests
from bs4 import BeautifulSoup
from collections import OrderedDict
from typing import List, Dict, Tuple

BASE = "https://obs.itu.edu.tr"

# ------------------------- configuration -------------------------
OUTFILE = os.path.join("..", "data", "itu_all_plans.txt")

DEFAULT_PLAN_TYPES = [
    "lisans", "cap", "yandal", "uolp", "ddp",
    "yuksek-lisans", "yuksek-lisans-ikinci-ogretim", "doktora",
]
DEFAULT_LEVELS = [2]  # only Lisans level
DELAY = 0.40          # polite delay between requests
TIMEOUT = 30
RETRIES = 3

PLAN_LABELS = {
    "lisans": "Lisans",
    "cap": "ÇAP",
    "yandal": "Yandal",
    "uolp": "UOLP/DDP",
    "ddp":  "UOLP/DDP",
    "yuksek-lisans": "Yüksek Lisans",
    "yuksek-lisans-ikinci-ogretim": "Yüksek Lisans (İÖ)",
    "doktora": "Doktora",
}

PROGRAM_CODE_RE = re.compile(r"^[A-ZÇĞİÖŞÜ0-9_]+$", re.UNICODE)
DETAIL_HREF_RE = re.compile(r"/public/DersPlan/DersPlanDetay/(\d+)")
SEM_HEADER_RE  = re.compile(r"(^\d+\.\s*Yarıyıl)|(^\d+\s*Semester)|Yarıyıl|Semester", re.I|re.U)

# ---------------------------- HTTP helpers ----------------------------

def make_session(timeout: int = TIMEOUT, retries: int = RETRIES, backoff: float = 0.8) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "ITU-CoursePlan-Scraper/1.0"})
    s.request_timeout = timeout
    s.request_retries = retries
    s.request_backoff = backoff
    return s

def get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    timeout = kwargs.pop("timeout", getattr(session, "request_timeout", TIMEOUT))
    retries = getattr(session, "request_retries", RETRIES)
    backoff = getattr(session, "request_backoff", 0.8)
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            r = session.get(url, timeout=timeout, **kwargs)
            r.raise_for_status()
            return r
        except Exception as e:
            last_exc = e
            if attempt < retries:
                time.sleep(backoff * attempt)
            else:
                raise
    raise last_exc

# ------------------------- scraping primitives ------------------------

def _nearest_faculty_for_tr(tr) -> str:
    hdr = tr.find_previous(["h1","h2","h3","h4","h5","h6"])
    if hdr:
        txt = hdr.get_text(strip=True)
        if txt and not txt.lower().startswith("program kodları"):
            return txt
    hdr2 = tr.find_previous(class_=re.compile(r"(card-header|accordion-header|header)", re.I))
    if hdr2:
        txt = hdr2.get_text(strip=True)
        if txt and not txt.lower().startswith("program kodları"):
            return txt
    return None

def fetch_program_codes(session: requests.Session, level: int = 2) -> List[Dict[str, str]]:
    """
    Return list of {faculty, name, programKodu} for a given level (2 = Lisans).
    Ignores generic 'Program Kodları' heading.
    """
    url = f"{BASE}/public/GenelTanimlamalar/ProgramKodlariList?programSeviyeTipiId={level}"
    r = get(session, url)
    soup = BeautifulSoup(r.text, "html.parser")

    programs = []
    current_faculty = None
    for el in soup.select("h1,h2,h3,h4,h5,h6, table tbody tr"):
        if el.name in {"h1","h2","h3","h4","h5","h6"}:
            text = el.get_text(strip=True)
            if text and not text.lower().startswith("program kodları"):
                current_faculty = text
        elif el.name == "tr":
            tds = [td.get_text(strip=True) for td in el.select("td")]
            if len(tds) >= 2 and PROGRAM_CODE_RE.match(tds[0]):
                fac = current_faculty or _nearest_faculty_for_tr(el) or "None"
                programs.append({"faculty": fac, "name": tds[1], "programKodu": tds[0]})
    return programs

def list_plan_ids(session: requests.Session, planTipiKodu: str, programKodu: str) -> List[Tuple[str, str]]:
    """
    For a (planTipiKodu, programKodu) pair, returns: list of (plan_id, effective_text).
    """
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

def _normalize_code(code: str) -> str:
    # Remove spaces, keep diacritics & trailing letters (e.g., FIZ 101E -> FIZ101E)
    return re.sub(r"\s+", "", code)

def _semester_sort_key(title: str) -> Tuple[int, str]:
    # Try to sort "1. Yarıyıl", "2. Yarıyıl", "3 Semester", etc. numerically
    m = re.search(r"(\d+)", title)
    return (int(m.group(1)) if m else 999, title)

def parse_plan_detail(session: requests.Session, plan_id: str) -> OrderedDict:
    """
    Parse /DersPlanDetay/<id> and return OrderedDict:
        { semester_title -> [course_codes_without_spaces, ...] }
    Robust to minor DOM differences.
    """
    url = f"{BASE}/public/DersPlan/DersPlanDetay/{plan_id}"
    r = get(session, url)
    soup = BeautifulSoup(r.text, "html.parser")

    # Primary: find headers that look like semester titles, then the next <table>
    candidates = []
    for header in soup.select("h4, h5, h6, strong"):
        htxt = header.get_text(" ", strip=True)
        if htxt and SEM_HEADER_RE.search(htxt):
            tbl = header.find_next("table")
            if tbl:
                candidates.append((htxt, tbl))

    semesters = OrderedDict()
    if candidates:
        # Sort by semester number if present
        candidates.sort(key=lambda x: _semester_sort_key(x[0]))
        for title, tbl in candidates:
            codes = []
            for tr in tbl.select("tbody tr"):
                tds = tr.select("td")
                if not tds:
                    continue
                code_raw = tds[0].get_text(strip=True)
                if not code_raw:
                    continue
                codes.append(_normalize_code(code_raw))
            semesters[title] = codes

    # Fallback: if nothing matched, try to grab all tables with plausible course rows
    if not semesters:
        for idx, tbl in enumerate(soup.select("table")):
            codes = []
            tds0 = tbl.select("tbody tr td:first-child")
            # Heuristic: if first column cells look like course codes, accept table
            good = 0
            for td in tds0:
                text = td.get_text(strip=True)
                if re.search(r"[A-ZÇĞİÖŞÜ]{2,}\s*\d{3,}[A-Z]?", text):
                    good += 1
            if good >= 3:  # at least a few course-like codes in the first column
                for tr in tbl.select("tbody tr"):
                    tds = tr.select("td")
                    if not tds:
                        continue
                    code_raw = tds[0].get_text(strip=True)
                    if not code_raw:
                        continue
                    codes.append(_normalize_code(code_raw))
                semesters[f"Semester {idx+1}"] = codes

    return semesters

# --------------------------- output writer ----------------------------

def clean_plan_header(program_name: str, effective_text: str) -> str:
    """
    Remove 'Detay ' prefix and build a clean PLAN line.
    """
    text = effective_text.strip()
    if text.lower().startswith("detay"):
        text = text[5:].strip()
    if program_name in text:
        return text
    return f"{program_name} {text}"

def append_block_with_semesters(f, faculty: str, plan_type_label: str, program_name: str,
                                plan_header: str, sem_to_codes: OrderedDict) -> None:
    """
    Writes:
      FACULTY
      <faculty>
      TYPE
      <plan type>
      MAJOR
      <program name>
      PLAN
      <plan header>
      <line per semester: CODE1;CODE2;...>
    """
    f.write("FACULTY\n")
    f.write(f"{faculty}\n")
    f.write("TYPE\n")
    f.write(f"{plan_type_label}\n")
    f.write("MAJOR\n")
    f.write(f"{program_name}\n")
    f.write("PLAN\n")
    f.write(f"{plan_header}\n")
    for _, codes in sem_to_codes.items():
        if codes:
            f.write(";".join(codes) + "\n")

# ------------------------------- main --------------------------------

def main():
    pathlib.Path(os.path.dirname(OUTFILE)).mkdir(parents=True, exist_ok=True)
    # truncate each run
    with open(OUTFILE, "w", encoding="utf-8"):
        pass

    session = make_session()

    total_programs = 0
    total_plans = 0

    for level in DEFAULT_LEVELS:
        programs = fetch_program_codes(session, level=level)
        total_programs += len(programs)

        for planTipiKodu in DEFAULT_PLAN_TYPES:
            plan_type_label = PLAN_LABELS.get(planTipiKodu, planTipiKodu)

            for p in programs:
                programKodu = p["programKodu"]
                program_name = p["name"]
                faculty = p["faculty"] or "None"

                # Find all plans (rows) for this (plan type, program)
                try:
                    plan_rows = list_plan_ids(session, planTipiKodu, programKodu)
                except Exception:
                    plan_rows = []

                if not plan_rows:
                    continue

                for plan_id, effective_text in plan_rows:
                    plan_header = clean_plan_header(program_name, effective_text)

                    # Parse semesters & codes
                    try:
                        sem_to_codes = parse_plan_detail(session, plan_id)
                    except Exception:
                        sem_to_codes = OrderedDict()

                    with open(OUTFILE, "a", encoding="utf-8") as f:
                        append_block_with_semesters(
                            f, faculty, plan_type_label, program_name, plan_header, sem_to_codes
                        )
                    total_plans += 1
                    time.sleep(DELAY)

    print(f"[done] wrote: {OUTFILE}")
    print(f"[stats] programs: {total_programs}, plans written: {total_plans}")

if __name__ == "__main__":
    main()
