#!/usr/bin/env python3
"""Compile the Games and Culture manuscript using the supplied SAGE class.

Use --refresh-source only to convert the existing Markdown draft to LaTeX.
Without that flag, edits to manuscript.tex/title_page.tex are preserved.
"""
from pathlib import Path
import argparse,re,shutil,subprocess,json,os
REPO=Path(__file__).resolve().parents[1]
OUT=REPO/'paper/submission'
TEMPLATE=REPO/'paper/A_demonstration_of_the_LaTeX2e_class_file_for_SAGE_Publications__2_'
PREAMBLE=r'''% Games and Culture: repository publishing target, supplied SAGE template.
\documentclass[Review,sageapa,times,doublespace]{sagej}
\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage{longtable,array,url,float}
\usepackage[hidelinks]{hyperref}
% Review defaults to a smaller trim and one-and-a-half spacing in this class.
% Match the checked journal submission dimensions and actual double spacing.
\geometry{reset,letterpaper,margin=1in,headheight=30pt}
\doublespacing
\raggedbottom
\urlstyle{same}
\captionsetup[figure]{font={normalsize,stretch=1.667},labelfont={bf},textfont=rm}
\captionsetup[table]{font={normalsize,stretch=1.667},labelfont={bf},textfont=rm}
\def\journalname{Games and Culture}
\def\volumenumber{Submission draft}
\def\issuenumber{2026}
\def\volumeyear{2026}
\hypersetup{pdfauthor={},pdftitle={Maintaining the Game, Maintaining the Collection}}
'''

def esc(t):
 mapping={'\\':r'\textbackslash{}','&':r'\&','%':r'\%','$':r'\$','#':r'\#','_':r'\_','{':r'\{','}':r'\}','~':r'\textasciitilde{}','^':r'\textasciicircum{}','−':r'\ensuremath{-}','→':r'\ensuremath{\rightarrow}','±':r'\ensuremath{\pm}','×':r'\ensuremath{\times}','–':'--','—':'---','’':"'",'‘':"'",'“':'``','”':"''"}
 return ''.join(mapping.get(c,c) for c in t)
def inline(t):
 parts=re.split(r'(https?://[^\s]+|\*[^*]+\*)',t)
 out=[]
 for s in parts:
  if s.startswith('http'):out.append(r'\url{'+s+'}')
  elif s.startswith('*') and s.endswith('*'):out.append(r'\emph{'+esc(s[1:-1])+'}')
  else:out.append(esc(s))
 return ''.join(out)
def paragraphs(t):return '\n\n'.join(inline(x) for x in t.strip().split('\n\n'))+'\n'
def body(t):
 lines=t.splitlines();out=[]
 for line in lines:
  if line.startswith('### '):out.append(r'\subsection{'+inline(re.sub(r'^\d+\.\d+\s*','',line[4:]))+'}')
  elif line.startswith('## '):out.append(r'\section{'+inline(re.sub(r'^\d+\.\s*','',line[3:]))+'}')
  else:out.append(inline(line))
 return '\n'.join(out)+'\n'
