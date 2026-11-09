FROM python:3.11-slim

# Prevent python from buffering stdout/stderr
ENV PYTHONUNBUFFERED=1
ENV PORT=8791
ENV HOST=0.0.0.0
ENV TZ=America/Chicago

WORKDIR /app

# Install system dependencies and configure timezone
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Ensure data directories exist
RUN mkdir -p zero_dte_scalper/data

EXPOSE 8791

# Start the multi-service cloud supervisor
CMD ["python3", "cloud_entrypoint.py"]
