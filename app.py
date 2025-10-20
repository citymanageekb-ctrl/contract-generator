"""
Сити Менедж Снег - Генератор договоров
Веб-приложение с Claude AI для автоматического заполнения договоров
Версия: 1.0
"""

from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for
from functools import wraps
import anthropic
import os
import json
import base64
from datetime import datetime
from pathlib import Path
import sqlite3
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB

# ==================== КОНФИГУРАЦИЯ ====================

# API ключ (задается через переменную окружения)
API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Пароль для доступа (задается через переменную окружения)
APP_PASSWORD = os.getenv("APP_PASSWORD", "sneg2025")  # По умолчанию: sneg2025

# Модели
MODEL_HAIKU = "claude-3-5-haiku-20241022"
MODEL_SONNET = "claude-sonnet-4-20250514"

# Системный промпт
SYSTEM_PROMPT = """Ты — ассистент по сборке юридических документов в HTML. Твоя задача: по пользовательскому запросу собрать единый HTML-документ из нескольких HTML-шаблонов проекта, корректно заполнить поля, сохранить исходное форматирование и выдать результат в одном файле.

ВАЖНО: Все плейсхолдеры имеют вид:
<!--FIELD:ИмяПоля--><span data-ph="ИмяПоля">[ИмяПоля]</span><!--/FIELD-->

Разрешено изменять ТОЛЬКО текст внутри <span data-ph="...">...</span>.

Источник данных и приоритет:
1. Явные данные из текста запроса пользователя
2. Файлы, прикрепленные пользователем
3. Если значение не найдено — задай уточняющий вопрос

Автологика:
- Номер договора: если не указан, генерируй как ДДММГГГГ/N
- Дата договора: текущая дата ({current_date}), если не указана иная
- Реквизиты: каждый пункт с новой строки ВНУТРИ ячейки (используй <br>)

КРИТИЧЕСКИ ВАЖНО:
1. Верни ТОЛЬКО готовый HTML-документ без дополнительных объяснений
2. НЕ используй markdown-блоки (```html)
3. НЕ добавляй текст до или после HTML
4. Если нужно задать вопрос - верни JSON: {{"question": "текст вопроса", "missing_fields": ["поле1", "поле2"]}}"""

# Справочник Озон
OZON_DIRECTORY = {
    'Озон Сургут': 'ООО "Интернет Решения" г. Сургут, Нефтеюганское шоссе, д. 22/2',
    'Озон Ноябрьск': 'ООО "Интернет Решения" ЯНАО, г. Ноябрьск, промузел Пелей, 12-й проезд, панель XIV',
    'Озон Тагил': 'ООО "Интернет Решения" г. Нижний Тагил, Свердловское шоссе, д. 65',
    'Озон Тюмень': 'ООО "Интернет Решения" г. Тюмень, ул. 30 лет Победы',
    'Озон Миасс': 'ООО "Интернет Решения" г. Миасс, ул. 60 лет Октября, стр. 1/1',
    'Озон Челябинск': 'ООО "Интернет Решения" г. Челябинск, ул. Линейная, д. 59/1',
    'Озон Екатеринбург Логопарк': 'ООО "Интернет Решения" г. Екатеринбург, логопарк Кольцовский, 15',
    'Озон Екатеринбург Черняховского': 'ООО "Интернет Решения" г. Екатеринбург, ул. Черняховского, д. 104'
}

# Типы договоров и их части
CONTRACT_TYPES = {
    'city_manage_gov': {
        'name': 'Договор Сити Менедж Гос',
        'parts': ['Договор_Сити_Менедж_Гос.html', 'СМ_Приложение_1_Гос.html', 'СМ_Приложение_2.html']
    },
    'city_manage_ozon': {
        'name': 'Договор Сити Менедж Озон',
        'parts': ['Договор_Сити_Менедж_Озон.html', 'СМ_Приложение_1_Озон.html', 'СМ_Приложение_2.html']
    },
    'city_manage_perekrestok': {
        'name': 'Договор Сити Менедж Перекрёсток',
        'parts': ['Договор_Сити_Менедж_Перекрёсток.html', 'СМ_Приложение_1_Перекрёсток.html', 'СМ_Приложение_2.html']
    },
    'skorichenko_gov': {
        'name': 'Договор Скориченко Гос',
        'parts': ['Договор_Скориченко_Гос.html', 'СК_Приложение_1_Гос.html', 'СК_Приложение_2_Гос.html']
    },
    'skorichenko_monetka': {
        'name': 'Договор Скориченко Монетка',
        'parts': ['Договор_Скориченко_Монетка.html', 'СК_Приложение_1_Монетка.html', 'СК_Приложение_2_Монетка.html']
    }
}

# ==================== БАЗА ДАННЫХ ====================

def init_db():
    """Инициализация базы данных для истории"""
    conn = sqlite3.connect('contracts_history.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS contracts
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  contract_type TEXT NOT NULL,
                  user_data TEXT NOT NULL,
                  generated_html TEXT NOT NULL,
                  model_used TEXT NOT NULL,
                  cost_estimate REAL,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

def save_to_history(contract_type, user_data, generated_html, model_used, cost_estimate):
    """Сохранение договора в историю"""
    conn = sqlite3.connect('contracts_history.db')
    c = conn.cursor()
    c.execute('''INSERT INTO contracts (contract_type, user_data, generated_html, model_used, cost_estimate)
                 VALUES (?, ?, ?, ?, ?)''',
              (contract_type, user_data, generated_html, model_used, cost_estimate))
    conn.commit()
    conn.close()

def get_history(limit=50):
    """Получение истории договоров"""
    conn = sqlite3.connect('contracts_history.db')
    c = conn.cursor()
    c.execute('''SELECT id, contract_type, user_data, created_at, model_used, cost_estimate
                 FROM contracts ORDER BY created_at DESC LIMIT ?''', (limit,))
    rows = c.fetchall()
    conn.close()
    return rows

def get_contract_by_id(contract_id):
    """Получение договора по ID"""
    conn = sqlite3.connect('contracts_history.db')
    c = conn.cursor()
    c.execute('SELECT * FROM contracts WHERE id = ?', (contract_id,))
    row = c.fetchone()
    conn.close()
    return row

# ==================== АУТЕНТИФИКАЦИЯ ====================

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password')
        if password == APP_PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error='Неверный пароль')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

