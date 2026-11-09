export const config = {
  port: Number(process.env.WA_PORT || 4100),
  // Shared secret with the SWM app (WA_GATEWAY_TOKEN). The gateway only listens on the internal Docker
  // network, and every request must carry it as a Bearer token.
  token: process.env.WA_GATEWAY_TOKEN || '',
  dataDir: process.env.WA_DATA_DIR || '/data',
  sessionDir: process.env.WA_SESSION_DIR || '/data/sessions',
  cacheDir: process.env.WA_CACHE_DIR || '/data/media-cache',
  // SWM's media folder, mounted read-only at the same path, so uploads are handed over as file paths.
  mediaRoot: process.env.MEDIA_ROOT || '/app/media',
  chromium: process.env.PUPPETEER_EXECUTABLE_PATH || '/usr/bin/chromium',
};
