# JS - 2 тема - Основы работы с JavaScript

Источник: Notion
Notion page ID: 1b3a39a4-80c9-80b6-9014-f36945f657c2
Notion URL: https://app.notion.com/p/JS-2-JavaScript-1b3a39a480c980b69014f36945f657c2
Notion last edited: 2026-02-18T15:49:00.000Z
Путь Notion: 30ca39a4 / Продукт / Программа обучения / Темы / JS - 2 тема - Основы работы с JavaScript

<callout icon="🎯" color="gray_bg">
	**Цель**
	Формирование базовых навыков работы с Javascript
</callout>
<callout icon="🔨" color="gray_bg">
	**Задачи**
	- организовать файловую структуру проекта
	- связать HTML и JavaScript файлы для обработки пользовательского ввода
	- познакомиться с переменными в JavaScript и научиться их использовать
	- разобрать код для сохранения пользовательского ввода
	- изучить функцию запуска алгоритма и настроить ее запуск
	- осуществить проверку введённых данных в форме авторизации
	- прописать логику проверки пароля
	- реализовать перенаправление после успешного входа
</callout>
<callout icon="📂" color="gray_bg">
	**Материалы**
	<file src="file://%7B%22source%22%3A%22attachment%3A26e5a14d-5389-4fdf-bf14-e6c0cb62d06c%3A%D0%BA%D0%BE%D0%B4_JS.txt%22%2C%22permissionRecord%22%3A%7B%22table%22%3A%22block%22%2C%22id%22%3A%221bba39a4-80c9-801e-9789-f8601973f5b9%22%2C%22spaceId%22%3A%22516b4608-c37a-4154-91ab-b5a5e820e71e%22%7D%7D"></file>
</callout>
<callout icon="✅" color="gray_bg">
	**Ожидаемый результат**
	JS
	```javascript
let username = "yellow" // cоздаем переменную и вносим туда данные
let password = "yellowclub"  // cоздаем переменную и вносим туда данные

function login() {
  let username_input = document.getElementById("username").value  // забираем значение введенного имя пользователя из input по id
  let password_input = document.getElementById("password").value  // забираем значение введенного пароля из input по id

	// проверяем совпадение введенного и сохраненного имени пользователя и пароля
  if (username_input == username && password_input == password) {
	  alert("Авторизация успешна!")
	  window.location.href = "../portfolio/index.html"
  } else {
    alert("Неверное имя пользователя или пароль!")
  }
}
	```
	HTML
	```html
<html>
    <head> 
        <title>Авторизация</title>
        <link href="img/cat.png" type="image/png" rel="icon">
        <link rel="stylesheet" href="styles.css">
    </head>
    <body>
        <div class="container">
            <h2>Авторизация</h2>
            <input id="username" placeholder="Введите логин" />                   <!--присваиваем id для input имени пользователя-->
            <input id="password" type="password" placeholder="Введите пароль" />  <!--присваиваем id для input пароля-->                              
            <button onclick="login()">Войти</button>                              <!--добавляем атрибут onclick для запуска функции login()-->
        </div>
        <script src="app.js"></script>                                            <!--связываем html и js файлы-->  
    </body>
</html>
	```
</callout>
<callout icon="📃" color="gray_bg">
	**План урок**
	<table_of_contents color="gray"/>
</callout>
# Видеоурок
Средняя версия с написанием и разбором построчно готового кода
<video src="https://youtu.be/qzOUimKk_sU"></video>
Минимальная версия с разбором готового кода
<video src="https://youtu.be/Cu5j8X-5Mw4"></video>
<empty-block/>
# **Файловая структура проекта**
<callout icon="☑️" color="gray_bg">
	Организовать корректную файловую структуру проекта.
</callout>
- делаем папку **portfolio **или** site** и перемещаем туда файлы **html** и **css**, в которых сделано портфолио или веб-страница про страну/игру
- делаем папку** login** и перемещаем туда файлы **html** и **css**, в которых сделана форма авторизации
- создаем файл **app.js** в папке **login**
# **Связывание HTML и JavaScript**
<callout icon="☑️" color="gray_bg">
	Связать файлы HTML и JavaScript.
