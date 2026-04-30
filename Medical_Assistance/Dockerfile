# Use a stable slim version
FROM python:3.10-slim-bookworm

# Install system dependencies
# libgl1  <-- FIXES ImportError: libGL.so.1
# libglib2.0-0 <-- Required by OpenCV
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first
COPY requirements.txt .

# Install CPU-only PyTorch (Keep this to save space)
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu

# Install other dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

ENV PYTHONUNBUFFERED=1

CMD ["python", "app_with_followup.py"]