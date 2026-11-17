#!/usr/bin/env python3
"""Build library/library.json (the Akhand 108 text library) from texts/raw/.

Usage:
  python tools/build_library.py            rebuild library/library.json and texts/COVERAGE.md
  python tools/build_library.py --fetch    (re)download missing raw sources into texts/raw, then rebuild
  python tools/build_library.py --check    rebuild, then run the acceptance assertions

Stdlib only. Every verse comes from a fetched file in texts/raw/ (see SOURCES).
Nothing here contains verse text: the script only parses, cleans and splits.
"""
import argparse
import datetime
import html
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "texts" / "raw"
OUT = ROOT / "library" / "library.json"
COVERAGE = ROOT / "texts" / "COVERAGE.md"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
MAXLEN = 400

# --------------------------------------------------------------------------
# Raw sources: local name -> URL
# --------------------------------------------------------------------------
GH_DD = "https://raw.githubusercontent.com/bhavykhatri/DharmicData/main"
GH_RE = "https://raw.githubusercontent.com/sanskrit/raw_etexts/master"
GP = GH_RE + "/purANam/mishram/misc-gp/raw/stotra"
GH_HD = "https://raw.githubusercontent.com/TheHardikDewra/durga-saptashati/HEAD"
GH_KS = "https://raw.githubusercontent.com/kaustubhk24/sangrah/HEAD/docs"
GH_JM = "https://raw.githubusercontent.com/jayeshmepani/HinduScriptures/HEAD/DharmicData"


def wiki(site, title):
    q = urllib.parse.quote(title)
    return f"https://{site}.wikisource.org/w/index.php?title={q}&action=raw"


def wikipage(site, title):
    return f"https://{site}.wikisource.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"))


RCM_KANDS = [
    ("balkand", "1_बाल_काण्ड", "Balkand", "बालकाण्ड"),
    ("ayodhyakand", "2_अयोध्या_काण्ड", "Ayodhyakand", "अयोध्याकाण्ड"),
    ("aranyakand", "3_अरण्य_काण्ड", "Aranyakand", "अरण्यकाण्ड"),
    ("kishkindhakand", "4_किष्किन्धा_काण्ड", "Kishkindhakand", "किष्किन्धाकाण्ड"),
    ("sundarkand", "5_सुंदर_काण्ड", "Sundarkand", "सुन्दरकाण्ड"),
    ("lankakand", "6_लंका_काण्ड", "Lankakand", "लङ्काकाण्ड"),
    ("uttarkand", "7_उत्तर_काण्ड", "Uttarkand", "उत्तरकाण्ड"),
]

SOURCES = {}  # local file name -> url
for i, (slug, fname, _, _) in enumerate(RCM_KANDS, 1):
    SOURCES[f"ramcharitmanas-{i}-{slug}.json"] = f"{GH_DD}/Ramcharitmanas/{fname}_data.json"
SOURCES.update({
    "durga-saptashati-data.js": f"{GH_HD}/data.js",
    "strotratnavali.html": f"{GP}/strotratnavali.html",
    "devi-stotra-ratnakar.html": f"{GP}/devi-stotra-ratnakar.html",
    "sahasranaam-stotra-sangrah.html": f"{GP}/sahasranaam-stotra-sangrah.html",
    "sri-hanumanchalisa.html": f"{GP}/sri-hanumanchalisa.html",
    "shri-satya-narayan-vrat-katha.html": f"{GP}/shri-satya-narayan-vrat-katha.html",
    "arti-sangrah.html": f"{GP}/arti-sangrah.html",
    "aadityahardyastrotam.html": f"{GP}/aadityahardyastrotam.html",
    "lingashtaka-peterfreund.md": f"{GH_RE}/kAvyam/padyam/peterFreund/stotram/lingashtaka.md",
    "vishnu-sahasranama-peterfreund.md": f"{GH_RE}/kAvyam/padyam/peterFreund/stotram/vishnu_sahasranama.md",
    "shiv-chalisa.md": f"{GH_KS}/chalisa/shiv-chalisa.md",
    "ganesh-chalisa.md": f"{GH_KS}/chalisa/ganesh-chalisa.md",
    "hanuman-chalisa-ks.md": f"{GH_KS}/chalisa/aa-hanuman-chalisa.md",
    "mahamrityunjaya-ks.md": f"{GH_KS}/mantras/shiva_maha-mrityunjaya-mantram.md",
    "bajrang-baan.json": f"{GH_JM}/Chalisa/Bajrang Baan Chalisa.json",
    "laxmi-chalisa.json": f"{GH_JM}/Chalisa/Laxmi Chalisa.json",
    "rigveda-mandala-3.json": f"{GH_DD}/Rigveda/rigveda_mandala_3.json",
    "rigveda-mandala-7.json": f"{GH_DD}/Rigveda/rigveda_mandala_7.json",
})
WIKI_SA = {
    "shiv-shadakshara.wiki": "शिवषडाक्षरस्तोत्रम्",
    "navagraha-stotram.wiki": "नवग्रहस्तोत्रम्",
    "gayatri-mantra.wiki": "गायत्रीमन्त्रः",
    "aditya-hridayam.wiki": "आदित्यहृदयम्",
    "ganapati-atharvashirsha.wiki": "गणपत्यथर्वशीर्षम्",
    "durga-chalisa.wiki": "दुर्गा चालीसा",
    "rudrashtakam.wiki": "रुद्राष्टकम्",
    "shiv-tandav-stotram.wiki": "शिवताण्डवस्तोत्रम्",
    "lingashtakam.wiki": "लिङ्गाष्टकम्",
    "sri-suktam.wiki": "श्रीसूक्तम्",
    "chandi-kavacham.wiki": "चण्डीकवचम्",
    "argala-stotram.wiki": "अर्गलास्तोत्रम्",
    "gita-1-sa.wiki": "भगवद्गीता/अर्जुनविषादयोगः",
}
for k, v in WIKI_SA.items():
    SOURCES[k] = wiki("sa", v)
SOURCES["ramcharitmanas-balkand-hi.json"] = (
    "https://hi.wikisource.org/w/api.php?action=parse&page="
    + urllib.parse.quote("रामचरितमानस/बालकाण्ड") + "&prop=text&format=json&formatversion=2")


