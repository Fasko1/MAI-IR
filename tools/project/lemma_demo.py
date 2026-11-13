"""Короткая демонстрация настоящего словаря OpenCorpora через C++ CLI."""
import argparse
import json
from pathlib import Path
import subprocess
from tools.project.build import ROOT,EXE


# На маленьком временном TSV показать реальные леммы C++ без загрузки полного индекса.
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--engine',type=Path,default=ROOT/('build/ir_engine'+EXE))
    args=p.parse_args()
    folder=ROOT/'build/lemma_demo'
    folder.mkdir(exist_ok=True)
    source=folder/'tokens.tsv'
    output=folder/'lemmas.tsv'
    words=['компьютерами','процессорами','клетки','клетками','ёлки','007','ВИЧ-1','DNA','неизвестнословоxyz']
    expected=['компьютер','процессор','клетка','клетка','елка','007','ВИЧ-1','DNA','неизвестнословоxyz']
    source.write_text('1\t'+' '.join(words)+'\tКонтрольный пример\t\n',encoding='utf-8')
    result=subprocess.run([str(args.engine),'lemmatize','--input',str(source),'--output',str(output),
                          '--dictionary',str(ROOT/'data/morphology/lemmas.tsv')],cwd=ROOT,capture_output=True,encoding='utf-8',check=True)
    actual=output.read_text(encoding='utf-8').split('\t')[1].split()
    if actual!=expected:
        raise RuntimeError('Словарный пример не совпал: '+repr(actual))
    pairs=[{'token':word,'lemma':lemma} for word,lemma in zip(words,actual)]
    report={'source':'data/morphology/provenance.json','pairs':pairs,'counts':json.loads(result.stdout),'passed':True}
    target=ROOT/'results/advanced/lemma_examples.json'
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    for pair in pairs:
        print(pair['token'],'->',pair['lemma'])
    print('PASS: словарные формы, идентификаторы и неизвестное слово')


if __name__=='__main__':
    main()
