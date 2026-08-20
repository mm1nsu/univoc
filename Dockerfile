# Uni-VOC 장학금 챗봇 - 배포용 Dockerfile
# Render / Railway / Fly.io / 일반 VPS 어디서든 이 이미지 그대로 쓰면 됨.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 배포 플랫폼이 PORT 환경변수를 내려주면 그 포트로, 없으면 8000으로 뜸.
ENV PORT=8000
EXPOSE 8000

CMD ["python3", "app.py"]
