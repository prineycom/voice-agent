"""Offline regression for Russian sentence tokenization (issue #13 req #7).

Uses the same tokenizer the LiveKit Agents framework ships, exercised fully
offline (blingfire is a bundled native lib; no network/GPU).
"""

from livekit.agents.tokenize import blingfire


def test_splits_russian_sentences():
    """A multi-sentence Russian string splits on . ! ? into 3 sentences."""
    # min_sentence_len=1 disables the default short-sentence merge so we see
    # the raw split behavior on punctuation.
    tok = blingfire.SentenceTokenizer(min_sentence_len=1)
    sentences = tok.tokenize("Привет, друг! Как дела сегодня? Я рад тебя видеть.")
    assert len(sentences) == 3


def test_abbreviations_do_not_missplit():
    """Abbreviations (т.е. / т.д.) must not split into bare-letter fragments."""
    tok = blingfire.SentenceTokenizer(min_sentence_len=1)
    sentences = tok.tokenize("Это т.е. сокращение, и т.д. в конце.")

    # Robust to blingfire's min_sentence_len merging: assert no fragment is a
    # bare abbreviation letter, and the abbreviations survive intact somewhere.
    for sentence in sentences:
        assert sentence.strip() not in {"т", "е", "д"}
    joined = " ".join(sentences)
    assert "т.е." in joined
    assert "т.д." in joined
