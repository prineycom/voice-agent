"""Map the shared emotion enum → the A2F emotion input vector.

The LLM emits an inline emotion tag; the agent parses it (decision #5) and sends
it as the ``emotion`` field on each per-sentence ``/tts`` request (decision #6).
The Desktop TTS forks its PCM into the A2F service together with this emotion.

A2F's Audio2Emotion vocabulary (10 dims) differs from our small enum, so we map
enum → a sparse A2E vector. Order matches the A2E model's emotion output:
``[grief, joy, disgust, outofbreath, pain, anger, amazement, cheekiness, sadness, fear]``.
"""

# A2E emotion dimensions, in model order.
A2E_DIMS = [
    "grief", "joy", "disgust", "outofbreath", "pain",
    "anger", "amazement", "cheekiness", "sadness", "fear",
]

# Shared enum (SOUL.md / agent) → A2E dimension weights.
_ENUM_TO_A2E = {
    "neutral": {},
    "happy": {"joy": 1.0},
    "sad": {"sadness": 1.0, "grief": 0.3},
    "surprised": {"amazement": 1.0},
    "thinking": {"cheekiness": 0.4},
}


def enum_to_vector(name: str | None) -> list[float]:
    weights = _ENUM_TO_A2E.get((name or "neutral").lower(), {})
    return [round(weights.get(dim, 0.0), 3) for dim in A2E_DIMS]
