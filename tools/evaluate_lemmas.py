"""Ограниченная оценка лемматизации на двух прежних размеченных пулах.

Метки сохранены из исходного проекта и получены ИИ. Это диагностика,
не независимая человеческая оценка всей коллекции и не отдельная ЛР6.
"""
import argparse
import csv
import json
from pathlib import Path
import subprocess
import sys
import hashlib

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from lab13.evaluate import metrics
from tools.project.build import EXE


# Сравнить стеммы и леммы на двух заранее размеченных пулах; разметка не охватывает весь корпус.
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--engine',type=Path,default=ROOT/('build/ir_engine'+EXE))
    args=p.parse_args()
    with (ROOT/'lab04/review_10000/review_10000.csv').open(encoding='utf-8-sig') as f:
        judgments=list(csv.DictReader(f))
    queries={r['query_id']:r['query'] for r in judgments}
    pools={qid:{r['doc_id']:int(r['relevant']) for r in judgments if r['query_id']==qid} for qid in queries}
    qids=sorted(queries)
    text='\n'.join(' || '.join(queries[qid].split()) for qid in qids)+'\n'
    rows=[]
    for name,index,extra in [('stem','data/lab11_index/index.ir',[]),
                             ('lemma','data/advanced/index.ir',['--lemmas',str(ROOT/'data/morphology/lemmas.tsv')])]:
        command=[str(args.engine),'search','--index',str(ROOT/index),'--ranked','--limit','1000000',*extra]
        result=subprocess.run(command,cwd=ROOT,input=text,capture_output=True,encoding='utf-8',check=True)
        outputs=[json.loads(line) for line in result.stdout.splitlines()]
        if len(outputs)!=len(qids) or any('error' in r for r in outputs):
            raise RuntimeError('Неполная выдача оценки')
        for qid,output in zip(qids,outputs):
            if output['count']!=len(output['results']):
                raise RuntimeError('Усечённая выдача оценки')
            pool=pools[qid]
            ids=[r['id'] for r in output['results'] if r['id'] in pool]
            relevant=sum(pool[i] for i in ids)
            rows.append({'query':queries[qid],'mode':name,'judged':len(pool),'retrieved':len(ids),
                         'precision':relevant/len(ids) if ids else 0,'recall':relevant/sum(pool.values()),
                         'at5':metrics(ids,pool,5),'ids_at5':ids[:5]})
    output={'scope':'two historical closed pools; AI judgments, no independent human review',
            'candidate_policy':'explicit OR, same original query text; full-corpus weights, then filtered to judged pool',
            'results':rows}
    with (ROOT/'data/advanced/index.ir').open('rb') as f:
        output['index_header_sha256']=hashlib.sha256(f.read(96)).hexdigest()
    path=ROOT/'results/advanced/lemma_quality.json'
    path.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(output,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
