"""Offline tests for wake_phrases — leading-wake strip, only-wake, stop phrase.

Pure text logic, no GPU/SFU/network. Covers the one-breath strip (#58) and the
stop-phrase rule (#59): {wake}+стоп sleeps, a bare «стоп» does not.
"""

import pytest

from wake_phrases import (
    is_only_wake_word,
    is_stop_phrase,
    strip_wake_word,
)


# -- strip_wake_word ----------------------------------------------------------
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Приней, сколько времени", "сколько времени"),
        ("Приней сколько времени", "сколько времени"),
        ("приня какая погода", "какая погода"),
        ("Хей Джарвис, что нового", "что нового"),
        ("хей джарвис расскажи анекдот", "расскажи анекдот"),
        ("эй джарвис, помоги", "помоги"),
        ("ПРИНЕЙ ВКЛЮЧИ СВЕТ", "ВКЛЮЧИ СВЕТ"),
    ],
)
def test_strip_leading_wake(raw, expected):
    assert strip_wake_word(raw) == expected


def test_strip_only_leading_occurrence():
    # A wake word later in the sentence is left alone (only the leading one goes).
    assert strip_wake_word("скажи приней громче") == "скажи приней громче"


def test_strip_non_wake_unchanged():
    assert strip_wake_word("сколько сейчас времени") == "сколько сейчас времени"
    assert strip_wake_word("принеси воды") == "принеси воды"  # near-miss, NOT stripped


@pytest.mark.parametrize(
    "raw",
    [
        "принять решение",   # «приня» is a prefix — must NOT be stripped to «ть решение»
        "приняли закон",
        "принял душ",
        "принятие мер",
        "принесите счёт",
    ],
)
def test_strip_does_not_corrupt_prefix_words(raw):
    # The trailing word boundary keeps «приня»/«приней» a whole-word match.
    assert strip_wake_word(raw) == raw
    assert not is_only_wake_word(raw)


def test_strip_preserves_inner_text():
    assert strip_wake_word("Приней   —   привет мир") == "привет мир"


# -- is_only_wake_word --------------------------------------------------------
@pytest.mark.parametrize("raw", ["Приней", "приня", "  Приней!  ", "Хей Джарвис", "хей джарвис."])
def test_only_wake_true(raw):
    assert is_only_wake_word(raw)


@pytest.mark.parametrize("raw", ["Приней сколько времени", "стоп", "привет", ""])
def test_only_wake_false(raw):
    assert not is_only_wake_word(raw)


# -- is_stop_phrase -----------------------------------------------------------
@pytest.mark.parametrize(
    "raw",
    [
        "Приней, стоп",
        "Приней стоп",
        "приня стоп",
        "хей джарвис стоп",
        "Хей Джарвис, стоп",
        "эй джарвис стоп",
    ],
)
def test_stop_phrase_true(raw):
    assert is_stop_phrase(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "стоп",                       # bare стоп must NOT sleep
        "останови музыку",
        "Приней, поставь на стоп через минуту",  # not adjacent
        "принеси стоп-сигнал",        # near-miss «принеси», not a wake word
        "",
    ],
)
def test_stop_phrase_false(raw):
    assert not is_stop_phrase(raw)


def test_bare_stop_never_sleeps():
    # The defining rule: naming the agent is required to stop it.
    assert not is_stop_phrase("стоп стоп стоп")
