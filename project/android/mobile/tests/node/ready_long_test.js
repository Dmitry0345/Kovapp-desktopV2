// «Уже подходит» днём: видео с умеренным потоком уходит как есть при любой длине (пережимает сервер ночью).
//   node ready_long_test.js
'use strict';
const { makeContext, load } = require('./webmods');
const ctx = makeContext();
load(ctx, 'js/media/video-prep.js');
const VP = ctx.Kov.VideoPrep;
const base = (w, h, duration, bpp) => ({
  width: w, height: h, fps: 30, duration, bitrate: bpp * w * h * 30,
  video: { entry: { family: 'avc', codec: 'avc1.64001f' }, rotation: 0 },
  audio: { entry: { codec: 'mp4a.40.2' } }, info: { audioTracks: 1 },
});
const cases = [
  ['короткое 1080p с умеренным потоком — готово', base(1080, 1920, 30, 0.07), true],
  ['длинное 1080p с умеренным потоком — днём как есть', base(1080, 1920, 900, 0.07), true],
  ['1080p с большим потоком — сжимается сразу', base(1080, 1920, 900, 0.2), false],
  ['длинное 720p с умеренным потоком — готово (уже HD)', base(720, 1280, 900, 0.07), true],
  ['длинное 720p с большим потоком — сжимается', base(720, 1280, 900, 0.2), false],
  ['ровно 60 с 1080p — как короткое', base(1080, 1920, 60, 0.07), true],
];
let bad = 0;
for (const [name, a, want] of cases) {
  const got = VP.alreadyReady(a, null);
  if (got !== want) bad++;
  console.log((got === want ? '  ok   ' : '  FAIL ') + name + (got === want ? '' : '  — получено ' + got));
}
console.log('Итого: ' + cases.length + ' проверок, ошибок: ' + bad);
process.exit(bad ? 1 : 0);
