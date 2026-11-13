"""Приёмка нового полного индекса: raw/VB, HTTP и исходные тексты цитат."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import re
import struct
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.project.build import EXE
from lab08.web import create_server


# Проверить полные выдачи raw/VB, строгие цитаты по исходным текстам и HTTP-сниппеты.
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--engine',type=Path,default=ROOT/('build/ir_engine'+EXE))
    args=p.parse_args()
    folder=ROOT/'data/advanced'
    result={}
    with (ROOT/'lab04/queries.csv').open(encoding='utf-8-sig') as f:
        queries=[row['query'] for row in csv.DictReader(f)]
    queries+=['клетки && иммунитет','(космос || нейроны) && !вирус','!несуществующийтермин',
              '(в || и) && !(ген && клетка)','агент 007']
    hashes={}
    for mode in ('raw','index'):
        for ranked in (False,True):
            command=[str(args.engine),'search','--index',str(folder/(mode+'.ir')),
                     '--lemmas',str(ROOT/'data/morphology/lemmas.tsv'),'--limit','1000000']
            if ranked:
                command.append('--ranked')
            with tempfile.TemporaryFile() as output:
                subprocess.run(command,cwd=ROOT,input=('\n'.join(queries)+'\n').encode('utf-8'),stdout=output,check=True)
                output.seek(0)
                digest=hashlib.sha256()
                lines=0
                tail=b''
                while chunk:=output.read(1048576):
                    if b'"error":' in tail+chunk:
                        raise RuntimeError('Ошибка полного сравнения')
                    digest.update(chunk)
                    lines+=chunk.count(b'\n')
                    tail=chunk[-32:]
                if lines!=len(queries):
                    raise RuntimeError('Не все запросы обработаны')
                hashes[f'{mode}/{ranked}']=digest.hexdigest()
            print('Полные выдачи:',mode,ranked,flush=True)
    result['full_result_lists']=len(queries)*2
    result['hashes']=hashes
    result['lossless']=all(hashes[f'raw/{ranked}']==hashes[f'index/{ranked}'] for ranked in (False,True))
    if not result['lossless']:
        raise RuntimeError('Raw/VB ответы отличаются')

    # Независимо проверяем полную цитату в исходных статьях первых 50 результатов.
    command=[str(args.engine),'search','--index',str(folder/'index.ir'),'--positions',str(folder/'positions.ir'),
             '--lemmas',str(ROOT/'data/morphology/lemmas.tsv'),'--query','"искусственный интеллект"','--limit','50']
    answer=json.loads(subprocess.run(command,cwd=ROOT,capture_output=True,encoding='utf-8',check=True).stdout)
    wanted={row['id'] for row in answer['results']}
    if not wanted:
        raise RuntimeError('Нет положительных примеров цитаты для приёмки')
    ordinals={}
    with (folder/'index.ir').open('rb') as index:
        index.seek(24)
        count=struct.unpack('<Q',index.read(8))[0]
        index.seek(96)
        for ordinal in range(count):
            size=struct.unpack('<I',index.read(4))[0]
            doc_id=index.read(size).decode('utf-8')
            for _ in range(2):
                size=struct.unpack('<I',index.read(4))[0]
                index.seek(size,1)
            index.seek(16,1)
            if doc_id in wanted:
                ordinals[doc_id]=ordinal
    with (folder/'texts.offsets').open('rb') as offsets,(ROOT/'data/corpus/texts.tsv').open('rb') as corpus:
        for doc_id,ordinal in ordinals.items():
            offsets.seek(32+ordinal*8)
            corpus.seek(struct.unpack('<Q',offsets.read(8))[0])
            fields=corpus.readline().decode('utf-8').rstrip('\n').split('\t')
            if fields[0]!=doc_id:
                raise RuntimeError('Несогласованные смещения текста')
            tokens=re.findall(r'[a-zа-яё0-9]+(?:[-‐‑][a-zа-яё0-9]+)*',fields[1].lower())
            if not any(tokens[i:i+2]==['искусственный','интеллект'] for i in range(len(tokens)-1)):
                raise RuntimeError('Частичное совпадение цитаты в документе '+doc_id)
    if set(ordinals)!=wanted:
        raise RuntimeError('Не все результаты цитаты найдены в таблице документов')
    result['quote_verified_original_documents']=len(wanted)

    server=create_server(folder/'index.ir',port=0,engine=args.engine)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    checks=[]
    try:
        base=f'http://127.0.0.1:{server.server_port}/'
        for query,mode in [('компьютер && процессор','boolean'),('компьютер && процессор','ranked'),
                           ('"искусственный интеллект"','boolean'),('"искусственный неизвестнословоxyz интеллект"','boolean')]:
            started=time.perf_counter()
            with urlopen(base+'?'+urlencode({'q':query,'mode':mode}),timeout=120) as response:
                text=response.read().decode('utf-8')
                if response.status!=200 or 'Ошибка запроса:' in text:
                    raise RuntimeError('Ошибка HTTP')
            if 'неизвестнословоxyz' in query:
                if 'Найдено документов: <b>0</b>' not in text:
                    raise RuntimeError('Частичная цитата попала в ответ')
            elif '<mark>' not in text or 'class="snippet"' not in text:
                raise RuntimeError('Нет сниппетов или подсветки')
            checks.append({'query':query,'mode':mode,'seconds':time.perf_counter()-started,'passed':True})
            print('HTTP:',query,mode,flush=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    result['http']=checks
    result['passed']=result['lossless'] and all(r['passed'] for r in checks)
    (ROOT/'results/advanced/acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Новый полный индекс и HTTP проверены',flush=True)


if __name__=='__main__':
    main()
