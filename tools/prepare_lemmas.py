"""Подготовка ДАННЫХ OpenCorpora; алгоритм поиска лемм выполняет собственный C++.

XML читается потоково, неоднозначности сохраняются в журнале. Для одного токена
выбирается лексикографически первая лемма; контекстная морфология не заявляется.
"""
import argparse
import bz2
import hashlib
import json
from pathlib import Path
import sqlite3
import urllib.request
import xml.etree.ElementTree as ET
import struct
import importlib.metadata

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://opencorpora.org/files/export/dict/dict.opcorpora.xml.bz2'


def packaged_pairs():
    """Читаем только таблицы словаря, не вызываем готовую морфологию.

    Идея декодирования парадигм сверена с учебным проектом К. Арусланова;
    реализация конвертера здесь самостоятельная, источник данных фиксируется.
    """
    import pymorphy3_dicts_ru
    from dawg_python import RecordDAWG
    root = Path(pymorphy3_dicts_ru.get_path())
    meta = dict(json.loads((root/'meta.json').read_text(encoding='utf-8')))
    assert meta['format_version'] == '2.4'
    suffixes = json.loads((root/'suffixes.json').read_text(encoding='utf-8'))
    prefixes = meta['compile_options']['paradigm_prefixes']
    data = (root/'paradigms.array').read_bytes()
    count = struct.unpack_from('<H',data)[0]
    pos = 2
    paradigms = []
    for _ in range(count):
        n = struct.unpack_from('<H',data,pos)[0]
        pos += 2
        assert n and n % 3 == 0
        paradigms.append(struct.unpack_from('<'+'H'*n,data,pos))
        pos += n*2
    assert pos == len(data)
    for word,(pid,fid) in RecordDAWG('>HH').load(str(root/'words.dawg')).iteritems():
        paradigm = paradigms[pid]
        n = len(paradigm)//3
        suffix, prefix = suffixes[paradigm[fid]], prefixes[paradigm[2*n+fid]]
        assert word.startswith(prefix) and word.endswith(suffix)
        stem = word[len(prefix):len(word)-len(suffix) if suffix else len(word)]
        lemma = prefixes[paradigm[2*n]] + stem + suffixes[paradigm[0]]
        yield word.lower().replace('ё','е'),lemma.lower().replace('ё','е')


# Получить пары словоформа/лемма, выбрать единственную лемму и записать происхождение и хеши данных.
def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--download', action='store_true')
    p.add_argument('--packaged', action='store_true', help='OpenCorpora из пакета данных pymorphy3-dicts-ru')
    args = p.parse_args()
    folder = ROOT/'data/morphology'
    folder.mkdir(exist_ok=True)
    archive = folder/'dict.opcorpora.xml.bz2'
    if not archive.exists() and not args.packaged:
        if not args.download:
            raise SystemExit('Нужны данные OpenCorpora: повторите с --download')
        partial = archive.with_suffix('.part')
        with urllib.request.urlopen(URL, timeout=60) as source, partial.open('wb') as out:
            while block := source.read(1048576):
                out.write(block)
        partial.replace(archive)
    database = folder/'prepare.sqlite'
    conn = sqlite3.connect(database)
    conn.execute('CREATE TABLE IF NOT EXISTS forms(form TEXT PRIMARY KEY, lemma TEXT, ambiguous INTEGER DEFAULT 0)')
    conn.execute('DELETE FROM forms')
    count = 0
    if args.packaged:
        batch = []
        for form,lemma in packaged_pairs():
            batch.append((form,lemma))
            if len(batch) >= 50000:
                conn.executemany('''INSERT INTO forms(form,lemma) VALUES(?,?) ON CONFLICT(form) DO UPDATE SET
                    ambiguous=MAX(ambiguous, forms.lemma<>excluded.lemma),lemma=MIN(forms.lemma,excluded.lemma)''',batch)
                conn.commit()
                count += len(batch)
                batch.clear()
                print('Словоформ обработано:',count,flush=True)
        conn.executemany('''INSERT INTO forms(form,lemma) VALUES(?,?) ON CONFLICT(form) DO UPDATE SET
            ambiguous=MAX(ambiguous, forms.lemma<>excluded.lemma),lemma=MIN(forms.lemma,excluded.lemma)''',batch)
    else:
      with bz2.open(archive, 'rb') as source:
        for event, element in ET.iterparse(source, events=('end',)):
            if element.tag != 'lemma':
                continue
            lemma = element.find('l').get('t').lower().replace('ё','е')
            forms = {node.get('t').lower().replace('ё','е') for node in element.findall('f')}
            forms.add(lemma)
            conn.executemany('''INSERT INTO forms(form,lemma) VALUES(?,?) ON CONFLICT(form) DO UPDATE SET
                ambiguous=MAX(ambiguous, forms.lemma<>excluded.lemma),lemma=MIN(forms.lemma,excluded.lemma)''',
                ((form,lemma) for form in forms if form and not any(c.isspace() for c in form)))
            element.clear()
            count += 1
            if count % 50000 == 0:
                conn.commit()
                print('Словарных статей:',count,flush=True)
    conn.commit()
    output = folder/'lemmas.tsv'
    partial = output.with_suffix('.building')
    with partial.open('w',encoding='utf-8',newline='\n') as out:
        for form, lemma in conn.execute('SELECT form,lemma FROM forms ORDER BY form'):
            out.write(form+'\t'+lemma+'\n')
    partial.replace(output)
    total, ambiguous = conn.execute('SELECT count(*),sum(ambiguous) FROM forms').fetchone()
    meta = {'source':URL,'license':'OpenCorpora dictionary data, CC BY-SA 3.0',
            'source_sha256':hashlib.sha256(archive.read_bytes()).hexdigest() if archive.exists() else None,
            'data_distribution':importlib.metadata.version('pymorphy3-dicts-ru') if args.packaged else None,
            'dictionary_sha256':hashlib.sha256(output.read_bytes()).hexdigest(),
            'forms':total,'ambiguous_forms':ambiguous,'policy':'lexicographically first normalized lemma; unknown token unchanged'}
    if args.packaged:
        import pymorphy3_dicts_ru
        root=Path(pymorphy3_dicts_ru.get_path())
        meta['distribution_license']=importlib.metadata.metadata('pymorphy3-dicts-ru').get('License')
        meta['distribution_homepage']=importlib.metadata.metadata('pymorphy3-dicts-ru').get('Home-page')
        meta['source_files_sha256']={name:hashlib.sha256((root/name).read_bytes()).hexdigest()
                                    for name in ['meta.json','paradigms.array','suffixes.json','words.dawg']}
    (folder/'provenance.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    conn.close()
    print(json.dumps(meta,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
