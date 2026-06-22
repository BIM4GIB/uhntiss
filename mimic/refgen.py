import numpy as np, soundfile as sf, json
SR=44100; BPM=84; beat=60.0/BPM
def env(n,k): t=np.arange(n)/SR; return np.exp(-t*k)
def kick():
    n=int(0.20*SR); t=np.arange(n)/SR
    return (np.sin(2*np.pi*65*t)*env(n,20)).astype('float32')
def snare():
    n=int(0.16*SR)
    return (0.7*(np.random.randn(n)+0.3*np.sin(2*np.pi*190*np.arange(n)/SR))*env(n,28)).astype('float32')
def hat():
    n=int(0.05*SR); x=np.random.randn(n); x=np.diff(x,prepend=0.0)
    return (0.5*x*env(n,110)).astype('float32')
def click():
    n=int(0.03*SR); return (0.4*np.sin(2*np.pi*1000*np.arange(n)/SR)*env(n,150)).astype('float32')
# layout: 1 count-in bar (4 clicks) then 4 pattern bars
bars_pat=4; total_beats=(1+bars_pat)*4
buf=np.zeros(int((total_beats*beat+0.5)*SR),dtype='float32')
def place(sound,t): 
    s=int(t*SR); buf[s:s+len(sound)]+=sound
grid=[]  # ground truth pattern notes (label per onset)
# count-in
for b in range(4): place(click(), b*beat)
# pattern bars start at beat 4
for bar in range(bars_pat):
    base=(4+bar*4)*beat
    place(kick(), base+0*beat); grid.append((base+0*beat,'kick'))
    place(hat(),  base+0.5*beat); grid.append((base+0.5*beat,'hat'))
    place(snare(),base+1*beat); grid.append((base+1*beat,'snare'))
    place(hat(),  base+1.5*beat); grid.append((base+1.5*beat,'hat'))
    place(kick(), base+2*beat); grid.append((base+2*beat,'kick'))
    place(hat(),  base+2.5*beat); grid.append((base+2.5*beat,'hat'))
    place(snare(),base+3*beat); grid.append((base+3*beat,'snare'))
    place(hat(),  base+3.5*beat); grid.append((base+3.5*beat,'hat'))
buf=np.clip(buf,-1,1)
sf.write('mimic/reference.wav', buf, SR, subtype='PCM_16')
json.dump({'sr':SR,'bpm':BPM,'pattern_start_s':4*beat,'grid':grid}, open('mimic/grid.json','w'))
from collections import Counter
print(f'reference: {len(buf)/SR:.1f}s, {len(grid)} pattern notes, {dict(Counter(l for _,l in grid))}')
print('count-in: 4 clicks, then 4 bars of boom-ts-pa-ts @ 84 BPM')
