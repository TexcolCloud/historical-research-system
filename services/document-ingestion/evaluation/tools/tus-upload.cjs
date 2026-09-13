// Fixed independent tus-js-client 4.3.1, with a receipt spanning client processes.
const fs = require('node:fs');
const tus = require('tus-js-client');
const [file, endpoint, digest, receiptPath, pauseText, chunkText] = process.argv.slice(2);
const pauseAt = Number(pauseText || 0);
const started = Date.now();
const previous = fs.existsSync(receiptPath) ? JSON.parse(fs.readFileSync(receiptPath, 'utf8')) : null;
let stopped = false;
const events = [];
function save(state, accepted) {
  const result = {client: 'tus-js-client-4.3.1', state, upload_url: upload.url,
    accepted, total: fs.statSync(file).size, seconds: (Date.now() - started) / 1000,
    max_rss_kib: process.resourceUsage().maxRSS, events};
  fs.writeFileSync(receiptPath, JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
}
const upload = new tus.Upload(fs.createReadStream(file), {
  endpoint: previous ? null : endpoint,
  uploadUrl: previous?.upload_url,
  chunkSize: Number(chunkText || 8 * 1024 * 1024), retryDelays: null,
  storeFingerprintForResuming: false,
  metadata: {container: 'zip', sha256: digest},
  headers: {'Idempotency-Key': `acceptance-${digest}`},
  onAfterResponse(req, res) {
    events.push({method: req.getMethod(), status: res.getStatus(), offset: res.getHeader('Upload-Offset')});
    if (previous && req.getMethod() === 'POST') throw new Error('Resume must never create a new upload');
  },
  onChunkComplete(chunk, accepted) {
    if (pauseAt && accepted >= pauseAt && !stopped) {
      stopped = true;
      upload.abort(false).then(() => { save('paused', accepted); process.exit(0); });
    }
  },
  onSuccess() { if (!stopped) { save('uploaded', fs.statSync(file).size); process.exit(0); } },
  onError(error) { console.error(error.message); process.exit(1); },
});
upload.start();
