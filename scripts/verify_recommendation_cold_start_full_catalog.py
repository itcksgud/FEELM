from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
from recommendation_exploration_full_catalog import canonical_bytes, sha256
from recommendation_evidence_paths import repository_path

def main() -> None:
    p=argparse.ArgumentParser(); p.add_argument("--manifest",type=Path,required=True); a=p.parse_args()
    m=json.loads(a.manifest.read_text(encoding="utf-8")); assert m["evidence_id"]=="REC-EV-011"
    assert m["protocol"]["candidate_generation"]["positive_injection"] is False
    assert m["validation"]=={"positive_injection":False,"raw_ids_tracked":False,"selection_lock_verified_before_evaluation":True,"status":"PASS"}
    for r in m["artifacts"].values():
        q=repository_path(r["path"]); assert q.is_file() and q.stat().st_size==r["bytes"] and sha256(q)==r["sha256"]
    lock=json.loads(repository_path(m["artifacts"]["protocol_lock"]["path"]).read_text(encoding="utf-8"))
    assert lock["protocol_hash"]==hashlib.sha256(canonical_bytes(lock["protocol"])).hexdigest()
    result=json.loads(repository_path(m["artifacts"]["evaluation_result"]["path"]).read_text(encoding="utf-8"))
    assert result["coverage"]["train_known_movies"]==50977 and result["coverage"]["positive_injection"] is False
    assert result["conclusion"]["expected_star_approved"] is False and result["conclusion"]["public_ui_approved"] is False
    assert result["conclusion"]["personal_ranking_champion"] is None
    assert '"user_id"' not in json.dumps(result) and '"movie_id"' not in json.dumps(result)
    print(json.dumps({"status":"PASS","evidence_id":"REC-EV-011","selected_alpha":m["selected_alpha"]}))
if __name__=="__main__": main()
