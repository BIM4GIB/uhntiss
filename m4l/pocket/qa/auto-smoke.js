// Test-only real-audio host check. Excluded from the performance device.
inlets=1;outlets=0;
var stage=0,notes=[],saved=null,tasks=[],expected=[],liveExpected=[],liveSeen=[];
var duration=0,sourceDone=true,lastSerial=0,skipped=false,beatSeconds=.5,sampleRate=48000;
function later(ms,fn){var t=new Task(fn,this);tasks.push(t);t.schedule(ms);}
function command(name){this.patcher.getnamed('pocket_engine').message(name);}
function loadbang(){
 var f=new File(jsarguments[1],'read');if(f.isopen){var config=JSON.parse(f.readstring(f.eof));f.close();
  expected=config.capture||config;liveExpected=config.live||[];duration=config.duration_ms||0;sourceDone=!duration;beatSeconds=config.beat_seconds||.5;sampleRate=config.sample_rate||48000;
 }
}
function report(ok,detail){
 var f=new File(String(jsarguments[1]).replace('auto-expected.json','auto-host-smoke.json'),'write','TEXT');
 if(f.isopen){f.eof=0;f.writestring(JSON.stringify({pass:ok,detail:detail,events:notes,expected:expected,live_voices:liveSeen,expected_live_voices:liveExpected,skipped_status:skipped},null,2));f.close();}
 post('POCKET AUTO: '+detail+'\n');stage=99;
}
function events(){notes=arrayfromargs(arguments);}
function checkNotes(){
 if(notes.length!==expected.length*3)return false;
 for(var i=0;i<expected.length;i++){
  if(notes[i*3+2]!==expected[i][1])return false;
  if(expected[i].length>2&&Math.abs(notes[i*3+1]-expected[i][2])>1e-6)return false;
  // Actual host beat duration: relative times follow the source, not a grid.
  if(Math.abs((notes[i*3]-notes[0])*beatSeconds-(expected[i][0]-expected[0][0]))>1.01/sampleRate)return false;
 }
 return true;
}
function status(s,run,p,l,u,d,h,n){
 if(stage===2&&h>lastSerial){if(h-lastSerial!==1)skipped=true;liveSeen.push(d);lastSerial=h;}
 if(stage===0&&run){stage=1;command('record');later(30000,function(){if(stage<99)report(false,'Timed out at '+stage);});}
 if(stage===1&&s===2){stage=2;lastSerial=h;this.patcher.getnamed('qa_player').message('start',0);if(duration)later(duration+200,function(){sourceDone=true;});}
 if(stage===2&&s===3&&sourceDone){stage=3;later(100,function(){
  if(liveExpected.length&&(skipped||JSON.stringify(liveSeen)!==JSON.stringify(liveExpected))){report(false,'Full-take live voices mismatch');return;}
  if(!checkNotes()){report(false,'Audio capture voices/timing mismatch');return;}
  saved=this.patcher.getnamed('loop_state').getvalueof();command('clear');
 });}
 if(stage===3&&n===0){stage=4;later(100,function(){command('undo');});}
 if(stage===4&&n===expected.length){stage=5;later(100,function(){
  if(!checkNotes()){report(false,'Undo changed audio take');return;}command('clear');
 });}
 if(stage===5&&n===0){stage=6;later(100,function(){this.patcher.getnamed('loop_state').setvalueof(saved);});}
 if(stage===6&&n===expected.length){stage=7;later(100,function(){report(checkNotes(),'Real vocal audio: complete captured voice sequence, raw dynamics, relative pocket, Clear, Undo and v4 blob restore checked.');});}
}
