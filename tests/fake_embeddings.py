"""Deterministic offline embeddings for vector-store behavior tests."""

from __future__ import annotations

import hashlib
import re

import numpy as np


_TOPICS = (
    ("patient", "stammdaten", "geburtsdatum", "versicherung"),
    ("urlaub", "personalportal", "abwesenheit", "fuehrungskraft"),
    ("drucker", "warteschlange", "formular"),
    ("abrechnung", "quartalsabschluss", "kassenaerzt"),
    ("notfallkennwort", "blaufisch"),
    ("serverraum", "zweiten stock"),
    ("spareribs", "smoker", "barbecue", "kerntemperatur"),
)


class FakeSentenceTransformer:
    """Small cosine space with an e5-like high unrelated baseline."""

    def __init__(self, dimensions: int):
        self.dimensions = dimensions

    def encode(self, texts, **_kwargs):
        vectors = []
        for text in texts:
            value = str(text).lower()
            vector = np.zeros(self.dimensions, dtype=np.float32)
            vector[0] = np.sqrt(0.84)
            topic = next((i for i, words in enumerate(_TOPICS, 1)
                          if any(word in value for word in words)), None)
            if topic is None:
                tokens = re.findall(r"[a-z0-9]{4,}", value)
                seed = tokens[0] if tokens else value
                digest = hashlib.blake2b(seed.encode(), digest_size=2).digest()
                topic = 16 + int.from_bytes(digest, "big") % (self.dimensions - 16)
            vector[topic] = np.sqrt(0.16)
            vectors.append(vector)
        return np.asarray(vectors, dtype=np.float32)


def install(vector_store_module) -> None:
    vector_store_module._embedding_model = FakeSentenceTransformer(
        vector_store_module.EMBEDDING_DIM)
