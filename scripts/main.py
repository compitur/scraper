#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import argparse
import pathlib
from collections import OrderedDict
from typing import Dict, List, Tuple, Iterable

import requests
from bs4 import BeautifulSoup

BASE = "https://obs.itu.edu.tr"

# ---------------------------- HTTP helpers ----------------------------

def make_session(timeout: int = 30, retries: int = 3, backoff: float = 0.8) -> requests.Session:
    """
    Simple retry wrapper for GET requests. Keeps it dependency-free (no urllib3 Retry).
    """
    s = requests.Session()
    s.headers.update({
        "User-Agent": "ITU-CoursePlan-Scraper/1.0 (+requests; +BeautifulSoup)"
    })
    s.request_timeout = timeout  # custom attribute for convenience
    s.request_retries = retries
    s.request_backoff = backoff
    return s

def get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    """
    GET with naive retries.
    """
    timeout = kwargs.pop("timeout", getattr(session, "request_timeout", 30))
    retries = getattr(session, "request_retries", 3)
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
    raise last_exc  # pragma: no cover

# ------------------------- scraping primitives ------------------------

PROGRAM_CODE_RE = re.compile(r"^[A-ZÇĞİÖŞÜ0-9_]+$")  # e.g., BLGE_LS
DETAIL_HREF_RE = re.compile(r"/public/DersPlan/DersPlanDetay/(\d+)")

def fetch_program_codes(session: requests.Session, level: int = 2) -> List[Dict[str, str]]:
    """
    Returns: list of dicts {faculty, name, programKodu} for a given level (2 = Lisans).
    """
    url = f"{BASE}/public/GenelTanimlamalar/ProgramKodlariList?programSeviyeTipiId={level}"
    r = get(session, url)
    soup = BeautifulSoup(r.text, "html.parser")

    rows = []
    current_faculty = None
    # page layout: <h5>Faculty</h5> then a table of programs
    for el in soup.select("h5, table tbody tr"):
        if el.name == "h5":
            current_faculty = el.get_text(strip=True)
        elif el.name == "tr":
            tds = [td.get_text(strip=True) for td in el.select("td")]
            if len(tds) >= 2 and PROGRAM_CODE_RE.match(tds[0]):
                rows.append({
                    "faculty": current_faculty,
                    "name": tds[1],
                    "programKodu": tds[0],
                })
    return rows

