FROM python:3.12-slim

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY server.py .
COPY eat.html .

# Expose port
EXPOSE 5000

# Run the application
CMD ["python", "server.py"]
