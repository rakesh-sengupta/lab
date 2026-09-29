#!/usr/bin/env python3
"""
labpub — one publication list for the lab website and the CV.

    labpub add 10.1007/978-3-032-31170-2_10   add from a DOI (Crossref)
    labpub add --manual                        add by hand, for items with no DOI
    labpub orcid                               show ORCID works not yet in the list
    labpub orcid --apply                       ... and add them
    labpub cv [--out FILE]                     write the CV's publications.tex
    labpub check                               validate data/publications.json
    labpub list [--type journal]               show what is in the list
    labpub push [-m MESSAGE]                   check, commit and push; the site redeploys

Everything lives in data/publications.json. The website reads that file directly;
`labpub cv` turns it into LaTeX for main.tex. Standard library only — no pip installs.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import json
import re
import subprocess
import sys
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
CONFIG_PATH = HERE / "config.json"

TYPES = ("journal", "proceedings", "chapter", "book", "edited")
FIELD_ORDER = ["id", "type", "year", "status", "authors", "title", "venue", "book", "editors",
               "series", "volume", "issue", "pages", "publisher", "isbn", "doi", "cats",
               "flagship", "web", "cv_section", "note", "review", "source"]
EDITABLE = ["type", "year", "status", "authors", "title", "venue", "book", "editors", "series",
            "volume", "issue", "pages", "publisher", "isbn", "doi", "cats", "flagship", "web", "note"]

# CV sections: key, prefix, heading. Order here is the order in the CV.
CV_SECTIONS = [
    ("books", "B", "Authored books"),
    ("edited", "E", "Edited volumes"),
    ("press", "P", "In press, accepted and under contract"),
    ("journals", "J", "Journal articles"),
    ("chapters", "Ch", "Book chapters"),
    ("proceedings", "C", "Peer-reviewed conference proceedings"),
]


# ============================================================ config & data
def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def data_path(cfg: dict) -> Path:
    return REPO / cfg["data_file"]


def load_db(cfg: dict) -> dict:
    with open(data_path(cfg), encoding="utf-8") as f:
        return json.load(f)


def save_db(cfg: dict, db: dict) -> None:
    db["publications"] = [ordered(p) for p in db["publications"]]
    text = json.dumps(db, indent=2, ensure_ascii=False) + "\n"
    data_path(cfg).write_text(text, encoding="utf-8")


ALWAYS = {"cats": [], "flagship": False, "web": True}


def ordered(p: dict) -> dict:
    """Canonical field order, so saving never reshuffles untouched entries."""
    out = {}
    for k in FIELD_ORDER:
        if k in ALWAYS:
            v = p.get(k, ALWAYS[k])
            out[k] = bool(v) if k != "cats" else list(v or [])
        elif k in p and p[k] not in (None, "", []):
            if k == "review" and not p[k]:
                continue
            out[k] = p[k]
    for k in p:                       # never silently drop an unknown field
        if k not in out and k not in FIELD_ORDER:
            out[k] = p[k]
    return out


# ============================================================ small helpers
def norm_doi(d: str | None) -> str:
    if not d:
        return ""
    d = d.strip()
    d = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", d, flags=re.I)
    return d.lower()


def norm_title(t: str | None) -> str:
    t = (t or "").lower()
    t = re.sub(r"<[^>]+>", " ", t)            # Crossref sometimes has <i>…</i>
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def initials(given: str) -> str:
    """'Lekhana Priya' -> 'L. P.'  'Sang-Ah' -> 'S.-A.'  'B.' -> 'B.'"""
    out = []
    for part in re.split(r"\s+", (given or "").strip()):
        if not part:
            continue
        pieces = [x for x in part.split("-") if x]
        out.append("-".join(x[0].upper() + "." for x in pieces))
    return " ".join(out)


def fmt_author(given: str, family: str) -> str:
    given, family = (given or "").strip(), (family or "").strip()
    if not given:
        return family
    return f"{initials(given)} {family}".strip()


def is_me(name: str, cfg: dict) -> bool:
    return name.strip() in cfg["me"]


def make_id(p: dict, existing: set[str]) -> str:
    first = (p.get("authors") or ["anon"])[0].split()[-1]
    first = re.sub(r"[^a-z]", "", first.lower()) or "anon"
    year = str(p.get("year") or _dt.date.today().year)
    words = [w for w in norm_title(p.get("title")).split()
             if w not in {"a", "an", "the", "on", "of", "in", "and", "for", "to", "is"}]
    base = f"{first}{year}{words[0] if words else 'work'}"
    pid, n = base, 2
    while pid in existing:
        pid = f"{base}{n}"
        n += 1
    return pid


def sentence_case(title: str, cfg: dict) -> str:
    """Conservative: lower-cases Title-Cased words, keeps acronyms, mixed case,
    configured proper nouns, the first word, and the first word after a colon."""
    keep = set(cfg.get("keep_case", []))
    words = title.split(" ")
    out, cap_next = [], True
    for w in words:
        core = re.sub(r"[^\w\-']", "", w)
        if cap_next or not core:
            out.append(w)
        elif core in keep or core.upper() == core or re.search(r"[A-Z].*[A-Z]", core[1:] or ""):
            out.append(w)                       # EEG, OCOS, Go/No-Go style words
        elif core[:1].isupper() and core[1:].islower():
            out.append(w[:1].lower() + w[1:] if w[:1].isalpha() else w.replace(core, core.lower(), 1))
        else:
            out.append(w)
        cap_next = w.endswith(":") or w.endswith("?")
    return " ".join(out)


# ============================================================ HTTP
def http_json(url: str, cfg: dict) -> dict:
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": f"labpub/1.0 (mailto:{cfg.get('contact_email', 'unknown')})",
    })
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.load(r)


# ============================================================ Crossref
CR_TYPE = {
    "journal-article": "journal", "proceedings-article": "proceedings",
    "book-chapter": "chapter", "book-part": "chapter", "book-section": "chapter",
    "reference-entry": "chapter", "book": "book", "monograph": "book",
    "edited-book": "edited", "posted-content": "journal",
}


def short_publisher(name: str, cfg: dict) -> str:
    if not name:
        return ""
    for pattern, short in cfg.get("publisher_short", {}).items():
        if pattern.lower() in name.lower():
            return short
    return name


def is_series(name: str, cfg: dict) -> bool:
    return any(s.lower() in name.lower() for s in cfg.get("series_names", []))


def crossref_year(m: dict):
    for key in ("published-print", "issued", "published-online", "published", "created"):
        parts = (m.get(key) or {}).get("date-parts") or [[None]]
        if parts and parts[0] and parts[0][0]:
            return int(parts[0][0])
    return None


def short_venue(container: str, year) -> str:
    """'2024 15th Int. Conf. on … (ICCCNT)' -> 'ICCCNT 2024'."""
    m = re.search(r"\(([A-Za-z][A-Za-z0-9\-]{1,15})\)\s*$", container or "")
    if m and year:
        return f"{m.group(1)} {year}"
    return container


def from_crossref(doi: str, cfg: dict, message: dict | None = None) -> dict:
    """Build an entry from Crossref. `message` lets tests pass a recorded response."""
    if message is None:
        url = "https://api.crossref.org/works/" + urllib.parse.quote(norm_doi(doi), safe="/")
        message = http_json(url, cfg)["message"]
    m = message
    ctype = CR_TYPE.get(m.get("type", ""), "journal")
    year = crossref_year(m)
    title = " ".join((m.get("title") or [""])[0].split())
    sub = (m.get("subtitle") or [""])
    if sub and sub[0] and sub[0].lower() not in title.lower():
        title = f"{title}: {sub[0]}"
    title = re.sub(r"<[^>]+>", "", title)
    authors = [fmt_author(a.get("given", ""), a.get("family", a.get("name", "")))
               for a in m.get("author", [])]
    editors = [fmt_author(a.get("given", ""), a.get("family", a.get("name", "")))
               for a in m.get("editor", [])]
    containers = [c for c in (m.get("container-title") or []) if c]
    series = next((c for c in containers if is_series(c, cfg)), "")
    main = next((c for c in containers if not is_series(c, cfg)), "")
    pages = (m.get("page") or "").replace("--", "–").replace("-", "–")
    entry = {
        "type": ctype, "year": year, "authors": authors, "title": title,
        "volume": m.get("volume", ""), "issue": m.get("issue", ""), "pages": pages,
        "publisher": short_publisher(m.get("publisher", ""), cfg),
        "doi": norm_doi(m.get("DOI") or doi), "source": "crossref",
    }
    if ctype == "journal":
        entry["venue"] = main or series
    elif ctype == "proceedings":
        entry["venue"] = short_venue(main or series, year)
        entry["series"] = series if main else ""
    elif ctype == "chapter":
        # Springer conference series (LNNS, CCIS, LNCS…) arrive as book chapters.
        looks_like_conf = bool(series) and bool(
            m.get("event")
            or re.search(r"proceedings|conference|symposium|workshop|congress", main or "", re.I)
            or re.search(r"\b[A-Z]{3,}\b", main or ""))
        if looks_like_conf:
            entry["type"] = "proceedings"
            ev = (m.get("event") or {})
            acr = ev.get("acronym") or ""
            entry["venue"] = f"{acr} {year}".strip() if acr else short_venue(main, year)
            entry["series"] = series
        else:
            entry["book"] = main or series
            if editors:
                entry["editors"] = editors
            entry["volume"] = ""
    elif ctype in ("book", "edited"):
        if ctype == "edited" and not authors:
            entry["authors"] = editors
        isbn = (m.get("ISBN") or [""])[0]
        if isbn:
            entry["isbn"] = isbn
        entry["volume"] = ""
    return {k: v for k, v in entry.items() if v not in ("", None, [])}


# ============================================================ ORCID
OR_TYPE = {"journal-article": "journal", "conference-paper": "proceedings",
           "book-chapter": "chapter", "book": "book", "edited-book": "edited"}


def orcid_works(cfg: dict, payload: dict | None = None) -> list[dict]:
    """Return one summary dict per ORCID work group."""
    if payload is None:
        payload = http_json(f"https://pub.orcid.org/v3.0/{cfg['orcid']}/works", cfg)
    works = []
    for g in payload.get("group", []):
        summ = (g.get("work-summary") or [{}])[0]
        dois = [x.get("external-id-value", "") for x in
                (g.get("external-ids") or {}).get("external-id", [])
                if x.get("external-id-type") == "doi"]
        title = (((summ.get("title") or {}).get("title") or {}).get("value")) or ""
        pdate = summ.get("publication-date") or {}
        year = ((pdate.get("year") or {}).get("value")) if pdate else None
        works.append({
            "title": title,
            "year": int(year) if year else None,
            "type": OR_TYPE.get(summ.get("type", ""), "journal"),
            "doi": norm_doi(dois[0]) if dois else "",
            "venue": ((summ.get("journal-title") or {}) or {}).get("value", "") if summ.get("journal-title") else "",
        })
    return works


def find_match(work: dict, pubs: list[dict]):
    d = work.get("doi")
    if d:
        for p in pubs:
            if norm_doi(p.get("doi")) == d:
                return p
    t = norm_title(work.get("title"))
    if not t:
        return None
    best, score = None, 0.0
    for p in pubs:
        s = difflib.SequenceMatcher(None, t, norm_title(p.get("title"))).ratio()
        if s > score:
            best, score = p, s
    return best if score >= 0.9 else None


# ============================================================ rendering
def join_names(names: list[str], amp: str) -> str:
    if len(names) <= 1:
        return "".join(names)
    if len(names) == 2:
        return f"{names[0]} {amp} {names[1]}"
    return ", ".join(names[:-1]) + f" {amp} " + names[-1]


def cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def render_text(p: dict, cfg: dict) -> str:
    """Plain-text preview in the same shape as the website."""
    names = [cfg.get("web_name", "Rakesh Sengupta").upper() if is_me(a, cfg) else a
             for a in p.get("authors", [])]
    who = join_names(names, "&") + (" (Eds.)" if p["type"] == "edited" else "")
    when = p.get("status") or p.get("year") or ""
    bits = [f"{who} ({when}). {p.get('title', '')}."]
    t = p["type"]
    if t == "journal":
        v = p.get("venue", "")
        vol = p.get("volume", "")
        if p.get("issue"):
            vol += f"({p['issue']})"
        bits.append(", ".join(x for x in [v, vol, p.get("pages", "")] if x) + ("." if v else ""))
    elif t == "proceedings":
        s = ", ".join(x for x in [p.get("venue", ""), p.get("series", ""),
                                  f"vol. {p['volume']}" if p.get("volume") else "",
                                  p.get("publisher", ""),
                                  f"pp. {p['pages']}" if p.get("pages") else ""] if x)
        bits.append(s + ".")
    elif t == "chapter":
        if p.get("book"):
            eds = f"{join_names(p['editors'], '&')} (Ed{'s' if len(p['editors']) > 1 else ''}.), " \
                if p.get("editors") else ""
            pp = f" (pp. {p['pages']})" if p.get("pages") else ""
            bits.append(f"In {eds}{p['book']}{pp}." + (f" {p['publisher']}." if p.get("publisher") else ""))
    else:
        bits.append(", ".join(x for x in [p.get("publisher", ""),
                                          f"ISBN {p['isbn']}" if p.get("isbn") else ""] if x) + ".")
    if p.get("doi"):
        bits.append(f"doi:{p['doi']}")
    return " ".join(b for b in bits if b.strip(". "))


TEX_ESC = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
           "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\^{}"}


def tex(s) -> str:
    return "".join(TEX_ESC.get(c, c) for c in str(s or ""))


def tex_name(name: str, cfg: dict) -> str:
    if is_me(name, cfg):
        return r"\me"
    n = tex(name).replace(". ", ".~")
    return rf"\stu{{{n}}}" if name.strip() in cfg.get("students", []) else n


def render_tex(p: dict, cfg: dict) -> str:
    names = [tex_name(a, cfg) for a in p.get("authors", [])]
    who = join_names(names, r"\&") + (" (Eds.)" if p["type"] == "edited" else "")
    when = p.get("status") or p.get("year") or "n.d."
    out = [f"{who} ({when}). "]
    t, title = p["type"], tex(p.get("title", ""))
    if t in ("book", "edited"):
        out.append(rf"\emph{{{title}}}. ")
        tail = ", ".join(x for x in [tex(p.get("publisher", ""))] if x)
        if tail:
            out.append(tail + ". ")
        if p.get("isbn"):
            out.append(f"ISBN {tex(p['isbn'])}. ")
    else:
        out.append(title + ("" if title.endswith("?") else ".") + " ")
        if t == "journal" and p.get("venue"):
            vol = tex(p.get("volume", ""))
            if p.get("issue"):
                vol += f"({tex(p['issue'])})"
            parts = [rf"\emph{{{tex(p['venue'])}}}"] + [x for x in [vol, tex(p.get("pages", ""))] if x]
            out.append(", ".join(parts) + ". ")
        elif t == "proceedings":
            parts = [rf"\emph{{{tex(p.get('venue', ''))}}}" if p.get("venue") else "",
                     tex(p.get("series", "")),
                     rf"vol.~{tex(p['volume'])}" if p.get("volume") else "",
                     tex(p.get("publisher", "")),
                     rf"pp.~{tex(p['pages'])}" if p.get("pages") else ""]
            out.append(", ".join(x for x in parts if x) + ". ")
        elif t == "chapter":
            if p.get("book"):
                eds = ""
                if p.get("editors"):
                    en = join_names([tex(e).replace(". ", ".~") for e in p["editors"]], r"\&")
                    eds = f"{en} (Ed{'s' if len(p['editors']) > 1 else ''}.), "
                pp = rf" (pp.~{tex(p['pages'])})" if p.get("pages") else ""
                out.append(rf"In {eds}\emph{{{tex(p['book'])}}}{pp}. ")
                if p.get("publisher"):
                    out.append(tex(p["publisher"]) + ". ")
            else:
                out.append("Book chapter. ")
    if p.get("doi"):
        out.append(rf"\doi{{{p['doi']}}}")
    line = "".join(out).rstrip()
    return re.sub(r"\\me(?= )", r"\\me\\", line)   # \me followed by a space needs "\me\ "


def cv_section_of(p: dict) -> str:
    if p.get("cv_section"):
        return p["cv_section"]
    t = p["type"]
    if p.get("status") and t != "book":
        return "press"
    return {"book": "books", "edited": "edited", "journal": "journals",
            "chapter": "chapters", "proceedings": "proceedings"}[t]


def build_cv(db: dict, cfg: dict) -> str:
    pubs = db["publications"]
    lines = ["%% Generated by labpub from data/publications.json. Do not edit by hand:",
             "%% change the JSON (or use labpub add) and run  labpub cv  again.",
             f"%% Generated {_dt.datetime.now():%Y-%m-%d %H:%M}.", ""]
    for key, prefix, heading in CV_SECTIONS:
        items = [p for p in pubs if cv_section_of(p) == key]
        if not items:
            continue
        # newest first; stable, so the JSON order breaks ties
        items = sorted(items, key=lambda p: (1 if p.get("status") else 0, p.get("year") or 0), reverse=True)
        lines.append(rf"\cvsubsection{{{heading}}}")
        lines.append(rf"\begin{{publist}}{{{key}}}{{{prefix}}}")
        for p in items:
            wrapped = textwrap.fill(render_tex(p, cfg), width=96, initial_indent="  \\item ",
                                    subsequent_indent="    ", break_long_words=False,
                                    break_on_hyphens=False)
            lines.append(wrapped)
            if p.get("note"):
                lines.append(f"    % {p['note']}")
        lines.append(r"\end{publist}")
        lines.append("")
    return "\n".join(lines)


# ============================================================ validation
def validate(db: dict, cfg: dict) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    seen_ids, seen_dois = {}, {}
    cats_ok = set(cfg.get("categories", {}))
    for i, p in enumerate(db.get("publications", [])):
        where = p.get("id") or f"entry #{i + 1}"
        if not p.get("id"):
            errors.append(f"{where}: missing id")
        elif p["id"] in seen_ids:
            errors.append(f"{where}: duplicate id")
        seen_ids[p.get("id")] = True
        if p.get("type") not in TYPES:
            errors.append(f"{where}: type must be one of {', '.join(TYPES)}")
            continue
        if not p.get("authors"):
            errors.append(f"{where}: no authors")
        if not p.get("title"):
            errors.append(f"{where}: no title")
        if not p.get("year") and not p.get("status"):
            errors.append(f"{where}: needs a year, or a status such as 'in press'")
        if p.get("year") and not isinstance(p["year"], int):
            errors.append(f"{where}: year must be a number, e.g. 2026")
        if p["type"] == "journal" and not p.get("venue") and not p.get("status"):
            errors.append(f"{where}: journal article with no journal name")
        if p["type"] == "proceedings" and not p.get("venue"):
            errors.append(f"{where}: proceedings paper with no conference/venue")
        bad = [c for c in p.get("cats", []) if c not in cats_ok]
        if bad:
            errors.append(f"{where}: unknown categories {bad}; allowed: {sorted(cats_ok)}")
        d = norm_doi(p.get("doi"))
        if d:
            if d in seen_dois:
                errors.append(f"{where}: same DOI as {seen_dois[d]}")
            seen_dois[d] = where
        if not any(is_me(a, cfg) for a in p.get("authors", [])):
            warnings.append(f"{where}: your name is not in the author list "
                            f"(expected one of {cfg['me']})")
        if p.get("review"):
            warnings.append(f"{where}: added automatically and not yet reviewed "
                            f"(remove \"review\": true once checked)")
        if p.get("note", "").upper().startswith("TODO"):
            warnings.append(f"{where}: {p['note']}")
        if p.get("web") and not p.get("cats") and p["type"] not in ("book", "edited"):
            warnings.append(f"{where}: no website categories; it will show only under 'All'")
    return errors, warnings


# ============================================================ interaction
def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        ans = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        ans = ""
    return ans or default


def yes(prompt: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    try:
        ans = input(f"{prompt} [{d}]: ").strip().lower()
    except EOFError:
        ans = ""
    return default if not ans else ans.startswith("y")


def show(p: dict, cfg: dict) -> None:
    print("\n  Website:  " + textwrap.fill(render_text(p, cfg), 90, subsequent_indent=" " * 12))
    print("  CV:       " + textwrap.fill(render_tex(p, cfg), 90, subsequent_indent=" " * 12))
    print(f"  type={p['type']}  cats={p.get('cats', [])}  flagship={p.get('flagship', False)}"
          f"  web={p.get('web', True)}  status={p.get('status', '') or '-'}\n")


def set_field(p: dict, field: str, value: str) -> None:
    if field in ("authors", "editors"):
        p[field] = [x.strip() for x in value.split(";") if x.strip()]
    elif field == "cats":
        p[field] = [x.strip() for x in value.split(",") if x.strip()]
    elif field == "year":
        p[field] = int(value) if value.strip() else None
    elif field in ("flagship", "web"):
        p[field] = value.strip().lower() in ("y", "yes", "true", "1")
    else:
        p[field] = value.strip()


def edit_loop(p: dict, cfg: dict) -> None:
    cats = cfg.get("categories", {})
    if not p.get("cats"):
        print("Website categories: " + ", ".join(f"{k} ({v})" for k, v in cats.items()))
        p["cats"] = [c.strip() for c in ask("Categories, comma separated").split(",") if c.strip()]
    if "flagship" not in p:
        p["flagship"] = yes("Mark as flagship?", False)
    while True:
        show(p, cfg)
        field = ask("Edit a field (" + ", ".join(EDITABLE) + "), or Enter to accept")
        if not field:
            return
        if field not in EDITABLE:
            print(f"  '{field}' is not an editable field.")
            continue
        current = p.get(field, "")
        if isinstance(current, list):
            current = "; ".join(current) if field != "cats" else ", ".join(current)
        hint = "  (separate names with ;)" if field in ("authors", "editors") else ""
        set_field(p, field, ask(f"{field}{hint}", str(current)))


def manual_entry(cfg: dict) -> dict:
    p = {}
    t = ask("Type (" + "/".join(TYPES) + ")", "journal")
    p["type"] = t if t in TYPES else "journal"
    status = ask("Status if not yet published (e.g. in press, accepted), else Enter", "")
    if status:
        p["status"] = status
    y = ask("Year", "" if status else str(_dt.date.today().year))
    p["year"] = int(y) if y else None
    set_field(p, "authors", ask("Authors, as 'R. Sengupta; B. Verma'", cfg["me"][0]))
    p["title"] = ask("Title")
    if p["type"] == "journal":
        p["venue"] = ask("Journal")
        p["volume"], p["issue"], p["pages"] = ask("Volume"), ask("Issue"), ask("Pages or article number")
    elif p["type"] == "proceedings":
        p["venue"] = ask("Conference, short form (e.g. ICCCNT 2024)")
        p["series"], p["volume"] = ask("Series (e.g. Lecture Notes in Networks and Systems)"), ask("Volume")
        p["publisher"], p["pages"] = ask("Publisher"), ask("Pages")
    elif p["type"] == "chapter":
        p["book"] = ask("Book title")
        eds = ask("Editors, as 'N. Son; A. Other' (optional)")
        if eds:
            set_field(p, "editors", eds)
        p["pages"], p["publisher"] = ask("Pages"), ask("Publisher")
    else:
        p["publisher"], p["isbn"] = ask("Publisher"), ask("ISBN (optional)")
    doi = ask("DOI (optional)")
    if doi:
        p["doi"] = norm_doi(doi)
    p["web"] = yes("Show on the website?", True)
    return {k: v for k, v in p.items() if v not in ("", None, [])}


def insert(db: dict, p: dict) -> None:
    db["publications"].insert(0, p)      # newest entries lead their year


# ============================================================ commands
def cmd_add(args, cfg):
    db = load_db(cfg)
    pubs = db["publications"]
    if args.manual:
        p = manual_entry(cfg)
    else:
        if not args.doi:
            sys.exit("Give a DOI, or use --manual.")
        dup = find_match({"doi": norm_doi(args.doi), "title": ""}, pubs)
        if dup:
            sys.exit(f"Already in the list as '{dup['id']}'.")
        try:
            p = from_crossref(args.doi, cfg)
        except urllib.error.HTTPError as e:
            sys.exit(f"Crossref has no record for {args.doi} (HTTP {e.code}). Try  labpub add --manual")
        except urllib.error.URLError as e:
            sys.exit(f"Could not reach Crossref ({e.reason}). Check your connection.")
        dup = find_match({"doi": "", "title": p.get("title")}, pubs)
        if dup and not yes(f"A very similar title is already listed ('{dup['id']}'). Add anyway?", False):
            return
        if p.get("title") and yes("Convert the title to sentence case?", True):
            p["title"] = sentence_case(p["title"], cfg)
    p.setdefault("web", True)
    edit_loop(p, cfg)
    p["id"] = make_id(p, {x["id"] for x in pubs})
    insert(db, p)
    errors, _ = validate(db, cfg)
    mine = [e for e in errors if e.startswith(p["id"] + ":")]
    if mine:
        print("Not saved — please fix:\n  " + "\n  ".join(mine))
        return
    save_db(cfg, db)
    print(f"Added '{p['id']}'. Next:  labpub cv   and   labpub push")


def cmd_orcid(args, cfg):
    db = load_db(cfg)
    pubs = db["publications"]
    try:
        works = orcid_works(cfg)
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        sys.exit(f"Could not reach ORCID: {e}")
    new = [w for w in works if not find_match(w, pubs)]
    report = ["The weekly ORCID check found works on "
              f"[ORCID {cfg['orcid']}](https://orcid.org/{cfg['orcid']}) that are not on the site yet.",
              "", "**Before merging:** check authors, title case, venue and the website categories. "
              "Each entry below is flagged `\"review\": true` in `data/publications.json`; "
              "remove that line once it is right.", ""]
    if not new:
        print("Nothing new: every ORCID work is already in the list.")
        return
    print(f"{len(new)} ORCID work(s) not in the list:")
    for w in new:
        print(f"  - {w.get('year') or '????'}  {w['title'][:80]}  {('doi:' + w['doi']) if w['doi'] else '(no DOI)'}")
    if not args.apply:
        print("\nRun  labpub orcid --apply  to add them.")
        return
    added = []
    for w in new:
        if not args.yes and not yes(f"\nAdd '{w['title'][:70]}'?", True):
            continue
        p, why = None, ""
        if w.get("doi"):
            try:
                p = from_crossref(w["doi"], cfg)
            except Exception as e:           # noqa: BLE001 — fall back to ORCID summary
                why = f"Crossref lookup failed ({e.__class__.__name__}); built from ORCID summary"
        if p is None:
            p = {"type": w["type"], "year": w["year"], "title": w["title"],
                 "authors": [cfg["me"][0]], "doi": w.get("doi", "")}
            if w.get("venue"):
                p["venue"] = w["venue"]
            why = why or "no DOI; built from ORCID summary"
            p["note"] = "TODO: authors taken as you alone; add co-authors"
        p["source"] = "orcid"
        p["review"] = True
        p.setdefault("web", True)
        if not args.yes:
            edit_loop(p, cfg)
            p.pop("review", None)
        p["id"] = make_id(p, {x["id"] for x in db["publications"]})
        insert(db, p)
        added.append(p)
        report.append(f"- **{p['id']}** — {render_text(p, cfg)}" + (f"  \n  _{why}_" if why else ""))
    if not added:
        print("Nothing added.")
        return
    save_db(cfg, db)
    print(f"\nAdded {len(added)} entr{'y' if len(added) == 1 else 'ies'}.")
    if args.report:
        Path(args.report).write_text("\n".join(report) + "\n", encoding="utf-8")
        print(f"Report written to {args.report}")


def cmd_cv(args, cfg):
    db = load_db(cfg)
    out = args.out or cfg.get("cv_publications_tex") or "publications.tex"
    out = Path(out).expanduser()
    if not out.is_absolute() and not args.out:
        out = (REPO / out) if cfg.get("cv_publications_tex") else Path.cwd() / out
    out.write_text(build_cv(db, cfg), encoding="utf-8")
    counts = {}
    for p in db["publications"]:
        counts[cv_section_of(p)] = counts.get(cv_section_of(p), 0) + 1
    print(f"Wrote {out}")
    print("  " + "  ".join(f"{k}: {counts.get(k, 0)}" for k, _, _ in CV_SECTIONS))


def cmd_check(args, cfg):
    db = load_db(cfg)
    errors, warnings = validate(db, cfg)
    for w in warnings:
        print("  note:  " + w)
    for e in errors:
        print("  ERROR: " + e)
    n = len(db["publications"])
    if errors:
        print(f"{len(errors)} error(s) in {n} entries.")
        return 1
    print(f"OK — {n} entries, {len(warnings)} note(s).")
    return 0


def cmd_list(args, cfg):
    db = load_db(cfg)
    for p in db["publications"]:
        if args.type and p["type"] != args.type:
            continue
        when = str(p.get("year") or "") if not p.get("status") else p["status"][:10]
        flag = "*" if p.get("flagship") else " "
        web = " " if p.get("web", True) else "(hidden)"
        print(f"{flag} {when:<10} {p['type']:<11} {p['id']:<30} {p.get('title', '')[:58]} {web}")


def git(*a, check=True, capture=False):
    return subprocess.run(["git", "-C", str(REPO), *a], check=check, text=True,
                          capture_output=capture)


def cmd_push(args, cfg):
    if cmd_check(args, cfg):
        sys.exit("Fix the errors above before pushing.")
    rel = cfg["data_file"]
    diff = git("diff", "--quiet", "--", rel, check=False)
    staged = git("diff", "--cached", "--quiet", "--", rel, check=False)
    if diff.returncode == 0 and staged.returncode == 0:
        print("No changes to publications.json — nothing to push.")
        return
    git("add", "--", rel)
    msg = args.message
    if not msg:
        new_ids = []
        out = git("diff", "--cached", "-U0", "--", rel, capture=True).stdout
        for line in out.splitlines():
            m = re.match(r'^\+\s+"id": "([^"]+)"', line)
            if m:
                new_ids.append(m.group(1))
        msg = "labpub: add " + ", ".join(new_ids) if new_ids else "labpub: update publications"
    git("commit", "-m", msg)
    if args.no_push:
        print("Committed (not pushed, --no-push).")
        return
    git("push")
    print("Pushed. The site redeploys in about a minute.")


# ============================================================ main
def main(argv=None):
    ap = argparse.ArgumentParser(prog="labpub", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="add a publication from a DOI or by hand")
    a.add_argument("doi", nargs="?")
    a.add_argument("--manual", action="store_true", help="enter the details yourself")

    o = sub.add_parser("orcid", help="find ORCID works missing from the list")
    o.add_argument("--apply", action="store_true", help="add what is missing")
    o.add_argument("--yes", action="store_true", help="no questions (used by the weekly check)")
    o.add_argument("--report", help="write a Markdown summary here")

    c = sub.add_parser("cv", help="write publications.tex for the CV")
    c.add_argument("--out", help="output file (default from config.json)")

    sub.add_parser("check", help="validate the publication list")

    l = sub.add_parser("list", help="show the publication list")
    l.add_argument("--type", choices=TYPES)

    p = sub.add_parser("push", help="check, commit and push")
    p.add_argument("-m", "--message")
    p.add_argument("--no-push", action="store_true", help="commit only")

    args = ap.parse_args(argv)
    cfg = load_config()
    rc = {"add": cmd_add, "orcid": cmd_orcid, "cv": cmd_cv, "check": cmd_check,
          "list": cmd_list, "push": cmd_push}[args.cmd](args, cfg)
    return rc or 0


if __name__ == "__main__":
    try:                                   # quiet exit when piped into `head`
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (AttributeError, ValueError):
        pass
    sys.exit(main())
