import hmac
import logging
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

# Streamlit may retain imported helper modules across a source-code update.
# Refresh older helper versions once so a deployment takes effect without a reboot.
import importlib
import quiz_engine as _quiz_engine
import progress_store as _progress_store
import pdf_export as _pdf_export
for _module, _marker, _version in ((_quiz_engine, 'QUIZ_ENGINE_VERSION', 2),
                                   (_progress_store, 'PROGRESS_STORE_VERSION', 4),
                                   (_pdf_export, 'PDF_EXPORT_VERSION', 2)):
    if getattr(_module, _marker, 0) < _version:
        importlib.reload(_module)

from catalog_refresh import refresh_catalog
from grading import grade_question, response_text
from pdf_export import create_pdf, word_explanation
from progress_store import ProgressStore
from progress_tracker import seen_ids, summarize
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
def connect_store(url, schema_version=4):
    store = ProgressStore(url)
    store.initialize()
    return store


def logout():
    for key in list(st.session_state):
        del st.session_state[key]
    st.rerun()


def begin(section, groups=None, review=False):
    if groups is None:
        try:
            history = store.list_section_runs(st.session_state.student_id)
            groups = build_section(section, seen_ids=seen_ids(section, history))
        except Exception:
            st.error("目前無法讀取練習紀錄，請稍後再試。")
            return
    st.session_state.run = dict(section=section, groups=groups, review=review,
                                started=time.monotonic(), submitted=False, saved=False,
                                review_pending=False, attempt_id=str(uuid.uuid4()), responses={}, duration=0)
    st.rerun()


def save_run(store):
    run = st.session_state.run
    try:
        label = run['section'] + ('｜錯題重練' if run['review'] else '')
        snapshot = [dict(q, group_name=g['name'] if g['passage'] else '', passage=g['passage'])
                    for g in run['groups'] for q in g['questions']]
        store.save_section(run['attempt_id'], st.session_state.student_id,
                           st.session_state.student_name, label, snapshot,
                           run['responses'], run['duration'])
        run['saved'] = True
    except Exception:
        run['saved'] = False


def duration_text(seconds):
    return f'{int(seconds)//60} 分 {int(seconds)%60:02} 秒'


def points_text(answers):
    grades = [item.get('grading') for item in answers]
    if grades and all(grade is not None for grade in grades):
        earned = sum(grade['points'] for grade in grades)
        maximum = sum(grade['max_points'] for grade in grades)
        return f'{earned:g}/{maximum:g} 分（簡答含學生自評）'
    return None


def time_label(timestamp):
    return datetime.fromisoformat(timestamp).astimezone(ZoneInfo('Asia/Taipei')).strftime('%Y-%m-%d %H:%M') + '（台灣時間）'


def display_note(word):
    lines = word_explanation(word)
    if lines:
        st.markdown('  \n'.join(lines))


