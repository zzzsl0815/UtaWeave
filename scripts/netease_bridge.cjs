// Private local adapter. Credentials stay in a server-side file and never enter HTTP logs.
const http = require('http');
const fs = require('fs');
const path = require('path');
console.log = () => {}; console.error = () => {};
const modulePath = process.env.NETEASE_API_MODULE;
if (!modulePath) { process.stderr.write('Set NETEASE_API_MODULE to the installed API module.\n'); process.exit(2); }
const api = require(path.resolve(modulePath));
const cookie = process.env.NETEASE_COOKIE_FILE ? fs.readFileSync(process.env.NETEASE_COOKIE_FILE, 'utf8').trim() : '';
const allowed = new Set(['login_status', 'song_url_v1']);
const server = http.createServer(async (req, res) => {
  let raw = '';
  for await (const chunk of req) {
    raw += chunk;
    if (raw.length > 100000) { res.writeHead(413); res.end(); return; }
  }
  let timer;
  try {
    const {method, params} = JSON.parse(raw);
    if (!allowed.has(method)) throw Error('unsupported');
    const result = await Promise.race([
      api[method]({...params, cookie}),
      new Promise((_, reject) => { timer = setTimeout(() => reject(Error('timeout')), 40000); })
    ]);
    res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(result.body));
  } catch (_) {
    res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify({code: 502, message: 'provider_request_failed'}));
  } finally { clearTimeout(timer); }
});
server.listen(0, '127.0.0.1', () => process.stdout.write(String(server.address().port) + '\n'));
