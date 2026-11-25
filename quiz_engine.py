"""Create complete GSAT-style sections without altering the vocabulary bank."""
QUIZ_ENGINE_VERSION = 2
import copy
import json
import random
from pathlib import Path

ROOT = Path(__file__).parent
VOCABULARY = json.loads((ROOT / "questions.json").read_text(encoding="utf-8"))
PASSAGES = json.loads((ROOT / "sections.json").read_text(encoding="utf-8"))
GLOSSARY = json.loads((ROOT / "study_notes.json").read_text(encoding="utf-8"))
SECTIONS = {"詞彙題": 10, "綜合測驗": 10, "文意選填": 10, "篇章結構": 4, "閱讀測驗": 12, "混合題型": 4}


def build_section(section, rng=random, seen_ids=None):
    seen_ids = set(seen_ids or ())
    if section == "詞彙題":
        unseen = [q for q in VOCABULARY if q.get("id") not in seen_ids]
        seen = [q for q in VOCABULARY if q.get("id") in seen_ids]
        take = min(10, len(unseen))
        qs = copy.deepcopy(rng.sample(unseen, take) + rng.sample(seen, 10-take))
        rng.shuffle(qs)
        for q in qs:
            rng.shuffle(q["options"])
        return [dict(name="詞彙題", passage="", questions=qs, shared=False, terms=[])]
    counts = {"綜合測驗": 2, "文意選填": 1, "篇章結構": 1, "閱讀測驗": 3, "混合題型": 1}
    candidates = [p for p in PASSAGES if p["kind"] == section]
    rng.shuffle(candidates)
    candidates.sort(key=lambda p: sum(q.get("id") not in seen_ids for q in p["questions"]), reverse=True)
    groups = copy.deepcopy(candidates[:counts[section]])
    for group in groups:
        if section == "文意選填":
            choices = [q["answer"] for q in group["questions"]]
            rng.shuffle(choices)
            for q in group["questions"]:
                q["options"] = choices[:]
        elif group["shared"]:
            choices = group["questions"][0]["options"][:]
            rng.shuffle(choices)
            for q in group["questions"]:
                q["options"] = choices[:]
        else:
            for q in group["questions"]:
                if q.get("options"):
                    rng.shuffle(q["options"])
    assert sum(len(g["questions"]) for g in groups) == SECTIONS[section]
    return groups


def flatten_questions(groups):
    return [q for g in groups for q in g["questions"]]


def duplicate_shared_answers(groups, responses):
    offset = 0
    for g in groups:
        n = len(g["questions"])
        if g["shared"]:
            selected = [responses[i] for i in range(offset, offset+n) if responses[i] is not None]
            if len(selected) != len(set(selected)):
                return True
        offset += n
    return False


def note_for(word):
    return GLOSSARY.get(word.strip().lower())
