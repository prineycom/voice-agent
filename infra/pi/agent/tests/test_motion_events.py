"""Offline tests for motion_events — emotion enum, JSON builder, streaming stripper.

Pure logic, no GPU/SFU/network: every case exercises only normalize_emotion,
motion_event_json, and EmotionTagStripper.
"""

import json

from motion_events import (
    DEFAULT_EMOTION,
    EMOTIONS,
    EmotionTagStripper,
    motion_event_json,
    normalize_emotion,
)


def _assert_no_tag(text: str) -> None:
    """The cleaned stream must never leak the literal "[emotion" marker."""
    assert "[emotion" not in text


# -- normalize_emotion --------------------------------------------------------
def test_normalize_known_emotions():
    for emotion in EMOTIONS:
        assert normalize_emotion(emotion) == emotion
        assert normalize_emotion(emotion.upper()) == emotion
        assert normalize_emotion(f"  {emotion}  ") == emotion


def test_normalize_unknown_and_empty_defaults_to_neutral():
    assert normalize_emotion("ecstatic") == DEFAULT_EMOTION
    assert normalize_emotion("") == DEFAULT_EMOTION
    assert normalize_emotion(None) == DEFAULT_EMOTION
    assert DEFAULT_EMOTION == "neutral"


# -- motion_event_json --------------------------------------------------------
def test_motion_event_json_parses():
    payload = json.loads(motion_event_json("speaking", "happy"))
    assert payload == {"type": "motion", "state": "speaking", "emotion": "happy"}


def test_motion_event_json_normalizes_unknown_emotion():
    payload = json.loads(motion_event_json("idle", "ecstatic"))
    assert payload == {"type": "motion", "state": "idle", "emotion": "neutral"}


# -- EmotionTagStripper -------------------------------------------------------
def test_single_chunk_with_tag():
    s = EmotionTagStripper()
    out = s.feed("[emotion:happy]hi")
    out += s.flush()
    assert out == "hi"
    assert s.pop_emotions() == ["happy"]
    _assert_no_tag(out)


def test_tag_split_across_two_feeds():
    s = EmotionTagStripper()
    out = s.feed("[emo")
    out += s.feed("tion:sad] yo")
    out += s.flush()
    assert out == " yo"
    assert s.pop_emotions() == ["sad"]
    _assert_no_tag(out)


def test_on_emotion_callback_fires_in_order():
    seen: list[str] = []
    s = EmotionTagStripper(on_emotion=seen.append)
    out = s.feed("[emotion:happy]a[emotion:sad]b")
    out += s.flush()
    assert out == "ab"
    assert seen == ["happy", "sad"]
    # pop_emotions records the same emotions.
    assert s.pop_emotions() == ["happy", "sad"]
    _assert_no_tag(out)


def test_multiple_tags_across_stream():
    s = EmotionTagStripper()
    out = s.feed("[emotion:happy]hello ")
    out += s.feed("world [emotion:sur")
    out += s.feed("prised] done")
    out += s.flush()
    assert out == "hello world  done"
    assert s.pop_emotions() == ["happy", "surprised"]
    _assert_no_tag(out)


def test_tag_at_very_start_and_very_end():
    s = EmotionTagStripper()
    out = s.feed("[emotion:thinking]middle[emotion:neutral]")
    # The trailing complete tag may be held until flush; either way no leak.
    _assert_no_tag(out)
    out += s.flush()
    assert out == "middle"
    assert s.pop_emotions() == ["thinking", "neutral"]
    _assert_no_tag(out)


def test_no_tag_passes_through_unchanged():
    s = EmotionTagStripper()
    out = s.feed("just some plain text ")
    out += s.feed("with no tags at all.")
    out += s.flush()
    assert out == "just some plain text with no tags at all."
    assert s.pop_emotions() == []
    _assert_no_tag(out)


def test_non_emotion_bracket_is_not_held_forever():
    s = EmotionTagStripper()
    out = s.feed("array[0] = 1")
    out += s.flush()
    assert out == "array[0] = 1"
    assert s.pop_emotions() == []
    _assert_no_tag(out)


def test_divergent_open_bracket_emitted():
    """A '[' that cannot start "[emotion:" is emitted, not stranded."""
    s = EmotionTagStripper()
    out = s.feed("foo [bar baz")
    out += s.flush()
    assert out == "foo [bar baz"
    assert s.pop_emotions() == []
    _assert_no_tag(out)


