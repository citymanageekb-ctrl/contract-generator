"""
Сити Менедж Снег - Генератор договоров
Версия: 1.6 - Универсал, расширенные справочники, экспорт в DOCX
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
from io import BytesIO
from werkzeug.security import check_password_hash, generate_password_hash

# Для конвертации HTML в DOCX
from docx import Document
from htmldocx import HtmlToDocx

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

# СИСТЕМНЫЙ ПРОМПТ
SYSTEM_PROMPT = """Ты - эксперт по составлению юридических договоров в формате HTML.

КРИТИЧЕСКИ ВАЖНО:
1. СОХРАНЯЙ ВСЮ СТРУКТУРУ - все части, приложения, разделы
2. ПРИОРИТЕТ ДАННЫХ: файлы > текст > справочники > автозаполнение
3. НЕ ВЫДУМЫВАЙ данные - если нет, оставь [ИмяПоля]
4. ТОЧНО следуй форматированию

ФОРМАТ УСЛУГ (СТРОГО):
✅ ПРАВИЛЬНО: "Механизированная уборка снега погрузчиком – 3000 руб/час с НДС 20%. Вывоз снега 20 м³ – 5100 руб/рейс с НДС 20%."
❌ НЕПРАВИЛЬНО: точка с запятой, двоеточие, дефис, м3

ПРАВИЛА:
- Разделитель: ". " (точка + пробел)
- Тире: " – " (длинное + пробелы)
- Регистр: м³ (не м3)

E-MAIL ОТВЕТСТВЕННОГО:
- Сити Менедж: если email = "mitkina.citymanage@yandex.ru" → пусто, иначе → ", {email}"
- Скориченко: всегда вставляй email

ОТВЕТСТВЕННОЕ ЛИЦО:
ОБЯЗАТЕЛЬНО: "ФИО, тел. +7 XXX XXX-XX-XX, e-mail: xxx@xxx.ru"

