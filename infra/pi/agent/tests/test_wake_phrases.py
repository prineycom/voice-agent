"""Offline tests for wake_phrases — leading-wake strip and only-wake (#58).

Pure text logic, no GPU/SFU/network. Covers the one-breath strip. The stop-phrase
rule ({wake}+стоп) is added with is_stop_phrase in #59.
"""

import pytest

from wake_phrases import (
    is_only_wake_word,
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


def test_strip_preserves_inner_text():
    assert strip_wake_word("Приней   —   привет мир") == "привет мир"


# -- is_only_wake_word --------------------------------------------------------
@pytest.mark.parametrize("raw", ["Приней", "приня", "  Приней!  ", "Хей Джарвис", "хей джарвис."])
def test_only_wake_true(raw):
    assert is_only_wake_word(raw)


@pytest.mark.parametrize("raw", ["Приней сколько времени", "стоп", "привет", ""])
def test_only_wake_false(raw):
    assert not is_only_wake_word(raw)
