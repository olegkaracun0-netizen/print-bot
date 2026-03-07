@app.route('/')
def home():
    """Главная страница с супер-премиум дизайном"""
    orders_count = len([d for d in os.listdir(ORDERS_PATH) if os.path.isdir(os.path.join(ORDERS_PATH, d))]) if os.path.exists(ORDERS_PATH) else 0
    
    # Загружаем историю для статистики
    history = load_orders_history()
    total_revenue = sum(order.get('total_price', 0) for order in history)
    total_photos = sum(order.get('total_photos', 0) for order in history)
    total_pages = sum(order.get('total_pages', 0) for order in history)
    
    html = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>🖨️ Print Bot Premium | Супер-дизайн</title>
        {PREMIUM_CSS}
    </head>
    <body>
        <div class="container">
            <!-- Шапка с неоновым эффектом -->
            <div class="premium-card" style="text-align: center; margin-bottom: 40px;">
                <h1 class="neon-text">✨ PRINT BOT PREMIUM ✨</h1>
                <p style="color: white; font-size: 1.5em; margin-top: 20px; text-shadow: 0 0 10px rgba(255,255,255,0.5);">
                    Супер-премиум система печати с 3D-эффектами
                </p>
                <div style="margin-top: 30px;">
                    <a href="/orders/" class="glow-btn">📦 ПРОСМОТР ЗАКАЗОВ</a>
                    <a href="/stats/" class="glow-btn" style="margin-left: 20px;">📊 СТАТИСТИКА</a>
                </div>
            </div>
            
            <!-- Статистика -->
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-icon">📦</div>
                    <div class="stat-value">{orders_count}</div>
                    <div class="stat-label">Активных заказов</div>
                </div>
                <div class="stat-card">
                    <div class="stat-icon">💰</div>
                    <div class="stat-value">{total_revenue} ₽</div>
                    <div class="stat-label">Выручка</div>
                </div>
                <div class="stat-card">
                    <div class="stat-icon">📸</div>
                    <div class="stat-value">{total_photos}</div>
                    <div class="stat-label">Напечатано фото</div>
                </div>
                <div class="stat-card">
                    <div class="stat-icon">📄</div>
                    <div class="stat-value">{total_pages}</div>
                    <div class="stat-label">Напечатано страниц</div>
                </div>
            </div>
            
            <!-- Последние заказы -->
            <div class="premium-card">
                <h2 style="color: white; font-size: 2.5em; margin-bottom: 30px; text-shadow: 0 0 20px rgba(255,255,255,0.3);">
                    🔥 ПОСЛЕДНИЕ ЗАКАЗЫ
                </h2>
                <div style="display: grid; gap: 20px;">
    """
    
    # Показываем последние 5 заказов
    for order in sorted(history, key=lambda x: x.get('date', ''), reverse=True)[:5]:
        status_class = f"status-{order.get('status', 'new')}"
        status_text = get_status_display(order.get('status', 'new'))
        
        html += f"""
                    <div class="order-card" style="background: rgba(255,255,255,0.95); padding: 25px;">
                        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap;">
                            <div>
                                <h3 style="font-size: 1.3em; margin-bottom: 10px;">
                                    🆔 {order.get('order_id', 'N/A')}
                                </h3>
                                <p style="color: #666;">
                                    👤 {order.get('user_name', 'Неизвестно')} | 
                                    📅 {datetime.fromisoformat(order.get('date', datetime.now().isoformat())).strftime('%d.%m.%Y %H:%M')}
                                </p>
                            </div>
                            <span class="status-badge {status_class}">{status_text}</span>
                        </div>
                        <div style="margin-top: 15px; display: flex; gap: 20px; flex-wrap: wrap;">
                            <span>📸 Фото: {order.get('total_photos', 0)}</span>
                            <span>📄 Страниц: {order.get('total_pages', 0)}</span>
                            <span>💰 {order.get('total_price', 0)} ₽</span>
                        </div>
                        <a href="/orders/{order.get('order_id')}/" style="display: inline-block; margin-top: 15px; color: #667eea; text-decoration: none; font-weight: 600;">
                            Подробнее →
                        </a>
                    </div>
        """
    
    html += """
                </div>
            </div>
            
            <!-- Информация о боте -->
            <div class="premium-card" style="text-align: center;">
                <h2 style="color: white; font-size: 2em; margin-bottom: 20px;">🤖 О БОТЕ</h2>
                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 30px; color: white;">
                    <div>
                        <div style="font-size: 3em; margin-bottom: 15px;">📸</div>
                        <h3>Фото</h3>
                        <p>JPG, PNG<br>3 формата печати</p>
                    </div>
                    <div>
                        <div style="font-size: 3em; margin-bottom: 15px;">📄</div>
                        <h3>Документы</h3>
                        <p>PDF, DOC, DOCX<br>Ч/б и цветная печать</p>
                    </div>
                    <div>
                        <div style="font-size: 3em; margin-bottom: 15px;">🚚</div>
                        <h3>Доставка</h3>
                        <p>Самовывоз, СДЭК, Яндекс</p>
                    </div>
                </div>
                <div style="margin-top: 40px;">
                    <p style="color: white; font-size: 1.3em;">📞 Контактный телефон: <strong>{CONTACT_PHONE}</strong></p>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    
    return render_template_string(html)

