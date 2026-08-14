FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements first (for better caching)
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose ports: 8100 REST API, 30000 UI access
EXPOSE 8100 30000

# Health check using a simple HTTP check with a longer start period
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8100/health', timeout=5)" || exit 1

# Run the application
CMD ["python", "server.py"]