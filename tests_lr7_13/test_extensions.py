"""ЛР4/10/12/14: независимые ожидания для лемм, строгих цитат и сниппетов.

Проверяется настоящий C++ CLI. В частности, все слова в другом порядке,
часть цитаты, пропуск и совпадение через границу документов недопустимы.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import os
import random

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.project.build import EXE
ENGINE = Path(os.environ.get('IR_TEST_ENGINE',str(ROOT/('build/ir_engine'+EXE))))


def run(*args, check=True):
    p = subprocess.run([str(ENGINE),*map(str,args)],cwd=ROOT,capture_output=True,encoding='utf-8')
    if check and p.returncode:
        raise AssertionError(p.stderr)
    return p


class ExtensionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.temp.name)
        cls.dictionary = cls.folder/'dictionary.tsv'
        cls.dictionary.write_text('клетки\tклетка\nклетку\tклетка\nкота\tкот\nёлки\tелка\nелки\tелка\n',encoding='utf-8')
        texts = ['кот кот дом','кот дом','дом кот','кот большой дом','кот','клетки иммунитет','клетка иммунитет','','007 кота']
        cls.tokens = cls.folder/'tokens.tsv'
        cls.tokens.write_text(''.join(f'{i+1}\t{text}\tЗаголовок {i+1}\thttps://example.org/{i+1}\n' for i,text in enumerate(texts)),encoding='utf-8')
        cls.texts = cls.folder/'texts.tsv'
        cls.texts.write_text(cls.tokens.read_text(encoding='utf-8').replace('кот большой дом','кот <script>alert(1)</script> большой дом'),encoding='utf-8')
        cls.lemmas = cls.folder/'lemmas.tsv'
        result = run('lemmatize','--input',cls.tokens,'--output',cls.lemmas,'--dictionary',cls.dictionary)
        cls.lemma_stats = json.loads(result.stdout)
        cls.index = cls.folder/'index.ir'
        run('build','--inline','--manifest',cls.lemmas,'--index',cls.index)
        cls.positions = cls.folder/'positions.ir'
        run('coordinates','--input',cls.tokens,'--index',cls.index,'--output',cls.positions)
        cls.offsets = cls.folder/'texts.offsets'
        run('text-offsets','--input',cls.texts,'--index',cls.index,'--output',cls.offsets)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def search(self,query,*extra):
        return json.loads(run('search','--profile','--index',self.index,'--lemmas',self.dictionary,
            '--positions',self.positions,'--texts',self.texts,'--text-offsets',self.offsets,
            '--query',query,*extra).stdout)

    def ids(self,query,*extra):
        return [r['id'] for r in self.search(query,*extra)['results']]

    def test_lemma_is_dictionary_form_not_stem(self):
        self.assertEqual(self.ids('клетку'),['6','7'])
        self.assertEqual(self.ids('кота'),['1','2','3','4','5','9'])
        self.assertIn('клетка',self.lemmas.read_text(encoding='utf-8'))

    def test_occurrences_and_identifiers_preserved(self):
        self.assertEqual(self.lemma_stats['documents'],9)
        self.assertEqual(self.ids('007'),['9'])
        self.assertEqual(self.ids('несуществующийтермин'),[])

    def test_exact_phrase_no_partial_match(self):
        self.assertEqual(self.ids('"кот дом"'),['1','2'])

    def test_reversed_order_is_different(self):
        self.assertEqual(self.ids('"дом кот"'),['3'])

    def test_repeated_terms_require_distinct_positions(self):
        self.assertEqual(self.ids('"кот кот дом"'),['1'])
        self.assertEqual(self.ids('"кот кот кот"'),[])

    def test_quote_does_not_replace_wordforms(self):
        self.assertEqual(self.ids('"клетки иммунитет"'),['6'])
        self.assertEqual(self.ids('"клетка иммунитет"'),['7'])

    def test_unknown_phrase_component_is_not_ignored(self):
        self.assertEqual(self.ids('"кот неизвестно дом"'),[])

    def test_boolean_phrase_composition(self):
        self.assertEqual(self.ids('"кот дом" || 007'),['1','2','9'])
        self.assertEqual(self.ids('кот && !"кот дом"'),['3','4','5','9'])

    def test_ranked_quote_keeps_strict_filter(self):
        data=self.search('"кот дом"','--ranked')
        self.assertEqual({r['id'] for r in data['results']},{'1','2'})
        self.assertTrue(all(r['score']>0 for r in data['results']))

    def test_phrase_missing_quotes_report_error(self):
        self.assertIn('error',self.search('"кот дом'))
        self.assertIn('error',self.search('""'))

    def test_snippet_contains_original_text_and_marks(self):
        data=self.search('клетку')
        row=next(r for r in data['results'] if r['id']=='6')
        self.assertEqual(''.join(p['text'] for p in row['snippet_parts']),'клетки иммунитет')
        self.assertEqual([p['text'] for p in row['snippet_parts'] if p['match']],['клетки'])

    def test_html_is_escaped_before_highlight(self):
        from lab08.web import page
        html=page('большой',data=self.search('большой'))
        self.assertIn('&lt;script&gt;',html)
        self.assertNotIn('<script>',html)
        self.assertIn('<mark>большой</mark>',html)

    def test_skips_preserve_complete_results(self):
        for q in ['кот && дом','кот || дом','кот && !дом','"кот дом" || 007']:
            self.assertEqual(self.search(q)['results'],self.search(q,'--no-skips')['results'])

    def test_coordinate_binding_rejects_changed_document_ids(self):
        other=self.folder/'other.tsv'
        other.write_text('999\tкот\tOther\t\n',encoding='utf-8')
        idx=self.folder/'other.ir'
        run('build','--inline','--manifest',other,'--index',idx)
        p=run('search','--index',idx,'--positions',self.positions,'--query','кот',check=False)
        self.assertNotEqual(p.returncode,0)

    def test_persisted_skips_restart_vb_decoder(self):
        # Редкие документы расположены в конце длинного списка. Ожидание
        # известно из генератора, а не из контрольной выдачи движка.
        source=self.folder/'long.tsv'
        source.write_text(''.join(f'{i}\tcommon'+(' rare' if i in (900,950,999) else '')+f'\tDoc {i}\t\n' for i in range(1000)),encoding='utf-8')
        index=self.folder/'long.ir'
        run('build','--inline','--manifest',source,'--index',index,'--compressed')
        for step in (16,32,64):
            skips=self.folder/f'skip-{step}.ir'
            run('make-skips','--index',index,'--output',skips,'--step',step)
            result=json.loads(run('search','--profile','--index',index,'--skip-index',skips,'--query','common && rare').stdout)
            self.assertEqual([r['id'] for r in result['results']],['900','950','999'])
            self.assertGreater(result['jumps'],0)
            baseline=json.loads(run('search','--profile','--index',index,'--query','common && rare','--no-skips').stdout)
            self.assertEqual(result['results'],baseline['results'])
            self.assertLess(result['comparisons'],baseline['comparisons'])

    def test_phrase_against_independent_sequence_oracle(self):
        rng=random.Random(1012)
        texts=[rng.choices(['alpha','beta','and','gamma'],k=rng.randrange(1,18)) for _ in range(200)]
        source=self.folder/'oracle.tsv'
        source.write_text(''.join(f'{i}\t{" ".join(words)}\tDoc {i}\t\n' for i,words in enumerate(texts)),encoding='utf-8')
        index=self.folder/'oracle.ir'
        coords=self.folder/'oracle.positions'
        run('build','--inline','--manifest',source,'--index',index)
        run('coordinates','--input',source,'--index',index,'--output',coords)
        phrases=[['alpha','beta'],['alpha','and','beta'],['alpha','alpha'],['gamma','beta','alpha'],['alpha','missing']]
        for words in phrases:
            expected=sorted(str(i) for i,text in enumerate(texts) if any(text[j:j+len(words)]==words for j in range(len(text)-len(words)+1)))
            phrase='"'+' '.join(words)+'"'
            answer=json.loads(run('search','--index',index,'--positions',coords,'--query',phrase,'--limit','1000').stdout)
            self.assertEqual([r['id'] for r in answer['results']],expected)

    def test_skip_checksum_rejects_changed_point(self):
        source=self.folder/'crc.tsv'
        source.write_text(''.join(f'{i}\talpha beta\tDoc {i}\t\n' for i in range(300)),encoding='utf-8')
        index=self.folder/'crc.ir'
        skips=self.folder/'crc.skips'
        run('build','--inline','--manifest',source,'--index',index,'--compressed')
        run('make-skips','--index',index,'--output',skips)
        data=bytearray(skips.read_bytes())
        data[-16]^=1
        skips.write_bytes(data)
        result=run('search','--index',index,'--skip-index',skips,'--query','alpha && beta',check=False)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('checksum',result.stderr)

    def test_long_snippet_is_bounded_and_keeps_match(self):
        text=' '.join(['контекст']*100+['needle']+['продолжение']*100)
        source=self.folder/'long_snippet.tsv'
        source.write_text('1\t'+text+'\tExample\t\n',encoding='utf-8')
        index=self.folder/'long_snippet.ir'
        offsets=self.folder/'long_snippet.offsets'
        run('build','--inline','--manifest',source,'--index',index)
        run('text-offsets','--input',source,'--index',index,'--output',offsets)
        result=json.loads(run('search','--index',index,'--texts',source,'--text-offsets',offsets,'--query','needle').stdout)
        row=result['results'][0]
        snippet=''.join(p['text'] for p in row['snippet_parts'])
        self.assertLessEqual(len(snippet),280)
        self.assertTrue(any(p['match'] and p['text']=='needle' for p in row['snippet_parts']))
        self.assertTrue(row['snippet_prefix'])
        self.assertTrue(row['snippet_suffix'])


if __name__=='__main__':
    unittest.main(verbosity=2)
