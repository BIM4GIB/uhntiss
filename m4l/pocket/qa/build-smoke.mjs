import fs from 'node:fs';import path from 'node:path';import {fileURLToPath} from 'node:url';
const out=path.resolve(process.argv[2]);
const buf=fs.readFileSync(path.join(out,'UHNTISS Pocket.amxd'));
const data=JSON.parse(buf.subarray(32,-1));
data.patcher.boxes.unshift({box:{id:'qa',maxclass:'newobj',text:'js pocket-smoke.js',patching_rect:[400,250,150,22]}});
data.patcher.lines.push({patchline:{source:['engine',2],destination:['qa',0]}});
const payload=Buffer.from(JSON.stringify(data)+'\0'),header=Buffer.from(buf.subarray(0,32));header.writeUInt32LE(payload.length,28);
fs.writeFileSync(path.join(out,'UHNTISS Pocket QA.amxd'),Buffer.concat([header,payload]));
fs.copyFileSync(fileURLToPath(new URL('./smoke.js',import.meta.url)),path.join(out,'pocket-smoke.js'));
