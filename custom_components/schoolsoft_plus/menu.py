"""School lunch menu linked from SchoolSoft's Skolinformation page.

The school publishes the weekly menu as a link (often a Google Drive file)
under a "MATSEDEL" heading. The file may be a PDF, which is parsed as text,
or an image, which can be read by a Home Assistant AI Task entity.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import datetime as dt
import hashlib
import io
import logging
import re
from typing import Any

from bs4 import BeautifulSoup
import httpx

_LOGGER = logging.getLogger(__name__)

SCHOOL_INFO_PATH = "jsp/student/right_student_school.jsp"
MAX_MENU_BYTES = 20 * 1024 * 1024

WEEKDAYS = ("mandag", "tisdag", "onsdag", "torsdag", "fredag")
WEEKDAY_LABELS = ("Måndag", "Tisdag", "Onsdag", "Torsdag", "Fredag")
# Weekday heading, allowing OCR noise before it (e.g. "—-—D TORSDAG —").
_DAY_RE = re.compile(
    r"^[^A-Za-zÅÄÖåäö\n]*(?:[A-Za-zÅÄÖåäö]{1,2}\s+)?(m[åa]ndag|tisdag|onsdag|torsdag|fredag)\b[:\s]*",
    re.I | re.M,
)
_WEEK_RE = re.compile(r"\b(?:v\.?|vecka|veckan?)\s*(\d{1,2})\b", re.I)
_VEG_RE = re.compile(r"^\s*(?:dagens\s+)?(?:vegetariskt?|veg)\s*[:\-]\s*", re.I)
_MAIN_RE = re.compile(r"^\s*dagens\s+r[äa]tt\s*[:\-]\s*", re.I)
# Bump when text parsing changes so cached PDF/OCR results are parsed again.
PARSER_VERSION = 2
_DRIVE_RE = re.compile(r"drive\.google\.com/(?:file/d/|open\?id=|uc\?(?:[^#]*&)?id=)([\w-]+)")


@dataclass(slots=True)
class MenuDay:
    main: str
    description: str = ""
    vegetarian: str = ""

    def as_lunch(self) -> str:
        return f"{self.main} | Veg: {self.vegetarian}" if self.vegetarian else self.main


@dataclass(slots=True)
class SchoolMenu:
    """The latest downloaded menu file and what was parsed from it."""

    link: str
    label: str
    content: bytes
    content_type: str
    filename: str
    digest: str
    fetched: dt.datetime
    week: int | None = None
    days: dict[dt.date, MenuDay] = field(default_factory=dict)
    parse_status: str = "ej tolkad"
    ocr_text: str = ""

    @property
    def is_image(self) -> bool:
        return self.content_type.startswith("image/")

    @property
    def is_pdf(self) -> bool:
        return self.content[:4] == b"%PDF"


_MENU_RE = re.compile(r"matsedel|veckans meny|lunchmeny", re.I)
_HTTP_RE = re.compile(r"^https?://", re.I)


def find_menu_links(html: str) -> list[tuple[str, str]]:
    """Return (url, label) for each menu link under a 'matsedel' heading.

    Schools may list several weeks at once (e.g. last week and next week),
    each as "Här hittar du matsedel för v. 45". The label is "v. <week>" when
    the text next to the link names a week, otherwise "".
    """
    soup = BeautifulSoup(html, "lxml")
    marker = soup.find(string=_MENU_RE)
    if marker is None:
        return []
    first = marker.find_next("a", href=_HTTP_RE)
    if first is None:
        return []
    container = marker.find_parent(class_="formtable")
    anchors = container.find_all("a", href=_HTTP_RE) if container else []
    found: list[tuple[str, str]] = []
    for link in anchors or [first]:
        # Text around the link, e.g. "Här hittar du matsedel för v. 43".
        parent = link.parent
        context = parent.get_text(" ", strip=True) if parent is not None and parent is not container else ""
        if not context:
            context = " ".join(s.strip() for s in link.find_all_next(string=True, limit=4) if s.strip())
        if link is first:
            context = f"{marker} {context}"
        match = _WEEK_RE.search(context)
        if match or link is first or _MENU_RE.search(context):
            found.append((link["href"], f"v. {match.group(1)}" if match else ""))
    return found


def choose_menu_link(links: list[tuple[str, str]], today: dt.date) -> tuple[str, str] | None:
    """Pick the menu for the week of the next school day (today, or Monday on weekends).

    If that week isn't listed, take the latest week; links without a week
    number are only used when no link has one.
    """
    if not links:
        return None
    school_day = today + dt.timedelta(days=7 - today.weekday() if today.weekday() >= 5 else 0)
    target = school_day - dt.timedelta(days=school_day.weekday())
    dated = []
    for link, label in links:
        match = _WEEK_RE.search(label)
        if match and 1 <= int(match.group(1)) <= 53:
            dated.append((week_monday(int(match.group(1)), today), (link, label)))
    if not dated:
        return links[0]
    for monday, item in dated:
        if monday == target:
            return item
    return max(dated, key=lambda d: d[0])[1]


def upcoming_menu_links(links: list[tuple[str, str]], today: dt.date) -> list[tuple[str, str]]:
    """Links for this week and later weeks, other than the one choose_menu_link picks."""
    this_monday = today - dt.timedelta(days=today.weekday())
    chosen = choose_menu_link(links, today)
    extra = []
    for link, label in links:
        match = _WEEK_RE.search(label)
        if (link, label) == chosen or not match or not 1 <= int(match.group(1)) <= 53:
            continue
        if week_monday(int(match.group(1)), today) >= this_monday:
            extra.append((link, label))
    return extra


def download_url(link: str) -> str:
    """Direct download URL for share links (Google Drive)."""
    match = _DRIVE_RE.search(link)
    if match:
        return f"https://drive.google.com/uc?export=download&id={match.group(1)}"
    return link


def _filename(headers: httpx.Headers, fallback: str) -> str:
    disp = headers.get("content-disposition", "")
    match = re.search(r"filename\*=UTF-8''([^;]+)", disp) or re.search(r'filename="?([^";]+)', disp)
    return match.group(1) if match else fallback


async def async_download(client: httpx.AsyncClient, link: str, label: str) -> SchoolMenu:
    url = download_url(link)
    resp = await client.get(url, follow_redirects=True, timeout=60)
    resp.raise_for_status()
    content = resp.content
    if len(content) > MAX_MENU_BYTES:
        raise ValueError(f"Menu file too large ({len(content)} bytes)")
    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    if content[:4] == b"%PDF":
        ctype = "application/pdf"
    elif content[:8] == b"\x89PNG\r\n\x1a\n":
        ctype = "image/png"
    elif content[:3] == b"\xff\xd8\xff":
        ctype = "image/jpeg"
    elif ctype.startswith("text/html"):
        raise ValueError("Menu link returned a web page, not a file (is it shared publicly?)")
    filename = _filename(resp.headers, url.rsplit("/", 1)[-1])
    if not label:
        match = _WEEK_RE.search(filename)
        label = f"v. {match.group(1)}" if match else ""
    return SchoolMenu(
        link=link,
        label=label,
        content=content,
        content_type=ctype or "application/octet-stream",
        filename=filename,
        digest=hashlib.sha256(content).hexdigest(),
        fetched=dt.datetime.now(dt.UTC),
    )


def week_monday(week: int, today: dt.date) -> dt.date:
    """Monday of ISO week ``week`` in the year that puts it closest to today."""
    candidates = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            candidates.append(dt.date.fromisocalendar(year, week, 1))
        except ValueError:
            continue
    return min(candidates, key=lambda d: abs((d - today).days))


def week_of(menu: SchoolMenu) -> int | None:
    for text in (menu.label, menu.filename):
        match = _WEEK_RE.search(text or "")
        if match:
            return int(match.group(1))
    return None


# -- PDF -----------------------------------------------------------------------


def _pdf_text(content: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def parse_menu_text(text: str) -> tuple[int | None, dict[int, MenuDay]]:
    """Split a text menu on weekday headings. Returns (week, weekday index -> day)."""
    week_match = _WEEK_RE.search(text)
    week = int(week_match.group(1)) if week_match else None
    parts = _DAY_RE.split(text)
    days: dict[int, MenuDay] = {}
    # parts = [preamble, day1, body1, day2, body2, ...]
    for name, body in zip(parts[1::2], parts[2::2], strict=False):
        idx = WEEKDAYS.index(name.lower().replace("å", "a"))
        # Skip lines without a real word (dashes and other heading decoration).
        lines = [ln.strip() for ln in body.splitlines() if re.search(r"[A-Za-zÅÄÖåäö]{2,}", ln)]
        if not lines:
            continue
        main = veg = ""
        rest: list[str] = []
        current = None  # "main" or "veg": the labelled dish a wrapped line continues
        labelled = False  # "Dagens rätt:" seen; text after the dishes is then noise
        for i, ln in enumerate(lines):
            if _VEG_RE.match(ln):
                veg, current = _VEG_RE.sub("", ln), "veg"
            elif _MAIN_RE.match(ln):
                main, current, labelled = _MAIN_RE.sub("", ln), "main", True
            elif current == "main" and ln[:1].islower():
                main += " " + ln
            elif current == "veg" and (ln[:1].islower() or not labelled):
                veg += " " + ln
            elif current == "veg" and labelled:
                continue
            elif i == 0:
                main = ln
            else:
                rest.append(ln)
        days[idx] = MenuDay(main=main, description=" ".join(rest), vegetarian=veg)
    return week, days


async def async_parse_pdf(menu: SchoolMenu) -> tuple[int | None, dict[int, MenuDay]]:
    text = await asyncio.get_running_loop().run_in_executor(None, _pdf_text, menu.content)
    return parse_menu_text(text)


# -- OCR (images) ------------------------------------------------------------------

TESSDATA_URL = "https://github.com/tesseract-ocr/tessdata_best/raw/main/{lang}.traineddata"
OCR_LANG = "swe"


async def async_ensure_tessdata(client: httpx.AsyncClient, directory: str) -> str:
    """Download the Swedish Tesseract model once. Returns the tessdata dir."""
    import os

    path = os.path.join(directory, f"{OCR_LANG}.traineddata")
    loop = asyncio.get_running_loop()
    if await loop.run_in_executor(None, os.path.exists, path):
        return directory
    resp = await client.get(TESSDATA_URL.format(lang=OCR_LANG), follow_redirects=True, timeout=120)
    resp.raise_for_status()
    if len(resp.content) < 1_000_000:
        raise ValueError("Tesseract language model download looks truncated")

    def _write() -> None:
        os.makedirs(directory, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(resp.content)
        os.replace(tmp, path)

    await loop.run_in_executor(None, _write)
    return directory


def _ocr(content: bytes, tessdata: str) -> str:
    from PIL import Image, ImageOps
    import tesserocr

    image = Image.open(io.BytesIO(content))
    image = ImageOps.grayscale(image.convert("RGB"))
    if image.width < 1800:
        factor = 1800 / image.width
        image = image.resize((int(image.width * factor), int(image.height * factor)), Image.LANCZOS)
    with tesserocr.PyTessBaseAPI(path=tessdata, lang=OCR_LANG, psm=tesserocr.PSM.AUTO) as api:
        api.SetImage(image)
        return api.GetUTF8Text()


def clean_ocr_text(text: str) -> str:
    """Drop decoration noise: keep lines that contain at least a real word."""
    lines = []
    for line in text.splitlines():
        line = re.sub(r"[|~_<>{}\[\]=*•®©™]+", " ", line)
        line = re.sub(r"\s{2,}", " ", line).strip(" .,-'\"")
        if re.search(r"[A-Za-zÅÄÖåäöÉé]{3,}", line):
            lines.append(line)
    return "\n".join(lines)


async def async_ocr(menu: SchoolMenu, tessdata: str) -> tuple[str, int | None, dict[int, MenuDay]]:
    raw = await asyncio.get_running_loop().run_in_executor(None, _ocr, menu.content, tessdata)
    text = clean_ocr_text(raw)
    week, days = parse_menu_text(text)
    for day in days.values():
        # Headings in decorative menus are often in capitals.
        if day.main.isupper():
            day.main = day.main.capitalize()
    return text, week, days


# -- AI Task (images) ------------------------------------------------------------

AI_INSTRUCTIONS = (
    "Bilden är en skolmatsedel för en vecka. Läs av veckonumret och för varje "
    "veckodag måndag–fredag: huvudrätten (rubriken), beskrivningen och det "
    "vegetariska alternativet. Skriv av texten exakt som den står, på svenska, "
    "utan att hitta på något. Lämna fält tomma om de saknas."
)


def ai_structure() -> dict[str, Any]:
    text = {"text": {}}
    structure: dict[str, Any] = {
        "vecka": {"description": "Veckonummer, t.ex. 43", "required": False, "selector": {"number": {"min": 1, "max": 53, "mode": "box"}}},
    }
    for key, label in zip(WEEKDAYS, WEEKDAY_LABELS, strict=True):
        structure[f"{key}_ratt"] = {"description": f"{label}: huvudrätt", "required": False, "selector": text}
        structure[f"{key}_beskrivning"] = {"description": f"{label}: beskrivning", "required": False, "selector": text}
        structure[f"{key}_vegetariskt"] = {"description": f"{label}: vegetariskt alternativ", "required": False, "selector": text}
    return structure


def parse_ai_result(data: dict[str, Any]) -> tuple[int | None, dict[int, MenuDay]]:
    week = None
    try:
        week = int(float(data.get("vecka"))) if data.get("vecka") not in (None, "") else None
    except (TypeError, ValueError):
        week = None
    days: dict[int, MenuDay] = {}
    for idx, key in enumerate(WEEKDAYS):
        main = str(data.get(f"{key}_ratt") or "").strip()
        veg = _VEG_RE.sub("", str(data.get(f"{key}_vegetariskt") or "").strip())
        desc = str(data.get(f"{key}_beskrivning") or "").strip()
        if main or veg:
            days[idx] = MenuDay(main=main or veg, description=desc, vegetarian=veg)
    return week, days


def dated_days(
    week: int | None, days: dict[int, MenuDay], today: dt.date
) -> dict[dt.date, MenuDay]:
    if week is None or not days:
        return {}
    monday = week_monday(week, today)
    return {monday + dt.timedelta(days=idx): split_labelled(day) for idx, day in sorted(days.items())}


# Some menus have a theme heading per day followed by labelled dishes:
# "Italiensk inspiration" / "DAGENS RÄTT Krämig pestopasta ... VEGETARISKT ...".
# The labels are matched in capitals only, so words in the dishes don't match.
_LABELLED_RE = re.compile(
    r"DAGENS\s+R[ÄA]TT\s*[:\-]?\s*(?P<main>.*?)(?:\s*VEGETARISKT?\s*[:\-]?\s*(?P<veg>.*))?$", re.S
)
_SIGN_OFF_RE = re.compile(r"\s*Smaklig måltid.*$", re.S | re.I)


def split_labelled(day: MenuDay) -> MenuDay:
    """Use the dish after 'DAGENS RÄTT' as main and keep the theme as description."""
    match = _LABELLED_RE.search(day.description)
    if not match or not match.group("main").strip():
        return day
    main = _SIGN_OFF_RE.sub("", match.group("main")).strip().rstrip(".")
    veg = day.vegetarian or _SIGN_OFF_RE.sub("", match.group("veg") or "").strip().rstrip(".")
    return MenuDay(main=main, description=day.main, vegetarian=veg)
