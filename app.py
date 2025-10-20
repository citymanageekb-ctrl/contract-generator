"""
Сити Менедж Снег - Генератор договоров
Веб-приложение с Claude AI для автоматического заполнения договоров
Версия: 1.1 (исправлена проблема с пустым HTML)
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
import re
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB

# ==================== КОНФИГУРАЦИЯ ====================

# API ключ (задается через переменную окружения)
API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Пароль для доступа (задается через переменную окружения)
APP_PASSWORD = os.getenv("APP_PASSWORD", "sneg2025")

# Модели
MODEL_HAIKU = "claude-3-5-haiku-20241022"
MODEL_SONNET = "claude-sonnet-4-20250514"

# Системный промпт
SYSTEM_PROMPT = """Ты — ассистент по сборке юридических документов в HTML.

ЗАДАЧА: По запросу пользователя собрать единый HTML-документ из предоставленных шаблонов и заполнить все поля данными.

ВАЖНО О ПЛЕЙСХОЛДЕРАХ:
Все плейсхолдеры имеют вид:
<!--FIELD:ИмяПоля--><span data-ph="ИмяПоля">[ИмяПоля]</span><!--/FIELD-->

Ты МОЖЕШЬ изменять ТОЛЬКО текст внутри <span data-ph="...">ЗДЕСЬ</span>.
НЕ трогай HTML-теги, стили, структуру.

ИСТОЧНИКИ ДАННЫХ (приоритет):
1. Явные данные из текста пользователя
2. Прикрепленные файлы
3. Если не хватает данных - задай вопрос в формате JSON

АВТОЛОГИКА:
- Номер договора: если не указан, генерируй как ДДММГГГГ/N
- Дата договора: {current_date} если не указана
- Реквизиты: каждый пункт с новой строки ВНУТРИ ячейки через <br>

КРИТИЧЕСКИ ВАЖНО:
1. Верни ТОЛЬКО готовый HTML без дополнительного текста
2. НЕ используй markdown блоки ```html или ```
3. НЕ добавляй объяснений до или после HTML
4. Если нужны данные - верни ТОЛЬКО JSON: {{"question": "текст", "missing_fields": ["поле1"]}}
5. HTML должен начинаться с <!DOCTYPE html> или <html> или первого тега из шаблона

ФОРМАТ ОТВЕТА:
- Если все данные есть: верни чистый HTML
- Если нужны данные: верни JSON с вопросом

НЕ ДОБАВЛЯЙ НИКАКОГО ТЕКСТА КРОМЕ HTML ИЛИ JSON!"""

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

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

def extract_html_from_response(text):
    """Извлечение HTML из ответа Claude с очисткой от markdown"""
    # Убираем markdown блоки
    text = re.sub(r'```html\s*', '', text)
    text = re.sub(r'```\s*', '', text)
    text = text.strip()
    
    # Если начинается с DOCTYPE или html тега - возвращаем как есть
    if text.startswith('<!DOCTYPE') or text.startswith('<html') or text.startswith('<div'):
        return text
    
    # Ищем первый HTML тег
    html_start = re.search(r'<(!DOCTYPE|html|div|table|body)', text, re.IGNORECASE)
    if html_start:
        return text[html_start.start():].strip()
    
    return text

def is_json_response(text):
    """Проверка является ли ответ JSON с вопросом"""
    text = text.strip()
    if text.startswith('{') and text.endswith('}'):
        try:
            data = json.loads(text)
            return 'question' in data, data
        except json.JSONDecodeError:
            pass
    return False, None

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
            else:
                return jsonify({'error': f'Шаблон не найден: {part_file}'}), 500

        if not template_parts:
            return jsonify({'error': 'Не удалось загрузить шаблоны'}), 500

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
        template_text = '\n\n=== РАЗДЕЛИТЕЛЬ ЧАСТЕЙ ===\n\n'.join([f'ЧАСТЬ {i+1}:\n{part}' for i, part in enumerate(template_parts)])
        
        user_message = f"""Тип договора: {contract_config['name']}

HTML-ШАБЛОНЫ (собери их в один документ):
{template_text}

{'СПРАВОЧНИК ОЗОН (используй только для Договора Сити Менедж Озон):\n' + json.dumps(OZON_DIRECTORY, ensure_ascii=False, indent=2) if 'ozon' in contract_type else ''}

ДАННЫЕ ОТ ПОЛЬЗОВАТЕЛЯ:
{user_input}

ИНСТРУКЦИЯ:
1. Проанализируй данные (текст + файлы если есть)
2. Собери единый HTML из всех частей шаблона
3. Заполни ВСЕ плейсхолдеры <!--FIELD:...-->...</span><!--/FIELD-->
4. Если данных не хватает - верни JSON: {{"question": "что нужно?", "missing_fields": ["поле1"]}}
5. Если все данные есть - верни ТОЛЬКО чистый HTML БЕЗ markdown блоков

ВАЖНО: 
- НЕ используй ```html
- Верни ТОЛЬКО HTML или ТОЛЬКО JSON
- Никаких объяснений!"""

        content.append({
            'type': 'text',
            'text': user_message
        })

        # Вызов Claude API
        if not API_KEY:
            return jsonify({'error': 'API ключ не настроен. Добавьте ANTHROPIC_API_KEY в переменные окружения.'}), 500

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
        is_json, json_data = is_json_response(assistant_response)
        if is_json and json_data and 'question' in json_data:
            return jsonify({
                'status': 'question',
                'question': json_data['question'],
                'missing_fields': json_data.get('missing_fields', []),
                'conversation': messages + [{'role': 'assistant', 'content': assistant_response}],
                'cost': round(cost, 4)
            })

        # Извлечение и очистка HTML
        clean_html = extract_html_from_response(assistant_response)

        # Проверка что HTML не пустой
        if len(clean_html) < 100:
            return jsonify({
                'error': 'Получен слишком короткий ответ. Попробуйте еще раз или используйте модель Sonnet.',
                'debug_response': assistant_response[:500]
            }), 500

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

    except anthropic.APIError as e:
        return jsonify({'error': f'Ошибка Claude API: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'error': f'Ошибка сервера: {str(e)}'}), 500

# ==================== ЗАПУСК ====================

if __name__ == '__main__':
    init_db()
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
