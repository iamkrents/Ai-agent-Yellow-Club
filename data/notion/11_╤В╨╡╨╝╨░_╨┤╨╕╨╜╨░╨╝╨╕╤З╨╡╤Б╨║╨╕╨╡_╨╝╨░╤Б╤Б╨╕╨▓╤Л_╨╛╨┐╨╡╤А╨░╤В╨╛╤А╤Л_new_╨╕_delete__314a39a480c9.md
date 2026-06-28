# 11 тема - Динамические массивы. Операторы new и delete

Источник: Notion
Notion page ID: 314a39a4-80c9-801f-a63f-c675cffcd178
Notion URL: https://app.notion.com/p/11-new-delete-314a39a480c9801fa63fc675cffcd178
Notion last edited: 2026-02-27T08:15:00.000Z
Путь Notion: 30ca39a4 / Продукт / Программа обучения / Темы / 11 тема - Динамические массивы. Операторы new и delete

<callout icon="🎯" color="gray_bg">
	**Цель**
	<empty-block/>
</callout>
<callout icon="🔨" color="gray_bg">
	**Задачи**
	<empty-block/>
</callout>
<callout icon="✅" color="gray_bg">
	**Ожидаемый результат**
	<empty-block/>
</callout>
<callout icon="📃" color="gray_bg">
	**План**
	<table_of_contents color="gray"/>
