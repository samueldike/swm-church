FROM node:20-bookworm-slim
ENV NODE_ENV=production \
    PUPPETEER_SKIP_DOWNLOAD=true \
    PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium
# System Chromium (WPPConnect drives WhatsApp Web in it), fonts so names and emoji render, tini for signals,
# ffmpeg to turn browser voice recordings into WhatsApp voice notes (OGG/Opus).
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium fonts-liberation fonts-noto-color-emoji ca-certificates tini ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY package.json ./
RUN npm install --omit=dev --no-audit --no-fund
COPY src ./src
# /data holds the WhatsApp logins: it is a volume so they survive rebuilds and restarts.
VOLUME ["/data"]
EXPOSE 4100
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["node", "src/server.js"]
