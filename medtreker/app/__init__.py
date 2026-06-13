import os
from flask import Flask, render_template
from flask_login import LoginManager

from app.models import db, User
from config import Config


def create_app():
    app = Flask(__name__, static_folder='static', template_folder='templates')
    app.config.from_object(Config)

    db.init_app(app)

    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Пожалуйста, войдите в систему'
    login_manager.login_message_category = 'info'

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    from app.routes.auth import auth_bp
    from app.routes.patient import patient_bp
    from app.routes.doctor import doctor_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(patient_bp)
    app.register_blueprint(doctor_bp)


    from datetime import timedelta, date as date_cls, datetime as datetime_cls

    @app.context_processor
    def inject_globals():
        return {
            'timedelta': timedelta,
            'date': date_cls,
            'datetime': datetime_cls,
        }

    # Обработчики ошибок
    @app.errorhandler(403)
    def forbidden(e):
        return render_template('shared/403.html'), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template('shared/404.html'), 404

    #  папка uploads существует
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    # Создание таблиц и наполнение справочника лекарствами
    with app.app_context():
        db.create_all()
        _seed_medicines()

    return app


def _seed_medicines():
    """Заполнение справочника лекарств начальными данными"""
    from app.models import Medicine
    if Medicine.query.count() > 0:
        return
    medicines = [
        ('Амоксициллин', 'капсулы', 'Антибиотик широкого спектра действия'),
        ('Ибупрофен', 'таблетки', 'Нестероидное противовоспалительное средство'),
        ('Витамин D3', 'капсулы', 'Витаминный препарат'),
        ('Омега-3', 'капсулы', 'Полиненасыщенные жирные кислоты'),
        ('Лизиноприл', 'таблетки', 'Ингибитор АПФ, применяется при гипертонии'),
        ('Амлодипин', 'таблетки', 'Блокатор кальциевых каналов'),
        ('Метформин', 'таблетки', 'Гипогликемическое средство при диабете 2 типа'),
        ('Аспирин', 'таблетки', 'Антиагрегантное и противовоспалительное средство'),
        ('Левотироксин', 'таблетки', 'Гормон щитовидной железы'),
        ('Пантопразол', 'таблетки', 'Ингибитор протонной помпы'),
        ('Цетиризин', 'таблетки', 'Антигистаминный препарат'),
        ('Магний B6', 'таблетки', 'Минерально-витаминный комплекс'),
    ]
    for name, form, desc in medicines:
        med = Medicine(name=name, dosage_form=form, description=desc)
        db.session.add(med)
    db.session.commit()
