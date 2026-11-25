"""Scoring for mixed-format questions; free responses remain self-assessed."""

import re
import unicodedata


def normalize_short(value):
    value = unicodedata.normalize('NFKC', str(value)).casefold().strip()
    value = re.sub(r'\s+', ' ', value)
    return value.rstrip(' .!?。！？')


def grade_question(question, response, self_points=None):
    kind = question.get('type', 'single')
    if kind == 'multiple':
        options = question['options']
        selected, answer = set(response), set(question['answer'])
        if not selected <= set(options) or not answer <= set(options):
            raise ValueError('Unknown multiple-choice option')
        errors = len(selected.symmetric_difference(answer))
        maximum = float(question.get('max_points', 4))
        points = max(0.0, round(maximum * (len(options) - 2 * errors) / len(options), 2))
        return dict(correct=errors == 0, points=points, max_points=maximum,
                    self_assessed=False, errors=errors)
    if kind == 'short':
        answers = [question['answer']] + question.get('acceptable_answers', [])
        matched = normalize_short(response) in {normalize_short(a) for a in answers}
        maximum = int(question.get('max_points', 2))
        if self_points is None or not 0 <= self_points <= maximum:
            raise ValueError('A self-assessed score is required for short answers')
        return dict(correct=self_points == maximum, points=self_points, max_points=maximum,
                    self_assessed=True, automatic_exact_match=matched)
    maximum = float(question.get('max_points', 2))
    correct = response == question['answer']
    return dict(correct=correct, points=maximum if correct else 0, max_points=maximum, self_assessed=False)


def response_text(value):
    return '、'.join(value) if isinstance(value, list) else str(value)
