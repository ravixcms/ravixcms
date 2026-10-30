/* Self-hosted proof-of-work check. Finds a number s so that SHA-256("<challenge>:<s>") starts with `bits` zero bits,
   then puts it in the form. No network requests, no third party. Works in chunks so the page stays responsive. */
(function () {
  'use strict';
  var K = new Int32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2]);
  var W = new Int32Array(64);

  function sha256(bytes, len) {
    var h0 = 0x6a09e667, h1 = 0xbb67ae85 | 0, h2 = 0x3c6ef372, h3 = 0xa54ff53a | 0, h4 = 0x510e527f, h5 = 0x9b05688c | 0, h6 = 0x1f83d9ab, h7 = 0x5be0cd19;
    var total = ((len + 9 + 63) >> 6) << 6, buf = new Uint8Array(total);
    buf.set(bytes.subarray(0, len));
    buf[len] = 0x80;
    var bitLen = len * 8, hi = Math.floor(bitLen / 4294967296), lo = bitLen >>> 0;
    buf[total - 8] = hi >>> 24; buf[total - 7] = hi >>> 16; buf[total - 6] = hi >>> 8; buf[total - 5] = hi;
    buf[total - 4] = lo >>> 24; buf[total - 3] = lo >>> 16; buf[total - 2] = lo >>> 8; buf[total - 1] = lo;
    for (var off = 0; off < total; off += 64) {
      var i;
      for (i = 0; i < 16; i++) { var j = off + i * 4; W[i] = (buf[j] << 24) | (buf[j + 1] << 16) | (buf[j + 2] << 8) | buf[j + 3]; }
      for (i = 16; i < 64; i++) {
        var a = W[i - 15], b = W[i - 2];
        var s0 = ((a >>> 7) | (a << 25)) ^ ((a >>> 18) | (a << 14)) ^ (a >>> 3);
        var s1 = ((b >>> 17) | (b << 15)) ^ ((b >>> 19) | (b << 13)) ^ (b >>> 10);
        W[i] = (W[i - 16] + s0 + W[i - 7] + s1) | 0;
      }
      var A = h0, B = h1, C = h2, D = h3, E = h4, F = h5, G = h6, H = h7;
      for (i = 0; i < 64; i++) {
        var S1 = ((E >>> 6) | (E << 26)) ^ ((E >>> 11) | (E << 21)) ^ ((E >>> 25) | (E << 7));
        var ch = (E & F) ^ (~E & G);
        var t1 = (H + S1 + ch + K[i] + W[i]) | 0;
        var S0 = ((A >>> 2) | (A << 30)) ^ ((A >>> 13) | (A << 19)) ^ ((A >>> 22) | (A << 10));
        var mj = (A & B) ^ (A & C) ^ (B & C);
        var t2 = (S0 + mj) | 0;
        H = G; G = F; F = E; E = (D + t1) | 0; D = C; C = B; B = A; A = (t1 + t2) | 0;
      }
      h0 = (h0 + A) | 0; h1 = (h1 + B) | 0; h2 = (h2 + C) | 0; h3 = (h3 + D) | 0; h4 = (h4 + E) | 0; h5 = (h5 + F) | 0; h6 = (h6 + G) | 0; h7 = (h7 + H) | 0;
    }
    return [h0, h1, h2, h3, h4, h5, h6, h7];
  }

  function enoughZeroBits(words, bits) {
    var i = 0;
    while (bits >= 32) { if (words[i] !== 0) return false; i++; bits -= 32; }
    return bits === 0 || (words[i] >>> (32 - bits)) === 0;
  }

  function solve(prefix, bits, onDone, onProgress) {
    var pre = new TextEncoder().encode(prefix + ':'), buf = new Uint8Array(pre.length + 16), n = 0;
    buf.set(pre);
    function step() {
      var end = n + 20000;
      for (; n < end; n++) {
        var s = String(n), len = pre.length;
        for (var k = 0; k < s.length; k++) buf[len++] = s.charCodeAt(k);
        if (enoughZeroBits(sha256(buf, len), bits)) return onDone(String(n));
      }
      if (onProgress) onProgress(n);
      setTimeout(step, 0);
    }
    step();
  }

  window.RavixPow = { sha256: sha256, solve: solve };  // exposed for the browser tests

  function init(box) {
    var form = box.closest('form'), challenge = box.querySelector('[name=captcha_challenge]'), solution = box.querySelector('[name=captcha_solution]');
    var status = box.querySelector('[data-pow-status]'), buttons = form ? form.querySelectorAll('[type=submit]') : [];
    var bits = parseInt(box.getAttribute('data-bits'), 10) || 18;
    Array.prototype.forEach.call(buttons, function (b) { b.disabled = true; });
    try {
      solve(challenge.value, bits, function (answer) {
        solution.value = answer;
        box.setAttribute('data-pow-state', 'done');
        status.textContent = box.querySelector('[data-pow-done]').textContent;
        Array.prototype.forEach.call(buttons, function (b) { b.disabled = false; });
      });
    } catch (e) {
      box.setAttribute('data-pow-state', 'failed');
      status.textContent = box.querySelector('[data-pow-failed]').textContent;
    }
  }

  function boot() { Array.prototype.forEach.call(document.querySelectorAll('[data-pow]'), init); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
