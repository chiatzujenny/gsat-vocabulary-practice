import json
import os
import random
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import quiz_engine
from catalog_refresh import refresh_catalog
from pdf_export import create_pdf
from progress_store import ProgressStore
from progress_tracker import summarize
from grading import grade_question
from quiz_engine import GLOSSARY, PASSAGES, SECTIONS, VOCABULARY, build_section, duplicate_shared_answers, flatten_questions


class QuestionBankTests(unittest.TestCase):
    def test_catalog_refresh_picks_up_edited_files_without_process_restart(self):
        previous = (quiz_engine.VOCABULARY, quiz_engine.PASSAGES, quiz_engine.GLOSSARY)
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for filename, data in [('questions.json', VOCABULARY),
                                       ('sections.json', PASSAGES), ('study_notes.json', GLOSSARY)]:
                    (root / filename).write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
                updated = json.loads((root / 'sections.json').read_text(encoding='utf-8'))
                updated[0]['name'] = '更新後的測試題組'
                (root / 'sections.json').write_text(json.dumps(updated, ensure_ascii=False), encoding='utf-8')
                refresh_catalog(root)
                self.assertEqual(quiz_engine.PASSAGES[0]['name'], '更新後的測試題組')
        finally:
            quiz_engine.VOCABULARY, quiz_engine.PASSAGES, quiz_engine.GLOSSARY = previous

    def test_reading_passages_are_full_length_with_valid_options(self):
        reading = [group for group in PASSAGES if group['kind'] == '閱讀測驗']
        self.assertGreaterEqual(sum(len(group['questions']) for group in reading), 20)
        for group in reading:
            words = re.findall(r"\b[\w]+(?:['’-][\w]+)*\b", group['passage'])
            self.assertGreaterEqual(len(words), 300, group['name'])
            self.assertLessEqual(len(words), 400, group['name'])
            self.assertEqual(len(group['questions']), 4)
            for q in group['questions']:
                self.assertEqual(len(q['options']), 4)
                self.assertEqual(len(set(q['options'])), 4)
                self.assertIn(q['answer'], q['options'])
                self.assertTrue(q['explanation'].strip())

    def test_cloze_has_two_five_question_groups_per_round(self):
        groups = build_section('綜合測驗', random.Random(13))
        self.assertEqual(len(groups), 2)
        for group in groups:
            self.assertEqual(len(group['questions']), 5)
            for i, question in enumerate(group['questions'], 1):
                self.assertIn(f'[{i}]', group['passage'])
                self.assertEqual(len(question['options']), 4)
                self.assertEqual(len(set(question['options'])), 4)
                self.assertIn(question['answer'], question['options'])

    def test_question_bank_coverage_and_glossary(self):
        self.assertEqual(len(VOCABULARY), 100)
        for section in list(SECTIONS)[1:]:
            self.assertEqual(sum(len(g['questions']) for g in PASSAGES if g['kind'] == section), 20)
        for q in VOCABULARY:
            for option in q['options']:
                self.assertTrue({'meaning','similar','opposite','collocation'} <= GLOSSARY[option.lower()].keys())
        for group in PASSAGES:
            for term in group['terms']:
                self.assertTrue({'meaning','similar','opposite','collocation'} <= GLOSSARY[term.lower()].keys())

    def test_sections_have_expected_lengths_and_valid_answer_keys(self):
        for section, expected in SECTIONS.items():
            groups = build_section(section, random.Random(42))
            questions = flatten_questions(groups)
            self.assertEqual(len(questions), expected)
            for q in questions:
                if q.get('type') == 'short':
                    self.assertTrue(q['answer'])
                elif q.get('type') == 'multiple':
                    self.assertTrue(set(q['answer']).issubset(q['options']))
                else:
                    self.assertIn(q['answer'], q['options'])
            answers = {i: q['answer'] for i, q in enumerate(questions)}
            self.assertFalse(duplicate_shared_answers(groups, answers))
            if groups[0]['shared']:
                answers[1] = answers[0]
                self.assertTrue(duplicate_shared_answers(groups, answers))

    def test_mixed_partial_credit_and_short_self_assessment(self):
        q = {'type':'multiple', 'options':list('ABCDEF'), 'answer':['A','C','E']}
        self.assertEqual(grade_question(q, ['A','C','E'])['points'], 4)
        self.assertEqual(grade_question(q, ['A','C'])['points'], 2.67)
        self.assertEqual(grade_question(q, ['A','C','E','F'])['points'], 2.67)
        short = grade_question({'type':'short', 'answer':'pattern'}, 'PATTERN', 2)
        self.assertTrue(short['automatic_exact_match'])
        self.assertTrue(short['self_assessed'])

    def test_seen_questions_are_deprioritized(self):
        first = build_section('詞彙題', random.Random(7))
        ids = {q['id'] for q in flatten_questions(first)}
        second = build_section('詞彙題', random.Random(8), seen_ids=ids)
        self.assertFalse(ids.intersection(q['id'] for q in flatten_questions(second)))

    def test_pdf_can_be_generated_for_all_sections(self):
        for section in SECTIONS:
            groups = build_section(section, random.Random(1))
            answers = {i: q['answer'] for i, q in enumerate(flatten_questions(groups))}
            self.assertTrue(create_pdf(section, groups, answers, 123).startswith(b'%PDF'))


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = os.path.join(self.tmp.name, 'progress.db').replace('\\', '/')
        self.store = ProgressStore('sqlite:///' + path)
        self.store.initialize()

    def tearDown(self):
        self.store.engine.dispose()
        self.tmp.cleanup()

    def test_optional_name_and_legacy_name_lookup(self):
        code = self.store.create_student_code()
        sid = self.store.authenticate_student(code)
        self.assertTrue(self.store.resolve_student_name(sid, '').startswith('學生-'))
        q = [{'question':'Q', 'answer':'yes', 'category':'test', 'group_name':'測試文章',
              'passage':'The original passage.', 'options':['yes','no']}]
        self.store.save('legacy-1', sid, '原有學生', '一般練習', q, {0:'yes'})
        self.assertEqual(self.store.resolve_student_name(sid, ''), '原有學生')
        self.assertEqual(self.store.resolve_student_name(sid, '新名稱'), '新名稱')
        self.assertEqual(self.store.resolve_student_name(sid, ''), '新名稱')

    def test_saved_attempts_are_deduplicated_and_filtered_by_student(self):
        first = self.store.authenticate_student(self.store.create_student_code())
        second = self.store.authenticate_student(self.store.create_student_code())
        q = [{'question':'Q', 'answer':'yes', 'category':'test', 'group_name':'測試文章',
              'passage':'The original passage.', 'options':['yes','no']}]
        self.store.save_section('run-1', first, '小明', '詞彙題', q, {0:'yes'}, 75)
        self.store.save_section('run-1', first, '小明', '詞彙題', q, {0:'yes'}, 75)
        self.store.save_section('run-2', second, '小華', '閱讀測驗', q, {0:'no'}, 130)
        rows = self.store.list_section_runs(first)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['duration_seconds'], 75)
        self.assertEqual(rows[0]['answers'][0]['selected'], 'yes')
        self.assertEqual(rows[0]['answers'][0]['passage'], 'The original passage.')
        self.assertEqual(len(self.store.list_section_runs()), 2)

    def test_progress_counts_unique_current_ids_and_legacy_separately(self):
        sid = self.store.authenticate_student(self.store.create_student_code())
        q = flatten_questions(build_section('詞彙題', random.Random(9)))[0]
        for attempt in ('first', 'second'):
            self.store.save_section(attempt, sid, '學生', '詞彙題', [q], {0:q['answer']}, 60)
        self.store.save('old', sid, '學生', '一般練習', [q], {0:q['answer']})
        history = self.store.list_section_runs(sid)
        info = summarize('詞彙題', history, self.store.list_attempts(sid))
        self.assertEqual(info['rounds'], 3)
        self.assertEqual(info['seen'], 1)
        self.assertEqual(info['total'], 100)
        self.assertEqual(info['average'], 1)
        self.assertEqual(info['duration'], 60)
        other = self.store.authenticate_student(self.store.create_student_code())
        self.assertEqual(self.store.list_attempts(other), [])
        self.assertEqual(self.store.list_section_runs(other), [])

    def test_admin_can_view_old_and_new_records_with_time(self):
        sid = self.store.authenticate_student(self.store.create_student_code())
        q = [{'question':'Q', 'answer':'yes', 'category':'test', 'group_name':'測試文章',
              'passage':'The original passage.', 'options':['yes','no']}]
        self.store.save('legacy-1', sid, '小明', '一般練習', q, {0:'yes'})
        self.store.save_section('new-1', sid, '小明', '閱讀測驗', q, {0:'no'}, 130)
        with patch('progress_store.ProgressStore', return_value=self.store):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'), default_timeout=20)
            app.secrets['progress'] = {'database_url':f'postgresql://test/{os.path.basename(self.tmp.name)}?sslmode=require',
                                       'admin_password':'z'*32}
            app.run()
            app.text_input[2].set_value('z'*32)
            next(b for b in app.button if b.label == '登入管理頁面 / Sign in').click().run()
            labels = [e.label for e in app.expander]
            self.assertTrue(any('舊紀錄未計時' in label for label in labels))
            self.assertTrue(any('閱讀測驗' in label and '2 分 10 秒' in label for label in labels))
            self.assertTrue(any('題組：測試文章' in x.value for x in app.markdown))
            self.assertTrue(any('The original passage.' in x.value for x in app.markdown))

    def test_save_failure_keeps_answers_available_for_retry(self):
        code = self.store.create_student_code()
        with patch('progress_store.ProgressStore', return_value=self.store):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'), default_timeout=20)
            app.secrets['progress'] = {'database_url':f'postgresql://test/{os.path.basename(self.tmp.name)}?sslmode=require',
                                       'admin_password':'b'*32}
            app.run()
            app.text_input[1].set_value(code)
            app.button[0].click().run()
            app.radio[0].set_value('詞彙題').run()
            next(b for b in app.button if b.label == '開始本回').click().run()
            run = app.session_state['run']
            for i, q in enumerate(flatten_questions(run['groups'])):
                app.radio(key=f"answer_{run['attempt_id']}_{i}").set_value(q['answer'])
            with patch.object(self.store, 'save_section', side_effect=RuntimeError('offline')):
                next(b for b in app.button if b.label == '交卷並查看解析').click().run()
            self.assertFalse(app.session_state['run']['saved'])
            self.assertTrue(next(b for b in app.button if b.label == '登出').disabled)
            next(b for b in app.button if b.label == '重試儲存').click().run()
            self.assertTrue(app.session_state['run']['saved'])
            self.assertEqual(len(self.store.list_section_runs()), 1)

    def test_student_can_complete_each_section_and_see_progress(self):
        code = self.store.create_student_code()
        sid = self.store.authenticate_student(code)
        self.store.resolve_student_name(sid, '小明')
        with patch('progress_store.ProgressStore', return_value=self.store):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'), default_timeout=20)
            app.secrets['progress'] = {'database_url':f'postgresql://test/{os.path.basename(self.tmp.name)}?sslmode=require',
                                       'admin_password':'a'*32}
            app.run()
            app.text_input[1].set_value(code)
            app.button[0].click().run()
            self.assertEqual(app.session_state['student_name'], '小明')
            for section, expected in SECTIONS.items():
                app.radio[0].set_value(section).run()
                next(b for b in app.button if b.label == '開始本回').click().run()
                run = app.session_state['run']
                questions = flatten_questions(run['groups'])
                self.assertEqual(len(questions), expected)
                for i, question in enumerate(questions):
                    key = f"answer_{run['attempt_id']}_{i}"
                    shared = any(g['shared'] and question in g['questions'] for g in run['groups'])
                    if question.get('type') == 'short':
                        app.text_input(key=key).set_value(question['answer'])
                    elif question.get('type') == 'multiple':
                        for j, option in enumerate(question['options']):
                            app.checkbox(key=f'{key}_option_{j}').set_value(option in question['answer'])
                    else:
                        widget = app.selectbox(key=key) if shared else app.radio(key=key)
                        widget.set_value(question['answer'])
                next(b for b in app.button if b.label == '交卷並查看解析').click().run()
                self.assertFalse(app.exception)
                if section == '混合題型':
                    for i, question in enumerate(questions):
                        if question.get('type') == 'short':
                            app.radio(key=f"self_{run['attempt_id']}_{i}").set_value(2)
                    next(b for b in app.button if b.label == '確認自評並儲存').click().run()
                    self.assertFalse(app.exception)
                self.assertTrue(app.session_state['run']['saved'])
                if section == '詞彙題':
                    previous_ids = {q['id'] for q in questions}
                    next(b for b in app.button if b.label == '開始下一回').click().run()
                    self.assertFalse(previous_ids.intersection(
                        q['id'] for q in flatten_questions(app.session_state['run']['groups'])))
                    next(b for b in app.button if b.label == '放棄本回（不保存）').click().run()
            self.assertEqual(len(self.store.list_section_runs(sid)), len(SECTIONS))
            app.radio[0].set_value('練習進度').run()
            self.assertEqual(len(app.progress), len(SECTIONS))


if __name__ == '__main__':
    unittest.main()
