// Пояснение к файлу: Основной потоковый препроцессор большого корпуса.
// Как читать: Одна строка texts.tsv → токены через callback → основы → две согласованные TSV-строки и счётчики. В памяти один документ.
// Команды запуска и структура проекта: README.md.

// Один документ в памяти; STL остаётся только внутри адаптера токенизации.
// Вход/выход: id TAB текст/термины TAB заголовок TAB URL, одна строка на документ.
#include "../src/ir/engine.h"
#include "../src/ir/arguments.h"
#include "../lab04/funcs/stemmer.h"
// Callback получает один токен, пишет его и его основу в согласованные позиции; счётчики собираются за тот же проход.
struct Output {
    FILE* tokens; FILE* stems;
    uint64_t count=0,changed=0,token_chars=0,stem_chars=0;
    bool first=true;
    static void emit(const char* text,void* user) {
        auto& out=*static_cast<Output*>(user);
        if(!out.first){std::fputc(' ',out.tokens);std::fputc(' ',out.stems);}out.first=false;
        std::fputs(text,out.tokens);
        char* stem=ir::copy_string(text);extract_stem(stem);
        if(!*stem){std::free(stem);ir::fail("Empty stem");}
        std::fputs(stem,out.stems);++out.count;
        out.changed+=std::strcmp(text,stem)!=0;
        out.token_chars+=count_unicode_characters(text);out.stem_chars+=count_unicode_characters(stem);
        std::free(stem);
    }
};
int main(int argc,char** argv) {
    try {
#ifdef _WIN32
        ir::Utf8Arguments unicode;argc=static_cast<int>(unicode.values.size);argv=unicode.values.data;
#endif
        if(argc!=6)ir::fail("Usage: preprocess_stream TEXTS_TSV TOKENS_TSV STEMS_TSV ABBREVS STATS_JSON");
        char error[768]={};if(!ir_normalizer_init(argv[4],nullptr,error,sizeof(error)))ir::fail("%s",error);
        ir::File input,tokens,stems;input.open(argv[1],"rb");tokens.open(argv[2],"wb");stems.open(argv[3],"wb");
        ir::Vec<char> line;Output out{tokens.fp,stems.fp};uint64_t docs=0,text_bytes=0;
        // На каждой итерации одна TSV-строка: проверка четырёх полей, обработка текста и перенос заголовка/URL без изменений.
        while(ir::read_line(input.fp,line,64*1048576)) {
            char* fields[4]={line.data,nullptr,nullptr,nullptr};size_t count=1;
            for(char* p=line.data;*p;++p)if(*p=='\t'){if(count==4)ir::fail("Too many TSV fields");*p=0;fields[count++]=p+1;}
            if(count!=4||!*fields[0]||!ir::valid_utf8(fields[1]))ir::fail("Invalid document record");
            std::fprintf(tokens.fp,"%s\t",fields[0]);std::fprintf(stems.fp,"%s\t",fields[0]);
            out.first=true;text_bytes+=std::strlen(fields[1]);
            if(!ir_tokenize(fields[1],Output::emit,&out,error,sizeof(error)))ir::fail("%s",error);
            std::fprintf(tokens.fp,"\t%s\t%s\n",fields[2],fields[3]);std::fprintf(stems.fp,"\t%s\t%s\n",fields[2],fields[3]);
            ++docs;if(docs%25000==0)std::fprintf(stderr,"Preprocessed %llu documents\n",static_cast<unsigned long long>(docs));
        }
        input.close();tokens.close();stems.close();
        ir::File stats;stats.open(argv[5],"wb");double n=double(out.count);
        std::fprintf(stats.fp,"{\"documents\":%llu,\"tokens\":%llu,\"stems\":%llu,\"text_bytes\":%llu,\"changed_occurrences\":%llu,\"unchanged_occurrences\":%llu,\"changed_fraction\":%.12g,\"token_characters\":%llu,\"stem_characters\":%llu,\"mean_token_length\":%.12g,\"mean_stem_length\":%.12g,\"aligned\":true}\n",
          (unsigned long long)docs,(unsigned long long)out.count,(unsigned long long)out.count,(unsigned long long)text_bytes,
          (unsigned long long)out.changed,(unsigned long long)(out.count-out.changed),n?out.changed/n:0,
          (unsigned long long)out.token_chars,(unsigned long long)out.stem_chars,n?out.token_chars/n:0,n?out.stem_chars/n:0);
        stats.close();return 0;
    }catch(const ir::Error& e){std::fprintf(stderr,"Error: %s\n",e.message);return 1;}
}
