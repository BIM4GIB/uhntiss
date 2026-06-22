import json, numpy as np, librosa
from collections import Counter
from mouthflow import transcribe as T

PITCH2LAB={36:'kick',38:'snare',42:'hat',46:'hat',39:'perc',-1:'DROP'}
g=json.load(open('mimic/grid.json')); grid=g['grid']  # [(t,label)]
yf,_=librosa.load('mimic/mimic.wav',sr=T._SR,mono=True)
print(f'mimic: peak={np.max(np.abs(yf)):.3f} rms={np.sqrt(np.mean(yf**2)):.4f}')
onsets=list(T._detect_onsets(yf,T._SR))
feats={t:T._features_at(yf,T._SR,t) for t in onsets}
onsets=[t for t in onsets if feats[t]['rms']>=0.005]
print(f'user onsets (usable): {len(onsets)}  | grid notes: {len(grid)}')

gt=np.array([t for t,_ in grid]); gl=[l for _,l in grid]
# find reaction/latency offset that maximizes matches
def matches(delta, tol=0.08):
    used=set(); m=0
    for o in onsets:
        best=-1; bd=tol
        for j,tg in enumerate(gt):
            if j in used: continue
            d=abs(o-(tg+delta))
            if d<bd: bd=d; best=j
        if best>=0: used.add(best); m+=1
    return m
deltas=np.arange(-0.05,0.30,0.005)
best_d=max(deltas,key=matches)
print(f'best onset offset (your reaction+latency): {best_d*1000:.0f} ms  -> {matches(best_d)}/{len(grid)} matched')

# label matched onsets and score model vs heuristic
used=set(); rows=[]
for o in onsets:
    best=-1; bd=0.08
    for j,tg in enumerate(gt):
        if j in used: continue
        d=abs(o-(gt[j]+best_d))
        if d<bd: bd=d; best=j
    if best>=0:
        used.add(best)
        true=gl[best]; f=feats[o]
        mp=PITCH2LAB[T._classify(f)]; hp=PITCH2LAB[T._classify_heuristic(f)]
        rows.append((true,mp,hp))
N=len(rows)
mc=sum(1 for t,m,h in rows if m==t); hc=sum(1 for t,m,h in rows if h==t)
print(f'\nmatched & labeled hits: {N}')
print(f'  TRAINED MODEL class accuracy: {mc}/{N} = {mc/N:.2f}')
print(f'  OLD HEURISTIC class accuracy: {hc}/{N} = {hc/N:.2f}')
print('\nconfusion (TRAINED, true->pred):')
labs=['kick','snare','hat']
conf=Counter((t,m) for t,m,h in rows)
print('          '+'  '.join(f'{l:>6}' for l in labs)+'   (other)')
for t in labs:
    row='  '.join(f'{conf[(t,p)]:>6}' for p in labs)
    other=sum(v for (tt,pp),v in conf.items() if tt==t and pp not in labs)
    print(f'  {t:7} {row}   {other:>6}')