st.title(TITLE)
refresh_catalog()
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
        students_list = store.list_students()
    except Exception:
        st.error('目前無法讀取作答紀錄，請稍後再試。')
        st.stop()
    rows = []
    for row in legacy:
        row.update(section='詞彙題（舊版）', duration_seconds=None, source='legacy')
        rows.append(row)
    for row in new:
        row['source'] = 'section'
    rows.extend(new)
    rows.sort(key=lambda item: item['submitted_at'], reverse=True)
    if st.session_state.pop('student_delete_success', False):
        st.success('學生代碼及該學生的所有作答紀錄已刪除。')
    if not rows and not students_list:
        st.info('目前沒有學生或作答紀錄。')
    else:
        names = {row['student_id']: row['student_name'] for row in reversed(rows)}
        for student in students_list:
            sid = student['student_id']
            names[sid] = student['display_name'] or names.get(sid, f'學生-{sid[:8]}')
        labels = {sid: f'{name}（代碼識別 {sid[:8]}）' for sid, name in names.items()}
        selected = st.selectbox('選擇學生', list(labels), format_func=lambda sid: labels[sid])
        selected_rows = [row for row in rows if row['student_id'] == selected]
        st.write(f'共 {len(selected_rows)} 次作答；同一代碼會歸在同一位學生。')
        if st.session_state.get('pending_student_delete') == selected:
            st.warning('確認移除這位學生？其個人代碼、名稱及所有作答紀錄將永久刪除，無法在網站復原。')
            st.write(f"學生：{labels[selected]}　｜　作答紀錄：{len(selected_rows)} 筆")
            if st.button('確認移除學生及全部紀錄', type='primary'):
                try:
                    store.delete_student_and_attempts(selected)
                except Exception:
                    st.error('無法確認移除結果，請重新檢查後再試。')
                else:
                    del st.session_state.pending_student_delete
                    st.session_state.pop('pending_delete', None)
                    st.session_state.student_delete_success = True
                    st.rerun()
            if st.button('取消移除學生'):
                del st.session_state.pending_student_delete
                st.rerun()
        else:
            st.session_state.pop('pending_student_delete', None)
            if st.button('移除這位學生及全部紀錄'):
                st.session_state.pending_student_delete = selected
                st.rerun()
        if st.session_state.pop('delete_success', False):
            st.success('該筆作答紀錄已刪除，相關統計已更新。')
        pending_delete = st.session_state.get('pending_delete')
        if pending_delete:
            match = next((r for r in selected_rows if
                          (r['source'], r['attempt_id'], r['student_id']) == pending_delete), None)
            if match is None:
                del st.session_state.pending_delete
            else:
                st.warning('確認刪除這筆作答紀錄？刪除後無法在網站復原，學生的進度和統計也會更新。')
                st.write(f"學生：{match['student_name']}（代碼識別 {match['student_id'][:8]}）")
                st.write(f"題型：{match['section']}　｜　時間：{time_label(match['submitted_at'])}")
                match_score = points_text(match['answers']) or f"{match['score']}/{match['total']}"
                st.write(f'成績：{match_score}')
                if st.button('確認刪除此紀錄', type='primary'):
                    try:
                        deleted = store.delete_attempt(*pending_delete)
                    except Exception:
                        st.error('無法確認刪除結果，請重新檢查紀錄後再試。')
                    else:
                        del st.session_state.pending_delete
                        if deleted:
                            st.session_state.delete_success = True
                            st.rerun()
                        else:
                            st.warning('找不到這筆紀錄，可能已被其他管理員刪除。')
                if st.button('取消刪除'):
                    del st.session_state.pending_delete
                    st.rerun()
        with st.expander('各題型統計'):
            student_new = [r for r in new if r['student_id'] == selected]
            student_old = [r for r in legacy if r['student_id'] == selected]
            for label in SECTIONS:
                info = summarize(label, student_new, student_old)
                if not info['rounds']:
                    continue
                line = f"{label}：{info['rounds']} 回，已練 {info['seen']}/{info['total']} 題"
                if info['average'] is not None:
                    line += f"，平均得分率 {info['average']:.0%}"
                if info['duration'] is not None:
                    line += f"，平均時間 {duration_text(info['duration'])}"
                st.write(line)
            st.caption('不重複題數僅計可對應目前題庫的紀錄；混合題包含學生自評，須由教師覆核。')
        for row in selected_rows:
            timing = '舊紀錄未計時' if row['duration_seconds'] is None else duration_text(row['duration_seconds'])
            score = points_text(row['answers']) or f"{row['score']}/{row['total']}"
            with st.expander(f"{time_label(row['submitted_at'])} · {row['student_name']} · {row['section']} · {score} · {timing}"):
                last_group = None
                for i, answer in enumerate(row['answers'], 1):
                    group_name = answer.get('group_name', '')
                    if group_name and group_name != last_group:
                        st.markdown(f'**題組：{group_name}**')
                        st.write(answer.get('passage', ''))
                        last_group = group_name
                    st.write(f"{'✅' if answer['correct'] else '❌'} {i}. {answer['question']}")
                    if answer.get('options'):
                        st.write('選項：' + ' / '.join(answer['options']))
                    st.write(f"作答：{response_text(answer['selected'])}　｜　參考答案：{response_text(answer['answer'])}")
                    grade = answer.get('grading')
                    if grade is not None:
                        remark = '（學生自評，需教師覆核）' if grade.get('self_assessed') else ''
                        st.caption(f"得分 {grade['points']:g}/{grade['max_points']:g} {remark}")
                if st.button('刪除此筆紀錄', key=f"delete_{row['source']}_{row['attempt_id']}"):
                    st.session_state.pending_delete = (row['source'], row['attempt_id'], row['student_id'])
                    st.rerun()
    st.stop()

