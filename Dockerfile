# 1. 베이스 이미지
FROM apache/airflow:3.0.3-python3.11

# 2. root 유저로 전환하여 OS 패키지 설치
USER root
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl wget gnupg ca-certificates libnss3 libatk-bridge2.0-0 \
        libx11-xcb1 libxcomposite1 libxcursor1 libxdamage1 libxi6 \
        libxtst6 libpangocairo-1.0-0 libgtk-3-0 libxrandr2 libasound2 \
        fonts-liberation lsb-release xdg-utils && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 3. 다시 airflow 유저로 전환 (매우 중요: 이 아래 작업은 airflow 권한으로 수행)
USER airflow

# 4. 필요한 Python 패키지들을 한 번에 설치
RUN pip install --no-cache-dir \
    playwright \
    apache-airflow-providers-postgres \
    apache-airflow-providers-apache-spark \
    psycopg2-binary

# 5. Playwright용 브라우저 설치
RUN playwright install chromium