def assets(t):
 sections=re.split(r'### ',t)[1:];out=[]
 for section in sections:
  lines=section.strip().splitlines();heading=lines[0];content=lines[1:]
  out.append(r'\clearpage')
  if heading.startswith('Table '):
   title=heading.split('. ',1)[1];rows=[]
   for line in content:
    if line.startswith('|'):
     cells=[x.strip() for x in line.strip('|').split('|')]
     if not all(re.fullmatch(r':?-+:?',c) for c in cells):rows.append(cells)
   spec=r'@{}p{0.43\linewidth}p{0.16\linewidth}p{0.16\linewidth}p{0.16\linewidth}@{}' if len(rows[0])==4 else r'@{}p{0.20\linewidth}p{0.38\linewidth}p{0.36\linewidth}@{}'
   out.append(r'\begin{longtable}{'+spec+'}')
   out.append(r'\caption{'+inline(title)+r'}\label{tab:'+heading[6]+r'}\\')
   header=' & '.join(r'\textbf{'+inline(c)+'}' for c in rows[0])+r' \\'
   out.extend([r'\toprule',header,r'\midrule',r'\endfirsthead',r'\toprule',header,r'\midrule',r'\endhead',r'\bottomrule',r'\endfoot'])
   out.extend(' & '.join(inline(c) for c in row)+r' \\' for row in rows[1:])
   out.append(r'\end{longtable}')
   out.extend(inline(x)+'\n' for x in content if x.startswith('Note.'))
  elif heading.startswith('Figure '):
   title=heading.split('. ',1)[1];path=next(re.search(r'\]\((.*?)\)',x).group(1) for x in content if x.startswith('!['))
   out.extend([r'\begin{figure}[p]',r'\centering',r'\includegraphics[width=\linewidth]{'+path.replace('.png','.pdf')+'}',r'\caption{'+inline(title)+'}',r'\label{fig:'+heading[7]+'}'])
   out.extend(r'\caption*{'+inline(x)+'}' for x in content if x.startswith('Note.'))
   if heading.startswith('Figure 3.'):out.append(r'\label{LastPage}')
   out.append(r'\end{figure}')
 return '\n'.join(out)+'\n'
def card_references(text):
 for a,b in [('Figure 2A',r'Figure~\ref{fig:2}A'),('Figure 2B',r'Figure~\ref{fig:2}B'),('Figure 1 ',r'Figure~\ref{fig:1} '),('Figure 3 ',r'Figure~\ref{fig:3} ')]:text=text.replace(a,b)
 if (OUT/'card_figures.tex').exists():
  names = [('Tyrantus', 'tyrantus'), ('The Demon Seed', 'the-demon-seed'), ('Big Game Hunter', 'big-game-hunter'), ('Evasive Wyrm', 'evasive-wyrm'), ('Snowchugger', 'snowchugger'), ('Chill-o-matic', 'chill-o-matic'), ('Blightborn Tamsin', 'blightborn-tamsin')]
  for name, slug in names:
   text=text.replace(name,name+r' (Figure~\ref{fig:card-'+slug+'})',1)
  placements = [('Snowchugger (Figure', 'Comparison'), ('Tyrantus was selected', 'Tyrantus'), ('The Demon Seed was selected', 'DemonSeed'), ('Patch 26.0, released April 4', 'BlightbornTamsin'), ('Big Game Hunter supplies', 'BigGameHunter'), ('Evasive Wyrm provides', 'EvasiveWyrm')]
  for start, name in placements:
   text=text.replace(start,r'\paperCard'+name+'\n\n'+start,1)
 return text

def existing_analysis_figures(text):
 text=text.replace('Table 1 distinguishes',r'Table~\ref{tab:1} distinguishes').replace('Table 2 records',r'Table~\ref{tab:2} records')
 if not (OUT/'analysis_figures.tex').exists():return text
 text=text.replace(r'Figure~\ref{fig:2}A displays this composition.',r'Figure~\ref{fig:2} displays this composition using the original analysis chart.')
 text=text.replace(r'Figure~\ref{fig:2}B compares the three distributions using the same denominator.',r'Table~\ref{tab:matched-costs} compares these measures on the same sample; Figure~\ref{fig:conversion} shows the original analysis chart of additional conversion dust.')
 text=text.replace('The conversion distribution is concentrated',r'\input{matched_cost_table.tex}'+'\n\n'+'The conversion distribution is concentrated',1)
 pattern=r'\\clearpage\s*\\begin\{figure\}\[p\]\s*\\centering\s*\\includegraphics\[width=\\linewidth\]\{figures/figure_2.pdf\}.*?\\end\{figure\}'
 return re.sub(pattern,lambda m:r'\input{analysis_figures.tex}',text,flags=re.S)

def load_author_config():
 path=Path(os.environ.get('POWER_CREEP_AUTHOR_CONFIG', Path(os.environ.get('XDG_CONFIG_HOME', Path.home()/'.config'))/'power-creep/author.json'))
 if not path.is_file():
  raise SystemExit('Author configuration required for --refresh-source: set POWER_CREEP_AUTHOR_CONFIG to a private JSON file (see README.md).')
 author=json.loads(path.read_text())
 for key in ['running_head','name','affiliation','correspondence','email']:
  if not isinstance(author.get(key),str) or not author[key].strip():
   raise SystemExit('Missing author configuration field: '+key)
 return author

