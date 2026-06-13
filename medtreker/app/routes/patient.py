import json
import os
from datetime import datetime, date, timedelta
from flask import (Blueprint, render_template, redirect, url_for,
                   flash, request, abort, current_app, send_file, jsonify)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from app.models import db, User, Course, CourseItem, IntakeLog, PrescriptionPhoto, Medicine
from app.utils import generate_scheduled_logs, allowed_file, export_history_pdf

patient_bp = Blueprint('patient', __name__, url_prefix='/patient')


def patient_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_patient():
            abort(403)
        return f(*args, **kwargs)
    return decorated


# Дашборд

@patient_bp.route('/dashboard')
@login_required
@patient_required
def dashboard():
    today = date.today()
    courses = Course.query.filter_by(patient_id=current_user.id).order_by(Course.created_at.desc()).all()

    today_start = datetime.combine(today, datetime.min.time())
    today_end   = datetime.combine(today, datetime.max.time())
    today_logs = (IntakeLog.query
                  .join(CourseItem)
                  .join(Course)
                  .filter(
                      Course.patient_id == current_user.id,
                      IntakeLog.scheduled_at >= today_start,
                      IntakeLog.scheduled_at <= today_end
                  )
                  .order_by(IntakeLog.scheduled_at)
                  .all())

    all_logs = (IntakeLog.query
                .join(CourseItem)
                .join(Course)
                .filter(
                    Course.patient_id == current_user.id,
                    IntakeLog.scheduled_at <= today_end
                ).all())
    taken_count = sum(1 for l in all_logs if l.status == 'taken')
    total_past  = sum(1 for l in all_logs if l.status in ('taken', 'missed', 'skipped'))
    compliance  = round(taken_count / total_past * 100) if total_past else 0
    missed_today = sum(1 for l in today_logs if l.status == 'missed')
    active_count = sum(1 for c in courses if c.status == 'active')
    conflicts    = _find_conflicts(today_logs)

    return render_template('patient/dashboard.html',
                           courses=courses,
                           today_logs=today_logs,
                           compliance=compliance,
                           missed_today=missed_today,
                           active_count=active_count,
                           conflicts=conflicts,
                           today=today)


def _find_conflicts(logs):
    from collections import defaultdict
    by_time = defaultdict(list)
    for log in logs:
        t = log.scheduled_at.strftime('%H:%M')
        by_time[t].append(log)
    conflicts = []
    for t, ls in by_time.items():
        if len(ls) > 1:
            names = ', '.join(
                f"{l.course_item.medicine.name} {l.course_item.dosage} {l.course_item.unit}"
                for l in ls
            )
            conflicts.append({'time': t, 'names': names})
    return conflicts


# Дневник приёмов

@patient_bp.route('/diary')
@login_required
@patient_required
def diary():
    date_str = request.args.get('date', date.today().isoformat())
    try:
        selected_date = date.fromisoformat(date_str)
    except ValueError:
        selected_date = date.today()

    day_start = datetime.combine(selected_date, datetime.min.time())
    day_end   = datetime.combine(selected_date, datetime.max.time())

    logs = (IntakeLog.query
            .join(CourseItem)
            .join(Course)
            .filter(
                Course.patient_id == current_user.id,
                IntakeLog.scheduled_at >= day_start,
                IntakeLog.scheduled_at <= day_end
            )
            .order_by(IntakeLog.scheduled_at)
            .all())

    week_start = selected_date - timedelta(days=selected_date.weekday())
    week_days  = [week_start + timedelta(days=i) for i in range(7)]


    week_statuses = {}
    for day in week_days:
        d_start = datetime.combine(day, datetime.min.time())
        d_end   = datetime.combine(day, datetime.max.time())
        day_logs = (IntakeLog.query
                    .join(CourseItem)
                    .join(Course)
                    .filter(
                        Course.patient_id == current_user.id,
                        IntakeLog.scheduled_at >= d_start,
                        IntakeLog.scheduled_at <= d_end
                    ).all())
        if not day_logs:
            week_statuses[day] = 'none'
        elif any(l.status == 'missed' or (l.status == 'pending' and day < date.today()) for l in day_logs):
            week_statuses[day] = 'missed'
        elif any(l.status == 'pending' and day == date.today() for l in day_logs):
            week_statuses[day] = 'pending'
        elif all(l.status == 'taken' for l in day_logs):
            week_statuses[day] = 'taken'
        elif any(l.status == 'taken' for l in day_logs):
            week_statuses[day] = 'partial'
        else:
            week_statuses[day] = 'pending'

    return render_template('patient/diary.html',
                           logs=logs,
                           selected_date=selected_date,
                           week_days=week_days,
                           week_statuses=week_statuses,
                           today=date.today())


#  Отметить приём 