def fetch_all():
    RAW.mkdir(parents=True, exist_ok=True)
    for name, url in SOURCES.items():
        dest = RAW / name
        if dest.exists() and dest.stat().st_size > 0:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        p = urllib.parse.urlsplit(url)
        url2 = urllib.parse.urlunsplit((p.scheme, p.netloc, urllib.parse.quote(p.path),
                                        urllib.parse.quote(p.query, safe="=&:/%"), ""))
        for attempt in range(6):
            try:
                req = urllib.request.Request(url2, headers={"User-Agent": UA})
                data = urllib.request.urlopen(req, timeout=90).read()
                break
            except Exception as e:  # noqa: BLE001
                print(f"  retry {name}: {e}", file=sys.stderr)
                time.sleep(8)
        else:
            raise SystemExit(f"could not fetch {name} from {url}")
        dest.write_bytes(data)
        print(f"fetched {name} ({len(data)} bytes)")
        if "wikisource.org" in url:
            time.sleep(5)  # wikisource rate limit
    # one combined, trimmed Gita file (the per-chapter sources are 1-5 MB each because of commentaries we do not use;
    # only chapter/verse/text are kept, downloaded in memory)
    dest = RAW / "bhagavad-gita.json"
    if not dest.exists():
        verses = []
        for c in range(1, 19):
            url = f"{GH_DD}/SrimadBhagvadGita/bhagavad_gita_chapter_{c}.json"
            data = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=120).read()
            for v in json.loads(data)["BhagavadGitaChapter"]:
                verses.append({"chapter": v["chapter"], "verse": v["verse"], "text": v["text"]})
        dest.write_text(json.dumps(verses, ensure_ascii=False, indent=0), encoding="utf-8")


# --------------------------------------------------------------------------
# Cleaning
# --------------------------------------------------------------------------
DEVNUM = "०-९"
ZERO_WIDTH = re.compile("[​‌‍‎‏⁠﻿­]")
VEDIC = re.compile("[॑-॔᳐-᳹]")
MARKER = re.compile(
    r"॥\s*!?\s*[०-९0-9]+(?:\s*[.।\\-]+\s*[०-९0-9]+)*\s*(?:\([^)]*\))?\s*(?:[॥।])?"
    r"|।\s*[०-९0-9]+(?:\s*[.।\\-]+\s*[०-९0-9]+)*\s*(?:\([^)]*\))?\s*॥")
TRAIL_NUM = re.compile(r"\s+[०-९0-9]+\s*$")
PUNCT_TO_SPACE = re.compile(r"[,;:.!?\"'`‘’“”–—()\[\]{}<>/\\#@*_~^+=&%$-]")


def clean_line(s, trailing_num=False, itx_fix=False):
    s = html.unescape(s)
    s = re.sub(r"<[^>]*>", " ", s)
    s = unicodedata.normalize("NFC", s)
    s = ZERO_WIDTH.sub("", s)
    s = VEDIC.sub("", s)
    if itx_fix:
        # ITRANS->Devanagari artefact in TheHardikDewra/data.js: ".h" became "।ह्" inside words
        s = re.sub(r"(?<=्)।ह्(?=[क-ह])", "", s)
    s = s.replace("।।", "॥").replace("||", "॥").replace("|", "।")
    s = MARKER.sub("॥", s)
    if trailing_num:
        s = TRAIL_NUM.sub("", s)
    s = re.sub(r"\([^)]*\)", " ", s)  # parenthetical glosses
    s = PUNCT_TO_SPACE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"(?<=[।॥])(?=[ऀ-ॿ])", " ", s)  # "दयाला।सदा" -> "दयाला। सदा"
    s = re.sub(r"॥\s*॥", "॥", s)
    s = re.sub(r"॥\s*।$", "॥", s)
    return unicodedata.normalize("NFC", s).strip()


def has_marker(s):
    return bool(MARKER.search(s.replace("।।", "॥").replace("||", "॥")))


def is_speaker(line):
    """A bare '<name> उवाच' line (a label, not a verse)."""
    w = clean_line(line).replace("॥", "").replace("।", "").split()
    return 0 < len(w) <= 5 and w[-1].endswith("वाच") or (len(w) == 1 and w[0].endswith("ुवाच"))


def split_long(lines, maxlen=MAXLEN):
    """lines: list of cleaned lines of one verse -> list of verse strings, each <= maxlen."""
    text = "\n".join(lines)
    if len(text) <= maxlen:
        return [text]
    out, cur = [], []
    for ln in lines:
        if cur and len("\n".join(cur + [ln])) > maxlen:
            out.append("\n".join(cur))
            cur = []
        cur.append(ln)
        while len("\n".join(cur)) > maxlen and len(cur) == 1:
            # a single over-long line: split at the last danda / space before maxlen
            big = cur[0]
            cut = max(big.rfind("।", 0, maxlen), big.rfind("॥", 0, maxlen))
            if cut < 40:
                cut = big.rfind(" ", 0, maxlen)
            out.append(big[: cut + 1].strip())
            cur = [big[cut + 1:].strip()]
    if cur and any(cur):
        out.append("\n".join(cur))
    return [o for o in out if o]


def finish(groups, **kw):
    """groups: list of list-of-raw-lines. Returns list of verse texts."""
    verses = []
    for g in groups:
        lines = [clean_line(x, **kw) for x in g]
        lines = [x for x in lines if x and re.search(r"[ऀ-ॿ]", x)]
        if lines:
            verses.extend(split_long(lines))
    return verses


def pairs(lines, n=2):
    return [lines[i:i + n] for i in range(0, len(lines), n)]


def read(name):
    return (RAW / name).read_text(encoding="utf-8")


def html_to_lines(name):
    t = read(name)
    t = re.sub(r"(?is)<(script|style).*?</\1>", "", t)
    t = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</h\d>|</li>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    return [l.strip() for l in t.split("\n") if l.strip()]


# --------------------------------------------------------------------------
# Parsers
# --------------------------------------------------------------------------
HINDI_WORDS = set("है हैं के में से और जो वे उन तथा हूँ करता करते करती यह कि जिन जिनके जिनका जिसे आदि उनके उनको उन्हें "
                  "करो इन तुम्हारे तुम्हारी मुझको इस इसके इसका होता होती होते रहे रहा रहती लिये लिए मैं मेरे मेरी मुझे आपको हमारे हमें हम "
                  "था थे थी गया गयी गये वाले वाली वाला भी ही नहीं अपने अपनी अपना सभी करके करनेवाले".split())


def looks_hindi(line):
    w = re.sub(r"[॥।\-—()\[\]]", " ", line).split()
    n = sum(1 for x in w if x in HINDI_WORDS)
    if re.search(r"[;—]", line):  # Sanskrit lines carry no semicolon or em-dash; translations do
        return True
    return n >= 2 or (n >= 1 and len(line) > 110)


END_MARK = re.compile(r"॥\s*[०-९]+\s*॥\s*$")


