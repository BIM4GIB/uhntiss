import fs from 'node:fs';import path from 'node:path';import {fileURLToPath} from 'node:url';
const out=path.resolve(process.argv[2]);
const buf=fs.readFileSync(path.join(out,'UHNTISS Pocket.amxd'));
const data=JSON.parse(buf.subarray(32,-1));
const box=(id,text,extra={})=>({box:{id,maxclass:'newobj',text,patching_rect:[400,250,400,22],...extra}});
data.patcher.boxes.unshift(
 box('qa',`js pocket-auto-smoke.js "${path.join(out,'auto-expected.json')}"`),
 box('qa-buffer',`buffer~ pocket_qa_audio "${path.join(out,'auto-input.wav')}"`),
 box('qa-player','play~ pocket_qa_audio 1',{varname:'qa_player'}));
data.patcher.lines=data.patcher.lines.filter(l=>!(l.patchline.source[0]==='in'&&l.patchline.destination[0]==='engine'));
for(const [a,o,b,i] of [['qa-player',0,'engine',0],['engine',2,'qa',0]])data.patcher.lines.push({patchline:{source:[a,o],destination:[b,i]}});
const payload=Buffer.from(JSON.stringify(data)+'\0'),header=Buffer.from(buf.subarray(0,32));header.writeUInt32LE(payload.length,28);
fs.writeFileSync(path.join(out,'UHNTISS Pocket Auto QA.amxd'),Buffer.concat([header,payload]));
fs.copyFileSync(fileURLToPath(new URL('./auto-smoke.js',import.meta.url)),path.join(out,'pocket-auto-smoke.js'));
