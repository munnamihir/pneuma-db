# PNEUMA Relay Server — Docker image
# Build: docker build -t pneuma-relay .
# Run:   docker run -d -p 8765:8765 pneuma-relay
# Free:  Deploy to Railway, Fly.io, Oracle Cloud Always Free

FROM python:3.12-slim

# Metadata
LABEL org.opencontainers.image.title="PNEUMA Relay"
LABEL org.opencontainers.image.description="PNEUMA post-quantum relay server"
LABEL org.opencontainers.image.source="https://github.com/YOUR_USERNAME/pneuma-db"
LABEL org.opencontainers.image.version="3.0.0"

# Create non-root user
RUN useradd -m -u 1001 pneuma

WORKDIR /app

# Install dependencies
COPY requirements-relay.txt .
RUN pip install --no-cache-dir -r requirements-relay.txt

# Copy relay server
COPY pneuma_db/ ./pneuma_db/

# Switch to non-root user
USER pneuma

# Expose WebSocket port
EXPOSE 8765

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import asyncio, websockets; asyncio.run(websockets.connect('ws://localhost:8765'))" || exit 1

# Start relay
CMD ["python", "-m", "pneuma_db.relay_server", "--host", "0.0.0.0", "--port", "8765"]
