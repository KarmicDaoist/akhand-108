"""(1) Verification accepts the exact verse with cosmetic differences and rejects a changed word."""
from akhand.parse import VerseStream
from akhand.verify import normalize

V1 = "नमामीशमीशान निर्वाणरूपं विभुं व्यापकं ब्रह्मवेदस्वरूपम्।\nनिजं निर्गुणं निर्विकल्पं निरीहं चिदाकाशमाकाशवासं भजेऽहम्॥"
V2 = "ॐ नमः शिवाय"
V3 = "क्षमा करो प्रभु ज़रा सा"  # includes a nukta consonant


def test_cosmetic_differences_are_accepted():
    want = normalize(V1)
    assert normalize(V1.replace("।\n", "। ").replace("॥", "॥ १ ॥")) == want  # dandas, verse number, newline
    assert normalize("१. " + V1 + "  ") == want  # numbering and spaces
    assert normalize("क्‍ष") == normalize("क्‌ष") == normalize("क्ष")  # ZWJ/ZWNJ never decide equality
    assert normalize(V3.replace("ज़", "ज़")) == normalize(V3)  # precomposed vs decomposed nukta


def test_changed_word_or_letter_is_rejected():
    want = normalize(V1)
    assert normalize(V1.replace("भजेऽहम्", "भजामहम्")) != want
    assert normalize("नमः शिवाय ं") == normalize("नमः शिवाय ं") != normalize("नमः शिवाय ँ")  # anusvara vs candrabindu
    assert normalize("ॐ नमः शिवाय") != normalize("ॐ नमः शिवाया")
    assert normalize("ॐ त्र्यम्बकं") != normalize("ॐ त्र्यंबकं")  # anusvara vs conjunct: different spelling, strict


def _run(reply, expected, chunk=3, clean=True):
    p = VerseStream([normalize(e) for e in expected])
    got = []
    for i in range(0, len(reply), chunk):
        got += p.feed(reply[i:i + chunk])
    got += p.finish(clean)
    return p, got


def test_stream_counts_only_exact_verses_and_ignores_the_rest():
    exp = [V1, V2, V3]
    reply = f"Here is the recitation:\n\n1. {V1}\n\nSome commentary line\n\n{V2}\n\n{V3}\n\nHope this helps!"
    p, got = _run(reply, exp)
    assert got == [0, 1, 2] and not p.mismatched
    # no blank lines between verses, label glued to a verse
    p, got = _run(f"श्लोक 1:\n{V1}\n{V2}\n{V3}", exp, chunk=7)
    assert got == [0, 1, 2]


def test_wrong_word_stops_counting_and_never_skips_ahead():
    exp = [V1, V2, V3]
    wrong = V2.replace("शिवाय", "शंकराय")
    p, got = _run(f"{V1}\n\n{wrong}\n\n{V3}", exp)
    assert got == [0] and p.mismatched  # V3 is not counted although it is correct: order is strict


def test_cut_off_stream_does_not_count_a_partial_verse():
    p, got = _run(V1 + "\n\n" + V2[:6], [V1, V2], clean=False)
    assert got == [0] and not p.mismatched
