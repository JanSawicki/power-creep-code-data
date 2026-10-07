#!/usr/bin/env python3
"""Recover card IDs from local hsdata builds and audit regular-copy wiki availability.

No LLM calls. Wiki pages are cached with revision URLs and hashes. Current wiki
availability is not a reconstruction of historical ownership or refund windows.
"""
import argparse,csv,hashlib,json,re,subprocess,sys,time
from collections import defaultdict,Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import quote
import requests
from bs4 import BeautifulSoup
from lxml import etree
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import ingest_hsdata as ingest


def write_json(path,data):path.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')
def key(row,side):return tuple(row[side+'_'+f] for f in ['name','patch_id','card_set','class','type','rarity','mana','attack','health','tribe','text'])
def local(build,targets,commits):
 commit=commits.get(build)
 if not commit:return build,[],{'status':'BUILD_NOT_FOUND'}
 xml=ingest.get_xml_bytes(commit);root=etree.fromstring(xml);out=[];tag_names=Counter()
 target_names={t[0] for t in targets}
 for e in root.iter('Entity'):
  name=e.findtext("Tag[@name='CARDNAME']/enUS")
  if name not in target_names:continue
  tags={}
  for t in e.findall('Tag'):
   n=t.get('name');tag_names[n]+=1
   if t.get('type')=='LocString':tags[n]=t.findtext('enUS')
   elif t.get('type')=='Int':tags[n]=int(t.get('value'))
   else:tags[n]=t.get('value',t.text)
  if tags.get('COLLECTIBLE')!=1:continue
  def x(mapping,k):v=tags.get(k);return mapping.get(v,str(v)) if v is not None else ''
  text=tags.get('CARDTEXT') or tags.get('CARDTEXT_INHAND') or ''
  text=ingest.clean_card_text(ingest.expand_script_data_placeholders(text,tags,e.get('CardID'),build).replace('\n',' '))
  k=(name,build,x(ingest.CARD_SET,'CARD_SET'),x(ingest.CARD_CLASS,'CLASS'),x(ingest.CARD_TYPE,'CARDTYPE'),x(ingest.RARITY,'RARITY'),str(tags.get('COST',0)),str(tags.get('ATK','')),str(tags.get('HEALTH','')),x(ingest.RACE,'CARDRACE'),text or '')
  if k in targets:
   out.append({'key':k,'card_id':e.get('CardID'),'set_id':tags.get('CARD_SET'),'source_commit':commit,'observed_build':build,'collection_related_card_database_id':tags.get('COLLECTION_RELATED_CARD_DATABASE_ID'),'acquisition_tags':{n:v for n,v in tags.items() if any(t in n for t in ['CRAFTABLE','DISENCHANTABLE','DUST_COST','DUST_VALUE'])}})
 return build,out,{'commit':commit,'source_xml_sha256':hashlib.sha256(xml).hexdigest(),'direct_acquisition_tag_names':[n for n in tag_names if any(t in n for t in ['CRAFTABLE','DISENCHANTABLE','DUST_COST','DUST_VALUE'])]}


def wiki(slug,cache):
 url='https://hearthstone.wiki.gg/wiki/'+quote(slug,safe='_-()')
 path=cache/(hashlib.sha256(url.encode()).hexdigest()+'.html')
 try:
  if path.exists():raw=path.read_bytes()
  else:
   time.sleep(1.6)
   r=requests.get(url,timeout=40)
   if r.status_code==429:
    time.sleep(min(45,int(r.headers.get('Retry-After','30'))));r=requests.get(url,timeout=40)
   r.raise_for_status();raw=r.content;path.write_bytes(raw)
  s=BeautifulSoup(raw,'html.parser');c=s.select_one('.mw-parser-output')
  if c is None:raise ValueError('Missing wiki article body')
  text=c.get_text(' ',strip=True)
  revision=next((a['href'] for a in s.select('a[href]') if '?oldid=' in a['href'] and slug in a['href']),None)
  # Capture only the actual acquisition section, never TOC or previous availability.
  h=s.find(id='How_to_get');section=''
  if h:
   parent=h if h.name in ['h2','h3'] else h.parent
   parts=[]
   for sibling in parent.next_siblings:
    if getattr(sibling,'name',None) in ['h2','h3']:break
    if hasattr(sibling,'get_text'):parts.append(sibling.get_text(' ',strip=True))
   section=' '.join(parts)
  return slug,{'url':url,'revision_url':revision,'html_sha256':hashlib.sha256(raw).hexdigest(),'cache_file':str(path.name),'article_text':text,'how_to_get':section,'links':[(a.get('href'),a.get_text(' ',strip=True)) for a in c.select('a[href]')],'status':'FETCHED'}
 except Exception as exc:return slug,{'url':url,'status':'UNAVAILABLE','error':str(exc)}


