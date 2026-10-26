import vm from 'node:vm';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,readFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
test('audio device packages the actual native path, controls and persistent state',()=>{
 const out=mkdtempSync(path.join(tmpdir(),'pocket-package-'));
 try {
  execFileSync(process.execPath,[fileURLToPath(new URL('./build.mjs',import.meta.url)),out]);
  const data=readFileSync(path.join(out,'UHNTISS Pocket.amxd'));
  assert.equal(data.subarray(0,4).toString(),'ampf');assert.equal(data.subarray(8,12).toString(),'aaaa');assert.equal(data.subarray(24,28).toString(),'ptch');assert.equal(data.readUInt32LE(28),data.length-32);assert.equal(data.at(-1),0);
  const p=JSON.parse(data.subarray(32,-1)).patcher;
  const boxes=new Map(p.boxes.map(b=>[b.box.id,b.box]));
  assert.equal(boxes.get('engine').text,'uhntiss.pocket07~');
  assert.equal(boxes.get('display').filename,'pocket07-ui.js');assert.equal(boxes.get('profile-js').text,'js pocket07-profile.js');
  for(const file of ['pocket07-ui.js','pocket07-profile.js'])assert.ok(readFileSync(path.join(out,file)).length>0);
  assert.equal(boxes.get('loop-state').saved_attribute_attributes.valueof.parameter_type,3);
  assert.equal(boxes.get('display').background,1);
  for(const id of ['record','undo','clear','voice','mode','pad0','pad1','pad2'])assert.equal(boxes.get(id).parameter_enable,1);
  for(const line of p.lines) {assert.ok(boxes.has(line.patchline.source[0]));assert.ok(boxes.has(line.patchline.destination[0]));}
  const wired=(a,o,b,i=0)=>p.lines.some(l=>l.patchline.source[0]===a&&l.patchline.source[1]===o&&l.patchline.destination[0]===b&&l.patchline.destination[1]===i);
  assert.ok(wired('loadprofile',0,'profile-js'));assert.ok(wired('profile-js',1,'display'));assert.ok(wired('engine',3,'note-unpack'));assert.equal(boxes.get('midi-routing').text,'live.routing');assert.ok(wired('load',0,'midi-routing-init'));assert.ok(wired('midi-routing-init',0,'midi-routing-query'));assert.ok(wired('midi-routing-query',1,'midi-port'));assert.ok(wired('midi-routing-query',0,'midi-routing'));assert.ok(wired('midi-target-choose',0,'midi-routing'));assert.ok(wired('note-pack',0,'midi-format'));assert.ok(wired('in',0,'engine'));assert.ok(wired('phase',0,'engine',1));assert.ok(wired('sync',6,'hostbeat'));assert.ok(wired('engine',0,'out'));assert.ok(wired('engine',1,'out',1));
  assert.ok(!p.boxes.some(b=>/node\.script|shell|udpreceive|tcpclient/.test(b.box.text||'')));
 } finally {rmSync(out,{recursive:true,force:true});}
});

test('PROFILE int click sends v4/v5; failures stay visible and never claim installation',()=>{
 const source=readFileSync(new URL('./pocket-profile.js',import.meta.url),'utf8');
 let contents='', exists=true, messages=[], readCalls=0, stalled=false, readLimit=32767, advanceRequested=true;
 const context=vm.createContext({post(){},outlet(...args){messages.push(args)},File:function(){this.isopen=exists;this.eof=contents.length;this.position=0;this.readstring=(n)=>{readCalls++;if(stalled)return "";const remaining=contents.length-this.position;const result=contents.slice(this.position,this.position+Math.min(n,readLimit));this.position+=advanceRequested?Math.min(n,remaining):result.length;return result;};this.close=()=>{};}});
 vm.runInContext(source,context);
 assert.notEqual(context.msg_int.local,1);
 const click=doc=>{contents=typeof doc==='string'?doc:JSON.stringify(doc);messages=[];context.msg_int(1);return messages;};
 assert.equal(click({version:4,values:Array(156).fill(0)})[0].length,158);
 const values=Array(24726).fill(0);values[0]=5;values[1]=3;
 readCalls=0;assert.equal(click({version:5,feature_version:3,values})[0].length,24728);
 values[1]=4;assert.equal(click({version:5,feature_version:4,values})[0].length,24728);
 assert.deepEqual(click({version:5,feature_version:3,values}),[[1,'profile_error']]);values[1]=3;assert.ok(readCalls>20,"large profile must stay below Max's per-read string limit");
 readLimit=128;advanceRequested=false;assert.equal(click({version:5,feature_version:3,values})[0].length,24728);
 values[1]=4;assert.equal(click({version:5,feature_version:4,values})[0].length,24728);
 assert.deepEqual(click({version:5,feature_version:3,values}),[[1,'profile_error']]);values[1]=3;
 for(const doc of ['{broken', {version:5,feature_version:2,values}, {version:4,values:[0]}]) assert.deepEqual(click(doc),[[1,'profile_error']]);
 stalled=true;assert.deepEqual(click('{}'),[[1,'profile_error']]);stalled=false;
 exists=false;assert.deepEqual(click('{}'),[[1,'profile_error']]);
});
