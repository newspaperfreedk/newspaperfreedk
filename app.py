from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.utils import secure_filename
from datetime import datetime
import os
import requests
import PyPDF2
from docx import Document
import openai
from dotenv import load_dotenv
import time
import random
import string
from twilio.rest import Client
import logging
from config import Config
from extensions import db, login_manager, migrate, csrf
from models import User, News
from apscheduler.schedulers.background import BackgroundScheduler
from forms import LoginForm
from admin_routes import admin  # Import the admin blueprint

app = Flask(__name__)
app.config.from_object(Config)
app.config['DEBUG'] = True  # Enable debug mode
app.config['PROPAGATE_EXCEPTIONS'] = True  # Propagate exceptions to the error handler
app.config['TEMPLATES_AUTO_RELOAD'] = True  # Auto-reload templates

# Initialize extensions
db.init_app(app)
login_manager.init_app(app)
migrate.init_app(app, db)
csrf.init_app(app)
login_manager.login_view = 'login'

# Register blueprints
app.register_blueprint(admin)  # Register the admin blueprint

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.DEBUG)  # Set logging level to DEBUG
logger = logging.getLogger(__name__)

# Configure Twilio credentials
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')

# Initialize Twilio client
try:
    twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
except Exception as e:
    print(f"Error initializing Twilio client: {str(e)}")
    twilio_client = None

# Configure allowed file extensions
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
ALLOWED_DOCUMENT_EXTENSIONS = {'pdf', 'doc', 'docx', 'txt'}

# Configure OpenAI API
openai.api_key = os.getenv('OPENAI_API_KEY')
openai.api_base = "https://api.openai.com/v1"
openai.api_type = "open_ai"
openai.api_version = None

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def allowed_document(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_DOCUMENT_EXTENSIONS

@app.route('/')
@app.route('/home')
def home():
    try:
        # Ensure database is initialized
        with app.app_context():
            db.create_all()
            
        # Query news and categories
        news = News.query.filter_by(is_draft=False).order_by(News.date_posted.desc()).all()
        categories = db.session.query(News.category).distinct().all()
        
        return render_template('index.html', news=news, categories=categories)
    except Exception as e:
        app.logger.error(f"Error in home route: {str(e)}")
        flash('An error occurred while loading the page. Please try again later.', 'danger')
        return render_template('index.html', news=[], categories=[])

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    
    form = LoginForm()
    if form.validate_on_submit():
        username = form.username.data
        password = form.password.data
        remember = form.remember.data
        
        user = User.query.filter_by(username=username).first()
        if user and user.verify_password(password):
            login_user(user, remember=remember)
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('home'))
        else:
            flash('Invalid username or password', 'danger')
    
    return render_template('auth/login.html', form=form)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        if password != confirm_password:
            flash('Passwords do not match', 'danger')
            return redirect(url_for('register'))
        
        if User.query.filter_by(username=username).first():
            flash('Username already exists', 'danger')
            return redirect(url_for('register'))
        
        if User.query.filter_by(email=email).first():
            flash('Email already registered', 'danger')
            return redirect(url_for('register'))
        
        user = User(username=username, email=email)
        user.password = password  # Use the password setter
        db.session.add(user)
        db.session.commit()
        
        flash('Registration successful! Please login.', 'success')
        return redirect(url_for('login'))
    
    return render_template('auth/register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

@app.route('/category/<category>')
def category_news(category):
    news = News.query.filter_by(category=category).order_by(News.date_posted.desc()).all()
    categories = db.session.query(News.category).distinct().all()
    return render_template('category.html', news=news, categories=categories, current_category=category)

@app.route('/news/<int:news_id>')
def news_detail(news_id):
    news_item = News.query.get_or_404(news_id)
    
    # Increment view count
    news_item.views += 1
    db.session.commit()
    
    # Get related news (same category, excluding current article)
    related_news = News.query.filter(
        News.category == news_item.category,
        News.id != news_item.id,
        News.is_draft == False
    ).order_by(News.views.desc()).limit(5).all()
    
    return render_template('news_detail.html', news=news_item, related_news=related_news)

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        message = request.form.get('message')
        
        # Here you can add code to send email or save to database
        flash('Thank you for your message! We will get back to you soon.', 'success')
        return redirect(url_for('contact'))
    
    return render_template('contact.html')

@app.route('/profile')
@login_required
def profile():
    try:
        # Get user's news articles
        user_news = News.query.filter_by(author=current_user.username).order_by(News.date_posted.desc()).all()
        
        return render_template('profile.html', user=current_user, news=user_news)
    except Exception as e:
        app.logger.error(f"Error in profile route: {str(e)}")
        flash('An error occurred while loading your profile.', 'danger')
        return redirect(url_for('home'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True) 