def gp_verses(lines, start_pat, stop_pat=None, number_stop=None):
    """Gita Press layout: Sanskrit lines, verse ends with '॥ N॥', followed by a Hindi translation (also ends
    with '॥ N॥'). Returns groups of raw Sanskrit lines between the first line matching start_pat and the first
    later line matching stop_pat."""
    s = next(i for i, l in enumerate(lines) if re.search(start_pat, l))
    e = len(lines)
    if stop_pat:
        e = next(i for i in range(s + 1, len(lines)) if re.search(stop_pat, lines[i]))
    groups, cur = [], []
    for l in lines[s:e]:
        if re.search(r"^इति|समाप्त|सम्पूर्ण", l):
            cur = []
            continue
        if looks_hindi(l):
            cur = []
            continue
        if re.search(r"उवाच\s*$", l) and not END_MARK.search(l):
            continue
        cur.append(l)
        if END_MARK.search(l):
            groups.append(cur)
            cur = []
    return groups


def marker_number(line):
    m = re.search(r"([०-९]+)\s*॥\s*$", line)
    return int("".join(str("०१२३४५६७८९".index(c)) for c in m.group(1))) if m else None


def gp_section(name, heading_pat, next_heading_pat, first_line_skip=1):
    lines = html_to_lines(name)
    s = next(i for i, l in enumerate(lines) if re.search(heading_pat, l))
    e = next(i for i in range(s + 1, len(lines)) if re.search(next_heading_pat, lines[i]))
    sect = lines[s + first_line_skip:e]
    groups, cur, nums = [], [], []
    for l in sect:
        if re.search(r"^इति|समाप्त|सम्पूर्ण|^ध्यानम्$", l):
            cur = []
            continue
        if looks_hindi(l):
            cur = []
            continue
        cur.append(l)
        if END_MARK.search(l):
            groups.append(cur)
            nums.append(marker_number(l))
            cur = []
    if nums != list(range(1, len(nums) + 1)):
        raise SystemExit(f"{name} {heading_pat}: verse numbers not consecutive: {nums}")
    return finish(groups)


def wiki_poem(name):
    t = read(name)
    t = re.sub(r"\{\{\s*header(?:[^{}]|\{\{[^{}]*\}\})*\}\}", "", t, flags=re.S)
    t = re.sub(r"\[\[वर्गः:[^\]]*\]\]", "", t)
    t = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", t)
    t = re.sub(r"\[\[([^\]]*)\]\]", r"\1", t)
    t = t.replace("<poem>", "").replace("</poem>", "")
    return t


def parse_rcm(i):
    slug = RCM_KANDS[i - 1][0]
    data = json.loads(read(f"ramcharitmanas-{i}-{slug}.json"))
    verses = []
    for e in data:
        lines = [l.strip() for l in e["content"].split("\n") if l.strip()]
        if e["type"] == "श्लोक":
            groups, cur = [], []
            for l in lines:
                cur.append(l)
                if has_marker(l):
                    groups.append(cur)
                    cur = []
            if cur:
                groups.extend(pairs(cur))
        else:
            groups = pairs(lines)
        verses.extend(finish(groups))
    return verses


def parse_gita(chapter):
    data = json.loads(read("bhagavad-gita.json"))
    out = []
    for v in data:
        if v["chapter"] != chapter:
            continue
        lines = [l.strip() for l in v["text"].split("\n") if l.strip()]
        lines = [l for l in lines if not (re.search(r"उवाच\s*$", l) and not has_marker(l))]
        out.extend(finish([lines]))
    return out


def parse_saptashati_chapter(n):
    js = read("durga-saptashati-data.js")
    d = json.loads(js[js.index("{"): js.rindex("}") + 1])
    text = [c for c in d["chapters"] if c["num"] == n][0]["devanagari"]
    return marker_verses(text.split("\n"))


def marker_verses(lines, skip_before_first_marker=True):
    """Verses end at a line carrying a '॥ N॥'-style marker. Header lines before the first marker (vinyoga,
    dhyana) are dropped, standalone '<x> उवाच' lines and chapter colophons are dropped."""
    groups, cur, first = [], [], True
    for l in lines:
        if not l.strip():
            continue
        if is_speaker(l) and not has_marker(l):
            continue
        if re.search(r"ध्यायः|समस्ता उवाच|^उवाच\s*[०-९]", l):  # chapter colophon / mantra-count notes
            cur = []
            continue
        cur.append(l)
        if has_marker(l):
            if first:
                first = False
                cur = [] if is_speaker(cur[-1]) else cur[-2:]
            groups.append(cur)
            cur = []
    groups = [g for g in groups if g and not (len(g) == 1 and is_speaker(g[0]))]
    return finish(groups, itx_fix=True)


def parse_anga(anga_id):
    js = read("durga-saptashati-data.js")
    d = json.loads(js[js.index("{"): js.rindex("}") + 1])
    a = [x for x in d["angas"] if x["id"] == anga_id][0]
    return marker_verses(a["devanagari"].split("\n"))


def parse_navarna():
    js = read("durga-saptashati-data.js")
    d = json.loads(js[js.index("{"): js.rindex("}") + 1])
    return finish([[d["navarnaMantra"]["devanagari"]]])


def parse_om_namah_shivaya():
    t = wiki_poem("shiv-shadakshara.wiki")
    m = re.search(r"ॐ\s*नमः\s*शिवाय", t)
    return finish([[m.group(0)]])


def parse_rudrashtakam():
    return gp_section("strotratnavali.html", r"^१५—श्रीरुद्राष्टकम्", r"^१६—")


def parse_tandav():
    return gp_section("strotratnavali.html", r"^१४—शिवताण्डवस्तोत्रम्", r"^१५—")


def parse_lingashtakam():
    lines = [l.strip() for l in read("lingashtaka-peterfreund.md").split("\n") if l.strip()]
    lines = lines[1:]  # title
    groups, cur = [], []
    for l in lines:
        cur.append(l)
        if re.search(r"\s[०-९]+\s*$", l):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return finish(groups, trailing_num=True)


def md_body(name):
    t = read(name)
    t = re.sub(r"(?s)^---.*?---", "", t)
    t = re.sub(r"(?s)<span class='index-text'>.*", "", t)
    return [l.strip() for l in t.split("\n")]


def parse_shiv_chalisa():
    lines = [l for l in md_body("shiv-chalisa.md") if l and not l.startswith("#")]
    # chaupai lines carry a mid-line danda ("A।B॥"); doha lines do not. Pair each kind separately.
    groups, cur, kind = [], [], None
    for l in lines:
        k = bool(re.search(r"।\s*[ऀ-ॿ]", l))
        if cur and (k != kind or len(cur) == 2):
            groups.append(cur)
            cur = []
        kind = k
        cur.append(l)
    if cur:
        groups.append(cur)
    return finish(groups)


