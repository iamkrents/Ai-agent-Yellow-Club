# 5 тема - Работа с окружением: сцена в стиле Minecraft

Источник: Notion
Notion page ID: 1e1a39a4-80c9-80ff-afbc-cd4ace3ffc81
Notion URL: https://app.notion.com/p/5-Minecraft-1e1a39a480c980ffafbccd4ace3ffc81
Notion last edited: 2026-03-11T19:56:00.000Z
Путь Notion: 30ca39a4 / Продукт / Программа обучения / Темы / 5 тема - Работа с окружением: сцена в стиле Minecraft

<callout icon="🎯" color="gray_bg">
	**Цель**
	Формирование навыков работы с точкой Origin и базовыми приёмами моделирования сцены
</callout>
<callout icon="🔨" color="gray_bg">
	**Задачи**
	- освоить работу с точкой Origin и Pivot point
	- выстроить позу Стиву
		- организовать объекты сцены
		- корректно разместить Origin для частей тела
	- построить сцену в стиле Minecraft
		- создать базовые блоки
		- оформить сцену
</callout>
<callout icon="✅" color="gray_bg">
	**Ожидаемый результат**
	Построена сцена в стиле Minecraft и настроена возможность изменения позы модели Стива
	![](https://prod-files-secure.s3.us-west-2.amazonaws.com/516b4608-c37a-4154-91ab-b5a5e820e71e/03d27824-399c-47d9-b494-0e2542083a4e/%D0%A1%D0%BD%D0%B8%D0%BC%D0%BE%D0%BA_%D1%8D%D0%BA%D1%80%D0%B0%D0%BD%D0%B0_2025-05-02_%D0%B2_20.39.41.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466R7ILWCYF%2F20260615%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260615T183411Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEJv%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEaCXVzLXdlc3QtMiJIMEYCIQCShlDsA5deCeQwKr5NZCthuJRbjvq32SspDANP2DzXSgIhAK7T9EKItKEvlIcoIglunmLKT2%2Bl92%2Fcz3VqObdMOxAcKv8DCGMQABoMNjM3NDIzMTgzODA1Igy8GYsZCY1ZO4sJcjYq3AMYBowfm1vjJtucbbEXV02%2Bdbg%2BhMNYhtxVxN2sweceBi4caUQ1XUxIb3mMA9pNIY0oA6T9jhfvN0NJAvBngO3tAD9MTFkpl6u%2FLar%2B2cHtEKE3AIRDoRODTdhucU1JLE5HJsT%2FB3tUs2mP5CYRr6%2FsXOfqRmVvjJzHB2XrkiSIHzjHv4L1BLnxPgnq8XY6C9YjBBXveX59YRBgf6IlBLv9eVS0YBBonjCKfUkwnPrYxTUUbks4bp5fxnU9I5q03FTcnsxZvPb2GEuTWLRyZKg%2FRGx8PCGrEhAhc9IbtM4y%2BvXeZd6JznFmt%2F71gyrTYadtu1kx0hYWIsYLry1u%2Bf1513nGQfSq8W%2FY5a1ReZzwhg0ckm%2BcMFIFtOVm5BWDbZDzmNDrXIEt2O4KhbInUaFlimG1jc90MRZnDs3gEMDvLD3%2BwWUQLyDJlw3tpmttqKa5paY9oXlvvrG8mxIdJDFR556mZmWBiZBvxCU7KVueSMtR406%2FglFI4wOojr%2BCRMJNpGKQSE4rc1x1ksorbEYae7WqE1rAyJq6M7xQRzgWaWqrMElS4dHvFE9zVtzo1P%2B5jCKtjF4Tc%2Fa0FtdPRf%2BtvL9e7KvBsfsPtkf21wNE95GyDnQQlouiqjiJqzCfh8HRBjqkATmyMvQ1N95Wp04NcRBPk6j3X0uofYQg9VJApyMXIJ%2FzBIix9Vo1B0vX70nbDhO%2FknhqgTqIq8JkYVSqxG%2Fucc%2FiAr7n8ugb5NaC%2BjW7cUpu4V20ZD7D%2F3LjiWQSFEAI5fCa5vjrpG5WxVjQOLw1AMY2lIv5TmA6PSM3KF9uj%2FR1w9vZJH8OIMUKslGwGC7ttuDzbYrih2IqDeSeuKaXSkb%2BAtWh&X-Amz-Signature=6be6b1cfb216327d2a6717e9bb0663317ff72af93366abc43fa7bb3871791dfc&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
</callout>
<callout icon="📂" color="gray_bg">
	**Материалы**
	[Ориджин объекта (object origin) - Blender 4.4 Manual](https://docs.blender.org/manual/ru/4.4/scene_layout/object/origin.html)
	[Центр трансформации (pivot point) - Blender 4.4 Manual](https://docs.blender.org/manual/ru/4.4/editors/3dview/controls/pivot_point/index.html)
</callout>
<callout icon="📃" color="gray_bg">
	**План**
	<table_of_contents color="gray"/>
</callout>
# Видеоурок
<video src="https://youtu.be/mjN2Jgbeo70"></video>
# Origin и Pivot Point
## Origin
<callout icon="☑️" color="gray_bg">
	Разберите как и для чего работать с точкой Origin.
</callout>
У каждого объекта есть точка ориджин. Местоположение этой точки определяет, где находится объект в 3D-пространстве. Когда объект выделен – появляется маленький кружок, обозначающий точку ориджин. Местоположение этой точки важно при перемещении, вращении или масштабировании объекта.
- рассказываем, что такое Origin
- учимся выставлять точку Origin на примере куба
## Pivot Point (дополнительно)
<callout icon="☑️" color="gray_bg">
	Разберите, что такое Pivot Point и как с ней работать.
</callout>
«Центр трансформации» определяет местоположение объекта-гизмо. Изменение этого местоположения может облегчить выполнение трансформаций вокруг нужной точки.
Центр трансформации можно изменить с помощью селектора в заголовке 3D-вьюпорта:
![](https://prod-files-secure.s3.us-west-2.amazonaws.com/516b4608-c37a-4154-91ab-b5a5e820e71e/80419a79-1c65-447b-a154-704e85c1ca3c/editors_3dview_controls_pivot-point_index_popover.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=ASIAZI2LB466R7ILWCYF%2F20260615%2Fus-west-2%2Fs3%2Faws4_request&X-Amz-Date=20260615T183411Z&X-Amz-Expires=3600&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEJv%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEaCXVzLXdlc3QtMiJIMEYCIQCShlDsA5deCeQwKr5NZCthuJRbjvq32SspDANP2DzXSgIhAK7T9EKItKEvlIcoIglunmLKT2%2Bl92%2Fcz3VqObdMOxAcKv8DCGMQABoMNjM3NDIzMTgzODA1Igy8GYsZCY1ZO4sJcjYq3AMYBowfm1vjJtucbbEXV02%2Bdbg%2BhMNYhtxVxN2sweceBi4caUQ1XUxIb3mMA9pNIY0oA6T9jhfvN0NJAvBngO3tAD9MTFkpl6u%2FLar%2B2cHtEKE3AIRDoRODTdhucU1JLE5HJsT%2FB3tUs2mP5CYRr6%2FsXOfqRmVvjJzHB2XrkiSIHzjHv4L1BLnxPgnq8XY6C9YjBBXveX59YRBgf6IlBLv9eVS0YBBonjCKfUkwnPrYxTUUbks4bp5fxnU9I5q03FTcnsxZvPb2GEuTWLRyZKg%2FRGx8PCGrEhAhc9IbtM4y%2BvXeZd6JznFmt%2F71gyrTYadtu1kx0hYWIsYLry1u%2Bf1513nGQfSq8W%2FY5a1ReZzwhg0ckm%2BcMFIFtOVm5BWDbZDzmNDrXIEt2O4KhbInUaFlimG1jc90MRZnDs3gEMDvLD3%2BwWUQLyDJlw3tpmttqKa5paY9oXlvvrG8mxIdJDFR556mZmWBiZBvxCU7KVueSMtR406%2FglFI4wOojr%2BCRMJNpGKQSE4rc1x1ksorbEYae7WqE1rAyJq6M7xQRzgWaWqrMElS4dHvFE9zVtzo1P%2B5jCKtjF4Tc%2Fa0FtdPRf%2BtvL9e7KvBsfsPtkf21wNE95GyDnQQlouiqjiJqzCfh8HRBjqkATmyMvQ1N95Wp04NcRBPk6j3X0uofYQg9VJApyMXIJ%2FzBIix9Vo1B0vX70nbDhO%2FknhqgTqIq8JkYVSqxG%2Fucc%2FiAr7n8ugb5NaC%2BjW7cUpu4V20ZD7D%2F3LjiWQSFEAI5fCa5vjrpG5WxVjQOLw1AMY2lIv5TmA6PSM3KF9uj%2FR1w9vZJH8OIMUKslGwGC7ttuDzbYrih2IqDeSeuKaXSkb%2BAtWh&X-Amz-Signature=ff0769f3b8b0cc5f45e78fefb482560b9a6dec8de926f902015c91245497f454&X-Amz-SignedHeaders=host&x-amz-checksum-mode=ENABLED&x-id=GetObject)
# Работа с позой Стива
## Организация объектов
<callout icon="☑️" color="gray_bg">
	Переименуйте все объекты Стива.
</callout>
- назначаем имена объектам в Outliner:
	- голова
	- тело
	- правая рука
	- левая рука
	- правая нога
	- левая нога
<callout icon="☑️" color="gray_bg">
	Создайте коллекцию для Стива.
</callout>
**1 способ**
- создаем коллекцию (Collection) в Outliner
- переименовываем коллекцию - “Стив”
- выбираем все части Стива
- перемещаем все части в коллекцию
**2 способ**
- выбираем все части Стива
- нажимаем M → New Collection
- переименовываем коллекцию - “Стив”
## Настройка Origin для Стива
<callout icon="☑️" color="gray_bg">
	Корректно разместите Origin для всех частей тела и измените позу Стива.
</callout>
- выставляем точку Origin в корректные места для:
	- головы
	- рук
	- ног
- изменяем позу Стива
	- выбираем часть тела
	- нажимаем R для вращения
	- выбираем корректную ось
	- поворачиваем часть тела
# Стилизация сцены
## **Создание блоков Minecraft**
<callout icon="☑️" color="gray_bg">
	Смоделируйте несколько базовых блоков в стиле Minecraft (земля, камень, дерево, вода, лава, листва).
</callout>
- добавляем несколько кубов
- переименовываем кубы исходя из блоков, которые будут созданы
- добавляем соответствующие материалы на блоки
- используем инструмент Loop Cut при необходимости
## Построение сцены
<callout icon="☑️" color="gray_bg">
	Постройте сцену в стиле Minecraft.
</callout>
- формируем сцену из созданных блоков
- используем Snap Tool для точного размещения боков
- размещаем Стива на сцене
# Повторение
<callout icon="☑️" color="gray_bg">
	Повторите пройденный материал.
</callout>
<empty-block/>
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
	<callout icon="📂" color="gray_bg">
		Материалы
	</callout>
</callout>
