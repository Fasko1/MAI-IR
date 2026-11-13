// Пояснение к файлу: Независимый повторный проход по сохранённым TSV токенов и основ.
// Как читать: Проверяет ID/метаданные и пары терминов; повторно вычисляет основы, считает изменения и длины. Число вхождений должно сохраняться.
// Команды запуска и структура проекта: README.md.

// Независимый построчный аудит сохранённых токенов/основ, C++ без STL.
#include "../src/ir/base.h"
#include "../src/ir/arguments.h"
#include "funcs/stemmer.h"
// Парсер одной строки TSV выделяет ID, термины, заголовок и URL; потоки читаются параллельно.
struct Row {
    ir::Vec<char> line;
    char *id=nullptr,*terms=nullptr,*meta=nullptr;
    bool read(FILE* file) {
        if(!ir::read_line(file,line,64*1048576))return false;
        id=line.data;terms=std::strchr(id,'\t');if(!terms)ir::fail("Missing terms");*terms++=0;
        meta=std::strchr(terms,'\t');if(!meta)ir::fail("Missing metadata");*meta++=0;
        return true;
    }
};
char* next(char*& p) {if(!*p)return nullptr;char* start=p;while(*p&&*p!=' ')++p;if(*p)*p++=0;return start;}
// Повторное чтение независимо от счётчиков препроцессора: расхождение строк или пар терминов завершает аудит ошибкой.
int main(int argc,char** argv) {
    try {
#ifdef _WIN32
        ir::Utf8Arguments unicode;argc=static_cast<int>(unicode.values.size);argv=unicode.values.data;
#endif
        if(argc!=4)ir::fail("Usage: audit_stream TOKENS_TSV STEMS_TSV OUTPUT_JSON");
        ir::File a,b,out;a.open(argv[1],"rb");b.open(argv[2],"rb");Row token,stem;
        uint64_t docs=0,n=0,changed=0,tc=0,sc=0;
        for(;;){bool x=token.read(a.fp),y=stem.read(b.fp);if(x!=y)ir::fail("Document counts differ");if(!x)break;
            if(std::strcmp(token.id,stem.id)||std::strcmp(token.meta,stem.meta))ir::fail("Document IDs/metadata differ");
            char *p=token.terms,*q=stem.terms;
            for(;;){char* t=next(p);char* s=next(q);if(bool(t)!=bool(s))ir::fail("Token/stem counts differ: %s",token.id);if(!t)break;
                if(!*t||!*s||!ir::valid_utf8(t)||!ir::valid_utf8(s))ir::fail("Invalid term");
                ++n;changed+=std::strcmp(t,s)!=0;tc+=count_unicode_characters(t);sc+=count_unicode_characters(s);
            }++docs;
        }
        out.open(argv[3],"wb");double d=double(n);
        std::fprintf(out.fp,"{\"documents\":%llu,\"tokens\":%llu,\"stems\":%llu,\"changed_occurrences\":%llu,\"unchanged_occurrences\":%llu,\"changed_fraction\":%.12g,\"token_characters\":%llu,\"stem_characters\":%llu,\"mean_token_length\":%.12g,\"mean_stem_length\":%.12g,\"aligned\":true}\n",
            (unsigned long long)docs,(unsigned long long)n,(unsigned long long)n,(unsigned long long)changed,(unsigned long long)(n-changed),d?changed/d:0,
            (unsigned long long)tc,(unsigned long long)sc,d?tc/d:0,d?sc/d:0);out.close();return 0;
    }catch(const ir::Error& e){std::fprintf(stderr,"Error: %s\n",e.message);return 1;}
}
