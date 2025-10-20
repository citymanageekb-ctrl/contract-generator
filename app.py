"""
Сити Менедж Снег - Генератор договоров
Версия: 1.4 - Исправлена инициализация БД
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
import traceback
import logging
from werkzeug.security import check_password_hash, generate_password_hash

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
APP_PASSWORD = os.getenv("APP_PASSWORD", "sneg2025")

MODEL_HAIKU = "claude-3-5-haiku-20241022"
MODEL_SONNET = "claude-sonnet-4-20250514"

SYSTEM_PROMPT = """Ты - эксперт по заполнению HTML-документов.

ЗАДАЧА: Взять HTML-шаблоны и заполнить поля данными пользователя.

ПРАВИЛА:
1. Найди конструкции: <!--FIELD:Имя--><span data-ph="Имя">[Имя]</span><!--/FIELD-->
2. Замени текст ТОЛЬКО внутри <span data-ph="...">...</span>
3. НЕ меняй HTML-теги и стили
4. Собери все части в один документ

АВТОЗАПОЛНЕНИЕ:
- Номер договора не указан → ДДММГГГГ/1
- Дата не указана → {current_date}

ФОРМАТ ОТВЕТА:
- Если все данные есть: верни ТОЛЬКО HTML
- Если нужны данные: {{"question": "...", "missing_fields": [...]}}

НЕ используй markdown блоки ```
Начни ответ с HTML тега."""

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

def init_db():
    """Инициализация базы данных"""
    try:
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
        logger.info("База данных инициализирована")
    except Exception as e:
        logger.error(f"Ошибка инициализации БД: {e}")

def save_to_history(contract_type, user_data, generated_html, model_used, cost_estimate):
    try:
        conn = sqlite3.connect('contracts_history.db')
        c = conn.cursor()
        c.execute('''INSERT INTO contracts (contract_type, user_data, generated_html, model_used, cost_estimate)
                     VALUES (?, ?, ?, ?, ?)''',
                  (contract_type, user_data, generated_html, model_used, cost_estimate))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка сохранения в историю: {e}")

def get_history(limit=50):
    try:
        conn = sqlite3.connect('contracts_history.db')
        c = conn.cursor()
        c.execute('''SELECT id, contract_type, user_data, created_at, model_used, cost_estimate
                     FROM contracts ORDER BY created_at DESC LIMIT ?''', (limit,))
        rows = c.fetchall()
        conn.close()
        return rows
    except Exception as e:
        logger.error(f"Ошибка получения истории: {e}")
        return []

def get_contract_by_id(contract_id):
    try:
        conn = sqlite3.connect('contracts_history.db')
        c = conn.cursor()
        c.execute('SELECT * FROM contracts WHERE id = ?', (contract_id,))
        row = c.fetchone()
        conn.close()
        return row
    except Exception as e:
        logger.error(f"Ошибка получения договора: {e}")
        return None

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

@app.route('/')
@login_required
def index():
    return render_template('index.html', contract_types=CONTRACT_TYPES)

@app.route('/history')
@login_required
def history():
    contracts = get_history()
    return render_template('history.html', contracts=contracts, contract_types=CONTRACT_TYPES)

@app.route('/history/<int:contract_id>')
@login_required
def view_contract(contract_id):
    contract = get_contract_by_id(contract_id)
    if contract:
        return contract[3]
    return "Договор не найден", 404

def clean_html(text):
    text = re.sub(r'```html\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'```\s*', '', text)
    text = text.strip()
    
    patterns = [r'(<!DOCTYPE[^>]*>.*)', r'(<html[^>]*>.*)', r'(<div[^>]*>.*)']
    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1)
    
    return text

