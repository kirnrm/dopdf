FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV SITE2PDF_ALLOWED_HOSTS=jnanakshi.org PORT=8080
EXPOSE 8080
CMD ["gunicorn","-w","1","--threads","4","--timeout","1800","-b","0.0.0.0:8080","app:app"]
