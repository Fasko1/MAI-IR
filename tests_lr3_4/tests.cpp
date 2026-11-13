// Пояснение к файлу: C++ исполнитель декларативных сценариев cases.json.
// Как читать: Собственный разбор JSON → подготовка файлов → запуск CLI → сравнение → TXT/JSON отчёт. Без STL и стороннего JSON-парсера.
// Команды запуска и структура проекта: README.md.

// Автотесты ЛР3–4: cases.json -> настоящие программы -> сравнение.
// Вся проверка на C++; без STL и сторонних JSON-библиотек.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdarg>
#include <cerrno>
#ifdef _WIN32
#include <windows.h>
#include <direct.h>
#else
#include <sys/stat.h>
#include <sys/wait.h>
#endif
static void die(const char*s) {
    std::fprintf(stderr,"Ошибка проверки: %s\n",s);
    std::exit(2);
}
static void* mem(void*p,size_t n) {
    void*q=std::realloc(p,n);
    if(!q)die("Нет памяти");
    return q;
}
struct Text {
    char*p;
    size_t n=0,cap=32;
    Text() {
        p=static_cast<char*>(mem(nullptr,cap));
        p[0]=0;
    }~Text() {
        std::free(p);
    }
    Text(const Text&)=delete;
    Text& operator=(const Text&)=delete;
    void add(char c) {
        if(n+2>cap) {
            cap*=2;
            p=static_cast<char*>(mem(p,cap));
        }p[n++]=c;
        p[n]=0;
    }
    void clear() {
        n=0;
        p[0]=0;
    }
};
struct Words {
    char**p=nullptr;
    size_t n=0;
    ~Words() {
        for(size_t i=0;i<n;++i)std::free(p[i]);
        std::free(p);
    }
    void add(const char*s) {
        p=static_cast<char**>(mem(p,(n+1)*sizeof(char*)));
        p[n]=static_cast<char*>(mem(nullptr,std::strlen(s)+1));
        std::strcpy(p[n++],s);
    }
};
// Own parser for the fixed schema. Reads JSON strings, arrays of strings and bool.
struct Json {
    const char*p;
    explicit Json(const char*s):p(s) {
    }
    void ws() {
        while(*p==' '||*p=='\n'||*p=='\r'||*p=='\t')++p;
    }
    bool take(char c) {
        ws();
        if(*p!=c)return false;
        ++p;
        return true;
    }
    void need(char c) {
        if(!take(c))die("Синтаксис cases.json");
    }
    unsigned hex() {
        unsigned v=0;
        for(int i=0;i<4;++i) {
            char c=*p;
            if(!c)die("Незавершённое unicode escape");
            ++p;
            v*=16;
            if(c>='0'&&c<='9')v+=c-'0';
            else if(c>='a'&&c<='f')v+=c-'a'+10;
            else if(c>='A'&&c<='F')v+=c-'A'+10;
            else die("Неверное unicode escape");
        }return v;
    }
    void utf8(Text&s,unsigned v) {
        if(!v)die("NUL не поддерживается");
        if(v<128)s.add(static_cast<char>(v));
        else if(v<2048) {
            s.add(static_cast<char>(192|(v>>6)));
            s.add(static_cast<char>(128|(v&63)));
        }else if(v<65536) {
            s.add(static_cast<char>(224|(v>>12)));
            s.add(static_cast<char>(128|((v>>6)&63)));
            s.add(static_cast<char>(128|(v&63)));
        }else {
            s.add(static_cast<char>(240|(v>>18)));
            s.add(static_cast<char>(128|((v>>12)&63)));
            s.add(static_cast<char>(128|((v>>6)&63)));
            s.add(static_cast<char>(128|(v&63)));
        }
    }
    void str(Text&s) {
        need('"');
        s.clear();
        while(*p&&*p!='"') {
            unsigned char c=*p++;
            if(c<32)die("Управляющий символ JSON");
            if(c!='\\') {
                s.add(static_cast<char>(c));
                continue;
            }char e=*p++;
            switch(e) {
                case '"':s.add('"');
                break;
                case '\\':s.add('\\');
                break;
                case '/':s.add('/');
                break;
                case 'n':s.add('\n');
                break;
                case 'r':s.add('\r');
                break;
                case 't':s.add('\t');
                break;
                case 'b':s.add('\b');
                break;
                case 'f':s.add('\f');
                break;
                case 'u': {
                    unsigned v=hex();
                    if(v>=0xD800&&v<=0xDBFF) {
                        if(p[0]!='\\'||p[1]!='u')die("Неверная суррогатная пара");
                        p+=2;
                        unsigned lo=hex();
                        if(lo<0xDC00||lo>0xDFFF)die("Неверный суррогат");
                        v=0x10000+((v-0xD800)<<10)+(lo-0xDC00);
                    }else if(v>=0xDC00&&v<=0xDFFF)die("Одиночный суррогат");
                    utf8(s,v);
                    break;
                }default:die("Неверное экранирование");
            }
        }need('"');
    }
    bool boolean() {
        ws();
        if(!std::strncmp(p,"true",4)) {
            p+=4;
            return true;
        }if(!std::strncmp(p,"false",5)) {
            p+=5;
            return false;
        }die("Ожидается bool");
        return false;
    }
    void words(Words&w) {
        need('[');
        if(take(']'))return;
        do {
            Text s;
            str(s);
            w.add(s.p);
        }while(take(','));
        need(']');
    }
};
struct Case {
    Text id,stage,name,input,note;
    Words expected;
    bool abbrevs=false,stopwords=false,demo=false,known=false;
    void parse(Json&j) {
        j.need('{');
        unsigned mask=0;
        do {
            Text key;
            j.str(key);
            j.need(':');
            unsigned b=0;
            if(!std::strcmp(key.p,"id")) {
                b=1;
                j.str(id);
            }else if(!std::strcmp(key.p,"stage")) {
                b=2;
                j.str(stage);
            }else if(!std::strcmp(key.p,"name")) {
                b=4;
                j.str(name);
            }else if(!std::strcmp(key.p,"input")) {
                b=8;
                j.str(input);
            }else if(!std::strcmp(key.p,"expected")) {
                b=16;
                j.words(expected);
            }else if(!std::strcmp(key.p,"note")) {
                b=32;
                j.str(note);
            }else if(!std::strcmp(key.p,"abbrevs")) {
                b=64;
                abbrevs=j.boolean();
            }else if(!std::strcmp(key.p,"stopwords")) {
                b=128;
                stopwords=j.boolean();
            }else if(!std::strcmp(key.p,"demo")) {
                b=256;
                demo=j.boolean();
            }else if(!std::strcmp(key.p,"known_issue")) {
                b=512;
                known=j.boolean();
            }else die("Неизвестное поле JSON");
            if(mask&b)die("Повторное поле JSON");
            mask|=b;
        }while(j.take(','));
        j.need('}');
        if((mask&63)!=63||!*id.p)die("Не все обязательные поля");
        if(std::strcmp(stage.p,"tokens")&&std::strcmp(stage.p,"stems"))die("Неизвестный stage");
    }
};
static bool read(const char*path,Text&s) {
    FILE*f=std::fopen(path,"rb");
    if(!f) {
        if(errno==ENOENT)return false;
        die("Открытие файла");
    }s.clear();
    int c;
    while((c=std::fgetc(f))!=EOF) {
        if(!c)die("NUL в файле");
        s.add(static_cast<char>(c));
    }bool bad=std::ferror(f);
    std::fclose(f);
    if(bad)die("Чтение файла");
    return true;
}
static void write(const char*path,const char*s) {
    FILE*f=std::fopen(path,"wb");
    if(!f)die("Создание файла");
    size_t n=std::strlen(s);
    bool bad=std::fwrite(s,1,n,f)!=n;
    if(std::fclose(f))bad=true;
    if(bad)die("Запись файла");
}
static void directory(const char*p) {
#ifdef _WIN32
    int r=_mkdir(p);
#else
    int r=mkdir(p,0755);
#endif
    if(r&&errno!=EEXIST)die("Создание каталога");
}
static void erase(const char*p) {
    if(std::remove(p)&&errno!=ENOENT)die("Удаление прошлого результата");
}
static bool exists(const char*p) {
    FILE*f=std::fopen(p,"rb");
    if(!f)return false;
    std::fclose(f);
    return true;
}
static int launch(const char*cmd) {
    int r=std::system(cmd);
#ifndef _WIN32
    if(r==-1)return -1;
    if(WIFEXITED(r))return WEXITSTATUS(r);
    return 128+(WIFSIGNALED(r)?WTERMSIG(r):0);
#else
    return r;
#endif
}
static void lines(Text&s,Words&w) {
    Text word;
    for(size_t i=0;i<s.n;++i) {
        if(s.p[i]=='\n') {
            if(word.n&&word.p[word.n-1]=='\r')word.p[--word.n]=0;
            w.add(word.p);
            word.clear();
        }else word.add(s.p[i]);
    }if(word.n) {
        if(word.p[word.n-1]=='\r')word.p[--word.n]=0;
        w.add(word.p);
    }
}
// СРАВНЕНИЕ: число элементов, порядок, повторы и каждая строка.
// Здесь фактический ответ проверяется относительно ожидания из cases.json.
static bool compare_expected(const Words&a,const Words&b) {
    if(a.n!=b.n)return false;
    for(size_t i=0;i<a.n;++i)if(std::strcmp(a.p[i],b.p[i]))return false;
    return true;
}
static void quote(FILE*f,const char*s) {
    std::fputc('"',f);
    for(;*s;++s) {
        switch(*s) {
            case '"':std::fputs("\\\"",f);
            break;
            case '\\':std::fputs("\\\\",f);
            break;
            case '\n':std::fputs("\\n",f);
            break;
            case '\r':std::fputs("\\r",f);
            break;
            case '\t':std::fputs("\\t",f);
            break;
            default:if(static_cast<unsigned char>(*s)<32)std::fprintf(f,"\\u%04x",static_cast<unsigned char>(*s));
            else std::fputc(*s,f);
        }
    }std::fputc('"',f);
}
static void array(FILE*f,const Words&w) {
    std::fputc('[',f);
    for(size_t i=0;i<w.n;++i) {
        if(i)std::fputs(", ",f);
        quote(f,w.p[i]);
    }std::fputc(']',f);
}
static FILE*report,*json;
static void both(const char*fmt,...) {
    va_list a;
    va_start(a,fmt);
    std::vprintf(fmt,a);
    va_end(a);
    va_start(a,fmt);
    std::vfprintf(report,fmt,a);
    va_end(a);
}
static void show(FILE*f,const Case&c,const Words&actual,const char*status,int rc,bool output) {
    std::fprintf(f,"[%s] %s: %s\n  Вход: ",status,c.id.p,c.name.p);
    quote(f,c.input.p);
    std::fputs("\n  Ожидается: ",f);
    array(f,c.expected);
    std::fputs("\n  Получено:  ",f);
    array(f,actual);
    std::fprintf(f,"\n  Пояснение: %s\n  CLI exit: %d; выходной файл: %s\n\n",c.note.p,rc,output?"есть":"нет");
}
static int pass=0,fail=0,xfail=0,xpass=0;
// ГЛАВНАЯ ПРОВЕРКА: вход из cases.json -> настоящий CLI -> ответ -> сравнение.
// Копии правил токенизатора или стеммера здесь нет.
static void run_case(const Case&c,size_t index) {
    bool tok=!std::strcmp(c.stage.p,"tokens");
    const char*out=tok?"build/tests_lr3_4/work/tokens/1.tokens":"build/tests_lr3_4/work/actual.stems";
    write(tok?"build/tests_lr3_4/work/texts/1.txt":"build/tests_lr3_4/work/input.tokens",c.input.p);
    erase(out);
#ifdef _WIN32
    const char*t="build\\tests_lr3_4\\tokenization.exe",*s="build\\tests_lr3_4\\stemming.exe";
#else
    const char*t="./build/tests_lr3_4/tokenization.exe",*s="./build/tests_lr3_4/stemming.exe";
#endif
    char cmd[1200];
    if(tok)std::snprintf(cmd,sizeof cmd,"%s build/tests_lr3_4/work/texts build/tests_lr3_4/work/tokens %s > build/tests_lr3_4/work/cli.log 2>&1",t,c.abbrevs?"lab03/abbrevs.txt":"build/tests_lr3_4/work/empty_abbrevs.txt");
    else std::snprintf(cmd,sizeof cmd,"%s build/tests_lr3_4/work/input.tokens build/tests_lr3_4/work/actual.stems%s > build/tests_lr3_4/work/cli.log 2>&1",s,c.stopwords?" lab04/stop_words_ru.txt":"");
    int rc=launch(cmd);
    Text result;
    bool output=read(out,result);
    Words actual;
    lines(result,actual);
    // Пустой вход также обязан создать/перезаписать пустой выходной файл.
    bool ok=rc==0&&output;
    bool match=ok&&compare_expected(actual,c.expected);
    const char*status;
    // A launch/file error cannot be hidden as XFAIL.
    if(!ok) {
        status="FAIL";
        ++fail;
    }else if(c.known) {
        if(match) {
            status="XPASS";
            ++xpass;
        }else if(!std::strcmp(c.id.p,"S18") && actual.n==0) {
            // Для S18 известен именно пустой ответ, а не любое несовпадение.
            status="XFAIL";
            ++xfail;
        }else {
            status="FAIL";
            ++fail;
        }
    }else if(match) {
        status="PASS";
        ++pass;
    }else {
        status="FAIL";
        ++fail;
    }
    show(stdout,c,actual,status,rc,output);
    show(report,c,actual,status,rc,output);
    if(rc) {
        Text err;
        if(read("build/tests_lr3_4/work/cli.log",err))both("Диагностика: %s\n",err.p);
    }
    if(index)std::fputs(",\n",json);
    std::fputs("{\"id\":",json);
    quote(json,c.id.p);
    std::fputs(",\"status\":",json);
    quote(json,status);
    std::fputs(",\"input\":",json);
    quote(json,c.input.p);
    std::fputs(",\"expected\":",json);
    array(json,c.expected);
    std::fputs(",\"actual\":",json);
    array(json,actual);
    std::fprintf(json,",\"returncode\":%d,\"output_exists\":%s}",rc,output?"true":"false");
}
int main(int argc,char**argv) {
#ifdef _WIN32
    SetConsoleOutputCP(CP_UTF8);
#endif
    bool demo=argc==2&&!std::strcmp(argv[1],"--demo");
    if(argc!=1&&!demo) {
        std::fprintf(stderr,"Usage: tests.exe [--demo]\n");
        return 2;
    }
    if(!exists("lab03/abbrevs.txt")||!exists("lab04/stop_words_ru.txt")||!exists("build/tests_lr3_4/tokenization.exe")||!exists("build/tests_lr3_4/stemming.exe"))die("Запусти скрипт сборки из проекта");
    directory("build/tests_lr3_4/work");
    directory("build/tests_lr3_4/work/texts");
    directory("build/tests_lr3_4/work/tokens");
    directory("tests_lr3_4/results");
    write("build/tests_lr3_4/work/empty_abbrevs.txt","");
    Text source;
    if(!read("tests_lr3_4/cases.json",source))die("Нет cases.json");
    const char*start=source.p;
    if(source.n>=3&&static_cast<unsigned char>(start[0])==239&&static_cast<unsigned char>(start[1])==187&&static_cast<unsigned char>(start[2])==191)start+=3;
    // Validate whole JSON and unique IDs before executing any test.
    {
        Json j(start);
        j.need('[');
        if(j.take(']'))die("Нет тестов");
        Words ids;
        do {
            Case c;
            c.parse(j);
            for(size_t i=0;i<ids.n;++i)if(!std::strcmp(ids.p[i],c.id.p))die("Повторный ID");
            ids.add(c.id.p);
        }while(j.take(','));
        j.need(']');
        j.ws();
        if(*j.p)die("Лишние данные после JSON");
    }
    const char*txt=demo?"tests_lr3_4/results/demo.txt":"tests_lr3_4/results/test_results.txt";
    const char*js=demo?"tests_lr3_4/results/demo.json":"tests_lr3_4/results/test_results.json";
    report=std::fopen(txt,"wb");
    json=std::fopen(js,"wb");
    if(!report||!json)die("Не удалось создать отчёт");
    both("C++ автотесты ЛР3–4 через CLI; режим: %s\n\n",demo?"demo":"полный");
    std::fputs("{\"runner\":\"C++ CLI\",\"cases\":[\n",json);
    Json j(start);
    j.need('[');
    size_t n=0;
    do {
        Case c;
        c.parse(j);
        if(!demo||c.demo)run_case(c,n++);
    }while(j.take(','));
    j.need(']');
    both("Итог: PASS=%d, FAIL=%d, XFAIL=%d, XPASS=%d\n",pass,fail,xfail,xpass);
    both("Проверено: %zu. XFAIL — известный дефект, не успешный тест.\n",n);
    std::fprintf(json,"\n],\"summary\":{\"PASS\":%d,\"FAIL\":%d,\"XFAIL\":%d,\"XPASS\":%d}}\n",pass,fail,xfail,xpass);
    bool bad=std::ferror(report)||std::ferror(json);
    if(std::fclose(report))bad=true;
    if(std::fclose(json))bad=true;
    if(bad)die("Ошибка записи отчёта");
    std::printf("Результаты: %s\n",txt);
    if(!n)die("Не выбраны тесты");
    return fail||xpass?1:0;
}
