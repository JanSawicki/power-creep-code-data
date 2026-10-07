#!/usr/bin/env python3
"""Download original-resolution wiki.gg renders for every named manuscript card."""
from pathlib import Path
import json,hashlib
from datetime import date
from urllib.parse import quote
import requests
from PIL import Image
REPO=Path(__file__).resolve().parents[1]
OUT=REPO/'paper/submission/figures/cards'
CARDS=[
 ('Magma Rager','CS2_118','magma_rager'),
 ('Ice Rager','AT_092','ice_rager'),
 ('Tyrantus','UNG_852','tyrantus'),
 ('The Demon Seed','SW_091','the_demon_seed'),
 ('Big Game Hunter','EX1_005','big_game_hunter'),
 ('Evasive Wyrm','DRG_079','evasive_wyrm'),
 ('Snowchugger','GVG_002','snowchugger'),
 ('Chill-o-matic','TTN_077','chill_o_matic'),
 ('Blightborn Tamsin','SW_091t4','blightborn_tamsin'),
]
BASE='https://hearthstone.wiki.gg'
def main():
 OUT.mkdir(parents=True,exist_ok=True);session=requests.Session();manifest=[]
 previous_path=OUT/'image_manifest.json'
 previous={x['card_id']:x for x in json.loads(previous_path.read_text())} if previous_path.exists() else {}
 for name,card_id,stem in CARDS:
  page=BASE+'/wiki/'+quote(name.replace(' ','_'),safe='-()_')
  filepage=BASE+'/wiki/File:'+card_id+'.png'
  url=BASE+'/images/'+card_id+'.png'
  path=OUT/(stem+'.png')
  if path.exists():
   content=path.read_bytes()
  else:
   result=session.get(url,timeout=45);result.raise_for_status();content=result.content;path.write_bytes(content)
  with Image.open(path) as im:
   im.verify()
  with Image.open(path) as im:w,h=im.size
  assert w>=500 and h>=650,(name,w,h)
  manifest.append({'card_name':name,'card_id':card_id,'wiki_page':page,'wiki_file_page':filepage,'original_image_url':url,'local_path':'figures/cards/'+path.name,'width_px':w,'height_px':h,'retrieved':previous.get(card_id,{}).get('retrieved',date.today().isoformat()),'sha256':hashlib.sha256(content).hexdigest(),'display_width_in':(2.9 if name != 'Blightborn Tamsin' else 3),'effective_dpi':w/(2.9 if name != 'Blightborn Tamsin' else 3),'rights':'Hearthstone card artwork and card render copyright Blizzard Entertainment; sourced via Hearthstone Wiki (wiki.gg). No open-image license or publication permission is asserted.'})
  for key in ['artist','artist_source','artist_corroboration','copyright_holder']:
   if key in previous.get(card_id,{}):manifest[-1][key]=previous[card_id][key]
  print(f'{name}: original {w} × {h} px → {path.name}')
 historical_path=OUT/'historical_image_manifest.json'
 if historical_path.exists():
  for item in json.loads(historical_path.read_text()):
   path=REPO/'paper/submission'/item['local_path']
   if not path.exists():
    result=session.get(item['source_url'],timeout=45);result.raise_for_status();path.write_bytes(result.content)
   assert hashlib.sha256(path.read_bytes()).hexdigest()==item['sha256'],item['local_path']
 (OUT/'image_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
if __name__=='__main__':main()
