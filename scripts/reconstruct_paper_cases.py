#!/usr/bin/env python3
"""Recover printing-aware case state transitions from the existing hsdata checkout.

Source appearances are build observations, never silently labeled release dates.
"""
from pathlib import Path
import sys, subprocess, json, csv
from concurrent.futures import ThreadPoolExecutor
from lxml import etree
REPO=Path(__file__).resolve().parents[1];sys.path.insert(0,str(REPO/'scripts'))
import ingest_hsdata as ingest
NAMES={'Tyrantus','The Demon Seed','Establish the Link','Complete the Ritual','Blightborn Tamsin','Big Game Hunter','Evasive Wyrm','Snowchugger','Chill-o-matic','Spellbreaker','Royal Librarian'}
def read(patch):
 commit,version=patch;xml=ingest.get_xml_bytes(commit)
 if not xml:return []
 root=etree.fromstring(xml);rows=[]
 for e in root.iter('Entity'):
  name=e.find("Tag[@name='CARDNAME']/enUS")
  if name is None or name.text not in NAMES:continue
  tags={}
  for t in e.findall('Tag'):
   tags[t.get('name')]=t.findtext('enUS') if t.get('type')=='LocString' else t.get('value',t.text)
  rows.append({'name':name.text,'card_id':e.get('CardID'),'source_commit':commit,'observed_build':version,'mana':tags.get('COST','0'),'attack':tags.get('ATK'),'health':tags.get('HEALTH'),'set_id':tags.get('CARD_SET'),'rarity_id':tags.get('RARITY'),'collectible':tags.get('COLLECTIBLE'),'text':tags.get('CARDTEXT',tags.get('CARDTEXT_INHAND'))})
 return rows
if __name__=='__main__':
 patches=ingest.get_patch_commits();last={};changes=[]
 for rows in ThreadPoolExecutor(4).map(read,patches):
  for r in rows:
   state=tuple(r[k] for k in ['mana','attack','health','set_id','rarity_id','collectible','text'])
   key=r['card_id']
   if last.get(key)!=state:changes.append(r);last[key]=state
 out=REPO/'paper/submission/supplement/source_case_transitions.csv'
 with out.open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(changes[0]));w.writeheader();w.writerows(changes)
 print(f'{len(patches)} source commits; {len(changes)} printing-level state transitions')
