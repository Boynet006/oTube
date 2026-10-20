FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY templates/ templates/
COPY static/ static/

# config.json, duration cache, thumbs and transcodes live here (mount a volume)
ENV OTUBE_DATA=/data PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8700
CMD ["python", "app.py"]