def test_unknown_emotion_records_neutral():
    s = EmotionTagStripper()
    out = s.feed("[emotion:ecstatic]hi")
    out += s.flush()
    assert out == "hi"
    assert s.pop_emotions() == ["neutral"]
    _assert_no_tag(out)


def test_dangling_partial_tag_emitted_verbatim_on_flush():
    s = EmotionTagStripper()
    out = s.feed("text [emotion:ha")
    # The partial tag is held back, so feed must not have leaked it.
    _assert_no_tag(out)
    assert out == "text "
    # flush emits the dangling, never-completed tag verbatim.
    tail = s.flush()
    assert tail == "[emotion:ha"
    assert s.pop_emotions() == []


def test_malformed_tag_no_colon_stripped_as_neutral():
    """"[emotion]" (no colon/word) is a complete tag: stripped, recorded neutral."""
    s = EmotionTagStripper()
    out = s.feed("[emotion]hi")
    out += s.flush()
    assert out == "hi"
    assert s.pop_emotions() == ["neutral"]
    _assert_no_tag(out)


def test_malformed_tag_space_no_colon_stripped():
    """"[emotion happy]" (space, no colon) is stripped, recorded neutral, no leak."""
    s = EmotionTagStripper()
    out = s.feed("[emotion happy]hi")
    out += s.flush()
    assert out == "hi"
    assert s.pop_emotions() == ["neutral"]
    _assert_no_tag(out)


def test_malformed_tag_mid_text_stripped():
    """"a[emotion b]c" -> "ac" with the malformed complete tag removed."""
    s = EmotionTagStripper()
    out = s.feed("a[emotion b]c")
    out += s.flush()
    assert out == "ac"
    assert s.pop_emotions() == ["neutral"]
    _assert_no_tag(out)


def test_malformed_tag_extra_chars_stripped():
    """"[emotionX abc]" is a complete tag (no parseable word): stripped, neutral."""
    s = EmotionTagStripper()
    out = s.feed("[emotionX abc]done")
    out += s.flush()
    assert out == "done"
    assert s.pop_emotions() == ["neutral"]
    _assert_no_tag(out)


def test_malformed_tag_split_across_feeds():
    """A malformed tag split mid-stream is still held back and stripped on close."""
    s = EmotionTagStripper()
    out = s.feed("a[emotionX")
    # No closing ']' yet: the partial must be held, never leaked.
    _assert_no_tag(out)
    assert out == "a"
    out += s.feed(" z]b")
    out += s.flush()
    assert out == "ab"
    assert s.pop_emotions() == ["neutral"]
    _assert_no_tag(out)


def test_numeric_bracket_not_stranded():
    """"array[7] = 1" passes through losslessly (no emotion tag involved)."""
    s = EmotionTagStripper()
    out = s.feed("array[7] = 1")
    out += s.flush()
    assert out == "array[7] = 1"
    assert s.pop_emotions() == []
    _assert_no_tag(out)


def test_open_bracket_text_not_stranded():
    """"[abc def" diverges from "[emotion" and is emitted, never stranded."""
    s = EmotionTagStripper()
    out = s.feed("[abc def")
    out += s.flush()
    assert out == "[abc def"
    assert s.pop_emotions() == []
    _assert_no_tag(out)


def test_global_invariant_no_emotion_substring_ever_leaks():
    """Across every case, concatenated feed outputs + flush never contain "[emotion"."""
    cases = [
        "[emotion]hi",
        "[emotion happy]hi",
        "a[emotion b]c",
        "[emotionX abc]done",
        "[emotion:happy]well-formed",
        "x[emotion happy]y",
        "a[emotionX z]b",
        "q[emotion:happy]w",
        "array[7] = 1 [em",
        "[abc def",
        "plain text, no tags",
    ]
    for text in cases:
        s = EmotionTagStripper()
        out = "".join(s.feed(ch) for ch in text) + s.flush()
        assert "[emotion" not in out, f"leaked for {text!r}: {out!r}"


def test_char_by_char_streaming():
    """Feeding one character at a time still strips the tag cleanly."""
    s = EmotionTagStripper()
    out = ""
    for ch in "ab[emotion:surprised]cd":
        out += s.feed(ch)
    out += s.flush()
    assert out == "abcd"
    assert s.pop_emotions() == ["surprised"]
    _assert_no_tag(out)