@app.route('/orders/')
def list_orders():
    """Список всех заказов"""
    orders = []
    if os.path.exists(ORDERS_PATH):
        for item in os.listdir(ORDERS_PATH):
            order_path = os.path.join(ORDERS_PATH, item)
            if os.path.isdir(order_path):
                info_file = os.path.join(order_path, "информация_о_заказе.txt")
                status = "new"
                total = 0
                if os.path.exists(info_file):
                    with open(info_file, 'r', encoding='utf-8') as f:
                        content = f.read()
                        # Извлекаем статус
                        status_match = re.search(r'Статус: (.*?)(?:\n|$)', content)
                        if status_match:
                            status_text = status_match.group(1)
                            # Конвертируем обратно в ключ
                            for key, value in ORDER_STATUSES.items():
                                if value == status_text:
                                    status = key
                                    break
                        # Извлекаем сумму
                        total_match = re.search(r'ИТОГО К ОПЛАТЕ: (\d+)', content)
                        if total_match:
                            total = int(total_match.group(1))
                
                orders.append({
                    'id': item,
                    'path': order_path,
                    'status': status,
                    'total': total,
                    'has_files': len([f for f in os.listdir(order_path) if f.endswith(('.jpg', '.jpeg', '.png', '.pdf', '.docx', '.doc'))]) > 0
                })
    
    # Сортируем по дате (более новые сверху)
    orders.sort(key=lambda x: x['id'], reverse=True)
    
    status_counts = {status: sum(1 for o in orders if o['status'] == status) for status in ORDER_STATUSES.keys()}
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>📦 Все заказы | Print Bot</title>
        {PREMIUM_CSS}
    </head>
    <body>
        <div class="container">
            <div class="premium-card">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px;">
                    <h1 class="neon-text" style="font-size: 2.5em;">📦 ВСЕ ЗАКАЗЫ</h1>
                    <a href="/" class="glow-btn" style="padding: 10px 20px;">🏠 НА ГЛАВНУЮ</a>
                </div>
                
                <!-- Статус фильтры -->
                <div style="display: flex; gap: 15px; flex-wrap: wrap; margin-bottom: 30px;">
    """
    
    for status_key, status_value in ORDER_STATUSES.items():
        count = status_counts.get(status_key, 0)
        status_class = f"status-{status_key}"
        html += f"""
                    <a href="?status={status_key}" class="status-badge {status_class}" style="text-decoration: none;">
                        {status_value} ({count})
                    </a>
        """
    
    html += """
                </div>
    """
    
    if not orders:
        html += """
                <div class="empty-state">
                    <div class="empty-icon">📭</div>
                    <h2 style="font-size: 2em; margin-bottom: 20px;">Заказов пока нет</h2>
                    <p style="font-size: 1.2em;">Отправьте файлы боту, чтобы создать первый заказ</p>
                </div>
        """
    else:
        html += """
                <div style="display: grid; gap: 30px;">
        """
        
        for order in orders:
            status_class = f"status-{order['status']}"
            status_text = get_status_display(order['status'])
            
            html += f"""
                    <div class="order-card">
                        <div class="order-header">
                            <div style="display: flex; justify-content: space-between; align-items: center;">
                                <h2 style="color: white; font-size: 1.5em;">🆔 {order['id']}</h2>
                                <span class="status-badge {status_class}">{status_text}</span>
                            </div>
                        </div>
                        <div style="padding: 30px;">
                            <div style="display: flex; gap: 30px; flex-wrap: wrap; margin-bottom: 20px;">
                                <span style="font-size: 1.2em;">💰 Сумма: <strong>{order['total']} ₽</strong></span>
                                <span style="font-size: 1.2em;">📁 Файлов: {sum(1 for f in os.listdir(order['path']) if os.path.isfile(os.path.join(order['path'], f)) and not f.startswith('информация'))}</span>
                            </div>
                            <a href="/orders/{order['id']}/" class="glow-btn" style="display: inline-block; text-decoration: none;">
                                ПОДРОБНЕЕ →
                            </a>
                        </div>
                    </div>
            """
        
        html += """
                </div>
        """
    
    html += """
            </div>
        </div>
    </body>
    </html>
    """
    
    return render_template_string(html)

@app.route('/orders/<order_id>/')
def view_order(order_id):
    """Просмотр конкретного заказа"""
    order_path = os.path.join(ORDERS_PATH, order_id)
    
    if not os.path.exists(order_path) or not os.path.isdir(order_path):
        abort(404)
    
    # Читаем информацию о заказе
    info_file = os.path.join(order_path, "информация_о_заказе.txt")
    info_content = ""
    if os.path.exists(info_file):
        with open(info_file, 'r', encoding='utf-8') as f:
            info_content = f.read()
    
    # Собираем файлы
    files = []
    photos = []
    docs = []
    
    for f in os.listdir(order_path):
        file_path = os.path.join(order_path, f)
        if os.path.isfile(file_path) and not f.startswith('информация'):
            ext = f.lower().split('.')[-1] if '.' in f else ''
            file_info = {
                'name': f,
                'path': file_path,
                'size': os.path.getsize(file_path),
                'size_str': format_file_size(os.path.getsize(file_path)),
                'ext': ext
            }
            files.append(file_info)
            
            if ext in ['jpg', 'jpeg', 'png']:
                photos.append(file_info)
            elif ext in ['pdf', 'docx', 'doc']:
                docs.append(file_info)
    
    # Получаем статус из файла
    status = "new"
    status_match = re.search(r'Статус: (.*?)(?:\n|$)', info_content)
    if status_match:
        status_text = status_match.group(1)
        for key, value in ORDER_STATUSES.items():
            if value == status_text:
                status = key
                break
    
    # Получаем сумму
    total_match = re.search(r'ИТОГО К ОПЛАТЕ: (\d+)', info_content)
    total = int(total_match.group(1)) if total_match else 0
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Заказ {order_id} | Print Bot</title>
        {PREMIUM_CSS}
    </head>
    <body>
        <div class="container">
            <div class="premium-card">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px;">
                    <h1 class="neon-text" style="font-size: 2em;">🆔 ЗАКАЗ {order_id}</h1>
                    <div>
                        <a href="/orders/" class="glow-btn" style="padding: 10px 20px; margin-right: 10px;">📋 К СПИСКУ</a>
                        <a href="/" class="glow-btn" style="padding: 10px 20px;">🏠 ГЛАВНАЯ</a>
                    </div>
                </div>
                
                <!-- Статус заказа с возможностью изменения -->
                <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 40px; padding: 30px; margin-bottom: 30px;">
                    <h2 style="color: white; margin-bottom: 20px;">Текущий статус:</h2>
                    <div style="display: flex; gap: 15px; flex-wrap: wrap;">
    """
    
    for status_key, status_value in ORDER_STATUSES.items():
        selected_class = "glow-btn" if status_key == status else "status-badge " + f"status-{status_key}"
        if status_key == status:
            html += f"""
                        <span class="glow-btn" style="cursor: default;">{status_value}</span>
            """
        else:
            html += f"""
                        <a href="/orders/{order_id}/status/{status_key}/" class="status-badge status-{status_key}" style="text-decoration: none; cursor: pointer;">
                            {status_value}
                        </a>
            """
    
    html += f"""
                    </div>
                </div>
                
                <!-- Информация о заказе -->
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 30px; margin-bottom: 30px;">
                    <div class="order-card" style="padding: 30px;">
                        <h2 style="margin-bottom: 20px;">📊 Детали заказа</h2>
                        <pre style="font-family: 'Poppins', sans-serif; white-space: pre-wrap; line-height: 1.6;">{info_content}</pre>
                    </div>
                    
                    <div class="order-card" style="padding: 30px;">
                        <h2 style="margin-bottom: 20px;">📁 Файлы ({len(files)})</h2>
                        <div style="max-height: 500px; overflow-y: auto;">
    """
    
    for file in files:
        icon = "📸" if file['ext'] in ['jpg', 'jpeg', 'png'] else "📄"
        html += f"""
                            <div style="padding: 15px; border-bottom: 1px solid #eee; display: flex; justify-content: space-between; align-items: center;">
                                <div>
                                    {icon} <strong>{file['name']}</strong><br>
                                    <small style="color: #666;">{file['size_str']}</small>
                                </div>
                                <a href="/orders/{order_id}/file/{file['name']}" class="glow-btn" style="padding: 5px 15px; font-size: 0.9em;" download>📥 Скачать</a>
                            </div>
        """
    
    html += """
                        </div>
                    </div>
                </div>
    """
    
    # Галерея фото
    if photos:
        html += """
                <div class="order-card" style="padding: 30px;">
                    <h2 style="margin-bottom: 20px;">📸 Галерея фото</h2>
                    <div class="photo-gallery">
        """
        
        for photo in photos:
            html += f"""
                        <img src="/orders/{order_id}/file/{photo['name']}" class="photo-preview" onclick="window.open(this.src, '_blank')">
            """
        
        html += """
                    </div>
                </div>
        """
    
    # Действия с заказом
    html += f"""
                <div style="display: flex; gap: 20px; margin-top: 30px;">
                    <a href="/orders/{order_id}/download/" class="glow-btn" style="flex: 1; text-align: center; text-decoration: none;">
                        📦 СКАЧАТЬ ВСЕ ФАЙЛЫ
                    </a>
                    <a href="/orders/{order_id}/delete/" class="glow-btn" style="flex: 1; text-align: center; text-decoration: none; background: linear-gradient(45deg, #f44336, #ff1744);"
                       onclick="return confirm('Вы уверены, что хотите удалить заказ? Это действие нельзя отменить!');">
                        🗑️ УДАЛИТЬ ЗАКАЗ
                    </a>
                </div>
            </div>
        </div>
        
        <script>
            // Автообновление статуса каждые 30 секунд
            setTimeout(function() {{
                location.reload();
            }}, 30000);
        </script>
    </body>
    </html>
    """
    
    return render_template_string(html)

