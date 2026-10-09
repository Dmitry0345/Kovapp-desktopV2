const { chromium } = require('playwright');
const fs = require('fs');
(async () => {
const glyph = `<g transform="translate(-1 12.75) scale(0.6875)">
  <path d="M98 16A26 26 0 0 1 124 42" fill="none" stroke="#F5B301" stroke-width="8" stroke-linecap="round"/>
  <rect x="59" y="36" width="11" height="48" rx="5.5" fill="#F5F7F6"/>
  <path d="M95 38L73 60L95 82" fill="none" stroke="#F5F7F6" stroke-width="11" stroke-linecap="round" stroke-linejoin="round"/></g>`;
const square = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 108 108"><rect x="4" y="4" width="100" height="100" rx="26" fill="#132A32"/>${glyph}</svg>`;
const round = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 108 108"><circle cx="54" cy="54" r="50" fill="#132A32"/>${glyph}</svg>`;
const dens = { mdpi: 48, hdpi: 72, xhdpi: 96, xxhdpi: 144, xxxhdpi: 192 };
const out = process.argv[2];
const b = await chromium.launch();
const p = await b.newPage();
for (const [name, svg] of [['ic_launcher', square], ['ic_launcher_round', round]]) {
  for (const [d, px] of Object.entries(dens)) {
    await p.setViewportSize({ width: px, height: px });
    await p.setContent(`<html><body style="margin:0;background:transparent">${svg.replace('<svg ', `<svg width="${px}" height="${px}" `)}</body></html>`);
    fs.mkdirSync(`${out}/mipmap-${d}`, { recursive: true });
    await p.screenshot({ path: `${out}/mipmap-${d}/${name}.png`, omitBackground: true });
  }
}
// предпросмотр: адаптивная иконка под круглой маской и значок уведомления
await p.setViewportSize({ width: 420, height: 140 });
await p.setContent(`<body style="margin:0;background:#ddd;display:flex;gap:20px;padding:10px">
${round.replace('<svg ', '<svg width="120" height="120" ')}${square.replace('<svg ', '<svg width="120" height="120" ')}
<svg width="120" height="120" viewBox="0 0 24 24" style="background:#333"><g transform="translate(-11.79 -1) scale(0.26)">
<path d="M98 16A26 26 0 0 1 124 42" fill="none" stroke="#fff" stroke-width="9" stroke-linecap="round"/>
<rect x="59" y="36" width="11" height="48" rx="5.5" fill="#fff"/>
<path d="M95 38L73 60L95 82" fill="none" stroke="#fff" stroke-width="11" stroke-linecap="round" stroke-linejoin="round"/></g></svg></body>`);
await p.screenshot({ path: 'icons-preview.png' });
await b.close();
})();