def main():
 p=argparse.ArgumentParser();p.add_argument('--output-dir',type=Path,default=ROOT/'paper/submission/supplement/acquisition_audit');a=p.parse_args();out=a.output_dir;out.mkdir(parents=True,exist_ok=True);cache=ROOT/'analysis/acquisition_audit/wiki_pages';cache.mkdir(parents=True,exist_ok=True)
 q=list(csv.DictReader((ROOT/'paper/submission/supplement/human_review/pairs.csv').open()));targets=defaultdict(set)
 for r in q:
  for side in ['baseline','candidate']:k=key(r,side);targets[k[1]].add(k)
 commits={v:c for c,v in ingest.get_patch_commits()};matches=defaultdict(list);builds={}
 if (out/'card_audit.json').exists() and (out/'source_builds.json').exists():
  builds=json.loads((out/'source_builds.json').read_text())
  for card in json.loads((out/'card_audit.json').read_text()):
   for state in card['source_matches']:matches[tuple(state['key'])].append(state)
 else:
  with ThreadPoolExecutor(2) as pool:
   for build,states,meta in pool.map(lambda b:local(b,targets[b],commits),targets):
    builds[build]=meta
    for state in states:matches[tuple(state['key'])].append(state)
 slugs=set()
 for k,states in matches.items():
  slug=k[0].replace(' ','_')
  if all(state['card_id'].upper().startswith('CORE_') for state in states):slug+='_(Core)'
  elif all(state['card_id'].upper().startswith('VAN_') for state in states):slug+='_(Classic)'
  slugs.add(slug)
  if k[0]=='Hallucination' and all(state['card_id']=='UNG_856' for state in states):slugs.add('Spore_Hallucination')
 slugs.update(['Core_reserve','Core','Crafting','Legacy','Event_set'])
 print(f'Recovered {len(matches)} distinct represented states; checking {len(slugs)} wiki pages.',flush=True)
 pages={}
 with ThreadPoolExecutor(1) as pool:
  for i,(slug,data) in enumerate(pool.map(lambda slug:wiki(slug,cache),sorted(slugs)),1):
   pages[slug]=data
   if i%20==0:print(f'Wiki pages checked: {i}/{len(slugs)}',flush=True)
 # Save source excerpts separately from derived classifications.
 write_json(out/'wiki_evidence.json',{k:{**{f:v for f,v in d.items() if f not in ['links','article_text']},'article_excerpt':d.get('article_text','')[:1800]} for k,d in pages.items()})
 cards=[]
 for k in sorted({key(r,side) for r in q for side in ['baseline','candidate']}):
  states=matches.get(k,[]);name,build,setname,cl,typ,rarity,mana,attack,health,tribe,text=k
  card={'name':name,'observed_build':build,'card_set':setname,'rarity':rarity,'source_matches':states,'source_card_ids':[s['card_id'] for s in states],'status':'UNRESOLVED','craft_regular':None,'disenchant_regular':None,'ordinary_craft_dust':None,'ordinary_recovery_dust':None,'evidence_url':None,'evidence_excerpt':None,'rule_sources':['https://hearthstone.wiki.gg/wiki/Crafting','https://hearthstone.wiki.gg/wiki/Core','https://news.blizzard.com/en-us/article/19995505/a-new-way-to-play'],'time_scope':'Wiki availability at audit retrieval; exact historical transaction eligibility not established.'}
  if not states:card['reason']='No exact source-state match'
  else:
   slug=name.replace(' ','_');ids=card['source_card_ids']
   if all(i.upper().startswith('CORE_') for i in ids):slug+='_(Core)'
   elif all(i.upper().startswith('VAN_') for i in ids):slug+='_(Classic)'
   if name=='Hallucination' and ids==['UNG_856']:slug='Spore_Hallucination'
   page=pages.get(slug,{})
   card['evidence_url']=page.get('revision_url') or page.get('url');card['wiki_slug']=slug
   body=page.get('article_text','');section=page.get('how_to_get','')
   # Exact card ID must be exposed by the selected page (no name-only substitution).
   idmatch=all(re.search(r'(?<![A-Za-z0-9_])'+re.escape(i)+r'(?![A-Za-z0-9_])',body,re.I) for i in ids)
   card['id_matching_note']='Exact ID token; historical Core_ versus CORE_ casing ignored. UNG_856 renamed Hallucination to Spore Hallucination, explicitly documented by wiki.'
   card['wiki_card_id_match']=idmatch
   craft=re.search(r'Craft a Regular copy for ([\d,]+)',section)
   if page.get('status')!='FETCHED':card['reason']='Wiki page unavailable'
   elif not idmatch:card['reason']='Wiki page does not identify every matched source card ID'
   elif all(i.upper().startswith('CORE_') for i in ids):
    card.update(status='CORE_OR_RESERVE_NO_DUST',craft_regular=False,disenchant_regular=False,ordinary_recovery_dust=0,evidence_excerpt=body[:1300],reason='Core-copy rules; reserve access separately excluded from owned-baseline scenario')
    card['reserve_at_observed_build']=all(s['set_id']==1810 for s in states)
   elif re.search(r'(?:Regular(?: and [A-Za-z]+)* cop(?:y|ies)|Regular version)[^.]{0,180}uncraftable',section,re.I) and not craft:
    if rarity=='FREE':
     card.update(status='REGULAR_UNCRAFTABLE',craft_regular=False,disenchant_regular=False,ordinary_recovery_dust=0,evidence_excerpt=section,reason='Free/Basic-card rule: no ordinary crafting or dust recovery')
    else:
     card.update(status='REGULAR_UNCRAFTABLE_RECOVERY_UNRESOLVED',craft_regular=False,disenchant_regular=None,ordinary_recovery_dust=None,evidence_excerpt=section,reason='Normal-copy uncraftability established; disenchanting eligibility not explicitly established by this entry')
   elif craft:
    cost=int(craft.group(1).replace(',',''));expected={'COMMON':40,'RARE':100,'EPIC':400,'LEGENDARY':1600}.get(rarity)
    if cost==expected:
     card.update(status='REGULAR_CRAFTABLE',craft_regular=True,disenchant_regular=True,ordinary_craft_dust=cost,ordinary_recovery_dust={'COMMON':5,'RARE':20,'EPIC':100,'LEGENDARY':400}[rarity],evidence_excerpt=section,reason='Explicit regular crafting availability; ordinary recovery follows general crafting rules, outside refund windows')
    else:card['reason']=f'Wiki crafting cost {cost} conflicts with retained rarity {rarity}'
   else:card['reason']='No explicit current regular crafting or uncraftability statement'
  cards.append(card)
 bykey={k:c for k,c in zip(sorted({key(r,s) for r in q for s in ['baseline','candidate']}),cards)}
 pairs=[]
 for row in q:
  ca,cb=(bykey[key(row,s)] for s in ['baseline','candidate']);status='UNRESOLVED';reason='Unresolved card-level acquisition evidence'
  if cb['craft_regular'] is False:status='CANDIDATE_NOT_CRAFTABLE';reason='Exact candidate version has no ordinary crafting route'
  elif ca.get('reserve_at_observed_build'):status='BASELINE_CORE_RESERVE';reason='Retained baseline is a reserve Core version; owned-baseline scenario not established'
  elif cb['craft_regular'] is True and ca['disenchant_regular'] is not None:status='VERIFIED_ORDINARY_RULES';reason='Availability verified from matching wiki card IDs; historical holdings not reconstructed'
  pairs.append({'pair_id':row['pair_id'],'baseline_card_ids':ca['source_card_ids'],'candidate_card_ids':cb['source_card_ids'],'status':status,'reason':reason,'baseline_status':ca['status'],'candidate_status':cb['status'],'candidate_craft':cb['ordinary_craft_dust'],'baseline_recovery':ca['ordinary_recovery_dust'],'baseline_source':ca['evidence_url'],'candidate_source':cb['evidence_url']})
 # Source-audited arithmetic is separate from frozen historical eligibility decisions.
 accepted=set()
 decision_path=ROOT/'paper/submission/supplement/human_review/decisions.jsonl'
 if decision_path.exists():
  latest={}
  for line in decision_path.read_text().splitlines():
   d=json.loads(line);latest[d['pair_id']]=d['decision']
  accepted={pid for pid,d in latest.items() if d=='YES'}
 cost_rows=[]
 rank_cost={'FREE':0,'COMMON':40,'RARE':100,'EPIC':400,'LEGENDARY':1600}
 bypair={row['pair_id']:row for row in q}
 for pair in pairs:
  if pair['status']!='VERIFIED_ORDINARY_RULES':continue
  row=bypair[pair['pair_id']];craft=pair['candidate_craft'];recovery=pair['baseline_recovery']
  cost_rows.append({'pair_id':pair['pair_id'],'human_accepted':pair['pair_id'] in accepted,'baseline':row['baseline_name'],'candidate':row['candidate_name'],'baseline_rarity':row['baseline_rarity'],'candidate_rarity':row['candidate_rarity'],'candidate_craft':craft,'baseline_recovery':recovery,'nominal_price_difference':craft-rank_cost[row['baseline_rarity']],'additional_dust':max(0,craft-recovery),'net_dust':craft-recovery})
 import statistics
 def stats(rows):
  return {'n':len(rows),'quantities':{k:{'median':statistics.median(r[k] for r in rows),'mean':statistics.mean(r[k] for r in rows)} for k in ['nominal_price_difference','candidate_craft','additional_dust']}}
 write_json(out/'audited_cost_summary.json',{'detector':stats(cost_rows),'human_accepted':stats([r for r in cost_rows if r['human_accepted']]),'scope':'Source-audited normal-copy ordinary rules; no historical holdings or refund reconstruction.'})
 for filename,rows in [('pair_audit.csv',[{**r,'baseline_card_ids':';'.join(r['baseline_card_ids']),'candidate_card_ids':';'.join(r['candidate_card_ids'])} for r in pairs]),('audited_conversion_pairs.csv',cost_rows)]:
  with (out/filename).open('w',newline='') as f:
   w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 write_json(out/'card_audit.json',cards);write_json(out/'pair_audit.json',pairs);write_json(out/'source_builds.json',builds)
 write_json(out/'summary.json',{'audited_at':datetime.now(timezone.utc).isoformat(),'pairs':len(pairs),'human_accepted_pairs':len(accepted),'accepted_pair_status':dict(Counter(p['status'] for p in pairs if p['pair_id'] in accepted)),'unique_card_states':len(cards),'exact_source_states_recovered':len(matches),'wiki_pages':len(pages),'wiki_fetch_status':dict(Counter(p['status'] for p in pages.values())),'card_status':dict(Counter(c['status'] for c in cards)),'pair_status':dict(Counter(p['status'] for p in pairs)),'direct_craftability_tags_found':any(b.get('direct_acquisition_tag_names') for b in builds.values()),'limitations':['hsdata exposes IDs, set membership and collectible flags, not direct crafting/disenchanting flags for these states.','Wiki evidence describes availability at retrieval, not every retained historical build.','Core reserve versions are not substituted with same-name owned originals.','Normal-copy crafting and ordinary recovery only; no refund windows or actual holdings.']})
 print(json.dumps(json.loads((out/'summary.json').read_text()),indent=2))

if __name__=='__main__':main()