@patient_bp.route('/intake/<int:log_id>/mark', methods=['POST'])
@login_required
@patient_required
def mark_intake(log_id):
    log = IntakeLog.query.get_or_404(log_id)
    if log.patient_id != current_user.id:
        abort(403)

    action = request.form.get('action', 'taken')
    log.status = action
    if action == 'taken':
        log.taken_at = datetime.utcnow()
    db.session.commit()

    if 'photo' in request.files:
        file = request.files['photo']
        if file and file.filename and allowed_file(file.filename, current_app.config['ALLOWED_EXTENSIONS']):
            filename = secure_filename(
                f"rx_{log.course_item_id}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{file.filename}")
            file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
            course_item = log.course_item
            if course_item.prescription_photo:
                course_item.prescription_photo.file_path = filename
                course_item.prescription_photo.uploaded_at = datetime.utcnow()
            else:
                db.session.add(PrescriptionPhoto(course_item_id=course_item.id, file_path=filename))
            db.session.commit()

    next_url = request.form.get('next', url_for('patient.diary',
                                date=log.scheduled_at.date().isoformat()))
    return redirect(next_url)


# Просмотр фото рецепта

@patient_bp.route('/photo/<int:log_id>')
@login_required
@patient_required
def view_photo(log_id):
    log = IntakeLog.query.get_or_404(log_id)
    if log.patient_id != current_user.id:
        abort(403)
    course_item = log.course_item
    if not course_item.prescription_photo:
        abort(404)
    upload_dir = current_app.config['UPLOAD_FOLDER']
    filename = course_item.prescription_photo.file_path
    return send_file(
        os.path.join(upload_dir, filename),
        mimetype='image/jpeg'
    )


# Загрузка фото рецепта 

@patient_bp.route('/intake/<int:log_id>/upload_photo', methods=['POST'])
@login_required
@patient_required
def upload_photo(log_id):
    log = IntakeLog.query.get_or_404(log_id)
    if log.patient_id != current_user.id:
        abort(403)
    if 'photo' not in request.files:
        flash('Файл не выбран', 'error')
        return redirect(request.referrer or url_for('patient.diary'))

    file = request.files['photo']
    if file and allowed_file(file.filename, current_app.config['ALLOWED_EXTENSIONS']):
        filename = secure_filename(
            f"rx_{log.course_item_id}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{file.filename}")
        file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
        course_item = log.course_item
        if course_item.prescription_photo:
            course_item.prescription_photo.file_path = filename
            course_item.prescription_photo.uploaded_at = datetime.utcnow()
        else:
            db.session.add(PrescriptionPhoto(course_item_id=course_item.id, file_path=filename))
        db.session.commit()
    return redirect(request.referrer or url_for('patient.diary'))


#  Курсы 

@patient_bp.route('/courses')
@login_required
@patient_required
def courses():
    all_courses = (Course.query
                   .filter_by(patient_id=current_user.id)
                   .order_by(Course.created_at.desc())
                   .all())
    return render_template('patient/courses.html', courses=all_courses)


@patient_bp.route('/courses/add', methods=['GET', 'POST'])
@login_required
@patient_required
def add_course():
    medicines_objs = Medicine.query.order_by(Medicine.name).all()
    medicines = [dict(id=m.id, name=m.name, dosage_form=m.dosage_form or '') for m in medicines_objs]
    today_date = date.today()

    if request.method == 'POST':
        title     = request.form.get('title', '').strip()
        start_str = request.form.get('start_date', '')
        end_str   = request.form.get('end_date', '')
        goal      = request.form.get('goal', '').strip()

        try:
            start_date = date.fromisoformat(start_str)
            end_date   = date.fromisoformat(end_str)
        except ValueError:
            flash('Неверный формат даты', 'error')
            return render_template('patient/add_course.html',
                                   medicines=medicines, today=today_date)

        if not title:
            flash('Введите название курса', 'error')
            return render_template('patient/add_course.html',
                                   medicines=medicines, today=today_date)

        course = Course(
            patient_id=current_user.id,
            doctor_id=None,
            title=title,
            start_date=start_date,
            end_date=end_date,
            status='active',
            goal=goal or None,
            doctor_comment=None
        )
        db.session.add(course)
        db.session.flush()

        med_ids    = request.form.getlist('medicine_id[]')
        med_names  = request.form.getlist('medicine_name[]')
        dosages    = request.form.getlist('dosage[]')
        units      = request.form.getlist('unit[]')
        times_list = request.form.getlist('times[]')
        days_list  = request.form.getlist('days[]')

        for i, med_name in enumerate(med_names):
            med_name = med_name.strip()
            if not med_name:
                continue
            mid      = int(med_ids[i]) if i < len(med_ids) and med_ids[i] else 0
            medicine = Medicine.query.get(mid) if mid else None
            if not medicine:
                medicine = Medicine.query.filter_by(name=med_name).first()
            if not medicine:
                medicine = Medicine(name=med_name, dosage_form=units[i] if i < len(units) else 'мг')
                db.session.add(medicine)
                db.session.flush()

            times_raw = times_list[i] if i < len(times_list) else '["08:00"]'
            days_raw  = days_list[i]  if i < len(days_list)  else '["mon","tue","wed","thu","fri","sat","sun"]'

            item = CourseItem(
                course_id=course.id,
                medicine_id=medicine.id,
                dosage=float(dosages[i]) if i < len(dosages) and dosages[i] else 1.0,
                unit=units[i] if i < len(units) else 'мг',
                schedule_times=times_raw,
                days_of_week=days_raw
            )
            db.session.add(item)
            db.session.flush()
            generate_scheduled_logs(item, course, current_user.id)

        db.session.commit()
        flash('Курс успешно добавлен!', 'success')
        return redirect(url_for('patient.dashboard'))

    return render_template('patient/add_course.html',
                           medicines=medicines, today=today_date)


