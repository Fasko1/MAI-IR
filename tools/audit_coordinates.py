"""Независимый аудит всех сохранённых координат и статистика плотности.

Это проверка готовых бинарных данных, не реализация индекса/поиска на Python.
NumPy обрабатывает ограниченный блок; весь корпус в память не загружается.
"""
import json
from pathlib import Path
import struct
import time
import numpy as np

ROOT=Path(__file__).resolve().parents[1]


def u64(f):
    return struct.unpack('<Q',f.read(8))[0]


# Независимый проход блоками: проверить порядок, docID, позиции и число пар термин–документ.
def main():
    started=time.perf_counter()
    with (ROOT/'data/advanced/index.ir').open('rb') as index:
        index.seek(24)
        documents=u64(index)
        lengths=np.empty(documents,dtype=np.uint64)
        index.seek(96)
        for i in range(documents):
            for _ in range(3):
                size=struct.unpack('<I',index.read(4))[0]
                index.seek(size,1)
            lengths[i]=u64(index)
            index.seek(8,1)
    pairs=0
    occurrences=0
    with (ROOT/'data/advanced/positions.ir').open('rb') as f:
        assert f.read(8)==b'MAICOOR2'
        binding=u64(f)
        assert u64(f)==documents
        terms,total=u64(f),u64(f)
        counts=np.empty(terms,dtype=np.uint64)
        following=0
        for i in range(terms):
            size=u64(f)
            assert size<=65536
            f.seek(size,1)
            offset,count=u64(f),u64(f)
            assert offset==following and count>0
            following+=count
            counts[i]=count
        assert following==total
        for count in counts:
            remaining=int(count)
            previous=None
            previous_doc=None
            while remaining:
                take=min(remaining,131072)
                data=f.read(take*8)
                assert len(data)==take*8
                values=np.frombuffer(data,dtype='<u8')
                if previous is not None:
                    assert int(values[0])>previous
                assert np.all(values[1:]>values[:-1])
                docs=values>>32
                positions=values & np.uint64(0xffffffff)
                assert np.all(docs<documents)
                assert np.all(positions<lengths[docs.astype(np.intp)])
                pairs+=int(np.count_nonzero(docs[1:]!=docs[:-1]))
                pairs+=previous_doc is None or int(docs[0])!=previous_doc
                previous=int(values[-1])
                previous_doc=int(docs[-1])
                remaining-=take
                occurrences+=take
        assert not f.read(1)
    assert occurrences==total==int(lengths.sum())
    result={'passed':True,'documents':documents,'surface_terms':terms,'coordinates':occurrences,
            'term_document_pairs':pairs,'mean_coordinates_per_term':occurrences/terms,
            'mean_coordinates_per_pair':occurrences/pairs,'seconds':time.perf_counter()-started}
    (ROOT/'results/advanced/coordinate_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