Текущая дата: {current_date}"""

# СПРАВОЧНИКИ
OBJECTS_DIRECTORY = {
    'Озон Сургут': 'ООО «Интернет Решения» г. Сургут, Нефтеюганское шоссе, д. 22/2',
    'Озон Ноябрьск': 'ООО «Интернет Решения» ЯНАО, г. Ноябрьск, промузел Пелей, 12-й проезд, панель XIV',
    'Озон Тагил': 'ООО «Интернет Решения» г. Нижний Тагил, Свердловское шоссе, д. 65',
    'Озон Тюмень': 'ООО «Интернет Решения» г. Тюмень, ул. 30 лет Победы',
    'Озон Миасс': 'ООО «Интернет Решения» г. Миасс, ул. 60 лет Октября, стр. 1/1',
    'Озон Челябинск': 'ООО «Интернет Решения» г. Челябинск, ул. Линейная, д. 59/1',
    'Озон Екатеринбург Логопарк': 'ООО «Интернет Решения» г. Екатеринбург, логопарк Кольцовский, 15',
    'Озон Екатеринбург Черняховского': 'ООО «Интернет Решения» г. Екатеринбург, ул. Черняховского, д. 104',
    'Лента Тольятти': 'Самарская обл, г. Тольятти, ул. Южное шоссе, д. 4',
    'КБ Магнитогорск': 'РЦ ООО «Оазис» Челябинская обл, г. Магнитогорск, ул. Комсомольская, д. 132',
    'КБ Казань': 'РЦ ООО «Оазис» Республика Татарстан, Лаишевский муниципальный район, Столбищенское с.п., ул. Взлетная, д. 28',
    'КБ Чита': 'РЦ ООО «Оазис» Забайкальский край, г. Чита, ул. Автостроителей, д. 10',
    'КБ Артем': 'РЦ ООО «Оазис» Приморский край, Артемовский г.о., г. Артем, ул. 2-я Рабочая, д. 162, корп. 3',
    'КБ Пенза': 'РЦ ООО «Автотранс» г. Пенза, ул. Аустрина, земельный участок 168У',
    'КБ Хабаровск': 'РЦ ООО «Автотранс» Хабаровский край, г. Хабаровск, ул. Шкотова, д. 15А',
    'КБ РЦ Пермь': 'РЦ ООО «Автотранс» Пермский край, Пермский м.о., Двуреченское с.п., примерно в 0,99 км по направлению на север от ориентира д. Устиново, ул. Героя, д. 21',
    'КБ Екатеринбург (Серовский тракт)': 'РЦ ООО «Абсолют» 620000, Свердловская обл, г. Екатеринбург, Серовский тракт 11 км, стр. 3А',
    'КБ Екатеринбург (Оазис)': 'РЦ ООО «Оазис» г. Екатеринбург, ЕКАД 5 км., стр. 6/14',
    'КБ Копейск': 'ООО «Оазис» Челябинская обл, г. Копейск, ул. Логопарковая, д. 1А',
    'КБ Челябинск': 'РЦ ООО «Абсолют» Челябинская обл, г. Челябинск, Копейское шоссе, д. 1П',
    'КБ Уфа': 'РЦ ООО «Оазис» 450028, Республика Башкортостан, г. Уфа, ул. Гвардейская, д. 57/1А литера А1, пом. 172',
    'КБ Оренбург': 'РЦ ООО «Прометей» Оренбургская обл, г. Оренбург, ул. Тихая, зд. 1/1',
    'КБ Ижевск': 'РЦ ООО «Прометей» Удмуртская республика, Завьяловский район, территория Складская, зд. 1/1',
    'КБ Барнаул': 'РЦ ООО «Оазис» Алтайский край, г. Барнаул, ул. Мамонтова, д. 208',
    'КБ Омск': 'РЦ ООО «Оазис» г. Омск, ул. Айвазовского, д. 31',
    'КБ Новосибирск': 'РЦ ООО «Оазис» Новосибирская обл., Новосибирский район, Толмачевский сельсовет, платформа 3307 км, д. 19К1/1',
    'КБ Калининград': 'РЦ ООО «Прометей» Калининградская обл, г. Калининград, Большая Окружная 4-я, д. 102, корп. 1',
    'Башнефть': 'ООО «Башнефть-Розница»',
    'Ашан': 'ООО «Ашан»',
    'Почта России': 'АО «Почта России»'
}

ADDRESS_DIRECTORY = {
    'Озон Сургут': 'г. Сургут, Нефтеюганское ш., д. 22/2',
    'Озон Ноябрьск': 'г. Ноябрьск, промузел Пелей, 12-й проезд, панель XIV',
    'Озон Тагил': 'г. Нижний Тагил, Свердловское ш., д. 65',
    'Озон Тюмень': 'г. Тюмень, ул. 30 лет Победы',
    'Озон Миасс': 'г. Миасс, ул. 60 лет Октября, стр. 1/1',
    'Озон Челябинск': 'г. Челябинск, ул. Линейная, д. 59/1',
    'Озон Екатеринбург Логопарк': 'г. Екатеринбург, логопарк Кольцовский, 15',
    'Озон Екатеринбург Черняховского': 'г. Екатеринбург, ул. Черняховского, д. 104',
    'Лента Тольятти': 'Самарская обл, г. Тольятти, ул. Южное шоссе, д. 4',
    'Ашан Засечное': 'с. Засечное, ул. Мясницкая, д. 4',
    'Ашан Пенза': 'г. Пенза, ул. Антонова, д. 78',
    'Башнефть Курган 1': 'г. Курган, пр. Конституции, д. 26',
    'Башнефть Курган 2': 'г. Курган, ул. Машиностроителей, д. 36А',
}

CONTRACT_TYPES = {
    'city_manage_gov': {
        'name': 'Договор Сити Менедж Гос',
        'parts': ['Договор_Сити_Менедж_Гос.html', 'СМ_Приложение_1_Гос.html', 'СМ_Приложение_2.html']
    },
    'city_manage_universal': {
        'name': 'Договор Сити Менедж Универсал',
        'parts': ['Договор_Сити_Менедж_Универсал.html', 'СМ_Приложение_1_Универсал.html', 'СМ_Приложение_2.html']
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

def html_to_docx(html_content, contract_type_name):
    """Конвертирует HTML в DOCX"""
    try:
        logger.info("Конвертация HTML → DOCX")
        document = Document()
        parser = HtmlToDocx()
        parser.add_html_to_document(html_content, document)
        
        docx_file = BytesIO()
        document.save(docx_file)
        docx_file.seek(0)
        
        logger.info("Конвертация завершена")
        return docx_file
    except Exception as e:
        logger.error(f"Ошибка конвертации: {e}")
        raise

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

@app.route('/download/<int:contract_id>')
@login_required
def download_contract(contract_id):
    """Скачивание в DOCX"""
    try:
        contract = get_contract_by_id(contract_id)
        if not contract:
            return "Договор не найден", 404
        
        contract_type = contract[1]
        html_content = contract[3]
        created_at = contract[6]
        
        contract_type_name = CONTRACT_TYPES.get(contract_type, {}).get('name', 'Договор')
        filename = f"{contract_type_name}_{created_at.replace(':', '-').replace(' ', '_')}.docx"
        
        docx_file = html_to_docx(html_content, contract_type_name)
        
        return send_file(
            docx_file,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logger.error(f"Ошибка скачивания: {e}")
        return f"Ошибка: {str(e)}", 500

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
        logger.info("=== НАЧАЛО ГЕНЕРАЦИИ ===")
        
        data = request.json
        contract_type = data.get('contract_type')
        user_input = data.get('user_input')
        files_data = data.get('files', [])
        use_sonnet = data.get('use_sonnet', False)
        
        logger.info(f"Тип: {contract_type}, Модель: {'Sonnet' if use_sonnet else 'Haiku'}")
        logger.info(f"Данные: {len(user_input)} символов, Файлов: {len(files_data)}")

        if not contract_type or not user_input:
            return jsonify({'error': 'Не указан тип или данные'}), 400

        if not API_KEY:
            return jsonify({'error': 'API ключ не настроен'}), 500

        model = MODEL_SONNET if use_sonnet else MODEL_HAIKU
        templates_dir = Path('contracts_templates')
        contract_config = CONTRACT_TYPES.get(contract_type)
        
        if not contract_config:
            return jsonify({'error': 'Неизвестный тип договора'}), 400

        logger.info("Загрузка шаблонов...")
        template_parts = []
        for part_file in contract_config['parts']:
            template_path = templates_dir / part_file
            if template_path.exists():
                with open(template_path, 'r', encoding='utf-8') as f:
                    template_parts.append(f.read())
            else:
                return jsonify({'error': f'Шаблон не найден: {part_file}'}), 500

        logger.info(f"Загружено: {len(template_parts)} шаблонов")

        current_date = datetime.now().strftime('%d.%m.%Y')
        system_prompt = SYSTEM_PROMPT.format(current_date=current_date)

        content = []

        # ФАЙЛЫ
        if files_data:
            content.append({'type': 'text', 'text': f"ФАЙЛЫ ({len(files_data)} шт): Приоритет данных из файлов!"})

        for i, file_data in enumerate(files_data):
            file_type = file_data.get('type', '')
            file_name = file_data.get('name', f'file_{i+1}')
            
            content.append({'type': 'text', 'text': f"ФАЙЛ {i+1}: {file_name}"})
            
            if 'image' in file_type:
                content.append({
                    'type': 'image',
                    'source': {'type': 'base64', 'media_type': file_type, 'data': file_data.get('data', '')}
                })
            elif 'pdf' in file_type:
                content.append({
                    'type': 'document',
                    'source': {'type': 'base64', 'media_type': 'application/pdf', 'data': file_data.get('data', '')}
                })
            elif any(ext in file_type for ext in ['text', 'document', 'csv']):
                try:
                    text_content = base64.b64decode(file_data.get('data', '')).decode('utf-8')
                    content.append({'type': 'text', 'text': f"СОДЕРЖИМОЕ:\n{text_content}"})
                except:
                    logger.warning(f"Не удалось декодировать {file_name}")

        # ШАБЛОНЫ
        template_text = ""
        for i, part in enumerate(template_parts):
            template_text += f"\n\n{'='*80}\nЧАСТЬ {i+1}/{len(template_parts)}: {contract_config['parts'][i]}\n{'='*80}\n\n{part}"
        
        objects_ref = json.dumps(OBJECTS_DIRECTORY, ensure_ascii=False, indent=2)
        addresses_ref = json.dumps(ADDRESS_DIRECTORY, ensure_ascii=False, indent=2)
        
        user_message = f"""ЗАДАЧА: Заполни ВСЕ {len(template_parts)} части шаблона.

