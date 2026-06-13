import json
import os
import tempfile
from datetime import datetime, date, timedelta


DAYS_MAP = {
    'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3,
    'fri': 4, 'sat': 5, 'sun': 6
}


def allowed_file(filename, allowed_extensions):
    return ('.' in filename and
            filename.rsplit('.', 1)[1].lower() in allowed_extensions)


def generate_scheduled_logs(course_item, course, patient_id):
    """Генерирует записи IntakeLog для каждого запланированного приёма в курсе."""
    from app.models import db, IntakeLog

    try:
        times = json.loads(course_item.schedule_times)
    except Exception:
        times = ['08:00']
    try:
        days = json.loads(course_item.days_of_week)
    except Exception:
        days = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']

    day_numbers = {DAYS_MAP[d] for d in days if d in DAYS_MAP}

    current = course.start_date
    end = course.end_date

    logs_to_add = []
    while current <= end:
        if current.weekday() in day_numbers:
            for t_str in times:
                try:
                    h, m = map(int, t_str.split(':'))
                except ValueError:
                    h, m = 8, 0
                scheduled = datetime.combine(current, datetime.min.time().replace(hour=h, minute=m))
                log = IntakeLog(
                    course_item_id=course_item.id,
                    patient_id=patient_id,
                    scheduled_at=scheduled,
                    status='pending'
                )
                logs_to_add.append(log)
        current += timedelta(days=1)

    db.session.add_all(logs_to_add)


