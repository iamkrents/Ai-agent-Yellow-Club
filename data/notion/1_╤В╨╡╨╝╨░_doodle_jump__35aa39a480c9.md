# 1 тема - Doodle Jump

Источник: Notion
Notion page ID: 35aa39a4-80c9-80cb-87a1-eaf4d755b31c
Notion URL: https://app.notion.com/p/1-Doodle-Jump-35aa39a480c980cb87a1eaf4d755b31c
Notion last edited: 2026-05-15T08:26:00.000Z
Путь Notion: 30ca39a4 / Продукт / Программа обучения / Темы / 1 тема - Doodle Jump

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
	```c++
#include <SFML/Graphics.hpp>
#include <optional>
#include <cstdlib>
#include <ctime>

using namespace sf;

struct Point
{
    int x;
    int y;
};

int main()
{
    srand(static_cast<unsigned>(time(nullptr)));

    // В SFML 3 размер окна записывается через фигурные скобки:
    // VideoMode({ширина, высота})
    RenderWindow app(VideoMode({ 400, 533 }), "Doodle Game!");

    app.setFramerateLimit(60);

    Texture tBackground;
    Texture tPlatform;
    Texture tDoodle;

    if (!tBackground.loadFromFile("images/background.png"))
        return -1;

    if (!tPlatform.loadFromFile("images/platform.png"))
        return -1;

    if (!tDoodle.loadFromFile("images/doodle.png"))
        return -1;

    Sprite sBackground(tBackground);
    Sprite sPlatform(tPlatform);
    Sprite sDoodle(tDoodle);

    Point platform[20];

    // Создаём 10 платформ в случайных местах
    for (int i = 0; i < 10; i++)
    {
        platform[i].x = rand() % 400;
        platform[i].y = rand() % 533;
    }

    int x = 100;
    int y = 100;
    int h = 200;

    float dx = 0;
    float dy = 0;

    while (app.isOpen())
    {
        // В SFML 3 pollEvent() возвращает std::optional<sf::Event>
        while (const std::optional event = app.pollEvent())
        {
            // Закрытие окна
            if (event->is<Event::Closed>())
            {
                app.close();
            }
        }

        // Управление игроком
        if (Keyboard::isKeyPressed(Keyboard::Key::Right))
        {
            x += 3;
        }

        if (Keyboard::isKeyPressed(Keyboard::Key::Left))
        {
            x -= 3;
        }

        // Гравитация
        dy += 0.2f;
        y += static_cast<int>(dy);

        // Если игрок упал вниз — подкидываем его вверх
        if (y > 500)
        {
            dy = -10;
        }

        // Если игрок поднялся выше определённой высоты,
        // двигаем платформы вниз
        if (y < h)
        {
            y = h;

            for (int i = 0; i < 10; i++)
            {
                platform[i].y = platform[i].y - static_cast<int>(dy);

                // Если платформа ушла вниз за экран,
                // переносим её наверх
                if (platform[i].y > 533)
                {
                    platform[i].y = 0;
                    platform[i].x = rand() % 400;
                }
            }
        }

        // Проверка столкновения игрока с платформами
        for (int i = 0; i < 10; i++)
        {
            if ((x + 50 > platform[i].x) &&
                (x + 20 < platform[i].x + 68) &&
                (y + 70 > platform[i].y) &&
                (y + 70 < platform[i].y + 14) &&
                (dy > 0))
            {
                dy = -10;
            }
        }

        // Устанавливаем позицию игрока
        sDoodle.setPosition(Vector2f(
            static_cast<float>(x),
            static_cast<float>(y)
        ));

        // Отрисовка
        app.clear();

        app.draw(sBackground);
        app.draw(sDoodle);

        for (int i = 0; i < 10; i++)
        {
            sPlatform.setPosition(Vector2f(
                static_cast<float>(platform[i].x),
                static_cast<float>(platform[i].y)
            ));

            app.draw(sPlatform);
        }

        app.display();
    }

    return 0;
}
	```
</callout>
<callout icon="📂" color="gray_bg">
	**Материалы**
	<empty-block/>
</callout>
<callout icon="📃" color="gray_bg">
	**План**
	<table_of_contents color="gray"/>
</callout>
# Подключаем SFML и создаём окно
На первом этапе создаём пустое окно игры.
```c++
#include <SFML/Graphics.hpp>
#include <optional>

using namespace sf;

int main()
{
    RenderWindow app(VideoMode({ 400, 533 }), "Doodle Game!");

    app.setFramerateLimit(60);

    while (app.isOpen())
    {
        while (const std::optional event = app.pollEvent())
        {
            if (event->is<Event::Closed>())
            {
                app.close();
            }
        }

        app.clear(Color::Black);
        app.display();
    }

    return 0;
}
```
# Загружаем фон
```c++
#include <SFML/Graphics.hpp>
#include <optional>

using namespace sf;

int main()
{
    RenderWindow app(VideoMode({ 400, 533 }), "Doodle Game!");

    app.setFramerateLimit(60);

    Texture tBackground; // добавили

    if (!tBackground.loadFromFile("images/background.png")) // добавили
    {
        return -1;
    }

    Sprite sBackground(tBackground); // добавили

    while (app.isOpen())
    {
        while (const std::optional event = app.pollEvent())
        {
            if (event->is<Event::Closed>())
            {
                app.close();
            }
        }

        app.clear(Color::Black);
        app.draw(sBackground); // добавили
        app.display();
    }

    return 0;
}
```
`Texture tBackground;` - `Texture` — это сама картинка, которая загружается из файла.<br>`tBackground.loadFromFile("images/background.png")` - Загружает картинку из папки `images`<br>`Sprite sBackground(tBackground);` - `Sprite` — объект, который можно нарисовать на экране.<br>`app.draw(sBackground);`- Рисует фон в окне
# Добавляем персонажа
```c++
#include <SFML/Graphics.hpp>
#include <optional>

using namespace sf;

int main()
{
    RenderWindow app(VideoMode({ 400, 533 }), "Doodle Game!");

    app.setFramerateLimit(60);

    Texture tBackground; 
    Texture tDoodle; // добавили

    if (!tBackground.loadFromFile("images/background.png")) 
    {
        return -1;
    }

    if(!tDoodle.loadFromFile("images/doodle.png")) // добавили
    {
        return -1;
    }

    Sprite sBackground(tBackground);
    Sprite sDoodle(tDoodle); // добавили

    int x = 100; // добавили
    int y = 100;

    while (app.isOpen())
    {
        while (const std::optional event = app.pollEvent())
        {
            if (event->is<Event::Closed>())
            {
                app.close();
            }
        }

        sDoodle.setPosition(Vector2f( // добавили
            static_cast<float>(x),
            static_cast<float>(y)
        ));

        app.clear(Color::Black);
        app.draw(sBackground); 
        app.draw(sDoodle); // добавили
        app.display();
    }

    return 0;
}
```
```plain text
TexturetDoodle;
```
Создаём текстуру для персонажа.
```plain text
SpritesDoodle(tDoodle);
```
Создаём спрайт персонажа.
```plain text
intx =100;
inty =100;
```
Координаты персонажа.
```plain text
sDoodle.setPosition(...)
```
Ставит персонажа в нужное место на экране.
<empty-block/>
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
