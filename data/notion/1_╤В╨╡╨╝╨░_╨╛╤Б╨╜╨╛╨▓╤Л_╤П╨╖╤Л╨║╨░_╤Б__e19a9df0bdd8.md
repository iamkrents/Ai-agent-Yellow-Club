# 1 тема - Основы языка С

Источник: Notion
Notion page ID: e19a9df0-bdd8-41db-ad4c-5b034a1bdbf4
Notion URL: https://app.notion.com/p/1-e19a9df0bdd841dbad4c5b034a1bdbf4
Notion last edited: 2025-10-02T19:53:00.000Z
Путь Notion: 30ca39a4 / Продукт / Программа обучения / Темы / 1 тема - Основы языка С

<callout icon="🎯" color="gray_bg">
	**Цель**
	Познакомиться с языком С
</callout>
<callout icon="🔨" color="gray_bg">
	**Задача**
	- понять как и где используется язык C
	- скачать и установить среду разработки для языка C
	- познакомиться со средой разработки Visual Studio
	- познакомиться с кодом на языке C
</callout>
<callout icon="✅" color="gray_bg">
	**Ожидаемый результат урока**
	- установлена среда разработки Visual Studio
	- создан и настроен проект
	```c
// подключение библиотеки ввода и вывода данных
# include <stdio.h> 

int main(){                // точка входа  
	printf("Hello World");   // функция вывода
	
	return 0;                // завершение выполнения функции (скорректировать)
}
	```
</callout>
<callout icon="📂" color="gray_bg">
	**Материалы**
	<empty-block/>
</callout>
<callout icon="📃" color="gray_bg">
	**План урок**
	<table_of_contents color="gray"/>
</callout>
# Язык программирования Python
<callout icon="🧑‍🎓" color="gray_bg">
	Почему мы изучали Python?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Он легкий и понятный, один из самых используемых в мире.
	</callout>
</callout>
## Что это за язык и как он работает
<callout icon="💡" color="gray_bg">
	Вспомнить основные принципы работы на языке Python.
</callout>
## Интерпретаторы и компиляторы
<callout icon="🧑‍🎓" color="gray_bg">
	Кто помнит, что такое интерпретатор и компилятор и чем они отличаются?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Переводят код с языка программирования в двоичный.
	</callout>
</callout>
<callout icon="💡" color="gray_bg">
	Рассказать про интерпретаторы и компиляторы:
	- как работают
	- отличия
	- преимущества и недостатки
</callout>
# Язык программирования С
<callout icon="🧑‍🎓" color="gray_bg">
	А на каком языке написан Python?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Язык C.
	</callout>
</callout>
## Что это за язык и как он работает
Язык С является компилируемым языком.
<callout icon="💡" color="gray_bg">
	Рассказать вводную информацию про язык С:
	- для чего используется
	- преимущества/недостатки
</callout>
## Зачем его изучать
<callout icon="🧑‍🎓" color="gray_bg">
	Зачем изучать язык С?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Не знаем.
	</callout>
</callout>
Благодаря изучению этого языка вы сможете:
	- посмотреть на первоисточник языка Python
	- сравнить язык C и Python
	- поработать на более низком языке.
# Среда разработка на С
<callout icon="🧑‍🎓" color="gray_bg">
	Где удобнее всего писать код?
	<callout icon="🙋‍♂️" color="yellow_bg">
		В среде разработки.
	</callout>
</callout>
Весь комплекс программ называется IDE — интегрированная среда разработки. Это система тесно взаимосвязанных программ, которые могут выглядеть как одно приложение, из которого доступны все необходимые функции для работы над кодом.
## Visual Studio
<callout icon="🧑‍🎓" color="gray_bg">
	Где мы будем писать код на языке C?
	<callout icon="🙋‍♂️" color="yellow_bg">
		В какой-то среде разработки наподобие PyCharm.
	</callout>
</callout>
Visual Studio - среда разработки от Microsoft на языке C и других.
<callout icon="☑️" color="gray_bg">
	Скачать Visual Studio с официального сайта.
</callout>
## Создание и настройка проекта
<callout icon="☑️" color="gray_bg">
	Создать проект и настроить его по инструкции.
	<callout icon="🌐">
		[**Инструкция**](https://metanit.com/c/tutorial/1.4.php)
	</callout>
</callout>
<callout icon="💡" color="gray_bg">
	Если Visual Studio еще не установился, поработать в онлайн-редакторе.
	<callout icon="🌐" color="green">
		[**Онлайн-редактор**](https://www.onlinegdb.com/online_c_compiler)
	</callout>
</callout>
## Знакомство с кодом
Здесь уже написан определенный код. Давайте его разберем.
```c
#include <stdio.h>

int main()
{
    printf("Hello World");

    return 0;
}
```
<span color="blue">**\<stdio.h\>**</span> - библиотека для ввода/вывода данных
<span color="blue">**#include**</span> - конструкция для** **подключения библиотеки
<span color="blue">**int main()**</span> - точка входа в программу на языке С
<span color="blue">**printf()**</span> - функция вывода
<span color="blue">**"Hello world"**</span> - текст, который хотим вывести в консоль
<span color="blue">**return**</span> - оператор, который завершает выполнение функции.
# Повторение
<callout icon="☑️" color="gray_bg">
	Подробно повторить пройденный материал.
</callout>
<callout icon="🧑‍🎓" color="gray_bg">
	Что вы запомнили?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Что такое язык C и как начать с ним работать.
	</callout>
</callout>
