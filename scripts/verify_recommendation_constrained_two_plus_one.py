from __future__ import annotations
import argparse,hashlib,json
from pathlib import Path
from recommendation_exploration_full_catalog import canonical_bytes,sha256
from recommendation_evidence_paths import artifact_matches, repository_path
def main():
 p=argparse.ArgumentParser();p.add_argument('--manifest',type=Path,required=True);a=p.parse_args();m=json.loads(a.manifest.read_text())
 assert m['evidence_id']=='REC-EV-013' and m['two_plus_one'] is None and m['discovery_policy'] is None and m['product_approved'] is False
 for r in m['artifacts'].values():q=repository_path(r['path']);assert artifact_matches(q,r)
 lock=json.loads(repository_path(m['artifacts']['lock']['path']).read_text());assert lock['protocol_hash']==hashlib.sha256(canonical_bytes(lock['protocol'])).hexdigest();assert lock['selected'] is None
 result=json.loads(repository_path(m['artifacts']['result']['path']).read_text());assert result['selected'] is None and result['positive_injection'] is False;assert result['paired_ci']['status']=='DIAGNOSTIC_FAILED_SELECTION_BUDGET'
 assert '"user_id"' not in json.dumps(result) and '"movie_id"' not in json.dumps(result)
 print(json.dumps({'status':'PASS','evidence_id':'REC-EV-013','two_plus_one':None}))
if __name__=='__main__':main()
