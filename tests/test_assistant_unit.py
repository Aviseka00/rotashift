from datetime import date, timedelta

from app.assistant_llm import _parse_llm_json
from app.routers.assistant_api import _date_window, _person_name_hits


def test_assistant_explicit_date_range():
    assert _date_window("show schedule 2026-09-10 to 2026-09-01") == (
        date(2026, 9, 1),
        date(2026, 9, 10),
    )


def test_assistant_tomorrow_is_one_day():
    start, end = _date_window("who is on G shift tomorrow?")
    assert start == date.today() + timedelta(days=1)
    assert end == start


def test_parse_llm_json_fenced_payload():
    parsed = _parse_llm_json(
        '```json\n{"intent":"help","answer":"Apply leave from the ⋮ menu.","suggestions":["Show my schedule"]}\n```'
    )
    assert parsed["answer"].startswith("Apply leave")
    assert parsed["intent"] == "help"
    assert parsed["suggestions"] == ["Show my schedule"]


def test_parse_llm_plain_text_is_kept():
    parsed = _parse_llm_json("Photosynthesis converts light into chemical energy in plants.")
    assert parsed["intent"] == "help"
    assert "Photosynthesis" in parsed["answer"]
    assert parsed["items"] == []


def test_person_name_hits_first_name_and_possessive():
    assert _person_name_hits("what is smruti's shift today", "Smruti Priya Das")
    assert _person_name_hits("Smruti shift", "SMRUTI DAS")
    assert _person_name_hits("when is smruti working tomorrow", "Smruti")
    assert not _person_name_hits("what is a dash diet", "Smruti Dash")
    assert not _person_name_hits("show my schedule", "Smruti Das")
    from app.routers.assistant_api import _name_match_score

    assert _name_match_score("what is smruti askrota's shift today", "Smruti Askrota") > _name_match_score(
        "what is smruti askrota's shift today", "Smruti Patel"
    )
    assert not _person_name_hits("what is photosynthesis", "Smruti Das")
