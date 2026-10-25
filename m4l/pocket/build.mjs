#!/usr/bin/env node
// Generate a reviewable Max patch and its audio-effect container. No Python.
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const here=path.dirname(fileURLToPath(import.meta.url));
const out=path.resolve(process.argv[2] || path.join(here,'build'));
fs.mkdirSync(out,{recursive:true});
const boxes=[],lines=[],parameters={};
let row=200;
function box(id,maxclass,props={}) {boxes.push({box:{id,maxclass,patching_rect:[20,row+=30,180,22],...props}});return id;}
function obj(id,text,props={}) {return box(id,'newobj',{text,...props});}
function wire(a,outlet,b,inlet=0) {lines.push({patchline:{source:[a,outlet],destination:[b,inlet]}});}
function param(id,name,initial=0,type=2,max=1,extra={}) {
 parameters[id]=[name,name,0];
 return {parameter_enable:1,varname:id,saved_attribute_attributes:{valueof:{parameter_longname:name,parameter_shortname:name,parameter_type:type,parameter_mmin:0,parameter_mmax:max,parameter_initial:[initial],parameter_initial_enable:1,...extra}}};
}
function button(id,label,x,y,w,h,command,{toggle=false,on=label,initial=0,accent=false}={}) {
 const p=param(id,label,initial,2,1,{parameter_enum:['Off','On'],parameter_invisible:toggle?0:4,parameter_surface:toggle?0:1});
 box(id,'live.text',{...p,mode:toggle?1:0,text:label,texton:on,fontname:'Arial',fontsize:id==='record'?13:10,rounded:5,presentation:1,presentation_rect:[x,y,w,h],activebgcolor:accent?[.71,.96,.36,1]:[.14,.18,.15,1],activebgoncolor:[.71,.96,.36,1],activetextcolor:accent?[.05,.07,.05,1]:[.85,.90,.85,1],activetextoncolor:[.05,.07,.05,1],annotation:command});
 if(toggle) {obj(id+'-msg','prepend setting '+command);wire(id,0,id+'-msg');}
 else {box(id+'-msg','message',{text:command});wire(id,0,id+'-msg');}
 wire(id+'-msg',0,'engine');
}
box('display','jsui',{filename:'pocket-ui.js',jsarguments:[],numinlets:1,numoutlets:0,border:0,ignoreclick:1,background:1,presentation:1,presentation_rect:[0,0,1155,169],patching_rect:[20,20,1155,169]});
obj('in','plugin~');obj('phase','plugphasor~');obj('sync','plugsync~');
obj('engine','uhntiss.pocket06~',{varname:'pocket_engine'});obj('out','plugout~');
// The fourth outlet is a high-priority MIDI stream, independent of UI polling.
obj('note-unpack','unpack i i');obj('note-length','makenote 100 30');
obj('note-pack','pack i i');obj('midi-format','midiformat');obj('midi-out','midiout');
wire('engine',3,'note-unpack');wire('note-unpack',1,'note-length',1);wire('note-unpack',0,'note-length');
wire('note-length',1,'note-pack',1);wire('note-length',0,'note-pack');wire('note-pack',0,'midi-format');wire('midi-format',0,'midi-out');
// Live's Device I/O routing exposes MIDI from an audio effect to a rack track.
// Only route selection uses the Live API; notes stay on the scheduler MIDI path.
obj('midi-routing','live.routing',{varname:'midi_routing'});
obj('midi-port','loadmess port midi_outputs');wire('midi-port',0,'midi-routing');
function routingMenu(id,x,y,width,selected,list,command) {
 box(id,'umenu',{varname:id,items:['No output'],presentation:1,presentation_rect:[x,y,width,21],fontsize:10,parameter_enable:0});
 obj(id+'-selected','route <none>');wire('midi-routing',selected,id+'-selected');
 box(id+'-none','message',{text:'clear'});wire(id+'-selected',0,id+'-none');wire(id+'-none',0,id);
 obj(id+'-set','prepend set');wire(id+'-selected',1,id+'-set');wire(id+'-set',0,id);
 obj(id+'-list','t l clear');wire('midi-routing',list,id+'-list');wire(id+'-list',1,id);
 obj(id+'-iter','iter');obj(id+'-append','prepend append');wire(id+'-list',0,id+'-iter');wire(id+'-iter',0,id+'-append');wire(id+'-append',0,id);
 obj(id+'-choose','prepend '+command);wire(id,0,id+'-choose');wire(id+'-choose',0,'midi-routing');
}
routingMenu('midi-target',984,49,155,0,2,'type');
routingMenu('midi-channel',984,80,155,1,3,'channel');
wire('in',0,'engine');wire('phase',0,'engine',1);wire('engine',0,'out');wire('engine',1,'out',1);wire('engine',2,'display');
obj('hostbeat','prepend hostbeat');obj('running','prepend running');obj('meter','prepend meter');
wire('sync',6,'hostbeat');wire('sync',0,'running');wire('sync',4,'meter');
for(const id of ['hostbeat','running','meter'])wire(id,0,'engine');
obj('loop-state','pattr loop_state @bindto pocket_engine @parameter_enable 1',{varname:'loop_state',parameter_enable:1,saved_attribute_attributes:{valueof:{parameter_longname:'Pocket loop data',parameter_shortname:'Loop data',parameter_type:3,parameter_invisible:1}}});
parameters['loop-state']=['Pocket loop data','Loop data',0];
button('record','RECORD 2 BARS',16,48,132,44,'record',{accent:true});
button('undo','UNDO',156,48,62,44,'undo');button('clear','CLEAR',226,48,62,44,'clear');
button('mode','REPLACE',16,101,132,26,'overdub',{toggle:true,on:'OVERDUB'});
button('monitor','LIVE',156,101,62,26,'monitor',{toggle:true,initial:1});
button('playback','LOOP',226,101,62,26,'playback',{toggle:true,initial:1});
for(const [i,name] of ['KICK','SNARE','HAT'].entries())button('pad'+i,name,16+i*94,137,86,22,'tap '+i+' 0.85');
box('voice','live.menu',{...param('voice','Input Voice',0,2,3,{parameter_enum:['Auto','Kick','Snare','Hat']}),items:['Auto','Kick','Snare','Hat'],presentation:1,presentation_rect:[828,10,87,21]});
obj('voice-msg','prepend setting voice');wire('voice',0,'voice-msg');wire('voice-msg',0,'engine');
for(const [i,name] of ['K','S','H'].entries())button('learn'+i,'TEACH '+name,665+i*101,144,93,18,'learn '+i);
// PROFILE: load a Teach profile built offline from one-class enrollment clips
// (`mouthflow enroll export-pocket` writes uhntiss_pocket_profile.json on Max's
// search path). js reads the file; the engine validates a v4 or v5 `profile` payload.
box('loadprofile','live.text',{...param('loadprofile','Load profile',0,2,1,{parameter_enum:['Off','On'],parameter_invisible:4,parameter_surface:1}),mode:0,text:'PROFILE',texton:'PROFILE',fontname:'Arial',fontsize:10,rounded:5,presentation:1,presentation_rect:[665,10,70,21],activebgcolor:[.14,.18,.15,1],activebgoncolor:[.71,.96,.36,1],activetextcolor:[.85,.90,.85,1],activetextoncolor:[.05,.07,.05,1],annotation:'Load the Teach profile exported by mouthflow enroll export-pocket'});
obj('profile-js','js pocket-profile.js');wire('loadprofile',0,'profile-js');wire('profile-js',0,'engine');wire('profile-js',1,'display');
const knobs=[['input','Input',1,0.1,4],['threshold','Gate',.010,.002,.12],['tone','Tone',.5,0,1],['decay','Decay',.5,0,1],['drive','Heat',.25,0,1],['output','Level',.75,0,1]];
for(const [i,[id,name,value,min,max]] of knobs.entries()) {
 const p=param(id,name,value,0,max,{parameter_mmin:min,parameter_unitstyle:1});
 box(id,'live.dial',{...p,presentation:1,presentation_rect:[662+i*51,51,44,68],appearance:0,activefgdialcolor:[.71,.96,.36,1],activeneedlecolor:[.89,.94,.89,1],textcolor:[.67,.73,.68,1],annotation:id==='threshold'?'Raise the gate until room noise stops triggering hits.':name});
 obj(id+'-msg','prepend setting '+id);wire(id,0,id+'-msg');wire(id+'-msg',0,'engine');
}
// Live parameter restoration sends the current controls; loadbang handles Max standalone.
obj('load','live.thisdevice');obj('init','t b b b b b b b b b b');wire('load',0,'init');
['voice','mode','monitor','playback',...knobs.map(k=>k[0])].forEach((id,i)=>{box('read-'+id,'message',{text:'outputvalue'});wire('init',i,'read-'+id);wire('read-'+id,0,id);});
obj('banks','live.banks',{banks:{0:['record','undo','mode','clear','input','threshold','tone','output'],1:['pad0','pad1','pad2','voice','decay','drive','monitor','playback']}});
boxes.push(boxes.shift()); // Max stores frontmost boxes first; the canvas belongs behind controls.
const patch={patcher:{fileversion:1,appversion:{major:9,minor:1,revision:0,architecture:'x64',modernui:1},classnamespace:'box',rect:[100,100,1000,700],openinpresentation:1,default_fontsize:12,default_fontface:0,default_fontname:'Arial',bgcolor:[.055,.068,.062,1],devicewidth:1155,boxes,lines,parameters,dependency_cache:[{name:'pocket-ui.js',type:'TEXT',implicit:1},{name:'pocket-profile.js',type:'TEXT',implicit:1},{name:'uhntiss.pocket06~.mxo',type:'iLaX'}],autosave:0}};
const json=JSON.stringify(patch,null,2);
fs.writeFileSync(path.join(out,'UHNTISS Pocket.maxpat'),json+'\n');
const payload=Buffer.from(json+'\0');
const header=Buffer.alloc(32);header.write('ampf',0);header.writeUInt32LE(4,4);header.write('aaaa',8);header.write('meta',12);header.writeUInt32LE(4,16);header.writeUInt32LE(1,20);header.write('ptch',24);header.writeUInt32LE(payload.length,28);
fs.writeFileSync(path.join(out,'UHNTISS Pocket.amxd'),Buffer.concat([header,payload]));
fs.copyFileSync(path.join(here,'pocket-ui.js'),path.join(out,'pocket-ui.js'));
fs.copyFileSync(path.join(here,'pocket-profile.js'),path.join(out,'pocket-profile.js'));
console.log('Device generated: '+out);