@patient_bp.route('/courses/<int:course_id>')
@login_required
@patient_required
def course_detail(course_id):
    course = Course.query.get_or_404(course_id)
    if course.patient_id != current_user.id:
        abort(403)
    items = course.items.all()
    return render_template('patient/course_detail.html', course=course, items=items)


@patient_bp.route('/courses/<int:course_id>/edit', methods=['GET', 'POST'])
@login_required
@patient_required
def edit_course(course_id):
    course = Course.query.get_or_404(course_id)
    if course.patient_id != current_user.id:
        abort(403)
    if course.doctor_id is not None:
        flash('Нельзя редактировать курс, назначенный врачом', 'error')
        return redirect(url_for('patient.course_detail', course_id=course_id))

    medicines_objs = Medicine.query.order_by(Medicine.name).all()
    medicines = [dict(id=m.id, name=m.name, dosage_form=m.dosage_form or '') for m in medicines_objs]

    if request.method == 'POST':
        course.title = request.form.get('title', course.title).strip()
        try:
            course.start_date = date.fromisoformat(request.form.get('start_date'))
            course.end_date   = date.fromisoformat(request.form.get('end_date'))
        except (ValueError, TypeError):
            flash('Неверный формат даты', 'error')
            return render_template('patient/edit_course.html',
                                   course=course, medicines=medicines)
        db.session.commit()
        flash('Курс обновлён', 'success')
        return redirect(url_for('patient.course_detail', course_id=course_id))

    return render_template('patient/edit_course.html', course=course, medicines=medicines)


@patient_bp.route('/courses/<int:course_id>/delete', methods=['POST'])
@login_required
@patient_required
def delete_course(course_id):
    course = Course.query.get_or_404(course_id)
    if course.patient_id != current_user.id:
        abort(403)
    if course.doctor_id is not None:
        flash('Нельзя удалить курс, назначенный врачом', 'error')
        return redirect(url_for('patient.courses'))
    db.session.delete(course)
    db.session.commit()
    flash('Курс удалён', 'success')
    return redirect(url_for('patient.courses'))


# Статистика 

@patient_bp.route('/statistics')
@login_required
@patient_required
def statistics():
    courses = Course.query.filter_by(patient_id=current_user.id).all()
    stats = []
    for c in courses:
        total = IntakeLog.query.join(CourseItem).filter(
            CourseItem.course_id == c.id,
            IntakeLog.status.in_(['taken', 'missed', 'skipped'])
        ).count()
        taken = IntakeLog.query.join(CourseItem).filter(
            CourseItem.course_id == c.id, IntakeLog.status == 'taken'
        ).count()
        missed = IntakeLog.query.join(CourseItem).filter(
            CourseItem.course_id == c.id, IntakeLog.status == 'missed'
        ).count()
        pct = round(taken / total * 100) if total else 0
        stats.append({'course': c, 'total': total, 'taken': taken,
                      'missed': missed, 'pct': pct})
    return render_template('patient/statistics.html', stats=stats)


# Экспорт PDF 

@patient_bp.route('/courses/<int:course_id>/export_pdf')
@login_required
@patient_required
def export_pdf(course_id):
    course = Course.query.get_or_404(course_id)
    if course.patient_id != current_user.id:
        abort(403)
    pdf_path = export_history_pdf(course, current_user)
    return send_file(pdf_path, as_attachment=True,
                     download_name=f'medtreker_course_{course_id}.pdf',
                     mimetype='application/pdf')


# api поиск препаратов 

@patient_bp.route('/api/medicines/search')
@login_required
def search_medicines():
    q = request.args.get('q', '').strip()
    meds = Medicine.query.filter(Medicine.name.ilike(f'%{q}%')).limit(10).all()
    return jsonify([{'id': m.id, 'name': m.name, 'dosage_form': m.dosage_form or ''} for m in meds])


@patient_bp.route('/api/medicines/create', methods=['POST'])
@login_required
def create_medicine():
    name = request.json.get('name', '').strip()
    dosage_form = request.json.get('dosage_form', 'мг')
    if not name:
        return jsonify({'error': 'Имя обязательно'}), 400
    med = Medicine.query.filter_by(name=name).first()
    if not med:
        med = Medicine(name=name, dosage_form=dosage_form)
        db.session.add(med)
        db.session.commit()
    return jsonify({'id': med.id, 'name': med.name})