"""Hindu panchang for a place and a local date, stdlib only.

Everything is read at local sunrise (the Hindu day runs sunrise to sunrise): vara, tithi, paksha, nakshatra,
lunar masa in both systems (with adhika), ritu, ayana, Vikram and Shaka year, samvatsara.

Astronomy: Sun from Meeus ch. 25 (low accuracy, 0.01 deg), Moon from the main terms of Meeus ch. 47
(about 0.01 deg, i.e. a minute or two of tithi/nakshatra timing). Longitudes are referred to the mean equinox of
date and the Lahiri (Chitrapaksha) ayanamsa is subtracted for the sidereal values. Sunrise is the upper limb
with standard refraction (34'), at sea level.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date as Date, datetime, timedelta, tzinfo

NEAR_BOUNDARY_MIN = 15.0
SYNODIC_DAYS = 29.530588
MEAN_ELONG_RATE = 12.1907  # degrees per day, Moon minus Sun

# ---- names ---------------------------------------------------------------------------------------------------------
# Plain names follow the usual transliteration (the spelling drikpanchang.com uses).
TITHI_EN = ("Pratipada", "Dwitiya", "Tritiya", "Chaturthi", "Panchami", "Shashthi", "Saptami", "Ashtami", "Navami",
            "Dashami", "Ekadashi", "Dwadashi", "Trayodashi", "Chaturdashi")  # 15th: Purnima / Amavasya
TITHI_HI = ("प्रतिपदा", "द्वितीया", "तृतीया", "चतुर्थी", "पञ्चमी", "षष्ठी", "सप्तमी", "अष्टमी", "नवमी", "दशमी",
            "एकादशी", "द्वादशी", "त्रयोदशी", "चतुर्दशी")
TITHI_LOC = ("प्रतिपदायां", "द्वितीयायां", "तृतीयायां", "चतुर्थ्यां", "पञ्चम्यां", "षष्ठ्यां", "सप्तम्यां", "अष्टम्यां",
             "नवम्यां", "दशम्यां", "एकादश्यां", "द्वादश्यां", "त्रयोदश्यां", "चतुर्दश्यां")
PURNIMA = ("Purnima", "पूर्णिमा", "पौर्णमास्यां")
AMAVASYA = ("Amavasya", "अमावस्या", "अमावास्यायां")

PAKSHA = {"shukla": ("Shukla", "शुक्लपक्ष", "शुक्लपक्षे"), "krishna": ("Krishna", "कृष्णपक्ष", "कृष्णपक्षे")}

# index = date.weekday(): Monday first
VARA_EN = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
VARA_HI = ("सोमवार", "मङ्गलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार")
VARA_LOC = ("सोमवासरे", "मङ्गलवासरे", "बुधवासरे", "गुरुवासरे", "शुक्रवासरे", "शनिवासरे", "रविवासरे")

NAKSHATRA_EN = ("Ashwini", "Bharani", "Krittika", "Rohini", "Mrigashira", "Ardra", "Punarvasu", "Pushya", "Ashlesha",
                "Magha", "Purva Phalguni", "Uttara Phalguni", "Hasta", "Chitra", "Swati", "Vishakha", "Anuradha",
                "Jyeshtha", "Mula", "Purva Ashadha", "Uttara Ashadha", "Shravana", "Dhanishtha", "Shatabhisha",
                "Purva Bhadrapada", "Uttara Bhadrapada", "Revati")
NAKSHATRA_HI = ("अश्विनी", "भरणी", "कृत्तिका", "रोहिणी", "मृगशिरा", "आर्द्रा", "पुनर्वसु", "पुष्य", "आश्लेषा", "मघा",
                "पूर्वफाल्गुनी", "उत्तरफाल्गुनी", "हस्त", "चित्रा", "स्वाती", "विशाखा", "अनुराधा", "ज्येष्ठा", "मूला",
                "पूर्वाषाढा", "उत्तराषाढा", "श्रवण", "धनिष्ठा", "शतभिषा", "पूर्वभाद्रपदा", "उत्तरभाद्रपदा", "रेवती")

# index 0 = Chaitra
MASA_EN = ("Chaitra", "Vaishakha", "Jyeshtha", "Ashadha", "Shravana", "Bhadrapada", "Ashwina", "Kartika",
           "Margashirsha", "Pausha", "Magha", "Phalguna")
MASA_HI = ("चैत्र", "वैशाख", "ज्येष्ठ", "आषाढ", "श्रावण", "भाद्रपद", "आश्विन", "कार्तिक", "मार्गशीर्ष", "पौष", "माघ",
           "फाल्गुन")
ADHIKA_HI = "अधिक"

# index = amanta masa // 2
RITU_EN = ("Vasanta", "Grishma", "Varsha", "Sharad", "Hemanta", "Shishira")
RITU_HI = ("वसन्त", "ग्रीष्म", "वर्षा", "शरद्", "हेमन्त", "शिशिर")
RITU_LOC = ("वसन्तऋतौ", "ग्रीष्मऋतौ", "वर्षाऋतौ", "शरदृतौ", "हेमन्तऋतौ", "शिशिरऋतौ")

AYANA = {"uttarayana": ("Uttarayana", "उत्तरायण", "उत्तरायणे"), "dakshinayana": ("Dakshinayana", "दक्षिणायन", "दक्षिणायने")}

# 60-year cycle, 1 = Prabhava
SAMVATSARA_EN = (
    "Prabhava", "Vibhava", "Shukla", "Pramoda", "Prajapati", "Angira", "Shrimukha", "Bhava", "Yuva", "Dhata",
    "Ishvara", "Bahudhanya", "Pramathi", "Vikrama", "Vrisha", "Chitrabhanu", "Subhanu", "Tarana", "Parthiva", "Vyaya",
    "Sarvajit", "Sarvadhari", "Virodhi", "Vikrita", "Khara", "Nandana", "Vijaya", "Jaya", "Manmatha", "Durmukha",
    "Hemalambi", "Vilambi", "Vikari", "Sharvari", "Plava", "Shubhakrit", "Shobhakrit", "Krodhi", "Vishvavasu",
    "Parabhava", "Plavanga", "Kilaka", "Saumya", "Sadharana", "Virodhikrit", "Paridhavi", "Pramadi", "Ananda",
    "Rakshasa", "Nala", "Pingala", "Kalayukta", "Siddharthi", "Raudra", "Durmati", "Dundubhi", "Rudhirodgari",
    "Raktakshi", "Krodhana", "Akshaya")
SAMVATSARA_HI = (
    "प्रभव", "विभव", "शुक्ल", "प्रमोद", "प्रजापति", "अङ्गिरा", "श्रीमुख", "भाव", "युवा", "धाता",
    "ईश्वर", "बहुधान्य", "प्रमाथी", "विक्रम", "वृष", "चित्रभानु", "सुभानु", "तारण", "पार्थिव", "व्यय",
    "सर्वजित्", "सर्वधारी", "विरोधी", "विकृति", "खर", "नन्दन", "विजय", "जय", "मन्मथ", "दुर्मुख",
    "हेमलम्बी", "विलम्बी", "विकारी", "शार्वरी", "प्लव", "शुभकृत्", "शोभकृत्", "क्रोधी", "विश्वावसु",
    "पराभव", "प्लवङ्ग", "कीलक", "सौम्य", "साधारण", "विरोधकृत्", "परिधावी", "प्रमादी", "आनन्द",
    "राक्षस", "नल", "पिङ्गल", "कालयुक्त", "सिद्धार्थी", "रौद्र", "दुर्मति", "दुन्दुभि", "रुधिरोद्गारी",
    "रक्ताक्षी", "क्रोधन", "अक्षय")

DIGIT_WORDS = ("शून्य", "एक", "द्वि", "त्रि", "चतुः", "पञ्च", "षट्", "सप्त", "अष्ट", "नव")

MASA_SYSTEMS = ("purnimanta", "amanta")
SAMVATSARA_SYSTEMS = ("north", "south")
AYANA_SYSTEMS = ("drik", "sidereal")


# ---- astronomy -----------------------------------------------------------------------------------------------------
def _rad(x: float) -> float:
    return math.radians(x)


def _norm(x: float) -> float:
    return x % 360.0


def _wrap180(x: float) -> float:
    return (x + 180.0) % 360.0 - 180.0


def jd_from_datetime(dt: datetime) -> float:
    """Julian day (UT) of an aware datetime."""
    return dt.timestamp() / 86400.0 + 2440587.5


def datetime_from_jd(jd: float, tz: tzinfo) -> datetime:
    return datetime.fromtimestamp((jd - 2440587.5) * 86400.0, tz)


def delta_t(jd_ut: float) -> float:
    """TT - UT in seconds (Espenak and Meeus polynomial for 2005-2050, adequate nearby)."""
    t = (jd_ut - 2451545.0) / 365.25
    return 62.92 + 0.32217 * t + 0.005589 * t * t


def _tt(jd_ut: float) -> float:
    return jd_ut + delta_t(jd_ut) / 86400.0


def sun_longitude(jd_tt: float) -> float:
    """Tropical longitude of the Sun, mean equinox of date, aberration included (Meeus 25)."""
    t = (jd_tt - 2451545.0) / 36525.0
    l0 = 280.46646 + 36000.76983 * t + 0.0003032 * t * t
    m = _rad(357.52911 + 35999.05029 * t - 0.0001537 * t * t)
    c = ((1.914602 - 0.004817 * t - 0.000014 * t * t) * math.sin(m) + (0.019993 - 0.000101 * t) * math.sin(2 * m)
         + 0.000289 * math.sin(3 * m))
    return _norm(l0 + c - 0.00569)


# (D, M, M', F, longitude coefficient in 1e-6 degree): Meeus table 47.A, terms above 250e-6 degree
_MOON_LON = (
    (0, 0, 1, 0, 6288774), (2, 0, -1, 0, 1274027), (2, 0, 0, 0, 658314), (0, 0, 2, 0, 213618), (0, 1, 0, 0, -185116),
    (0, 0, 0, 2, -114332), (2, 0, -2, 0, 58793), (2, -1, -1, 0, 57066), (2, 0, 1, 0, 53322), (2, -1, 0, 0, 45758),
    (0, 1, -1, 0, -40923), (1, 0, 0, 0, -34720), (0, 1, 1, 0, -30383), (2, 0, 0, -2, 15327), (0, 0, 1, 2, -12528),
    (0, 0, 1, -2, 10980), (4, 0, -1, 0, 10675), (0, 0, 3, 0, 10034), (4, 0, -2, 0, 8548), (2, 1, -1, 0, -7888),
    (2, 1, 0, 0, -6766), (1, 0, -1, 0, -5163), (1, 1, 0, 0, 4987), (2, -1, 1, 0, 4036), (2, 0, 2, 0, 3994),
    (4, 0, 0, 0, 3861), (2, 0, -3, 0, 3665), (0, 1, -2, 0, -2689), (2, 0, -1, 2, -2602), (2, -1, -2, 0, 2390),
    (1, 0, 1, 0, -2348), (2, -2, 0, 0, 2236), (0, 1, 2, 0, -2120), (0, 2, 0, 0, -2069), (2, -2, -1, 0, 2048),
    (2, 0, 1, -2, -1773), (2, 0, 0, 2, -1595), (4, -1, -1, 0, 1215), (0, 0, 2, 2, -1110), (3, 0, -1, 0, -892),
    (2, 1, 1, 0, -810), (4, -1, -2, 0, 759), (0, 2, -1, 0, -713), (2, 2, -1, 0, -700), (2, 1, -2, 0, 691),
    (2, -1, 0, -2, 596), (4, 0, 1, 0, 549), (0, 0, 4, 0, 537), (4, -1, 0, 0, 520), (1, 0, -2, 0, -487),
    (2, 1, 0, -2, -399), (0, 0, 2, -2, -381), (1, 1, 1, 0, 351), (3, 0, -2, 0, -340), (4, 0, -3, 0, 330),
    (2, -1, 2, 0, 327), (0, 2, 1, 0, -323), (1, 1, -1, 0, 299), (2, 0, 3, 0, 294),
)


def moon_longitude(jd_tt: float) -> float:
    """Tropical longitude of the Moon, mean equinox of date (Meeus 47, main terms)."""
    t = (jd_tt - 2451545.0) / 36525.0
    t2, t3, t4 = t * t, t ** 3, t ** 4
    lp = 218.3164477 + 481267.88123421 * t - 0.0015786 * t2 + t3 / 538841.0 - t4 / 65194000.0
    d = 297.8501921 + 445267.1114034 * t - 0.0018819 * t2 + t3 / 545868.0 - t4 / 113065000.0
    m = 357.5291092 + 35999.0502909 * t - 0.0001536 * t2 + t3 / 24490000.0
    mp = 134.9633964 + 477198.8675055 * t + 0.0087414 * t2 + t3 / 69699.0 - t4 / 14712000.0
    f = 93.2720950 + 483202.0175233 * t - 0.0036539 * t2 - t3 / 3526000.0 + t4 / 863310000.0
    e = 1.0 - 0.002516 * t - 0.0000074 * t2
    s = 0.0
    for cd, cm, cmp_, cf, coef in _MOON_LON:
        arg = _rad(cd * d + cm * m + cmp_ * mp + cf * f)
        s += coef * (e ** abs(cm)) * math.sin(arg)
    s += 3958 * math.sin(_rad(119.75 + 131.849 * t)) + 1962 * math.sin(_rad(lp - f)) + 318 * math.sin(_rad(53.09 + 479264.290 * t))
    return _norm(lp + s / 1e6)


def lahiri_ayanamsa(jd_tt: float) -> float:
    """Lahiri (Chitrapaksha) ayanamsa in degrees: 23 deg 51' 11\" at J2000 plus general precession in longitude."""
    t = (jd_tt - 2451545.0) / 36525.0
    return 23.85306 + (5028.796195 * t + 1.1054348 * t * t) / 3600.0


