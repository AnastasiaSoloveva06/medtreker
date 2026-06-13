from flask import Blueprint, render_template, redirect, url_for, flash, request, session
from flask_login import login_user, logout_user, login_required, current_user
from app.models import db, User

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/', methods=['GET'])
def index():
    if current_user.is_authenticated:
        if current_user.is_doctor():
            return redirect(url_for('doctor.dashboard'))
        return redirect(url_for('patient.dashboard'))
    return redirect(url_for('auth.login'))


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        if current_user.is_doctor():
            return redirect(url_for('doctor.dashboard'))
        return redirect(url_for('patient.dashboard'))

    error = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user, remember=True)
            if user.is_doctor():
                return redirect(url_for('doctor.dashboard'))
            return redirect(url_for('patient.dashboard'))
        error = 'Неверный email или пароль'

    return render_template('auth/login.html', error=error)


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('auth.index'))

    error = None
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        role = request.form.get('role', 'patient')

        if role not in ('doctor', 'patient'):
            error = 'Недопустимая роль'
        elif not full_name or not email or not password:
            error = 'Заполните все обязательные поля'
        elif User.query.filter_by(email=email).first():
            error = 'Пользователь с таким email уже существует'
        else:
            username = email.split('@')[0]
        
            base = username
            i = 1
            while User.query.filter_by(username=username).first():
                username = f'{base}{i}'
                i += 1

            user = User(
                username=username,
                email=email,
                role=role,
                full_name=full_name
            )
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            if user.is_doctor():
                return redirect(url_for('doctor.dashboard'))
            return redirect(url_for('patient.dashboard'))

    return render_template('auth/register.html', error=error)


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))
