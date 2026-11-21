FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY data ./data
COPY wsgi.py .
EXPOSE 8000
# One worker on purpose: per-rider locks and the payout reconciler live in-process; threads give concurrency.
CMD ["gunicorn", "-w", "1", "--threads", "16", "--timeout", "60", "-b", "0.0.0.0:8000", "wsgi:app"]
