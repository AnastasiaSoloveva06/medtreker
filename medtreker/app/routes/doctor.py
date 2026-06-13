import os
from datetime import datetime, date
from flask import (Blueprint, render_template, redirect, url_for,
                   flash, request, abort, current_app, send_file, jsonify)
from flask_login import login_required, current_user

from app.models import db, User, Course, CourseItem, IntakeLog, Medicine
from app.utils import generate_scheduled_logs, export_history_pdf

doctor_bp = Blueprint('doctor', __name__, url_prefix='/doctor')


def doctor_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_doctor():
            abort(403)
        return f(*args, **kwargs)
    return decorated


# Дашборд врача 

@doctor_bp.route('/dashboard')
@login_required
@doctor_required
def dashboard():

    patients_with_courses = (db.session.query(User)
                             .join(Course, Course.patient_id == User.id)
                             .filter(Course.doctor_id == current_user.id)
                             .distinct()
                             .all())

    # Статистика по каждому пациенту
    patient_stats = []
    for patient in patients_with_courses:
        active_courses = (Course.query
                          .filter_by(patient_id=patient.id, doctor_id=current_user.id, status='active')
                          .all())
        if not active_courses:
            continue
        course = active_courses[0]
        pct = course.get_compliance()
        status_key, status_label = course.get_status_label()
        patient_stats.append({
            'patient': patient,
            'course': course,
            'pct': pct,
            'status_key': status_key,
            'status_label': status_label,
            'has_comment': bool(course.doctor_comment)
        })

    total_patients = len(patient_stats)
    active_courses_count = sum(1 for ps in patient_stats)
    low_compliance = sum(1 for ps in patient_stats if ps['pct'] < 50)

    return render_template('doctor/dashboard.html',
                           patient_stats=patient_stats,
                           total_patients=total_patients,
                           active_courses_count=active_courses_count,
                           low_compliance=low_compliance)


# Список пациентов

@doctor_bp.route('/patients')
@login_required
@doctor_required
def patients():
    search = request.args.get('search', '').strip()
    query = (db.session.query(User)
             .join(Course, Course.patient_id == User.id)
             .filter(Course.doctor_id == current_user.id)
             .distinct())
    if search:
        query = query.filter(User.full_name.ilike(f'%{search}%'))
    patients_list = query.all()
    return render_template('doctor/patients.html', patients=patients_list, search=search)


# Карточка пациента 

@doctor_bp.route('/patients/<int:patient_id>')
@login_required
@doctor_required
def patient_card(patient_id):
    patient = User.query.get_or_404(patient_id)
    if patient.role != 'patient':
        abort(404)
    courses = (Course.query
               .filter_by(patient_id=patient_id, doctor_id=current_user.id)
               .order_by(Course.created_at.desc())
               .all())
    course_stats = []
    for course in courses:
        pct = course.get_compliance()
        status_key, status_label = course.get_status_label()
        items = course.items.all()
        item_stats = []
        for item in items:
            total_i = IntakeLog.query.filter_by(course_item_id=item.id).filter(
                IntakeLog.status.in_(['taken', 'missed', 'skipped'])
            ).count()
            taken_i = IntakeLog.query.filter_by(course_item_id=item.id, status='taken').count()
            pct_i = round(taken_i / total_i * 100) if total_i else 0
            item_stats.append({'item': item, 'pct': pct_i,
                               'status_key': 'ok' if pct_i >= 75 else 'warn' if pct_i >= 50 else 'bad'})
        course_stats.append({
            'course': course,
            'pct': pct,
            'status_key': status_key,
            'status_label': status_label,
            'item_stats': item_stats
        })
    return render_template('doctor/patient_card.html',
                           patient=patient,
                           course_stats=course_stats)


#  Удаление курса врачом 

@doctor_bp.route('/courses/<int:course_id>/delete', methods=['POST'])
@login_required
@doctor_required
def delete_course(course_id):
    course = Course.query.get_or_404(course_id)
    if course.doctor_id != current_user.id:
        abort(403)
    patient_id = course.patient_id
    db.session.delete(course)
    db.session.commit()
    flash('Курс удалён', 'success')
    return redirect(url_for('doctor.patient_card', patient_id=patient_id))


#  Назначение курса 

