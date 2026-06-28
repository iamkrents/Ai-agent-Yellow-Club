# 3 тема - Создание echo-bot

Источник: Notion
Notion page ID: 8df4e1bc-2051-4342-82c9-013e63e7c5b0
Notion URL: https://app.notion.com/p/3-echo-bot-8df4e1bc2051434282c9013e63e7c5b0
Notion last edited: 2026-04-28T08:05:00.000Z
Путь Notion: 30ca39a4 / Продукт / Программа обучения / Темы / 3 тема - Создание echo-bot

<callout icon="🎯" color="gray_bg">
	**Цель**
	Формирование навыков создания ботов в telegram и старта разработки
</callout>
<callout icon="🔨" color="gray_bg">
	**Задачи**
	- инициализировать бота
		- разобрать понятие бота
		- создать бота в telegram
		- создать бота в коде
		- соединить ботов с помощью токена
		- создать диспетчер
	- запустить бота с помощью асинхронной функции
		- разобрать понятие асинхронных функций
		- написать асинхронную функцию запуска
		- импортировать модуль для запуска асинхронных функций
		- запустить асинхронную функцию
	- создать функцию для взаимодействия с пользователем
		-  разобрать типы объектов в Aiogram
		- создать функцию для приема сообщений и ответ на них
		- добавить декоратор
		- подключить фильтры
</callout>
<callout icon="✅" color="gray_bg">
	**Ожидаемый результат урока**
	Каждый ученик реализовал echo-bot
</callout>
<callout icon="📂" color="gray_bg">
	**Материалы**
</callout>
<callout icon="📃" color="gray_bg">
	**План урок**
	<table_of_contents color="gray"/>
</callout>
# Видеоурок
<video src="https://youtu.be/eBW6XX7hrAY"></video>
# Создание бота
## Отличия бота в telegram и в коде
<callout icon="🧑‍🎓" color="gray_bg">
	Что такое бот в телеграме?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Специальный чат
	</callout>
	Что такое бот в коде?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Связующее звено между ботом в чате и кодом
	</callout>
</callout>
## Создание идеи бота
- придумать идею бота
## Создание бота в Telegram
- придумать идею бота
- botfather
## Создание бота в коде
- импорт бота из Aiogram
```python
from aiogram import Bot
```
## Соединение ботов
<callout icon="🧑‍🎓" color="gray_bg">
	Что такое токен?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Уникальный ключ для подключения бота в телеграме к коду
	</callout>
	Как получить токен?
	<callout icon="🙋‍♂️" color="yellow_bg">
		В @botfather
	</callout>
</callout>
```python
from aiogram import Bot
bot = Bot(token='6243605704:AAGgzm-snEoUUZzI9-tNqeNJA7NvNiGSdQU')
```
## Диспетчер
<callout icon="🧑‍🎓" color="gray_bg">
	Что такое бот в диспетчер?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Как диспетчер в реальной жизни, объект, который слушает, что происходит в боте и обрабатывает запросы
	</callout>
</callout>
Код для подключения диспетчера
```python
from aiogram import Dispatcher
```
# Создание функций запуска и запуск
## Асинхронные функции
<callout icon="🧑‍🎓" color="gray_bg">
	В чем отличие синхронных и асинхронных функций?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Синхронные могут выполняться только последовательно, асинхронные могут выполнять один процесс, пока ожидается результат другого процесса.
	</callout>
</callout>
## Асинхронная функция запуска
<callout icon="🧑‍🎓" color="gray_bg">
	Как запустить бота?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Написать асинхронную функцию
	</callout>
	Какой синтаксис у асинхронной функции?
	```python
async def название_функции():
	```
	Какое ключевое слово необходимо для выполнения функции? {color="gray_bg"}
	```python
await
	```
	Что означает <span color="blue">dp.start_polling(bot)</span> {color="gray_bg"}
	<callout icon="🙋‍♂️" color="yellow_bg">
		<span color="blue">dp</span> - комнда диспетчеру<br><span color="blue">start_polling - </span>начать прослушку<br><span color="blue">bot </span>- обращение к боту
	</callout>
</callout>
Асинхронная функция запуска
```python
async def main():                     # создание функции
    print("Бот запущен")
    await dp.start_polling(bot)       # команда диспетчеру начать прослушку
```
## Подключение библиотеки асинхронных функций
```python
import asyncio
```
## Запуск асинхронной функции
```python
asyncio.run(main())                   # запустить асинхронную функцию
```
# Взаимодействие с пользователем
## Типы объектов в aiogram
<callout icon="🧑‍🎓" color="gray_bg">
	Что будут присылать пользователи в бот?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Сообщения, текст, картинки.
	</callout>
	Какой тип объектов тогда понадобится для работы?
	<callout icon="🙋‍♂️" color="yellow_bg">
		message
	</callout>
</callout>
Message - это не просто текст сообщения. Это объект, который может содержать текст, id пользователя, время отправки сообщения и т.д.
Импорт типов aigoram в код
```python
from aiogram import types
```
## Прием сообщений и ответ на них
У бота есть 2 задачи:
- принимать сообщения
- обрабатывать сообщения
Асинхронная функция для отправки сообщения ботом пользователю
```python
async def echo(message: types.Message):   # указываем в скобках тип принимаемого объекта
    await message.answer("Бот находится в разработке.") # метод для ответа на сообщения
```
<callout icon="🧑‍🎓" color="gray_bg">
	Как бот поймет, что нужно запускать эту функцию?
	<callout icon="🙋‍♂️" color="yellow_bg">
		С помощью декоратора
	</callout>
</callout>
## Декоратор
<callout icon="🧑‍🎓" color="gray_bg">
	Что такое декоратор и как он записывается?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Инструмент для расширения возможностей функции.<br>Записывается через знак собачки - <span color="blue">@</span>
	</callout>
</callout>
В случае с ботом декоратор будет выступать триггером для выполнения функции ответа на сообщения пользователя.
```python
@dp.message( )  # декоратор, чтобы функция ниже сработала на любое сообщение от пользователя

async def echo(message: types.Message):   # указываем в скобках тип принимаемого объекта
    await message.answer("Бот находится в разработке.") # метод для ответа на сообщения
```
## Фильтры
<callout icon="🧑‍🎓" color="gray_bg">
	Все ли сообщения будут одинаковыми или их можно категоризировать?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Есть стартовое сообщение, которое выполняется автоматически
	</callout>
</callout>
Подключение фильтров для стартового сообщения
```python
from aiogram.filters import CommandStart
```
Код для ответа бота на стартовое сообщение
```python
@dp.message(CommandStart()) # декоратор, чтобы функция ниже сработала на сообщение /start для пользователя

async def start_cmd(message: types.Message):
    await message.answer("Привет! Это бот про персонажей Brawl Stars!")
```
<empty-block/>
<empty-block/>
<empty-block/>
<empty-block/>
