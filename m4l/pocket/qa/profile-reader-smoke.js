// Standalone Max runtime check. No audio or private profile values are logged.
inlets=1;outlets=0;
function report(result) {
  var f=new File('/tmp/uhntiss-pocket-reader-smoke.json','write','TEXT');
  if(f.isopen){f.eof=0;f.writestring(JSON.stringify(result,null,2));f.close();}
  post('POCKET READER: '+JSON.stringify(result)+'\n');
}
report.local=1;
function profile() {
  var a=arrayfromargs(arguments);
  var expected=a.length===156?156:246+a[2]*1440+a[3]*17+a[5]*31;
  var valid=a.length===expected && (a.length===156 || (a[0]===5 && a[1]===3));
  for(var i=0;i<a.length;++i)if(typeof a[i]!=='number'||!isFinite(a[i]))valid=false;
  var f=new File('uhntiss_pocket_profile.json','read');
  var result={pass:valid,values:a.length,version:a.length===156?4:a[0],feature_version:a.length===156?null:a[1]};
  if(f.isopen){
    result.file_bytes=f.eof;
    var legacy=f.readstring(f.eof);
    result.legacy_single_read_characters=legacy.length;
    result.legacy_single_read_position=f.position;
    try{JSON.parse(legacy);result.legacy_parse_success=true;}catch(e){result.legacy_parse_success=false;}
    f.close();
  }
  report(result);
}
function profile_error(){report({pass:false,error:'production reader rejected the file'});}
