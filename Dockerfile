FROM python:3.11-slim

WORKDIR /app

# Kerakli kutubxonalarni o‘rnatish
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bot faylini nusxalash
COPY main.py .

# Botni ishga tushirish
CMD ["python", "main.py"]