FROM python:3.12-slim

# System deps for building Python C extensions (aiosqlite, bcrypt, etc.)
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer cache)
# Pin numpy FIRST to prevent litellm/streamlit pulling numpy 2.x (x86-64-v2 only)
COPY requirements.txt .
RUN pip install --no-cache-dir "numpy==1.26.4" && \
    pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . .

# API + Dashboard ports
EXPOSE 8000 8501

# Default: run the API (overridden in docker-compose per service)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
