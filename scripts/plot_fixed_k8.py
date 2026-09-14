"""Static audit-friendly figures from frozen result tables."""
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from k8_common import DOC,OUT,read,pin,write

plt.rcParams.update({'font.family':'Malgun Gothic','axes.unicode_minus':False,'font.size':10})
v=pd.read_csv(OUT/'recommend/validation-curves.csv')
sel=read(OUT/'recommend/selection.json')
s=pd.DataFrame(read(OUT/'recommend/verification-summary.json'));s=s[s.cap==10]
fig,axs=plt.subplots(1,3,figsize=(16,5.5),gridspec_kw={'width_ratios':[1.25,1,1]})
best=v[v.kind=='group'].sort_values(['ndcg10','id'],ascending=[False,True]).drop_duplicates(['hierarchy','budget'])
pivot=best.pivot(index='hierarchy',columns='budget',values='ndcg10').sort_index()
im=axs[0].imshow(pivot.values,aspect='auto',cmap='YlGnBu',vmin=0,vmax=float(pivot.max().max()))
axs[0].set_xticks(range(len(pivot.columns)),pivot.columns);axs[0].set_yticks(range(len(pivot)),pivot.index)
for i in range(len(pivot)):
    for j in range(len(pivot.columns)):
        val=pivot.iloc[i,j];axs[0].text(j,i,f'{val:.3f}',ha='center',va='center',color='white' if val>.1 else '#13243a',fontsize=9)
axs[0].set_xlabel('rerank 후보 예산');axs[0].set_title('선택용90명: 계층·예산별 최고 NDCG@10')
ids=[sel['group']['id'],sel['baseline']['id'],'full-shared','popularity10']
labels=['그룹+100편','무그룹+100편','전체 예측','인기10편']
by=s.set_index('policy').loc[ids]
colors=['#256c77','#8896a8','#b1c6d1','#c87534']
bars=axs[1].bar(range(4),by.ndcg10,color=colors)
axs[1].set_xticks(range(4),labels,rotation=20,ha='right');axs[1].set_ylim(0,.16)
axs[1].set_title('고정 후173명: 관측 NDCG@10')
for bar,val in zip(bars,by.ndcg10):axs[1].text(bar.get_x()+bar.get_width()/2,val+.004,f'{val:.4f}',ha='center')
bars=axs[2].bar(range(4),by.latency_p95_ms,color=colors)
axs[2].set_yscale('log');axs[2].set_ylim(.1,100);axs[2].set_ylabel('p95 밀리초 · 로그 축')
axs[2].set_yticks([.1,1,10,100],['0.1','1','10','100']);axs[2].minorticks_off()
axs[2].set_xticks(range(4),labels,rotation=20,ha='right');axs[2].set_title('같은180명: warm CPU 처리 시간')
for bar,val in zip(bars,by.latency_p95_ms):axs[2].text(bar.get_x()+bar.get_width()/2,val*1.13,f'{val:.3f}',ha='center')
for ax in axs[1:]:ax.spines[['top','right']].set_visible(False);ax.grid(axis='y',alpha=.15);ax.set_axisbelow(True)
fig.suptitle('16개 하위 그룹 선택 — 전체 평균 개선이 개인화 개선을 뜻하지는 않음',fontsize=16,fontweight='bold',y=.99)
fig.text(.01,.015,'개발 사용자 재사용 · 이력 있음117명은 NDCG 감소, 이력 없음56명은 증가 · UNKNOWN은 비선호가 아님 · BAND_EMPTY 대체 규칙 적용',fontsize=9,color='#445')
fig.tight_layout(rect=[0,.06,1,.94]);fig.savefig(DOC/'quality-and-latency.png',dpi=160);plt.close(fig)

a=pd.read_parquet(OUT/'final/assignments.parquet');counts=a.groupby('taste_id').size();weak=a[~a.top_supported].groupby('taste_id').size().reindex(range(8),fill_value=0)
labels=['솔트\n다큐','핫칠리\n공포','솔티카라멜\n코미디·드라마','다크초코\n드라마','블랙페퍼\n스릴러','갈릭버터\n코미디','허니버터\n애니메이션','치즈믹스\n혼합·근거 부족']
fig,ax=plt.subplots(figsize=(12,5))
ax.bar(range(8),counts-weak,color='#2c7480',label='상위 장르 특징 지원')
ax.bar(range(8),weak,bottom=counts-weak,color='#b9bec6',label='상위 장르 특징 미지원')
for i,n in counts.items():ax.text(i,n+700,f'{n:,}',ha='center',fontsize=10)
ax.set_xticks(range(8),labels);ax.set_ylim(0,61000);ax.set_ylabel('영화 수');ax.legend(frameon=False,loc='upper left')
ax.set_title('전체237,817편의 고정8맛 배정 — 7번의30,577편은 장르 근거 부족',fontsize=15,fontweight='bold',pad=16)
ax.spines[['top','right']].set_visible(False);ax.grid(axis='y',alpha=.15);ax.set_axisbelow(True)
fig.tight_layout();fig.savefig(DOC/'catalog-evidence.png',dpi=160);plt.close(fig)
write(DOC/'plot-inputs.json',{'script':pin(__file__),'files':{p:pin(OUT/p) for p in ['recommend/validation-curves.csv','recommend/selection.json','recommend/verification-summary.json','final/assignments.parquet']}})