</callout>
- добавляем функцию **alert()** в **js** файл для проверки связи
```javascript
alert("Связь выполнена!")
```
- связываем **html** и **js** файлы
```html
<html>
    <head> 
        <title>Авторизация</title>
        <link href="img/cat.png" type="image/png" rel="icon">
        <link rel="stylesheet" href="styles.css">
    </head>
    <body>
        <div class="container">
            <h2>Авторизация</h2>
            <input placeholder="Введите логин" />
            <input type="password" placeholder="Введите пароль" />                              
            <button>Войти</button>
        </div>
        <script src="app.js"></script>                                            <!--связываем html и js файлы-->  
    </body>
</html>
```
- запускаем **html** документ для проверки
- после успешной проверки комментируем строку с **alert()**
```javascript
// alert("Связь выполнена!")
```
# **Создание переменной для хранения логина**
<callout icon="☑️" color="gray_bg">
	Рассказать о переменных в JavaScript и научиться их использовать.
</callout>
- разбираем строку с переменной и вносим туда имя пользователя, с которым будем сверяться при авторизации
```javascript
// alert("Связь выполнена!")
let username = "yellow" // cоздаем переменную и вносим туда данные
```
- проверяем сохраненное имя пользователя и затем комментируем строку с **alert()**
```javascript
// alert("Связь выполнена!")
let username = "yellow" // cоздаем переменную и вносим туда данные
alert(username)
```
# **Получение пользовательского ввода из формы**
<callout icon="☑️" color="gray_bg">
	Разобрать код для сохранения пользовательского ввода.
</callout>
- разбираем строку с переменной для сохранения введенного имени пользователя
```javascript
// alert("Связь выполнена!")
let username = "yellow" // cоздаем переменную и вносим туда данные
// alert(username)

let username_input = document.getElementById("username").value    // забираем значение введенного имени пользователя из input по id
```
- присваиваем** id** для **input**
```html
<html>
    <head> 
        <title>Авторизация</title>
        <link href="img/cat.png" type="image/png" rel="icon">
        <link rel="stylesheet" href="styles.css">
    </head>
    <body>
        <div class="container">
            <h2>Авторизация</h2>
            <input id="username" placeholder="Введите логин" />                   <!--присваиваем id для input имени пользователя-->
            <input type="password" placeholder="Введите пароль" />                              
            <button>Войти</button>
        </div>
        <script src="app.js"></script>                                            <!--связываем html и js файлы-->  
    </body>
</html>
```
- проверяем сохранение имени пользователя с помощью **alert() **и понимаем, что ничего не сохранилось
```javascript
// alert("Связь выполнена!")
let username = "yellow" // cоздаем переменную и вносим туда данные
// alert(username)

let username_input = document.getElementById("username").value    // забираем значение введенного имя пользователя из input по id
alert(username_input)
```
# Реализация функции запуска кода
<callout icon="☑️" color="gray_bg">
	Разобрать функцию запуска алгоритма и настроить запуск функции.
</callout>
- добавляем функцию **login**
```javascript
// alert("Связь выполнена!")
let username = "yellow" // cоздаем переменную и вносим туда данные
// alert(username)

function login() {
	let username_input = document.getElementById("username").value    // забираем значение введенного имя пользователя из input по id
	alert(username_input)
}
```
- добавляем атрибут **onclick **к кнопке для запуска функции **login()**
```html
<html>
    <head> 
        <title>Авторизация</title>
        <link href="img/cat.png" type="image/png" rel="icon">
        <link rel="stylesheet" href="styles.css">
    </head>
    <body>
        <div class="container">
            <h2>Авторизация</h2>
            <input id="username" placeholder="Введите логин" />                   <!--присваиваем id для input имени пользователя-->
            <input type="password" placeholder="Введите пароль" />                              
            <button onclick="login()">Войти</button>                              <!--добавляем атрибут onclick для запуска функции login()-->
        </div>
        <script src="app.js"></script>                                            <!--связываем html и js файлы-->  
    </body>
</html>
```
- проверяем сохранение имени пользователя с помощью **alert()**
```javascript
// alert("Связь выполнена!")
let username = "yellow" // cоздаем переменную и вносим туда данные
// alert(username)

function login() {
	let username_input = document.getElementById("username").value    // забираем значение введенного имя пользователя из input по id
	alert(username_input)
}
```
# **Проверка введённых данных**
<callout icon="☑️" color="gray_bg">
	Настроить проверку введённых данных в форме авторизации.