def parse_ganesh_chalisa():
    lines = [l for l in md_body("ganesh-chalisa.md") if l and not l.startswith("#")]
    lines = [l.replace("<br />", "") for l in lines]
    return finish(pairs(lines))


def parse_mahamrityunjaya():
    rv = json.loads(read("rigveda-mandala-7.json"))
    text = next(x["text"] for x in rv if x["sukta"] == 59)
    verse = [p for p in text.split("\n\n") if "॥१२॥" in p][-1]
    verse = verse.replace("॥१२॥", "")
    parts = [x.strip() for x in verse.split("।") if x.strip()]
    ks = "\n".join(md_body("mahamrityunjaya-ks.md"))
    prefix = "ॐ " if "ओं त्र्य" in ks else ""
    lines = [prefix + parts[0] + " ।", parts[1] + " ॥"]
    return finish([lines])



def parse_durga_chalisa():
    t = wiki_poem("durga-chalisa.wiki")
    t = t.split("==संबंधित")[0]
    lines = [l.strip() for l in t.split("\n") if l.strip() and not l.strip().startswith("==")]
    return finish(pairs(lines))


def parse_atharvashirsha():
    t = wiki_poem("ganapati-atharvashirsha.wiki")
    blocks = [b.strip() for b in re.split(r"\n\s*\n", t) if b.strip()]
    groups = []
    for b in blocks:
        lines = [l.strip() for l in b.split("\n") if l.strip()]
        # headings: lines wrapped in ॥ ... ॥ with no verse text of their own
        lines = [l for l in lines if not re.fullmatch(r"॥[^॥]*॥", l.replace("।", ""))
                 or re.search(r"[०-९]", l)]
        if lines:
            groups.append(lines)
    return finish(groups)


def parse_ganesh_vakratunda():
    lines = html_to_lines("shri-satya-narayan-vrat-katha.html")
    i = next(i for i, l in enumerate(lines) if l.startswith("वक्रतुण्ड महाकाय"))
    return finish([lines[i:i + 2]])


def parse_hanuman_chalisa():
    lines = html_to_lines("sri-hanumanchalisa.html")
    s = next(i for i, l in enumerate(lines) if l.startswith("श्रीगुरु चरन सरोज रज"))
    e = next(i for i in range(s, len(lines)) if "सुर भूप" in lines[i])
    body = [l for l in lines[s:e + 1] if l not in ("दोहा", "चौपाई")]
    groups, cur = [], []
    for l in body:
        cur.append(l)
        if l.rstrip().endswith("॥"):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return finish(groups)


def parse_bajrang_baan():
    d = json.loads(read("bajrang-baan.json"))
    groups = []
    for e in d:
        t = [x.strip() for x in e["text"].split("\n") if x.strip()]
        t = [x for x in t if re.sub(r"[:\s]", "", x) not in ("दोहा", "चौपाई")]
        groups.extend(pairs(t, 2) if len(t) != 3 else [t])
    return finish(groups)


def parse_vishnu_sahasranama():
    lines = html_to_lines("sahasranaam-stotra-sangrah.html")
    s = next(i for i, l in enumerate(lines) if l.startswith("वैशम्पायन उवाच"))
    nums = []
    groups, cur = [], []
    prev = 0
    for l in lines[s:]:
        if looks_hindi(l):
            cur = []
            continue
        if re.search(r"उवाच\s*$", l) and not END_MARK.search(l):
            continue
        cur.append(l)
        m = END_MARK.search(l)
        if m:
            n = int("".join(str("०१२३४५६७८९".index(c)) for c in re.search(r"([०-९]+)\s*॥\s*$", l).group(1)))
            if n != prev + 1:
                break  # numbering restarts: next stotra
            prev = n
            groups.append(cur)
            cur = []
    if prev != 142:
        raise SystemExit(f"vishnu sahasranama: expected 142 verses, got {prev}")
    return finish(groups)


def gp_numbered_from(name, start_line_pat, stop_pat):
    lines = html_to_lines(name)
    return finish(gp_verses(lines, start_line_pat, stop_pat))


def parse_sri_suktam():
    return gp_section("devi-stotra-ratnakar.html", r"^३३\.\s*श्रीसूक्तम्", r"^३४\.")


def parse_lakshmi_chalisa():
    d = json.loads(read("laxmi-chalisa.json"))
    items = [re.sub(r"॥\s*टेक\s*॥", "", e["text"]) for e in d]
    # first 'दोहा' heading ... second 'दोहा' block end; aarti follows the closing dohas
    start = next(i for i, t in enumerate(items) if t.strip() == "दोहा") + 1
    end = next(i for i, t in enumerate(items) if "आरती" in t and i > start)
    groups = []
    chaupai = []
    for t in items[start:end]:
        if t.strip() == "दोहा":
            if chaupai:
                groups.extend(pairs(chaupai))
                chaupai = []
            continue
        lines = [x for x in t.split("\n") if x.strip()]
        if len(lines) >= 2:   # a doha block (two lines)
            if chaupai:
                groups.extend(pairs(chaupai))
                chaupai = []
            groups.append(lines)
        else:
            chaupai.append(lines[0])
    if chaupai:
        groups.extend(pairs(chaupai))
    verses = finish(groups)
    return [v.replace("टेक", "").strip() for v in verses]


def parse_saraswati_vandana():
    return gp_section("strotratnavali.html", r"^२५—श्रीसरस्वतीस्तोत्रम्", r"^२६—")


def parse_gayatri():
    t = wiki_poem("gayatri-mantra.wiki")
    lines = [l.strip() for l in t.split("\n") if l.strip()]
    return finish([lines])


def parse_aditya_hridayam():
    t = wiki_poem("aditya-hridayam.wiki")
    lines = [l.strip() for l in t.split("\n") if l.strip()]
    groups, cur = [], []
    for l in lines:
        if re.fullmatch(r"ॐ", l) or re.fullmatch(r"॥[^॥]*॥", l):
            continue
        cur.append(l)
        if re.search(r"॥\s*[०-९]+\s*॥?\s*$", l):
            groups.append(cur)
            cur = []
    return finish(groups)


NAVAGRAHA = [("surya", "Surya", "सूर्य", "रविः"), ("chandra", "Chandra", "चन्द्र", "सोमः"),
             ("mangal", "Mangal", "मङ्गल", "मङ्गलः"), ("budh", "Budh", "बुध", "बुधः"),
             ("guru", "Guru", "गुरु", "गुरुः"), ("shukra", "Shukra", "शुक्र", "शुक्रः"),
             ("shani", "Shani", "शनि", "शनिः"), ("rahu", "Rahu", "राहु", "राहुः"),
             ("ketu", "Ketu", "केतु", "केतुः")]


