// Пояснение к файлу: Небольшой AND-поиск по файловым токенам для эксперимента ЛР4.
// Как читать: Сравнение запросов с терминами документов до/после стемминга. Основной поиск большого корпуса реализован в src/ir/engine.cpp.
// Команды запуска и структура проекта: README.md.

// Последовательный AND-поиск по токенам/стеммам. STL и индекс отсутствуют.
#include <cstdio>
#include <cstdlib>
#include <cstring>
// Читаем строку, при необходимости увеличиваем буфер через realloc.
static char *read_line(FILE *f) {
 size_t cap=128,n=0; char *p=static_cast<char*>(std::malloc(cap));
 if(!p) {std::fprintf(stderr,"Out of memory\n");std::exit(1);}
 int c;
 while((c=std::fgetc(f))!=EOF && c!='\n') {
  if(n+1>=cap) {cap*=2; char *q=static_cast<char*>(std::realloc(p,cap));if(!q){std::free(p);std::exit(1);}p=q;}
  p[n++]=static_cast<char>(c);
 }
 if(c==EOF && n==0){std::free(p);return nullptr;}
 if(n && p[n-1]=='\r') --n;
 p[n]=0;return p;
}

// Точка входа: проверяем аргументы, открываем вход и выход.
int main(int argc,char **argv) {
 if(argc!=4){std::fprintf(stderr,"Usage: search FILELIST QUERY_TOKENS RESULTS\n");return 1;}
 FILE *q=std::fopen(argv[2],"rb");if(!q)return 1;
 char **terms=nullptr;size_t count=0;char *line;
 while((line=read_line(q))) {
  if(!*line){std::free(line);continue;}
  char **next=static_cast<char**>(std::realloc(terms,(count+1)*sizeof(char*)));
  if(!next) { std::exit(1); }
  terms=next;terms[count++]=line;
 }
 std::fclose(q);
 FILE *list=std::fopen(argv[1],"rb"),*out=std::fopen(argv[3],"wb");
 if(!list||!out)return 1;
 // Читаем каждый документ последовательно: поискового индекса здесь нет.
 while((line=read_line(list))) {
  FILE *doc=std::fopen(line,"rb");if(!doc)return 1;
  bool *found=static_cast<bool*>(std::calloc(count?count:1,sizeof(bool)));
  if(!found)std::exit(1);
  char *word;
  while((word=read_line(doc))) {
   for(size_t i=0;i<count;++i)if(std::strcmp(word,terms[i])==0)found[i]=true;
   std::free(word);
  }
  if(std::ferror(doc)) { return 1; }
  std::fclose(doc);
  // AND: документ подходит только при наличии всех терминов запроса.
  bool match=count>0;for(size_t i=0;i<count;++i)if(!found[i])match=false;
  if(match)std::fprintf(out,"%s\n",line);
  std::free(found);std::free(line);
 }
 bool failed=std::ferror(list)||std::ferror(out);std::fclose(list);if(std::fclose(out))failed=true;
 for(size_t i=0;i<count;++i) { std::free(terms[i]); }
 std::free(terms);
 return failed?1:0;
}
