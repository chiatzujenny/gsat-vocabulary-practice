import json
import os
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from pdf_export import create_pdf
from progress_store import ProgressStore
from quiz_engine import GLOSSARY, PASSAGES, SECTIONS, VOCABULARY, build_section, duplicate_shared_answers, flatten_questions


class QuestionBankTests(unittest.TestCase):
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
            self.assertTrue(all(q['answer'] in q['options'] for q in questions))
            answers = {i: q['answer'] for i, q in enumerate(questions)}
            self.assertFalse(duplicate_shared_answers(groups, answers))
            if groups[0]['shared']:
                answers[1] = answers[0]
                self.assertTrue(duplicate_shared_answers(groups, answers))

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
                    widget = app.selectbox(key=key) if shared else app.radio(key=key)
                    widget.set_value(question['answer'])
                next(b for b in app.button if b.label == '交卷並查看解析').click().run()
                self.assertFalse(app.exception)
                self.assertTrue(app.session_state['run']['saved'])
            self.assertEqual(len(self.store.list_section_runs(sid)), 5)
            app.radio[0].set_value('練習進度').run()
            self.assertEqual(sum('已完成' in x.value for x in app.markdown), 5)


if __name__ == '__main__':
    unittest.main()