try:
    active_student = store.student_exists(st.session_state.student_id)
except Exception:
    st.error('目前無法確認學生代碼，請稍後再試。')
    st.stop()
if not active_student:
    st.warning('這組學生代碼已停用，請向老師確認。')
    if st.button('返回登入頁'):
        logout()
    st.stop()

with st.sidebar:
    running = st.session_state.get('run')
    pending = bool(running and (running.get('review_pending') or (running['submitted'] and not running['saved'])))
    if st.button('登出', disabled=pending):
        logout()
    st.caption(f"目前登入：{st.session_state.student_name}")
    page = st.radio('選擇頁面', ['練習進度'] + list(SECTIONS), key='navigation_page')

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
        legacy = store.list_attempts(st.session_state.student_id)
    except Exception:
        st.error('目前無法讀取練習進度，請稍後再試。')
        st.stop()
    st.caption('各題型可自由練習；進度表示目前題庫中已完成的不重複題數，不等同熟練度。')
    for label in SECTIONS:
        info = summarize(label, history, legacy)
        st.markdown(f"### {label}")
        ratio = info['seen'] / info['total'] if info['total'] else 0
        st.progress(ratio, text=f"練習進度 {ratio:.0%}")
        summary = f"完成 {info['rounds']} 回"
        if info['average'] is not None:
            summary += f" · 平均得分率 {info['average']:.0%}"
        if info['duration'] is not None:
            summary += f" · 平均時間 {duration_text(info['duration'])}"
        st.caption(summary)
        st.button(f'練習{label}', key=f'practice_{label}', use_container_width=True,
                  on_click=lambda target=label: st.session_state.update(navigation_page=target))
    st.caption('優先安排尚未做過的題目或題組；題組需完整出題，重複練習不會重複計入覆蓋率。舊版及無法對應新版題庫的作答仍計入回數，但不列入覆蓋率。混合題含學生自評，分數須由教師覆核。')
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
    elif page == '混合題型':
        st.caption('每回一篇、四小題，包含多選與填充／簡答。簡答以學生自評為準，建議教師覆核。')
    if st.button('開始本回', type='primary'):
        begin(page)
    st.stop()

questions = flatten_questions(run['groups'])
if not run['submitted'] and not run.get('review_pending'):
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
                kind = q.get('type', 'single')
                if group['shared']:
                    letter = {value: chr(65+i) for i, value in enumerate(q['options'])}
                    st.selectbox('選擇答案', q['options'], index=None, key=key,
                                 format_func=lambda item, mapping=letter: f'{mapping[item]}. {item}',
                                 placeholder='請選擇', label_visibility='collapsed')
                elif kind == 'multiple':
                    st.caption('多選題：可選一個或多個選項。')
                    for j, option in enumerate(q['options']):
                        st.checkbox(option, key=f'{key}_option_{j}')
                elif kind == 'short':
                    st.text_input('英文填充／簡答', key=key, placeholder='請輸入英文答案')
                else:
                    st.radio('選擇答案', q['options'], index=None, key=key, label_visibility='collapsed')
                st.divider()
                offset += 1
        submitted = st.form_submit_button('交卷並查看解析', type='primary', use_container_width=True)
        if submitted:
            responses = {}
            for i, q in enumerate(questions):
                key = f"answer_{run['attempt_id']}_{i}"
                if q.get('type') == 'multiple':
                    responses[i] = [opt for j, opt in enumerate(q['options'])
                                    if st.session_state.get(f'{key}_option_{j}', False)]
                else:
                    responses[i] = st.session_state.get(key)
                    if isinstance(responses[i], str):
                        responses[i] = responses[i].strip()
            if any(value is None or value == '' or value == [] for value in responses.values()):
                st.warning('還有題目未作答，請完成所有題目後再交卷。')
            elif duplicate_shared_answers(run['groups'], responses):
                st.warning('文意選填和篇章結構的每個共用選項只能使用一次，請檢查重複選項。')
            else:
                run['duration'] = max(0, int(time.monotonic() - run['started']))
                run['responses'] = responses
                if any(q.get('type') == 'short' for q in questions):
                    run['review_pending'] = True
                else:
                    if any(q.get('type') == 'multiple' for q in questions):
                        for i, q in enumerate(questions):
                            q['_grade'] = grade_question(q, responses[i])
                    run['submitted'] = True
                    save_run(store)
                st.rerun()
    if st.button('放棄本回（不保存）'):
        del st.session_state.run
        st.rerun()