@doctor_bp.route('/assign', methods=['GET', 'POST'])
@login_required
@doctor_required
def assign_course():

    patients_list = User.query.filter_by(role='patient').order_by(User.full_name).all()
    medicines_objs = Medicine.query.order_by(Medicine.name).all()
    medicines = [dict(id=m.id, name=m.name, dosage_form=m.dosage_form or "") for m in medicines_objs]

    if request.method == 'POST':
        patient_id = request.form.get('patient_id', type=int)
        title = request.form.get('title', '').strip()
        start_str = request.form.get('start_date', '')
        end_str = request.form.get('end_date', '')

        if not patient_id or not title:
            flash('Заполните все обязательные поля', 'error')
            return render_template('doctor/assign_course.html',
                                   patients=patients_list, medicines=medicines)

        try:
            start_date = date.fromisoformat(start_str)
            end_date = date.fromisoformat(end_str)
        except ValueError:
            flash('Неверный формат дат', 'error')
            return render_template('doctor/assign_course.html',
                                   patients=patients_list, medicines=medicines)

        patient = User.query.get(patient_id)
        if not patient or patient.role != 'patient':
            flash('Пациент не найден', 'error')
            return render_template('doctor/assign_course.html',
                                   patients=patients_list, medicines=medicines)

        course = Course(
            doctor_id=current_user.id,
            patient_id=patient_id,
            title=title,
            start_date=start_date,
            end_date=end_date,
            status='active'
        )
        db.session.add(course)
        db.session.flush()

        med_ids = request.form.getlist('medicine_id[]')
        med_names = request.form.getlist('medicine_name[]')
        dosages = request.form.getlist('dosage[]')
        units = request.form.getlist('unit[]')
        times_list = request.form.getlist('times[]')
        days_list = request.form.getlist('days[]')

        for i in range(len(med_names)):
            med_name = med_names[i].strip() if i < len(med_names) else ''
            if not med_name:
                continue
            mid = int(med_ids[i]) if i < len(med_ids) and med_ids[i] else 0
            medicine = Medicine.query.get(mid) if mid else Medicine.query.filter_by(name=med_name).first()
            if not medicine:
                medicine = Medicine(name=med_name, dosage_form=units[i] if i < len(units) else 'мг')
                db.session.add(medicine)
                db.session.flush()

            times_raw = times_list[i] if i < len(times_list) else '["08:00"]'
            days_raw = days_list[i] if i < len(days_list) else '["mon","tue","wed","thu","fri","sat","sun"]'

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
            generate_scheduled_logs(item, course, patient_id)

        db.session.commit()
        flash('Курс успешно назначен пациенту!', 'success')
        return redirect(url_for('doctor.dashboard'))

    return render_template('doctor/assign_course.html',
                           patients=patients_list, medicines=medicines)



# Комментарий к курсу

@doctor_bp.route('/courses/<int:course_id>/comment', methods=['POST'])
@login_required
@doctor_required
def add_comment(course_id):
    course = Course.query.get_or_404(course_id)
    if course.doctor_id != current_user.id:
        abort(403)
    comment = request.form.get('comment', '').strip()
    course.doctor_comment = comment
    db.session.commit()
    flash('Комментарий сохранён', 'success')
    return redirect(url_for('doctor.patient_card', patient_id=course.patient_id))


#  Просмотр истории приёмов пациента 

@doctor_bp.route('/courses/<int:course_id>/history')
@login_required
@doctor_required
def course_history(course_id):
    course = Course.query.get_or_404(course_id)
    if course.doctor_id != current_user.id:
        abort(403)

    date_from_str = request.args.get('date_from', course.start_date.isoformat())
    date_to_str = request.args.get('date_to', date.today().isoformat())
    try:
        date_from = date.fromisoformat(date_from_str)
        date_to = date.fromisoformat(date_to_str)
    except ValueError:
        date_from = course.start_date
        date_to = date.today()

    logs = (IntakeLog.query
            .join(CourseItem)
            .filter(
                CourseItem.course_id == course_id,
                IntakeLog.scheduled_at >= datetime.combine(date_from, datetime.min.time()),
                IntakeLog.scheduled_at <= datetime.combine(date_to, datetime.max.time())
            )
            .order_by(IntakeLog.scheduled_at)
            .all())

    return render_template('doctor/course_history.html',
                           course=course,
                           logs=logs,
                           date_from=date_from,
                           date_to=date_to)


# Экспорт истории в PDF 

@doctor_bp.route('/courses/<int:course_id>/export_pdf')
@login_required
@doctor_required
def export_pdf(course_id):
    course = Course.query.get_or_404(course_id)
    if course.doctor_id != current_user.id:
        abort(403)
    pdf_path = export_history_pdf(course, course.patient)
    return send_file(pdf_path, as_attachment=True,
                     download_name=f'medtreker_patient_{course.patient_id}_course_{course_id}.pdf',
                     mimetype='application/pdf')


# Статистика врача 

@doctor_bp.route('/statistics')
@login_required
@doctor_required
def statistics():
    courses = (Course.query
               .filter_by(doctor_id=current_user.id)
               .order_by(Course.created_at.desc())
               .all())
    stats = []
    for c in courses:
        pct = c.get_compliance()
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
        stats.append({'course': c, 'pct': pct, 'total': total,
                      'taken': taken, 'missed': missed})
    return render_template('doctor/statistics.html', stats=stats)



@doctor_bp.route('/api/patients')
@login_required
@doctor_required
def api_patients():
    patients_list = User.query.filter_by(role='patient').all()
    return jsonify([{'id': p.id, 'full_name': p.full_name} for p in patients_list])