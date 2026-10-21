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
  assert.equal(boxes.get('engine').text,'uhntiss.pocket02~');
  assert.equal(boxes.get('loop-state').saved_attribute_attributes.valueof.parameter_type,3);
  assert.equal(boxes.get('display').background,1);
  for(const id of ['record','undo','clear','voice','mode','pad0','pad1','pad2'])assert.equal(boxes.get(id).parameter_enable,1);
  for(const line of p.lines) {assert.ok(boxes.has(line.patchline.source[0]));assert.ok(boxes.has(line.patchline.destination[0]));}
  const wired=(a,o,b,i=0)=>p.lines.some(l=>l.patchline.source[0]===a&&l.patchline.source[1]===o&&l.patchline.destination[0]===b&&l.patchline.destination[1]===i);
  assert.ok(wired('in',0,'engine'));assert.ok(wired('phase',0,'engine',1));assert.ok(wired('sync',6,'hostbeat'));assert.ok(wired('engine',0,'out'));assert.ok(wired('engine',1,'out',1));
  assert.ok(!p.boxes.some(b=>/node\.script|shell|udpreceive|tcpclient/.test(b.box.text||'')));
 } finally {rmSync(out,{recursive:true,force:true});}
});
