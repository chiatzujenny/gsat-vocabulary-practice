"""Create a downloadable explanation PDF with embedded Traditional Chinese font."""
PDF_EXPORT_VERSION = 2
from html import escape
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

from quiz_engine import note_for
from grading import response_text

FONT = "JennyNotoTC"
if FONT not in pdfmetrics.getRegisteredFontNames():
    pdfmetrics.registerFont(TTFont(FONT, str(Path(__file__).parent / "fonts" / "NotoSansTC-Regular.ttf")))


def word_explanation(word):
    info = note_for(word)
    if not info:
        return None
    lines = [f"{word}：{info['meaning']}"]
    if 'collocation' in info:
        lines.extend([f"用法與搭配：{info['collocation']}", f"近義：{info['similar']}；反義：{info['opposite']}"])
        if info.get('example'):
            lines.append(f"例句：{info['example']}")
        if info.get('confusion'):
            lines.append(f"易混淆：{info['confusion']}")
    else:
        lines.append("延伸用法、近義與反義：請依詞性及本題語境判斷。")
    return lines


def create_pdf(section, groups, responses, duration):
    styles = getSampleStyleSheet()
    normal = ParagraphStyle('tc', parent=styles['Normal'], fontName=FONT, fontSize=9.5, leading=15,
                            wordWrap='CJK', spaceAfter=5, textColor=colors.HexColor('#243246'))
    title = ParagraphStyle('title_tc', parent=normal, fontSize=18, leading=26, alignment=TA_CENTER, spaceAfter=12)
    heading = ParagraphStyle('heading_tc', parent=normal, fontSize=13, leading=19, spaceBefore=10, spaceAfter=6,
                             textColor=colors.HexColor('#193b64'), keepWithNext=True)
    small = ParagraphStyle('small_tc', parent=normal, fontSize=8.5, leading=13, leftIndent=10)
    out = BytesIO()
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=19*mm, rightMargin=19*mm,
                            topMargin=17*mm, bottomMargin=17*mm, title='Jenny老師的英文練習解析')
    blocks = []
    def para(text, style=normal):
        blocks.append(Paragraph(escape(str(text)).replace('\n','<br/>'), style))
    para('Jenny老師的英文練習網站｜作答解析', title)
    para(f'{section}　｜　作答時間 {duration // 60} 分 {duration % 60:02} 秒')
    offset = 0
    for group in groups:
        if group['passage']:
            para(group['name'], heading)
            para(group['passage'])
        for q in group['questions']:
            idx = offset
            chosen = responses[idx]
            para(f'第 {idx+1} 題　{q["question"]}', heading)
            if q.get('options'):
                para('選項：' + '　／　'.join(q['options']))
            grade = q.get('_grade')
            result = ('得分：' + f"{grade['points']:g}/{grade['max_points']:g}" +
                      ('（學生自評，待教師覆核）' if grade.get('self_assessed') else '')) if grade else ('答對' if chosen == q['answer'] else '答錯')
            para(f'你的答案：{response_text(chosen)}　｜　參考答案：{response_text(q["answer"])}　｜　{result}')
            para('中文解析：' + q['explanation'])
            words = q.get('options', []) if not group['shared'] and section in ('詞彙題', '綜合測驗') else ([q['answer']] if isinstance(q['answer'], str) else [])
            if group['shared'] and section == '篇章結構':
                words = []
            for word in words:
                details = word_explanation(word)
                if details:
                    for line in details:
                        para(line, small)
            offset += 1
        if group['shared'] and section == '文意選填':
            para('共用選項單字', heading)
            for word in group['questions'][0]['options']:
                details = word_explanation(word)
                if details:
                    for line in details:
                        para(line, small)
        if group.get('terms'):
            para('文章重點詞彙', heading)
            for word in group['terms']:
                details = word_explanation(word)
                if details:
                    for line in details:
                        para(line, small)
        blocks.append(Spacer(1, 4*mm))
    def footer(canvas, pdfdoc):
        canvas.setFont(FONT, 8)
        canvas.setFillColor(colors.HexColor('#66758a'))
        canvas.drawCentredString(A4[0]/2, 10*mm, f'第 {pdfdoc.page} 頁')
    doc.build(blocks, onFirstPage=footer, onLaterPages=footer)
    return out.getvalue()
