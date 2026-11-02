import pytest

from materials import domain
from partners.domain.entities import PlacementStatus
from partners.domain.matching import TextMatcher

T = domain.MATERIAL_TRANSITIONS


def test_happy_path_states():
    s = domain.next_state(T, "texts_ready", "preparing", system=True)
    assert s == "on_approval"
    s = domain.next_state(T, "approve", s, "admin")
    assert s == "scheduled"
    assert domain.next_state(T, "published", s, system=True) == "published"


def test_forbidden_transitions():
    with pytest.raises(domain.TransitionError):
        domain.next_state(T, "approve", "preparing", "director")      # тексты ещё не готовы
    with pytest.raises(domain.TransitionError):
        domain.next_state(T, "texts_ready", "preparing", "admin")     # это делает система
    with pytest.raises(domain.TransitionError):
        domain.next_state(T, "approve", "on_approval", None)          # нет роли


def test_approval_basis_rules():
    domain.check_approval_basis("director", "director")
    domain.check_approval_basis("admin", "oral")
    domain.check_approval_basis("admin", "none")
    with pytest.raises(domain.TransitionError):
        domain.check_approval_basis("admin", "director")              # только директор
    with pytest.raises(domain.TransitionError):
        domain.check_approval_basis("director", "oral")


ORIGINAL = ("Команда технопарка Исток примет участие в международной битве роботов. Наши ребята "
            "представят нового робота RedHorn 3, созданного при поддержке партнёра АО НПП Исток.")


def test_matcher_exact_copy_confirmed():
    r = TextMatcher().compare(ORIGINAL, ORIGINAL)
    assert r.similarity == 1.0 and r.decision == PlacementStatus.CONFIRMED


def test_matcher_inside_longer_note_uses_coverage():
    longer = "Новости школы. " + ORIGINAL + " " + " ".join(f"слово{i}" for i in range(200))
    r = TextMatcher().compare(ORIGINAL, longer)
    assert r.jaccard < 0.35 and r.coverage > 0.9
    assert r.decision == PlacementStatus.CONFIRMED


def test_matcher_normalizes_yo_and_case():
    variant = ORIGINAL.upper().replace("Е", "Ё")
    assert TextMatcher().compare(ORIGINAL, variant).similarity == 1.0


def test_matcher_unrelated_not_found():
    r = TextMatcher().compare(ORIGINAL, "Сегодня в школе прошёл праздник осени, ученики читали стихи и пели песни.")
    assert r.decision == PlacementStatus.EXPECTED


def test_matcher_rewritten_is_disputed():
    words = ORIGINAL.split()
    half = " ".join(words[: len(words) // 2]) + " и это было здорово, все ждут результатов соревнований очень"
    r = TextMatcher().compare(ORIGINAL, half)
    assert r.decision in (PlacementStatus.DISPUTED, PlacementStatus.CONFIRMED)
    assert 0.35 <= r.similarity
