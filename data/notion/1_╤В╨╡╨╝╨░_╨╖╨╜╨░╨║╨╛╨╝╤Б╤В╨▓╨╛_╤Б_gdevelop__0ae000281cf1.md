# 1 тема - Знакомство с GDevelop

Источник: Notion
Notion page ID: 0ae00028-1cf1-4784-ae6c-d4f18a5643d5
Notion URL: https://app.notion.com/p/1-GDevelop-0ae000281cf14784ae6cd4f18a5643d5
Notion last edited: 2026-03-22T19:52:00.000Z
Путь Notion: 30ca39a4 / Продукт / Программа обучения / Темы / 1 тема - Знакомство с GDevelop

<callout icon="🎯" color="gray_bg">
	**Цель**
	Формирование навыков работы с GDevelop
</callout>
<callout icon="🔨" color="gray_bg">
	**Задачи**
	- познакомиться с возможностями GDevelop и примерами игр
	- изучить интерфейс редактора и его основные функции
	- освоить графический редактор Piskel
	- создать первые игровые объекты и настроить их поведение
</callout>
<callout icon="✅" color="gray_bg">
	**Ожидаемый результат**
	Ученик уверенно ориентируется в GDevelop и может создать базовую игровую сцену
</callout>
<callout icon="📂" color="gray_bg">
	**Материалы**
	[Платформа GDevelop](https://gdevelop.io)
</callout>
<callout icon="📃" color="gray_bg">
	**План**
	<table_of_contents color="gray"/>
</callout>
# Видеоурок
<video src="https://youtu.be/AX_OVBf4CP0"></video>
<empty-block/>
# Введение в Gdevelop
## Что такое GDevelop?
Для создания игр чаcто используются игровые движки:
- Unreal Engine - для 3D игр
- Unity - чаще для 2D игр
Мы же будем изучать платформу** **GDevelop. 
<callout icon="👨‍🎓" color="gray_bg">
	 Кто-нибудь знаком с GDevelop?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Нет(
	</callout>
</callout>
GDevelop - это бесплатный, открытый конструктор игр, не требующий навыков программирования. В нем много крутых возможностей, также он проще для начального изучения разработки игр. 
<callout icon="💡" color="gray_bg">
	GDevelop был создан в 2008 году Флорианом Рибо. С тех пор платформа постоянно развивается и совершенствуется сообществом разработчиков со всего мира.
</callout>
<callout icon="☑️" color="gray_bg">
	Установите и запустите программу:
	- скачайте GDevelop с официального сайта при необходимости
	- установите программу, следуя инструкции установщика, если она еще не установлена
	- запустите GDevelop
</callout>
## Примеры игр, созданных в GDevelop
<callout icon="👨‍🎓" color="gray_bg">
	 Какие игры можно создать в GDevelop?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Разные!
	</callout>
</callout>
В GDevelop можно создавать множество различных типов игр:
- платформеры
- головоломки
- шутеры
- бесконечные раннеры
- игры-квесты с исследованием мира
<callout icon="💡" color="gray_bg">
	Некоторые игры, созданные в GDevelop, имеют миллионы загрузок в Play Store и App Store!
</callout>
Вам предстоит понять с чем вы будете работать, какие механики можно использовать в игре. Поэтому давайте посмотрим несколько примеров игр, созданных в GDevelop.
<callout icon="☑️" color="gray_bg">
	Дайте ребятам 5-10 минут поиграть в различные игры:
	- откройте пункт меню “Играть”
	- выберите игру и запустите ее
</callout>
# Интерфейс и основные элементы
## **Создание проекта**
GDevelop предлагает несколько шаблонов для быстрого начала разработки:
- Пустой проект - с нуля, без предустановленных элементов
- Platformer - 2D платформер с физикой прыжков, гравитацией и взаимодействием с объектами
- Top-down - вид сверху (например, для RPG или шутеров), где персонаж двигается в разных направлениях
- Physics - игровая физика, где объекты взаимодействуют реалистично (падение, столкновение)
- 3D Platformer - трёхмерный платформер, похожий на классические игры жанра
- 3D First Person - игра от первого лица (например, шутеры или симуляторы)
Мы будем делать игру в жанре 2D платформер в стилистике PixelArt, но начнем с пустого проекта.
<callout icon="👨‍🎓" color="gray_bg">
	 Какие игры в такой стилистике вы знаете?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Terraria!
	</callout>
</callout>
<callout icon="☑️" color="gray_bg">
	Создайте пустой проект:
	- закройте все игры, вернитесь в основное меню 
	- выберите пункт меню “Создать”
	- нажмите кнопку “Создать новую игру”
	- выберите “Пустой проект”
	- выберите “Десктопный/ мобильный режим”
	- назовите проект своим именем
	- выберите, где будет хранится проект - Ваш компьютер
	- создайте папку на рабочем столе и назовите ее своим именем
	- вернитесь в GDevelop и выберите папку
	- поставьте галочку “Оптимизировать для Pixel Art”
	- нажмите кнопку “Создать новую игру”
</callout>
Перед нами открылся редактор. 
## Редактор сцены
По центру находится визуальный редактор, где мы будем размещать игровые объекты. Рамка - это стартовая область, с которой мы начнем разработку игровой локации.
**Навигация**
<callout icon="☑️" color="gray_bg">
	Разберите навигацию по редактору сцены:
	- для отдаления и приближения покрутите колёсико мышки
	- для перемещения по рабочей области
		- зажмите пробел и используйте левую кнопку мыши
		- зажмите колесико мыши
		- передвиньте ползунок внизу/сбоку окна редактора
</callout>
**Фон**
Сейчас у нас серый фон, но мы можем его менять.
<callout icon="☑️" color="gray_bg">
	Поменяйте цвет фона: 
	- нажмите правой кнопкой мышки на фон
	- выберите “Свойства сцены”
	- в поле “Изменить цвет фона” выберите цвет
</callout>
<callout icon="💡" color="gray_bg">
	Выбирайте спокойные тона, чтобы глаза не уставали.
</callout>
## Редактор событий
Сверху у нас есть 2 вкладки:
- “Безымянная сцена” - это окно редактора сцены, в котором мы сейчас и находимся
- “Безымянная сцена(События)” - это окно редактора событий сцены
<callout icon="☑️" color="gray_bg">
	Перейдите в редактор событий
</callout>
В редакторе событий мы будем программировать нашу игру. На данный момент тут пусто. Мы вернемся сюда, когда начнем изучать события.
## Верхняя область
Рядом с вкладками редактора сцены и редактора событий есть 2 кнопки:
- “Панель проекта” - иконка в виде 3 полосок - отображает структуру и настройки проекта
- “Меню приветствия” - иконка в виде домика - здесь можно создать новый проект или открыть существующий
<callout icon="☑️" color="gray_bg">
	Давайте назначим нашей сцене название:
	- выберите “Панель проекта” (иконка в виде 3 полосок)
	- найдите раздел “Сцены”
	- возле названия “Безымянная сцена” нажмите на 3 точки и выберете “Переименовать”
	- назовите сцену “Игровой уровень”
</callout>
Теперь вкладки редактора сцены и редактора событий имеют понятные названия.
## Дополнительные панели
Давайте теперь посмотрим на правую верхнюю сторону редактора сцены.
<callout icon="👨‍🎓" color="gray_bg">
	Как думаете, что там находится?
	<callout icon="🙋‍♂️" color="yellow_bg">
		…
	</callout>
</callout>
Здесь у нас есть несколько кнопок, открывающих дополнительные панели:
- “Панель объектов” - иконка в виде кубика
- “Панель групп объектов” - иконка в виде трех кубиков
- “Панель свойств” - иконка в виде карандаша
- “Панель списка экземпляров” - иконка в виде ромба и трех полосок
- “Панель слоев” - иконка в виде трех ромбов
Нам понадобятся в работе в основном только 2 панели:
- “Панель объектов” - тут будут находиться все объекты, которые мы будем использовать в игре
- “Панель свойств” - в этой панели мы будем редактировать свойства наших игровых объектов
<callout icon="☑️" color="gray_bg">
	Поработайте с открытием и закрытием панелей:
	- откройте все панели
	- закройте все панели, нажимая на крестик
	- откройте только “Панель объектов”
</callout>
Также правее есть несколько дополнительных кнопок:
- переключить/изменить сетку
- отменить последние изменения
- повторить последние изменения
- изменить масштаб редактора
- удалить выбранные экземпляры со сцены
- открыть настройки
Мы будем в дальнейшем активно использовать сетку, чтобы выставлять ровно объекты в игре.
## Важные кнопки
Давайте разберем еще 2 кнопки, которые мы будем использовать чаще всего.
- кнопка сохранения проекта - иконка дискеты - с помощью нее мы будем сохранять проект в конце каждого урока
- кнопка запуска предпросмотра - иконка треугольника - с помощью этой кнопки мы будем запускать игру
<callout icon="☑️" color="gray_bg">
	Найдите эти кнопки самостоятельно:
	- сохраните проект
	- запустите предпросмотр
</callout>
<callout icon="👨‍🎓" color="gray_bg">
	Получилось сохранить проект?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Да!
	</callout>
	А поиграть в игру?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Нет, ведь мы еще ничего в нее не добавили…
	</callout>
</callout>
# **Графический редактор Piskel**
Давайте создадим первый объект - персонажа. Мы будем рисовать его самостоятельно с помощью встроенного графического редактора Piskel.
В “Панели объектов” есть разделы “Глобальные объекты” и ”Объекты сцены”. 
<callout icon="👨‍🎓" color="gray_bg">
	Как думаете, чем они отличаются?
	<callout icon="🙋‍♂️" color="yellow_bg">
		“Глобальные объекты” - объекты, которые присутствуют во всех сценах, например, персонаж.
		”Объекты сцены” - объекты, которые присутствуют только в одной сцене, например, какая-то платформа.
	</callout>
</callout>
<callout icon="☑️" color="gray_bg">
	Добавьте первый объект:
	- нажмите кнопку “Добавить новый объект” - появляется список возможных типов объектов для добавления
	- выберите “Спрайт” - объект, который можно использовать для большинства элементов игры
	- переименуйте объект - назовите его “Персонаж”.
	- выберите “Создать с помощью Piskel”
</callout>
Открывыется встроенный графический редактор Piskel. 
<callout icon="💡" color="gray_bg">
	Редактором Piskel можно пользоваться в том числе отдельно в браузере. 
</callout>
<callout icon="👨‍🎓" color="gray_bg">
	На что похож этот графический редактор?
	<callout icon="🙋‍♂️" color="yellow_bg">
		На Photoshop!
	</callout>
</callout>
Верно, поэтому давайте сравним инструменты в этом редакторе с инструментами из Photoshop. 
## Основные инструменты
<callout icon="💡" color="gray_bg">
	На первом занятии достаточно изучить инструменты: 
	- ручка
	- ластик
	- заливка
	- прямоугольник
	- круг
	- рука
</callout>
**Инструмент “Ручка”**
<callout icon="👨‍🎓" color="gray_bg">
	На что похож первый инструмент?
	<callout icon="🙋‍♂️" color="yellow_bg">
		На ручку!
	</callout>
	Какой инструмент из Photoshop напоминает?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Кисть!
	</callout>
	Что с помощью него мы сможем делать?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Рисовать что-то!
	</callout>
</callout>
Давайте попробуем порисовать.
<callout icon="👨‍🎓" color="gray_bg">
	Какие свойства были у кисти в Photoshop? Что мы можем менять?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Размер, цвет!
	</callout>
	Как изменить размер ручки?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Вверху есть квадратики разного размера. Также можно использовать горячие клавиши Х (ха) и Ъ!
	</callout>
	А где можно изменить цвет ручки?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Внизу есть квадратик!
	</callout>
</callout>
**Инструмент “Заливка”**
<callout icon="👨‍🎓" color="gray_bg">
	Что делает инструмент “заливка”?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Закрашивает область каким-то одним цветом!
	</callout>
	А где можно изменить цвет?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Внизу есть квадратик.
	</callout>
</callout>
**Инструмент “Ластик”**
<callout icon="👨‍🎓" color="gray_bg">
	Что делает инструмент “Ластик”?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Стирает рисунок!
	</callout>
	Как изменить размер ластика?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Вверху есть квадратики разного размера. Также можно использовать горячие клавиши Х (ха) и Ъ.
	</callout>
</callout>
**Инструменты “Прямоугольник” и “Круг”**
<callout icon="👨‍🎓" color="gray_bg">
	Что делают инструменты  “Прямоугольник” и “Круг“?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Помогают рисовать ровные фигуры.
	</callout>
</callout>
**Инструмент “Рука”**
<callout icon="👨‍🎓" color="gray_bg">
	Что делает инструмент “рука”?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Перемещает объекты.
	</callout>
	Да, в отличие от аналогичного инструмента в Photoshop, рука здесь помогает передвигать объекты.
</callout>
Дополнительно можно рассмотреть оставшиеся инструменты:
- “Зеркальная ручка” - рисует зеркальные линии
- “Цветовая заливка” - позволяет закрашивать закрашивать участки одного цвета
- “Ровная линия” - помогает рисовать ровные линии
- “Волшебная палочка”, “Прямоугольное выделение”, “Лассо” - помогают выделять объекты на холсте
- “Осветление/затемнение” - помогает осветлить или затемнить участки на картинке, для затемнения нужно зажать Ctrl
## Работа с размерами
Давайте научимся управлять размером холста, так как нам далее понадобится менять размер объектов.
<callout icon="☑️" color="gray_bg">
	Справа найдите иконку квадрата со стрелкой наискось - “Resize”.
</callout>
Разберем параметры:
- Width - ширина холста
- Height - высота холста
- Maintain aspect ratio - позволяет сохранить пропорции изображения
- Resize canvas content - позволяет изменять размер изображения вместе с размером холста
<callout icon="☑️" color="gray_bg">
	Измените размер холста:
	- установить ширину - 100px
	- установите высоту - 100px
	- нажмите кнопку “Resize” для применения введенных значений
</callout>
# Создание персонажа и платформы
## Персонаж
<callout icon="☑️" color="gray_bg">
	Создайте тестового персонажа - Стикмена:
	- нарисуйте и раскрасьте персонажа
	- нажмите “Save” для сохранения рисунка
	- нажите “Применить” для сохранения изменений
	- вынесите персонажа из объектов в редактор сцены
</callout>
<callout icon="💡" color="gray_bg">
	Лучше всего рисовать по размеру холста, не оставляя расстояния снизу и сверху. 
</callout>
> *Пример*
	![](https://prod-files-secure.s3.us-west-2.amazonaws.com/516b4608-c37a-4154-91ab-b5a5e820e71e/8c16a2dd-a305-4d37-bca7-02a3f8fa4d6b/%D0%A1%D1%82%D0%B8%D0%BA%D0%BC%D0%B5%D0%BD.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466WDJGZCK3%2F20260615%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260615T183708Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEJv%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEaCXVzLXdlc3QtMiJIMEYCIQClhGsS96%2FcAeF%2B64uqS5WRrnKzmQHV%2FV4ytc%2FIWYcnIAIhANa5XSf0Or4awesBqCYFHMUNRQn1aG5gHEV8g1E0dDU4Kv8DCGMQABoMNjM3NDIzMTgzODA1Igzu2nYlRJuNmI%2BD23Qq3AOc9A8Wul66JXmwkd2XLGjeIcGVqcc%2Bxwp5tftMdDMm1GFGVddZ%2FtvFP0C%2BpJn%2FooPfqLONPjxAMmWiTOdLKGaIPbsxw1OsAGx5VJPTbgRkeTPQwGsO9UEN0ui2xDX03aE52Wtmvev%2F6Km62GwqmXnditMecFA1d%2Bg%2F%2FJcau0XPXJg7549fD2cyO1%2Fk3qHdyk3MmDhIGWLYi4YtLhHJjtWmlECJ9S1JsUGQyyGghw0S08rxVtWG7TFpGCbizNyHIVtl1PfxAk55OUb3sQgHpoiOA4QQeIIZKh5gsr000eYQ%2Fnwa7HDSL9gRsnnnuesX%2BGCReSZs4%2BYQr8GJAN6cibPHqTLwfPB2Awg3USzvYpHzIucp0ZSbO6uiaO0wXvMzF4IdvzQAxkVIxNq5yjQM%2FKyk%2FjkjiJRyqsA1w2AsaxYyGotGXsA5b2FmivEg50fH%2B%2FKg2eNBOBIzDumzd4I46cLLrJmj4BBRL5KMdXLyZg3bKCyIzr5GO2IuTDADqbUzDxL3K05IkcKWMxZMRAcZqUTFN7PLbCoq7taGXJTP2dsZT%2F77EFvrD1vTSVtTxEqoslSOHnpHrdJN806aOanLeJNJ7TwpHVrn2jguVIh3FKfYo4CukvTFAZN4%2BeAXPTCahsHRBjqkAWSaHef6YrV4%2BpuoUgLscz2vpvdDuUknndyUX7gYq%2FaJvLCG1haXqW2ijmjjri9%2BPDSPuVVIOt07bQA%2B5eD%2BdyyUtYeAQV31eXza33xqJ4381FWd8LQBxZMqIdAGS%2B7nNrJgZm6kDlbYHJ7aW3xvhEpMe55HCr5bunRZV9i4jJoZKe%2BzAvzgggnWHVLUqO1u3TS8I9Xj1JstZD2GFkjVZHMsSQ6H&X-Amz-Signature=8f5fc0c928b7685b00d3c040d3211db283a9f302c29339190d0ed207aff17e2e&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
<callout icon="💡" color="gray_bg">
	Если вы нажмете на персонажа в редакторе сцены, у вас появятся квадратики, с помощью которых можно увеличить его. Но нужно помнить, что это растровая графика, а значит будут растягиваться пиксели. Если вы хотите сделать персонажа больше, то стоит вернуться в редактор Piskel и изменить размер холста пройденным способом.
</callout>
## Поведение персонажа
<callout icon="☑️" color="gray_bg">
	Запустите предпросмотр игры.
</callout>
<callout icon="👨‍🎓" color="gray_bg">
	Персонаж может двигаться?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Нет(
	</callout>
</callout>
Сейчас наш персонаж - это просто картинка. Чтобы сделать из картинки настоящего игрового персонажа, нам понадобится назначить ему поведение.
<callout icon="☑️" color="gray_bg">
	Назначьте поведение персонажу:
	- нажмите два раза на персонажа
	- перейдите в раздел “Поведения”
	- нажмите “Добавить новое поведение”
</callout>
<callout icon="👨‍🎓" color="gray_bg">
	Как думаете, какое поведение понадобится для нашего персонажа?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Персонаж платформера!
	</callout>
</callout>
<callout icon="☑️" color="gray_bg">
	Назначьте поведение персонажу:
	- выберите поведение “Персонаж платформера”
	- нажмите “Применить”
	- запустите предпросмотр игры
</callout>
<callout icon="💡" color="gray_bg">
	Если свернуть окно предпросмотра, то вы не сможете запустить новое. Поэтому важно закрывать предпросмотр игры после каждого использования.
</callout>
<callout icon="👨‍🎓" color="gray_bg">
	Что произошло с персонажем?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Он упал(
	</callout>
</callout>
Теперь у наш персонаж подчиняется гравитации и падает вниз. Но если вы успеете нажать на стрелочки вправо-влево, то увидите, что наш он уже двигается. 
## Платформа
Чтобы наш персонаж не падал ему нужно на чём-то стоять. Поэтому нам нужно сделать платформу. 
<callout icon="☑️" color="gray_bg">
	Добавьте платформу:
	- нажмите “Добавить новый объект”
	- выберите “Спрайт”
	- переименуйте объект - “Платформа”
	- нажмите “Создать с помощью Piskel”.
	- полностью закрасьте хост
	- немного стилизуйте
	- нажмите “Save” 
	- нажмите “Применить”.
</callout>
> *Пример*
	![](https://prod-files-secure.s3.us-west-2.amazonaws.com/516b4608-c37a-4154-91ab-b5a5e820e71e/aad78ec0-21ee-4beb-a613-e5bfd2795fe2/%D0%A1%D0%BD%D0%B8%D0%BC%D0%BE%D0%BA_%D1%8D%D0%BA%D1%80%D0%B0%D0%BD%D0%B0_2025-03-20_%D0%B2_16.18.32.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466WDJGZCK3%2F20260615%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260615T183708Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEJv%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEaCXVzLXdlc3QtMiJIMEYCIQClhGsS96%2FcAeF%2B64uqS5WRrnKzmQHV%2FV4ytc%2FIWYcnIAIhANa5XSf0Or4awesBqCYFHMUNRQn1aG5gHEV8g1E0dDU4Kv8DCGMQABoMNjM3NDIzMTgzODA1Igzu2nYlRJuNmI%2BD23Qq3AOc9A8Wul66JXmwkd2XLGjeIcGVqcc%2Bxwp5tftMdDMm1GFGVddZ%2FtvFP0C%2BpJn%2FooPfqLONPjxAMmWiTOdLKGaIPbsxw1OsAGx5VJPTbgRkeTPQwGsO9UEN0ui2xDX03aE52Wtmvev%2F6Km62GwqmXnditMecFA1d%2Bg%2F%2FJcau0XPXJg7549fD2cyO1%2Fk3qHdyk3MmDhIGWLYi4YtLhHJjtWmlECJ9S1JsUGQyyGghw0S08rxVtWG7TFpGCbizNyHIVtl1PfxAk55OUb3sQgHpoiOA4QQeIIZKh5gsr000eYQ%2Fnwa7HDSL9gRsnnnuesX%2BGCReSZs4%2BYQr8GJAN6cibPHqTLwfPB2Awg3USzvYpHzIucp0ZSbO6uiaO0wXvMzF4IdvzQAxkVIxNq5yjQM%2FKyk%2FjkjiJRyqsA1w2AsaxYyGotGXsA5b2FmivEg50fH%2B%2FKg2eNBOBIzDumzd4I46cLLrJmj4BBRL5KMdXLyZg3bKCyIzr5GO2IuTDADqbUzDxL3K05IkcKWMxZMRAcZqUTFN7PLbCoq7taGXJTP2dsZT%2F77EFvrD1vTSVtTxEqoslSOHnpHrdJN806aOanLeJNJ7TwpHVrn2jguVIh3FKfYo4CukvTFAZN4%2BeAXPTCahsHRBjqkAWSaHef6YrV4%2BpuoUgLscz2vpvdDuUknndyUX7gYq%2FaJvLCG1haXqW2ijmjjri9%2BPDSPuVVIOt07bQA%2B5eD%2BdyyUtYeAQV31eXza33xqJ4381FWd8LQBxZMqIdAGS%2B7nNrJgZm6kDlbYHJ7aW3xvhEpMe55HCr5bunRZV9i4jJoZKe%2BzAvzgggnWHVLUqO1u3TS8I9Xj1JstZD2GFkjVZHMsSQ6H&X-Amz-Signature=e327e2f9fe326ec41b38f4ab9ad0e7a4e659c7bb9a833744e0db5b6d1959666a&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
## Поведение платформы
<callout icon="☑️" color="gray_bg">
	Поставьте блок платформы под Стикмена и снова запустите игру.
</callout>
<callout icon="👨‍🎓" color="gray_bg">
	Что произошло?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Человечек провалился(
	</callout>
	Что нужно сделать, чтобы он стоял на платформе?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Нужно назначить поведение!
	</callout>
	Какое поведение вы выберите?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Платформа!
	</callout>
</callout>
<callout icon="☑️" color="gray_bg">
	Назначьте поведение платфоме:
	- нажмите два раза на платформу
	- перейдите в раздел “Поведения”
	- нажмите “Добавить новое поведение”
	- выберите поведение “Платформа”
	- нажмите “Применить”
	- запустите предпросмотр игры
</callout>
<callout icon="👨‍🎓" color="gray_bg">
	Работает?
	<callout icon="🙋‍♂️" color="yellow_bg">
		Да!
	</callout>
</callout>
<callout icon="☑️" color="gray_bg">
	Сделайте мини-паркур:
	- расставьте блоки по сцене
	- попробуйте пройти, не упав в пропасть
</callout>
# Завершение проекта и подведение итогов
После завершения работы важно сохранить проект, а затем закрыть и отправить в облачное хранилище - Яндекс диск.
<callout icon="☑️" color="gray_bg">
	Завершите работу над проектом:
	- сохраните проект
	- закройте проект
	- загрузите в Яндекс-диск
</callout>
<callout icon="💡" color="gray_bg">
	Очень важно проверить, что все файлы загрузились в облачное хранилище!
</callout>
На следующем занятие мы распишем концепцию вашей игры.
<callout icon="☑️" color="gray_bg">
	Продумайте идею своей игры
</callout>
<callout icon="👨‍🎓" color="gray_bg">
	В таком виде у нас будет храниться концепция:
	Идея игры
	Цель игры
	- персонаж
	- сеттинг - среда, в которой происходит действие; место, время и условия действия.
	- ресурсы
	- препятствия и враги
	- окружение
	- механики
	<callout icon="🙋‍♂️" color="yellow_bg">
		Мы уже все придумали!
	</callout>
</callout>