def sidereal(lon_tropical: float, jd_tt: float) -> float:
    return _norm(lon_tropical - lahiri_ayanamsa(jd_tt))


def elongation(jd_tt: float) -> float:
    return _norm(moon_longitude(jd_tt) - sun_longitude(jd_tt))


def _gmst(jd_ut: float) -> float:
    t = (jd_ut - 2451545.0) / 36525.0
    return _norm(280.46061837 + 360.98564736629 * (jd_ut - 2451545.0) + 0.000387933 * t * t - t ** 3 / 38710000.0)


def sun_altitude(jd_ut: float, lat: float, lon: float) -> float:
    """Altitude of the Sun's centre in degrees (no refraction)."""
    jd = _tt(jd_ut)
    t = (jd - 2451545.0) / 36525.0
    lam = _rad(sun_longitude(jd))
    eps = _rad(23.439291 - 0.0130042 * t)
    ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))
    dec = math.asin(math.sin(eps) * math.sin(lam))
    h = _rad(_gmst(jd_ut) + lon) - ra
    phi = _rad(lat)
    return math.degrees(math.asin(math.sin(phi) * math.sin(dec) + math.cos(phi) * math.cos(dec) * math.cos(h)))


def sunrise(day: Date, lat: float, lon: float, tz: tzinfo) -> datetime:
    """Local sunrise (upper limb, refraction 34') as an aware datetime; ValueError when the Sun does not rise."""
    h0 = -(34.0 + 16.0) / 60.0
    midnight = datetime(day.year, day.month, day.day, tzinfo=tz)
    lo = jd_from_datetime(midnight)
    hi = lo + 0.5
    if not (sun_altitude(lo, lat, lon) < h0 < sun_altitude(hi, lat, lon)):
        raise ValueError(f"no sunrise at lat {lat} on {day}")
    for _ in range(50):
        mid = (lo + hi) / 2
        if sun_altitude(mid, lat, lon) < h0:
            lo = mid
        else:
            hi = mid
    return datetime_from_jd((lo + hi) / 2, tz)


