"""ЛР12: выбор шага по обучающим запросам, проверка на другом пуле.

Время запроса берётся из C++ после загрузки индекса. Первый повтор прогревочный.
Сравниваются полные количества и первые 50 ID/score; полный порядок дополнительно
проверяется на контролируемых корпусах в test_extensions.py.
"""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
import hashlib

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.project.build import EXE

TRAIN=['и && компьютер','в && процессор','на && телефон','по && нейрон','для && биология']
TEST=['и && космос','в && вирус','на && робот','по && квантовый','для && медицина']


# Один прогрев и пять повторов; загрузка индекса исключается из измеряемой части.
def measure(engine,index,queries,skip=None):
    command=[str(engine),'search','--profile','--index',str(index),'--lemmas',str(ROOT/'data/morphology/lemmas.tsv'),'--limit','50']
    command+=['--skip-index',str(skip)] if skip else ['--no-skips']
    batch=queries*6
    result=subprocess.run(command,cwd=ROOT,input='\n'.join(batch)+'\n',capture_output=True,encoding='utf-8',check=True)
    rows=[json.loads(line) for line in result.stdout.splitlines()]
    if len(rows)!=len(batch) or any('error' in r for r in rows):
        raise RuntimeError('Ошибка или неполный результат измерений')
    groups=[]
    for i,q in enumerate(queries):
        samples=[rows[i+j*len(queries)] for j in range(1,6)]
        expected=rows[i]
        if any(r['count']!=expected['count'] or r['results']!=expected['results'] for r in samples):
            raise RuntimeError('Нестабильная выдача')
        groups.append({'query':q,'count':expected['count'],'results':expected['results'],
                       'seconds':statistics.median(r['search_seconds'] for r in samples),
                       'jumps':samples[0]['jumps'],'comparisons':samples[0]['comparisons']})
    return groups


# Сравнить шаги на обучающем пуле, выбрать лучший и отдельно измерить на проверочном.
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine',type=Path,default=ROOT/('build/ir_engine'+EXE))
    args=parser.parse_args()
    folder=ROOT/'data/advanced'
    results=ROOT/'results/advanced'
    index=folder/'index.ir'
    baseline=measure(args.engine,index,TRAIN+TEST)
    variants=[]
    for step in (32,128,0):
        path=folder/f'skips-{step}.ir'
        if not path.exists():
            stats=subprocess.run([str(args.engine),'make-skips','--index',str(index),'--output',str(path),'--step',str(step)],cwd=ROOT,capture_output=True,encoding='utf-8',check=True)
            (results/f'skips-{step}.json').write_text(stats.stdout,encoding='utf-8')
        rows=measure(args.engine,index,TRAIN+TEST,path)
        for base,row in zip(baseline,rows):
            if base['count']!=row['count'] or base['results']!=row['results']:
                raise RuntimeError('Прыжки изменили результат: '+row['query'])
        train=statistics.mean(row['seconds'] for row in rows[:len(TRAIN)])
        test=statistics.mean(row['seconds'] for row in rows[len(TRAIN):])
        variants.append({'step':step,'policy':'ceil(sqrt(df))' if step==0 else 'fixed',
                         'bytes':path.stat().st_size,'train_seconds':train,'test_seconds':test,'queries':rows})
        print('Измерен шаг:',step,flush=True)
    chosen=min(variants,key=lambda v:v['train_seconds'])
    result={'training_queries':TRAIN,'test_queries':TEST,'repeats':5,'warmups':1,
            'scope':'C++ search_seconds, loaded index; full counts and first 50 ordered results equal',
            'baseline':baseline,'variants':variants,'chosen_step':chosen['step'],
            'train_speedup':statistics.mean(r['seconds'] for r in baseline[:len(TRAIN)])/chosen['train_seconds'],
            'test_speedup':statistics.mean(r['seconds'] for r in baseline[len(TRAIN):])/chosen['test_seconds']}
    with index.open('rb') as f:
        result['index_header_sha256']=hashlib.sha256(f.read(96)).hexdigest()
    (results/'skip_benchmark.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    # Выбор делается только по TRAIN. Тестовый пул на выбор не влияет.
    config=folder/'index.search.json'
    if config.exists():
        data=json.loads(config.read_text(encoding='utf-8'))
        data['skip-index']=f'data/advanced/skips-{chosen["step"]}.ir'
        partial=config.with_suffix('.building')
        partial.write_text(json.dumps(data,indent=2),encoding='utf-8')
        partial.replace(config)
    print(json.dumps({k:result[k] for k in ['chosen_step','train_speedup','test_speedup']},ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
