"""Panchang correctness against a published panchang.

Reference values: drikpanchang.com day panchang for Raipur, Chhattisgarh, India (21 deg 14' N, 81 deg 38' E,
Asia/Kolkata), one page per date, fetched 2026-11-13:
https://www.drikpanchang.com/panchang/day-panchang.html?geoname-id=1258980&date=DD/MM/YYYY
Each row is what that page shows at sunrise: the sunrise time, tithi, paksha, nakshatra, the lunar month in the
Purnimanta and Amanta systems ("Adhika" prefix = Adhik), Vikram Samvat year and its samvatsara name, Vedic Ritu, Drik
Ayana (tropical solstices) and Vedic Ayana (sidereal sankranti). Dates cover purnima, amavasya, the month change in
both systems (2026-05-02 purnimanta, 2026-05-17 and 2026-06-16 amanta and adhika, 2026-11-10 and 2026-11-25), the
solstice and Makara sankranti ayana changes, the samvatsara change (2027-04-06 -> 04-07; on 2026-03-19 the page already
shows the new year because the Chaitra new moon falls that day, 06:52 IST, an hour after sunrise) and 2026-11-14.
"""
from datetime import date
from zoneinfo import ZoneInfo

import pytest

from akhand import panchang as pj

LAT, LON, TZ = 21.2514, 81.6296, ZoneInfo("Asia/Kolkata")

# (date, sunrise, tithi, paksha, nakshatra, purnimanta, amanta, vikram year, samvatsara, vedic ritu, drik ayana, vedic ayana)
REFERENCE = [
    ("2026-03-18", "06:10", "Chaturdashi", "Krishna", "Purva Bhadrapada", "Chaitra", "Phalguna", 2082, "Kalayukta", "Shishira", "Uttarayana", "Uttarayana"),
    ("2026-03-19", "06:09", "Amavasya", "Krishna", "Uttara Bhadrapada", "Chaitra", "Phalguna", 2083, "Siddharthi", "Shishira", "Uttarayana", "Uttarayana"),
    ("2026-03-20", "06:08", "Dwitiya", "Shukla", "Revati", "Chaitra", "Chaitra", 2083, "Siddharthi", "Vasanta", "Uttarayana", "Uttarayana"),
    ("2026-05-01", "05:33", "Purnima", "Shukla", "Swati", "Vaishakha", "Vaishakha", 2083, "Siddharthi", "Vasanta", "Uttarayana", "Uttarayana"),
    ("2026-05-02", "05:32", "Pratipada", "Krishna", "Vishakha", "Jyeshtha", "Vaishakha", 2083, "Siddharthi", "Vasanta", "Uttarayana", "Uttarayana"),
    ("2026-05-16", "05:25", "Amavasya", "Krishna", "Bharani", "Jyeshtha", "Vaishakha", 2083, "Siddharthi", "Vasanta", "Uttarayana", "Uttarayana"),
    ("2026-05-17", "05:25", "Pratipada", "Shukla", "Krittika", "Adhika Jyeshtha", "Adhika Jyeshtha", 2083, "Siddharthi", "Grishma", "Uttarayana", "Uttarayana"),
    ("2026-06-15", "05:21", "Amavasya", "Krishna", "Mrigashira", "Adhika Jyeshtha", "Adhika Jyeshtha", 2083, "Siddharthi", "Grishma", "Uttarayana", "Uttarayana"),
    ("2026-06-16", "05:22", "Dwitiya", "Shukla", "Ardra", "Jyeshtha", "Jyeshtha", 2083, "Siddharthi", "Grishma", "Uttarayana", "Uttarayana"),
    ("2026-06-21", "05:22", "Saptami", "Shukla", "Purva Phalguni", "Jyeshtha", "Jyeshtha", 2083, "Siddharthi", "Grishma", "Uttarayana", "Uttarayana"),
    ("2026-06-22", "05:23", "Ashtami", "Shukla", "Uttara Phalguni", "Jyeshtha", "Jyeshtha", 2083, "Siddharthi", "Grishma", "Dakshinayana", "Uttarayana"),
    ("2026-08-15", "05:42", "Tritiya", "Shukla", "Uttara Phalguni", "Shravana", "Shravana", 2083, "Siddharthi", "Varsha", "Dakshinayana", "Dakshinayana"),
    ("2026-11-08", "06:10", "Chaturdashi", "Krishna", "Swati", "Kartika", "Ashwina", 2083, "Siddharthi", "Sharad", "Dakshinayana", "Dakshinayana"),
    ("2026-11-09", "06:10", "Amavasya", "Krishna", "Swati", "Kartika", "Ashwina", 2083, "Siddharthi", "Sharad", "Dakshinayana", "Dakshinayana"),
    ("2026-11-10", "06:11", "Pratipada", "Shukla", "Vishakha", "Kartika", "Kartika", 2083, "Siddharthi", "Sharad", "Dakshinayana", "Dakshinayana"),
    ("2026-11-14", "06:13", "Panchami", "Shukla", "Purva Ashadha", "Kartika", "Kartika", 2083, "Siddharthi", "Sharad", "Dakshinayana", "Dakshinayana"),
    ("2026-11-24", "06:20", "Purnima", "Shukla", "Krittika", "Kartika", "Kartika", 2083, "Siddharthi", "Sharad", "Dakshinayana", "Dakshinayana"),
    ("2026-11-25", "06:20", "Pratipada", "Krishna", "Rohini", "Margashirsha", "Kartika", 2083, "Siddharthi", "Sharad", "Dakshinayana", "Dakshinayana"),
    ("2026-12-21", "06:36", "Dwadashi", "Shukla", "Bharani", "Margashirsha", "Margashirsha", 2083, "Siddharthi", "Hemanta", "Dakshinayana", "Dakshinayana"),
    ("2026-12-22", "06:37", "Trayodashi", "Shukla", "Krittika", "Margashirsha", "Margashirsha", 2083, "Siddharthi", "Hemanta", "Uttarayana", "Dakshinayana"),
    ("2027-01-14", "06:43", "Shashthi", "Shukla", "Uttara Bhadrapada", "Pausha", "Pausha", 2083, "Siddharthi", "Hemanta", "Uttarayana", "Dakshinayana"),
    ("2027-01-15", "06:43", "Saptami", "Shukla", "Revati", "Pausha", "Pausha", 2083, "Siddharthi", "Hemanta", "Uttarayana", "Uttarayana"),
    ("2027-02-10", "06:37", "Chaturthi", "Shukla", "Uttara Bhadrapada", "Magha", "Magha", 2083, "Siddharthi", "Shishira", "Uttarayana", "Uttarayana"),
    ("2027-04-06", "05:53", "Amavasya", "Krishna", "Uttara Bhadrapada", "Chaitra", "Phalguna", 2083, "Siddharthi", "Shishira", "Uttarayana", "Uttarayana"),
    ("2027-04-07", "05:52", "Pratipada", "Shukla", "Revati", "Chaitra", "Chaitra", 2084, "Raudra", "Vasanta", "Uttarayana", "Uttarayana"),
    ("2027-05-20", "05:24", "Purnima", "Shukla", "Vishakha", "Vaishakha", "Vaishakha", 2084, "Raudra", "Vasanta", "Uttarayana", "Uttarayana"),
]


