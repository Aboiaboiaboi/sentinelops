// One-off raster generation, run manually (`node scripts/generate-assets.mjs`)
// whenever the OG banner or icon SVGs change — not part of the build, since
// the outputs are committed like any other static asset under public/.
import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Resvg } from '@resvg/resvg-js';

const root = path.dirname(fileURLToPath(import.meta.url));
const publicDir = path.join(root, '..', 'public');

function render(svgPath, outPath, width) {
  const svg = readFileSync(svgPath, 'utf-8');
  const resvg = new Resvg(svg, { fitTo: { mode: 'width', value: width } });
  writeFileSync(outPath, resvg.render().asPng());
  console.log(`wrote ${path.relative(process.cwd(), outPath)}`);
}

render(path.join(root, 'og-image.svg'), path.join(publicDir, 'og-image.png'), 1200);
render(path.join(publicDir, 'favicon.svg'), path.join(publicDir, 'apple-touch-icon.png'), 180);
render(path.join(publicDir, 'favicon.svg'), path.join(publicDir, 'favicon-48x48.png'), 48);
