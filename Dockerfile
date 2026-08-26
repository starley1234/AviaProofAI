FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY stub_tc ./stub_tc
EXPOSE 8080
CMD ["python", "-m", "app.cli", "serve", "--host", "0.0.0.0", "--port", "8080"]
