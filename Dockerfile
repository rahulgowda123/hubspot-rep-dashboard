FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

RUN pip install --no-cache-dir gunicorn

COPY . .

EXPOSE 5000

CMD ["gunicorn", "--config", "gunicorn.conf.py", "app:app"]