</callout>
## Проблема обычных массивов
<columns>
	<column>
		Ранее мы изучали такую структуру данных, как массивы. Создавали мы их так: `int arr[5];`
		Это означает, что размер массива известен заранее - ещё на этапе компиляции. Под массив выделяется фиксированный участок памяти, который нельзя изменить во время работы программы
	</column>
	<column>
		![](https://prod-files-secure.s3.us-west-2.amazonaws.com/516b4608-c37a-4154-91ab-b5a5e820e71e/e98bb7bb-eb76-4528-a225-722c276b5472/image.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466R2SM45M5%2F20260615%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260615T183335Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEJv%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEaCXVzLXdlc3QtMiJHMEUCIQC9hFh9ViaMJC%2BWFYChzFeO3Xewdp8XVk9N0yGUwX9PkgIgTfnUW4l6zZSF1U4Zfex7tJQucqPhkHIU%2Ft2J315TF4Yq%2FwMIZBAAGgw2Mzc0MjMxODM4MDUiDArS374h1Se7%2Ft%2FmjyrcA%2BbJ%2B1C2eCye53lm%2BSHBSBMUwJwsJXdS0zYSI2bhNU5XN1spE%2BdRMl8SPQlT1dV103ecdoKd%2FlCOAxzCQzE8iTtCgTrnKKmv4sREib7qHX5vy8O62YHN%2BaKNs6MyOMCTBGOA3M7nVwnsLPrwNzDwoZiFkgTlIelv19g%2Bt18Zvb36unVquHCeeXE1%2BKdIupjxxgCHLvpNPQVqGWL2%2BrcA23aPLUBfsvAW76B0zTTZv%2F9P2c49m%2FUpCyC5E1RC7mReHZnXQaHtQ8CBbRMgYHxgyoDMVko7xNWKHi2LT0NEsrhBpgfpByXTcjSNvvkugmCALxlrRaBOh%2F4wNB72l4DXEtsj4Fz9KUsBB%2BlG5%2B2oFdkWG8U3xLcH2WdB4ou94%2BpCqgc04mTaNNZmSC%2B8MrRzCyfkmYvLmvw%2F3n5jiT%2FCYOHJrCn6HYFJaxPN3v0D6ssI17U314dDahF2UEpPJKw3XyPF4SHlAuPERgpav3FPHqtjeoWE5cIYYYZAgNPPLkOI4Eeq7h3AO9vZee%2FR7s5n7wzhLEJYTt5kXRHQGAvuovcpXwSlTv2giv6WftY%2FDgd5tPMg2tyMF3QORcBteaKmWCOy%2F7K%2Fq3F2B1mTpam8ACtOyNs4j7gmHmJVVusjMMmIwdEGOqUBHBZO7a0E3lqgwWgKHdamEdSwQEwlWYgY5T6gbE3zUiAAJK19XIX95lleRtSTK2062YEUb4VReVNbA6M2oaoxrDRYqmZGiuexBZHrsg0rXjEDDManzO79Xm5zvJhVdr9Tv0p0SgNSBzo%2FPD%2F0AwwpfMnmWmErBI8lG7jeLkubSDbo%2BT6cA%2B%2FU6qBtAlEfKVPVMG9q9104T5gQxvloFqBuAZhNYm4S&X-Amz-Signature=55c686a2148b33be085da036f97acc00948c607b8e080c9fd9df034c6856409b&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
	</column>
</columns>
Мы не можем добавить или удалить элементы из такого массива, потому что память под него уже зарезервирована. Например, массив из пяти `int` занимает 20 байт памяти - ни больше, ни меньше (интовый массив из пяти элементов где каждый инт по 4 байта)
Элементы массива хранятся последовательно в памяти. Сразу после массива могут находиться другие данные программы, которые нельзя сдвинуть или переместить. Поэтому увеличить размер обычного массива невозможно
# Динамические массивы
Проблема обычных массивов заключается в том, что их размер фиксирован и должен быть известен заранее. Однако на практике часто бывает так, что мы **не знаем количество данных до запуска программы** или оно может меняться во время её работы.<br>Для решения этой проблемы в C++ существуют **динамические массивы**
## Почему массив называется динамическим
Динамический массив так называется по двум причинам:
1. Память под него выделяется **в динамической памяти**.
2. В отличие от статического массива, **его размер можно задать во время работы программы**, а не заранее.
Напомню:
у **статического массива** размер:
- задаётся заранее,
- должен быть **константой **(постоянным, мы не можем его изменять во время выполнения программы),
- и не может зависеть от пользовательского ввода.
С **динамическим массивом** всё иначе - его размер можно вычислить или ввести с клавиатуры прямо во время выполнения программы
## Связь: Указатели + Динамический массив
На прошлом занятии мы изучали указатели и работу с памятью. Мы узнали, что оперативная память делится на две основные области: **стек** и **кучу**.
В основном мы рассматривали работу указателей **в контексте стека** - то есть внутри нашей программы. Мы разбирали, как указатели могут хранить адреса переменных, как передавать параметры в функции по указателю и как изменять значения переменных через них.
Однако динамическую память мы тогда не затрагивали. Все примеры были связаны с уже существующими переменными, память под которые выделялась автоматически.
На этом занятии мы расширим наше понимание указателей и посмотрим, **как они используются для работы с динамической памятью**. Именно с помощью указателей в C++ создаются и используются динамические массивы. Указатель в этом случае хранит адрес не обычной переменной, а **первого элемента массива, размещённого в куче**
## Выделение памяти под динамический массив
Давайте создадим указатель `arr`. Он будет хранить адрес первого элемента динамического массива.
Чтобы выделить под него место в динамической памяти мы будем использовать оператор `new` далее указываем тип и квадратные скобки<br>Оператор `new` выделяет в динамической памяти массив элементов типа `int` и возвращает указатель на первый элемент этого массива.<br><br>Таким образом, указатель `arr` содержит адрес на первый элемент массива в выделенной памяти
```c++
int size = 5;
int *arr = new int[size];
/* Можно и внутри скобок указать размер, но в таком случае наш массив почти 
ничем не будет отличаться от обычного. Мы будем в процессе измнять его размер 
изменяя как раз таки саму переменную size (чего в обычных массивах, напоминаю, 
сделать нельзя было, так как там наш размер был константной, то есть постоянным */
```
Как это работает?
С помощью указателей у нас есть возможность попросить у системы выделить дополнительную память. Мы говорим “дай нам пж кусочек своей оперативки и мы сюда положим массивчик”. У нас будет указатель который указывает на место в оперативной памяти где лежит первый элемент нашего массива, динамического массива. Таким образом мы можем запрашивать у операционной системы дополнительную память по ходу выполнения нашей программы, если она нам нужна. И если в ОС эта память есть, то она нам её даст. И вот как раз для того чтобы запросить эту память мы используем оператор `new` 
![](https://prod-files-secure.s3.us-west-2.amazonaws.com/516b4608-c37a-4154-91ab-b5a5e820e71e/a151806f-458e-45e9-9401-1f4d5a23dc9e/image.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466R2SM45M5%2F20260615%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260615T183335Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEJv%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEaCXVzLXdlc3QtMiJHMEUCIQC9hFh9ViaMJC%2BWFYChzFeO3Xewdp8XVk9N0yGUwX9PkgIgTfnUW4l6zZSF1U4Zfex7tJQucqPhkHIU%2Ft2J315TF4Yq%2FwMIZBAAGgw2Mzc0MjMxODM4MDUiDArS374h1Se7%2Ft%2FmjyrcA%2BbJ%2B1C2eCye53lm%2BSHBSBMUwJwsJXdS0zYSI2bhNU5XN1spE%2BdRMl8SPQlT1dV103ecdoKd%2FlCOAxzCQzE8iTtCgTrnKKmv4sREib7qHX5vy8O62YHN%2BaKNs6MyOMCTBGOA3M7nVwnsLPrwNzDwoZiFkgTlIelv19g%2Bt18Zvb36unVquHCeeXE1%2BKdIupjxxgCHLvpNPQVqGWL2%2BrcA23aPLUBfsvAW76B0zTTZv%2F9P2c49m%2FUpCyC5E1RC7mReHZnXQaHtQ8CBbRMgYHxgyoDMVko7xNWKHi2LT0NEsrhBpgfpByXTcjSNvvkugmCALxlrRaBOh%2F4wNB72l4DXEtsj4Fz9KUsBB%2BlG5%2B2oFdkWG8U3xLcH2WdB4ou94%2BpCqgc04mTaNNZmSC%2B8MrRzCyfkmYvLmvw%2F3n5jiT%2FCYOHJrCn6HYFJaxPN3v0D6ssI17U314dDahF2UEpPJKw3XyPF4SHlAuPERgpav3FPHqtjeoWE5cIYYYZAgNPPLkOI4Eeq7h3AO9vZee%2FR7s5n7wzhLEJYTt5kXRHQGAvuovcpXwSlTv2giv6WftY%2FDgd5tPMg2tyMF3QORcBteaKmWCOy%2F7K%2Fq3F2B1mTpam8ACtOyNs4j7gmHmJVVusjMMmIwdEGOqUBHBZO7a0E3lqgwWgKHdamEdSwQEwlWYgY5T6gbE3zUiAAJK19XIX95lleRtSTK2062YEUb4VReVNbA6M2oaoxrDRYqmZGiuexBZHrsg0rXjEDDManzO79Xm5zvJhVdr9Tv0p0SgNSBzo%2FPD%2F0AwwpfMnmWmErBI8lG7jeLkubSDbo%2BT6cA%2B%2FU6qBtAlEfKVPVMG9q9104T5gQxvloFqBuAZhNYm4S&X-Amz-Signature=f113c9b1d19ef59249a6f629c8ad0a6814e8b651d2cc8e9ddf64a171e113e361&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
На картинке видно, что size и arr у нас лежат в стеке, а сам массив - в куче.
НО!
В языке C++ **нет автоматического сборщика мусора**, как, например, в Java или C#.
Это означает, что если мы выделяем память в динамической области, **никто кроме нас не позаботится о её освобождении**.
Если мы запросили у системы память с помощью оператора `new`, то именно мы обязаны освободить её, когда она больше не нужна.
На первых примерах это может казаться не критичным — ну подумаешь, выделили 20 байт памяти.
Однако представим ситуацию, когда в программе есть цикл с миллионом итераций, и в каждой итерации мы запрашиваем у операционной системы память под новые данные.
Если мы не будем освобождать ранее выделенную память, она будет накапливаться. В какой-то момент свободная оперативная память закончится, и программа может аварийно завершиться.
Только после завершения программы операционная система автоматически очистит всю память, которую она занимала. Но во время работы программы **эта ответственность полностью лежит на программисте**.
## Оператор `delete`
Чтобы освободить динамическую память, в C++ используется оператор `delete`.
Для динамического массива применяется специальная форма:
```c++
delete [] arr; // сам оператор delete + [](говорим что удаляем массив) + arr(название)
```
Этот оператор освобождает участок памяти в куче, который ранее был выделен с помощью `new`
Важное правило: использовали new - используй delete
Итого создание динамического массива выглядит так:
```c++
int size = 5;
// необязательно создавать переменную, массив у нас динамический, 
// а значит мы можем писать не просто int size = 5; а попросить ввести размер с консоли
// int size;
// cin >> size
int *arr = new int[size];
delete [] arr;
```
Выведем массив на экран:
```c++
int size = 5;

int *arr = new int[size];

for (int i = 0; i < size; i++) {
    cout << arr[i] << endl;
}

delete [] arr;
```
после выделения памяти массив **не инициализируется автоматически (помним о том, что при создании пустого статического массива пустые ячейки заполнялись нулями)**
Если вы сразу выведете его элементы - вы увидите **мусорные значения**, так как в этих ячейках раньше могли храниться любые данные.
Заполним массив
```c++
int size = 5;

int *arr = new int[size];

for (int i = 0; i < size; i++) {
    cin >> arr[i];
}

for (int i = 0; i < size; i++) {
    cout << arr[i] << endl;
}

delete [] arr;
```
## Динамический ввод размера массива
Так как массив динамический, мы можем запросить его размер у пользователя:
```c++
int size;
cout << "Введите размер массива: ";
cin >> size;

int* arr = new int[size];
```
Теперь программа может создать массив:
- на 5 элементов,
- на 11 элементов,
- на 55,
- хоть на 1 — главное, чтобы хватило **непрерывного участка памяти**.
## Почему важна непрерывность памяти
Для динамического массива требуется **не просто свободная память**, а **непрерывная область памяти**.
Если в памяти нет подходящего цельного блока нужного размера - программа не сможет выделить массив, и возникнет ошибка.
## Переполнение памяти и выход за границы массива
**Важный момент!**
C++ **никак не контролирует выход за границы массива.**
Если у вас массив из `size` элементов, допустимые индексы: 0 ... size -1
Но если вы по ошибке обратитесь к `arr[size]`, компилятор:
- **не выдаст ошибку**,
- а программа получит доступ к **чужой памяти**.
Это может привести к:
- странным значениям,
- падению программы,
- повреждению данных,
- трудноуловимым багам.
Поэтому **всегда внимательно следите за условиями в циклах**.
Можете проверить изменив в выводе запись `i < size` на `i <= size`
```c++
int size = 5;

int *arr = new int[size];

for (int i = 0; i < size; i++) {
    cin >> arr[i];
}

for (int i = 0; i < size; i++) {
    cout << arr[i] << endl;
}

delete [] arr;
```
![](https://prod-files-secure.s3.us-west-2.amazonaws.com/516b4608-c37a-4154-91ab-b5a5e820e71e/e8bb0b1e-2afe-4721-978b-f9d4d7a3a8d4/image.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466R2SM45M5%2F20260615%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260615T183335Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEJv%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEaCXVzLXdlc3QtMiJHMEUCIQC9hFh9ViaMJC%2BWFYChzFeO3Xewdp8XVk9N0yGUwX9PkgIgTfnUW4l6zZSF1U4Zfex7tJQucqPhkHIU%2Ft2J315TF4Yq%2FwMIZBAAGgw2Mzc0MjMxODM4MDUiDArS374h1Se7%2Ft%2FmjyrcA%2BbJ%2B1C2eCye53lm%2BSHBSBMUwJwsJXdS0zYSI2bhNU5XN1spE%2BdRMl8SPQlT1dV103ecdoKd%2FlCOAxzCQzE8iTtCgTrnKKmv4sREib7qHX5vy8O62YHN%2BaKNs6MyOMCTBGOA3M7nVwnsLPrwNzDwoZiFkgTlIelv19g%2Bt18Zvb36unVquHCeeXE1%2BKdIupjxxgCHLvpNPQVqGWL2%2BrcA23aPLUBfsvAW76B0zTTZv%2F9P2c49m%2FUpCyC5E1RC7mReHZnXQaHtQ8CBbRMgYHxgyoDMVko7xNWKHi2LT0NEsrhBpgfpByXTcjSNvvkugmCALxlrRaBOh%2F4wNB72l4DXEtsj4Fz9KUsBB%2BlG5%2B2oFdkWG8U3xLcH2WdB4ou94%2BpCqgc04mTaNNZmSC%2B8MrRzCyfkmYvLmvw%2F3n5jiT%2FCYOHJrCn6HYFJaxPN3v0D6ssI17U314dDahF2UEpPJKw3XyPF4SHlAuPERgpav3FPHqtjeoWE5cIYYYZAgNPPLkOI4Eeq7h3AO9vZee%2FR7s5n7wzhLEJYTt5kXRHQGAvuovcpXwSlTv2giv6WftY%2FDgd5tPMg2tyMF3QORcBteaKmWCOy%2F7K%2Fq3F2B1mTpam8ACtOyNs4j7gmHmJVVusjMMmIwdEGOqUBHBZO7a0E3lqgwWgKHdamEdSwQEwlWYgY5T6gbE3zUiAAJK19XIX95lleRtSTK2062YEUb4VReVNbA6M2oaoxrDRYqmZGiuexBZHrsg0rXjEDDManzO79Xm5zvJhVdr9Tv0p0SgNSBzo%2FPD%2F0AwwpfMnmWmErBI8lG7jeLkubSDbo%2BT6cA%2B%2FU6qBtAlEfKVPVMG9q9104T5gQxvloFqBuAZhNYm4S&X-Amz-Signature=41cf6d4b0cfe1fcc483d6201d8a579a57bc3a37e0d2d6a5dea6269f96fb368ee&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
## Изменение размера динамического массива
Рассмотрим код:
```c++
int size = 5;
int *arr = new int[size];


size = 10; // размер массива не изменился
```
Важно понимать:
- изменение переменной `size` **НЕ изменяет размер массива**
- массив по-прежнему состоит из 5 элементов
Мы всего лишь изменили число в переменной, **а память в куче осталась прежней**.
## Как на самом деле изменяют размер динамического массива
В C++ **нельзя расширить уже существующий динамический массив**.
Поэтому используется такой алгоритм:
1. Создать новый массив нужного размера
2. Скопировать данные из старого массива
3. Удалить старый массив
4. Переназначить указатель
## Пример: увеличение массива
Допустим, у нас есть массив из 5 элементов, и мы хотим увеличить его до 8.
```c++
int oldSize = 5;
int newSize = 8;


int *arr = new int[oldSize];


// заполним массив
for (int i = 0; i < oldSize; i++) {
	arr[i] = i + 1;
}


// 1. создаём новый массив
int *newArr = new int[newSize];


// 2. копируем старые данные
for (int i = 0; i < oldSize; i++) {
	newArr[i] = arr[i];
}

// 3. удаляем старый массив
delete [] arr;


// 4. переназначаем указатель
arr = newArr;
```
Теперь:
- `arr` указывает на массив из **8 элементов**
- первые 5 элементов сохранены
- последние 3 - пока неинициализированы
## Инициализация новых элементов
После увеличения массива новые ячейки содержат **мусорные значения**.
Их обязательно нужно заполнить:
```c++
for (int i = oldSize; i < newSize; i++) {
	arr[i] = 0;
}
```
## Уменьшение размера массива
Алгоритм **точно такой же**, только копируем меньше элементов.
```c++
int *newArr = new int[newSize];


for (int i = 0; i < newSize; i++) {
	newArr[i] = arr[i];
}


delete [] arr;
arr = newArr;
```
## Важный момент: обновляем размер
После изменения массива **обязательно обновляйте переменную размера**:
```c++
size = newSize;
```
Именно эта переменная используется в циклах и проверках границ.
## Частая ошибка
```c++
int* arr = new int[5];
arr = new int[10];
```
Почему это плохо:
- старая память на 5 элементов **потеряна**
- удалить её уже невозможно
- возникает **утечка памяти**
## Правильный подход
Всегда:
1. сохраняем старый указатель
2. удаляем старую память
3. только потом переназначаем
## Задачи на закрепление
<details>
<summary>Задача 1</summary>
	## Задача 1. «Удалённые уровни игры»
	Игра хранит очки игрока за каждый пройденный уровень.
	Но если уровень оказался багованным — его **удаляют**.
	**Условие:**
	- вводится `N` — количество уровней
	- вводятся `N` очков
	- затем вводится номер уровня `X`, который нужно удалить
	Требуется:
	- создать **новый массив на ****`N - 1`**** элементов**
	- скопировать все значения, **кроме X-го**
	- удалить старый массив
	Нумерация уровней — с **1**, а не с 0.
	```c++
int N;
cin >> N;

int* arr = new int[N];
for (int i = 0; i < N; i++) {
    cin >> arr[i];
}

int X;
cin >> X; // номер уровня (1-based)

int* newArr = new int[N - 1];

int j = 0;
for (int i = 0; i < N; i++) {
    if (i != X - 1) {
        newArr[j++] = arr[i];
    }
}

delete[] arr;
arr = newArr;
N--;

for (int i = 0; i < N; i++) {
    cout << arr[i] << " ";
}

delete[] arr;
	```
</details>
<details>
<summary>Задача 2</summary>
	## Задача 2. «Инвентарь выживания»
	У игрока есть инвентарь предметов (числа — ID предметов).
	Каждый раз, когда игрок находит **редкий предмет (ID = 777)**:
	- инвентарь **увеличивается в 2 раза**
	- новые ячейки заполняются `1`
	**Условие:**
	- вводится `N` и массив
	- если среди элементов есть `777`, выполнить расширение
	- если нет — ничего не делать
	Можно встретить **несколько 777**, но расширение выполняется **один раз**.
	```c++
int N;
cin >> N;

int* arr = new int[N];
bool found = false;

for (int i = 0; i < N; i++) {
    cin >> arr[i];
    if (arr[i] == 777) {
        found = true;
    }
}

if (found) {
    int newSize = N * 2;
    int* newArr = new int[newSize];

    for (int i = 0; i < N; i++) {
        newArr[i] = arr[i];
    }

    for (int i = N; i < newSize; i++) {
        newArr[i] = -1;
    }

    delete[] arr;
    arr = newArr;
    N = newSize;
}

for (int i = 0; i < N; i++) {
    cout << arr[i] << " ";
}

delete[] arr;
	```
</details>
<details>
<summary>Задача 3</summary>
	## Задача 6. «Экономия памяти»
	Дан массив чисел. Нужно удалить **все нули**.
	**Условие:**
	- вводится `N` и массив
	- количество нулей заранее неизвестно
	Требуется:
	1. посчитать, сколько нулей
	2. создать массив нужного размера
	3. скопировать только ненулевые элементы
	Запрещено:
	- использовать второй временный массив того же размера
	```c++
int N;
cin >> N;

int* arr = new int[N];
int zeroCount = 0;

for (int i = 0; i < N; i++) {
    cin >> arr[i];
    if (arr[i] == 0) {
        zeroCount++;
    }
}

int newSize = N - zeroCount;
int* newArr = new int[newSize];

int j = 0;
for (int i = 0; i < N; i++) {
    if (arr[i] != 0) {
        newArr[j++] = arr[i];
    }
}

delete[] arr;
arr = newArr;
N = newSize;

for (int i = 0; i < N; i++) {
    cout << arr[i] << " ";
}

delete[] arr;
	```
</details>
<callout icon="📝">
	**Компоненты для оформления**
	<callout icon="🧑‍🎓" color="gray_bg">
		Вопрос преподавателя
		<callout icon="🙋‍♂️" color="yellow_bg">
			Прогнозируемый ответ учеников.
		</callout>
	</callout>
	> *Пример*
	<callout icon="💡" color="gray_bg">
		Важное примечание
	</callout>
	<callout icon="☑️" color="gray_bg">
		Задание
	</callout>
	<callout icon="📂" color="gray_bg">
		Материалы
	</callout>
</callout>
