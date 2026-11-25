"""Per-section coverage from identifiable questions in the current bank."""
import quiz_engine


def catalog_ids(section):
    if section == '詞彙題':
        return {q['id'] for q in quiz_engine.VOCABULARY if q.get('id')}
    return {q['id'] for g in quiz_engine.PASSAGES if g['kind'] == section
            for q in g['questions'] if q.get('id')}


def seen_ids(section, history):
    current = catalog_ids(section)
    return {a.get('question_id') for row in history
            if row['section'].split('｜', 1)[0] == section
            for a in row['answers'] if a.get('question_id') in current}


def summarize(section, history, legacy=()):
    rows = [r for r in history if r['section'] == section]
    old = [r for r in legacy if r.get('quiz_type') != '錯題重練'] if section == '詞彙題' else []
    scores = []
    for row in rows + old:
        grades = [a.get('grading') for a in row['answers']]
        if grades and all(g is not None for g in grades):
            maximum = sum(g['max_points'] for g in grades)
            earned = sum(g['points'] for g in grades)
        else:
            maximum, earned = row['total'], row['score']
        if maximum:
            scores.append(earned / maximum)
    durations = [r['duration_seconds'] for r in rows if r.get('duration_seconds') is not None]
    return dict(rounds=len(rows)+len(old), seen=len(seen_ids(section, history)),
                total=len(catalog_ids(section)), average=sum(scores)/len(scores) if scores else None,
                duration=sum(durations)/len(durations) if durations else None)