@app.route('/orders/<order_id>/status/<new_status>/')
def change_status(order_id, new_status):
    """Изменение статуса заказа"""
    if new_status not in ORDER_STATUSES:
        abort(404)
    
    if update_order_status(order_id, new_status):
        return f"""
        <html>
        <head>
            <meta http-equiv="refresh" content="2;url=/orders/{order_id}/">
            {PREMIUM_CSS}
        </head>
        <body>
            <div class="container" style="display: flex; justify-content: center; align-items: center; min-height: 100vh;">
                <div class="premium-card" style="text-align: center;">
                    <div class="stat-icon">✅</div>
                    <h1 class="neon-text" style="font-size: 2em;">СТАТУС ИЗМЕНЕН</h1>
                    <p style="color: white; font-size: 1.2em; margin: 20px 0;">
                        Новый статус: {get_status_display(new_status)}
                    </p>
                    <p style="color: white;">Перенаправление через 2 секунды...</p>
                </div>
            </div>
        </body>
        </html>
        """
    else:
        abort(500)

@app.route('/orders/<order_id>/file/<filename>')
def download_file(order_id, filename):
    """Скачивание файла"""
    order_path = os.path.join(ORDERS_PATH, order_id)
    file_path = os.path.join(order_path, filename)
    
    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        abort(404)
    
    return send_file(file_path, as_attachment=True, download_name=filename)