</callout>
- добавляем условие с помощью оператора **if **и выводим информацию в случае совпадения сохраненного и введенного имени пользователя с помощью **alert()**
```javascript
// alert("Связь выполнена!")
let username = "yellow" // cоздаем переменную и вносим туда данные
// alert(username)

function login() {
	let username_input = document.getElementById("username").value    // забираем значение введенного имя пользователя из input по id

	// проверяем совпадение введенного и сохраненного имени пользователя
	if (username_input == username) {
		alert("Авторизация успешна!")
	}
}
```
- добавляем вывод информаци в случае несовпадения сохраненного и введенного имени пользователя с помощью оператора **else** и **alert()**
```javascript
// alert("Связь выполнена!")
let username = "yellow" // cоздаем переменную и вносим туда данные
// alert(username)

function login() {
	let username_input = document.getElementById("username").value   // забираем значение введенного имя пользователя из input по id
		
	// проверяем совпадение введенного и сохраненного имени пользователя
  if (username_input == username) {
	  alert("Авторизация успешна!")
	} else {
		alert("Неверное имя пользователя!")
	}
}
```
# Реализация кода для проверки пароля
<callout icon="☑️" color="gray_bg">
	Прописать логику проверки пароля.
</callout>
- аналогично делаем проверку для пароля
```javascript
// alert("Связь выполнена!")
let username = "yellow" // cоздаем переменную и вносим туда данные
// alert(username)
let password = "yellowclub"  // cоздаем переменную и вносим туда данные

function login() {
  let username_input = document.getElementById("username").value  // забираем значение введенного имя пользователя из input по id
  let password_input = document.getElementById("password").value  // забираем значение введенного пароля из input по id

	// проверяем совпадение введенного и сохраненного имени пользователя и пароля
  if (username_input == username && password_input == password) {
	  alert("Авторизация успешна!")
  } else {
    alert("Неверное имя пользователя или пароль!")
  }
}
```
- не забываем присвоить **id** для **input** пароля
```html
<html>
    <head> 
        <title>Авторизация</title>
        <link href="img/cat.png" type="image/png" rel="icon">
        <link rel="stylesheet" href="styles.css">
    </head>
    <body>
        <div class="container">
            <h2>Авторизация</h2>
            <input id="username" placeholder="Введите логин" />                   <!--присваиваем id для input имени пользователя-->
            <input id="password" type="password" placeholder="Введите пароль" />  <!--присваиваем id для input пароля-->                              
            <button onclick="login()">Войти</button>                              <!--добавляем атрибут onclick для запуска функции login()-->
        </div>
        <script src="app.js"></script>                                            <!--связываем html и js файлы-->  
    </body>
</html>
```
# **Перенаправление после успешного входа**
<callout icon="☑️" color="gray_bg">
	Реализовать перенаправление на другую страницу после успешной авторизации.
</callout>
- делаем переход к веб-странице при успешной авторизации и можем убрать закоментированные **alert()**
```javascript
let username = "yellow" // cоздаем переменную и вносим туда данные
let password = "yellowclub"  // cоздаем переменную и вносим туда данные

function login() {
  let username_input = document.getElementById("username").value  // забираем значение введенного имя пользователя из input по id
  let password_input = document.getElementById("password").value  // забираем значение введенного пароля из input по id

	// проверяем совпадение введенного и сохраненного имени пользователя и пароля
  if (username_input == username && password_input == password) {
	  alert("Авторизация успешна!")
	  window.location.href = "../portfolio/index.html"
  } else {
    alert("Неверное имя пользователя или пароль!")
  }
}
```
<callout icon="📝">
	**Компоненты для оформления урока**
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
	<callout icon="🔗" color="gray_bg">
		Отсылка к уроку
	</callout>
</callout>