ШАБЛОНЫ:
{template_text}

ДАННЫЕ:
{user_input}

СПРАВОЧНИКИ:
Объекты: {objects_ref}
Адреса: {addresses_ref}

ПРАВИЛА:
1. ВСЕ {len(template_parts)} части в одном HTML
2. Приоритет: Файлы → Текст → Справочники
3. Ответственное лицо: ОБЯЗАТЕЛЬНО "ФИО, тел. +7 XXX XXX-XX-XX, e-mail: xxx@xxx.ru"
4. E-mail ответственного:
   - Сити Менедж: если "mitkina.citymanage@yandex.ru" → пусто, иначе ", {{email}}"
   - Скориченко: всегда вставляй
5. Наименование объекта (Универсал):
   Озон → "Озон", КБ → "КБ РЦ", Башнефть → "Башнефть", Ашан → "Ашан", Почта России → "Почта России"
6. Услуги: "Название – цена. " (тире длинное с пробелами, м³ не м3)

ПРОВЕРЬ:
✓ Все {len(template_parts)} части?
✓ Файлы использованы?
✓ Услуги правильно (тире, м³)?
✓ Ответственное с e-mail?

Верни ВЕСЬ документ БЕЗ markdown. Начни с HTML тега."""

        content.append({'type': 'text', 'text': user_message})

        logger.info("→ Claude API")
        
        try:
            client = anthropic.Anthropic(api_key=API_KEY)
            messages = [{'role': 'user', 'content': content}]

            response = client.messages.create(
                model=model,
                max_tokens=16000,
                system=system_prompt,
                messages=messages,
                temperature=0.2
            )

            logger.info("← Ответ получен")
            
            assistant_response = response.content[0].text
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            
            if model == MODEL_HAIKU:
                cost = (input_tokens * 0.25 + output_tokens * 1.25) / 1000000
            else:
                cost = (input_tokens * 3 + output_tokens * 15) / 1000000

            logger.info(f"Токены: {input_tokens}/{output_tokens}, Цена: ${cost:.4f}")

            if assistant_response.strip().startswith('{'):
                try:
                    json_data = json.loads(assistant_response)
                    if 'question' in json_data:
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

            if len(clean_html_text) < 1000:
                return jsonify({'error': 'Ответ слишком короткий', 'debug': assistant_response[:500]}), 500

            save_to_history(contract_type, user_input, clean_html_text, model, cost)
            
            # Получаем ID последнего сохранённого договора
            conn = sqlite3.connect('contracts_history.db')
            c = conn.cursor()
            c.execute('SELECT last_insert_rowid()')
            contract_id = c.fetchone()[0]
            conn.close()

            logger.info("=== УСПЕШНО ===")
            return jsonify({
                'status': 'success',
                'html': clean_html_text,
                'contract_id': contract_id,
                'cost': round(cost, 4),
                'model': model,
                'tokens': {'input': input_tokens, 'output': output_tokens}
            })

        except anthropic.AuthenticationError as e:
            return jsonify({'error': f'Ошибка API ключа: {str(e)}'}), 500
        except anthropic.APIError as e:
            return jsonify({'error': f'Ошибка API: {str(e)}'}), 500

    except Exception as e:
        logger.error(f"ОШИБКА: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': f'Ошибка: {str(e)}'}), 500

init_db()

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    logger.info(f"Порт: {port}, API: {'Да' if API_KEY else 'НЕТ'}")
    app.run(host='0.0.0.0', port=port, debug=False)