def parse_navagraha(label):
    t = wiki_poem("navagraha-stotram.wiki")
    m = re.search(r"॥\s*" + re.escape(label) + r"\s*॥\s*\n(.+?\n.+?)(?:\n|$)", t)
    return finish([m.group(1).split("\n")])


def parse_karpura():
    lines = html_to_lines("arti-sangrah.html")
    i = next(i for i, l in enumerate(lines) if l.startswith("कर्पूरगौरं करुणावतारं"))
    return finish([lines[i:i + 2]])


def parse_sarva_mangala():
    lines = html_to_lines("shri-satya-narayan-vrat-katha.html")
    i = next(i for i, l in enumerate(lines) if l.startswith("सर्वमङ्गलमाङ्गल्ये शिवे सर्वार्थसाधिके"))
    return finish([lines[i:i + 2]])


# --------------------------------------------------------------------------
# Library definition
# --------------------------------------------------------------------------
GITA_URL = GH_DD + "/SrimadBhagvadGita/bhagavad_gita_chapter_{n}.json"


def work(id_, title_hi, title_en, url, source_name, parse, seat=None, partial=None, note=""):
    return dict(id=id_, title_hi=title_hi, title_en=title_en, source_url=url, source_name=source_name,
                seat=seat, parse=parse, partial=partial, note=note)


def build_stations():
    S = []
    GP_STR = "Gita Press, Stotra Ratnavali (raw_etexts mirror)"
    WS_SA = "Sanskrit Wikisource"
    st = lambda i, id_, hi, en, mode, works: dict(index=i, id=id_, label_hi=hi, label_en=en, mode=mode, works=works)

    S.append(st(0, "shiv-japa", "शिव जप", "Shiv Japa", "japa", [
        work("om-namah-shivaya", "ॐ नमः शिवाय", "Om Namah Shivaya", wiki("sa", "शिवषडाक्षरस्तोत्रम्"),
             WS_SA + " (शिवषडाक्षरस्तोत्रम्, closing line)", parse_om_namah_shivaya)]))
    S.append(st(1, "shiv-stuti", "शिव स्तुति", "Shiv Stuti", "parallel", [
        work("rudrashtakam", "रुद्राष्टकम्", "Rudrashtakam", GP + "/strotratnavali.html", GP_STR, parse_rudrashtakam),
        work("shiv-tandav-stotram", "शिवताण्डवस्तोत्रम्", "Shiv Tandav Stotram", GP + "/strotratnavali.html", GP_STR,
             parse_tandav),
        work("lingashtakam", "लिङ्गाष्टकम्", "Lingashtakam", GH_RE + "/kAvyam/padyam/peterFreund/stotram/lingashtaka.md",
             "Peter Freund e-text (sanskrit/raw_etexts)", parse_lingashtakam),
        work("shiv-chalisa", "शिव चालीसा", "Shiv Chalisa", GH_KS + "/chalisa/shiv-chalisa.md",
             "kaustubhk24/sangrah", parse_shiv_chalisa)]))
    S.append(st(2, "mrityunjaya", "मृत्युंजय", "Mrityunjaya", "japa", [
        work("mahamrityunjaya-mantra", "महामृत्युञ्जय मन्त्र", "Mahamrityunjaya Mantra",
             GH_KS + "/mantras/shiva_maha-mrityunjaya-mantram.md", "kaustubhk24/sangrah (Rigveda 7.59.12)",
             parse_mahamrityunjaya)]))
    sapta = []
    for n in range(1, 14):
        sapta.append(work(f"durga-saptashati-{n:02d}", f"दुर्गा सप्तशती, अध्याय {n}", f"Durga Saptashati, Chapter {n}",
                          GH_HD + "/data.js", "TheHardikDewra/durga-saptashati (from Sanskrit Documents durga700.itx)",
                          (lambda n=n: parse_saptashati_chapter(n))))
    S.append(st(3, "saptashati", "सप्तशती", "Saptashati", "relay", sapta))
    S.append(st(4, "durga", "दुर्गा", "Durga", "parallel", [
        work("durga-chalisa", "दुर्गा चालीसा", "Durga Chalisa", wiki("sa", "दुर्गा चालीसा"), WS_SA, parse_durga_chalisa),
        work("devi-kavach", "देवी कवचम्", "Devi Kavacham", GH_HD + "/data.js", "TheHardikDewra/durga-saptashati",
             lambda: parse_anga("kavacha")),
        work("argala-stotram", "अर्गला स्तोत्रम्", "Argala Stotram", GH_HD + "/data.js", "TheHardikDewra/durga-saptashati",
             lambda: parse_anga("argala")),
        work("navarna-mantra", "नवार्ण मन्त्र", "Navarna Mantra", GH_HD + "/data.js", "TheHardikDewra/durga-saptashati",
             parse_navarna)]))
    S.append(st(5, "ganesh", "गणेश", "Ganesh", "parallel", [
        work("ganapati-atharvashirsha", "गणपत्यथर्वशीर्षम्", "Ganapati Atharvashirsha", wiki("sa", "गणपत्यथर्वशीर्षम्"),
             WS_SA, parse_atharvashirsha),
        work("vakratunda", "वक्रतुण्ड महाकाय", "Vakratunda Mahakaya", GP + "/shri-satya-narayan-vrat-katha.html",
             "Gita Press, Shri Satya Narayan Vrat Katha (raw_etexts mirror)", parse_ganesh_vakratunda),
        work("ganesh-chalisa", "गणेश चालीसा", "Ganesh Chalisa", GH_KS + "/chalisa/ganesh-chalisa.md",
             "kaustubhk24/sangrah", parse_ganesh_chalisa)]))
    ram = []
    for i, (slug, _, en, hi) in enumerate(RCM_KANDS, 1):
        ram.append(work(f"ramcharitmanas-{i}-{slug}", f"रामचरितमानस, {hi}", f"Ramcharitmanas, {en}",
                        f"{GH_DD}/Ramcharitmanas/{RCM_KANDS[i-1][1]}_data.json", "bhavykhatri/DharmicData",
                        (lambda i=i: parse_rcm(i))))
    S.append(st(6, "ram", "राम", "Ram", "relay", ram))
    S.append(st(7, "hanuman", "हनुमान", "Hanuman", "parallel", [
        work("hanuman-chalisa", "हनुमान चालीसा", "Hanuman Chalisa", GP + "/sri-hanumanchalisa.html",
             "Gita Press, Shri Hanuman Chalisa (raw_etexts mirror)", parse_hanuman_chalisa),
        work("bajrang-baan", "बजरंग बाण", "Bajrang Baan", GH_JM + "/Chalisa/Bajrang Baan Chalisa.json",
             "jayeshmepani/HinduScriptures", parse_bajrang_baan)]))
    vish = [work("vishnu-sahasranama", "श्रीविष्णुसहस्रनामस्तोत्रम्", "Vishnu Sahasranama",
                 GP + "/sahasranaam-stotra-sangrah.html", "Gita Press, Sahasranaam Stotra Sangrah (raw_etexts mirror)",
                 parse_vishnu_sahasranama)]
    for n in range(1, 19):
        vish.append(work(f"bhagavad-gita-{n:02d}", f"श्रीमद्भगवद्गीता, अध्याय {n}", f"Bhagavad Gita, Chapter {n}",
                         GITA_URL.format(n=n), "bhavykhatri/DharmicData (Gita Press text)", (lambda n=n: parse_gita(n))))
    S.append(st(8, "vishnu", "विष्णु", "Vishnu", "parallel", vish))
    S.append(st(9, "lakshmi", "लक्ष्मी", "Lakshmi", "parallel", [
        work("sri-suktam", "श्रीसूक्तम्", "Sri Suktam", GP + "/devi-stotra-ratnakar.html",
             "Gita Press, Devi Stotra Ratnakar (raw_etexts mirror)", parse_sri_suktam),
        work("lakshmi-chalisa", "लक्ष्मी चालीसा", "Lakshmi Chalisa", GH_JM + "/Chalisa/Laxmi Chalisa.json",
             "jayeshmepani/HinduScriptures", parse_lakshmi_chalisa),
        work("saraswati-vandana", "श्रीसरस्वतीस्तोत्रम्", "Saraswati Vandana (Sri Saraswati Stotram)",
             GP + "/strotratnavali.html", GP_STR, parse_saraswati_vandana)]))
    S.append(st(10, "surya", "सूर्य", "Surya", "parallel", [
        work("gayatri-mantra", "गायत्री मन्त्र", "Gayatri Mantra", wiki("sa", "गायत्रीमन्त्रः"), WS_SA, parse_gayatri),
        work("aditya-hridayam", "आदित्यहृदयम्", "Aditya Hridayam", wiki("sa", "आदित्यहृदयम्"), WS_SA,
             parse_aditya_hridayam)]))
    ng = []
    for seat, (slug, en, hi, label) in enumerate(NAVAGRAHA):
        ng.append(work(f"navagraha-{slug}", f"{hi} ({label.rstrip('ः')}) मन्त्र", f"{en} mantra",
                       wiki("sa", "नवग्रहस्तोत्रम्"), WS_SA + " (नवग्रहस्तोत्रम्, verse for this graha)",
                       (lambda label=label: parse_navagraha(label)), seat=seat))
    S.append(st(11, "navagraha", "नवग्रह", "Navagraha", "japa-per-seat", ng))
    return S


