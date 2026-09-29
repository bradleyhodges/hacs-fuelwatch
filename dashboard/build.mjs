import {build} from 'esbuild';
import {mkdir, copyFile} from 'node:fs/promises';
await mkdir('../custom_components/fuelwatch_wa/www',{recursive:true});
await build({entryPoints:['src/card.js'],bundle:true,format:'esm',minify:true,
  outfile:'../custom_components/fuelwatch_wa/www/fuelwatch-wa-card.js',
  loader:{'.css':'text','.png':'dataurl'},legalComments:'linked'});
await copyFile('node_modules/leaflet/LICENSE','../custom_components/fuelwatch_wa/www/Leaflet-LICENSE.txt');