@app.route('/orders/<order_id>/download/')
def download_all_files(order_id):
    """Скачивание всех файлов заказа архивом"""
    order_path = os.path.join(ORDERS_PATH, order_id)
    
    if not os.path.exists(order_path) or not os.path.isdir(order_path):
        abort(404)
    
    # Создаем временный ZIP-архив
    temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
    with zipfile.ZipFile(temp_zip.name, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(order_path):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, order_path)
                zipf.write(file_path, arcname)
    
    return send_file(
        temp_zip.name,
        as_attachment=True,
        download_name=f"заказ_{order_id}.zip",
        mimetype='application/zip'
    )

@app.route('/orders/<order_id>/delete/')
def delete_order(order_id):
    """Удаление заказа"""
    order_path = os.path.join(ORDERS_PATH, order_id)
    
    if not os.path.exists(order_path) or not os.path.isdir(order_path):
        abort(404)
    
    try:
        # Удаляем папку заказа
        shutil.rmtree(order_path)
        
        # Удаляем из истории
        history = load_orders_history()
        history = [order for order in history if order.get('order_id') != order_id]
        with open(ORDERS_DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        
        return """
        <html>
        <head>
            <meta http-equiv="refresh" content="3;url=/orders/">
            {PREMIUM_CSS}
        </head>
        <body>
            <div class="container" style="display: flex; justify-content: center; align-items: center; min-height: 100vh;">
                <div class="premium-card" style="text-align: center;">
                    <div class="stat-icon">🗑️</div>
                    <h1 class="neon-text" style="font-size: 2em;">ЗАКАЗ УДАЛЕН</h1>
                    <p style="color: white; font-size: 1.2em; margin: 20px 0;">
                        Заказ {order_id} успешно удален
                    </p>
                    <p style="color: white;">Перенаправление через 3 секунды...</p>
                </div>
            </div>
        </body>
        </html>
        """
    except Exception as e:
        logger.error(f"Ошибка удаления заказа: {e}")
        abort(500)

@app.route('/stats/')
def stats():
    """Статистика по заказам"""
    history = load_orders_history()
    
    # Общая статистика
    total_orders = len(history)
    total_revenue = sum(order.get('total_price', 0) for order in history)
    total_photos = sum(order.get('total_photos', 0) for order in history)
    total_pages = sum(order.get('total_pages', 0) for order in history)
    
    # Статистика по статусам
    status_stats = {}
    for status in ORDER_STATUSES.keys():
        status_stats[status] = sum(1 for order in history if order.get('status') == status)
    
    # Статистика по месяцам
    monthly_stats = {}
    for order in history:
        date_str = order.get('date', '')
        if date_str:
            month = date_str[:7]  # YYYY-MM
            if month not in monthly_stats:
                monthly_stats[month] = {'orders': 0, 'revenue': 0, 'photos': 0, 'pages': 0}
            monthly_stats[month]['orders'] += 1
            monthly_stats[month]['revenue'] += order.get('total_price', 0)
            monthly_stats[month]['photos'] += order.get('total_photos', 0)
            monthly_stats[month]['pages'] += order.get('total_pages', 0)
    
    # Сортируем месяцы
    months = sorted(monthly_stats.keys(), reverse=True)
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Статистика | Print Bot</title>
        {PREMIUM_CSS}
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    </head>
    <body>
        <div class="container">
            <div class="premium-card">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px;">
                    <h1 class="neon-text" style="font-size: 2.5em;">📊 СТАТИСТИКА</h1>
                    <a href="/" class="glow-btn">🏠 НА ГЛАВНУЮ</a>
                </div>
                
                <!-- Основные показатели -->
                <div class="stats-grid">
                    <div class="stat-card">
                        <div class="stat-icon">📦</div>
                        <div class="stat-value">{total_orders}</div>
                        <div class="stat-label">Всего заказов</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-icon">💰</div>
                        <div class="stat-value">{total_revenue} ₽</div>
                        <div class="stat-label">Общая выручка</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-icon">📸</div>
                        <div class="stat-value">{total_photos}</div>
                        <div class="stat-label">Всего фото</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-icon">📄</div>
                        <div class="stat-value">{total_pages}</div>
                        <div class="stat-label">Всего страниц</div>
                    </div>
                </div>
                
                <!-- Статус статистика -->
                <div class="order-card" style="padding: 30px; margin-bottom: 30px;">
                    <h2 style="margin-bottom: 20px;">📌 Статусы заказов</h2>
                    <div style="display: flex; gap: 20px; flex-wrap: wrap;">
    """
    
    for status, count in status_stats.items():
        status_class = f"status-{status}"
        status_text = ORDER_STATUSES.get(status, status)
        percentage = (count / total_orders * 100) if total_orders > 0 else 0
        html += f"""
                        <div style="flex: 1; min-width: 150px; text-align: center;">
                            <span class="status-badge {status_class}" style="font-size: 1.1em;">{status_text}</span>
                            <div style="font-size: 2em; font-weight: 800; margin: 10px 0;">{count}</div>
                            <div style="color: #666;">{percentage:.1f}%</div>
                        </div>
        """
    
    html += """
                    </div>
                </div>
                
                <!-- График по месяцам -->
                <div class="order-card" style="padding: 30px;">
                    <h2 style="margin-bottom: 20px;">📈 Динамика заказов по месяцам</h2>
                    <canvas id="ordersChart" style="width: 100%; height: 400px;"></canvas>
                </div>
            </div>
        </div>
        
        <script>
            const ctx = document.getElementById('ordersChart').getContext('2d');
            new Chart(ctx, {
                type: 'line',
                data: {
                    labels: {months},
                    datasets: [{
                        label: 'Количество заказов',
                        data: {[monthly_stats.get(m, {{}}).get('orders', 0) for m in months]},
                        borderColor: '#667eea',
                        backgroundColor: 'rgba(102, 126, 234, 0.1)',
                        tension: 0.4
                    }, {
                        label: 'Выручка (₽)',
                        data: {[monthly_stats.get(m, {{}}).get('revenue', 0) for m in months]},
                        borderColor: '#feca57',
                        backgroundColor: 'rgba(254, 202, 87, 0.1)',
                        tension: 0.4,
                        yAxisID: 'y1'
                    }]
                },
                options: {
                    responsive: true,
                    plugins: {
                        legend: {
                            labels: {
                                font: {
                                    family: "'Poppins', sans-serif",
                                    size: 12
                                }
                            }
                        }
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            title: {
                                display: true,
                                text: 'Количество заказов'
                            }
                        },
                        y1: {
                            beginAtZero: true,
                            position: 'right',
                            title: {
                                display: true,
                                text: 'Выручка (₽)'
                            },
                            grid: {
                                drawOnChartArea: false
                            }
                        }
                    }
                }
            });
        </script>
    </body>
    </html>
    """
    
    return render_template_string(html)

@app.route('/webhook', methods=['POST'])
def webhook():
    """Обработка вебхуков от Telegram"""
    if not bot:
        return "Bot not initialized", 500
    
    try:
        update = telegram.Update.de_json(request.get_json(force=True), bot)
        dispatcher.process_update(update)
        return "OK", 200
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        logger.error(traceback.format_exc())
        return "Error", 500

@app.route('/set_webhook')
def set_webhook():
    """Установка вебхука"""
    if not bot:
        return "Bot not initialized", 500
    
    try:
        webhook_url = f"{RENDER_URL}/webhook"
        bot.set_webhook(url=webhook_url)
        return f"Webhook set to {webhook_url}", 200
    except Exception as e:
        return f"Error: {e}", 500

def error_handler(update, context):
    """Глобальный обработчик ошибок"""
    logger.error(f"Update {update} caused error {context.error}")
    
    try:
        if update and update.effective_chat:
            context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ **Произошла ошибка**\n\nПожалуйста, попробуйте еще раз или начните заново с /start",
                parse_mode="Markdown"
            )
    except:
        pass

def run_bot():
    """Запуск бота и веб-сервера"""
    global updater, dispatcher, bot
    
    try:
        # Создаем бота
        bot = telegram.Bot(token=TOKEN)
        
        # Создаем updater
        updater = Updater(token=TOKEN, use_context=True)
        dispatcher = updater.dispatcher
        
        # Добавляем обработчик ошибок
        dispatcher.add_error_handler(error_handler)
        
        # Обработчик команды /start
        dispatcher.add_handler(CommandHandler("start", start))
        
        # Обработчик для файлов
        file_handler = MessageHandler(
            Filters.document | Filters.photo,
            handle_file
        )
        dispatcher.add_handler(file_handler)
        
        # Обработчик для текстовых сообщений (ручной ввод количества)
        text_handler = MessageHandler(
            Filters.text & ~Filters.command,
            handle_quantity_input
        )
        dispatcher.add_handler(text_handler)
        
        # Обработчик для callback-запросов (кнопки)
        dispatcher.add_handler(CallbackQueryHandler(button_handler))
        
        # Устанавливаем вебхук
        webhook_url = f"{RENDER_URL}/webhook"
        bot.set_webhook(url=webhook_url)
        logger.info(f"✅ Webhook установлен: {webhook_url}")
        
        # Информация о боте
        bot_info = bot.get_me()
        logger.info(f"✅ Бот запущен: @{bot_info.username}")
        logger.info(f"✅ Папка заказов: {ORDERS_PATH}")
        logger.info(f"✅ ID администратора: {ADMIN_CHAT_ID}")
        
        # Запускаем Flask
        app.run(host="0.0.0.0", port=PORT)
        
    except Exception as e:
        logger.error(f"❌ Ошибка запуска: {e}")
        logger.error(traceback.format_exc())
        sys.exit(1)

if __name__ == "__main__":
    # Проверяем наличие папки для заказов
    if not os.path.exists(ORDERS_PATH):
        os.makedirs(ORDERS_PATH, exist_ok=True)
        logger.info(f"📁 Создана папка заказов: {ORDERS_PATH}")
    
    # Запускаем бота
    run_bot()