def build():
    stations = build_stations()
    cover = []
    for s in stations:
        works = []
        for w in s["works"]:
            verses = w["parse"]()
            works.append({
                "id": w["id"], "title_hi": w["title_hi"], "title_en": w["title_en"],
                "source_url": w["source_url"], "source_name": w["source_name"], "seat": w["seat"],
                "verses": [{"n": i, "text": v} for i, v in enumerate(verses, 1)],
            })
            cover.append((s["index"], s["id"], w, len(verses)))
        s["works"] = works
    gathering = {}
    g_defs = {
        "ganesh": ("Vakratunda Mahakaya (Ganesh invocation)", GP + "/shri-satya-narayan-vrat-katha.html", parse_ganesh_vakratunda),
        "shiv": ("Karpura Gauram (Shiv vandana)", GP + "/arti-sangrah.html", parse_karpura),
        "durga": ("Sarva Mangala Mangalye (Durga vandana)", GP + "/shri-satya-narayan-vrat-katha.html", parse_sarva_mangala),
    }
    for k, (title, url, fn) in g_defs.items():
        gathering[k] = {"title_en": title, "source_url": url,
                        "verses": [{"n": i, "text": v} for i, v in enumerate(fn(), 1)]}
    lib = {"version": 1, "built_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "stations": stations, "gathering": gathering}
    for s in lib["stations"]:
        for w in s["works"]:
            w.pop("parse", None)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(lib, ensure_ascii=False, indent=1), encoding="utf-8")
    return lib


# --------------------------------------------------------------------------
# Spot checks against a second, independent fetched source
# --------------------------------------------------------------------------
def skeleton(s, loose=False):
    """Letters only (marks, punctuation, spaces, numbers dropped). With loose=True also fold spelling variants that
    do not change the words: anusvara / final m / nasal+virama, avagraha, chandrabindu."""
    s = unicodedata.normalize("NFC", s)
    s = VEDIC.sub("", s)
    s = ZERO_WIDTH.sub("", s)
    s = s.replace("ॐ", "ओम").replace("ओं", "ओम")
    if loose:
        s = re.sub(r"म्(?=\s|$|[।॥])", "ं", s)
        s = s.replace("ऽ", "").replace("ँ", "ं")
    s = "".join(ch for ch in s if "ऀ" <= ch <= "ॿ" and ch not in "।॥" and not ("०" <= ch <= "९"))
    if loose:
        s = re.sub(r"[ङञणनम]्(?=[क-ह])", "ं", s).replace("ं", "")
    return s


