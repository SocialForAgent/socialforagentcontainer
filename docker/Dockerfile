FROM python:3.12-slim

RUN pip install socialforagent

WORKDIR /opt/sfa-bridge
COPY src/ /opt/sfa-bridge/
COPY config/ /opt/sfa-bridge/config/

ENV HERMES_HOME=/opt/hermes

CMD ["python3", "bridge.py", "config.json"]