@pytest.mark.parametrize("row", REFERENCE, ids=[r[0] for r in REFERENCE])
def test_matches_published_panchang(row):
    d, rise, tithi, paksha, nak, purni, amanta, vs, samv, ritu, drik_ayana, vedic_ayana = row
    p = pj.compute(date.fromisoformat(d), LAT, LON, TZ)
    pl = p.plain()
    got = (pl["tithi"], pl["paksha"], pl["nakshatra"], pl["masa_purnimanta"], pl["masa_amanta"], p.vikram_year,
           pl["samvatsara"], pl["ritu"], pl["ayana"])
    assert got == (tithi, paksha, nak, purni, amanta, vs, samv, ritu, drik_ayana), d
    assert pj.compute(date.fromisoformat(d), LAT, LON, TZ, ayana_system="sidereal").plain()["ayana"] == vedic_ayana
    h, m = map(int, rise.split(":"))
    assert abs((p.sunrise.hour * 60 + p.sunrise.minute) - (h * 60 + m)) <= 2, f"sunrise {p.sunrise:%H:%M} vs {rise}"


def test_south_samvatsara_and_weekday():
    p = pj.compute(date(2026, 11, 14), LAT, LON, TZ, samvatsara_system="south")
    # same page: Shaka Samvat 1948 Parabhava; 14 Nov 2026 is a Saturday; 2027-04-07 page: Shaka 1949 Plavanga
    assert (p.shaka_year, p.plain()["samvatsara"], p.plain()["vara"]) == (1948, "Parabhava", "Saturday")
    assert pj.compute(date(2027, 4, 7), LAT, LON, TZ, samvatsara_system="south").plain()["samvatsara"] == "Plavanga"