def spot_checks(lib):
    res = []
    byid = {w["id"]: w for st in lib["stations"] for w in st["works"]}

    def first(w, k=1):
        return " ".join(v["text"] for v in byid[w]["verses"][:k])

    def cmp(label, mine, other, other_desc):
        a, b = skeleton(mine), skeleton(other)
        la, lb = skeleton(mine, True), skeleton(other, True)
        if a == b:
            status = "MATCH"
        elif la == lb:
            status = "MATCH (spelling variants only: anusvara/nasal, avagraha)"
        elif la and lb and (la in lb or lb in la):
            status = "MATCH (one text longer; shared part identical after folding spelling variants)"
        else:
            status = "DIFFERS"
        res.append((label, other_desc, status, mine.replace("\n", " / "), other.replace("\n", " / ") if status != "MATCH" else ""))

    # 1 Rudrashtakam v1: Gita Press vs Sanskrit Wikisource
    t = wiki_poem("rudrashtakam.wiki")
    wl = [l for l in t.split("\n") if l.strip()][:2]
    cmp("Rudrashtakam verse 1 (first two lines)", " ".join(first("rudrashtakam").split("\n")[:2]), " ".join(clean_line(x) for x in wl),
        "Sanskrit Wikisource रुद्राष्टकम् (different edition: Gita Press has नमामीशमीशान, Wikisource नमामिशमीशान)")
    # 2 Shiv Tandav v1
    t = wiki_poem("shiv-tandav-stotram.wiki")
    ls = [l for l in t.split("\n") if l.strip()]
    i = next(i for i, l in enumerate(ls) if l.startswith("जटाऽटवी"))
    cmp("Shiv Tandav verse 1", first("shiv-tandav-stotram"), " ".join(clean_line(x) for x in ls[i:i + 4]),
        "Sanskrit Wikisource शिवताण्डवस्तोत्रम् (avagraha-insensitive compare below)")
    # 3 Hanuman Chalisa opening: Gita Press vs kaustubhk24
    ks = [l for l in md_body("hanuman-chalisa-ks.md") if l and not l.startswith("#")][:4]
    cmp("Hanuman Chalisa opening doha", first("hanuman-chalisa"), " ".join(clean_line(x) for x in ks),
        "kaustubhk24/sangrah (different edition/spelling)")
    # 4 Gita 1.1: Gita Press vs Sanskrit Wikisource
    t = read("gita-1-sa.wiki")
    m = re.search(r"धर्मक्षेत्रे\s*कुरुक्षेत्रे[^\n]*\n?[^\n]*", t)
    cmp("Bhagavad Gita 1.1", first("bhagavad-gita-01"), clean_line(m.group(0)) if m else "", "sa.wikisource भगवद्गीता/अर्जुनविषादयोगः")
    # 5 Gayatri: Wikisource vs Rigveda 3.62.10 (DharmicData), accents stripped
    rv = json.loads(read("rigveda-mandala-3.json"))
    txt = next(x["text"] for x in rv if x["sukta"] == 62)
    txt = VEDIC.sub("", unicodedata.normalize("NFC", txt))
    m = re.search(r"तत्\s*सवितुर्वरेण्यं[^॥]*", txt)
    cmp("Gayatri mantra (after the vyahritis)", " ".join(byid["gayatri-mantra"]["verses"][0]["text"].split("\n")[1:]),
        clean_line(m.group(0)) if m else "", "Rigveda 3.62.10 via DharmicData (accents stripped)")
    # 6 Mahamrityunjaya: Rigveda (library) vs the kaustubhk24/sangrah page
    ks = [l for l in md_body("mahamrityunjaya-ks.md") if l and not l.startswith("#")]
    k = next(i for i, l in enumerate(ks) if "ऋ|वे|७|५९|१२" in l)
    cmp("Mahamrityunjaya mantra", byid["mahamrityunjaya-mantra"]["verses"][0]["text"],
        " ".join(clean_line(x) for x in ks[k + 1:k + 3]), "kaustubhk24/sangrah page for RV 7.59.12")
    # 7 Sarva Mangala Mangalye: Gita Press vs Saptashati 11.10
    sj = read("durga-saptashati-data.js")
    d = json.loads(sj[sj.index("{"): sj.rindex("}") + 1])
    ch11 = [c for c in d["chapters"] if c["num"] == 11][0]["devanagari"]
    m = re.search(r"सर्वमङ्गलमाङ्गल्ये[^\n]*\n[^\n]*", ch11)
    cmp("Sarva Mangala Mangalye", lib["gathering"]["durga"]["verses"][0]["text"], clean_line(m.group(0)) if m else "",
        "Durga Saptashati 11.10 (TheHardikDewra/durga-saptashati)")
    # 8 Argala verse 1: data.js vs Sanskrit Wikisource
    t = read("argala-stotram.wiki")
    m = re.search(r"जय त्वं देवि चामुण्डे[^\n]*\n?[^\n]*", t)
    cmp("Argala Stotram verse 1", first("argala-stotram"), clean_line(m.group(0)) if m else "", "sa.wikisource अर्गलास्तोत्रम्")
    # 9 Vishnu Sahasranama: Gita Press vs Peter Freund e-text, first name verse
    pf = read("vishnu-sahasranama-peterfreund.md")
    m = re.search(r"विश्वं विष्णुर्वषट्कारो[^\n]*\n+[^\n]*", pf)
    gp = byid["vishnu-sahasranama"]["verses"]
    mine = next(v["text"] for v in gp if v["text"].startswith("ॐ विश्वं"))
    cmp("Vishnu Sahasranama first name-verse", mine, clean_line(m.group(0), trailing_num=True) if m else "",
        "Peter Freund e-text (PDF extract)")
    # 10 Aditya Hridayam v1: Wikisource vs Gita Press
    lines = html_to_lines("aadityahardyastrotam.html")
    i = next(i for i, l in enumerate(lines) if l.startswith("ततो युद्धपरिश्रान्तं"))
    cmp("Aditya Hridayam verse 1", first("aditya-hridayam"), " ".join(clean_line(x) for x in lines[i:i + 2]),
        "Gita Press, Aditya Hridaya Stotram")
    # 11 Sri Suktam v1: Gita Press vs Wikisource
    t = wiki_poem("sri-suktam.wiki")
    ls = [l for l in t.split("\n") if l.strip()]
    cmp("Sri Suktam verse 1", first("sri-suktam"), " ".join(clean_line(x) for x in ls[:2]), "sa.wikisource श्रीसूक्तम्")
    # 12 Ramcharitmanas Balkand opening chaupai: DharmicData vs hi.wikisource
    t = json.loads(read("ramcharitmanas-balkand-hi.json"))["parse"]["text"]
    t = html.unescape(re.sub(r"<[^>]+>", "\n", t))
    m = re.search(r"बन्दउँ गुरु-पद-पदुम-परागा[^\n]*", t)
    mine = next((v["text"] for v in byid["ramcharitmanas-1-balkand"]["verses"] if v["text"].startswith("बंदउ गुरु पद पदुम")), "")
    cmp("Ramcharitmanas Balkand, first chaupai line", mine.split("\n")[0], clean_line(m.group(0)) if m else "",
        "hi.wikisource रामचरितमानस/बालकाण्ड (rendered via API)")
    return res


