# Container image for the two browser-free services: target_app and
# operator_console. discover.py/replay.py/capability_api.py drive a headed
# Playwright browser and stay on the host — see the "Quick start via
# Docker" section in README.md.
FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt requirements.txt
COPY target_app/requirements.txt target_app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt -r target_app/requirements.txt

COPY . .

# No default CMD: docker-compose.yml sets the command per service.
