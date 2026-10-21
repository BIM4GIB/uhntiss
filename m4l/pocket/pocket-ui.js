// Display only. No audio, transport scheduling or loop mutation lives in JS.
autowatch = 1;
inlets = 1; outlets = 0;
mgraphics.init(); mgraphics.relative_coords = 0; mgraphics.autofill = 0;
var state = 0, running = 0, position = 0, level = 0, undos = 0, count = 0;
var learning=-1, remaining=0, calibrated=false;
var notes = [], lastHit = -1, serial = 0, flashUntil = 0, connected = false;
var ink = [0.89,0.92,0.88,1], muted = [0.47,0.53,0.5,1];
var green = [0.71,0.96,0.36,1], orange = [1,0.48,0.25,1];
var colors = [green,[1,0.64,0.44,1],[0.49,0.78,0.93,1]];
function status(s,r,p,l,u,d,h,n,learn,left,trained) {
    learning=learn;remaining=left;calibrated=trained;
    connected = true; state=s;running=r;position=p;level=l;undos=u;count=n;
    if(h!==serial) {serial=h;lastHit=d;flashUntil=Date.now()+95;}
    var rec=this.patcher.getnamed("record");
    if(rec) {rec.message("text", s===1?"CANCEL ARM":s===2?"CANCEL TAKE":"RECORD 2 BARS");rec.message("texton",s===1?"CANCEL ARM":s===2?"CANCEL TAKE":"RECORD 2 BARS");}
    mgraphics.redraw();
}
function events() {notes=arrayfromargs(arguments);mgraphics.redraw();}
function rect(x,y,w,h,c) {mgraphics.set_source_rgba(c);mgraphics.rectangle(x,y,w,h);mgraphics.fill();}
function text(str,x,y,size,c,bold) {mgraphics.set_source_rgba(c);mgraphics.select_font_face("Arial", "normal",bold?"bold":"normal");mgraphics.set_font_size(size);mgraphics.move_to(x,y);mgraphics.show_text(str);}
function line(x,y,x2,y2,c,width) {mgraphics.set_source_rgba(c);mgraphics.set_line_width(width||1);mgraphics.move_to(x,y);mgraphics.line_to(x2,y2);mgraphics.stroke();}
function paint() {
    rect(0,0,980,169,[.055,.068,.062,1]);
    text("UHNTISS",16,27,23,ink,true);text("POCKET / 02",141,26,10,green,true);
    text("INPUT VOICE",740,24,9,muted,false);text("LOCAL",924,24,10,green,true);
    line(16,38,964,38,[.17,.21,.18,1],1);
    var labels=["READY TO PLAY","ARMED / NEXT BAR","CAPTURING YOUR POCKET","LOOP LOCKED","NO HITS / LOOP KEPT","TAKE FULL / LOOP KEPT","USE 4/4 FOR THIS DEVICE","TAKE CANCELLED / TRANSPORT MOVED"];
    var label=connected?labels[state]:"WAITING FOR AUDIO";
    if(connected && state===1 && !running)label="ARMED / START LIVE TRANSPORT";
    if(connected && state===3 && !running)label="LOOP READY / TRANSPORT STOPPED";
    if(learning>=0)label="TEACH "+["KICK","SNARE","HAT"][learning]+" / "+remaining+" HITS LEFT";
    text(label,310,25,10,state===2?orange:green,true);
    var x=330,y=56,w=310,h=66;
    rect(x,y,w,h,[.075,.095,.083,1]);
    for(var b=0;b<=8;b++) {var bx=x+b*w/8;line(bx,y-3,bx,y+h,[.15,.20,.17,1],b===4?2:1);if(b<8)text(String(b%4+1),bx+3,y-6,8,muted,false);}
    text("K",310,73,9,colors[0],true);text("S",310,95,9,colors[1],true);text("H",310,117,9,colors[2],true);
    for(var row=1;row<3;row++)line(x,y+row*22,x+w,y+row*22,[.11,.15,.12,1],1);
    if(!notes.length)text("YOUR TIMING LIVES HERE",374,93,10,muted,false);
    for(var i=0;i+2<notes.length;i+=3) {
        var nx=x+(notes[i]/8)*w,vel=notes[i+1],drum=notes[i+2];
        rect(nx-1,y+drum*22+18-vel*14,3,3+vel*14,colors[drum]||green);
    }
    if(connected && running)line(x+position*w,y-3,x+position*w,y+h,state===2?orange:ink,1.5);
    text("2 BARS / 4:4",310,140,9,ink,true);text("NO QUANTIZE",428,140,9,muted,false);
    text(undos?"UNDO "+undos+" DEEP":"FIRST TAKE IS YOURS",536,140,9,muted,false);
    var help=state===1?"Start on the next downbeat. Record again cancels.":state===2?"Keep playing. The next cycle is yours.":"Mic or mapped pads. Your timing. Your accents.";
    if(!calibrated && state===0)help="Auto: B / PF / TS. Teach to tune your voice.";
    if(learning>=0)help="Make "+remaining+" isolated "+["B","PF","TS"][learning]+" sounds. Leave a short gap.";
    text(help,310,157,10,muted,false);
    text("INPUT",665,137,8,muted,true);
    rect(702,131,204,5,[.16,.2,.17,1]);rect(702,131,Math.min(1,level*3)*204,5,level>.9?orange:green);
    text(level>.9?"HOT":"",915,137,8,orange,true);

    if(Date.now()<flashUntil && lastHit>=0)rect(16+lastHit*94,164,86,3,colors[lastHit]);
}
function onresize() {mgraphics.redraw();}
onresize.local=1;paint.local=1;rect.local=1;text.local=1;line.local=1;
