FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    zip \
    && rm -rf /var/lib/apt/lists/*

# Install step CLI
RUN curl -fsSL https://github.com/smallstep/cli/releases/download/v0.27.2/step_linux_0.27.2_amd64.tar.gz -o /tmp/step.tar.gz && \
    tar -xzf /tmp/step.tar.gz -C /tmp && \
    mv /tmp/step_0.27.2/bin/step /usr/local/bin/step && \
    chmod +x /usr/local/bin/step && \
    rm -rf /tmp/step*

# Create app directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY app.py .

# Create certs directory
RUN mkdir -p /app/certs

# Expose Streamlit port
EXPOSE 8502

# Run Streamlit
CMD ["streamlit", "run", "app.py", "--server.port=8502", "--server.address=0.0.0.0", "--server.headless=true"]
