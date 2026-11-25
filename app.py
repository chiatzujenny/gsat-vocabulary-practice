import hmac
import logging
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from pdf_export import create_pdf, word_explanation
from progress_store import ProgressStore
from quiz_engine import SECTIONS, build_section, duplicate_shared_answers, flatten_questions

TITLE = 'Jenny老師的英文練習網站'
st.set_page_config(page_title=TITLE, page_icon='📘', layout='centered')


def get_settings():
    try:
        settings = st.secrets['progress']
        url = settings['database_url']
        password = settings['admin_password']
        if url.startswith('postgres://'):
            url = 'postgresql://' + url[len('postgres://'):]
        parsed = make_url(url)
        if (parsed.drivername not in ('postgresql', 'postgresql+psycopg2')
                or parsed.query.get('sslmode') not in ('require', 'verify-ca', 'verify-full')
                or len(password) < 24):
            return None
        if parsed.drivername == 'postgresql':
            url = 'postgresql+psycopg2://' + url[len('postgresql://'):]
        return url, password
    except (ArgumentError, KeyError, FileNotFoundError, TypeError, ValueError):
        return None


@st.cache_resource(show_spinner=False)
def connect_store(url):
    store = ProgressStore(url)
    store.initialize()
    return store


def logout():
    for key in list(st.session_state):
        del st.session_state[key]
    st.rerun()


def begin(section, groups=None, review=False):
    st.session_state.run = dict(section=section, groups=groups or build_section(section), review=review,
                                started=time.monotonic(), submitted=False, saved=False,
                                attempt_id=str(uuid.uuid4()), responses={}, duration=0)
    st.rerun()


def save_run(store):
    run = st.session_state.run
    try:
        label = run['section'] + ('｜錯題重練' if run['review'] else '')
        store.save_section(run['attempt_id'], st.session_state.student_id,
                           st.session_state.student_name, label, flatten_questions(run['groups']),
                           run['responses'], run['duration'])
        run['saved'] = True
    except Exception:
        run['saved'] = False


def duration_text(seconds):
    return f'{int(seconds)//60} 分 {int(seconds)%60:02} 秒'


def time_label(timestamp):
    return datetime.fromisoformat(timestamp).astimezone(ZoneInfo('Asia/Taipei')).strftime('%Y-%m-%d %H:%M') + '（台灣時間）'


def display_note(word):
    lines = word_explanation(word)
    if lines:
        st.markdown('  \n'.join(lines))


st.title(TITLE)
settings = get_settings()
if settings is None:
    st.error('網站管理員正在設定作答紀錄功能，請稍後再試。')
    st.stop()
try:
    store = connect_store(settings[0])
except Exception as exc:
    detail = str(getattr(exc, 'orig', exc)).lower()
    reasons = [label for label, phrase in (
        ('authentication','authentication'), ('password','password'), ('ssl','ssl'),
        ('channel_binding','channel binding'), ('timeout','timed out'),
        ('resolution','translate host name'), ('permission','permission denied'),
        ('unsupported_parameter','invalid connection option')) if phrase in detail]
    logging.error('Progress store initialization failed: %s (%s); categories=%s',
                  type(exc).__name__, type(getattr(exc,'orig',exc)).__name__, reasons)
    st.error('目前無法連線作答紀錄，請稍後再試或聯絡網站管理員。')
    st.stop()

if 'role' not in st.session_state:
    student_tab, admin_tab = st.tabs(['學生登入 Student login', '管理員登入 Admin login'])
    with student_tab:
        st.write('請輸入您的個人代碼；名稱選填。 / Enter your personal code; your name is optional.')
        with st.form('student_login'):
            name = st.text_input('名稱（選填） / Name (optional)', max_chars=80,
                                 help='再次登入時可留空，系統會沿用上次的名稱。 / Leave blank to use your previous name.')
            code = st.text_input('個人代碼 / Personal code', type='password', max_chars=128)
            enter = st.form_submit_button('登入並開始練習 / Sign in', type='primary')
        st.caption('交卷後會保存名稱、作答內容、分數和時間，供管理員查看。 / Your name, answers, score and time are saved for the administrator.')
        if enter:
            name, code = name.strip(), code.strip()
            if not code:
                st.warning('請填寫個人代碼。 / Please enter your personal code.')
            else:
                try:
                    student_id = store.authenticate_student(code)
                    resolved_name = store.resolve_student_name(student_id, name) if student_id else None
                except Exception:
                    st.error('無法驗證代碼，請稍後再試。 / Unable to verify your code. Please try again.')
                else:
                    if student_id:
                        st.session_state.role = 'student'
                        st.session_state.student_id = student_id
                        st.session_state.student_name = resolved_name
                        st.rerun()
                    else:
                        st.error('個人代碼不正確，請向管理員確認。 / Incorrect code. Please ask your administrator.')
    with admin_tab:
        with st.form('admin_login'):
            admin_password = st.text_input('管理員密碼 / Administrator password', type='password')
            admin_enter = st.form_submit_button('登入管理頁面 / Sign in')
        if admin_enter:
            try:
                now = int(time.time())
                if store.admin_locked(now):
                    st.warning('登入嘗試過於頻繁，請稍候幾秒再試。 / Please wait a few seconds and try again.')
                elif hmac.compare_digest(admin_password.encode('utf-8'), settings[1].encode('utf-8')):
                    store.clear_admin_failures()
                    st.session_state.role = 'admin'
                    st.rerun()
                else:
                    store.record_admin_failure(now)
                    st.error('管理員密碼不正確。 / Incorrect administrator password.')
            except Exception:
                st.error('目前無法驗證管理員登入。 / Unable to sign in right now.')
    st.stop()

