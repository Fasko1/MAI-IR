"""Полный пересчёт ЛР4/10/12/14 в отдельном data/advanced.

Принятые stems.tsv и два старых индекса сохраняются. Маркер для веба
публикуется только после успешного завершения и проверки всех этапов.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

from tools.project.build import ROOT, EXE


# Конвейер новых работ: проверка входов → независимые этапы → маркер готовности → измерения и отчёт.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-build',action='store_true')
    parser.add_argument('--force',action='store_true')
    parser.add_argument('--engine',type=Path,help='Отдельный EXE для безопасной параллельной разработки')
    parser.add_argument('--skip-pdf',action='store_true')
    parser.add_argument('--skip-evaluation',action='store_true',help='Только подготовка индексов; измерения выполнить отдельно')
    args = parser.parse_args()
    if not args.no_build:
        subprocess.run([sys.executable,'-m','tools.project.build'],cwd=ROOT,check=True)
    folder=ROOT/'data/advanced'
    results=ROOT/'results/advanced'
    folder.mkdir(exist_ok=True)
    results.mkdir(exist_ok=True)
    corpus=ROOT/'data/corpus'
    dictionary=ROOT/'data/morphology/lemmas.tsv'
    if not dictionary.is_file():
        raise SystemExit('Сначала python tools/prepare_lemmas.py --packaged (подготовка словаря описана в README.md)')
    state_path=results/'pipeline.json'
    inputs={str(p.relative_to(ROOT)):{'bytes':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns}
            for p in [corpus/'tokens.tsv',corpus/'texts.tsv',dictionary]}
    state=json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
    previous_inputs=state.get('inputs',{})
    previous_inputs={key:value for key,value in previous_inputs.items() if not key.startswith('build')}
    if args.force or previous_inputs!=inputs:
        state={'inputs':inputs,'started':datetime.now(timezone.utc).isoformat(),'stages':{}}
        # Генерируемый маркер не должен выбирать смесь старых и новых частей.
        (folder/'index.search.json').unlink(missing_ok=True)
    state['inputs']=inputs
    engine=args.engine or ROOT/('build/ir_engine'+EXE)

    def save():
        state_path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')

    # Этап можно продолжить по сохранённому состоянию. Промежуточный файл заменяет итоговый только после успеха.
    def stage(name,command,output):
        old=state['stages'].get(name,{})
        if old.get('complete') and output.exists() and old.get('bytes')==output.stat().st_size:
            print('Готовый этап:',name,flush=True)
            return
        state['current']=name
        save()
        print('Этап:',name,flush=True)
        started=time.perf_counter()
        command=list(map(str,command))
        atomic=name in ('lemmatization','coordinates','text_offsets','skips')
        partial=output.with_suffix(output.suffix+'.building')
        if atomic:
            for i,value in enumerate(command):
                if value=='--output':
                    command[i+1]=str(partial)
        stdout_path=results/(name+'.json')
        if stdout_path==output:
            stdout_path=results/(name+'_stdout.log')
        with stdout_path.open('w',encoding='utf-8') as out, (results/(name+'.log')).open('w',encoding='utf-8') as log:
            subprocess.run(command,cwd=ROOT,stdout=out,stderr=log,check=True)
        if atomic:
            partial.replace(output)
        state['stages'][name]={'complete':True,'seconds':time.perf_counter()-started,'bytes':output.stat().st_size}
        save()

    stage('lemmatization',[engine,'lemmatize','--input',corpus/'tokens.tsv','--output',folder/'lemmas.tsv','--dictionary',dictionary],folder/'lemmas.tsv')
    stage('index',[engine,'build','--inline','--manifest',folder/'lemmas.tsv','--index',folder/'raw.ir'],folder/'raw.ir')
    stage('compression',[engine,'compress','--input',folder/'raw.ir','--output',folder/'index.ir'],folder/'index.ir')
    stage('skips',[engine,'make-skips','--index',folder/'index.ir','--output',folder/'skips.ir'],folder/'skips.ir')
    stage('coordinates',[engine,'coordinates','--input',corpus/'tokens.tsv','--index',folder/'index.ir','--output',folder/'positions.ir'],folder/'positions.ir')
    stage('coordinate_audit',[sys.executable,'tools/audit_coordinates.py'],results/'coordinate_audit.json')
    stage('text_offsets',[engine,'text-offsets','--input',corpus/'texts.tsv','--index',folder/'index.ir','--output',folder/'texts.offsets'],folder/'texts.offsets')
    stage('verify',[engine,'verify','--index',folder/'index.ir'],folder/'index.ir')
    config={'lemmas':'data/morphology/lemmas.tsv','positions':'data/advanced/positions.ir',
            'texts':'data/corpus/texts.tsv','text-offsets':'data/advanced/texts.offsets','skip-index':'data/advanced/skips.ir'}
    measured=results/'skip_benchmark.json'
    if measured.exists():
        report=json.loads(measured.read_text(encoding='utf-8'))
        with (folder/'index.ir').open('rb') as index_file:
            signature=hashlib.sha256(index_file.read(96)).hexdigest()
        chosen=folder/f'skips-{report["chosen_step"]}.ir'
        if report.get('index_header_sha256')==signature and chosen.exists():
            config['skip-index']=chosen.relative_to(ROOT).as_posix()
    partial=folder/'index.search.building'
    partial.write_text(json.dumps(config,indent=2),encoding='utf-8')
    partial.replace(folder/'index.search.json')
    state['current']='complete'
    state['finished']=datetime.now(timezone.utc).isoformat()
    state['dictionary']=json.loads((dictionary.parent/'provenance.json').read_text(encoding='utf-8'))
    save()
    if not args.skip_evaluation:
        for name,script in [('skip_benchmark','tools/benchmark_advanced.py'),('lemma_quality','tools/evaluate_lemmas.py')]:
            state['current']=name
            save()
            started=time.perf_counter()
            subprocess.run([sys.executable,script,'--engine',str(engine)],cwd=ROOT,check=True)
            state['stages'][name]={'complete':True,'seconds':time.perf_counter()-started}
        state['current']='complete'
        state['finished']=datetime.now(timezone.utc).isoformat()
        save()
    subprocess.run([sys.executable,'-m','tools.project.report',*(['--skip-pdf'] if args.skip_pdf else [])],cwd=ROOT,check=True)
    print('Новый индекс готов. project.ps1 web автоматически использует data/advanced/index.ir.',flush=True)


if __name__=='__main__':
    main()
