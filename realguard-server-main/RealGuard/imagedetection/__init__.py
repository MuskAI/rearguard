from datetime import timedelta

from flask import Flask, render_template, session, redirect
from .views import detection
from .views import login
from .views import historical_record
from .views import retrieve
from .views import profile
from .views import api


def creat_app():
    app = Flask(__name__)
    app.secret_key = 'gdq821821'
    app.config.update(
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_REFRESH_EACH_REQUEST=True,
    )

    app.register_blueprint(detection.image_upload_blueprint)
    app.register_blueprint(login.login_blueprint)
    app.register_blueprint(historical_record.historical_record_blueprint)
    app.register_blueprint(retrieve.retrieve_blueprint)
    app.register_blueprint(profile.profile_blueprint)
    app.register_blueprint(api.api_blueprint)

    @app.route('/')
    def root():
        if 'user_info' not in session or session['user_info'] is None:
            return redirect('/login')
        return redirect('/index')

    @app.route('/index')
    def index():
        if 'user_info' not in session or session['user_info'] is None:
            return render_template('login.html')
        return render_template('index.html')

    @app.route('/introduce')
    def introduce():
        if 'user_info' not in session or session['user_info'] is None:
            return render_template('login.html')
        return render_template('introduce.html')

    @app.route('/retrieve_result')
    def retrieve_result():
        """检索结果展示页（从历史记录进入）"""
        if 'user_info' not in session or session['user_info'] is None:
            return render_template('login.html')
        return render_template('retrieve_result.html')

    return app
