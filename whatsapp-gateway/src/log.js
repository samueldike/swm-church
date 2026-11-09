// One JSON line per event on stdout (read with `docker logs swm-whatsapp`). No message content is logged.
const line = (level, fields, msg) => console.log(JSON.stringify({ t: new Date().toISOString(), level, msg, ...fields }));
export const log = {
  info: (f, m) => line('info', f, m),
  warn: (f, m) => line('warn', f, m),
  error: (f, m) => line('error', f, m),
};