def _new_moon_near(jd_tt: float) -> float:
    t = jd_tt
    for _ in range(12):
        step = _wrap180(elongation(t)) / MEAN_ELONG_RATE
        t -= step
        if abs(step) < 1e-6:
            break
    return t


def new_moons_around(jd_tt: float) -> tuple[float, float, float]:
    """(previous, next, the one after next) new moon as TT Julian days, previous <= jd_tt < next."""
    n0 = _new_moon_near(jd_tt - elongation(jd_tt) / MEAN_ELONG_RATE)
    if n0 > jd_tt:  # the guess landed on the following conjunction
        n0 = _new_moon_near(n0 - SYNODIC_DAYS)
    n1 = _new_moon_near(n0 + SYNODIC_DAYS)
    n2 = _new_moon_near(n1 + SYNODIC_DAYS)
    return n0, n1, n2


def lunar_month(jd_tt: float) -> tuple[int, bool, float, float]:
    """Amanta month at an instant: (index with 0 = Chaitra, adhika, start new moon, end new moon).

    The month is named after the sidereal sign the Sun is in at its starting new moon (Meena -> Chaitra, Mesha ->
    Vaishakha, ...). If the Sun is still in the same sign at the ending new moon there was no sankranti: adhika."""
    n0, n1, _ = new_moons_around(jd_tt)
    r0 = int(sidereal(sun_longitude(n0), n0) // 30)
    r1 = int(sidereal(sun_longitude(n1), n1) // 30)
    return (r0 + 1) % 12, r0 == r1, n0, n1


def _minutes_to_boundary(jd_tt: float, value_of, width: float) -> float:
    """Minutes until `value_of` (degrees, rising) next crosses or last crossed a multiple of `width`, whichever is nearer."""
    h = 1.0 / 24.0
    v = value_of(jd_tt)
    rate = _wrap180(value_of(jd_tt + h) - value_of(jd_tt - h)) / (2 * h)  # degrees per day
    r = v % width
    return min(r, width - r) / rate * 1440.0


# ---- the result ----------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Panchang:
    date: Date
    lat: float
    lon: float
    sunrise: datetime
    vara: int  # date.weekday(): 0 = Monday
    tithi: int  # 1-30
    paksha: str  # "shukla" | "krishna"
    nakshatra: int  # 1-27
    masa_amanta: int  # 0 = Chaitra
    adhika_amanta: bool
    masa_purnimanta: int
    adhika_purnimanta: bool
    masa_system: str
    ritu: int  # 0 = Vasanta
    ayana: str  # "uttarayana" | "dakshinayana"
    ayana_system: str
    vikram_year: int
    shaka_year: int
    samvatsara: int  # 1-60
    samvatsara_system: str
    near_boundary: bool
    minutes_to_tithi_change: float  # distance to the nearest tithi boundary (before or after sunrise)
    minutes_to_nakshatra_change: float
    sun_sidereal: float
    moon_sidereal: float

    # selected masa system
    @property
    def masa(self) -> int:
        return self.masa_purnimanta if self.masa_system == "purnimanta" else self.masa_amanta

    @property
    def adhika(self) -> bool:
        return self.adhika_purnimanta if self.masa_system == "purnimanta" else self.adhika_amanta

    @property
    def tithi_in_paksha(self) -> int:
        return (self.tithi - 1) % 15 + 1

    def _tithi_name(self, k: int) -> str:
        n = self.tithi_in_paksha
        if n == 15:
            return (PURNIMA if self.paksha == "shukla" else AMAVASYA)[k]
        return (TITHI_EN, TITHI_HI, TITHI_LOC)[k][n - 1]

    def _masa_en(self, idx: int, adhika: bool) -> str:
        return ("Adhika " if adhika else "") + MASA_EN[idx]

    def plain(self) -> dict:
        """Plain English names."""
        return {
            "vara": VARA_EN[self.vara], "tithi": self._tithi_name(0), "tithi_number": self.tithi,
            "paksha": PAKSHA[self.paksha][0], "nakshatra": NAKSHATRA_EN[self.nakshatra - 1],
            "masa": self._masa_en(self.masa, self.adhika),
            "masa_amanta": self._masa_en(self.masa_amanta, self.adhika_amanta),
            "masa_purnimanta": self._masa_en(self.masa_purnimanta, self.adhika_purnimanta),
            "ritu": RITU_EN[self.ritu], "ayana": AYANA[self.ayana][0],
            "vikram_year": self.vikram_year, "shaka_year": self.shaka_year,
            "samvatsara": SAMVATSARA_EN[self.samvatsara - 1],
        }

    def devanagari(self) -> dict:
        """Devanagari names as the sankalpa needs them (locative forms); samvatsara is the bare name."""
        masa = (ADHIKA_HI if self.adhika else "") + MASA_HI[self.masa]
        return {
            "samvatsara": SAMVATSARA_HI[self.samvatsara - 1],
            "vikram_year": number_words(self.vikram_year),
            "ayana": AYANA[self.ayana][2],
            "ritu": RITU_LOC[self.ritu],
            "masa": masa + "मासे",
            "paksha": PAKSHA[self.paksha][2],
            "tithi": self._tithi_name(2) + " तिथौ",
            "vara": VARA_LOC[self.vara],
            "nakshatra": NAKSHATRA_HI[self.nakshatra - 1] + "नक्षत्रे",
        }

    def devanagari_plain(self) -> dict:
        """Devanagari names without case endings."""
        return {
            "vara": VARA_HI[self.vara], "tithi": self._tithi_name(1), "paksha": PAKSHA[self.paksha][1],
            "nakshatra": NAKSHATRA_HI[self.nakshatra - 1],
            "masa": (ADHIKA_HI if self.adhika else "") + MASA_HI[self.masa],
            "ritu": RITU_HI[self.ritu], "ayana": AYANA[self.ayana][1],
            "samvatsara": SAMVATSARA_HI[self.samvatsara - 1],
        }


def number_words(n: int) -> str:
    """A number read digit by digit in Sanskrit words (the verifier ignores digits, so never write them as digits)."""
    return " ".join(DIGIT_WORDS[int(c)] for c in str(n))


def _sanitise(name: str, value: str, allowed: tuple) -> str:
    v = value.strip().lower()
    if v not in allowed:
        raise ValueError(f"{name} must be one of {', '.join(allowed)}, not {value!r}")
    return v


def compute(day: Date, lat: float, lon: float, tz: tzinfo, *, masa_system: str = "purnimanta",
            samvatsara_system: str = "north", ayana_system: str = "drik") -> Panchang:
    """The panchang at local sunrise of `day` at (lat north, lon east) in timezone `tz`.

    masa_system: "purnimanta" (month ends at full moon; the dark fortnight takes the next month's name) or "amanta".
    samvatsara_system: "north" (Vikram year + 10, mod 60) or "south" (Shaka year + 12, mod 60).
    ayana_system: "drik" (tropical solstices, the sankalpa convention) or "sidereal" (Makara and Karka sankranti)."""
    masa_system = _sanitise("masa_system", masa_system, MASA_SYSTEMS)
    samvatsara_system = _sanitise("samvatsara_system", samvatsara_system, SAMVATSARA_SYSTEMS)
    ayana_system = _sanitise("ayana_system", ayana_system, AYANA_SYSTEMS)
    rise = sunrise(day, lat, lon, tz)
    jd_ut = jd_from_datetime(rise)
    jd = _tt(jd_ut)
    sun_t, moon_t = sun_longitude(jd), moon_longitude(jd)
    sun_s, moon_s = sidereal(sun_t, jd), sidereal(moon_t, jd)
    elong = _norm(moon_t - sun_t)
    tithi = int(elong // 12) + 1
    paksha = "shukla" if tithi <= 15 else "krishna"
    nakshatra = int(moon_s // (360.0 / 27)) + 1

    m_idx, m_adhika, _, _ = lunar_month(jd)
    if m_adhika:  # an adhika month keeps its own name for both fortnights in either system
        p_idx, p_adhika = m_idx, True
    elif paksha == "krishna":  # purnimanta: the dark fortnight already belongs to the next amanta month
        p_idx, p_adhika = (m_idx + 1) % 12, False
    else:
        p_idx, p_adhika = m_idx, False

    lon_for_ayana = sun_t if ayana_system == "drik" else sun_s
    ayana = "uttarayana" if (lon_for_ayana >= 270.0 or lon_for_ayana < 90.0) else "dakshinayana"

    # The year turns at the new moon that starts Chaitra. Published panchangs show the new year for the whole civil
    # day on which that new moon falls, so judge it at the end of the day, not at sunrise.
    end_of_day = datetime(day.year, day.month, day.day, tzinfo=tz) + timedelta(days=1)
    y_idx, y_adhika, _, _ = lunar_month(_tt(jd_from_datetime(end_of_day)) - 1e-4)
    year_turned = not (day.month <= 4 and y_idx >= 8) and not (y_idx == 0 and y_adhika and day.month <= 4)
    vikram = day.year + (57 if year_turned else 56)
    shaka = vikram - 135
    samvatsara = ((vikram + 10) if samvatsara_system == "north" else (shaka + 12)) % 60 or 60

    tithi_min = _minutes_to_boundary(jd, elongation, 12.0)
    nak_min = _minutes_to_boundary(jd, lambda t: sidereal(moon_longitude(t), t), 360.0 / 27)
    return Panchang(
        date=day, lat=lat, lon=lon, sunrise=rise, vara=day.weekday(), tithi=tithi, paksha=paksha, nakshatra=nakshatra,
        masa_amanta=m_idx, adhika_amanta=m_adhika, masa_purnimanta=p_idx, adhika_purnimanta=p_adhika,
        masa_system=masa_system, ritu=m_idx // 2, ayana=ayana, ayana_system=ayana_system, vikram_year=vikram,
        shaka_year=shaka, samvatsara=samvatsara, samvatsara_system=samvatsara_system,
        near_boundary=tithi_min < NEAR_BOUNDARY_MIN or nak_min < NEAR_BOUNDARY_MIN,
        minutes_to_tithi_change=round(tithi_min, 1), minutes_to_nakshatra_change=round(nak_min, 1),
        sun_sidereal=sun_s, moon_sidereal=moon_s)
