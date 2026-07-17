"""
EMA_V5 Docker Configuration — Generates Docker and docker-compose configs.
Isolated from existing Docker configurations.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger


class EMAv5DockerConfig:
    """Generates Docker configuration for EMA_V5."""

    def generate_dockerfile(self) -> str:
        """Generate Dockerfile content."""
        return """# EMA_V5 Strategy — Docker Configuration
FROM python:3.14-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \\
    gcc \\
    g++ \\
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Create data directories
RUN mkdir -p data/bridge data/logs data/ema_v5_exports

# Expose ports
EXPOSE 8501 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \\
    CMD python -c "import sys; sys.path.insert(0, '.'); from scanner.ema_v5.deploy.health_check import EMAv5HealthCheck; h=EMAv5HealthCheck(); exit(0 if h.check_all()['healthy'] else 1)"

# Default command
CMD ["python", "-m", "scanner.ema_v5.deploy.deploy_manager", "start"]
"""

    def generate_compose(self) -> str:
        """Generate docker-compose.yml content."""
        return """# EMA_V5 Strategy — Docker Compose
version: '3.8'

services:
  ema-v5-engine:
    build:
      context: .
      dockerfile: Dockerfile.ema_v5
    container_name: ema_v5_engine
    restart: unless-stopped
    ports:
      - "8501:8501"
      - "8080:8080"
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
    environment:
      - EMA_V5_ENV=production
      - EMA_V5_LOG_LEVEL=INFO
      - EMA_V5_TELEGRAM_ENABLED=false
    healthcheck:
      test: ["CMD", "python", "-c", "import sys; sys.path.insert(0, '.'); from scanner.ema_v5.deploy.health_check import EMAv5HealthCheck; h=EMAv5HealthCheck(); exit(0 if h.check_all()['healthy'] else 1)"]
      interval: 30s
      timeout: 10s
      retries: 3
    deploy:
      resources:
        limits:
          cpus: '2.0'
          memory: 2G
        reservations:
          cpus: '0.5'
          memory: 512M

  dashboard:
    build:
      context: .
      dockerfile: Dockerfile.ema_v5
    container_name: ema_v5_dashboard
    restart: unless-stopped
    ports:
      - "8502:8501"
    volumes:
      - ./data:/app/data
    environment:
      - EMA_V5_ENV=production
    command: ["python", "-m", "streamlit", "run", "dashboard/app.py", "--server.port=8501", "--server.address=0.0.0.0"]
    depends_on:
      - ema-v5-engine
"""

    def generate_env_file(self) -> str:
        """Generate .env.example content."""
        return """# EMA_V5 Environment Configuration
# Copy to .env and fill in values

# General
EMA_V5_ENV=development
EMA_V5_LOG_LEVEL=INFO

# Telegram (optional)
EMA_V5_TELEGRAM_ENABLED=false
EMA_V5_TELEGRAM_BOT_TOKEN=
EMA_V5_TELEGRAM_CHAT_ID=

# Risk Management
EMA_V5_RISK_PER_TRADE=1.0
EMA_V5_MAX_POSITIONS=3
EMA_V5_MAX_DAILY_LOSS=5.0
EMA_V5_MAX_DRAWDOWN=15.0

# Strategy
EMA_V5_MIN_CONFIDENCE=90.0
EMA_V5_SL_ATR_MULT=1.5
EMA_V5_TP1_RR=1.5
EMA_V5_TP2_RR=3.0
EMA_V5_TP3_RR=5.0

# Dashboard
EMA_V5_DASHBOARD_PORT=8501
EMA_V5_AUTO_REFRESH=120
"""

    def generate_requirements(self) -> str:
        """Generate requirements.txt content for EMA_V5."""
        return """# EMA_V5 Requirements
numpy>=1.24.0
pandas>=2.0.0
loguru>=0.7.0
httpx>=0.24.0
openpyxl>=3.1.0
streamlit>=1.28.0
streamlit-autorefresh>=1.0.1
"""

    def get_all_configs(self) -> Dict[str, str]:
        """Get all configuration files."""
        return {
            "Dockerfile.ema_v5": self.generate_dockerfile(),
            "docker-compose.ema_v5.yml": self.generate_compose(),
            ".env.ema_v5.example": self.generate_env_file(),
            "requirements.ema_v5.txt": self.generate_requirements(),
        }

    def save_configs(self, output_dir: str = ".") -> Dict[str, str]:
        """Save all config files to disk."""
        import os
        from pathlib import Path

        configs = self.get_all_configs()
        saved = {}

        for filename, content in configs.items():
            filepath = Path(output_dir) / filename
            try:
                with open(filepath, "w") as f:
                    f.write(content)
                saved[filename] = str(filepath)
                logger.info("EMAv5 config saved: {}", filepath)
            except Exception as e:
                logger.error("EMAv5 config save failed {}: {}", filename, e)

        return saved
