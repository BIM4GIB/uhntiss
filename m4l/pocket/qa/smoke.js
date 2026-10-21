// Test harness only; never included in the playable device.
inlets=1;outlets=0;
var stage=0,tasks=[],log=[],saved=null,expected=0;
function command(name,a,b) {var e=this.patcher.getnamed('pocket_engine');if(arguments.length===1)e.message(name);else e.message(name,a,b);}
function later(ms,fn) {var t=new Task(fn,this);tasks.push(t);t.schedule(ms);}
function report(ok,detail) {
    var f=new File('/tmp/uhntiss-pocket-smoke.json','write','TEXT');
    if(f.isopen) {f.eof=0;f.writestring(JSON.stringify({pass:ok,detail:detail,events:log},null,2));f.close();}
    post('POCKET SMOKE: '+(ok?'PASS ':'FAIL ')+detail+'\n');
}
function status(s,run,p,level,undo,drum,serial,count) {
    if(!log.length || log[log.length-1].state!==s || log[log.length-1].count!==count)log.push({state:s,count:count,undo:undo,at:Date.now()});
    if(stage===0 && run) {stage=1;command('record');later(16000,function(){if(stage<7)report(false,'Timed out at stage '+stage);});}
    if(stage===1 && s===2) {stage=2;for(var i=0;i<6;i++)(function(n){later(200+n*350,function(){command('tap',n%3,.45+n*.08);});})(i);}
    if(stage===2 && s===3 && count>=6) {stage=3;expected=count;later(150,function(){saved=this.patcher.getnamed('loop_state').getvalueof();command('clear');});}
    if(stage===3 && count===0) {stage=4;later(150,function(){command('undo');});}
    if(stage===4 && count===expected) {stage=5;later(150,function(){command('clear');});}
    if(stage===5 && count===0) {stage=6;later(150,function(){this.patcher.getnamed('loop_state').setvalueof(saved);});}
    if(stage===6 && count===expected) {stage=7;report(true,'Captured six timed pad hits; clear, undo and pattr blob restore succeeded.');}
}
function events() {}
