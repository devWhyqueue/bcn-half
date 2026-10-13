FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY bot.py .
CMD ["python", "bot.py"]
