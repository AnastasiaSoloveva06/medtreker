from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):
    """Пользователи системы (пациенты и врачи)"""
    __tablename__ = 'user'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(50), nullable=False, unique=True)
    email = db.Column(db.String(100), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(10), nullable=False) 
    full_name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

 
    assigned_courses = db.relationship(
        'Course',
        foreign_keys='Course.doctor_id',
        backref=db.backref('doctor', lazy='joined'),
        lazy='dynamic'
    )
 
    patient_courses = db.relationship(
        'Course',
        foreign_keys='Course.patient_id',
        backref=db.backref('patient', lazy='joined'),
        lazy='dynamic'
    )

    intake_logs = db.relationship('IntakeLog', backref='patient_user', lazy='dynamic')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def is_doctor(self):
        return self.role == 'doctor'

    def is_patient(self):
        return self.role == 'patient'

    def __repr__(self):
        return f'<User {self.username} [{self.role}]>'


class Medicine(db.Model):
    """Глобальный справочник лекарственных средств"""
    __tablename__ = 'medicine'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(100), nullable=False)
    dosage_form = db.Column(db.String(50), nullable=True)  
    description = db.Column(db.Text, nullable=True)

    course_items = db.relationship('CourseItem', backref='medicine', lazy='dynamic')

    def __repr__(self):
        return f'<Medicine {self.name}>'


class Course(db.Model):
    """Курс лечения"""
    __tablename__ = 'course'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    doctor_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', ondelete='SET NULL'),
        nullable=True  
    )
    patient_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False
    )
    title = db.Column(db.String(150), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='active')
    goal = db.Column(db.Text, nullable=True)           
    doctor_comment = db.Column(db.Text, nullable=True)  
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    
    items = db.relationship(
        'CourseItem',
        backref='course',
        lazy='dynamic',
        cascade='all, delete-orphan'
    )

    def get_compliance(self):
        """Процент соблюдения курса"""
        from datetime import date
        today = date.today()
        logs = []
        for item in self.items:
            item_logs = IntakeLog.query.filter(
                IntakeLog.course_item_id == item.id,
                IntakeLog.scheduled_at <= datetime.combine(today, datetime.max.time())
            ).all()
            logs.extend(item_logs)

        if not logs:
            return 0
        taken = sum(1 for l in logs if l.status == 'taken')
        return round(taken / len(logs) * 100) if logs else 0

    def get_status_label(self):
        pct = self.get_compliance()
        if pct >= 75:
            return 'ok', 'Хорошо'
        elif pct >= 50:
            return 'warn', 'Внимание'
        else:
            return 'bad', 'Критично'

    def __repr__(self):
        return f'<Course {self.title}>'


class CourseItem(db.Model):
    """Конкретный препарат в рамках курса"""
    __tablename__ = 'course_item'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    course_id = db.Column(
        db.Integer,
        db.ForeignKey('course.id', ondelete='CASCADE'),
        nullable=False
    )
    medicine_id = db.Column(
        db.Integer,
        db.ForeignKey('medicine.id'),
        nullable=False
    )
    dosage = db.Column(db.Float, nullable=False)
    unit = db.Column(db.String(20), nullable=False)        
    schedule_times = db.Column(db.Text, nullable=False)     
    days_of_week = db.Column(db.Text, nullable=False)      

    intake_logs = db.relationship(
        'IntakeLog',
        backref='course_item',
        lazy='dynamic',
        cascade='all, delete-orphan'
    )

    prescription_photo = db.relationship(
        'PrescriptionPhoto',
        backref='course_item',
        uselist=False,
        cascade='all, delete-orphan'
    )

    def get_times_list(self):
        import json
        try:
            return json.loads(self.schedule_times)
        except Exception:
            return []

    def get_days_list(self):
        import json
        try:
            return json.loads(self.days_of_week)
        except Exception:
            return []

    def __repr__(self):
        return f'<CourseItem {self.medicine.name if self.medicine else self.medicine_id}>'


class IntakeLog(db.Model):
    """Журнал фактического и запланированного приёма доз"""
    __tablename__ = 'intake_log'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    course_item_id = db.Column(
        db.Integer,
        db.ForeignKey('course_item.id', ondelete='CASCADE'),
        nullable=False
    )
    patient_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id'),
        nullable=False
    )
    scheduled_at = db.Column(db.DateTime, nullable=False)
    taken_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(
        db.String(20),
        nullable=False,
        default='pending'
    )  
    note = db.Column(db.Text, nullable=True)

    def __repr__(self):
        return f'<IntakeLog {self.id} {self.status}>'


class PrescriptionPhoto(db.Model):
    """Фотографии рецептов, прикреплённые к позиции курса лечения"""
    __tablename__ = 'prescription_photo'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    course_item_id = db.Column(
        db.Integer,
        db.ForeignKey('course_item.id', ondelete='CASCADE'),
        nullable=False
    )
    file_path = db.Column(db.String(255), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f'<PrescriptionPhoto {self.file_path}>'