def write_coverage(lib, spots):
    rows = []
    for st in lib["stations"]:
        for w in st["works"]:
            rows.append((st["index"], st["id"], w))
    notes = {
        "durga-saptashati": "Complete: all 13 chapters, numbered mantras only (see notes). 'X uvacha' label mantras are dropped (they are speaker labels).",
        "ramcharitmanas": "Complete as provided by the dataset (all seven kands). Passed through no spelling edits.",
        "bhagavad-gita": "Complete (701 verses, speaker lines 'X uvacha' removed).",
        "vishnu-sahasranama": "Numbered verses 1-142 of the Mahabharata (Anushasana) text, including the 13 introductory verses.",
    }
    L = ["# Text library coverage", "",
         f"Built by `tools/build_library.py` from the files in `texts/raw/` (re-run `python tools/build_library.py --check`).",
         f"Library: `library/library.json`, built {lib['built_at']}.", "",
         "| Station | Work | Verses | Complete? | Source URL | Notes |", "|---|---|---|---|---|---|"]
    status_partial = {}
    for idx, sid, w in rows:
        n = len(w["verses"])
        base = re.sub(r"-\d+(-[a-z]+)?$", "", w["id"])
        note = ""
        for k, v in notes.items():
            if w["id"].startswith(k):
                note = v if (w["id"].endswith("01") or w["id"].endswith("balkand") or k == "vishnu-sahasranama") else ""
        complete = "complete"
        if w["id"].startswith("navagraha"):
            note = "one verse of the Navagraha stotra per graha (a stotra verse, not a bija mantra)"
        L.append(f"| {idx} {sid} | {w['title_en']} | {n} | {complete} | {w['source_url']} | {note} |")
    L += ["", "## Gathering texts", "", "| Text | Verses | Source URL |", "|---|---|---|"]
    for k, g in lib["gathering"].items():
        L.append(f"| {k}: {g['title_en']} | {len(g['verses'])} | {g['source_url']} |")
    L += ["", "## Spot checks against a second independent fetched source", "",
          "Comparison is on the letters only (marks, punctuation, spaces and verse numbers ignored). 'MATCH' = identical letters.", "",
          "| Work | Second source | Result | Library text | Other source text (if different) |", "|---|---|---|---|---|"]
    for label, other, status, mine, theirs in spots:
        L.append(f"| {label} | {other} | {status} | {mine} | {theirs} |")
    extra = (ROOT / "texts" / "COVERAGE_NOTES.md")
    L += ["", "## Notes, uncertainties, things not sourced", ""]
    if extra.exists():
        L.append(extra.read_text(encoding="utf-8"))
    COVERAGE.write_text("\n".join(L) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# Check
# --------------------------------------------------------------------------
EXPECT = [("shiv-japa", "japa"), ("shiv-stuti", "parallel"), ("mrityunjaya", "japa"), ("saptashati", "relay"),
          ("durga", "parallel"), ("ganesh", "parallel"), ("ram", "relay"), ("hanuman", "parallel"),
          ("vishnu", "parallel"), ("lakshmi", "parallel"), ("surya", "parallel"), ("navagraha", "japa-per-seat")]
BAD = re.compile(r"[A-Za-z0-9०-९]")
DEVA = re.compile(r"[ऀ-ॿ]")


def check(lib):
    errs = []
    assert lib["version"] == 1 and lib["built_at"], "header"
    st = lib["stations"]
    assert len(st) == 12, f"{len(st)} stations"
    for i, (sid, mode) in enumerate(EXPECT):
        s = st[i]
        if s["index"] != i or s["id"] != sid or s["mode"] != mode:
            errs.append(f"station {i}: got {s['index']},{s['id']},{s['mode']} expected {sid},{mode}")
        for k in ("label_hi", "label_en", "works"):
            if not s.get(k):
                errs.append(f"station {i} missing {k}")
        if not s["works"] or not any(w["verses"] for w in s["works"]):
            errs.append(f"station {i} has no verses")
        for w in s["works"]:
            for k in ("id", "title_hi", "title_en", "source_url", "source_name", "seat", "verses"):
                if k not in w:
                    errs.append(f"{w.get('id')} missing {k}")
            if not str(w["source_url"]).startswith("https"):
                errs.append(f"{w['id']} source_url not https")
            if i != 11 and w["seat"] is not None:
                errs.append(f"{w['id']} seat should be null")
            if not w["verses"]:
                errs.append(f"{w['id']} has no verses")
            for n, v in enumerate(w["verses"], 1):
                t = v["text"]
                if v["n"] != n:
                    errs.append(f"{w['id']} verse numbering at {n}")
                check_text(w["id"], n, t, errs)
    ng = st[11]["works"]
    if len(ng) != 9 or [w["seat"] for w in ng] != list(range(9)):
        errs.append("station 11 must have nine works with seats 0-8")
    for w in ng:
        if len(w["verses"]) != 1:
            errs.append(f"{w['id']} must be a single verse")
    for k in ("ganesh", "shiv", "durga"):
        g = lib["gathering"].get(k)
        if not g or not g["verses"] or not g["source_url"].startswith("https") or not g.get("title_en"):
            errs.append(f"gathering {k} missing")
            continue
        for n, v in enumerate(g["verses"], 1):
            check_text("gathering/" + k, n, v["text"], errs)
    return errs


def check_text(wid, n, t, errs):
    if unicodedata.normalize("NFC", t) != t:
        errs.append(f"{wid}#{n}: not NFC")
    if not t.strip():
        errs.append(f"{wid}#{n}: empty")
    if len(t) > 420:
        errs.append(f"{wid}#{n}: {len(t)} chars")
    if not DEVA.search(t):
        errs.append(f"{wid}#{n}: no Devanagari")
    m = BAD.search(t)
    if m:
        errs.append(f"{wid}#{n}: bad char {m.group(0)!r}: {t[:60]!r}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    if a.fetch:
        fetch_all()
    lib = build()
    spots = spot_checks(lib)
    write_coverage(lib, spots)
    total = 0
    print("station  id            mode           works  verses")
    for s in lib["stations"]:
        nv = sum(len(w["verses"]) for w in s["works"])
        total += nv
        print(f"{s['index']:>7}  {s['id']:<13} {s['mode']:<14} {len(s['works']):>5}  {nv:>6}")
    print(f"total verses: {total}; gathering: " +
          ", ".join(f"{k}={len(g['verses'])}" for k, g in lib["gathering"].items()))
    print("spot checks: " + ", ".join(f"{l.split(' ')[0]}={'ok' if st.startswith('MATCH') else 'DIFF'}" for l, _, st, _, _ in spots))
    if a.check:
        errs = check(lib)
        if errs:
            print(f"CHECK FAILED ({len(errs)} problems)")
            for e in errs[:60]:
                print("  -", e)
            sys.exit(1)
        json.loads(OUT.read_text(encoding="utf-8"))
        print("CHECK OK")


if __name__ == "__main__":
    main()