if st.session_state.role == 'admin':
    st.subheader('管理頁面')
    if st.button('登出'):
        logout()
    st.markdown('### 建立學生代碼')
    st.caption('每位學生使用不同的代碼。代碼只在建立時顯示，請複製後交給該學生。')
    if st.button('產生一組學生代碼'):
        try:
            st.session_state.new_code = store.create_student_code()
        except Exception:
            st.error('目前無法建立代碼，請稍後再試。')
    if st.session_state.get('new_code'):
        st.code(st.session_state.new_code)
        st.warning('離開此頁或產生下一組後無法再次查看這組代碼，請先安全地交給學生。')
    st.markdown('### 作答紀錄')
    try:
        legacy = store.list_attempts()
        new = store.list_section_runs()
    except Exception:
        st.error('目前無法讀取作答紀錄，請稍後再試。')
        st.stop()
    rows = []
    for row in legacy:
        row.update(section='詞彙題（舊版）', duration_seconds=None)
        rows.append(row)
    rows.extend(new)
    rows.sort(key=lambda item: item['submitted_at'], reverse=True)
    if not rows:
        st.info('目前尚無已完成的測驗。')
    else:
        names = {row['student_id']: row['student_name'] for row in reversed(rows)}
        labels = {sid: f'{name}（代碼識別 {sid[:8]}）' for sid, name in names.items()}
        selected = st.selectbox('選擇學生', list(labels), format_func=lambda sid: labels[sid])
        selected_rows = [row for row in rows if row['student_id'] == selected]
        st.write(f'共 {len(selected_rows)} 次作答；同一代碼會歸在同一位學生。')
        for row in selected_rows:
            timing = '舊紀錄未計時' if row['duration_seconds'] is None else duration_text(row['duration_seconds'])
            with st.expander(f"{time_label(row['submitted_at'])} · {row['student_name']} · {row['section']} · {row['score']}/{row['total']} · {timing}"):
                for i, answer in enumerate(row['answers'], 1):
                    st.write(f"{'✅' if answer['correct'] else '❌'} {i}. {answer['question']}")
                    st.write(f"作答：{answer['selected']}　｜　正確答案：{answer['answer']}")
    st.stop()

with st.sidebar:
    running = st.session_state.get('run')
    pending = bool(running and running['submitted'] and not running['saved'])
    if st.button('登出', disabled=pending):
        logout()
    st.caption(f"目前登入：{st.session_state.student_name}")
    page = st.radio('選擇頁面', ['練習進度'] + list(SECTIONS))

run = st.session_state.get('run')
if run and page != run['section'] and (not run['submitted'] or not run['saved']):
    st.warning(f"目前仍有「{run['section']}」測驗尚未完成或儲存。請回到該頁面繼續。")
    st.stop()
if run and page != run['section'] and run['submitted'] and run['saved']:
    del st.session_state.run
    run = None

if page == '練習進度':
    st.subheader('我的練習進度')
    try:
        history = store.list_section_runs(st.session_state.student_id)
        legacy_done = store.legacy_completed(st.session_state.student_id)
    except Exception:
        st.error('目前無法讀取練習進度，請稍後再試。')
        st.stop()
    for label, amount in SECTIONS.items():
        finished = [r for r in history if r['section'] == label]
        count = len(finished) + (legacy_done if label == '詞彙題' else 0)
        st.write(f"{'✅' if count else '○'} {label}：{'已完成' if count else '尚未完成'} · 每回 {amount} 題" + (f' · {count} 回' if count else ''))
        if finished:
            st.caption(f"最近一次：{time_label(finished[0]['submitted_at'])}，耗時 {duration_text(finished[0]['duration_seconds'])}")
        elif label == '詞彙題' and legacy_done:
            st.caption('包含舊版練習紀錄；舊版未記錄時間。')
    st.caption('計時從開始該大題起至交卷為止；重新整理或關閉頁面前，未交卷的進度不會保存。')
    st.stop()

if not run:
    st.subheader(page)
    st.write(f"每回固定 {SECTIONS[page]} 題；按開始後即正向計時。")
    if page in ('文意選填', '篇章結構'):
        st.caption('每個選項在同一篇文章中限用一次。')
    if page == '綜合測驗':
        st.caption('每回兩篇文章，各五題。')
    elif page == '閱讀測驗':
        st.caption('每回三篇文章，各四題。')
    if st.button('開始本回', type='primary'):
        begin(page)
    st.stop()