# ==================== ОСНОВНЫЕ РОУТЫ ====================

@app.route('/')
@login_required
def index():
    """Главная страница"""
    return render_template('index.html', contract_types=CONTRACT_TYPES)

@app.route('/history')
@login_required
def history():
    """Страница истории"""
    contracts = get_history()
    return render_template('history.html', contracts=contracts, contract_types=CONTRACT_TYPES)

@app.route('/history/<int:contract_id>')
@login_required
def view_contract(contract_id):
    """Просмотр конкретного договора"""
    contract = get_contract_by_id(contract_id)
    if contract:
        return contract[3]  # generated_html
    return "Договор не найден", 404

# ==================== API ENDPOINTS ====================

@app.route('/api/generate', methods=['POST'])
@login_required
def generate_contract():
    """Генерация договора через Claude API"""
    try:
        data = request.json
        contract_type = data.get('contract_type')
        user_input = data.get('user_input')
        files_data = data.get('files', [])
        use_sonnet = data.get('use_sonnet', False)
        conversation_history = data.get('conversation_history', [])

        if not contract_type or not user_input:
            return jsonify({'error': 'Не указан тип договора или данные'}), 400

        # Выбор модели
        model = MODEL_SONNET if use_sonnet else MODEL_HAIKU

        # Загрузка шаблонов
        templates_dir = Path('contracts_templates')
        contract_config = CONTRACT_TYPES.get(contract_type)
        
        if not contract_config:
            return jsonify({'error': 'Неизвестный тип договора'}), 400

        # Чтение HTML-шаблонов
        template_parts = []
        for part_file in contract_config['parts']:
            template_path = templates_dir / part_file
            if template_path.exists():
                with open(template_path, 'r', encoding='utf-8') as f:
                    template_parts.append(f.read())

        # Формирование промпта
        current_date = datetime.now().strftime('%d.%m.%Y')
        system_prompt = SYSTEM_PROMPT.format(current_date=current_date)

        # Построение контента для Claude
        content = []

        # Добавляем файлы если есть
        for file_data in files_data:
            file_type = file_data.get('type', '')
            file_content = file_data.get('data', '')

            if 'image' in file_type:
                content.append({
                    'type': 'image',
                    'source': {
                        'type': 'base64',
                        'media_type': file_type,
                        'data': file_content
                    }
                })
            elif 'pdf' in file_type or file_data.get('name', '').endswith('.pdf'):
                content.append({
                    'type': 'document',
                    'source': {
                        'type': 'base64',
                        'media_type': 'application/pdf',
                        'data': file_content
                    }
                })

        # Текстовый промпт
        template_text = '\n\n'.join([f'=== ЧАСТЬ {i+1} ===\n{part}' for i, part in enumerate(template_parts)])
        
        user_message = f"""Тип договора: {contract_config['name']}

HTML-шаблоны для сборки:
{template_text}

Справочник для Озон (используй ТОЛЬКО если выбран Договор Сити Менедж Озон):
{json.dumps(OZON_DIRECTORY, ensure_ascii=False, indent=2)}

Данные от пользователя:
{user_input}

ИНСТРУКЦИЯ: 
1. Проанализируй все предоставленные данные (текст + прикрепленные файлы)
2. Собери единый HTML-документ из указанных частей
3. Заполни ВСЕ плейсхолдеры данными
4. Если каких-то данных не хватает - верни JSON с вопросом: {{"question": "...", "missing_fields": [...]}}
5. Если все данные есть - верни ТОЛЬКО готовый HTML без markdown-блоков

ВАЖНО: Не используй markdown ```html, верни чистый HTML!"""

        content.append({
            'type': 'text',
            'text': user_message
        })

        # Вызов Claude API
        client = anthropic.Anthropic(api_key=API_KEY)
        
        messages = conversation_history + [{'role': 'user', 'content': content}]

        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt,
            messages=messages
        )

        assistant_response = response.content[0].text

        # Подсчет стоимости
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        
        if model == MODEL_HAIKU:
            cost = (input_tokens * 0.25 / 1000000) + (output_tokens * 1.25 / 1000000)
        else:
            cost = (input_tokens * 3 / 1000000) + (output_tokens * 15 / 1000000)

        # Проверка - это вопрос или готовый договор?
        try:
            json_response = json.loads(assistant_response)
            if 'question' in json_response:
                return jsonify({
                    'status': 'question',
                    'question': json_response['question'],
                    'missing_fields': json_response.get('missing_fields', []),
                    'conversation': messages + [{'role': 'assistant', 'content': assistant_response}],
                    'cost': round(cost, 4)
                })
        except json.JSONDecodeError:
            pass

        # Очистка HTML от markdown блоков
        clean_html = assistant_response.replace('```html\n', '').replace('```html', '').replace('```\n', '').replace('```', '').strip()

        # Сохранение в историю
        save_to_history(contract_type, user_input, clean_html, model, cost)

        return jsonify({
            'status': 'success',
            'html': clean_html,
            'cost': round(cost, 4),
            'model': model,
            'tokens': {
                'input': input_tokens,
                'output': output_tokens
            }
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==================== ЗАПУСК ====================

if __name__ == '__main__':
    init_db()
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