elif run.get('review_pending'):
    st.subheader('核對填充與簡答')
    st.info('請依參考答案及解析評分：意思與字形皆正確 2 分；意思正確但拼字或字形有誤 1 分；意思不符 0 分。成績為學生自評，須由教師覆核；計時已於交卷時停止。')
    with st.form('self_review_form'):
        grades = {}
        for i, q in enumerate(questions):
            if q.get('type') != 'short':
                continue
            st.markdown(f"**第 {i+1} 題：{q['question']}**")
            st.write(f"你的答案：{run['responses'][i]}")
            st.write('參考答案：' + response_text(q['answer']))
            if q.get('acceptable_answers'):
                st.write('其他可接受答案：' + '、'.join(q['acceptable_answers']))
            st.write('中文解析：' + q['explanation'])
            options = [0, 1, 2]
            matched = grade_question(q, run['responses'][i], self_points=0)['automatic_exact_match']
            st.caption('系統比對：' + ('與參考答案一致' if matched else '未與列出的參考答案完全一致，請依解析與教師指示核對。'))
            grades[i] = st.radio('本題自評分數', options, index=None, horizontal=True,
                                 format_func=lambda x: f'{x} 分', key=f"self_{run['attempt_id']}_{i}")
        confirmed = st.form_submit_button('確認自評並儲存', type='primary')
        if confirmed:
            if any(value is None for value in grades.values()):
                st.warning('請先為每一道填充／簡答題選擇自評分數。')
            else:
                for i, q in enumerate(questions):
                    q['_grade'] = grade_question(q, run['responses'][i], grades.get(i))
                run['review_pending'] = False
                run['submitted'] = True
                save_run(store)
                st.rerun()
else:
    responses = run['responses']
    wrong_indices = [i for i, q in enumerate(questions) if not q.get('_grade', {}).get('correct', responses[i] == q['answer'])]
    st.subheader('測驗結果')
    if questions and all('_grade' in q for q in questions):
        earned = sum(q['_grade']['points'] for q in questions)
        maximum = sum(q['_grade']['max_points'] for q in questions)
        st.metric('本回得分', f'{earned:g} / {maximum:g} 分')
        if any(q['_grade']['self_assessed'] for q in questions):
            st.caption('填充／簡答的分數包含學生自評，請以教師覆核為準。')
    else:
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
            grade = q.get('_grade')
            correct = grade['correct'] if grade else responses[idx] == q['answer']
            with st.expander(f"{'✅' if correct else '❌'} 第 {idx+1} 題 · {q['question']}", expanded=not correct):
                st.write(f"你的答案：{response_text(responses[idx])}　｜　參考答案：{response_text(q['answer'])}")
                if grade:
                    st.write(f"得分：{grade['points']:g} / {grade['max_points']:g}" + ('（學生自評）' if grade['self_assessed'] else ''))
                st.write('中文解析：' + q['explanation'])
                words = q.get('options', []) if not group['shared'] and page in ('詞彙題','綜合測驗') else ([q['answer']] if isinstance(q['answer'], str) else [])
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
