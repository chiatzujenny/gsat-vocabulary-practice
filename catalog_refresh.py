"""Reload editable question and glossary files when an app session reruns."""

import json
from pathlib import Path

import quiz_engine


def refresh_catalog(root=None):
    root = Path(root) if root else Path(__file__).parent
    # Read everything before updating the shared module to avoid publishing
    # partially read data if a file is missing or malformed.
    vocabulary, passages, glossary = [json.loads((root / name).read_text(encoding='utf-8'))
                                     for name in ('questions.json', 'sections.json', 'study_notes.json')]
    quiz_engine.VOCABULARY = vocabulary
    quiz_engine.PASSAGES = passages
    quiz_engine.GLOSSARY = glossary
