import json, shutil, numpy as np, librosa, mido
from mouthflow import transcribe as T
LAB2PITCH={'kick':36,'snare':38,'hat':42}
g=json.load(open('mimic/grid.json')); grid=g['grid']
yf,_=librosa.load('mimic/mimic.wav',sr=T._SR,mono=True)
onsets=[t for t in T._detect_onsets(yf,T._SR) if T._features_at(yf,T._SR,t)['rms']>=0.005]
gt=np.array([t for t,_ in grid])
def m(d,tol=0.08):
    used=set();c=0
    for o in onsets:
        b=-1;bd=tol
        for j,tg in enumerate(gt):
            if j in used:continue
            dd=abs(o-(tg+d))
            if dd<bd:bd=dd;b=j
        if b>=0:used.add(b);c+=1
    return c
best=max(np.arange(-0.05,0.30,0.005),key=m)
# ground truth = intended pattern at the performed timing
notes=[(float(t+best), LAB2PITCH[l]) for t,l in grid]
DEST='tests/fixtures/clips/01_boombap_mimic'
shutil.copy('mimic/mimic.wav', DEST+'.wav')
tpb=480;bpm=84
mid=mido.MidiFile(ticks_per_beat=tpb);tr=mido.MidiTrack();mid.tracks.append(tr)
tr.append(mido.MetaMessage('set_tempo',tempo=mido.bpm2tempo(bpm),time=0))
ev=[]
for t,p in notes:
    tick=int(round(t*bpm/60*tpb)); ev.append((tick,'on',p)); ev.append((tick+tpb//8,'off',p))
ev.sort();last=0
for tick,k,p in ev:
    tr.append(mido.Message('note_on' if k=='on' else 'note_off',note=p,velocity=90 if k=='on' else 0,time=tick-last,channel=9));last=tick
mid.save(DEST+'.mid')
json.dump({'tempo':bpm,'style':'boom-bap','notes':'mimic of synced reference; auto-labeled via playrec grid + reaction-offset'}, open(DEST+'.json','w'), indent=2)
print('wrote corpus clip:', DEST, f'({len(notes)} GT notes, offset {best*1000:.0f}ms)')
