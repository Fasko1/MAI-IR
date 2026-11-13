// Пояснение к файлу: Измерение времени самого C++ токенизатора.
// Как читать: Тексты читаются до измерения; повторные вызовы алгоритма дают время и число токенов. Файловый ввод не входит во внутренний замер.
// Команды запуска и структура проекта: README.md.

// Замеры только токенизации: тексты заранее загружены, чтение и запись не входят во время.
// Измеряем алгоритм на уже загруженном тексте; ввод-вывод в замер не входит.
#include <iostream>
#include <fstream>
#include <iterator>
#include <vector>
#include <algorithm>
#include <chrono>
#include <iomanip>
#include "funcs/tokenizer.h"
// Точка входа программы. Аргументы передаются при запуске из терминала.
int main(int argc,char **argv) {
 if(argc!=4) return 1;
 std::ifstream list(argv[1]),abbr(argv[2]);std::ofstream out(argv[3]);
 if(!list||!abbr||!out) return 1;
 std::vector<std::string> whitelist;std::string line,text;
 while(std::getline(abbr,line)) {if(!line.empty()&&line.back()=='\r')line.pop_back();if(!line.empty())whitelist.push_back(line);}
 while(std::getline(list,line)) {
  if(!line.empty()&&line.back()=='\r')line.pop_back();
  std::ifstream f(line,std::ios::binary);if(!f)return 1;
  text.append(std::istreambuf_iterator<char>(f),{});text+='\n';
 }
 if(text.empty()) return 1;
 out<<"input_bytes,repeat,seconds,tokens,average_token_length,kib_per_second\n";
 size_t previous=0;
 // Проверяем 10%, 25%, 50%, 75% и 100% загруженного корпуса.
 for(double fraction:{0.1,0.25,0.5,0.75,1.0}) {
  size_t n=static_cast<size_t>(text.size()*fraction);
  // Не разрезаем UTF-8 символ и слово в середине.
  if(n<text.size()) {size_t end=text.find_first_of(" \n\t\r",n);n=end==std::string::npos?text.size():end;}
  if(!n||n==previous) { continue; }
  previous=n;
  std::string sample=text.substr(0,n);
  split_into_tokens(sample,whitelist); // прогрев
  // Пять повторов на каждом размере; Python затем выберет медиану.
  for(int rep=1;rep<=5;++rep) {
   // Начало замера: только вызов split_into_tokens.
   auto start=std::chrono::steady_clock::now();
   auto tokens=split_into_tokens(sample,whitelist);
   double seconds=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
   size_t chars=0;for(const auto&t:tokens)chars+=utf8_length(t);
   out<<n<<','<<rep<<','<<std::setprecision(12)<<seconds<<','<<tokens.size()<<','
      <<(tokens.empty()?0.0:double(chars)/tokens.size())<<','<<(seconds>0?n/1024.0/seconds:0)<<'\n';
  }
 }
}
