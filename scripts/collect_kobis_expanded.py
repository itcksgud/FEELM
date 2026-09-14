"""Collect full public KOBIS exports, keeping annual and cumulative scopes separate.

No credentials, model fitting, or product changes. Resumable raw snapshots are
hash checked. A failed year is fatal; a truncated screen is never the full dataset.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone, date
import hashlib, html, http.cookiejar, json, re, time
from pathlib import Path
import urllib.request, urllib.parse, urllib.error
from html.parser import HTMLParser
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/recommendation-evidence/kobis-expanded-20260913'
BASE = 'https://www.kobis.or.kr'
PERIOD = '/kobis/business/stat/boxs/findPeriodBoxOfficeList.do'
ANNUAL = '/kobis/business/stat/boxs/findYearlyBoxOfficeList.do'
END = date(2026, 9, 12)
HEADERS = ['순위','영화명','개봉일','매출액','매출액 점유율','누적매출액','관객수','누적관객수','스크린수','상영횟수','대표국적','국적','제작사','배급사','등급','장르','감독','배우']
AHEADERS = ['순위','영화명','개봉일','매출액','매출액 점유율','관객수','스크린수','상영횟수','대표국적','국적','배급사']

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p, x):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(x, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts=[]
    def handle_data(self,data): self.parts.append(data)
def clean(s):
    parser=TextParser();parser.feed(s);parser.close()
    return re.sub(r'\s+', ' ', ''.join(parser.parts)).strip()
def integer(s):
    s=s.replace(',', '')
    if not re.fullmatch(r'-?\d+', s): raise ValueError(f'Invalid integer {s!r}')
    return int(s)
def table(s):
    headers = [clean(x) for x in re.findall(r'<th\b[^>]*>(.*?)</th>',s,re.S)]
    rows = [[clean(x) for x in re.findall(r'<td\b[^>]*>(.*?)</td>',r,re.S)] for r in re.findall(r'<tr\b[^>]*>(.*?)</tr>',s,re.S)]
    return headers, rows

def fetch(path, fields, label):
    raw=OUT/'raw'/f'{label}.html'; meta=OUT/'raw'/f'{label}.json'
    if raw.exists() and meta.exists():
        m=json.loads(meta.read_text(encoding='utf-8'))
        assert m['sha256']==sha(raw) and m['query']==fields and m['url']==BASE+path
        return raw.read_bytes().decode('utf-8'),m
    if raw.exists() or meta.exists(): raise RuntimeError(f'Incomplete cache {label}')
    opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    headers={'User-Agent':'FEELM-Local-Research/1.0'}
    # Normal public page session and form token; no login or OpenAPI key.
    with opener.open(urllib.request.Request(BASE+path,headers=headers),timeout=45) as r:
        s=r.read(20_000_001).decode('utf-8')
    token=re.search(r'name="CSRFToken" value="([^"]+)"',s)
    if token is None: raise RuntimeError('Public form unavailable')
    time.sleep(0.5)
    req=urllib.request.Request(BASE+path,data=urllib.parse.urlencode({**fields,'CSRFToken':token[1]}).encode(),headers=headers)
    start=datetime.now(timezone.utc).isoformat()
    with opener.open(req,timeout=60) as r:
        body=r.read(30_000_001); status=r.status
    if len(body)>30_000_000: raise RuntimeError('Export exceeds bound')
    if status!=200: raise RuntimeError(f'Unexpected HTTP {status}')
    raw.parent.mkdir(parents=True,exist_ok=True);raw.write_bytes(body)
    m={'url':BASE+path,'query':fields,'started_at_utc':start,'fetched_at_utc':datetime.now(timezone.utc).isoformat(),'http_status':status,'sha256':sha(raw),'bytes':len(body)}
    dump(meta,m)
    time.sleep(0.5)
    return body.decode('utf-8'),m

def parse_export(s, annual=False):
    headers,blocks=table(s); expected=AHEADERS if annual else HEADERS
    assert [x.replace(' ','') for x in headers]==[x.replace(' ','') for x in expected], (headers,expected)
    rows=[x for x in blocks if len(x)==len(expected) and re.fullmatch(r'\d+',x[0])]
    totals=[x for x in blocks if x and x[0]=='합계']
    assert len(totals)==1 and rows
    count_i=5 if annual else 6
    last_count=None; last_rank=0
    for ordinal,x in enumerate(rows,1):
        count=integer(x[count_i])
        assert last_count is None or count<=last_count, 'Audience order changed'
        source_rank=integer(x[0])
        assert source_rank>=max(1,last_rank), 'Source rank order changed'
        # Historical exports contain gaps even when every official total matches.
        # Completeness is established by full-export equality, totals and count,
        # never by recomputing the source's rank convention.
        last_count=count; last_rank=source_rank
    # Signed adjustments are preserved, never silently clipped to zero.
    for i in [3,count_i]:
        assert sum(integer(x[i]) for x in rows)==integer(totals[0][i]), f'Total mismatch column {i}'
    return rows,totals[0]

def screen_rows(s):
    found=[]
    for block in re.findall(r'<tr\b[^>]*>(.*?)</tr>',s,re.S):
        code=re.search(r"mstView\('movie','([^']+)'\)",block)
        if not code:continue
        cells=[clean(x) for x in re.findall(r'<td\b[^>]*>(.*?)</td>',block,re.S)]
        assert len(cells)==10
        found.append({'rank':integer(cells[0]),'code':code[1],'title':cells[1],'open_date':cells[2],'annual_admissions':integer(cells[6]),'cumulative_admissions':integer(cells[7])})
    totals=re.findall(r'총\s*<em[^>]*>([^<]+)',s)
    assert totals and len(set(totals))==1
    return found,integer(totals[0])

def run_year(year):
    end=END if year==END.year else date(year,12,31)
    fields={'loadEnd':'0','searchType':'excel','sMultiMovieYn':'','sRepNationCd':'','sWideAreaCd':'','sSearchFrom':f'{year}-01-01','sSearchTo':end.isoformat()}
    ps,pm=fetch(PERIOD,fields,f'period-{year}-full')
    assert f'조회기간: {year}-01-01~{end.isoformat()} 영화구분: 전체 국적: 전체 지역: 전체' in clean(ps), 'Period query echo mismatch'
    pr,pt=parse_export(ps)
    af={'loadEnd':'0','searchType':'excel','sMultiMovieYn':'','sRepNationCd':'','sWideAreaCd':'','sSearchYearFrom':str(year)}
    ass,am=fetch(ANNUAL,af,f'annual-{year}-full'); ar,at=parse_export(ass,True)
    assert f'조회기간: {year} 영화구분: 전체 국적: 전체 지역: 전체' in clean(ass), 'Annual query echo mismatch'
    # Entire annual export cross-check, including tail rows and negative corrections.
    projection=lambda x:(x[1],x[2],integer(x[3]),integer(x[6]),x[10],x[11],x[13])
    aprojection=lambda x:(x[1],x[2],integer(x[3]),integer(x[5]),x[8],x[9],x[10])
    assert Counter(map(projection,pr))==Counter(map(aprojection,ar)), f'Annual/period mismatch {year}'
    ss,sm=fetch(PERIOD,{**fields,'searchType':'search'},f'period-{year}-screen')
    sr,total=screen_rows(ss)
    assert total==len(pr), (year,total,len(pr))
    assert len(sr)==min(100,len(pr)), 'Screen unexpectedly truncated'
    for screen,x in zip(sr,pr[:len(sr)]):
        assert re.fullmatch(r'[0-9A-Za-z]{8}',screen['code'])
        assert (screen['rank'],screen['title'],screen['open_date'],screen['annual_admissions'],screen['cumulative_admissions'])==(integer(x[0]),x[1],x[2],integer(x[6]),integer(x[7])), 'Screen/export order or identity mismatch'
    result=[]
    for ordinal,x in enumerate(pr,1):
        rank=integer(x[0]); code=sr[ordinal-1]['code'] if ordinal<=len(sr) else None
        result.append({'source_row_id':f'{year}:{ordinal}','screening_year':year,'period_start':fields['sSearchFrom'],'period_end':end.isoformat(),'is_partial_year':year==END.year,'rank':rank,'kobis_movie_code':code,'title':x[1],'open_date':x[2] or None,'annual_sales_krw':integer(x[3]),'annual_admissions':integer(x[6]),'cumulative_sales_krw_at_period_end':integer(x[5]),'cumulative_admissions_at_period_end':integer(x[7]),'screens':integer(x[8]),'shows':integer(x[9]),'representative_nation':x[10],'nations':x[11],'production_companies':x[12],'distributors':x[13],'grades':x[14],'genres':x[15],'directors':x[16],'actors':x[17],'has_negative_adjustment':any(integer(x[i])<0 for i in [3,5,6,7]),'source_url':pm['url'],'fetched_at_utc':pm['fetched_at_utc'],'raw_sha256':pm['sha256'],'cumulative_scope':'KOBIS_TICKET_SYSTEM_AS_OF_QUERY_END_NOT_FULL_LIFETIME'})
    checks={'year':year,'rows':len(pr),'displayed_total':total,'screen_codes':len(sr),'annual_admissions_total':integer(pt[6]),'annual_sales_total':integer(pt[3]),'annual_export_all_rows_equal':True,'negative_adjustment_rows':sum(r['has_negative_adjustment'] for r in result),'period_end':end.isoformat(),'raw':[pm,am,sm]}
    dump(OUT/'checks'/f'{year}.json',checks)
    pd.DataFrame(result).to_parquet(OUT/f'year-{year}.parquet',index=False)
    print(json.dumps({k:v for k,v in checks.items() if k!='raw'},ensure_ascii=False),flush=True)
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');p.add_argument('--start-year',type=int,default=2004);p.add_argument('--end-year',type=int,default=2026);args=p.parse_args()
    assert args.execute and 2004<=args.start_year<=args.end_year<=2026
    rows=[]
    for year in range(args.start_year,args.end_year+1):rows.extend(run_year(year))
    frame=pd.DataFrame(rows);assert frame.source_row_id.is_unique
    frame.to_parquet(OUT/'annual-observations.parquet',index=False)
    frame.to_csv(OUT/'annual-observations.csv',index=False,encoding='utf-8-sig')
    dump(OUT/'collection-summary.json',{'status':'COLLECTED_PENDING_IDENTITY_LINK_AND_AUDIT','years':[args.start_year,args.end_year],'period_end':END.isoformat(),'rows':len(rows),'direct_code_rows':int(frame.kobis_movie_code.notna().sum()),'direct_unique_codes':int(frame.kobis_movie_code.nunique()),'negative_adjustment_rows':int(frame.has_negative_adjustment.sum()),'script_sha256':sha(Path(__file__)),'new_model_training':False})

if __name__=='__main__':main()