questions = flatten_questions(run['groups'])
if not run['submitted']:
    st.subheader(f"{run['section']} · {'錯題重練' if run['review'] else '本回'} · 共 {len(questions)} 題")
    @st.fragment(run_every=1)
    def show_timer():
        st.metric('作答時間', duration_text(max(0, time.monotonic() - run['started'])))
    show_timer()
    st.caption('計時至交卷為止；暫時離開頁面期間也會繼續計時。')
    with st.form('quiz_form'):
        offset = 0
        for group in run['groups']:
            if group['passage']:
                st.markdown(f"### {group['name']}")
                st.write(group['passage'])
            if group['shared']:
                choices = group['questions'][0]['options']
                st.markdown('**共用選項（每個選項限用一次）**')
                for i, option in enumerate(choices):
                    st.write(f'{chr(65+i)}. {option}')
            for q in group['questions']:
                st.markdown(f"**{offset+1}. {q['question']}**")
                key = f"answer_{run['attempt_id']}_{offset}"
                if group['shared']:
                    letter = {value: chr(65+i) for i, value in enumerate(q['options'])}
                    st.selectbox('選擇答案', q['options'], index=None, key=key,
                                 format_func=lambda item, mapping=letter: f'{mapping[item]}. {item}',
                                 placeholder='請選擇', label_visibility='collapsed')
                else:
                    st.radio('選擇答案', q['options'], index=None, key=key, label_visibility='collapsed')
                st.divider()
                offset += 1
        submitted = st.form_submit_button('交卷並查看解析', type='primary', use_container_width=True)
        if submitted:
            responses = {i: st.session_state.get(f"answer_{run['attempt_id']}_{i}") for i in range(len(questions))}
            if any(value is None for value in responses.values()):
                st.warning('還有題目未作答，請完成所有題目後再交卷。')
            elif duplicate_shared_answers(run['groups'], responses):
                st.warning('文意選填和篇章結構的每個共用選項只能使用一次，請檢查重複選項。')
            else:
                run['duration'] = max(0, int(time.monotonic() - run['started']))
                run['responses'] = responses
                run['submitted'] = True
                save_run(store)
                st.rerun()
    if st.button('放棄本回（不保存）'):
        del st.session_state.run
        st.rerun()
else:
    responses = run['responses']
    wrong_indices = [i for i, q in enumerate(questions) if responses[i] != q['answer']]
    st.subheader('測驗結果')
    st.metric('答對題數', f'{len(questions)-len(wrong_indices)} / {len(questions)}')
    st.write(f"{run['section']}作答時間：{duration_text(run['duration'])}")
    if run['saved']:
        st.success('作答紀錄與時間已儲存。')
    else:
        st.error('作答紀錄尚未儲存，請先不要關閉或重新整理頁面。')
        if st.button('重試儲存'):
            save_run(store)
            st.rerun()
    try:
        pdf = create_pdf(run['section'], run['groups'], responses, run['duration'])
        st.download_button('下載本回解析 PDF', pdf, file_name='jenny-english-explanations.pdf', mime='application/pdf')
    except Exception:
        st.warning('目前無法產生 PDF，請稍後再試。')
    if run['saved'] and wrong_indices and st.button(f'重練 {len(wrong_indices)} 題錯題（非正式一回）'):
        new_groups, offset = [], 0
        for group in run['groups']:
            selected = [q for j, q in enumerate(group['questions']) if offset+j in wrong_indices]
            if selected:
                new_groups.append(dict(group, questions=selected))
            offset += len(group['questions'])
        begin(run['section'], new_groups, review=True)
    if run['saved'] and st.button('開始下一回'):
        begin(run['section'])
    st.markdown('### 答案與解析')
    offset = 0
    for group in run['groups']:
        if group['passage']:
            with st.expander(f"閱讀文章：{group['name']}"):
                st.write(group['passage'])
        for q in group['questions']:
            idx = offset
            correct = responses[idx] == q['answer']
            with st.expander(f"{'✅' if correct else '❌'} 第 {idx+1} 題 · {q['question']}", expanded=not correct):
                st.write(f"你的答案：{responses[idx]}　｜　正確答案：{q['answer']}")
                st.write('中文解析：' + q['explanation'])
                words = q['options'] if not group['shared'] and page in ('詞彙題','綜合測驗') else [q['answer']]
                if group['shared'] and page == '篇章結構':
                    words = []
                for word in words:
                    display_note(word)
            offset += 1
        if group['shared'] and page == '文意選填':
            with st.expander(f"{group['name']}：共用選項單字"):
                for word in group['questions'][0]['options']:
                    display_note(word)
        if group['terms']:
            with st.expander(f"{group['name']}：文章重點單字"):
                for word in group['terms']:
                    display_note(word)
