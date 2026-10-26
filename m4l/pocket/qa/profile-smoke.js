// Host-only harness: waits for an actual PROFILE button click. Not shipped.
autowatch=1;inlets=1;outlets=0;
var routing={};
function routing_types(){routing.types=arrayfromargs(arguments);if(!routing.initial_types)routing.initial_types=routing.types.slice(0);}
function routing_selected(){routing.selected=arrayfromargs(arguments);}
function routing_dump(){routing.dump=arrayfromargs(arguments);}
var stage=0,tasks=[],saved=null,receipt=null,notes=[],summary={},started=Date.now();
function later(ms,fn){var t=new Task(fn,this);tasks.push(t);t.schedule(ms);}
function command(name,a,b){var e=this.patcher.getnamed('pocket_engine');if(arguments.length===1)e.message(name);else e.message(name,a,b);}
function report(ok,detail){var f=new File('/tmp/uhntiss-pocket-profile-smoke.json','write','TEXT');if(f.isopen){f.eof=0;f.writestring(JSON.stringify({pass:ok,detail:detail,profile:receipt,state:summary,midi:notes,routing:routing},null,2));f.close();}post('POCKET PROFILE QA '+detail+'\n');}
function profile_loaded(n,sr,rejects){if(stage)return;try {var dev=new LiveAPI(null,'this_device');routing.device_class=dev.get('class_name');routing.midi_outputs=dev.get('midi_outputs');}catch(e){routing.api_error=e.message;}receipt={voices:n,sample_rate:sr,rejects:rejects};stage=1;command('setting','output',0);command('record');later(18000,function(){if(stage<8)report(false,'timeout stage '+stage);});}
function profile_error(){report(false,'profile rejected');}
function profile_rate_mismatch(){report(false,'sample-rate mismatch');}
function midihit(note,vel){if(vel>0 && notes.length<50)notes.push({note:note,velocity:vel,ms:Date.now()-started});}
function status(s,run,p,level,undo,drum,serial,count){
 if(stage===1&&s===2){stage=2;for(var i=0;i<6;i++)(function(n){later(200+n*350,function(){command('tap',n%3,.45+n*.08);});})(i);}
 if(stage===2&&s===3&&count>=6){stage=3;later(150,function(){saved=this.patcher.getnamed('loop_state').getvalueof();summary={version:saved[0],events:saved[2],values:saved.length};command('clear');});}
 if(stage===3&&count===0){stage=4;later(100,function(){command('undo');});}
 if(stage===4&&count===6){stage=5;later(100,function(){command('clear');});}
 if(stage===5&&count===0){stage=6;later(100,function(){this.patcher.getnamed('loop_state').setvalueof(saved);});}
 if(stage===6&&count===6){stage=7;later(100,function(){var profile=saved.slice(3+saved[2]*5);var custom=[5,saved[1],1,.127891,.37,3,3,37].concat(profile);this.patcher.getnamed('loop_state').setvalueof(custom);later(4500,function(){var actual=this.patcher.getnamed('loop_state').getvalueof();summary.custom_note=actual[7];summary.custom_beat=actual[3];var midi=false;for(var i=0;i<notes.length;i++)if(notes[i].note===37)midi=true;stage=8;report(actual[0]===5&&actual[2]===1&&actual[7]===37&&actual[3]===.127891&&midi,'PROFILE acknowledged; capture, clear, undo, pattr restore and custom-note replay checked');command('clear');});});}
}
function events(){}
function voices(){}