def _register_cyrillic_font():
    """
    Регистрирует шрифт с поддержкой кириллицы.
    Ищет DejaVuSans (поставляется с matplotlib/reportlab на большинстве систем)
    или падает обратно на системный шрифт macOS/Linux.
    Возвращает имя зарегистрированного шрифта.
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    # Список кандидатов в порядке приоритета
    candidates = [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/dejavu/DejaVuSans.ttf',
        '/Library/Fonts/Arial Unicode MS.ttf',
        '/System/Library/Fonts/Supplemental/Arial Unicode MS.ttf',
        '/System/Library/Fonts/Helvetica.ttc',
        '/Library/Fonts/Arial.ttf',
        '/opt/homebrew/share/fonts/dejavu-fonts/DejaVuSans.ttf',
    ]

    try:
        import matplotlib
        mpl_data = matplotlib.get_data_path()
        deja = os.path.join(mpl_data, 'fonts', 'ttf', 'DejaVuSans.ttf')
        if os.path.exists(deja):
            candidates.insert(0, deja)
    except Exception:
        pass

    try:
        import reportlab
        rl_dir = os.path.dirname(reportlab.__file__)
        deja_rl = os.path.join(rl_dir, 'fonts', 'DejaVuSans.ttf')
        if os.path.exists(deja_rl):
            candidates.insert(0, deja_rl)
    except Exception:
        pass

    for path in candidates:
        if os.path.exists(path):
            try:
                font_name = 'CyrillicFont'
                pdfmetrics.registerFont(TTFont(font_name, path))
                return font_name
            except Exception:
                continue

    try:
        import urllib.request
        url = ('https://github.com/dejavu-fonts/dejavu-fonts/raw/master/'
               'ttf/DejaVuSans.ttf')
        tmp_font = os.path.join(tempfile.gettempdir(), 'DejaVuSans_medtreker.ttf')
        if not os.path.exists(tmp_font):
            urllib.request.urlretrieve(url, tmp_font)
        pdfmetrics.registerFont(TTFont('CyrillicFont', tmp_font))
        return 'CyrillicFont'
    except Exception:
        pass

    return None


def export_history_pdf(course, patient):
    """Генерирует PDF с историей приёмов курса и возвращает путь к файлу."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                    Table, TableStyle)
    from app.models import IntakeLog, CourseItem

    font_name = _register_cyrillic_font()
    if font_name is None:
        font_name = 'Helvetica' 

    tmp = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
    tmp.close()

    doc = SimpleDocTemplate(
        tmp.name,
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        'CyrTitle',
        fontName=font_name,
        fontSize=16,
        spaceAfter=6,
        leading=20,
    )
    subtitle_style = ParagraphStyle(
        'CyrSub',
        fontName=font_name,
        fontSize=10,
        textColor=colors.HexColor('#5f5e5a'),
        spaceAfter=8,
        leading=14,
    )
    normal_style = ParagraphStyle(
        'CyrNormal',
        fontName=font_name,
        fontSize=10,
        spaceAfter=6,
        leading=14,
    )

    story = []

    story.append(Paragraph('МедТрекер — История приёмов', title_style))
    story.append(Paragraph(
        f'Курс: {course.title} | '
        f'Период: {course.start_date.strftime("%d.%m.%Y")} – {course.end_date.strftime("%d.%m.%Y")} | '
        f'Пациент: {patient.full_name}',
        subtitle_style
    ))

    if course.doctor:
        story.append(Paragraph(f'Лечащий врач: {course.doctor.full_name}', subtitle_style))

    story.append(Spacer(1, 6 * mm))

    logs_all = (IntakeLog.query
                .join(CourseItem)
                .filter(CourseItem.course_id == course.id)
                .all())
    past_logs   = [l for l in logs_all if l.status in ('taken', 'missed', 'skipped')]
    taken_count = sum(1 for l in past_logs if l.status == 'taken')
    total       = len(past_logs)
    pct         = round(taken_count / total * 100) if total else 0

    story.append(Paragraph(
        f'Соблюдение курса: {pct}% | Принято: {taken_count} | Пропущено: {total - taken_count}',
        normal_style
    ))
    story.append(Spacer(1, 6 * mm))

    if course.doctor_comment:
        story.append(Paragraph(f'Комментарий врача: {course.doctor_comment}', normal_style))
        story.append(Spacer(1, 4 * mm))

    STATUS_RU = {
        'taken': 'Принято', 'missed': 'Пропущено',
        'pending': 'Ожидает', 'skipped': 'Пропущено'
    }

    header = ['Дата', 'Препарат', 'Доза', 'Запланировано', 'Принято', 'Статус']
    table_data = [header]

    for log in sorted(logs_all, key=lambda l: l.scheduled_at):
        item      = log.course_item
        med_name  = item.medicine.name if item.medicine else '-'
        dose      = f'{item.dosage} {item.unit}'
        taken_str = log.taken_at.strftime('%H:%M') if log.taken_at else '-'
        status_ru = STATUS_RU.get(log.status, log.status)
        table_data.append([
            log.scheduled_at.strftime('%d.%m.%Y'),
            med_name,
            dose,
            log.scheduled_at.strftime('%H:%M'),
            taken_str,
            status_ru,
        ])

    if len(table_data) > 1:
        col_widths = [25*mm, 45*mm, 20*mm, 25*mm, 25*mm, 25*mm]
        table = Table(table_data, colWidths=col_widths, repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (-1, 0),  colors.HexColor('#1a1a18')),
            ('TEXTCOLOR',     (0, 0), (-1, 0),  colors.white),
            ('FONTNAME',      (0, 0), (-1, -1), font_name),
            ('FONTSIZE',      (0, 0), (-1, 0),  9),
            ('FONTSIZE',      (0, 1), (-1, -1), 8),
            ('ROWBACKGROUNDS',(0, 1), (-1, -1),
             [colors.white, colors.HexColor('#eaf3f9')]),
            ('GRID',          (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
            ('TOPPADDING',    (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('LEFTPADDING',   (0, 0), (-1, -1), 6),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 6),
        ]))
        story.append(table)
    else:
        story.append(Paragraph('Нет записей о приёмах', normal_style))

    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph(
        f'Отчёт сформирован: {datetime.now().strftime("%d.%m.%Y %H:%M")}',
        subtitle_style
    ))

    doc.build(story)
    return tmp.name