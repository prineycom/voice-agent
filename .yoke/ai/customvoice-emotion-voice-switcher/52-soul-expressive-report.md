# Report — #52 Expressive text + emotion tags in SOUL.md

**Issue:** https://github.com/prineycom/voice-agent/issues/52 (parent #49, blocked by #51)
**Type:** HITL · **Status:** SOUL.md updated; human listening sign-off deferred to epic review.

## What changed

`infra/pi/agent/SOUL.md` — two sections:

- **Эмоции**: the tag enum expanded to the 10 ADR-0020 values (`neutral, happy, sad,
  excited, calm, serious, surprised, angry, tender, thinking`). Reframed so the LLM knows
  the tag now drives *the voice* (intonation/pace/delivery), added a short "когда какой"
  guide, told it to switch tags mid-reply, and warned against over-tagging / overacting.
- **Живая речь** (new): instructs the LLM to write expressive *text* that is spoken
  verbatim — interjections (`ммм`, `ага`, `оу`), laughter (`хах`, `ахах`), sighs (`эх…`,
  `нуу…`), lively punctuation — while keeping it natural ("пара живых штрихов на реплику, а
  не клоунада"). Explicitly distinguished from the `[emotion]` tag (stripped vs heard).

This keeps the existing persona ("Реакции", "Голос / устная речь") intact and reinforces it.

## Verification

- `test_soul.py` (persona marker + load contract): **6 passed**.
- Tags are still stripped before transcript/TTS by `EmotionTagStripper` (unchanged); the
  paralinguistic *text* is intentionally kept and spoken.

## Deferred (HITL)

- Unforced conversations showing varied emotion + natural interjections, not overdone — a
  subjective listen against `ryan`. This is the epic's human sign-off item and is done at
  your final review (with the live deploy). Tuning of the SOUL wording may follow the listen.

## AC status

- [x] SOUL.md instructs both expressive text and the expanded emotion tags, with examples
- [x] tags stripped before transcript; paralinguistic text kept
- [~] unforced variety / not overdone / human listening sign-off — epic review