def refresh():
 author=load_author_config()
 t=(OUT/'manuscript.md').read_text();title=t.splitlines()[0][2:]
 abstract=t.split('## Abstract\n',1)[1].split('Keywords:',1)[0].strip()
 keywords=t.split('Keywords:',1)[1].split('\n',1)[0].strip()
 main=t.split('## 1. Introduction',1)[1].split('## References',1)[0]
 main='## 1. Introduction'+main
 refs=t.split('## References',1)[1].split('## Tables and figures',1)[0]
 refparagraphs=[x.strip() for x in refs.split('\n\n') if x.strip()]
 bib=['% Checked APA entries preserved from the manuscript bibliography.',r'\begin{thebibliography}{99}']
 for i,p in enumerate(refparagraphs,1):bib.extend([r'\bibitem[]{R'+str(i)+'}',inline(p),''])
 bib.append(r'\end{thebibliography}')
 (OUT/'references.tex').write_text('\n'.join(bib)+'\n')
 header=PREAMBLE+'\n'+r'\begin{document}'+'\n'+r'\runninghead{Maintaining the game, maintaining the collection}'+'\n'+r'\title{'+esc(title)+'}\n'+r'\author{}'+'\n'+r'\begin{abstract}'+'\n'+paragraphs(abstract)+r'\end{abstract}'+'\n'+r'\keywords{'+esc(keywords)+'}\n'+r'\maketitle'+'\n'
 header=header.replace(r'\begin{document}', (r'\input{card_figures.tex}'+'\n' if (OUT/'card_figures.tex').exists() else '')+r'\begin{document}')
 tex=header+card_references(body(main))+r'\input{references.tex}'+'\n'+assets(t.split('## Tables and figures',1)[1])+r'\end{document}'+'\n'
 tex=existing_analysis_figures(tex)
 (OUT/'manuscript.tex').write_text(tex)
 titletext=(OUT/'title_page.md').read_text();bodytext='\n'.join(titletext.splitlines()[1:])
 titleheader=PREAMBLE+'\n'+r'\begin{document}'+'\n'+r'\runninghead{'+esc(author['running_head'])+'}\n'+r'\title{'+esc(title)+'}\n'+r'\author{'+esc(author['name'])+r'\affilnum{1}}'+'\n'+r'\affiliation{\affilnum{1}'+esc(author['affiliation'])+'}\n'+r'\corrauth{'+esc(author['correspondence'])+'}\n'+r'\email{'+esc(author['email'])+'}\n'+r'\makeatletter\def\@keywords{}\makeatother'+'\n'+r'\maketitle'+'\n'
 titletex=titleheader+body(bodytext).replace(r'\section{',r'\section*{').replace(r'\subsection{',r'\subsection*{')+r'\end{document}'+'\n'
 titletex=titletex.replace('pdfauthor={}','pdfauthor={'+esc(author['name'])+'}')
 (OUT/'title_page.tex').write_text(titletex)
 shutil.copyfile(TEMPLATE/'sagej.cls',OUT/'sagej.cls')
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--refresh-source',action='store_true');args=p.parse_args()
 if args.refresh_source:refresh()
 for name in ['manuscript','title_page']:
  result=subprocess.run(['latexmk','-pdf','-interaction=nonstopmode','-halt-on-error',name+'.tex'],cwd=OUT,capture_output=True,text=True)
  if result.returncode:
   print(result.stdout[-10000:]);print(result.stderr[-2000:]);raise SystemExit(result.returncode)
  log=(OUT/(name+'.log')).read_text(errors='replace')
  problems=[x for x in log.splitlines() if any(k in x for k in ['Overfull','undefined','LaTeX Warning:','Package hyperref Warning:'])]
  print(name+'.pdf compiled; '+str(len(problems))+' layout/reference warnings')
  for line in problems:print(line)
if __name__=='__main__':main()
