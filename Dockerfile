FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api.py hotspot_optimizer.py ./
COPY static/ static/

# Data directory should be mounted as a volume at runtime:
#   docker run -v /path/to/data:/app/data ...
# This avoids baking the 6+ GB database into the image.

EXPOSE 8000

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
