#!/usr/bin/env python3
"""Reproduce economic/count evidence and all 11 reports from packaged frozen data.

Usage: python reproduce_package.py --output-dir /tmp/power-creep-paper-reproduced
No LLM, ingestion, or source-download service is required.
"""
from pathlib import Path
import argparse,json,sys,csv,statistics,subprocess,tempfile
from collections import Counter
import duckdb
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'paper/submission/supplement'
sys.path.insert(0,str(ROOT/'scripts'))
import analysis as a
import detect as d

def main():
 global BASE
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output-dir',type=Path,required=True);p.add_argument('--supplement-dir',type=Path,default=BASE);args=p.parse_args()
 BASE=args.supplement_dir
 args.output_dir.mkdir(parents=True,exist_ok=True)
 r=a.read_positive_records(BASE/'frozen_data/powercreep_results_reviewed.jsonl')
 pairs=a.unique_pairs(r);different=[x for x in pairs if a.is_different_card(x)]
 included=[(x,a.conversion_cost(x)[0]) for x in different if a.conversion_cost(x)[0] is not None]
 expected=json.loads((BASE/'results_summary.json').read_text())
 assert len(r)==expected['retained_records'] and len(pairs)==expected['identity_pairs'] and len(different)==expected['different_card_pairs']
 assert dict(Counter(x['comparison_type'] for x in r))==expected['retained_categories']
 assert len(included)==expected['conversion_included']
 values={
  'nominal_price_difference':[a.DUST_COST[x['card_b']['rarity']]-a.DUST_COST[x['card_a']['rarity']] for x,c in included],
  'candidate_craft':[c.candidate_craft for x,c in included],
  'additional_dust':[c.additional_dust for x,c in included],
  'net_dust':[c.net_dust for x,c in included],
  'surplus_dust':[max(0,-c.net_dust) for x,c in included]}
 for key,v in values.items():
  e=expected['matched_sample'][key]
  assert {str(k):n for k,n in Counter(v).items()}==e['distribution']
  assert statistics.median(v)==e['median']
  assert abs(statistics.mean(v)-e['mean'])<1e-9
 with tempfile.TemporaryDirectory() as tmp:
  db=Path(tmp)/'catalogue.duckdb';con=duckdb.connect(str(db))
  con.execute('CREATE TABLE cards(patch_id TEXT,name TEXT,type TEXT,text TEXT,health INTEGER,attack INTEGER,mana INTEGER,armor INTEGER,card_set TEXT,class TEXT,rarity TEXT,tribe TEXT)')
  cols=[x[0] for x in con.execute('SELECT * FROM cards').description]
  states=[json.loads(x) for x in (BASE/'frozen_data/catalogue_retained_states.jsonl').read_text().splitlines()]
  con.executemany('INSERT INTO cards VALUES('+','.join('?' for c in cols)+')',[[s.get(k) for k in cols] for s in states])
  counts=con.execute('SELECT count(*),count(DISTINCT name),count(DISTINCT patch_id) FROM cards').fetchone()
  assert counts==(7760,6010,168)
  query=json.loads((BASE/'run_manifest.json').read_text())['candidate_SQL'];assert con.execute(query).fetchone()[0]==368528
  con.close()
  subprocess.run([sys.executable,str(ROOT/'scripts/analysis.py'),'--input',str(BASE/'frozen_data/powercreep_results_reviewed.jsonl'),'--database',str(db),'--timeline',str(BASE/'frozen_data/hearthstone_patches_and_expansions_by_year.md'),'--output-dir',str(args.output_dir/'reports')],check=True)
 (args.output_dir/'verification.json').write_text(json.dumps({'status':'PASS','retained_records':len(r),'identity_pairs':len(pairs),'different_card_pairs':len(different),'included_conversion_pairs':len(included),'candidate_pairs':368528,'matched_distributions':'exactly reproduced'},indent=2)+'\n')
 print('Verified frozen corpus, counts, candidate query, matched costs; regenerated 11 reports.')
if __name__=='__main__':main()
