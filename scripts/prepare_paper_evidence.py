#!/usr/bin/env python3
"""Prepare manuscript evidence after author-approved comparison exclusions."""
from pathlib import Path
import sys, json, hashlib, csv, statistics, subprocess
from collections import Counter
import duckdb
REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'scripts'))
import analysis as a
import detect as d
OUT=REPO/'paper/submission/supplement'
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def csvwrite(name,rows):
 with (OUT/name).open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def main():
 OUT.mkdir(parents=True,exist_ok=True)
 initial=a.read_positive_records(REPO/'data/powercreep_results.jsonl')
 retained=a.read_positive_records(REPO/'data/powercreep_results_reviewed.jsonl')
 pairs=a.unique_pairs(retained); different=[r for r in pairs if a.is_different_card(r)]
 con=duckdb.connect(str(REPO/'data/powercreep.duckdb'),read_only=True)
 corpus=con.execute('SELECT count(*),count(DISTINCT name),count(DISTINCT patch_id) FROM cards').fetchone()
 query=f'''SELECT count(*) FROM cards a JOIN cards b ON b.type=a.type
 AND {d.class_compatible_sql(False)}
 AND ((a.rarity='LEGENDARY')=(b.rarity='LEGENDARY'))
 AND ABS(COALESCE(b.mana,0)-COALESCE(a.mana,0))<=2
 AND {d.STAT_DOMINANCE_SQL} AND {d.TRIBE_DIRECTION_COMPATIBLE_SQL}
 AND (b.name>a.name OR (b.name=a.name AND b.patch_id>a.patch_id))'''
 candidates=con.execute(query).fetchone()[0]
 paths=[REPO/'data/powercreep.duckdb',REPO/'data/powercreep_results.jsonl',REPO/'data/powercreep_results_reviewed.jsonl']
 paths.append(REPO/'data/excluded_comparisons.json')
 paths+=list((REPO/'scripts').glob('*.py'))
 paths+=[REPO/'logs/detect.log',REPO/'logs/ingest.log',REPO/'logs/test_llm_judge.log']
 existing_queue=list(csv.DictReader((OUT/'annotation_queue.csv').open())) if (OUT/'annotation_queue.csv').exists() else []
 pair_ids={}
 for r in existing_queue:
  pair_ids[tuple(tuple(a.normalise(r[p+'_'+k]) for k in ['name','card_set','class','type']) for p in ['baseline','candidate'])]=r['pair_id']
 next_id=max([int(x[1:]) for x in pair_ids.values()]+[0])+1
 costs=[];excluded=[];queue=[];shortcuts=[]
 for i,r in enumerate(different,1):
  aa,bb=r['card_a'],r['card_b'];key=(a.card_identity(aa),a.card_identity(bb));pid=pair_ids.get(key)
  if pid is None:pid=f'P{next_id:03d}';next_id+=1
  row={'pair_id':pid,'category':r['comparison_type']}
  for prefix,card in [('baseline',aa),('candidate',bb)]:
   for key in ['name','patch_id','card_set','class','type','rarity','mana','attack','health','tribe','text']:row[prefix+'_'+key]=card.get(key)
  row.update(semantic_decision='NOT_ANNOTATED',chronology_decision='NOT_VERIFIED',annotator='',rationale='')
  queue.append(row)
  cost,status=a.conversion_cost(r)
  economic={'pair_id':pid,'baseline':a.short_card(aa),'candidate':a.short_card(bb),'category':r['comparison_type'],'baseline_rarity':aa['rarity'],'candidate_rarity':bb['rarity'],'status':status}
  if cost:
   economic.update(baseline_treatment=cost.baseline_treatment,baseline_recovery=cost.baseline_recovery,candidate_craft=cost.candidate_craft,nominal_price_difference=a.DUST_COST[bb['rarity']]-a.DUST_COST[aa['rarity']],net_dust=cost.net_dust,additional_dust=cost.additional_dust,surplus_dust=max(0,-cost.net_dust))
   costs.append(economic)
  else:excluded.append(economic)
 for stage,records in [('initial',initial),('retained',retained)]:
  for line,r in enumerate(records,1):
   if (r.get('llm_response') or {}).get('detector')=='deterministic_strict_stat_and_effect_superset':
    shortcuts.append({'stage':stage,'line':line,'baseline':r['card_a']['name'],'candidate':r['card_b']['name'],'baseline_text':r['card_a']['text'],'candidate_text':r['card_b']['text'],'audit_status':'NOT_HUMAN_VALIDATED'})
 csvwrite('annotation_queue.csv',queue);csvwrite('conversion_pairs.csv',costs);csvwrite('cost_exclusions.csv',excluded)
 if shortcuts:csvwrite('shortcut_audit_queue.csv',shortcuts)
 (OUT/'different_card_pairs.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in different))
 summary={
 'scope':'Detector outputs after author-approved incomplete-effect exclusions; no independent human annotation obtained',
 'corpus':dict(zip(['retained_rows','names','represented_patches'],corpus)),
 'candidate_pairs_reconstructed_with_current_code':candidates,
 'initial_records':len(initial),'retained_records':len(retained),'removed_records':len(initial)-len(retained),
 'identity_pairs':len(pairs),'different_card_pairs':len(different),
 'initial_categories':dict(Counter(r['comparison_type'] for r in initial)),
 'retained_categories':dict(Counter(r['comparison_type'] for r in retained)),
 'identity_pair_categories':dict(Counter(r['comparison_type'] for r in pairs)),
 'different_pair_categories':dict(Counter(r['comparison_type'] for r in different)),
 'rarity_transitions':dict(Counter('increased' if a.RARITY_ORDER[r['card_b']['rarity']]>a.RARITY_ORDER[r['card_a']['rarity']] else 'decreased' if a.RARITY_ORDER[r['card_b']['rarity']]<a.RARITY_ORDER[r['card_a']['rarity']] else 'unchanged' for r in different)),
 'conversion_included':len(costs),'cost_exclusions':dict(Counter(r['status'] for r in excluded)),
 'matched_sample':{key:{'n':len(costs),'mean':statistics.mean(r[key] for r in costs),'median':statistics.median(r[key] for r in costs),'min':min(r[key] for r in costs),'max':max(r[key] for r in costs),'distribution':dict(sorted(Counter(r[key] for r in costs).items()))} for key in ['nominal_price_difference','candidate_craft','additional_dust','net_dust','surplus_dust']},
 'included_rarity_transitions':dict(Counter('increased' if a.RARITY_ORDER[r['candidate_rarity']]>a.RARITY_ORDER[r['baseline_rarity']] else 'decreased' if a.RARITY_ORDER[r['candidate_rarity']]<a.RARITY_ORDER[r['baseline_rarity']] else 'unchanged' for r in costs)),
 'baseline_treatments':dict(Counter(r['baseline_treatment'] for r in costs)),
 'shortcut_records':dict(Counter(r['stage'] for r in shortcuts)),
 'retained_types':dict(Counter(r['card_a']['type'] for r in retained))}
 (OUT/'results_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
 initial_serial={json.dumps(r,sort_keys=True) for r in initial}
 retained_is_subset=all(json.dumps(r,sort_keys=True) in initial_serial for r in retained)
 manifest={'prepared':'2026-10-03','files':{str(p.relative_to(REPO)):sha(p) for p in paths},'hsdata_current_checkout':subprocess.check_output(['git','-C',str(REPO/'data/hsdata'),'rev-parse','HEAD'],text=True).strip(),'source_revision_at_ingestion':'NOT_RECORDED; current checkout is a recovery candidate, not a verified run-time revision','script_versions_at_run':'NOT_ARCHIVED; hashes describe current files','run_dates':'2026-10-02 12:26:16 to 2026-10-03 07:46:41 CEST (detect.log)','model':'deepseek-ai/DeepSeek-R1-Distill-Llama-70B','mana_window':2,'workers':32,'temperature_current_code':0,'max_tokens_current_code':[1024,1536],'model_weights_revision':'NOT_RECORDED','review_command_and_dates':'NOT_RECOVERED','parse_failure_counts':'NOT_RECOVERED; current detector maps exhausted JSON retries to NEGATIVE; reviewer treats failures as NO','retained_records_are_unchanged_initial_subset':retained_is_subset,'manual_curation':'NOT_ESTABLISHED','candidate_SQL':query,'reproduction_command':'.venv/bin/python scripts/prepare_paper_evidence.py'}
 (OUT/'run_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
 assert corpus==(7760,6010,168)
 assert candidates==368528
 assert len(different)==len(costs)+len(excluded)
 assert len({r['pair_id'] for r in queue})==len(queue)
 assert retained_is_subset

 print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