def list_plan_ids(session: requests.Session, planTipiKodu: str, programKodu: str) -> List[Tuple[str, str]]:
    """
    For a (planTipiKodu, programKodu) pair, returns: list of (plan_id, effective_text).
    effective_text is the full row text; we later format it into the PLAN header line.
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
        plan_id = m.group(1)
        effective_text = " ".join(row.get_text(" ", strip=True).split())
        out.append((plan_id, effective_text))
    return out

def parse_plan_detail(session: requests.Session, plan_id: str) -> OrderedDict:
    """
    Parses a plan detail page and returns:
      OrderedDict { semester_title -> [course_code, ...] }
    Codes are normalized by removing spaces (e.g., "FIZ 101E" -> "FIZ101E").
    """
    url = f"{BASE}/public/DersPlan/DersPlanDetay/{plan_id}"
    r = get(session, url)
    soup = BeautifulSoup(r.text, "html.parser")

    semesters = OrderedDict()
    for header in soup.select("h4, h5"):
        htxt = header.get_text(strip=True)
        if ("Yarıyıl" in htxt) or ("Semester" in htxt):
            table = header.find_next("table")
            if not table:
                continue
            codes = []
            for tr in table.select("tbody tr"):
                tds = tr.select("td")
                if not tds:
                    continue
                code_raw = tds[0].get_text(strip=True)
                if not code_raw:
                    continue
                code = re.sub(r"\s+", "", code_raw)
                codes.append(code)
            semesters[htxt] = codes
    return semesters

# --------------------------- output writers ---------------------------

def sanitize_filename(s: str) -> str:
    s = s.strip().replace(" ", "_").replace("/", "-")
    s = re.sub(r"[^\w\-\.\(\)çğıöşüÇĞİÖŞÜ]", "", s, flags=re.UNICODE)
    return s

def write_program_txt(path: str, faculty: str, plan_type_label: str, program_name: str,
                      plans_semesters: Iterable[Tuple[str, OrderedDict]]) -> None:
    """
    Writes the TXT in the exact format requested.
    plans_semesters: iterable of (plan_header_text, OrderedDict{semester -> [codes...]})
    """
    with open(path, "w", encoding="utf-8") as f:
        f.write("FACULTY\n")
        f.write(f"{faculty}\n")
        f.write("TYPE\n")
        f.write(f"{plan_type_label}\n")
        f.write("MAJOR\n")
        f.write(f"{program_name}\n")
        for plan_header, sem_dict in plans_semesters:
            f.write("PLAN\n")
            f.write(f"{plan_header}\n")
            for _, codes in sem_dict.items():
                if codes:
                    f.write(";".join(codes) + "\n")

# ------------------------------ driver -------------------------------

def scrape_one_program(
    session: requests.Session,
    programKodu: str,
    planTipiKodu: str,
    level_for_metadata: int,
    outdir: str,
    delay: float
) -> str:
    """
    Scrapes *all* plan versions for a single program and writes a single TXT file.
    Returns the output file path.
    """
    programs = fetch_program_codes(session, level=level_for_metadata)
    meta = next((p for p in programs if p["programKodu"] == programKodu), None)
    if not meta:
        raise RuntimeError(f"Program code '{programKodu}' not found at level={level_for_metadata}.")

    plan_ids = list_plan_ids(session, planTipiKodu, programKodu)
    if not plan_ids:
        raise RuntimeError(f"No plans found for program={programKodu}, planTipiKodu={planTipiKodu}.")

    plans_semesters = []
    for pid, effective_text in plan_ids:
        sem_dict = parse_plan_detail(session, pid)
        # Make the PLAN header line. If effective already includes program name, use as-is.
        if meta["name"] in effective_text:
            plan_header = effective_text
        else:
            # Example style: "<Program Name> <effective_text>"
            plan_header = f"{meta['name']} {effective_text}"
        plans_semesters.append((plan_header, sem_dict))
        time.sleep(delay)

    plan_type_label = "Lisans" if planTipiKodu == "lisans" else planTipiKodu
    fname = sanitize_filename(f"{programKodu}_{planTipiKodu}.txt")
    outpath = os.path.join(outdir, fname)
    write_program_txt(outpath, meta["faculty"], plan_type_label, meta["name"], plans_semesters)
    return outpath

def main():
    parser = argparse.ArgumentParser(
        description="Scrape ITU OBS DersPlan and write TXT files grouped by program."
    )
    parser.add_argument(
        "--plan", "-p",
        default="lisans",
        help="Plan tipi kodu (e.g., lisans, cap, yandal, yuksek-lisans, doktora, ...). Default: lisans"
    )
    parser.add_argument(
        "--level", "-l",
        type=int,
        default=2,
        help="ProgramSeviyeTipiId for ProgramKodlariList (2=Lisans). Used only to fetch program metadata. Default: 2"
    )
    parser.add_argument(
        "--programs", "-k",
        nargs="*",
        help="One or more programKodu values (e.g., BLGE_LS MIME_LS). If omitted, processes *all* programs at --level."
    )
    parser.add_argument(
        "--outdir", "-o",
        default="itu_txt",
        help="Output directory for TXT files. Default: itu_txt"
    )
    parser.add_argument(
        "--delay", "-d",
        type=float,
        default=0.6,
        help="Seconds to sleep between plan detail requests. Default: 0.6"
    )
    parser.add_argument(
        "--timeout", "-t",
        type=int,
        default=30,
        help="Request timeout seconds. Default: 30"
    )
    parser.add_argument(
        "--retries", "-r",
        type=int,
        default=3,
        help="Number of HTTP retries per request. Default: 3"
    )
    args = parser.parse_args()

    # Prep I/O
    pathlib.Path(args.outdir).mkdir(parents=True, exist_ok=True)

    # HTTP session
    session = make_session(timeout=args.timeout, retries=args.retries)

    # Which programs to process?
    if args.programs and len(args.programs) > 0:
        target_programs = args.programs
        # For speed, fetch metadata just once (same level)
        meta_list = fetch_program_codes(session, level=args.level)
        known_codes = {p["programKodu"] for p in meta_list}
        missing = [c for c in target_programs if c not in known_codes]
        if missing:
            print(f"[warn] Some programKodu not found at level={args.level}: {missing}. "
                  f"They may belong to another level or plan type.")
    else:
        # no explicit list given -> process all programs at the provided level
        meta_list = fetch_program_codes(session, level=args.level)
        target_programs = [p["programKodu"] for p in meta_list]
        print(f"[info] Found {len(target_programs)} programs at level={args.level}; processing all.")

    # Run
    ok, fail = 0, 0
    for programKodu in target_programs:
        try:
            outpath = scrape_one_program(
                session=session,
                programKodu=programKodu,
                planTipiKodu=args.plan,
                level_for_metadata=args.level,
                outdir=args.outdir,
                delay=args.delay
            )
            ok += 1
            print(f"[ok] {programKodu} -> {outpath}")
        except Exception as e:
            fail += 1
            print(f"[fail] {programKodu}: {e}")

    print(f"\nDone. Success: {ok}, Failures: {fail}, Output dir: {args.outdir}")

if __name__ == "__main__":
    main()
