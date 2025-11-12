FROM python:3.11-slim

WORKDIR /app

# Копіюємо файл залежностей
COPY requirements.txt .

# Встановлюємо залежності
RUN pip install --no-cache-dir -r requirements.txt

# Копіюємо код бота
COPY bot.py .

# Створюємо директорію для даних
RUN mkdir -p /app/data

# Порт (не обов'язково для Cloud Run, але для сумісності)
ENV PORT=8080

# Команда запуску
CMD ["python", "bot.py"]