# Always-on runner for Railway, Fly.io, Render or any Docker host.
# Set the secret FP_API_KEY on the platform; never bake it into the image.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY friendsbot friendsbot
COPY reports/paper/journal.json reports/paper/journal.json
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "friendsbot", "run"]