@app.route('/api/generate', methods=['POST'])
@login_required
def generate_contract():
    try:
        logger.info("=== НАЧАЛО ГЕНЕРАЦИИ ДОГОВОРА ===")
        
        data = request.json
        contract_type = data.get('contract_type')
        user_input = data.get('user_input')
        files_data = data.get('files', [])
        use_sonnet = data.get('use_sonnet', False)
        
        logger.info(f"Тип договора: {contract_type}")
        logger.info(f"Модель: {'Sonnet' if use_sonnet else 'Haiku'}")
        logger.info(f"Длина входных данных: {len(user_input)} символов")
        logger.info(f"Количество файлов: {len(files_data)}")

        if not contract_type or not user_input:
            logger.error("Не указан тип договора или данные")
            return jsonify({'error': 'Не указан тип договора или данные'}), 400

        if not API_KEY or API_KEY == "":
            logger.error("API ключ не установлен!")
            return jsonify({'error': 'API ключ Claude не настроен'}), 500

        logger.info(f"API ключ присутствует: {API_KEY[:20]}...")

        model = MODEL_SONNET if use_sonnet else MODEL_HAIKU
        templates_dir = Path('contracts_templates')
        contract_config = CONTRACT_TYPES.get(contract_type)
        
        if not contract_config:
            logger.error(f"Неизвестный тип договора: {contract_type}")
            return jsonify({'error': 'Неизвестный тип договора'}), 400

        logger.info("Загрузка шаблонов...")
        template_parts = []
        for part_file in contract_config['parts']:
            template_path = templates_dir / part_file
            if template_path.exists():
                with open(template_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    template_parts.append(content)
                    logger.info(f"Загружен: {part_file} ({len(content)} символов)")
            else:
                logger.error(f"Файл не найден: {template_path}")
                return jsonify({'error': f'Шаблон не найден: {part_file}'}), 500

        if not template_parts:
            logger.error("Не удалось загрузить ни одного шаблона")
            return jsonify({'error': 'Шаблоны не найдены'}), 500

        logger.info(f"Загружено шаблонов: {len(template_parts)}")

        current_date = datetime.now().strftime('%d.%m.%Y')
        system_prompt = SYSTEM_PROMPT.format(current_date=current_date)

        content = []

        for i, file_data in enumerate(files_data):
            file_type = file_data.get('type', '')
            logger.info(f"Файл {i+1}: {file_type}")
            
            if 'image' in file_type:
                content.append({
                    'type': 'image',
                    'source': {
                        'type': 'base64',
                        'media_type': file_type,
                        'data': file_data.get('data', '')
                    }
                })
            elif 'pdf' in file_type:
                content.append({
                    'type': 'document',
                    'source': {
                        'type': 'base64',
                        'media_type': 'application/pdf',
                        'data': file_data.get('data', '')
                    }
                })

        combined_template = '\n\n<!-- РАЗДЕЛИТЕЛЬ -->\n\n'.join(template_parts)
        
        user_message = f"""ЗАДАЧА: Заполни HTML-шаблон данными

ШАБЛОН:
{combined_template}

ДАННЫЕ:
{user_input}

{'СПРАВОЧНИК ОЗОН: ' + json.dumps(OZON_DIRECTORY, ensure_ascii=False) if 'ozon' in contract_type else ''}

Верни ТОЛЬКО HTML без markdown блоков."""

        content.append({'type': 'text', 'text': user_message})

        logger.info("Отправка запроса к Claude API...")
        
        try:
            client = anthropic.Anthropic(api_key=API_KEY)
            messages = [{'role': 'user', 'content': content}]

            response = client.messages.create(
                model=model,
                max_tokens=8000,
                system=system_prompt,
                messages=messages,
                temperature=0.3
            )

            logger.info("Получен ответ от Claude API")
            
            assistant_response = response.content[0].text
            logger.info(f"Длина ответа: {len(assistant_response)} символов")

            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            logger.info(f"Токены: вход={input_tokens}, выход={output_tokens}")
            
            if model == MODEL_HAIKU:
                cost = (input_tokens * 0.25 / 1000000) + (output_tokens * 1.25 / 1000000)
            else:
                cost = (input_tokens * 3 / 1000000) + (output_tokens * 15 / 1000000)

            logger.info(f"Стоимость: ${cost:.4f}")

            if assistant_response.strip().startswith('{'):
                try:
                    json_data = json.loads(assistant_response)
                    if 'question' in json_data:
                        logger.info("Claude задал вопрос")
                        return jsonify({
                            'status': 'question',
                            'question': json_data['question'],
                            'missing_fields': json_data.get('missing_fields', []),
                            'conversation': messages + [{'role': 'assistant', 'content': assistant_response}],
                            'cost': round(cost, 4)
                        })
                except:
                    pass

            clean_html_text = clean_html(assistant_response)
            logger.info(f"После очистки: {len(clean_html_text)} символов")

            if len(clean_html_text) < 200:
                logger.error(f"Ответ слишком короткий: {len(clean_html_text)} символов")
                return jsonify({
                    'error': 'Получен слишком короткий ответ. Попробуйте модель Sonnet.',
                    'debug': assistant_response[:500]
                }), 500

            logger.info("Сохранение в историю...")
            save_to_history(contract_type, user_input, clean_html_text, model, cost)

            logger.info("=== УСПЕШНО ===")
            return jsonify({
                'status': 'success',
                'html': clean_html_text,
                'cost': round(cost, 4),
                'model': model,
                'tokens': {'input': input_tokens, 'output': output_tokens}
            })

        except anthropic.AuthenticationError as e:
            logger.error(f"Ошибка аутентификации Claude API: {str(e)}")
            return jsonify({'error': f'Ошибка API ключа Claude: {str(e)}'}), 500
        
        except anthropic.APIError as e:
            logger.error(f"Ошибка Claude API: {str(e)}")
            return jsonify({'error': f'Ошибка Claude API: {str(e)}'}), 500

    except Exception as e:
        logger.error(f"КРИТИЧЕСКАЯ ОШИБКА: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': f'Ошибка сервера: {str(e)}'}), 500

# ВАЖНО: Инициализация БД при импорте модуля
init_db()

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    logger.info(f"Запуск приложения на порту {port}")
    logger.info(f"API ключ установлен: {'Да' if API_KEY else 'НЕТ'}")
    app.run(host='0.0.0.0', port=port, debug=False)
