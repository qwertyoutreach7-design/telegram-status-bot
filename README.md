# 🤖 Telegram HTTP Status Checker Bot

Бот для масової перевірки HTTP статусів сайтів з групуванням редіректів.

## 🚀 Функції

- ✅ Масове додавання URL
- 🔍 Перевірка HTTP статусів
- 📊 Групування редіректів по доменах
- 🎯 Виявлення проблем (4xx, 5xx, ERR)
- 💾 Збереження списку URL

## 📋 Встановлення локально

### 1. Клонуй репозиторій
```bash
git clone <your-repo-url>
cd telegram-status-bot
```

### 2. Створи віртуальне середовище
```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate
```

### 3. Встанови залежності
```bash
pip install -r requirements.txt
```

### 4. Налаштуй токен
```bash
# Скопіюй приклад
cp .env.example .env

# Відредагуй .env і вставте свій токен від @BotFather
```

### 5. Запусти бота
```bash
python bot.py
```

## 🌐 Деплой на Google Cloud Run

### Попередні вимоги:
- Google Cloud акаунт
- gcloud CLI встановлено і налаштовано

### Крок 1: Логін у Google Cloud
```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
```

### Крок 2: Увімкни API
```bash
gcloud services enable run.googleapis.com
gcloud services enable containerregistry.googleapis.com
```

### Крок 3: Деплой
```bash
gcloud run deploy telegram-status-bot \
  --source . \
  --platform managed \
  --region europe-central2 \
  --allow-unauthenticated \
  --set-env-vars BOT_TOKEN="YOUR_BOT_TOKEN_HERE" \
  --memory 512Mi \
  --cpu 1 \
  --min-instances 1 \
  --max-instances 10
```

**Заміни `YOUR_BOT_TOKEN_HERE` на свій токен!**

### Крок 4: Перевір логи
```bash
gcloud run services logs tail telegram-status-bot --region europe-central2
```

## 🔄 Оновлення бота

Локально:
```bash
git pull
python bot.py
```

На Cloud Run:
```bash
gcloud run deploy telegram-status-bot \
  --source . \
  --platform managed \
  --region europe-central2
```

## 📝 Використання бота

1. Знайди бота в Telegram
2. Натисни `/start`
3. Використовуй кнопки:
   - **➕ Додати URL** - додати один або кілька URL
   - **🚀 Запустити перевірку** - перевірити всі URL
   - **📋 Список URL** - показати збережені URL
   - **🗑 Очистити список** - видалити всі URL

## 🛠 Структура проєкту