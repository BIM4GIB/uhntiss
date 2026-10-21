// Test-only real-audio host check. Excluded from the performance device.
inlets=1;outlets=0;
var stage=0,notes=[],saved=null,tasks=[],expected=[],startAt=0;
function later(ms,fn){var t=new Task(fn,this);tasks.push(t);t.schedule(ms);}
function command(name){this.patcher.getnamed('pocket_engine').message(name);}
function loadbang(){
 var f=new File(jsarguments[1],'read');if(f.isopen){expected=JSON.parse(f.readstring(f.eof));f.close();}
}
function report(ok,detail){
 var f=new File('/tmp/uhntiss-pocket-auto-smoke.json','write','TEXT');
 if(f.isopen){f.eof=0;f.writestring(JSON.stringify({pass:ok,detail:detail,events:notes,expected:expected},null,2));f.close();}
 post('POCKET AUTO: '+detail+'\n');stage=99;
}
function events(){notes=arrayfromargs(arguments);}
function checkNotes(){
 if(notes.length!==expected.length*3)return false;
 for(var i=0;i<expected.length;i++){
  if(notes[i*3+2]!==expected[i][1])return false;
  // 120 BPM host: relative event times must follow the audio, not a grid.
  if(Math.abs((notes[i*3]-notes[0])*.5-(expected[i][0]-expected[0][0]))>.001)return false;
 }
 return true;
}
function status(s,run,p,l,u,d,h,n){
 if(stage===0&&run){stage=1;command('record');later(16000,function(){if(stage<99)report(false,'Timed out at '+stage);});}
 if(stage===1&&s===2){stage=2;this.patcher.getnamed('qa_player').message('start',0);}
 if(stage===2&&s===3){stage=3;later(100,function(){
  if(!checkNotes()){report(false,'Audio capture voices/timing mismatch');return;}
  saved=this.patcher.getnamed('loop_state').getvalueof();command('clear');
 });}
 if(stage===3&&n===0){stage=4;later(100,function(){command('undo');});}
 if(stage===4&&n===expected.length){stage=5;later(100,function(){
  if(!checkNotes()){report(false,'Undo changed audio take');return;}command('clear');
 });}
 if(stage===5&&n===0){stage=6;later(100,function(){this.patcher.getnamed('loop_state').setvalueof(saved);});}
 if(stage===6&&n===expected.length){stage=7;later(100,function(){report(checkNotes(),'Real vocal audio captured in Auto; voices, relative pocket, Clear, Undo and v3 blob restore checked.');});}
}
