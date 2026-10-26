// A focused runtime check for Max File + JS message transport, without Live/DSP.
import fs from 'node:fs';import path from 'node:path';import {fileURLToPath} from 'node:url';
const here=path.dirname(fileURLToPath(import.meta.url)),out=path.resolve(process.argv[2]);
fs.mkdirSync(out,{recursive:true});
fs.copyFileSync(path.join(here,'../pocket-profile.js'),path.join(out,'pocket061-profile.js'));
fs.copyFileSync(path.join(here,'profile-reader-smoke.js'),path.join(out,'profile-reader-smoke.js'));
const boxes=[['load','loadbang'],['reader','js pocket061-profile.js'],['result','js profile-reader-smoke.js']].map(([id,text],i)=>({box:{id,maxclass:'newobj',text,patching_rect:[20,20+i*50,280,22]}}));
const lines=[['load',0,'reader'],['reader',0,'result'],['reader',1,'result']].map(([a,o,b])=>({patchline:{source:[a,o],destination:[b,0]}}));
const patch={patcher:{fileversion:1,appversion:{major:9,minor:1,revision:0,architecture:'x64',modernui:1},rect:[100,100,500,260],boxes,lines}};
fs.writeFileSync(path.join(out,'Pocket Profile Reader QA.maxpat'),JSON.stringify(patch,null,2)+'\n');
