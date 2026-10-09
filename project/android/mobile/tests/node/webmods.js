// Загружает модули web_app (как они есть, без правок) в песочницу Node.
// Нужен для сверки: те же входные данные → тот же результат у JS и у Java.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const WEB = path.resolve(__dirname, '..', '..', '..', 'web_app');

function makeContext(extra) {
  const ctx = {
    console, TextDecoder, TextEncoder, Blob, Uint8Array, Uint32Array, Int32Array, Float64Array, DataView,
    ArrayBuffer, BigInt, Math, Date, JSON, Promise, Map, Set, WeakMap, setTimeout, clearTimeout,
    crypto: globalThis.crypto, performance: globalThis.performance, navigator: { deviceMemory: 8, hardwareConcurrency: 8 },
    isSecureContext: true, File: globalThis.File,
  };
  Object.assign(ctx, extra || {});
  ctx.window = ctx;
  ctx.self = ctx;
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  return ctx;
}

function load(ctx, rel, transform) {
  let src = fs.readFileSync(path.join(WEB, rel), 'utf8');
  if (transform) src = transform(src);
  vm.runInContext(src, ctx, { filename: rel });
}

module.exports = { WEB, makeContext, load };
